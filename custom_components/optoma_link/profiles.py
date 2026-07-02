"""Loads and indexes the bundled projector profiles.

A "profile" is a JSON file under ``projectors/`` that maps a projector
model's logical entities (switches, selects, numbers, sensors, buttons) to
its RS232 command codes. This is what makes the integration model-agnostic:
adding support for a new Optoma projector is a matter of adding a new JSON
file and opening a pull request, not writing Python.

See the "Adding a projector profile" section of the README for the schema.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_PROFILES_DIR = Path(__file__).parent / "projectors"

_REQUIRED_KEYS = ("schema_version", "model_id", "display_name")


def _load_profile_file(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.error("Could not load projector profile %s: %s", path, err)
        return None

    missing = [key for key in _REQUIRED_KEYS if key not in profile]
    if missing:
        _LOGGER.error("Projector profile %s is missing keys: %s", path, missing)
        return None

    for list_key in (
        "switches",
        "selects",
        "numbers",
        "binary_sensors",
        "sensors",
        "buttons",
        "device_info",
    ):
        profile.setdefault(list_key, [])

    profile.setdefault("aliases", [])
    profile.setdefault("manufacturer", "Optoma")
    profile.setdefault("capabilities", {})
    profile.setdefault("serial_read", None)

    return profile


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, dict[str, Any]]:
    """Return ``{model_id: profile}`` for every bundled profile JSON file."""
    profiles: dict[str, dict[str, Any]] = {}
    if not _PROFILES_DIR.is_dir():
        return profiles

    for path in sorted(_PROFILES_DIR.glob("*.json")):
        profile = _load_profile_file(path)
        if profile is None:
            continue
        profiles[profile["model_id"]] = profile

    return profiles


async def async_load_profiles(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Event-loop-safe ``load_profiles()``.

    The first call reads the profile JSON files in an executor thread;
    once the ``lru_cache`` is primed, the synchronous helpers in this
    module are free to call from the loop.
    """
    if load_profiles.cache_info().currsize:
        return load_profiles()
    return await hass.async_add_executor_job(load_profiles)


def get_profile(model_id: str) -> dict[str, Any] | None:
    """Look up a single profile by its model_id."""
    return load_profiles().get(model_id)


def guess_profile_id(raw_model_reply: str | None) -> str | None:
    """Best-effort match of a projector's raw 'Model Name' reply to a bundled profile.

    Handles both styles seen in Optoma's own documentation: newer projectors
    reply with the model name as plain text, older ones reply with a small
    numeric index that's only meaningful within that model's table.
    """
    if not raw_model_reply:
        return None
    reply = raw_model_reply.strip()
    if not reply:
        return None

    profiles = load_profiles()

    # Index-style reply (e.g. "1" -> "X501") -- only meaningful if some
    # profile's index_map actually defines that digit.
    for model_id, profile in profiles.items():
        index_map = profile.get("detect", {}).get("index_map")
        if index_map and reply in index_map:
            return model_id

    # Plain-text reply: compare case-insensitively against aliases/display name.
    upper = reply.upper()
    for model_id, profile in profiles.items():
        candidates = [*profile.get("aliases", []), profile.get("display_name", "")]
        for candidate in candidates:
            candidate_upper = candidate.upper()
            if candidate_upper and (candidate_upper == upper or candidate_upper in upper):
                return model_id

    return None


def describe_detected_model(model_id: str | None, raw_model_reply: str | None) -> str:
    """Human-readable summary used as a config flow description placeholder."""
    if model_id is None:
        if raw_model_reply:
            return f"Could not match the projector's reply ('{raw_model_reply}') to a known profile."
        return "Could not read a model name from the projector."

    profile = get_profile(model_id)
    name = profile["display_name"] if profile else model_id
    return f"Detected {name}."
