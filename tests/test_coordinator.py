"""Unit tests for coordinator.py's numeric normalization and value parsing.

Covers the zero-padding/sign-character tolerance added for the UHD60
using values captured verbatim from its debug logs, and confirms
non-padded replies -- what the other three profiles actually send --
still resolve on the first, exact-match attempt.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from optoma_link.coordinator import OptomaUpdateCoordinator as Coordinator
from optoma_link.transport import OptomaConnectionError, OptomaTimeoutError

# --- _normalize_numeric -----------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8", "8"),  # already canonical -- must be a no-op
        ("21", "21"),
        ("0", "0"),
        ("03", "3"),  # UHD60 live capture: Picture Mode padded to 2 digits
        ("07", "7"),  # UHD60 live capture: Aspect Ratio padded to 2 digits
        ("+01", "1"),  # UHD60 live capture: Brightness with a sign character
        ("-02", "-2"),
        ("+42", "42"),
    ],
)
def test_normalize_numeric(raw, expected):
    assert Coordinator._normalize_numeric(raw) == expected


def test_normalize_numeric_passes_through_non_numeric_unchanged():
    # e.g. UHZ68LV's Resolution sensor, keyed by strings like "1080p".
    assert Coordinator._normalize_numeric("1080p") == "1080p"


# --- _parse_value: switch / binary_sensor ------------------------------


def test_switch_parses_plain_one_as_true():
    assert Coordinator._parse_value("switch", {}, "1") is True


def test_switch_parses_plain_zero_as_false():
    assert Coordinator._parse_value("switch", {}, "0") is False


def test_switch_parses_padded_one_as_true():
    # The reported bug: a padded reply previously failed the literal
    # ``raw == "1"`` check and silently read as off, even while the
    # projector was genuinely on.
    assert Coordinator._parse_value("switch", {}, "+01") is True


def test_switch_treats_unparseable_input_as_false():
    # Documents current behavior rather than guarding a fix: nothing at
    # this layer can tell "genuinely off" apart from "this reply didn't
    # parse". transport.py's marker search is what keeps console noise
    # from reaching this function as raw input in the first place.
    assert Coordinator._parse_value("switch", {}, "garbage") is False


# --- _parse_value: select -----------------------------------------------

# Real read_options straight out of projectors/uhd60.json, so this test
# breaks if that profile's mapping ever regresses.
_UHD60_PICTURE_MODE_SPEC = {
    "read_options": {
        "0": "None", "1": "Presentation", "2": "Bright", "3": "Cinema",
        "4": "Reference", "5": "User", "6": "User (3D)", "9": "3D",
        "10": "DICOM SIM.", "11": "Film", "12": "Game", "14": "Vivid",
        "15": "ISF Day", "16": "ISF Night", "17": "ISF 3D",
        "18": "2D High Speed", "19": "Blending", "20": "Sport", "21": "HDR",
    }
}
_UHD60_ASPECT_RATIO_SPEC = {
    "read_options": {
        "1": "4:3", "2": "16:9", "3": "16:10", "5": "LBX", "6": "Native",
        "7": "Auto", "8": "Auto235", "9": "Superwide",
        "11": "Auto235 (Subtitle)", "12": "Auto 3D",
    }
}


def test_select_picture_mode_padded_value_from_live_uhd60():
    # Live capture: "~00123 1" replied "OK03" for documented code "3".
    assert Coordinator._parse_value("select", _UHD60_PICTURE_MODE_SPEC, "03") == "Cinema"


def test_select_picture_mode_unpadded_value_from_live_uhd60():
    # Live capture: the same command later replied "OK21" unpadded --
    # confirms the projector pads inconsistently per-value, not
    # per-command, so both shapes must resolve correctly.
    assert Coordinator._parse_value("select", _UHD60_PICTURE_MODE_SPEC, "21") == "HDR"


def test_select_aspect_ratio_padded_value_from_live_uhd60():
    # Live capture: "~00127 1" replied "OK07" for documented code "7".
    assert Coordinator._parse_value("select", _UHD60_ASPECT_RATIO_SPEC, "07") == "Auto"


def test_select_unmapped_value_falls_back_to_raw():
    assert Coordinator._parse_value("select", _UHD60_ASPECT_RATIO_SPEC, "99") == "99"


def test_select_unpadded_value_still_matches_first_try():
    # Regression guard for the other three profiles: a model that never
    # zero-pads must still resolve on the exact-match attempt alone.
    spec = {"read_options": {"7": "HDMI1", "8": "HDMI2"}}
    assert Coordinator._parse_value("select", spec, "8") == "HDMI2"


# --- _parse_value: number ------------------------------------------------


def test_number_parses_signed_padded_value():
    assert Coordinator._parse_value("number", {}, "+21") == 21


def test_number_parses_negative_value():
    assert Coordinator._parse_value("number", {}, "-2") == -2


def test_number_parses_float():
    assert Coordinator._parse_value("number", {}, "3.5") == 3.5


def test_number_returns_none_for_unparseable():
    assert Coordinator._parse_value("number", {}, "garbage") is None


# --- optional device details ----------------------------------------------


def _detail_coordinator():
    coordinator = object.__new__(Coordinator)
    coordinator.profile = {
        "device_info": [
            {"key": "firmware_version", "read": ["122", "1"]},
        ],
        "serial_read": ["353", "1"],
        "sensors": [],
    }
    coordinator._silent_device_details = set()
    return coordinator


@pytest.mark.asyncio
async def test_silent_optional_detail_is_skipped_after_confirmed_reply():
    coordinator = _detail_coordinator()
    reads = []

    async def read_spec(_entity_type, spec):
        reads.append(spec["key"])
        if spec["key"] == "firmware_version":
            return "C004"
        raise OptomaConnectionError("Timed out waiting for reply")

    coordinator._async_read_spec = read_spec
    updates = await coordinator._async_read_missing_device_details({})

    assert updates == {"firmware_version": "C004"}
    assert reads == ["firmware_version", "serial_number"]
    assert coordinator._silent_device_details == {"serial_number"}

    reads.clear()
    assert await coordinator._async_read_missing_device_details(updates) == {}
    assert reads == []


@pytest.mark.asyncio
async def test_first_silent_detail_still_reports_unreachable_projector():
    coordinator = _detail_coordinator()

    async def read_spec(_entity_type, _spec):
        raise OptomaConnectionError("Timed out waiting for reply")

    coordinator._async_read_spec = read_spec
    with pytest.raises(OptomaConnectionError):
        await coordinator._async_read_missing_device_details({})


# --- regression: issue #8 -- v2.9.0 deep-standby unavailable / can't power on ---
#
# On some models (reported on a UHZ68LV over LAN), once the projector has
# been in standby for about a minute it stops replying to anything at all --
# the TCP socket stays open and every write still goes out, but no reply
# ever comes back. Before this fix, OptomaConnectionError covered both that
# silence AND a genuinely dead connection, so _async_update_data treated
# "asleep" the same as "unreachable" and marked every entity unavailable
# (losing the power switch needed to wake it back up), and a write command
# that triggered the same silence surfaced as a failed service call even
# though it had, in fact, reached the projector and woken it up.


def _poll_coordinator(read_error: Exception) -> Coordinator:
    """A coordinator whose one 'power' read always raises ``read_error``."""
    coordinator = object.__new__(Coordinator)
    coordinator.profile = {
        "switches": [
            {
                "key": "power",
                "read": ["124", "1"],
                "on": ("124", "1"),
                "off": ("124", "0"),
            }
        ],
    }
    # Cached from before the projector went silent -- what a real standby
    # transition leaves behind, and what should survive if read_error is
    # mere silence rather than a real connection failure.
    coordinator.data = {"power": False, "status": "cooling_down"}
    coordinator._silent_device_details = set()
    coordinator.async_contexts = lambda: {"power"}
    coordinator._apply_dynamic_interval = lambda data: None

    async def read_spec(_entity_type, _spec):
        raise read_error

    coordinator._async_read_spec = read_spec
    return coordinator


@pytest.mark.asyncio
async def test_silent_standby_keeps_cached_data_instead_of_update_failed():
    coordinator = _poll_coordinator(OptomaTimeoutError("Timed out waiting for the projector's reply"))
    data = await coordinator._async_update_data()
    # No UpdateFailed raised (which is what would mark every entity
    # unavailable), and the last known state is still there.
    assert data == {"power": False, "status": "cooling_down"}


@pytest.mark.asyncio
async def test_genuine_connection_failure_still_raises_update_failed():
    # A real connection problem (refused, host unreachable, ...) must still
    # surface as UpdateFailed -- only the "connected but silent" case is new.
    coordinator = _poll_coordinator(OptomaConnectionError("Connection refused"))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


def _write_coordinator() -> Coordinator:
    coordinator = object.__new__(Coordinator)
    coordinator.data = {}
    coordinator.transport = AsyncMock()
    coordinator.hass = MagicMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator._apply_dynamic_interval = MagicMock()
    return coordinator


@pytest.mark.asyncio
async def test_write_command_tolerates_a_silent_reply():
    coordinator = _write_coordinator()
    coordinator.transport.async_send.side_effect = OptomaTimeoutError("no reply")
    await coordinator._async_write_command("124", "1")  # must not raise


@pytest.mark.asyncio
async def test_write_command_still_raises_on_a_real_connection_failure():
    coordinator = _write_coordinator()
    coordinator.transport.async_send.side_effect = OptomaConnectionError("refused")
    with pytest.raises(OptomaConnectionError):
        await coordinator._async_write_command("124", "1")


@pytest.mark.asyncio
async def test_power_on_from_deep_standby_does_not_raise():
    # The exact reported failure: switch.turn_on while the projector is in
    # deep standby used to come back as a failed service call (a timeout
    # wrapped in HomeAssistantError by entity.py's async_guard_command),
    # even though the write had already reached and woken the projector.
    coordinator = _write_coordinator()
    coordinator.transport.async_send.side_effect = OptomaTimeoutError("no reply")
    spec = {"key": "power", "on": ("124", "1"), "off": ("124", "0")}

    await coordinator.async_write_switch(spec, True)  # must not raise

    assert coordinator.data["power"] is True
    assert coordinator.data["status"] == "warming_up"
