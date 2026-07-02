"""Shared base entity for Optoma Link.

Device info is built from the matched profile (display name, manufacturer)
plus whatever the projector told us about itself during setup (serial
number, firmware version), instead of hardcoded per-model constants.
"""
from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OptomaUpdateCoordinator
from .transport import OptomaCommandError, OptomaConnectionError

_ENTITY_CATEGORY_MAP = {
    "diagnostic": EntityCategory.DIAGNOSTIC,
    "config": EntityCategory.CONFIG,
}


async def async_guard_command(
    spec: dict,
    awaitable: Awaitable[None],
    *,
    current: object = None,
    action: str | None = None,
) -> None:
    """Await a projector write, turning failures into user-facing errors.

    When the spec has a ``conflicts`` map and the entity's ``current`` value is
    one of its keys, the rejection is explained as a mode conflict (e.g. picture
    modes cannot be mixed across the SDR/HDR/Dolby Vision/3D "universes").
    Otherwise, if the spec carries an ``error_hint``, that is appended so the
    projector's bare 'F' rejection reads as actionable advice.
    """
    try:
        await awaitable
    except OptomaCommandError as err:
        conflicts = spec.get("conflicts") or {}
        current_universe = conflicts.get(current)
        requested_universe = conflicts.get(action)
        label = f"'{action}'" if action else "That option"
        message: str | None = None
        if current_universe and current_universe != requested_universe:
            # The active content universe blocks the requested (other) mode.
            message = (
                f"{label} is not available while the projector is in "
                f"{current_universe} mode. Match the setting to what is playing "
                "(SDR, HDR, Dolby Vision, or 3D)."
            )
        elif requested_universe == "3D":
            message = f"{label} picture mode is only available while 3D is turned on."
        elif requested_universe:
            message = (
                f"{label} picture mode is only available while the projector is "
                f"showing {requested_universe} content."
            )
        if message is None:
            hint = spec.get("error_hint")
            message = f"The projector rejected this command. {hint}" if hint else str(err)
        raise HomeAssistantError(message) from err
    except OptomaConnectionError as err:
        raise HomeAssistantError(str(err)) from err


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
        if spec.get("enabled_default") is False:
            self._attr_entity_registry_enabled_default = False

        # serial_number / sw_version are owned by the device registered in
        # __init__.py (and kept fresh there as the projector reports them), so
        # they are deliberately not set here -- an entity re-writing a stale or
        # placeholder value would fight that authoritative update.
        profile = coordinator.profile
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=profile.get("manufacturer", "Optoma"),
            model=profile.get("display_name", entry.title),
            name=entry.title,
        )
