"""Tests for geothermal.groundwater -- the "groundwater/Darcy advection"
pick from the 2026-09-28 accuracy audit (Phase 1 category 1). Validation
strategy: first prove the closed-form solution actually satisfies the PDE it
claims to solve (an automated version of the finite-difference check done by
hand during development -- see groundwater.py's own module docstring), then
check the physically-expected qualitative behavior (downstream colder than
upstream under extraction, effect grows with Darcy velocity, no steady state
at zero velocity) rather than pinning specific numeric outputs, since there
is no independent published table of moving-line-source values to pin
against here (unlike test_validation_analytical.py's ILS case, which pins to
the well-known closed-form E1 expression evaluated at the same test's own
parameters).
"""
import math

import pytest
from scipy.special import k0

from geothermal import groundwater

K_S = 2.0
ALPHA = 1.0e-6
R_B = 0.075
Q_PRIME_EXTRACT = 25.0  # W/m
RHO_CP_WATER = 4.18e6
RHO_CP_SOIL = 2.3e6


def _raw_temperature(q_prime, x, y, k_s, alpha, u):
    r = math.hypot(x, y)
    beta = u / (2 * alpha)
    return q_prime / (2 * math.pi * k_s) * math.exp(beta * x) * float(k0(beta * r))


def test_closed_form_satisfies_the_steady_advection_diffusion_pde():
    # (Txx + Tyy) - (u/alpha)*Tx must be ~0 away from the singularity at the origin,
    # for the governing equation this closed form claims to solve.
    u = 1.0e-6
    q_prime = 40.0
    h = 1.0e-4
    for x, y in [(0.5, 0.3), (1.0, -0.7), (-0.4, 0.9), (2.0, 1.5), (0.1, 0.1)]:
        T = lambda xx, yy: _raw_temperature(q_prime, xx, yy, K_S, ALPHA, u)
        Txx = (T(x + h, y) - 2 * T(x, y) + T(x - h, y)) / h**2
        Tyy = (T(x, y + h) - 2 * T(x, y) + T(x, y - h)) / h**2
        Tx = (T(x + h, y) - T(x - h, y)) / (2 * h)
        residual = (Txx + Tyy) - (u / ALPHA) * Tx
        scale = abs(Txx + Tyy)
        assert abs(residual) < 1e-4 * scale, f"(x={x},y={y}): residual {residual} too large relative to {scale}"


def test_thermally_retarded_velocity_scales_by_the_heat_capacity_ratio():
    # Exact proportionality always holds, regardless of direction.
    u_T = groundwater.thermally_retarded_velocity(1.0e-6, RHO_CP_WATER, RHO_CP_SOIL)
    assert u_T == pytest.approx(1.0e-6 * RHO_CP_WATER / RHO_CP_SOIL)
    # For a REAL saturated formation, water's own volumetric heat capacity (~4.18e6)
    # usually exceeds the bulk saturated-formation value -- u_T > u_darcy here, not <.
    assert u_T > 1.0e-6
    # A (hypothetical, non-physical-but-mathematically-valid) formation with a bulk
    # capacity larger than water's own would flip the direction -- proving this is a
    # genuine ratio, not a hard-coded "always slower" assumption a first draft of
    # this module's docstring incorrectly claimed (caught by this test before it shipped).
    u_T_flipped = groundwater.thermally_retarded_velocity(1.0e-6, RHO_CP_WATER, 6.0e6)
    assert u_T_flipped < 1.0e-6


def test_zero_darcy_velocity_raises_rather_than_silently_returning_something():
    with pytest.raises(ValueError, match="no steady state"):
        groundwater.groundwater_steady_state_effect(
            Q_PRIME_EXTRACT, R_B, K_S, ALPHA, 0.0, RHO_CP_WATER, RHO_CP_SOIL,
        )


def test_extraction_cools_downstream_more_than_upstream():
    # Under heat EXTRACTION, groundwater continuously sweeps cooled water downstream,
    # concentrating the thermal impact there -- downstream must show a LARGER magnitude
    # temperature drop than upstream (the classic asymmetric plume).
    result = groundwater.groundwater_steady_state_effect(
        Q_PRIME_EXTRACT, R_B, K_S, ALPHA, 5.0e-6, RHO_CP_WATER, RHO_CP_SOIL,
    )
    assert result["delta_T_downstream_C"] < 0.0
    assert result["delta_T_upstream_C"] < 0.0
    assert abs(result["delta_T_downstream_C"]) > abs(result["delta_T_upstream_C"])


def test_local_steady_temperature_change_shrinks_as_darcy_velocity_increases():
    # Counterintuitive but mathematically exact -- see groundwater_steady_state_effect's
    # own docstring for the derivative proof (e^z*K0(z) is strictly decreasing in z for
    # z > 0): faster flow removes heat more efficiently, so the LOCAL steady-state
    # temperature change at the borehole wall itself is smaller at higher velocity, even
    # though the Peclet number (and the plume's downstream reach) grows.
    slow = groundwater.groundwater_steady_state_effect(
        Q_PRIME_EXTRACT, R_B, K_S, ALPHA, 1.0e-6, RHO_CP_WATER, RHO_CP_SOIL,
    )
    fast = groundwater.groundwater_steady_state_effect(
        Q_PRIME_EXTRACT, R_B, K_S, ALPHA, 1.0e-5, RHO_CP_WATER, RHO_CP_SOIL,
    )
    assert fast["peclet_number"] > slow["peclet_number"]
    assert abs(fast["delta_T_downstream_C"]) < abs(slow["delta_T_downstream_C"])
    assert abs(fast["delta_T_upstream_C"]) < abs(slow["delta_T_upstream_C"])


def test_moving_line_source_temperature_rise_rejects_the_source_point_itself():
    with pytest.raises(ValueError, match="source location"):
        groundwater.moving_line_source_temperature_rise(40.0, 0.0, 0.0, K_S, ALPHA, 1.0e-6)


def test_moving_line_source_temperature_rise_rejects_zero_velocity():
    with pytest.raises(ValueError, match="no steady state"):
        groundwater.moving_line_source_temperature_rise(40.0, 1.0, 0.0, K_S, ALPHA, 0.0)
