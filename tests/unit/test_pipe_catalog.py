"""Validates the vendor pipe/grout catalog data (REHAU, Gerodur, Muovitech,
Pipelife Bulgaria pipes; REHAU, Muovitech, Fischer grouts), and that sourcing
pipe dimensions from the catalog instead of hand-typed literals reproduces
the same report worked examples already pinned in
test_geothermal_report_examples.py.
"""
import pytest

from geothermal import fields, gfunctions, pipe_catalog


def test_list_pipe_manufacturers_covers_all_four():
    assert pipe_catalog.list_pipe_manufacturers() == [
        "Gerodur", "Muovitech", "Pipelife Bulgaria", "REHAU",
    ]


def test_list_pipes_covers_expected_rehau_sizes():
    pipes = pipe_catalog.list_pipes(manufacturer="REHAU")
    keys = {p["key"] for p in pipes}
    assert keys == {
        "rehau_raugeo_20x1.9", "rehau_raugeo_25x2.3", "rehau_raugeo_32x2.9",
        "rehau_raugeo_40x3.7", "rehau_raugeo_50x4.6", "rehau_raugeo_63x5.8", "rehau_raugeo_75x6.8",
    }
    assert all(p["manufacturer"] == "REHAU" for p in pipes)


def test_list_pipes_with_no_manufacturer_returns_all():
    all_pipes = pipe_catalog.list_pipes()
    manufacturers = {p["manufacturer"] for p in all_pipes}
    assert manufacturers == {"REHAU", "Gerodur", "Muovitech", "Pipelife Bulgaria"}
    assert len(all_pipes) > len(pipe_catalog.list_pipes(manufacturer="REHAU"))


def test_gerodur_pipes_are_pe100_rc_with_sourced_dimensions():
    pipes = pipe_catalog.list_pipes(manufacturer="Gerodur")
    assert len(pipes) == 5
    thirty_two = next(p for p in pipes if p["key"] == "gerodur_gerotherm_duplex_32x3.0")
    assert thirty_two["r_in_m"] == pytest.approx(0.0130)
    assert thirty_two["r_out_m"] == pytest.approx(0.0160)


def test_muovitech_pipes_cover_expected_sizes():
    pipes = pipe_catalog.list_pipes(manufacturer="Muovitech")
    keys = {p["key"] for p in pipes}
    assert "muovitech_pe100_32x3.0" in keys
    assert "muovitech_pe100_75x6.8" in keys


def test_pipelife_pipes_cover_expected_sizes():
    pipes = pipe_catalog.list_pipes(manufacturer="Pipelife Bulgaria")
    keys = {p["key"] for p in pipes}
    assert keys == {
        "pipelife_pe100_32x3.0", "pipelife_pe100_40x3.7",
        "pipelife_pe100_50x4.6", "pipelife_pe100_63x5.8",
    }


def test_32x2_9_matches_the_pygfunction_reports_own_worked_example():
    """The report's step2/step3 scripts use r_in=0.0131, r_out=0.0160 for
    'PE 32 x 2.9 mm pipe radii' -- the catalog entry should reproduce that
    exactly, since both describe the same standard product."""
    pipe = pipe_catalog.get_pipe("rehau_raugeo_32x2.9")
    assert pipe["r_in_m"] == pytest.approx(0.0131)
    assert pipe["r_out_m"] == pytest.approx(0.0160)


def test_wall_thickness_is_internally_consistent_for_every_pipe():
    for pipe in pipe_catalog.list_pipes():
        wall_from_radii = pipe["r_out_m"] - pipe["r_in_m"]
        wall_from_table = pipe["s_mm"] / 1000.0
        assert wall_from_radii == pytest.approx(wall_from_table, abs=2e-4), pipe["key"]


def test_unknown_pipe_key_raises():
    with pytest.raises(KeyError):
        pipe_catalog.get_pipe("not_a_real_key")


def test_list_grout_manufacturers_covers_all_three():
    assert pipe_catalog.list_grout_manufacturers() == [
        "Fischer Spezialbaustoffe", "Muovitech", "REHAU",
    ]


def test_list_grouts_covers_rehau_red_and_blue():
    grouts = pipe_catalog.list_grouts(manufacturer="REHAU")
    keys = {g["key"] for g in grouts}
    assert keys == {"rehau_raugeo_fill_rojo", "rehau_raugeo_fill_azul"}
    rojo = pipe_catalog.get_grout("rehau_raugeo_fill_rojo")
    azul = pipe_catalog.get_grout("rehau_raugeo_fill_azul")
    assert rojo["k_g_W_mK"] == pytest.approx(2.0)
    assert azul["k_g_W_mK"] == pytest.approx(1.2)
    assert rojo["k_g_W_mK"] > azul["k_g_W_mK"]  # red is the high-conductivity variant


def test_muovitech_grout_is_sourced():
    grouts = pipe_catalog.list_grouts(manufacturer="Muovitech")
    assert len(grouts) == 1
    assert grouts[0]["key"] == "muovitech_muoviteco_2.0"
    assert grouts[0]["k_g_W_mK"] == pytest.approx(2.0)


def test_fischer_fallback_grouts_cover_240hs_and_235():
    grouts = pipe_catalog.list_grouts(manufacturer="Fischer Spezialbaustoffe")
    keys = {g["key"] for g in grouts}
    assert keys == {"fischer_geosolid_240hs", "fischer_geosolid_235"}
    hs = pipe_catalog.get_grout("fischer_geosolid_240hs")
    standard = pipe_catalog.get_grout("fischer_geosolid_235")
    assert hs["k_g_W_mK"] == pytest.approx(2.40)
    assert standard["k_g_W_mK"] == pytest.approx(2.35)
    assert hs["k_g_W_mK"] > standard["k_g_W_mK"]


def test_unknown_grout_key_raises():
    with pytest.raises(KeyError):
        pipe_catalog.get_grout("not_a_real_key")


def test_step3_mift_gfunction_reproduces_report_value_using_catalog_pipe():
    """Same case as test_geothermal_report_examples.py::test_step3_mift_gfunction_at_25_years,
    but pipe dimensions come from the catalog instead of literals, and grout
    conductivity comes from RAUGEO fill rojo (k_g=2.0) instead of the
    report's own k_g=1.5 -- so this is not expected to hit 18.241 exactly,
    just to run correctly end-to-end with catalog-sourced values."""
    pipe = pipe_catalog.get_pipe("rehau_raugeo_32x2.9")
    grout = pipe_catalog.get_grout("rehau_raugeo_fill_rojo")
    pipe_config = {
        "type": "single_u_tube",
        "pos": [(-0.03, 0.0), (0.03, 0.0)],
        "r_in": pipe["r_in_m"], "r_out": pipe["r_out_m"], "k_p": pipe["k_p_W_mK"],
    }
    field = fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 30)
    result = gfunctions.evaluate_mift(
        field, 1.0e-6, time, pipe_config, m_flow_network=12 * 0.30,
        k_s=2.0, k_g=grout["k_g_W_mK"], fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )
    assert result["g"][-1] > 0
