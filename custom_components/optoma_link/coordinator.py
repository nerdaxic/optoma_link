"""Generic, profile-driven DataUpdateCoordinator for Optoma Link.

Unlike the original single-model integration, this coordinator does not
know anything about a specific projector. Everything it polls and every
command it can send comes from the matched ``projectors/*.json`` profile
(see ``profiles.py``), so adding a new projector model never requires
touching this file.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    AUTO_SEND_FAULTS,
    AUTO_SEND_MESSAGES,
    AUTO_SEND_OPERATIONAL,
    DOMAIN,
    RESPONSE_OK_PREFIX,
    STANDBY_SCAN_INTERVAL,
)
from .transport import OptomaCommandError, OptomaConnectionError, OptomaTransport

_LOGGER = logging.getLogger(__name__)


def _parse_info_code(line: str) -> int | None:
    """Extract the numeric code from an unsolicited ``INFOn`` status line."""
    upper = line.strip().upper()
    if not upper.startswith("INFO"):
        return None
    rest = upper[len("INFO"):].strip()
    return int(rest) if rest.isdigit() else None


def _strip_ok(reply: str) -> str:
    """Strip the ``OK`` marker Optoma prefixes to read replies.

    The projector answers reads with ``OK`` followed by the value, but
    firmwares vary in casing (``OK`` vs ``Ok``), so match case-insensitively.
    Otherwise the prefix leaks into values (e.g. firmware read back as
    ``OKC20M11S32``) and numeric reads fail to parse and show as Unknown.
    """
    reply = reply.strip()
    if reply[: len(RESPONSE_OK_PREFIX)].casefold() == RESPONSE_OK_PREFIX.casefold():
        return reply[len(RESPONSE_OK_PREFIX) :].strip()
    return reply


class OptomaUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the projector and caches parsed state, per the active profile.

    ``data`` holds both polled values and the last commanded value for
    write-only / optimistic controls (anything whose profile entry has
    ``"read": null``, since the protocol has no read-back command for it).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        transport: OptomaTransport,
        profile: dict[str, Any],
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.transport = transport
        self.profile = profile
        self.data = {}
        # The user-configured interval; the *effective* update_interval relaxes
        # to STANDBY_SCAN_INTERVAL while the projector is off (see
        # _apply_dynamic_interval) and snaps back on a power-up push/command.
        self._scan_interval = scan_interval
        self._seed_write_only_defaults()
        transport.set_status_callback(self._handle_status_line)

    def set_scan_interval(self, seconds: int) -> None:
        """Apply a new user-configured poll interval (from the options flow)."""
        self._scan_interval = seconds
        self._apply_dynamic_interval(self.data or {})

    def _seed_write_only_defaults(self) -> None:
        """Give write-only controls a sensible starting value.

        Without a read-back the projector never tells us these, so a slider or
        dropdown would otherwise sit at its minimum until first set. A spec's
        ``default`` seeds a believable value instead (e.g. Sharpness 8).
        """
        for section in ("switches", "selects", "numbers"):
            for spec in self.profile.get(section, []):
                if spec.get("read") is None and "default" in spec:
                    self.data[spec["key"]] = spec["default"]

    # --- unsolicited status pushes ------------------------------------

    def _handle_status_line(self, line: str) -> None:
        """Turn an unsolicited ``INFOn`` line into a status/power update.

        Runs from the transport's read loop, so power transitions and faults
        reflect in Home Assistant the moment the projector reports them.
        """
        code = _parse_info_code(line)
        if code is None:
            return
        updates: dict[str, Any] = {}
        message = AUTO_SEND_MESSAGES.get(code)
        if message:
            updates["status_message"] = message
        if code in AUTO_SEND_OPERATIONAL:
            status = AUTO_SEND_OPERATIONAL[code]
            updates["status"] = status
            updates["power"] = status in ("on", "warming_up")
        elif code in AUTO_SEND_FAULTS:
            updates["status"] = "error"
        if not updates:
            return
        self.data = {**(self.data or {}), **updates}
        # Re-arm the poll timer *before* publishing: async_set_updated_data
        # reschedules the next refresh using the current update_interval, so a
        # power-up push flips polling back to the fast cadence immediately.
        self._apply_dynamic_interval(self.data)
        self.async_set_updated_data(self.data)

    # --- polling ------------------------------------------------------

    def _iter_readable_entities(self):
        """Yield (entity_type, spec) for every entity-backed read in the profile."""
        sections = (
            ("switch", "switches"),
            ("select", "selects"),
            ("number", "numbers"),
            ("binary_sensor", "binary_sensors"),
            ("sensor", "sensors"),
        )
        for entity_type, section in sections:
            for spec in self.profile.get(section, []):
                if spec.get("read"):
                    yield entity_type, spec

    def _iter_device_detail_specs(self):
        """Yield the entity-less reads that populate the device registry.

        These are the profile's ``device_info`` entries (firmware, MAC, ...)
        plus the top-level ``serial_read``, which the device card needs even
        when the Serial Number sensor entity is disabled (its default).
        """
        seen = set()
        for spec in self.profile.get("device_info", []):
            if spec.get("read"):
                seen.add(spec["key"])
                yield spec
        serial_read = self.profile.get("serial_read")
        if serial_read and "serial_number" not in seen:
            # Reuse the serial sensor's validation pattern if the profile has one.
            pattern = next(
                (
                    s.get("pattern")
                    for s in self.profile.get("sensors", [])
                    if s.get("key") == "serial_number"
                ),
                None,
            )
            yield {"key": "serial_number", "read": serial_read, "pattern": pattern}

    async def async_fetch_device_details(self) -> None:
        """Read the device-registry values once, at setup.

        Immutable/semi-static values (firmware, MAC, serial) don't belong on
        the recurring poll timer; they are read here at startup instead. Also
        doubles as the setup-time connectivity check: raises
        ``OptomaConnectionError`` (for ConfigEntryNotReady) if the projector
        is unreachable. Values the projector answers with a placeholder for
        (or rejects, e.g. in standby) are retried from the poll loop until a
        real value arrives, then never asked for again.
        """
        await self.transport.async_connect()
        updates = await self._async_read_missing_device_details(dict(self.data or {}))
        if updates:
            self.data = {**(self.data or {}), **updates}

    def _missing_device_detail_specs(self, data: dict[str, Any]):
        """Device-detail specs we still lack a real (non-placeholder) value for."""
        for spec in self._iter_device_detail_specs():
            value = data.get(spec["key"])
            text = str(value).strip() if value is not None else ""
            if not text or text == "0":
                yield spec

    async def _async_read_missing_device_details(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        for spec in self._missing_device_detail_specs(data):
            try:
                value = await self._async_read_spec("sensor", spec)
            except OptomaCommandError as err:
                _LOGGER.debug(
                    "Device detail '%s' not supported: %s", spec["key"], err
                )
                continue
            if value is not None:
                updates[spec["key"]] = value
        return updates

    async def _async_read_spec(self, entity_type: str, spec: dict[str, Any]) -> Any:
        """Send one spec's read and return the parsed value, or None if the
        reply fails the spec's shape validation.

        Raises ``OptomaCommandError`` / ``OptomaConnectionError`` unchanged.
        """
        code, sub_value = spec["read"]
        reply = await self.transport.async_send(code, sub_value)
        raw = _strip_ok(reply)
        # Reject values that don't match the field's expected shape. During
        # signal/power transitions the projector can briefly answer with the
        # wrong field (a serial that looks like "2160p"); keep the last good
        # value rather than showing garbage.
        pattern = spec.get("pattern")
        if pattern is not None and re.fullmatch(pattern, raw) is None:
            _LOGGER.debug(
                "Read '%s' returned %r, which fails validation %s; keeping previous value",
                spec["key"],
                raw,
                pattern,
            )
            return None
        return self._parse_value(entity_type, spec, raw)

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.data or {})
        # Contexts are registered by entities actually added to Home Assistant
        # (see OptomaEntity), so this is exactly the set of *enabled* entities.
        # Disabled entities' reads are never sent -- which also means the IP
        # read (87/3), whose sensor is disabled by default, only goes on the
        # wire if the user explicitly enables that sensor. (On the UHZ68LV
        # firmware C22/M12/S32 that read intermittently crashes the projector's
        # ProjectorService -- see the README's known issues.)
        active_keys = set(self.async_contexts())
        attempted = 0
        any_success = False
        last_error: Exception | None = None

        for entity_type, spec in self._iter_readable_entities():
            key = spec["key"]
            if key not in active_keys:
                continue
            attempted += 1
            try:
                value = await self._async_read_spec(entity_type, spec)
            except OptomaCommandError as err:
                _LOGGER.debug("Read '%s' not supported by projector: %s", key, err)
                continue
            except OptomaConnectionError as err:
                last_error = err
                continue
            any_success = True
            if value is not None:
                data[key] = value

        # Retry any device-registry detail the projector hasn't given a real
        # value for yet (some units answer "0" until fully booted). Once every
        # detail is in, this adds zero reads to the cycle.
        try:
            detail_updates = await self._async_read_missing_device_details(data)
        except OptomaConnectionError as err:
            last_error = err
        else:
            if detail_updates:
                any_success = True
                data.update(detail_updates)

        if attempted and not any_success:
            if last_error is not None:
                raise UpdateFailed(str(last_error))
            # Connection is alive but the projector rejected every read
            # (typical for some models in standby); keep the cached data.
            _LOGGER.debug("Projector rejected every poll command; keeping cached state")

        self._apply_dynamic_interval(data)
        return data

    def _apply_dynamic_interval(self, data: dict[str, Any]) -> None:
        """Poll fast while the projector is on, relaxed while it's off.

        Nothing the poll reads can change in standby, so the effective interval
        stretches to STANDBY_SCAN_INTERVAL (or the configured interval if the
        user already set it longer). The projector's power-up INFO push and
        HA-side power commands both come through here, so waking snaps polling
        back to the configured cadence without waiting out the slow timer.
        """
        power_on = data.get("power") is True or data.get("status") in (
            "on",
            "warming_up",
        )
        seconds = (
            self._scan_interval
            if power_on
            else max(self._scan_interval, STANDBY_SCAN_INTERVAL)
        )
        new_interval = timedelta(seconds=seconds)
        if self.update_interval != new_interval:
            _LOGGER.debug(
                "Poll interval -> %ss (projector %s)",
                seconds,
                "on" if power_on else "off/standby",
            )
            self.update_interval = new_interval

    @staticmethod
    def _normalize_numeric(value: str) -> str:
        """Canonicalize a numeric reply for comparisons/lookups.

        Every profile writes ``read_options`` keys and boolean checks against
        bare integers ("0", "1", "21", ...), matching how Optoma's own docs
        table them -- but firmwares are inconsistent about zero-padding and
        sign characters in what they actually send back. Seen on a UHD60:
        Picture Mode replied "03" for a documented "3", Aspect Ratio replied
        "07" for "7", Brightness replied "+01" for "1". ``int()`` already
        shrugs these off (unlike a literal ``0``-prefixed number in source
        code, ``int("03")`` is not parsed as octal), so route lookups through
        it before falling back to the raw string for genuinely non-numeric
        values (e.g. the Resolution sensor's "1080p"/"4K" keys).
        """
        try:
            return str(int(value))
        except ValueError:
            return value

    @classmethod
    def _parse_value(cls, entity_type: str, spec: dict[str, Any], raw: str) -> Any:
        if entity_type in ("switch", "binary_sensor"):
            return cls._normalize_numeric(raw) == "1"
        if entity_type == "select":
            read_options = spec.get("read_options") or {}
            return read_options.get(raw, read_options.get(cls._normalize_numeric(raw), raw))
        if entity_type == "number":
            try:
                return float(raw) if "." in raw else int(raw)
            except ValueError:
                return None
        if entity_type == "sensor":
            read_options = spec.get("read_options")
            if read_options:
                return read_options.get(raw, read_options.get(cls._normalize_numeric(raw), raw))
            if spec.get("format") == "ip":
                # Optoma returns the IP underscore-separated and zero-padded,
                # e.g. 010_127_040_241. Strip the padding so it is not later
                # misread as octal (010 -> 8); fall back to a plain swap.
                parts = raw.split("_")
                try:
                    return ".".join(str(int(part)) for part in parts)
                except ValueError:
                    return raw.replace("_", ".")
            value_type = spec.get("value_type", "str")
            if value_type == "int":
                try:
                    return int(raw)
                except ValueError:
                    return None
            if value_type == "float":
                try:
                    return float(raw)
                except ValueError:
                    return None
            return raw
        return raw

    def _set_optimistic(self, key: str, value: Any) -> None:
        self.data = {**(self.data or {}), key: value}
        self.async_set_updated_data(self.data)

    # --- generic write helpers, used by every platform --------------------

    async def async_write_switch(self, spec: dict[str, Any], on: bool) -> None:
        code, value = spec["on"] if on else spec["off"]
        await self.transport.async_send(code, value)
        updates: dict[str, Any] = {spec["key"]: on}
        # Give the power button instant feedback; the projector's auto-sends
        # (warming up -> on, cooling down -> standby) refine it moments later.
        if spec["key"] == "power":
            updates["status"] = "warming_up" if on else "cooling_down"
        self.data = {**(self.data or {}), **updates}
        # A power command from HA re-arms the poll cadence right away (fast on
        # power-on, relaxed on power-off) instead of waiting for the push.
        self._apply_dynamic_interval(self.data)
        self.async_set_updated_data(self.data)
        if spec.get("refresh_after"):
            self.hass.async_create_task(self._async_delayed_refresh())

    async def _async_delayed_refresh(self, delay: float = 2.0) -> None:
        """Re-poll shortly after a change the projector applies with a lag.

        Toggling 3D, for example, also flips Picture Mode and Resolution on the
        projector; a nudge here surfaces that without waiting a full interval.
        """
        await asyncio.sleep(delay)
        await self.async_request_refresh()

    async def async_write_select(self, spec: dict[str, Any], option: str) -> None:
        target = spec["options"][option]
        # An option is normally just the value for the spec's write_code, but it
        # may instead be a [code, value] pair to reach a different command (e.g.
        # Dynamic Black modes live on their own code, not the light-power one).
        if isinstance(target, (list, tuple)):
            code, value = target
        else:
            code, value = spec["write_code"], target
        await self.transport.async_send(code, value)
        self._set_optimistic(spec["key"], option)

    async def async_write_number(self, spec: dict[str, Any], value: float) -> None:
        num = int(value) if float(value).is_integer() else value
        await self.transport.async_send(spec["write_code"], str(num))
        self._set_optimistic(spec["key"], value)

    async def async_press_button(self, spec: dict[str, Any]) -> None:
        code, value = spec["command"]
        await self.transport.async_send(code, value)

    async def async_set_test_pattern(self, on: bool) -> None:
        """Used by the config-flow 'show test pattern' step and a button entity."""
        test_pattern = self.profile.get("test_pattern")
        if not test_pattern:
            raise OptomaCommandError("This projector profile has no test pattern command")
        value = test_pattern["on"] if on else test_pattern["off"]
        await self.transport.async_send(test_pattern["write_code"], value)
        self._set_optimistic("test_pattern", on)

    # --- raw passthrough (backs the send_command service) ----------------

    async def async_send_raw(self, code: str, value: str | None) -> str:
        return await self.transport.async_send(code, value)
