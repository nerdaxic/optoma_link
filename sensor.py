"""Sensor entities, generated from the active projector profile's 'sensors' list."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OptomaUpdateCoordinator
from .entity import OptomaEntity

_DEVICE_CLASS_MAP = {
    "temperature": SensorDeviceClass.TEMPERATURE,
}
_STATE_CLASS_MAP = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        OptomaSensor(coordinator, entry, spec)
        for spec in coordinator.profile.get("sensors", [])
    )


class OptomaSensor(OptomaEntity, SensorEntity):
    """A profile-defined read-only value (Lamp Hours, Firmware Version, ...)."""

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry, spec: dict) -> None:
        super().__init__(coordinator, entry, spec)
        if spec.get("unit"):
            self._attr_native_unit_of_measurement = spec["unit"]
        if spec.get("device_class") in _DEVICE_CLASS_MAP:
            self._attr_device_class = _DEVICE_CLASS_MAP[spec["device_class"]]
        if spec.get("state_class") in _STATE_CLASS_MAP:
            self._attr_state_class = _STATE_CLASS_MAP[spec["state_class"]]

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)
