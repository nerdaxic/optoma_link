"""Button entities: profile-defined buttons (Resync, ...) plus Wake-on-LAN."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MAC_ADDRESS, DOMAIN
from .coordinator import OptomaUpdateCoordinator
from .entity import OptomaEntity
from .wol import async_send_magic_packet


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        OptomaCommandButton(coordinator, entry, spec)
        for spec in coordinator.profile.get("buttons", [])
    ]

    mac_address = entry.data.get(CONF_MAC_ADDRESS)
    if mac_address and coordinator.profile.get("capabilities", {}).get("wol"):
        entities.append(OptomaWakeOnLanButton(coordinator, entry, mac_address))

    async_add_entities(entities)


class OptomaCommandButton(OptomaEntity, ButtonEntity):
    """A profile-defined one-shot command (Resync, ...)."""

    async def async_press(self) -> None:
        await self.coordinator.async_press_button(self._spec)


class OptomaWakeOnLanButton(OptomaEntity, ButtonEntity):
    """Sends a Wake-on-LAN magic packet.

    Some projectors stop responding to RS232/network power-on commands
    once they're in deep standby. If you've entered the projector's MAC
    address during setup, this gives the Power switch's 'turn on' a chance
    of actually working by waking the network interface first; it's also
    available standalone for scripts that want to wake the projector well
    before they need to actually turn it on (laser/lamp warm-up).
    """

    _attr_icon = "mdi:lan-pending"

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry, mac_address: str) -> None:
        super().__init__(coordinator, entry, {"key": "wake_on_lan", "name": "Wake on LAN"})
        self._mac_address = mac_address

    async def async_press(self) -> None:
        await async_send_magic_packet(self._mac_address)
