"""Cross-check geothermal.sizing (our own, dependency-free sizing loop)
against geothermal.sizing_ghetool (GHEtool's published L3 method) on the same
field, ground, pipe and load case.

They are NOT expected to match exactly: ours drives the validated hourly
simulation with a deliberately simple monthly-to-hourly load synthesis
(geothermal.sizing.synthesize_hourly_load), while GHEtool's L3 method uses
its own convolution-based approach directly on monthly loads. This test
pins down how close "close" currently is, so a future change to either side
shows up as a real diff instead of silent drift. Per the user's own
direction: keep GHEtool until/unless the two are shown to agree well.

GHEtool's sizing runs via geothermal.sizing_ghetool_client, which spawns it
in a subprocess (geothermal.ghetool_worker) -- GHEtool monkey-patches
pygfunction's solver internals process-wide on import (see ghetool_worker's
docstring), which would otherwise corrupt the direct pygfunction calls this
same test session makes elsewhere (test_geothermal_report_examples.py's MIFT
tests broke this way before this test was isolated). Do not import
geothermal.sizing_ghetool directly in this file.
"""
import pytest

from geothermal import sizing, sizing_ghetool_client

FIELD = {"layout": "rectangle", "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
# Same cross layout HC.GeoBore's MainWindow.BuildPipeConfig() sends for "Double U-tube,
# parallel" -- legs at shank spacing on both axes, matching GHEtool.MultipleUTube's own
# config="diagonal" leg placement for number_of_pipes=2 (see sizing_ghetool._ghetool_pipe_data).
DOUBLE_U_PARALLEL = {
    "type": "multiple_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0), (0.0, -0.03), (0.0, 0.03)],
    "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4, "nPipes": 2, "config": "parallel",
}
DOUBLE_U_SERIES = {**DOUBLE_U_PARALLEL, "config": "series"}

BASELOAD_HEATING_KWH = [3000, 2800, 2000, 1000, 300, 0, 0, 0, 200, 900, 2000, 2800]
BASELOAD_COOLING_KWH = [0, 0, 100, 300, 900, 1800, 2200, 2000, 900, 300, 0, 0]
PEAK_HEATING_KW = [25, 24, 18, 10, 4, 0, 0, 0, 3, 9, 18, 24]
PEAK_COOLING_KW = [0, 0, 2, 5, 10, 18, 22, 20, 10, 5, 0, 0]

COMMON_KWARGS = dict(
    k_s=2.0, k_g=1.5, T_g=12.0, pipe_config=PIPE, m_flow_borehole=0.30,
    fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=0.0,
)


def test_sizing_methods_agree_within_documented_tolerance():
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)

    ours = sizing.size_field(
        FIELD, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=10,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=16.0, H_min=20.0, H_max=400.0, tol_m=1.0,
        **COMMON_KWARGS,
    )
    theirs = sizing_ghetool_client.size_field_isolated(
        field_template=FIELD, baseload_heating_kWh=BASELOAD_HEATING_KWH, baseload_cooling_kWh=BASELOAD_COOLING_KWH,
        peak_heating_kW=PEAK_HEATING_KW, peak_cooling_kW=PEAK_COOLING_KW,
        simulation_period_years=10, T_f_min_limit_C=-2.0, T_f_max_limit_C=16.0, method="L3",
        **COMMON_KWARGS,
    )

    pct_diff = 100 * (ours["H_m"] - theirs["H_m"]) / theirs["H_m"]
    print(f"\nours: H={ours['H_m']:.1f} m, Rb*={ours['R_b_star_mK_W']:.4f}  |  "
          f"GHEtool: H={theirs['H_m']:.1f} m, Rb*={theirs['R_b_star_mK_W']:.4f}  |  diff={pct_diff:+.1f}%")

    # Effective borehole resistance uses the same pygfunction pipe/fluid code path either
    # way, so it should agree closely regardless of the sizing methodology difference.
    assert ours["R_b_star_mK_W"] == pytest.approx(theirs["R_b_star_mK_W"], rel=0.05)

    # Sized depth: current known gap is ~7%, attributable to the simplified load
    # synthesis, not a g-function/simulation discrepancy. Tightened as that improves.
    assert abs(pct_diff) < 15.0


def test_double_u_tube_parallel_can_be_cross_checked_against_ghetool():
    # Regression test: the GHEtool cross-check used to reject every multiple_u_tube config
    # with NotImplementedError, including this one -- the default "Double U-tube, parallel"
    # a user can select in the GUI's Sizing tab. GHEtool.MultipleUTube supports this exact
    # geometry (config="diagonal" reproduces MainWindow.BuildPipeConfig()'s cross layout).
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    kwargs = {**COMMON_KWARGS, "pipe_config": DOUBLE_U_PARALLEL, "m_flow_borehole": 0.60}

    ours = sizing.size_field(
        FIELD, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=10,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=16.0, H_min=20.0, H_max=400.0, tol_m=1.0,
        **kwargs,
    )
    theirs = sizing_ghetool_client.size_field_isolated(
        field_template=FIELD, baseload_heating_kWh=BASELOAD_HEATING_KWH, baseload_cooling_kWh=BASELOAD_COOLING_KWH,
        peak_heating_kW=PEAK_HEATING_KW, peak_cooling_kW=PEAK_COOLING_KW,
        simulation_period_years=10, T_f_min_limit_C=-2.0, T_f_max_limit_C=16.0, method="L3",
        **kwargs,
    )

    assert ours["R_b_star_mK_W"] == pytest.approx(theirs["R_b_star_mK_W"], rel=0.05)
    assert abs(100 * (ours["H_m"] - theirs["H_m"]) / theirs["H_m"]) < 15.0


def test_double_u_tube_series_is_refused_rather_than_silently_mismodeled():
    # GHEtool's MultipleUTube pipe model always wires pygfunction's MultipleUTube with the
    # library default config="parallel" -- it has no way to represent our "series" hydraulic
    # wiring. Sizing it anyway would silently cross-check against a different (parallel)
    # design, so this must fail loudly instead of returning a misleading number.
    with pytest.raises(RuntimeError, match="parallel"):
        sizing_ghetool_client.size_field_isolated(
            field_template=FIELD, baseload_heating_kWh=BASELOAD_HEATING_KWH, baseload_cooling_kWh=BASELOAD_COOLING_KWH,
            peak_heating_kW=PEAK_HEATING_KW, peak_cooling_kW=PEAK_COOLING_KW,
            simulation_period_years=10, T_f_min_limit_C=-2.0, T_f_max_limit_C=16.0, method="L3",
            **{**COMMON_KWARGS, "pipe_config": DOUBLE_U_SERIES, "m_flow_borehole": 0.60},
        )
