"""Shared base entity for Optoma Link.

Device info is built from the matched profile (display name, manufacturer)
plus whatever the projector told us about itself during setup (serial
number, firmware version), instead of hardcoded per-model constants.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OptomaUpdateCoordinator

_ENTITY_CATEGORY_MAP = {
    "diagnostic": EntityCategory.DIAGNOSTIC,
    "config": EntityCategory.CONFIG,
}


class OptomaEntity(CoordinatorEntity[OptomaUpdateCoordinator]):
    """Base entity tying every platform entity to the same device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OptomaUpdateCoordinator,
        entry: ConfigEntry,
        spec: dict,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._spec = spec
        self._key = spec["key"]
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_name = spec["name"]
        if spec.get("icon"):
            self._attr_icon = spec["icon"]
        category = spec.get("entity_category")
        if category in _ENTITY_CATEGORY_MAP:
            self._attr_entity_category = _ENTITY_CATEGORY_MAP[category]

        profile = coordinator.profile
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=profile.get("manufacturer", "Optoma"),
            model=profile.get("display_name", entry.title),
            name=entry.title,
            sw_version=coordinator.data.get("firmware_version") if coordinator.data else None,
            serial_number=coordinator.data.get("serial_number") if coordinator.data else None,
            configuration_url=entry.data.get("configuration_url"),
        )
