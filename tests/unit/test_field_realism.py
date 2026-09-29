"""Tests for the "field realism" accuracy-audit pick (Phase 1 items 10a/10b):
mixed per-borehole depths threaded into sizing.size_field's bisection
(depth_scale), and series/mixed network connectivity threaded into
simulation.run_hourly_simulation's MIFT path (bore_connectivity) -- both
previously existed as isolated primitives (fields.custom_field,
geothermal.networks) with no route into the actual sizing/simulation loop.
"""
import math

import pytest

from geothermal import fields, networks, simulation, sizing

PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
K_S, K_G, T_G, ALPHA = 2.0, 1.5, 12.0, 1.0e-6

HOURLY_LOAD = [15e3 * math.cos(2 * math.pi * h / 8760) + 5e3 for h in range(1, 8761)]


# ---- depth_scale (mixed borehole depths via a single-scalar bisection) --------

def test_default_depth_scale_reproduces_original_uniform_behavior():
    field_template = {"layout": "rectangle", "N_1": 4, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    field_explicit_none = sizing.build_field_at_depth(field_template, 100.0, depth_scale=None)
    field_omitted = sizing.build_field_at_depth(field_template, 100.0)
    assert field_explicit_none == field_omitted
    assert all(bh["H"] == 100.0 for bh in field_omitted["boreholes"])


def test_depth_scale_produces_correct_per_borehole_depths():
    field_template = {"layout": "rectangle", "N_1": 4, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    field = sizing.build_field_at_depth(field_template, 100.0, depth_scale=[1.0, 1.0, 0.9, 0.9])
    depths = [bh["H"] for bh in field["boreholes"]]
    assert depths == pytest.approx([100.0, 100.0, 90.0, 90.0])
    # x/y/D/r_b must come from the SAME uniform layout the depths are then overridden on.
    uniform = sizing.build_field_at_depth(field_template, 100.0)
    for scaled_bh, uniform_bh in zip(field["boreholes"], uniform["boreholes"]):
        assert scaled_bh["x"] == uniform_bh["x"]
        assert scaled_bh["y"] == uniform_bh["y"]


def test_depth_scale_rejects_wrong_length():
    field_template = {"layout": "rectangle", "N_1": 4, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    with pytest.raises(ValueError, match="depth_scale has"):
        sizing.build_field_at_depth(field_template, 100.0, depth_scale=[1.0, 1.0])


def test_depth_scale_rejects_non_positive_entries():
    field_template = {"layout": "rectangle", "N_1": 2, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    with pytest.raises(ValueError, match="must all be > 0"):
        sizing.build_field_at_depth(field_template, 100.0, depth_scale=[1.0, 0.0])


def test_size_field_with_depth_scale_solves_a_deeper_nominal_h_and_matches_total_length():
    # Two of four boreholes forced to 90% depth (e.g. shallower near a site constraint):
    # the bisection should compensate by solving a LARGER nominal H than the fully
    # uniform case, and the reported total_length_m must exactly match H_m * sum(scale) --
    # not just "some field got built," but the specific geometry this scale implies.
    field_template = {"layout": "rectangle", "N_1": 4, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    hourly = sizing.synthesize_hourly_load(
        [3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800],
        [0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0],
        [25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24],
        [0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0],
    )
    kwargs = dict(
        field_template=field_template, alpha=ALPHA, k_s=K_S, k_g=K_G, T_g=T_G, pipe_config=PIPE,
        m_flow_borehole=0.30, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0,
        hourly_load_W=hourly, simulation_period_years=3,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=20.0, H_min=20.0, H_max=400.0, tol_m=2.0, max_iter=15,
    )
    uniform = sizing.size_field(**kwargs)
    scaled = sizing.size_field(**kwargs, depth_scale=[1.0, 1.0, 0.9, 0.9])

    assert scaled["H_m"] >= uniform["H_m"]
    assert scaled["total_length_m"] == pytest.approx(scaled["H_m"] * 3.8, rel=1e-6)


# ---- bore_connectivity (series/mixed network wiring in simulation/sizing) -----

def _run(field, m_flow, bc):
    return simulation.run_hourly_simulation(
        field, ALPHA, K_S, K_G, T_G, PIPE, m_flow, "MPG", 25.0, 0.0, HOURLY_LOAD,
        gfunc_boundary_condition="MIFT", bore_connectivity=bc,
    )


def test_bore_connectivity_requires_mift():
    field = fields.rectangle_field(N_1=3, N_2=1, B_1=6.0, B_2=6.0, H=100.0, D=2.0, r_b=0.075)
    with pytest.raises(ValueError, match="requires gfunc_boundary_condition='MIFT'"):
        simulation.run_hourly_simulation(
            field, ALPHA, K_S, K_G, T_G, PIPE, 0.30, "MPG", 25.0, 0.0, HOURLY_LOAD,
            gfunc_boundary_condition="UBWT", bore_connectivity=networks.all_series_connectivity(3),
        )


def test_series_connectivity_genuinely_changes_the_result_vs_parallel():
    # Same field, same PER-CIRCUIT flow rate -- parallel has 3 circuits (each borehole its
    # own), series has 1 (the whole string) -- so this is a real hydraulic difference, not
    # just a label. Proves bore_connectivity is actually wired through to the g-function
    # and resistance calc, not silently ignored.
    field = fields.rectangle_field(N_1=3, N_2=1, B_1=6.0, B_2=6.0, H=100.0, D=2.0, r_b=0.075)
    parallel = _run(field, 0.20, None)
    series = _run(field, 0.20, networks.all_series_connectivity(3))

    assert series["R_b_star_mK_W"] != pytest.approx(parallel["R_b_star_mK_W"], rel=1e-6)
    assert series["T_f_C"] != parallel["T_f_C"]


def test_size_field_passes_bore_connectivity_through():
    field_template = {"layout": "rectangle", "N_1": 3, "N_2": 1, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
    hourly = sizing.synthesize_hourly_load(
        [3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800],
        [0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0],
        [25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24],
        [0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0],
    )
    # m_flow_borehole=0.60 here means "flow through the single series string" (1
    # circuit) -- a series string needs more total flow than 3 independent parallel
    # boreholes would to stay in a feasible range at this H_max.
    result = sizing.size_field(
        field_template, ALPHA, K_S, K_G, T_G, PIPE, m_flow_borehole=0.60,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0,
        hourly_load_W=hourly, simulation_period_years=2,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=20.0, H_min=20.0, H_max=300.0, tol_m=3.0, max_iter=12,
        gfunc_boundary_condition="MIFT", bore_connectivity=networks.all_series_connectivity(3),
    )
    assert result["H_m"] > 0
    assert result["gfunc_boundary_condition"] == "MIFT"
