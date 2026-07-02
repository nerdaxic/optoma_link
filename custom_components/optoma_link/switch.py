"""Switch entities, generated from the active projector profile's 'switches' list."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OptomaUpdateCoordinator
from .entity import OptomaEntity, async_guard_command


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: OptomaUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        OptomaSwitch(coordinator, entry, spec)
        for spec in coordinator.profile.get("switches", [])
    ]
    if coordinator.profile.get("test_pattern"):
        entities.append(OptomaTestPatternSwitch(coordinator, entry))
    async_add_entities(entities)


class OptomaSwitch(OptomaEntity, SwitchEntity):
    """A profile-defined on/off control (Power, AV Mute, Audio Mute, ...)."""

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry, spec: dict) -> None:
        super().__init__(coordinator, entry, spec)
        self._attr_assumed_state = spec.get("read") is None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_guard_command(
            self._spec, self.coordinator.async_write_switch(self._spec, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_guard_command(
            self._spec, self.coordinator.async_write_switch(self._spec, False)
        )


class OptomaTestPatternSwitch(OptomaEntity, SwitchEntity):
    """Toggles the projector's built-in test pattern (a grid, by default).

    Handy for confirming you're talking to the right unit, or for checking
    focus/alignment from within Home Assistant without walking over to the
    remote. Write-only on every command table we've seen, so this is always
    an assumed-state entity.
    """

    _attr_icon = "mdi:grid"
    _attr_entity_registry_enabled_default = False
    _attr_assumed_state = True

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, {"key": "test_pattern", "name": "Test Pattern"})

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_test_pattern(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_test_pattern(False)
