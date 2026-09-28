"""Tests for pipes.suggest_flow_rate_kg_s (new), and for the double U-tube
("multiple_u_tube") pipe type -- which pipes.build_pipe/effective_resistance
and pygfunction_pipe_type_str already supported, but which had zero test
coverage anywhere in this repo before now. Exercised here before exposing it
through the WPF "Pipe configuration" selector.
"""
import pytest

from geothermal import pipes

SINGLE_U = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}

DOUBLE_U_PARALLEL = {
    "type": "multiple_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0), (0.0, -0.03), (0.0, 0.03)],
    "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4, "nPipes": 2, "config": "parallel",
}

DOUBLE_U_SERIES = {**DOUBLE_U_PARALLEL, "config": "series"}

COMMON_KWARGS = dict(H=150.0, D=2.0, r_b=0.075, k_s=2.0, k_g=1.5)


# ---- double U-tube (previously untested code path) ----------------------

def test_double_u_tube_parallel_lowers_resistance_vs_single_at_matched_per_leg_flow():
    # A double U-tube only shows its advantage (more conductive pipe surface) once the
    # total borehole flow is scaled up so each leg still carries as much flow as a
    # single U-tube's legs do -- each parallel leg only gets m_flow_borehole/nPipes
    # (see build_pipe). Matching per-leg flow here (0.60 total / 2 legs = 0.30/leg,
    # same as the single U-tube's 0.30/leg) isolates the "more pipe" effect.
    single = pipes.effective_resistance(
        SINGLE_U, m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        **COMMON_KWARGS)
    double = pipes.effective_resistance(
        DOUBLE_U_PARALLEL, m_flow_borehole=0.60, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        **COMMON_KWARGS)

    assert double["R_b_star_mK_W"] < single["R_b_star_mK_W"]


def test_double_u_tube_at_same_total_flow_can_be_worse_than_single():
    # Documents a real, non-obvious finding (not a bug): at the SAME total borehole
    # flow, a parallel double U-tube's legs each carry HALF the single U-tube's
    # per-leg flow, which lowers each leg's Reynolds number and raises its convective
    # film resistance enough to outweigh the benefit of more conductive paths. This is
    # exactly why suggest_flow_rate_kg_s scales its turbulence-governed suggestion
    # with nPipes -- a double U-tube needs more total flow, not the same, to pay off.
    single = pipes.effective_resistance(
        SINGLE_U, m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        **COMMON_KWARGS)
    double_same_total_flow = pipes.effective_resistance(
        DOUBLE_U_PARALLEL, m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        **COMMON_KWARGS)

    assert double_same_total_flow["R_b_star_mK_W"] > single["R_b_star_mK_W"]


def test_double_u_tube_series_also_builds_without_error():
    result = pipes.effective_resistance(
        DOUBLE_U_SERIES, m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        **COMMON_KWARGS)
    assert result["R_b_star_mK_W"] > 0


def test_double_u_tube_pipe_type_str_maps_for_mift_gfunctions():
    assert pipes.pygfunction_pipe_type_str(DOUBLE_U_PARALLEL) == "DOUBLE_UTUBE_PARALLEL"
    assert pipes.pygfunction_pipe_type_str(DOUBLE_U_SERIES) == "DOUBLE_UTUBE_SERIES"


# ---- suggest_flow_rate_kg_s ---------------------------------------------

def test_suggest_flow_rate_capacity_governs_for_a_large_load():
    result = pipes.suggest_flow_rate_kg_s(
        capacity_W=20000.0, config=SINGLE_U, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0)
    assert result["governing"] == "capacity"
    assert result["flow_rate_kg_s"] == pytest.approx(result["flow_from_capacity_kg_s"])
    assert result["flow_rate_kg_s"] > result["flow_from_turbulence_kg_s"]


def test_suggest_flow_rate_turbulence_governs_for_a_small_load():
    result = pipes.suggest_flow_rate_kg_s(
        capacity_W=200.0, config=SINGLE_U, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0)
    assert result["governing"] == "turbulence"
    assert result["flow_rate_kg_s"] == pytest.approx(result["flow_from_turbulence_kg_s"])
    assert result["flow_rate_kg_s"] > result["flow_from_capacity_kg_s"]


def test_suggest_flow_rate_double_u_tube_parallel_needs_double_the_borehole_flow():
    # Each leg of a parallel double U-tube only carries half the borehole's total flow
    # (per pipes.build_pipe's own convention), so hitting the same per-leg Reynolds
    # number needs twice the total borehole flow rate vs. a single U-tube.
    single = pipes.suggest_flow_rate_kg_s(
        capacity_W=200.0, config=SINGLE_U, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0)
    double = pipes.suggest_flow_rate_kg_s(
        capacity_W=200.0, config=DOUBLE_U_PARALLEL, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0)

    assert double["flow_from_turbulence_kg_s"] == pytest.approx(2.0 * single["flow_from_turbulence_kg_s"], rel=1e-6)


def test_suggest_flow_rate_rejects_coaxial():
    coaxial = {"type": "coaxial", "r_in": [0.02, 0.03], "r_out": [0.025, 0.032], "k_p": [0.4, 0.4]}
    with pytest.raises(NotImplementedError):
        pipes.suggest_flow_rate_kg_s(
            capacity_W=1000.0, config=coaxial, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0)


def test_suggest_flow_rate_rejects_non_positive_delta_t():
    with pytest.raises(ValueError):
        pipes.suggest_flow_rate_kg_s(
            capacity_W=1000.0, config=SINGLE_U, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
            design_delta_T_C=0.0)


def test_suggest_flow_rate_rejects_non_positive_min_reynolds():
    with pytest.raises(ValueError):
        pipes.suggest_flow_rate_kg_s(
            capacity_W=1000.0, config=SINGLE_U, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
            min_reynolds=0.0)
