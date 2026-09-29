"""Tests for geothermal.uncertainty: Monte Carlo sizing (P50/P90 reporting)
around geothermal.sizing.size_field.

size_field's own bisection is not cheap (a real design run may take several
seconds), so the trial counts and tol_m/max_iter used here are deliberately
loosened FOR TEST SPEED ONLY -- not a recommended production configuration,
see geothermal/README.md's uncertainty section for that distinction.
"""
import pytest

from geothermal import sizing, uncertainty

FIELD = {"layout": "rectangle", "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
HOURLY = sizing.synthesize_hourly_load(
    [3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800],
    [0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0],
    [25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24],
    [0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0],
)

BASE_KWARGS = dict(
    field_template=FIELD, alpha=1.0e-6, k_s=2.0, k_g=1.5, T_g=12.0, pipe_config=PIPE,
    m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0,
    hourly_load_W=HOURLY, simulation_period_years=1,
    T_f_min_limit_C=-2.0, T_f_max_limit_C=20.0, H_min=20.0, H_max=150.0,
    tol_m=3.0, max_iter=10,  # loosened for test speed, see module docstring
)


def test_zero_variance_reproduces_the_deterministic_size_field_result():
    deterministic = sizing.size_field(**BASE_KWARGS)

    result = uncertainty.size_field_monte_carlo(
        BASE_KWARGS, uncertain_inputs={"k_s": {"dist": "normal", "mean": 2.0, "stddev": 0.0}},
        n_samples=5, random_seed=1,
    )

    assert result["n_failed"] == 0
    assert result["H_m_samples"] == pytest.approx([deterministic["H_m"]] * 5)
    assert result["H_m_stddev"] == pytest.approx(0.0, abs=1e-9)
    for row in result["percentiles"]:
        assert row["H_m"] == pytest.approx(deterministic["H_m"])


def test_p90_is_at_least_p50_and_brackets_the_deterministic_midpoint():
    deterministic = sizing.size_field(**BASE_KWARGS)

    result = uncertainty.size_field_monte_carlo(
        BASE_KWARGS, uncertain_inputs={"k_s": {"dist": "normal", "mean": 2.0, "stddev": 0.2}},
        n_samples=25, random_seed=42,
    )

    by_p = {row["p"]: row["H_m"] for row in result["percentiles"]}
    assert by_p[90.0] >= by_p[50.0]
    # Lower k_s (harder ground) needs a DEEPER field -- H is monotonically
    # decreasing in k_s over this range, so a +/-0.2 W/m.K spread around the
    # deterministic 2.0 should keep the sample median within a modest band
    # of the single-point answer, not off in unrelated territory.
    assert by_p[50.0] == pytest.approx(deterministic["H_m"], rel=0.15)
    assert result["H_m_stddev"] > 0.0  # genuine spread, not a constant


def test_uniform_distribution_samples_stay_within_its_bounds():
    result = uncertainty.size_field_monte_carlo(
        BASE_KWARGS, uncertain_inputs={"T_g": {"dist": "uniform", "low": 11.0, "high": 13.0}},
        n_samples=15, random_seed=7,
    )
    T_g_samples = result["sampled_inputs"]["T_g"]
    assert all(11.0 <= v <= 13.0 for v in T_g_samples)


def test_infeasible_trials_are_censored_at_h_max_and_counted():
    # A k_s low enough occasionally sampled that H_max=150m can't satisfy the
    # limits: size_field's own ValueError must not crash or silently drop
    # the trial -- it's recorded as a censored H_m=H_max and counted.
    result = uncertainty.size_field_monte_carlo(
        BASE_KWARGS, uncertain_inputs={"k_s": {"dist": "uniform", "low": 0.3, "high": 0.5}},
        n_samples=15, random_seed=3,
    )
    assert result["n_failed"] > 0
    assert result["fraction_failed"] == pytest.approx(result["n_failed"] / 15)
    assert len(result["failed_trial_inputs"]) == result["n_failed"]
    assert max(result["H_m_samples"]) == pytest.approx(150.0)


def test_rejects_unknown_uncertain_input_name():
    with pytest.raises(ValueError, match="not size_field parameters"):
        uncertainty.size_field_monte_carlo(
            BASE_KWARGS, uncertain_inputs={"not_a_real_param": {"dist": "normal", "mean": 1, "stddev": 0.1}},
            n_samples=5,
        )


def test_rejects_unknown_distribution_name():
    with pytest.raises(ValueError, match="unknown dist"):
        uncertainty.size_field_monte_carlo(
            BASE_KWARGS, uncertain_inputs={"k_s": {"dist": "triangular", "mean": 2.0}}, n_samples=5,
        )


def test_rejects_empty_uncertain_inputs():
    with pytest.raises(ValueError, match="at least one"):
        uncertainty.size_field_monte_carlo(BASE_KWARGS, uncertain_inputs={}, n_samples=5)


def test_rejects_non_positive_n_samples():
    with pytest.raises(ValueError, match="n_samples must be > 0"):
        uncertainty.size_field_monte_carlo(
            BASE_KWARGS, uncertain_inputs={"k_s": {"dist": "normal", "mean": 2.0, "stddev": 0.1}}, n_samples=0,
        )
