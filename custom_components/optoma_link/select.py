"""Select entities, generated from the active projector profile's 'selects' list."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    async_add_entities(
        OptomaSelect(coordinator, entry, spec)
        for spec in coordinator.profile.get("selects", [])
    )


class OptomaSelect(OptomaEntity, SelectEntity):
    """A profile-defined enum control (Input Source, Picture Mode, 3D Format, ...).

    Some of these have a read-back command (so the dropdown tracks the
    projector's real state); others -- mostly 3D and light-source settings
    on the cheaper/older command tables -- are write-only, so the entity is
    marked ``assumed_state`` and just reflects the last value we sent.
    """

    def __init__(self, coordinator: OptomaUpdateCoordinator, entry: ConfigEntry, spec: dict) -> None:
        super().__init__(coordinator, entry, spec)
        self._attr_assumed_state = spec.get("read") is None
        self._attr_options = list(spec["options"].keys())

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.get(self._key)

    async def async_select_option(self, option: str) -> None:
        await async_guard_command(
            self._spec,
            self.coordinator.async_write_select(self._spec, option),
            current=self.coordinator.data.get(self._key),
            action=option,
        )
