"""Button entities: profile-defined one-shot commands (Resync, ...)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OptomaUpdateCoordinator
from .entity import OptomaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        OptomaCommandButton(coordinator, entry, spec)
        for spec in coordinator.profile.get("buttons", [])
    )


class OptomaCommandButton(OptomaEntity, ButtonEntity):
    """A profile-defined one-shot command (Resync, ...)."""

    async def async_press(self) -> None:
        await self.coordinator.async_press_button(self._spec)
