"""Tests for geothermal.thermal_mass -- the "short-term grout/fluid
capacitance" pick from the 2026-09-28 accuracy audit (Phase 1 category 2).
Validation strategy: check the cross-sectional-area geometry against a hand
calculation for both single U-tube and coaxial, then prove the RC lag
integrated into simulation.run_hourly_simulation actually behaves like a
first-order step response (starts at zero offset, relaxes toward the
quasi-steady R_b* prediction, converges within a few time constants) rather
than just producing "some different number."
"""
import math

import pytest

from geothermal import fields, pipes, simulation, thermal_mass

SINGLE_UTUBE = {
    "type": "single_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0)],
    "r_in": 0.0131,
    "r_out": 0.0160,
    "k_p": 0.4,
}
COAXIAL = {
    "type": "coaxial",
    "r_in": [0.0221, 0.0487],
    "r_out": [0.025, 0.055],
    "k_p": [0.4, 0.4],
}
R_B = 0.075
RHO_CP_GROUT = 3.8e6
RHO_CP_PIPE = 1.5e6


def test_single_u_tube_area_breakdown_matches_hand_calculation():
    result = thermal_mass.borehole_thermal_capacitance(
        SINGLE_UTUBE, R_B, RHO_CP_GROUT, RHO_CP_PIPE, "MPG", 25.0, 20.0,
    )
    r_in, r_out = 0.0131, 0.0160
    A_fluid_expected = 2 * math.pi * r_in**2
    A_pipe_expected = 2 * math.pi * (r_out**2 - r_in**2)
    A_grout_expected = math.pi * R_B**2 - 2 * math.pi * r_out**2
    assert result["A_fluid_m2"] == pytest.approx(A_fluid_expected)
    assert result["A_pipe_m2"] == pytest.approx(A_pipe_expected)
    assert result["A_grout_m2"] == pytest.approx(A_grout_expected)
    assert result["C_pipe_J_mK"] == pytest.approx(RHO_CP_PIPE * A_pipe_expected)
    assert result["C_grout_J_mK"] == pytest.approx(RHO_CP_GROUT * A_grout_expected)
    assert result["C_b_J_mK"] == pytest.approx(
        result["C_fluid_J_mK"] + result["C_pipe_J_mK"] + result["C_grout_J_mK"]
    )


def test_coaxial_area_breakdown_matches_hand_calculation():
    result = thermal_mass.borehole_thermal_capacitance(
        COAXIAL, R_B, RHO_CP_GROUT, RHO_CP_PIPE, "MPG", 25.0, 20.0,
    )
    r_in_i, r_in_o = 0.0221, 0.0487
    r_out_i, r_out_o = 0.025, 0.055
    A_fluid_expected = math.pi * r_in_i**2 + math.pi * (r_in_o**2 - r_out_i**2)
    A_pipe_expected = math.pi * (r_out_i**2 - r_in_i**2) + math.pi * (r_out_o**2 - r_in_o**2)
    A_grout_expected = math.pi * R_B**2 - math.pi * r_out_o**2
    assert result["A_fluid_m2"] == pytest.approx(A_fluid_expected)
    assert result["A_pipe_m2"] == pytest.approx(A_pipe_expected)
    assert result["A_grout_m2"] == pytest.approx(A_grout_expected)


def test_rejects_pipe_legs_that_do_not_fit_in_the_borehole():
    oversized = {**SINGLE_UTUBE, "r_out": 0.08}  # bigger than r_b itself
    with pytest.raises(ValueError, match="do not fit"):
        thermal_mass.borehole_thermal_capacitance(
            oversized, R_B, RHO_CP_GROUT, RHO_CP_PIPE, "MPG", 25.0, 20.0,
        )


def test_rejects_non_positive_material_capacities():
    with pytest.raises(ValueError, match="rho_cp_grout_J_m3K must be > 0"):
        thermal_mass.borehole_thermal_capacitance(SINGLE_UTUBE, R_B, 0.0, RHO_CP_PIPE, "MPG", 25.0, 20.0)
    with pytest.raises(ValueError, match="rho_cp_pipe_J_m3K must be > 0"):
        thermal_mass.borehole_thermal_capacitance(SINGLE_UTUBE, R_B, RHO_CP_GROUT, -1.0, "MPG", 25.0, 20.0)


def test_time_constant_is_the_product_of_resistance_and_capacitance():
    assert thermal_mass.borehole_time_constant_s(0.15, 66000.0) == pytest.approx(0.15 * 66000.0)
    with pytest.raises(ValueError, match="R_b_star_mK_W must be > 0"):
        thermal_mass.borehole_time_constant_s(0.0, 66000.0)
    with pytest.raises(ValueError, match="C_b_J_mK must be > 0"):
        thermal_mass.borehole_time_constant_s(0.15, 0.0)


# ---- lag behavior integrated into run_hourly_simulation --------------------

_FIELD = fields.rectangle_field(N_1=1, N_2=1, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=R_B)


def test_none_capacitance_reproduces_original_behavior_exactly():
    load = [4000.0] * 24
    baseline = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
    )
    with_none = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
        borehole_capacitance_J_mK=None,
    )
    assert with_none["T_f_C"] == baseline["T_f_C"]
    assert "borehole_time_constant_s" not in with_none


def test_capacitance_delays_the_first_hour_offset_toward_the_wall_temperature():
    # A load step at hour 0: with zero thermal mass the fluid offset from T_b is
    # the full quasi-steady -q'*R_b*/H immediately; with capacitance, the internal
    # mass hasn't warmed/cooled yet, so T_f(hour 0) must sit STRICTLY BETWEEN T_b
    # and the no-lag T_f (a partial step, not the full one, not zero either).
    load = [4000.0] * 48
    R = pipes.effective_resistance(SINGLE_UTUBE, 150.0, 2.0, R_B, 2.0, 1.5, 0.30, "MPG", 25.0, 20.0)["R_b_star_mK_W"]
    cap = thermal_mass.borehole_thermal_capacitance(SINGLE_UTUBE, R_B, RHO_CP_GROUT, RHO_CP_PIPE, "MPG", 25.0, 20.0)

    no_lag = simulation.run_hourly_simulation(_FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load)
    with_lag = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
        borehole_capacitance_J_mK=cap["C_b_J_mK"],
    )
    T_b0 = with_lag["T_b_C"][0]
    assert with_lag["T_b_C"] == no_lag["T_b_C"]  # ground side is untouched by this feature
    assert min(T_b0, no_lag["T_f_C"][0]) < with_lag["T_f_C"][0] < max(T_b0, no_lag["T_f_C"][0])
    assert with_lag["borehole_time_constant_s"] == pytest.approx(R * cap["C_b_J_mK"])


def test_lag_converges_to_the_no_lag_offset_after_several_time_constants():
    cap = thermal_mass.borehole_thermal_capacitance(SINGLE_UTUBE, R_B, RHO_CP_GROUT, RHO_CP_PIPE, "MPG", 25.0, 20.0)
    load = [4000.0] * 8760
    no_lag = simulation.run_hourly_simulation(_FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load)
    with_lag = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
        borehole_capacitance_J_mK=cap["C_b_J_mK"],
    )
    tau_hours = with_lag["borehole_time_constant_s"] / 3600.0
    late_hour = int(10 * tau_hours)  # 10 time constants: (1 - exp(-10)) ~= 0.9999546
    assert late_hour < 8760
    assert with_lag["T_f_C"][late_hour] == pytest.approx(no_lag["T_f_C"][late_hour], abs=0.01)


def test_larger_capacitance_produces_a_slower_lag():
    load = [4000.0] * 6
    small_cap = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
        borehole_capacitance_J_mK=20000.0,
    )
    large_cap = simulation.run_hourly_simulation(
        _FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load,
        borehole_capacitance_J_mK=200000.0,
    )
    no_lag = simulation.run_hourly_simulation(_FIELD, 1e-6, 2.0, 1.5, 12.0, SINGLE_UTUBE, 0.30, "MPG", 25.0, 20.0, load)
    # First-hour offset from T_b: a bigger thermal mass reacts less within one hour,
    # so its T_f(hour 0) must sit closer to T_b (and further from the no-lag value).
    T_b0 = no_lag["T_b_C"][0]
    dist_small = abs(small_cap["T_f_C"][0] - no_lag["T_f_C"][0])
    dist_large = abs(large_cap["T_f_C"][0] - no_lag["T_f_C"][0])
    assert dist_large > dist_small
    assert abs(large_cap["T_f_C"][0] - T_b0) < abs(small_cap["T_f_C"][0] - T_b0)
