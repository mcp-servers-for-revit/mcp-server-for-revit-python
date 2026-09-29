"""Tests for geothermal.ground.weighted_average_ground_properties -- the
"layered ground" pick from the 2026-09-28 accuracy audit (Phase 1 item 1).
Validation strategy: check the length-weighted-average arithmetic against a
hand-computed expectation (not just "a number came out"), then the edge
cases (partial layer overlap at the borehole's own top/bottom, gaps/overlaps
in the log, mixed T_g_C presence) that a real geological log will actually
produce.
"""
import pytest

from geothermal import ground

TWO_LAYERS = [
    {"top_m": 0.0, "bottom_m": 20.0, "k_s": 1.5, "rho_cp_J_m3K": 2.2e6, "T_g_C": 10.0},
    {"top_m": 20.0, "bottom_m": 200.0, "k_s": 3.0, "rho_cp_J_m3K": 2.5e6, "T_g_C": 13.0},
]


def test_matches_hand_computed_length_weighted_average():
    # Borehole spans [D, D+H] = [2, 152]: 18 m in layer 1, 132 m in layer 2.
    result = ground.weighted_average_ground_properties(TWO_LAYERS, D=2.0, H=150.0)
    k_expected = (1.5 * 18.0 + 3.0 * 132.0) / 150.0
    rho_cp_expected = (2.2e6 * 18.0 + 2.5e6 * 132.0) / 150.0
    T_g_expected = (10.0 * 18.0 + 13.0 * 132.0) / 150.0
    assert result["k_s_eff"] == pytest.approx(k_expected)
    assert result["rho_cp_eff_J_m3K"] == pytest.approx(rho_cp_expected)
    assert result["alpha_eff_m2_s"] == pytest.approx(k_expected / rho_cp_expected)
    assert result["T_g_eff_C"] == pytest.approx(T_g_expected)
    assert result["layer_overlaps_m"] == pytest.approx([18.0, 132.0])


def test_a_single_layer_spanning_the_whole_borehole_reproduces_its_own_properties():
    layers = [{"top_m": 0.0, "bottom_m": 300.0, "k_s": 2.4, "rho_cp_J_m3K": 2.3e6}]
    result = ground.weighted_average_ground_properties(layers, D=2.0, H=150.0)
    assert result["k_s_eff"] == pytest.approx(2.4)
    assert result["rho_cp_eff_J_m3K"] == pytest.approx(2.3e6)
    assert "T_g_eff_C" not in result  # no layer supplied one


def test_layer_boundary_exactly_at_borehole_top_and_bottom_uses_full_thickness():
    # Layer 1 ends exactly where the borehole starts (D=20): contributes zero.
    layers = [
        {"top_m": 0.0, "bottom_m": 20.0, "k_s": 1.0, "rho_cp_J_m3K": 2.0e6},
        {"top_m": 20.0, "bottom_m": 120.0, "k_s": 2.5, "rho_cp_J_m3K": 2.4e6},
    ]
    result = ground.weighted_average_ground_properties(layers, D=20.0, H=100.0)
    assert result["layer_overlaps_m"] == pytest.approx([0.0, 100.0])
    assert result["k_s_eff"] == pytest.approx(2.5)


def test_rejects_a_log_that_does_not_reach_the_borehole_bottom():
    layers = [{"top_m": 0.0, "bottom_m": 100.0, "k_s": 2.0, "rho_cp_J_m3K": 2.3e6}]
    with pytest.raises(ValueError, match="does not fully cover"):
        ground.weighted_average_ground_properties(layers, D=2.0, H=150.0)


def test_rejects_a_gap_between_layers():
    layers = [
        {"top_m": 0.0, "bottom_m": 20.0, "k_s": 1.5, "rho_cp_J_m3K": 2.2e6},
        {"top_m": 25.0, "bottom_m": 200.0, "k_s": 3.0, "rho_cp_J_m3K": 2.5e6},  # 5 m gap
    ]
    with pytest.raises(ValueError, match="gap in layer log"):
        ground.weighted_average_ground_properties(layers, D=2.0, H=150.0)


def test_rejects_overlapping_layers():
    layers = [
        {"top_m": 0.0, "bottom_m": 25.0, "k_s": 1.5, "rho_cp_J_m3K": 2.2e6},
        {"top_m": 20.0, "bottom_m": 200.0, "k_s": 3.0, "rho_cp_J_m3K": 2.5e6},  # overlaps by 5 m
    ]
    with pytest.raises(ValueError, match="layers overlap"):
        ground.weighted_average_ground_properties(layers, D=2.0, H=150.0)


def test_rejects_mixed_presence_of_t_g_c():
    layers = [
        {"top_m": 0.0, "bottom_m": 20.0, "k_s": 1.5, "rho_cp_J_m3K": 2.2e6, "T_g_C": 10.0},
        {"top_m": 20.0, "bottom_m": 200.0, "k_s": 3.0, "rho_cp_J_m3K": 2.5e6},  # missing T_g_C
    ]
    with pytest.raises(ValueError, match="either every layer must supply T_g_C"):
        ground.weighted_average_ground_properties(layers, D=2.0, H=150.0)


def test_rejects_non_positive_k_s_or_rho_cp():
    with pytest.raises(ValueError, match="k_s must be > 0"):
        ground.weighted_average_ground_properties(
            [{"top_m": 0.0, "bottom_m": 200.0, "k_s": 0.0, "rho_cp_J_m3K": 2.2e6}], D=2.0, H=150.0,
        )
    with pytest.raises(ValueError, match="rho_cp_J_m3K must be > 0"):
        ground.weighted_average_ground_properties(
            [{"top_m": 0.0, "bottom_m": 200.0, "k_s": 2.0, "rho_cp_J_m3K": -1.0}], D=2.0, H=150.0,
        )


def test_effective_properties_feed_directly_into_an_ordinary_simulation_run():
    # The whole point: no changes needed anywhere else -- k_s_eff/alpha_eff_m2_s
    # plug straight into simulation.run_hourly_simulation like any hand-picked value.
    from geothermal import fields, simulation

    result = ground.weighted_average_ground_properties(TWO_LAYERS, D=2.0, H=150.0)
    field = fields.rectangle_field(N_1=2, N_2=2, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    pipe = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
    sim = simulation.run_hourly_simulation(
        field, result["alpha_eff_m2_s"], result["k_s_eff"], 1.5, result["T_g_eff_C"],
        pipe, 0.30, "MPG", 25.0, 20.0, hourly_load_W=[4000.0] * 8760,
    )
    assert sim["T_f_min_C"] < sim["T_f_max_C"]
