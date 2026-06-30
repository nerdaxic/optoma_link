"""Binary sensors, generated from the active projector profile's 'binary_sensors' list."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
        OptomaBinarySensor(coordinator, entry, spec)
        for spec in coordinator.profile.get("binary_sensors", [])
    )


class OptomaBinarySensor(OptomaEntity, BinarySensorEntity):
    """A profile-defined read-only flag (e.g. 3D Active)."""

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self._key)
