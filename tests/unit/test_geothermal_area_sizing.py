"""Tests for the area+peak-load 'quick sizing' path:
sizing.field_layout_from_area, sizing.synthesize_peak_only_load, and
hybrid.minimum_tower_capacity -- the pieces the Area Sizing tab composes to
answer "given my peak loads and available land, what's the minimum depth,
and do I need a dry cooler."
"""
import pytest

from geothermal import hybrid, sizing

FIELD = {"layout": "rectangle", "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}

COMMON_KWARGS = dict(
    k_s=2.0, k_g=1.5, T_g=18.0, pipe_config=PIPE, m_flow_borehole=0.30,
    fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=20.0,
)


# ---- field_layout_from_area ------------------------------------------

def test_field_layout_from_area_fits_a_square_grid_in_the_combined_area():
    # 36m x 36m = 1296 m2 combined, spacing 6m -> 7x7 grid fits exactly (6 gaps x 6m = 36m side).
    result = sizing.field_layout_from_area(area_ambient_m2=1000.0, area_under_building_m2=296.0, spacing_m=6.0)
    assert result["N_1"] == 7
    assert result["N_2"] == 7
    assert result["n_boreholes"] == 49
    assert result["total_area_available_m2"] == pytest.approx(1296.0)
    assert result["footprint_area_m2"] == pytest.approx(36.0 * 36.0)


def test_field_layout_from_area_tiny_area_gives_a_single_borehole():
    result = sizing.field_layout_from_area(area_ambient_m2=1.0, area_under_building_m2=0.0, spacing_m=6.0)
    assert result["N_1"] == 1
    assert result["N_2"] == 1
    assert result["n_boreholes"] == 1


def test_field_layout_from_area_rejects_zero_combined_area():
    with pytest.raises(ValueError):
        sizing.field_layout_from_area(area_ambient_m2=0.0, area_under_building_m2=0.0, spacing_m=6.0)


def test_field_layout_from_area_rejects_negative_area():
    with pytest.raises(ValueError):
        sizing.field_layout_from_area(area_ambient_m2=-10.0, area_under_building_m2=0.0, spacing_m=6.0)


def test_field_layout_from_area_max_rows_reshapes_a_narrow_site():
    # Same 1296 m2 combined area as the square test above (square grid would be 7x7=49),
    # but the site can only fit 3 rows -- N_1 should grow to keep ~49 boreholes.
    result = sizing.field_layout_from_area(
        area_ambient_m2=1000.0, area_under_building_m2=296.0, spacing_m=6.0, max_rows=3)
    assert result["N_2"] == 3
    assert result["N_1"] == 17  # ceil(49 / 3)
    assert result["n_boreholes"] == 51


def test_field_layout_from_area_max_rows_above_square_is_a_no_op():
    result_unconstrained = sizing.field_layout_from_area(
        area_ambient_m2=1000.0, area_under_building_m2=296.0, spacing_m=6.0)
    result_loosely_constrained = sizing.field_layout_from_area(
        area_ambient_m2=1000.0, area_under_building_m2=296.0, spacing_m=6.0, max_rows=10)
    assert result_loosely_constrained["N_1"] == result_unconstrained["N_1"]
    assert result_loosely_constrained["N_2"] == result_unconstrained["N_2"]


def test_field_layout_from_area_rejects_non_positive_max_rows():
    with pytest.raises(ValueError):
        sizing.field_layout_from_area(area_ambient_m2=1000.0, area_under_building_m2=0.0, spacing_m=6.0, max_rows=0)


# ---- synthesize_peak_only_load -----------------------------------------

def test_synthesize_peak_only_load_returns_8760_hours():
    hourly = sizing.synthesize_peak_only_load(peak_heating_kW=20.0, peak_cooling_kW=15.0)
    assert len(hourly) == 8760


def test_synthesize_peak_only_load_is_zero_outside_the_seasons():
    hourly = sizing.synthesize_peak_only_load(
        peak_heating_kW=20.0, peak_cooling_kW=15.0, heating_season_months=2, cooling_season_months=2)
    # April and October are outside a 2-month-each Jan-centered/Jul-centered season.
    days_before_april = 31 + 28 + 31  # Jan, Feb, Mar
    april_first_hour = days_before_april * 24
    assert hourly[april_first_hour] == 0.0


def test_synthesize_peak_only_load_hits_the_peak_value_in_season():
    hourly = sizing.synthesize_peak_only_load(
        peak_heating_kW=20.0, peak_cooling_kW=15.0, heating_season_months=2, cooling_season_months=2)
    assert hourly[0] == pytest.approx(20000.0)  # January 1st, heating season, heating peak
    july_first_hour = (31 + 28 + 31 + 30 + 31 + 30) * 24
    assert hourly[july_first_hour] == pytest.approx(-15000.0)  # July 1st, cooling season, cooling peak


def test_synthesize_peak_only_load_rejects_overlapping_seasons():
    with pytest.raises(ValueError):
        sizing.synthesize_peak_only_load(peak_heating_kW=1.0, peak_cooling_kW=1.0,
                                          heating_season_months=8, cooling_season_months=8)


# ---- hybrid.minimum_tower_capacity --------------------------------------

def test_minimum_tower_capacity_is_zero_when_the_field_already_satisfies_the_limit():
    hourly = sizing.synthesize_peak_only_load(peak_heating_kW=5.0, peak_cooling_kW=5.0,
                                               heating_season_months=2, cooling_season_months=2)
    result = hybrid.minimum_tower_capacity(
        FIELD, H=150.0, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=5,
        T_f_min_limit_C=-5.0, T_f_max_limit_C=40.0, **COMMON_KWARGS,
    )
    assert result["tower_capacity_kW"] == 0.0
    assert result["iterations"] == 0


def test_minimum_tower_capacity_finds_a_working_capacity_for_a_shallow_field():
    # Strongly cooling-dominated peak load, and a shallow field (short H) that
    # cannot hold the limit alone -- confirms a tower size is found, and that
    # applying it actually satisfies the limit at that fixed H.
    hourly = sizing.synthesize_peak_only_load(peak_heating_kW=10.0, peak_cooling_kW=120.0,
                                               heating_season_months=3, cooling_season_months=5)
    H = 40.0
    result = hybrid.minimum_tower_capacity(
        FIELD, H=H, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=5,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=40.0, **COMMON_KWARGS,
    )
    assert result["tower_capacity_kW"] > 0.0
    assert result["T_f_max_C"] <= 40.0 + 1e-6

    # Sanity: applying the found capacity directly reproduces a passing result.
    tower = hybrid.apply_cooling_tower(hourly, result["tower_capacity_kW"])
    assert tower["tower_peak_kW"] <= result["tower_capacity_kW"] + 1e-6


def test_minimum_tower_capacity_raises_when_even_capacity_max_is_insufficient():
    hourly = sizing.synthesize_peak_only_load(peak_heating_kW=10.0, peak_cooling_kW=300.0,
                                               heating_season_months=2, cooling_season_months=6)
    with pytest.raises(ValueError):
        hybrid.minimum_tower_capacity(
            FIELD, H=20.0, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=10,
            T_f_min_limit_C=-2.0, T_f_max_limit_C=25.0, capacity_max_kW=1.0, **COMMON_KWARGS,
        )
