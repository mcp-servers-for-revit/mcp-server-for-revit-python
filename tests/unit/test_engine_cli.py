"""geothermal.engine_cli is the JSON-over-stdio contract the WPF shell calls
as a subprocess. Exercised as a real subprocess (not imported directly) so
this test proves the actual `python -m geothermal.engine_cli` invocation the
C# side will use, not just the underlying functions.
"""
import json
import subprocess
import sys

import pytest


def _call(command, args=None):
    req = json.dumps({"command": command, "args": args or {}})
    proc = subprocess.run(
        [sys.executable, "-m", "geothermal.engine_cli"], input=req, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_list_commands_includes_every_module():
    names = _call("list_commands")["result"]
    for expected in ("build_rectangle_field", "evaluate_gfunction", "evaluate_mift_gfunction",
                      "run_hourly_simulation", "size_field", "size_field_ghetool",
                      "network_gfunction", "field_summary", "list_pipes", "list_pipe_manufacturers",
                      "list_grouts", "list_grout_manufacturers",
                      "apply_cooling_tower", "run_hourly_simulation_with_deadband_tower",
                      "size_field_with_deadband_tower",
                      "minimum_tower_capacity", "minimum_deadband_tower_capacity", "field_layout_from_area",
                      "synthesize_peak_only_load", "suggest_flow_rate"):
        assert expected in names


def test_suggest_flow_rate_command_governed_by_capacity_for_a_large_load():
    pipe = {"type": "single_u_tube", "pos": [[-0.03, 0.0], [0.03, 0.0]], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
    result = _call("suggest_flow_rate", {
        "capacity_W": 20000.0, "config": pipe,
        "fluid_str": "MPG", "fluid_percent": 25.0, "fluid_temperature_C": 5.0})["result"]
    assert result["governing"] == "capacity"
    assert result["flow_rate_kg_s"] > 0


def test_apply_cooling_tower_command_never_exceeds_rated_capacity():
    result = _call("apply_cooling_tower", {
        "hourly_load_W": [2000.0, -1000.0, -8000.0], "tower_capacity_kW": 5.0})["result"]
    assert result["ground_load_W"] == [2000.0, 0.0, -3000.0]
    assert result["tower_hours"] == 2
    assert result["tower_peak_kW"] == pytest.approx(5.0)


def test_field_layout_from_area_command_returns_a_square_grid():
    result = _call("field_layout_from_area", {
        "area_ambient_m2": 1000.0, "area_under_building_m2": 296.0, "spacing_m": 6.0})["result"]
    assert result["N_1"] == 7 and result["N_2"] == 7


def test_pipe_catalog_commands_return_sourced_data_for_every_manufacturer():
    manufacturers = _call("list_pipe_manufacturers")["result"]
    assert manufacturers == ["Gerodur", "Muovitech", "Pipelife Bulgaria", "REHAU"]

    rehau_pipes = _call("list_pipes", {"manufacturer": "REHAU"})["result"]
    assert any(p["key"] == "rehau_raugeo_32x2.9" and p["r_in_m"] == pytest.approx(0.0131) for p in rehau_pipes)

    all_pipes = _call("list_pipes")["result"]
    assert any(p["key"] == "gerodur_gerotherm_duplex_32x3.0" for p in all_pipes)
    assert any(p["key"] == "muovitech_pe100_32x3.0" for p in all_pipes)
    assert any(p["key"] == "pipelife_pe100_32x3.0" for p in all_pipes)


def test_grout_catalog_commands_return_sourced_data_for_every_manufacturer():
    manufacturers = _call("list_grout_manufacturers")["result"]
    assert manufacturers == ["Fischer Spezialbaustoffe", "Muovitech", "REHAU"]

    rehau_grouts = _call("list_grouts", {"manufacturer": "REHAU"})["result"]
    assert any(g["key"] == "rehau_raugeo_fill_rojo" and g["k_g_W_mK"] == pytest.approx(2.0) for g in rehau_grouts)

    all_grouts = _call("list_grouts")["result"]
    assert any(g["key"] == "muovitech_muoviteco_2.0" for g in all_grouts)
    assert any(g["key"] == "fischer_geosolid_240hs" for g in all_grouts)
    assert any(g["key"] == "fischer_geosolid_235" for g in all_grouts)


def test_unknown_command_returns_error_not_crash():
    result = _call("not_a_real_command")
    assert "error" in result
    assert "list_commands" in result["error"]


def test_invalid_json_on_stdin_returns_error():
    proc = subprocess.run(
        [sys.executable, "-m", "geothermal.engine_cli"], input="not json", capture_output=True, text=True)
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert "error" in result


def test_handler_exception_is_caught_and_reported():
    field = _call("build_rectangle_field", {
        "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "H": 150.0, "D": 2.0, "r_b": 0.075})["result"]
    time = _call("time_grid", {"t_min_s": 3600.0, "t_max_s": 25 * 8760 * 3600.0, "num": 50})["result"]
    result = _call("evaluate_gfunction", {
        "field": field, "alpha": 1e-6, "time": time, "boundary_condition": "NOT_A_REAL_BC"})
    assert "error" in result
    assert "ValueError" in result["error"]


def test_report_worked_example_1_end_to_end_through_the_cli():
    """Same case as tests/unit/test_geothermal_report_examples.py::test_step1,
    run through the actual subprocess contract the WPF shell will use."""
    field = _call("build_rectangle_field", {
        "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "H": 150.0, "D": 2.0, "r_b": 0.075})["result"]
    time = _call("time_grid", {"t_min_s": 3600.0, "t_max_s": 25 * 8760 * 3600.0, "num": 50})["result"]
    result = _call("evaluate_gfunction", {
        "field": field, "alpha": 1e-6, "time": time, "boundary_condition": "UBWT"})["result"]
    assert result["g"][-1] == pytest.approx(17.903, abs=1e-3)

    summary = _call("field_summary", {"field": field})["result"]
    assert summary["n_boreholes"] == 12
    assert summary["total_length_m"] == pytest.approx(1800.0)
