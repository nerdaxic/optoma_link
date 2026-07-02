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

from .const import DOMAIN, STATUS_LABELS, STATUS_OPTIONS
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
    entities: list[OptomaEntity] = [
        OptomaSensor(coordinator, entry, spec)
        for spec in coordinator.profile.get("sensors", [])
    ]
    if coordinator.profile.get("capabilities", {}).get("auto_status"):
        entities.append(OptomaStatusSensor(coordinator, entry))
    async_add_entities(entities)


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


class OptomaStatusSensor(OptomaEntity, SensorEntity):
    """Live projector status (Off / Warming up / On / Cooling down / Error).

    Driven by the projector's unsolicited ``INFOn`` pushes, so it reflects
    power transitions immediately instead of at the next poll. Falls back to
    the polled power state until the first status push arrives.
    """

    _attr_icon = "mdi:projector"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_OPTIONS

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, {"key": "status", "name": "Status"})

    @property
    def native_value(self):
        status = self.coordinator.data.get("status")
        if status is None:
            power = self.coordinator.data.get("power")
            if power is True:
                return "On"
            if power is False:
                return "Off"
            return None
        return STATUS_LABELS.get(status)

    @property
    def extra_state_attributes(self):
        message = self.coordinator.data.get("status_message")
        return {"last_message": message} if message else None
