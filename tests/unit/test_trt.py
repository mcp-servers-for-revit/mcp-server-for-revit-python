"""Tests for geothermal.trt: TRT parameter estimation.

The main validation test generates a synthetic TRT curve with
geothermal.simulation.run_hourly_simulation (an INDEPENDENT numerical method
-- pygfunction's approximate ClaessonJaved load-aggregation convolution, vs.
trt.py's own exact discrete convolution) at known k_s/R_b, then checks that
estimate_ground_properties_from_trt recovers those known values from the
simulated data. Agreement between two different algorithms for the same
physics is real evidence of correctness, not just "the optimizer found what
I told it to."
"""
import numpy as np
import pytest

from geothermal import fields, gfunctions, pipes, simulation, trt

FIELD = fields.custom_field(x=[0.0], y=[0.0], H=150.0, D=2.0, r_b=0.075)
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
K_S_TRUE = 2.0       # W/m.K
RHO_CP = 2.4e6        # J/m3.K, assumed known (not fit -- see trt.py's module docstring)
T_G = 12.0            # degC
N_HOURS = 72          # 3-day TRT, hourly logging
Q_INJECT_W = -8000.0  # negative = heat injected (this engine's sign convention: +ve = extracted)


def _synthetic_measurement():
    """A 3-day constant-injection TRT, simulated via the existing, already-
    validated hourly engine at a KNOWN k_s/R_b -- the ground truth this
    module's fit is checked against.
    """
    alpha_true = K_S_TRUE / RHO_CP
    hourly_Q = [Q_INJECT_W] * N_HOURS
    result = simulation.run_hourly_simulation(
        FIELD, alpha_true, K_S_TRUE, k_g=1.5, T_g=T_G, pipe_config=PIPE, m_flow_borehole=0.30,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0, hourly_load_W=hourly_Q,
    )
    time_s = [(i + 1) * 3600.0 for i in range(N_HOURS)]
    return time_s, result["T_f_C"], hourly_Q, result["R_b_star_mK_W"]


def test_uhtr_and_ubwt_agree_for_a_single_borehole():
    # trt.py's module docstring relies on this: with only one borehole,
    # "uniform across boreholes" is trivially satisfied either way, so UBWT
    # (what run_hourly_simulation/trt.py both use) and UHTR (what a TRT's
    # constant-heat-rate boundary condition would nominally call for) should
    # be numerically the same g-function.
    time = gfunctions.time_grid(3600.0, 72 * 3600.0, 20)
    g_ubwt = gfunctions.evaluate(FIELD, 1.0e-6, time, boundary_condition="UBWT")["g"]
    g_uhtr = gfunctions.evaluate(FIELD, 1.0e-6, time, boundary_condition="UHTR")["g"]
    assert g_ubwt == pytest.approx(g_uhtr, abs=1e-3)


def test_recovers_known_k_s_and_r_b_jointly_from_synthetic_data():
    # Tolerances here are wider than "should be exact" on purpose: the
    # synthetic T_f_C comes from run_hourly_simulation's APPROXIMATE
    # ClaessonJaved load-aggregation convolution, while this module fits
    # against its own EXACT discrete convolution -- a small, real gap
    # between the two (traced by hand: <0.03 C RMSE, not a bug) is expected
    # and is itself evidence the fit is finding a genuinely different, more
    # exact algorithm's best answer rather than just returning the seed.
    time_s, T_f_C, hourly_Q, R_b_true = _synthetic_measurement()

    result = trt.estimate_ground_properties_from_trt(
        H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=T_f_C, Q_W=hourly_Q,
        T_g=T_G, rho_cp_J_m3K=RHO_CP,
        k_s_guess=1.5, R_b_guess=0.10,  # deliberately off from the true values
        t_min_s=12 * 3600.0,
    )

    assert result["k_s_W_mK"] == pytest.approx(K_S_TRUE, rel=0.035)
    assert result["R_b_mK_W"] == pytest.approx(R_b_true, rel=0.015)
    assert result["R_b_was_fixed"] is False
    assert result["R_b_stderr_mK_W"] is not None
    assert result["n_points_total"] == N_HOURS
    # t_min_s=12h keeps the hour-12 sample itself (time_s[i] >= t_min_s), so
    # only the first 11 hourly points (hours 1-11) are excluded, not 12.
    assert result["n_points_used"] == N_HOURS - 11
    assert result["rmse_C"] < 0.05
    assert len(result["T_f_predicted_C"]) == N_HOURS


def test_fixing_r_b_from_a_known_pipe_gives_a_tighter_k_s_estimate():
    time_s, T_f_C, hourly_Q, R_b_true = _synthetic_measurement()

    joint = trt.estimate_ground_properties_from_trt(
        H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=T_f_C, Q_W=hourly_Q,
        T_g=T_G, rho_cp_J_m3K=RHO_CP, k_s_guess=1.5, R_b_guess=0.10, t_min_s=12 * 3600.0,
    )
    fixed = trt.estimate_ground_properties_from_trt(
        H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=T_f_C, Q_W=hourly_Q,
        T_g=T_G, rho_cp_J_m3K=RHO_CP, k_s_guess=1.5, R_b_mK_W=R_b_true, t_min_s=12 * 3600.0,
    )

    assert fixed["R_b_was_fixed"] is True
    assert fixed["R_b_mK_W"] == R_b_true
    assert fixed["R_b_stderr_mK_W"] is None
    assert fixed["k_s_W_mK"] == pytest.approx(K_S_TRUE, rel=0.02)
    # Holding a known R_b fixed removes one degree of freedom the optimizer
    # could otherwise trade off against k_s -- the estimate should be at
    # least as tight, usually tighter.
    assert fixed["k_s_stderr_W_mK"] <= joint["k_s_stderr_W_mK"] + 1e-9


def test_rejects_giving_both_or_neither_of_r_b_mK_W_and_r_b_guess():
    time_s, T_f_C, hourly_Q, R_b_true = _synthetic_measurement()
    common = dict(
        H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=T_f_C, Q_W=hourly_Q,
        T_g=T_G, rho_cp_J_m3K=RHO_CP, k_s_guess=1.5, t_min_s=12 * 3600.0,
    )
    with pytest.raises(ValueError, match="either R_b_mK_W .* or R_b_guess"):
        trt.estimate_ground_properties_from_trt(**common, R_b_mK_W=R_b_true, R_b_guess=0.1)
    with pytest.raises(ValueError, match="R_b_guess is required"):
        trt.estimate_ground_properties_from_trt(**common)


def test_rejects_a_non_uniform_time_grid():
    time_s = [3600.0, 7200.0, 20000.0, 25000.0]
    with pytest.raises(ValueError, match="evenly spaced"):
        trt.estimate_ground_properties_from_trt(
            H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=[10.0, 11.0, 12.0, 13.0],
            Q_W=-1000.0, T_g=T_G, rho_cp_J_m3K=RHO_CP, k_s_guess=2.0, R_b_guess=0.1, t_min_s=0.0,
        )


def test_rejects_t_min_s_that_leaves_too_few_points():
    time_s, T_f_C, hourly_Q, _ = _synthetic_measurement()
    with pytest.raises(ValueError, match="not enough to fit"):
        trt.estimate_ground_properties_from_trt(
            H=150.0, D=2.0, r_b=0.075, time_s=time_s, T_f_C=T_f_C, Q_W=hourly_Q,
            T_g=T_G, rho_cp_J_m3K=RHO_CP, k_s_guess=2.0, R_b_guess=0.1,
            t_min_s=time_s[-1],  # only the very last point would remain
        )
