"""Tests for the gfunc_boundary_condition="MIFT" option on
geothermal.simulation.run_hourly_simulation / geothermal.sizing.size_field --
the "UIFT g-functions" pick from the 2026-09-28 accuracy audit (Phase 1
item 5).

The validation strategy: show the two boundary conditions agree closely in a
regime where UBWT's uniform-wall-temperature idealization is a reasonable
approximation (a single borehole, or a modest field at a normal flow rate),
and show them diverge measurably where it isn't (a larger field at a low
per-borehole flow rate) -- a real accuracy difference, not just "MIFT runs
without crashing."
"""
import numpy as np
import pytest

from geothermal import fields, simulation, sizing

PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
K_S, K_G, T_G, ALPHA = 2.0, 1.5, 12.0, 1.0e-6

_hours = np.arange(1, 8761)
HOURLY_LOAD = (25e3 * np.cos(2 * np.pi * _hours / 8760) + 8e3).tolist()


def _run(field, m_flow, bc, hourly=HOURLY_LOAD):
    return simulation.run_hourly_simulation(
        field, ALPHA, K_S, K_G, T_G, PIPE, m_flow, "MPG", 25.0, 0.0, hourly,
        gfunc_boundary_condition=bc,
    )


def test_default_is_ubwt_and_matches_calling_it_explicitly():
    field = fields.custom_field(x=[0.0], y=[0.0], H=150.0, D=2.0, r_b=0.075)
    default = simulation.run_hourly_simulation(
        field, ALPHA, K_S, K_G, T_G, PIPE, 0.30, "MPG", 25.0, 0.0, HOURLY_LOAD,
    )
    explicit = _run(field, 0.30, "UBWT")
    assert default["T_f_C"] == explicit["T_f_C"]
    assert default["gfunc_boundary_condition"] == "UBWT"


def test_rejects_unknown_gfunc_boundary_condition():
    field = fields.custom_field(x=[0.0], y=[0.0], H=150.0, D=2.0, r_b=0.075)
    with pytest.raises(ValueError, match="gfunc_boundary_condition"):
        _run(field, 0.30, "UHTR")


def test_network_resistance_equals_single_borehole_r_b_star_for_identical_parallel_boreholes():
    # The two boundary conditions' offset-to-fluid-temperature step turns out
    # to be numerically identical for an all-parallel field of otherwise-
    # identical boreholes -- so any T_f difference below comes entirely from
    # the g-function itself, not from this offset. Documented in
    # run_hourly_simulation's own docstring; pinned here so a future change
    # that breaks this equivalence is caught.
    field = fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    ubwt = _run(field, 0.30, "UBWT")
    mift = _run(field, 0.30, "MIFT")
    assert mift["R_b_star_mK_W"] == pytest.approx(ubwt["R_b_star_mK_W"], rel=1e-9)


def test_mift_and_ubwt_agree_closely_at_a_normal_flow_rate():
    field = fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    ubwt = _run(field, 0.30, "UBWT")
    mift = _run(field, 0.30, "MIFT")
    diffs = np.abs(np.array(ubwt["T_f_C"]) - np.array(mift["T_f_C"]))
    assert diffs.max() < 0.1


def test_mift_and_ubwt_diverge_measurably_at_low_flow_on_a_larger_field():
    # Same physics, pushed into the regime where UBWT's idealization breaks
    # down: more boreholes (more field-wide thermal interference) and a much
    # lower per-borehole flow rate (more temperature rise along the flow
    # path, which UBWT's uniform-wall-temperature assumption cannot see).
    field = fields.rectangle_field(N_1=6, N_2=6, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    ubwt = _run(field, 0.03, "UBWT")
    mift = _run(field, 0.03, "MIFT")
    diffs = np.abs(np.array(ubwt["T_f_C"]) - np.array(mift["T_f_C"]))
    assert diffs.max() > 0.3  # a real, not rounding-noise, difference
    assert diffs.mean() > 0.1


def test_size_field_passes_gfunc_boundary_condition_through_to_every_trial():
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
        hourly_load_W=hourly, simulation_period_years=1,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=20.0, H_min=20.0, H_max=150.0, tol_m=2.0, max_iter=15,
    )
    result_ubwt = sizing.size_field(**kwargs)
    result_mift = sizing.size_field(**kwargs, gfunc_boundary_condition="MIFT")

    assert result_ubwt["gfunc_boundary_condition"] == "UBWT"
    assert result_mift["gfunc_boundary_condition"] == "MIFT"
    # At this flow rate/field size they should be close (regression against the
    # per-hour test above), but need not be bit-identical.
    assert result_mift["H_m"] == pytest.approx(result_ubwt["H_m"], rel=0.05)
