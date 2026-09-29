"""Validation of geothermal/simulation.py's borehole-wall temperature response
against a closed-form analytical solution that is INDEPENDENT of pygfunction
-- the audit's "no cross-check against an outside reference" gap (Phase 1
category 5, Validation & design risk).

Every other validation test in this repo (test_geothermal_report_examples.py)
pins numbers pygfunction's OWN documentation already printed -- a real,
useful regression check, but not independent of the library being tested. This
file instead derives the classical infinite line source (ILS) solution
(Ingersoll & Plass, 1948; also Carslaw & Jaeger 1959 Sec. 10.4) from its own
textbook formula, computed here with scipy's exponential-integral function,
and compares it against this project's own simulation.run_hourly_simulation
output for a single borehole under constant load.

Derivation (so the formula below is checked, not just trusted): the ILS
temperature rise at radius r after constant line heat rate q' (W/m) is
    DeltaT(r, t) = q' / (4 * pi * k_s) * E1(r^2 / (4 * alpha * t))
(E1 = the exponential integral, scipy.special.exp1). This codebase's g-function
convention (matching pygfunction/Eskilson) is T_b(t) - T_g = -q' * g(t) /
(2 * pi * k_s) for a heat-EXTRACTION-positive q' (see simulation.py's own
load_agg.initialize(g / (2 * pi * k_s))). Equating the two at r = r_b gives
    g_ILS(t) = 0.5 * E1(r_b^2 / (4 * alpha * t))
-- the standard "half-E1" ILS g-function quoted throughout the ground-source
literature (e.g. Spitler & Bernier 2016, ch. 3).

Two things are checked, in opposite directions, so this is a real test of the
finite line source's finite-length physics, not a coincidence:
1. Close agreement (a few percent) at moderate times (Fourier number Fo =
   alpha*t/r_b^2 well above 5, but t still small relative to the borehole's
   own steady-state time ts = H^2/(9*alpha)) -- the regime where a borehole
   genuinely behaves like an infinite line, radially, before its finite
   length starts to matter axially.
2. Real, growing divergence at long times (t approaching and exceeding a
   sizeable fraction of ts) -- the ILS has no steady state (grows as ln(t)
   forever) while the FLS's end effects make it level off, so the two MUST
   disagree there. Both magnitudes below were computed directly (see the
   standalone scripts run during development) before being written as
   assertions, not guessed.
"""
import numpy as np
import pytest
from scipy.special import exp1

from geothermal import fields, simulation

ALPHA = 1.0e-6  # m2/s
K_S = 2.0       # W/m.K
K_G = 1.5       # W/m.K
T_G = 12.0      # degC
R_B = 0.075     # m
H = 150.0       # m
Q_CONST_W = 4000.0

SINGLE_UTUBE = {
    "type": "single_u_tube",
    "pos": [(-0.03, 0.0), (0.03, 0.0)],
    "r_in": 0.0131,
    "r_out": 0.0160,
    "k_p": 0.4,
}


def _g_ils(t_s: float) -> float:
    return 0.5 * exp1(R_B**2 / (4 * ALPHA * t_s))


def _run_single_borehole(n_hours: int) -> np.ndarray:
    field = fields.rectangle_field(N_1=1, N_2=1, B_1=6.0, B_2=6.0, H=H, D=2.0, r_b=R_B)
    result = simulation.run_hourly_simulation(
        field, ALPHA, K_S, K_G, T_G, SINGLE_UTUBE, m_flow_borehole=0.30,
        fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=20.0,
        hourly_load_W=[Q_CONST_W] * n_hours, algorithm="ClaessonJaved",
    )
    return np.asarray(result["T_b_C"])


def _g_sim(T_b: np.ndarray, hour: int) -> float:
    q_prime = Q_CONST_W / H
    return -(T_b[hour - 1] - T_G) * 2 * np.pi * K_S / q_prime


def test_matches_infinite_line_source_within_a_few_percent_at_moderate_times():
    # 1 day, 1 week, 1 month, 6 months, 1 year -- Fo > 5 (radial ILS regime valid)
    # and t/ts < 0.02 (finite-length end effects still small, ts ~= 79 years here).
    T_b = _run_single_borehole(8760)
    for hour in (24, 24 * 7, 24 * 30, 24 * 180, 8760):
        t_s = hour * 3600.0
        g_ils = _g_ils(t_s)
        g_sim = _g_sim(T_b, hour)
        rel_err = abs(g_sim - g_ils) / g_ils
        assert rel_err < 0.025, f"hour={hour}: g_sim={g_sim:.4f} vs g_ils={g_ils:.4f} ({100*rel_err:.2f}% off)"


def test_genuinely_diverges_from_infinite_line_source_at_long_times():
    # By 20 years (t/ts ~= 0.25), the borehole's finite length has slowed its
    # own warming well below what an infinite line would predict -- the ILS
    # keeps growing as ln(t) forever, a real borehole does not. A validation
    # that agreed here too would mean the "independent" check was accidentally
    # measuring something else (e.g. reusing the FLS formula itself).
    T_b = _run_single_borehole(20 * 8760)
    g_ils = _g_ils(20 * 8760 * 3600.0)
    g_sim = _g_sim(T_b, 20 * 8760)
    rel_err = abs(g_sim - g_ils) / g_ils
    assert rel_err > 0.03
    assert g_sim < g_ils  # FLS's finite length means LESS warming than an infinite line predicts


def test_short_time_ils_regime_still_beats_a_naive_full_range_tolerance():
    # Guards against a vacuous test: moderate-time agreement (<2%) really is
    # tighter than the long-time divergence (>3%), not the same number twice.
    T_b_short = _run_single_borehole(8760)
    T_b_long = _run_single_borehole(20 * 8760)
    short_err = abs(_g_sim(T_b_short, 8760) - _g_ils(8760 * 3600.0)) / _g_ils(8760 * 3600.0)
    long_err = abs(_g_sim(T_b_long, 20 * 8760) - _g_ils(20 * 8760 * 3600.0)) / _g_ils(20 * 8760 * 3600.0)
    assert short_err < long_err
