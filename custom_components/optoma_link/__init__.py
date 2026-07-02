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
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    CONF_CONNECTION_TYPE,
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


def _clean_detail(value: object) -> str | None:
    """Drop empty or placeholder projector detail reads (e.g. serial "0")."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "0":
        return None
    return text


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

    coordinator = OptomaUpdateCoordinator(hass, transport, profile, scan_interval)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # The device registry only accepts http/https/homeassistant URLs
    # (see _validate_device_info_fields in helpers/device_registry.py), so
    # point the "Visit" link at exactly what the user entered for LAN entries
    # (host or IP) and omit the URL for serial. The projector-reported IP is
    # surfaced separately as a diagnostic sensor instead.
    is_serial = entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_TYPE_SERIAL
    if is_serial:
        configuration_url = None
    else:
        configuration_url = f"http://{entry.data[CONF_HOST]}"

    mac_address = _clean_detail(coordinator.data.get("mac_address"))
    connections = (
        {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac_address))} if mac_address else set()
    )

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        connections=connections,
        manufacturer=MANUFACTURER,
        model=profile.get("display_name", profile["model_id"]),
        name=entry.title,
        sw_version=_clean_detail(coordinator.data.get("firmware_version")),
        serial_number=_clean_detail(coordinator.data.get("serial_number")),
        configuration_url=configuration_url,
    )

    @callback
    def _refresh_device_details() -> None:
        """Backfill device details once the projector reports real values.

        Some units answer the serial/firmware/network reads with a placeholder
        (e.g. "0") on the first poll or two after connecting, so the real values
        can arrive after the device has already been created.
        """
        current = device_registry.async_get(device.id)
        if current is None:
            return
        updates: dict = {}
        serial = _clean_detail(coordinator.data.get("serial_number"))
        firmware = _clean_detail(coordinator.data.get("firmware_version"))
        mac = _clean_detail(coordinator.data.get("mac_address"))
        if serial and serial != current.serial_number:
            updates["serial_number"] = serial
        if firmware and firmware != current.sw_version:
            updates["sw_version"] = firmware
        if mac:
            connection = (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))
            if connection not in current.connections:
                updates["merge_connections"] = {connection}
        if updates:
            device_registry.async_update_device(device.id, **updates)

    entry.async_on_unload(coordinator.async_add_listener(_refresh_device_details))

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
