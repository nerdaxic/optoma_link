"""Unit tests for coordinator.py's numeric normalization and value parsing.

Covers the zero-padding/sign-character tolerance added for the UHD60
using values captured verbatim from its debug logs, and confirms
non-padded replies -- what the other three profiles actually send --
still resolve on the first, exact-match attempt.
"""
import pytest

from optoma_link.coordinator import OptomaUpdateCoordinator as Coordinator
from optoma_link.transport import OptomaConnectionError

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
