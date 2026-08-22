"""Tests for the bundled projector profiles.

Confirms every profile loads via the integration's own loader (schema
validity, not just "is it JSON"), and pins the UHD60 profile's live-hardware
verification down as an executable regression test rather than just a claim
in its "verified" field.
"""
from optoma_link.profiles import load_profiles


def test_all_bundled_profiles_load():
    assert set(load_profiles()) == {"uhz68lv", "w501", "zu650", "uhd60"}


def test_every_profile_has_required_shape():
    for model_id, profile in load_profiles().items():
        assert profile["model_id"] == model_id
        assert profile["display_name"]
        for section in ("switches", "selects", "numbers", "sensors", "buttons"):
            assert isinstance(profile.get(section, []), list)


def test_uhd60_is_marked_verified():
    assert load_profiles()["uhd60"]["verified"] is True


def test_uhd60_power_switch_read_code_matches_live_capture():
    # ~00124 1 is what every debug-log capture in the investigation showed
    # the integration actually sending for Power.
    power = next(s for s in load_profiles()["uhd60"]["switches"] if s["key"] == "power")
    assert power["read"] == ["124", "1"]
