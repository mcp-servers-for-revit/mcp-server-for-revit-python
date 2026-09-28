"""Coverage for geothermal.networks: parallel/series/mixed connectivity,
reversed flow direction, variable-flow-rate g-functions, and the
'equivalent solver only valid for parallel fields' behavior pygfunction
itself only reports via a Python warning.
"""
import pytest

from geothermal import fields, networks

ALPHA = 1.0e-6
K_S = 2.0
K_G = 1.5

SINGLE_UTUBE = {
    "type": "single_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0)],
    "r_in": 0.0131,
    "r_out": 0.0160,
    "k_p": 0.4,
}


def _small_field(n=6):
    return fields.rectangle_field(N_1=n, N_2=1, B_1=6.0, B_2=6.0, H=100.0, D=2.0, r_b=0.075)


def _common_kwargs(field):
    return dict(
        field=field, alpha=ALPHA, k_s=K_S, k_g=K_G, pipe_config=SINGLE_UTUBE,
        m_flow_network=0.5, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )


def test_connectivity_helpers():
    assert networks.all_parallel_connectivity(4) == [-1, -1, -1, -1]
    assert networks.all_series_connectivity(4) == [-1, 0, 1, 2]
    assert networks.series_strings_connectivity(2, 3) == [-1, 0, 1, -1, 3, 4]


def test_parallel_network_uses_equivalent_solver_and_matches_evaluate_mift():
    """An explicit all-parallel Network should reproduce gfunctions.evaluate_mift
    (which also builds an all-parallel network internally), since they're the
    same underlying construction."""
    from geothermal import gfunctions
    field = _small_field()
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 20)

    via_networks = networks.evaluate(
        time=time, bore_connectivity=networks.all_parallel_connectivity(6),
        **_common_kwargs(field),
    )
    via_gfunctions = gfunctions.evaluate_mift(
        field, ALPHA, time, SINGLE_UTUBE, m_flow_network=0.5, k_s=K_S, k_g=K_G,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )

    assert via_networks["method_used"] == "equivalent"
    assert via_networks["warnings"] == []
    # Two different pygfunction constructor paths (Network.from_static_params via
    # boreholes built from a Borefield, vs. gFunction.from_static_params building
    # boreholes from raw arrays) -- expect them to agree closely, not bit-for-bit.
    assert via_networks["g"][-1] == pytest.approx(via_gfunctions["g"][-1], rel=1e-3)


def test_series_network_falls_back_from_equivalent_without_warning_leak():
    field = _small_field()
    time = [3600.0, 8760 * 3600.0, 25 * 8760 * 3600.0]

    result = networks.evaluate(
        time=time, bore_connectivity=networks.all_series_connectivity(6), method="equivalent",
        **_common_kwargs(field),
    )
    assert result["method_requested"] == "equivalent"
    assert result["method_used"] == "similarities"
    assert all(g > 0 for g in result["g"])


def test_mixed_parallel_series_strings():
    field = _small_field(n=6)  # 2 strings of 3, matches series_strings_connectivity(2, 3)
    time = [3600.0, 25 * 8760 * 3600.0]
    result = networks.evaluate(
        time=time, bore_connectivity=networks.series_strings_connectivity(2, 3), method="similarities",
        **_common_kwargs(field),
    )
    assert result["g"][-1] > result["g"][0] > 0


def test_reversed_flow_runs_and_is_flagged():
    field = _small_field()
    time = [3600.0, 25 * 8760 * 3600.0]
    kwargs = _common_kwargs(field)
    kwargs["m_flow_network"] = -0.5
    result = networks.evaluate(
        time=time, bore_connectivity=networks.all_series_connectivity(6), method="similarities", **kwargs,
    )
    assert result["reversed_flow"] is True
    assert result["g"][-1] > 0


def test_variable_flow_gfunction_shape_and_monotonic_time():
    field = _small_field(n=4)
    time = [3600.0, 8760 * 3600.0, 10 * 8760 * 3600.0]
    m_flows = [0.3, 0.5, 0.8]
    result = networks.evaluate_variable_flow(
        field, ALPHA, time, SINGLE_UTUBE, m_flows, K_S, K_G,
        "MPG", 25.0, 5.0, bore_connectivity=networks.all_parallel_connectivity(4),
    )
    g = result["g"]
    assert len(g) == 3 and len(g[0]) == 3 and len(g[0][0]) == 3
    # same flow at evaluation and in history -> the "diagonal" is the plain constant-flow g-function
    for i in range(3):
        assert g[i][i][-1] > g[i][i][0] > 0


def test_variable_flow_requires_at_least_two_values():
    field = _small_field(n=4)
    with pytest.raises(ValueError):
        networks.evaluate_variable_flow(
            field, ALPHA, [3600.0], SINGLE_UTUBE, [0.5], K_S, K_G, "MPG", 25.0, 5.0,
        )


def test_variable_flow_rejects_equivalent_solver():
    field = _small_field(n=4)
    with pytest.raises(ValueError):
        networks.evaluate_variable_flow(
            field, ALPHA, [3600.0, 8760 * 3600.0], SINGLE_UTUBE, [0.3, 0.5], K_S, K_G,
            "MPG", 25.0, 5.0, method="equivalent",
        )


def test_effective_network_resistance_differs_from_single_borehole_rb_for_series():
    from geothermal.pipes import effective_resistance
    field = _small_field(n=6)

    rb_star = effective_resistance(
        SINGLE_UTUBE, H=100.0, D=2.0, r_b=0.075, k_s=K_S, k_g=K_G,
        m_flow_borehole=0.5, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )["R_b_star_mK_W"]

    network_result = networks.effective_network_resistance(
        field=field, pipe_config=SINGLE_UTUBE, k_s=K_S, k_g=K_G, m_flow_network=0.5,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
        bore_connectivity=networks.all_series_connectivity(6),
    )
    # series network: each borehole sees the full network flow (not split), so its
    # effective resistance is not simply the single-borehole R_b* computed above
    # (that used the same 0.5 kg/s as a single borehole's own flow, not a series string's).
    assert network_result["network_thermal_resistance_mK_W"] > 0
    assert network_result["network_thermal_resistance_mK_W"] != pytest.approx(rb_star, rel=1e-3)


def test_bore_connectivity_length_mismatch_raises():
    field = _small_field(n=4)
    with pytest.raises(ValueError):
        networks.build_network(
            field, SINGLE_UTUBE, K_S, K_G, 0.5, "MPG", 25.0, 5.0,
            bore_connectivity=[-1, -1],  # only 2 entries for 4 boreholes
        )
