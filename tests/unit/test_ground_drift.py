"""Tests for T_g_drift_C_per_year on geothermal.simulation.run_hourly_simulation /
geothermal.sizing.size_field -- the "long-term ground temperature drift" pick
from the 2026-09-28 accuracy audit (Phase 1 item 4).

Validation strategy: verify the drift is applied EXACTLY as documented (a
linear T_g(t) trend), by comparing the mean fluid temperature shift at two
different points in a multi-year simulation against the analytically
expected shift (drift_rate * elapsed_years) -- not just "the number changed."
"""
import numpy as np
import pytest

from geothermal import fields, simulation, sizing

FIELD = fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
K_S, K_G, T_G, ALPHA = 2.0, 1.5, 12.0, 1.0e-6
YEARS = 10

_hours = np.arange(1, 8761)
ONE_YEAR = (25e3 * np.cos(2 * np.pi * _hours / 8760) + 8e3).tolist()
HOURLY_LOAD = ONE_YEAR * YEARS


def _run(drift_C_per_year):
    return simulation.run_hourly_simulation(
        FIELD, ALPHA, K_S, K_G, T_G, PIPE, 0.30, "MPG", 25.0, 0.0, HOURLY_LOAD,
        T_g_drift_C_per_year=drift_C_per_year,
    )


def test_zero_drift_is_the_default_and_reproduces_original_behavior():
    explicit_zero = _run(0.0)
    omitted = simulation.run_hourly_simulation(
        FIELD, ALPHA, K_S, K_G, T_G, PIPE, 0.30, "MPG", 25.0, 0.0, HOURLY_LOAD,
    )
    assert explicit_zero["T_f_C"] == omitted["T_f_C"]
    assert explicit_zero["T_b_C"] == omitted["T_b_C"]


def test_drift_shifts_mean_fluid_temperature_by_the_analytically_expected_amount():
    # A 0.05 C/yr trend over a 10-year run: by year 1 (elapsed ~0.5 yr on average) the
    # shift should be ~0.025 C; by year 10 (elapsed ~9.5 yr on average) ~0.475 C. This is
    # an exact linear-model check, not just "warmer with drift on."
    drift_rate = 0.05
    baseline = _run(0.0)
    drifted = _run(drift_rate)

    year1_shift = np.mean(drifted["T_f_C"][:8760]) - np.mean(baseline["T_f_C"][:8760])
    year10_shift = np.mean(drifted["T_f_C"][-8760:]) - np.mean(baseline["T_f_C"][-8760:])

    assert year1_shift == pytest.approx(drift_rate * 0.5, abs=0.005)
    assert year10_shift == pytest.approx(drift_rate * 9.5, abs=0.005)


def test_negative_drift_cools_the_field_over_time():
    # Sign sanity check: a negative rate (an unusual but valid input -- e.g. a
    # site-specific cooling trend) should shift results the opposite direction.
    baseline = _run(0.0)
    cooling = _run(-0.05)
    assert np.mean(cooling["T_f_C"][-8760:]) < np.mean(baseline["T_f_C"][-8760:])


def test_tower_control_hook_still_receives_t_g_for_hour_zero_unaffected_by_drift():
    # Hour 0's tower_control call uses T_g directly (see run_hourly_simulation's own
    # code) -- at t=0 the drift term is exactly 0, so this must be unaffected either way.
    seen_prev_T_b = []

    def spy_control(hour_index, raw_load_W, prev_T_b_C):
        if hour_index == 0:
            seen_prev_T_b.append(prev_T_b_C)
        return raw_load_W, 0.0

    simulation.run_hourly_simulation(
        FIELD, ALPHA, K_S, K_G, T_G, PIPE, 0.30, "MPG", 25.0, 0.0, HOURLY_LOAD,
        T_g_drift_C_per_year=0.05, tower_control=spy_control,
    )
    assert seen_prev_T_b == [T_G]


def test_size_field_passes_drift_through_to_every_trial():
    field_template = {"layout": "rectangle", "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    hourly = sizing.synthesize_hourly_load(
        [3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800],
        [0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0],
        [25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24],
        [0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0],
    )
    kwargs = dict(
        field_template=field_template, alpha=ALPHA, k_s=K_S, k_g=K_G, T_g=T_G, pipe_config=PIPE,
        m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0,
        hourly_load_W=hourly, simulation_period_years=10,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=20.0, H_min=20.0, H_max=150.0, tol_m=2.0, max_iter=15,
    )
    no_drift = sizing.size_field(**kwargs)
    warming = sizing.size_field(**kwargs, T_g_drift_C_per_year=0.1)

    # A field that's going to run warmer at the end of its design life (climate/UHI
    # warming compounding on top of its own thermal buildup) needs to be sized AT LEAST
    # as deep to still meet the same max-temperature limit over the same period.
    assert warming["H_m"] >= no_drift["H_m"]
