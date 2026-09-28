"""Regression tests pinned to the 3 worked examples in the pygfunction
getting-started report (pygfunction 2.3.1), so the geothermal/ engine is
validated against numbers an independent source already ran and printed.
"""
import numpy as np
import pytest

from geothermal import fields, gfunctions, simulation

ALPHA = 1.0e-6  # m2/s
K_S = 2.0       # W/m.K
K_G = 1.5       # W/m.K
T_G = 12.0      # degC

SINGLE_UTUBE = {
    "type": "single_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0)],
    "r_in": 0.0131,
    "r_out": 0.0160,
    "k_p": 0.4,
}


def _report_field():
    return fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)


def test_step1_ubwt_gfunction_at_25_years():
    field = _report_field()
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 50)
    result = gfunctions.evaluate(field, ALPHA, time, boundary_condition="UBWT")
    assert result["g"][-1] == pytest.approx(17.903, abs=1e-3)


def test_step2_ten_year_hourly_simulation():
    field = _report_field()
    years = 10
    hours = np.arange(1, years * 8760 + 1)
    Q = 25e3 * np.cos(2 * np.pi * hours / 8760) + 8e3
    Q += 6e3 * np.cos(2 * np.pi * hours / 24) * (Q > 0)

    result = simulation.run_hourly_simulation(
        field, ALPHA, K_S, K_G, T_G, SINGLE_UTUBE, m_flow_borehole=0.30,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=20.0,
        hourly_load_W=Q.tolist(), algorithm="ClaessonJaved",
    )

    assert result["R_b_star_mK_W"] == pytest.approx(0.150, abs=1e-3)
    assert result["T_f_min_C"] == pytest.approx(-0.1, abs=0.1)
    assert result["T_f_max_C"] == pytest.approx(15.3, abs=0.1)

    T_f = np.asarray(result["T_f_C"])
    assert T_f[:8760].min() == pytest.approx(2.7, abs=0.1)
    assert T_f[-8760:].min() == pytest.approx(-0.1, abs=0.1)


def test_step3_mift_gfunction_at_25_years():
    field = _report_field()
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 30)
    result = gfunctions.evaluate_mift(
        field, ALPHA, time, SINGLE_UTUBE, m_flow_network=12 * 0.30,
        k_s=K_S, k_g=K_G, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )
    assert result["g"][-1] == pytest.approx(18.241, abs=1e-3)


def test_step3_boundary_condition_ordering_uhtr_ubwt_mift():
    """Report: for the same field/time grid, UBWT=17.89, UHTR=19.36, MIFT=18.241 (between the two)."""
    field = _report_field()
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 30)
    g_ubwt = gfunctions.evaluate(field, ALPHA, time, boundary_condition="UBWT")["g"][-1]
    g_uhtr = gfunctions.evaluate(field, ALPHA, time, boundary_condition="UHTR")["g"][-1]
    g_mift = gfunctions.evaluate_mift(
        field, ALPHA, time, SINGLE_UTUBE, m_flow_network=12 * 0.30,
        k_s=K_S, k_g=K_G, fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=5.0,
    )["g"][-1]
    assert g_ubwt == pytest.approx(17.89, abs=0.05)
    assert g_uhtr == pytest.approx(19.36, abs=0.05)
    assert g_ubwt < g_mift < g_uhtr


def test_solvers_agree_on_ubwt_25_years():
    """Report: equivalent, similarities and detailed all gave 17.89 for UBWT on this field."""
    field = _report_field()
    time = gfunctions.time_grid(3600.0, 25 * 8760 * 3600.0, 30)
    g_equiv = gfunctions.evaluate(field, ALPHA, time, method="equivalent", boundary_condition="UBWT")["g"][-1]
    g_simil = gfunctions.evaluate(field, ALPHA, time, method="similarities", boundary_condition="UBWT")["g"][-1]
    g_detail = gfunctions.evaluate(field, ALPHA, time, method="detailed", boundary_condition="UBWT")["g"][-1]
    assert g_equiv == pytest.approx(17.89, abs=0.05)
    assert g_simil == pytest.approx(17.89, abs=0.05)
    assert g_detail == pytest.approx(17.89, abs=0.05)
