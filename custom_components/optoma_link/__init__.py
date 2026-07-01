"""The Optoma Link integration.

Profile-driven (see ``profiles.py`` / ``projectors/*.json``) and
transport-agnostic (see ``transport.py``): this file just wires whichever
transport + profile a config entry resolved to into a single
``OptomaUpdateCoordinator`` and forwards setup to the generic platforms.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_MAC_ADDRESS,
    CONF_MODEL,
    CONF_PROJECTOR_ID,
    CONF_SCAN_INTERVAL,
    CONNECTION_TYPE_SERIAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import OptomaUpdateCoordinator
from .profiles import async_load_profiles
from .transport import OptomaCommandError, OptomaConnectionError, build_transport

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
]

SERVICE_SEND_COMMAND = "send_command"
SERVICE_SET_TEST_PATTERN = "set_test_pattern"
ATTR_CODE = "code"
ATTR_VALUE = "value"
ATTR_ENTRY_ID = "entry_id"
ATTR_ENABLED = "enabled"

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CODE): cv.string,
        vol.Optional(ATTR_VALUE): cv.string,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)

SET_TEST_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an Optoma Link projector from a config entry."""
    profiles = await async_load_profiles(hass)
    profile = profiles.get(entry.data[CONF_MODEL])
    if profile is None:
        raise HomeAssistantError(
            f"Unknown projector profile '{entry.data[CONF_MODEL]}'. Was a profile removed "
            "or renamed after this entry was set up?"
        )

    transport = build_transport(entry.data)
    transport.bind(entry.data.get(CONF_PROJECTOR_ID, "00"))

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    mac_address = entry.data.get(CONF_MAC_ADDRESS)

    coordinator = OptomaUpdateCoordinator(
        hass, transport, profile, scan_interval, mac_address=mac_address
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # The device registry only accepts http/https/homeassistant URLs
    # (see _validate_device_info_fields in helpers/device_registry.py), so
    # point at the projector's web admin page for LAN entries and omit the
    # URL entirely for serial connections.
    if entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_SERIAL:
        configuration_url = None
    else:
        configuration_url = f"http://{entry.data[CONF_HOST]}"

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=profile.get("display_name", profile["model_id"]),
        name=entry.title,
        sw_version=coordinator.data.get("firmware_version"),
        serial_number=coordinator.data.get("serial_number"),
        configuration_url=configuration_url,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply new options (poll interval) without requiring a reload."""
    coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    new_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.update_interval = timedelta(seconds=new_interval)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.transport.async_disconnect()
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
        hass.services.async_remove(DOMAIN, SERVICE_SET_TEST_PATTERN)
    return unload_ok


def _get_coordinator(hass: HomeAssistant, entry_id: str | None) -> OptomaUpdateCoordinator:
    coordinators: dict[str, OptomaUpdateCoordinator] = hass.data.get(DOMAIN, {})
    if not coordinators:
        raise HomeAssistantError("No Optoma Link device is configured")
    if entry_id is not None:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"Unknown config entry_id: {entry_id}")
        return coordinator
    return next(iter(coordinators.values()))


def _async_register_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):

        async def _async_handle_send_command(call: ServiceCall) -> ServiceResponse:
            coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
            try:
                reply = await coordinator.async_send_raw(
                    call.data[ATTR_CODE], call.data.get(ATTR_VALUE)
                )
            except (OptomaCommandError, OptomaConnectionError) as err:
                raise HomeAssistantError(str(err)) from err
            return {"response": reply}

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_COMMAND,
            _async_handle_send_command,
            schema=SEND_COMMAND_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_TEST_PATTERN):

        async def _async_handle_set_test_pattern(call: ServiceCall) -> None:
            coordinator = _get_coordinator(hass, call.data.get(ATTR_ENTRY_ID))
            try:
                await coordinator.async_set_test_pattern(call.data[ATTR_ENABLED])
            except (OptomaCommandError, OptomaConnectionError) as err:
                raise HomeAssistantError(str(err)) from err

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_TEST_PATTERN,
            _async_handle_set_test_pattern,
            schema=SET_TEST_PATTERN_SCHEMA,
        )
