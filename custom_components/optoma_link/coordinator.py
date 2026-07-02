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
        transport.set_status_callback(self._handle_status_line)

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
        self.async_set_updated_data(self.data)

    # --- polling ------------------------------------------------------

    def _iter_readable_entities(self):
        """Yield (entity_type, spec) for every profile entity with a read command."""
        for spec in self.profile.get("switches", []):
            if spec.get("read"):
                yield "switch", spec
        for spec in self.profile.get("selects", []):
            if spec.get("read"):
                yield "select", spec
        for spec in self.profile.get("numbers", []):
            if spec.get("read"):
                yield "number", spec
        for spec in self.profile.get("binary_sensors", []):
            if spec.get("read"):
                yield "binary_sensor", spec
        for spec in self.profile.get("sensors", []):
            if spec.get("read"):
                yield "sensor", spec
        # device_info reads populate the device registry (firmware, MAC, ...)
        # without creating entities; parse them like sensors.
        for spec in self.profile.get("device_info", []):
            if spec.get("read"):
                yield "sensor", spec

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.data or {})
        any_success = False
        last_error: Exception | None = None

        for entity_type, spec in self._iter_readable_entities():
            code, sub_value = spec["read"]
            key = spec["key"]
            try:
                reply = await self.transport.async_send(code, sub_value)
            except OptomaCommandError as err:
                _LOGGER.debug("Read '%s' not supported by projector: %s", key, err)
                continue
            except OptomaConnectionError as err:
                last_error = err
                continue

            any_success = True
            raw = _strip_ok(reply)
            data[key] = self._parse_value(entity_type, spec, raw)

        if not any_success:
            if last_error is not None:
                raise UpdateFailed(str(last_error))
            # Connection is alive but the projector rejected every read
            # (typical for some models in standby); keep the cached data.
            _LOGGER.debug("Projector rejected every poll command; keeping cached state")

        return data

    @staticmethod
    def _parse_value(entity_type: str, spec: dict[str, Any], raw: str) -> Any:
        if entity_type in ("switch", "binary_sensor"):
            return raw == "1"
        if entity_type == "select":
            read_options = spec.get("read_options") or {}
            return read_options.get(raw, raw)
        if entity_type == "number":
            try:
                return float(raw) if "." in raw else int(raw)
            except ValueError:
                return None
        if entity_type == "sensor":
            read_options = spec.get("read_options")
            if read_options:
                return read_options.get(raw, raw)
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
        value = spec["options"][option]
        await self.transport.async_send(spec["write_code"], value)
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
