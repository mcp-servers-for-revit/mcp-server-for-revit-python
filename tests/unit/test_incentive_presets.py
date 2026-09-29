"""Tests for geothermal.incentive_presets -- the "real jurisdiction preset"
feature wired to the Financial Plan tab's incentive_fraction input.
Validation strategy: check the catalog loads and every entry has the shape
the WPF UI depends on (a fillable base_fraction, a citation), rather than
asserting specific tax-policy numbers as if this test were the source of
truth for them -- the JSON file's own "source"/"as_of" fields are that.
"""
import pytest

from geothermal import incentive_presets


def test_list_incentive_presets_returns_at_least_one_entry():
    presets = incentive_presets.list_incentive_presets()
    assert len(presets) >= 1


def test_every_preset_has_a_fillable_rate_and_a_real_citation():
    for preset in incentive_presets.list_incentive_presets():
        assert 0.0 <= preset["base_fraction"] <= 1.0
        assert preset["jurisdiction"]
        assert preset["source"]  # must cite where the rate came from
        assert preset["as_of"]  # must say when it was researched, since tax law changes
        assert preset["notes"]  # must explain what the preset does and does not cover


def test_us_commercial_itc_preset_is_present_with_the_researched_base_rate():
    # Pins the specific preset this session researched and fetched (Plante Moran, 2026-09-29) --
    # a real regression check, not a re-derivation of tax policy.
    presets = incentive_presets.list_incentive_presets()
    us_itc = next((p for p in presets if p["id"] == "us_commercial_itc_section48"), None)
    assert us_itc is not None
    assert us_itc["base_fraction"] == pytest.approx(0.06)
    assert us_itc["enhanced_fraction"] == pytest.approx(0.30)
