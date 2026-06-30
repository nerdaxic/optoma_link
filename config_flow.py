"""Config flow for Optoma Link.

Setup walks through: pick LAN ('RS232 by Telnet') or direct Serial RS232,
collect connection details and validate them with specific diagnostics,
auto-detect the projector model and let the user confirm/override it from
a dropdown of bundled profiles, optionally record a Wake-on-LAN MAC
address, and -- if the resolved profile supports it -- offer a toggle-able
'show test pattern' step so the user can visually confirm they're talking
to the right unit before finishing.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    SelectOptionDict,
)

from .const import (
    CONF_BAUD_RATE,
    CONF_CONNECTION_TYPE,
    CONF_MAC_ADDRESS,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_PROJECTOR_ID,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    CONNECTION_TYPE_LAN,
    CONNECTION_TYPE_SERIAL,
    DEFAULT_BAUD_RATE,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROJECTOR_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODEL_NAME_READ,
)
from .profiles import describe_detected_model, guess_profile_id, load_profiles
from .transport import (
    OptomaCommandError,
    OptomaConnectionError,
    OptomaSerialTransport,
    OptomaTcpTransport,
    OptomaTransport,
)
from .wol import build_magic_packet

_LOGGER = logging.getLogger(__name__)

BAUD_RATE_CHOICES = ["9600", "19200", "38400", "57600", "115200"]

# Shown to the user (via description_placeholders) whenever a LAN connection
# attempt fails, so they know where to look on the projector itself.
LAN_REMEDIATION_HINT = (
    "On the projector, open the on-screen menu and check Network > LAN: "
    "make sure the Network Function is turned on, and under Network > "
    "Control, that 'Telnet' is enabled and its port matches what you "
    "entered here (default 23)."
)


def _normalize_projector_id(raw: str) -> str:
    raw = (raw or DEFAULT_PROJECTOR_ID).strip()
    return raw.zfill(2) if raw.isdigit() else raw


async def _async_resolve_host(hass, host: str) -> None:
    """Raise a specific error if DNS can't resolve the given host."""
    loop = asyncio.get_running_loop()
    try:
        await loop.getaddrinfo(host, None)
    except socket.gaierror as err:
        raise _CannotResolveHost(str(err)) from err


async def _async_probe_transport(transport: OptomaTransport, projector_id: str) -> str | None:
    """Connect, bind the projector ID, and try to read the model name.

    Returns the raw model-name reply, or ``None`` if the connection worked
    but the projector didn't answer that particular query (some older
    models/firmwares don't implement it).
    """
    transport.bind(projector_id)
    await transport.async_connect()
    try:
        reply = await transport.async_send(*MODEL_NAME_READ)
    except OptomaCommandError:
        return None
    if reply.startswith("Ok"):
        reply = reply[2:]
    return reply.strip() or None


class _CannotResolveHost(Exception):
    """DNS lookup failed for the configured host/hostname."""


def _profile_select_options() -> list[SelectOptionDict]:
    return [
        SelectOptionDict(value=model_id, label=profile.get("display_name", model_id))
        for model_id, profile in sorted(load_profiles().items())
    ]


class OptomaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Optoma Link."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._transport: OptomaTransport | None = None
        self._guessed_model_id: str | None = None
        self._raw_model_reply: str | None = None
        self._chosen_model_id: str | None = None
        self._mac_address: str | None = None
        self._name: str | None = None
        self._test_pattern_on = False

    # --- step 1: pick a transport ------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="user",
            menu_options=["lan", "serial"],
        )

    # --- step 2a: LAN ('RS232 by Telnet') -----------------------------

    async def async_step_lan(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = user_input or {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            projector_id = _normalize_projector_id(user_input[CONF_PROJECTOR_ID])
            password = user_input.get(CONF_PASSWORD) or None

            await self.async_set_unique_id(f"lan:{host}:{port}:{projector_id}")
            self._abort_if_unique_id_configured()

            try:
                await _async_resolve_host(self.hass, host)
                transport = OptomaTcpTransport(host, port, password=password)
                raw_reply = await asyncio.wait_for(
                    _async_probe_transport(transport, projector_id), timeout=10
                )
            except _CannotResolveHost:
                errors["base"] = "cannot_resolve_host"
            except (OptomaConnectionError, OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating LAN connection")
                errors["base"] = "unknown"
            else:
                self._connection_data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_LAN,
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_PROJECTOR_ID: projector_id,
                    CONF_PASSWORD: password,
                }
                self._transport = transport
                self._raw_model_reply = raw_reply
                self._guessed_model_id = guess_profile_id(raw_reply)
                return await self.async_step_confirm_model()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                vol.Required(
                    CONF_PROJECTOR_ID, default=defaults.get(CONF_PROJECTOR_ID, DEFAULT_PROJECTOR_ID)
                ): str,
                vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            }
        )
        return self.async_show_form(
            step_id="lan",
            data_schema=schema,
            errors=errors,
            description_placeholders={"osd_hint": LAN_REMEDIATION_HINT},
        )

    # --- step 2b: direct Serial RS232 ---------------------------------

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = user_input or {}

        available_ports = await self.hass.async_add_executor_job(_list_serial_ports)

        if user_input is not None:
            device = user_input[CONF_SERIAL_PORT]
            baud_rate = int(user_input.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE))
            projector_id = _normalize_projector_id(user_input[CONF_PROJECTOR_ID])
            password = user_input.get(CONF_PASSWORD) or None

            await self.async_set_unique_id(f"serial:{device}:{projector_id}")
            self._abort_if_unique_id_configured()

            try:
                transport = OptomaSerialTransport(device, baud_rate, password=password)
                raw_reply = await asyncio.wait_for(
                    _async_probe_transport(transport, projector_id), timeout=10
                )
            except (OptomaConnectionError, OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_open_serial"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating serial connection")
                errors["base"] = "unknown"
            else:
                self._connection_data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL,
                    CONF_SERIAL_PORT: device,
                    CONF_BAUD_RATE: baud_rate,
                    CONF_PROJECTOR_ID: projector_id,
                    CONF_PASSWORD: password,
                }
                self._transport = transport
                self._raw_model_reply = raw_reply
                self._guessed_model_id = guess_profile_id(raw_reply)
                return await self.async_step_confirm_model()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SERIAL_PORT, default=defaults.get(CONF_SERIAL_PORT, "")
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=available_ports,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_BAUD_RATE, default=str(defaults.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE))
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=BAUD_RATE_CHOICES, custom_value=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required(
                    CONF_PROJECTOR_ID, default=defaults.get(CONF_PROJECTOR_ID, DEFAULT_PROJECTOR_ID)
                ): str,
                vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            }
        )
        return self.async_show_form(step_id="serial", data_schema=schema, errors=errors)

    # --- step 3: confirm/override the auto-detected model -------------

    async def async_step_confirm_model(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        profiles = load_profiles()
        default_model = self._guessed_model_id or next(iter(sorted(profiles)), None)

        if user_input is not None:
            mac_address = (user_input.get(CONF_MAC_ADDRESS) or "").strip()
            if mac_address:
                try:
                    build_magic_packet(mac_address)
                except ValueError:
                    errors[CONF_MAC_ADDRESS] = "invalid_mac"

            if not errors:
                self._chosen_model_id = user_input[CONF_MODEL]
                self._mac_address = mac_address or None
                self._name = user_input[CONF_NAME]

                profile = profiles[self._chosen_model_id]
                if profile.get("test_pattern"):
                    return await self.async_step_test_pattern()
                return await self._async_finish()

        detected_text = describe_detected_model(self._guessed_model_id, self._raw_model_reply)
        default_name = (
            profiles[default_model]["display_name"] if default_model else DEFAULT_NAME
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=default_name): str,
                vol.Required(CONF_MODEL, default=default_model): SelectSelector(
                    SelectSelectorConfig(
                        options=_profile_select_options(), mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_MAC_ADDRESS, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="confirm_model",
            data_schema=schema,
            errors=errors,
            description_placeholders={"detected": detected_text},
        )

    # --- step 4 (optional): toggleable test-pattern confirmation -------

    async def async_step_test_pattern(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="test_pattern",
            menu_options=["show_test_pattern", "hide_test_pattern", "finish_setup"],
            description_placeholders={
                "state": "showing" if self._test_pattern_on else "hidden"
            },
        )

    async def async_step_show_test_pattern(self) -> FlowResult:
        await self._async_send_test_pattern(True)
        return await self.async_step_test_pattern()

    async def async_step_hide_test_pattern(self) -> FlowResult:
        await self._async_send_test_pattern(False)
        return await self.async_step_test_pattern()

    async def async_step_finish_setup(self) -> FlowResult:
        await self._async_send_test_pattern(False)
        return await self._async_finish()

    async def _async_send_test_pattern(self, on: bool) -> None:
        profile = load_profiles()[self._chosen_model_id]
        test_pattern = profile.get("test_pattern")
        if not test_pattern or self._transport is None:
            return
        try:
            value = test_pattern["on"] if on else test_pattern["off"]
            await self._transport.async_send(test_pattern["write_code"], value)
            self._test_pattern_on = on
        except (OptomaCommandError, OptomaConnectionError) as err:
            _LOGGER.debug("Could not toggle test pattern during setup: %s", err)

    # --- finish ---------------------------------------------------------

    async def _async_finish(self) -> FlowResult:
        if self._transport is not None:
            await self._transport.async_disconnect()

        data = {
            **self._connection_data,
            CONF_MODEL: self._chosen_model_id,
            CONF_MAC_ADDRESS: self._mac_address,
        }
        return self.async_create_entry(
            title=self._name or DEFAULT_NAME,
            data=data,
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OptomaOptionsFlow(config_entry)


def _list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [port.device for port in list_ports.comports()]


class OptomaOptionsFlow(OptionsFlow):
    """Handle options (currently just the poll interval)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self._config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
