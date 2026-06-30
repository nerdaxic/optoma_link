"""Number entities, generated from the active projector profile's 'numbers' list."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
        OptomaNumber(coordinator, entry, spec)
        for spec in coordinator.profile.get("numbers", [])
    )


class OptomaNumber(OptomaEntity, NumberEntity):
    """A profile-defined ranged control (Brightness, Contrast, Sharpness, ...).

    Useful for ambient-light/time-of-day automations driving Brightness or
    Contrast directly from a script or automation.
    """

    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry, spec: dict) -> None:
        super().__init__(coordinator, entry, spec)
        self._attr_assumed_state = spec.get("read") is None
        self._attr_native_min_value = spec["min"]
        self._attr_native_max_value = spec["max"]
        self._attr_native_step = spec.get("step", 1)
        if spec.get("unit"):
            self._attr_native_unit_of_measurement = spec["unit"]

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write_number(self._spec, value)
