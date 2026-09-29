"""Groundwater (Darcy) advection past a borehole -- the "groundwater/Darcy"
pick from the 2026-09-28 accuracy audit (Phase 1 category 1: Ground & site
characterization). pygfunction 2.3.1 has NO moving-medium heat source of any
kind (grep confirms zero hits for "darcy"/"advection"/"moving" anywhere in
the installed library) -- every g-function it computes assumes stagnant
ground. Building a full moving FINITE line source (the transient 3-D
solution that would plug directly into simulation.py's per-hour convolution,
alongside the existing stationary FLS) is a substantially larger undertaking
than this pass attempts: it is a genuinely different Green's function, not an
extra parameter on the existing one, and combining a transient moving-source
solution with pygfunction's existing load-aggregation scheme is an open
question this module does not attempt to answer.

**Deliberate scope**: this module implements the classical STEADY-STATE 2-D
moving line source instead (Diao, Li & Fang, 2004, "Improvement in modeling
of heat transfer in vertical ground heat exchangers", HVAC&R Research 10(4),
459-470; the same closed form is used as the groundwater building block in
later transient treatments, e.g. Molina-Giraldo et al. 2011, Int. J. Heat
Mass Transfer 54(25-26), and cited again in Piipponen et al. 2024, one of
this project's own stated literature-basis papers). It answers a narrower,
still genuinely useful question -- "once groundwater flow has had time to
reach its own steady state around this borehole, how much does that change
things, and is my site's flow even fast enough for it to matter" -- as a
SCREENING check, run alongside (not blended into) the existing transient,
no-advection hourly simulation. Estimating exactly *when* that steady state
is reached, and rigorously combining it with the transient conduction-only
response simulation.py already computes, is explicitly NOT attempted here --
doing that credibly needs the fuller moving-FLS treatment above, not a
guessed blending rule bolted onto two different solutions of two different
equations.

**Derivation and independent check** (so the formula is verified here, not
just cited): steady 2-D heat conduction with uniform advection velocity u in
the +x direction, away from the source, satisfies
    (d^2T/dx^2 + d^2T/dy^2) - (u/alpha) * dT/dx = 0.
The standard substitution T = exp(u*x / (2*alpha)) * phi(x, y) removes the
first-derivative term, leaving the modified Helmholtz equation for phi, whose
radially-symmetric fundamental solution is the modified Bessel function K0 --
giving
    DeltaT(x, y) = q' / (2*pi*k_s) * exp(u*x / (2*alpha)) * K0(u*r / (2*alpha)),
    r = sqrt(x^2 + y^2).
This was checked directly during development (not just trusted from the
papers above): substituting the closed form into the governing PDE and
evaluating both sides by finite differences at several (x, y) off the
singularity gave a residual near machine/truncation precision (relative
error ~1e-6, consistent with the finite-difference step itself, not a real
mismatch) -- see test_groundwater.py::
test_closed_form_satisfies_the_steady_advection_diffusion_pde for the same
check as an automated regression.

As u -> 0 this correctly has NO steady state (K0's argument -> 0, which
diverges logarithmically) -- physically correct: a stationary line source
never reaches steady state at all (it keeps warming as ln(t) forever, the
same fact test_validation_analytical.py's long-time-divergence test relies
on), and it is advection alone that lets the moving source reach one.
"""
from __future__ import annotations

import math
from typing import Any

from scipy.special import k0


def thermally_retarded_velocity(
    darcy_velocity_m_s: float, rho_cp_water_J_m3K: float, rho_cp_soil_J_m3K: float,
) -> float:
    """The effective advection velocity u_T that belongs in the volume-
    averaged heat transport equation this module solves (the PDE in the
    module docstring), given the Darcy (superficial) groundwater velocity:
    u_T = u_darcy * (rho*cp)_water / (rho*cp)_soil (Molina-Giraldo et al.
    2011, eq. 2). rho_cp_soil_J_m3K is the SATURATED formation's own bulk
    volumetric heat capacity (water-filled porosity and solid grains
    together) -- the same effective quantity this package already uses
    everywhere else to relate alpha = k_s / rho_cp_J_m3K (see trt.py), not
    the dry mineral-grain value alone.

    Despite the name, u_T is not always SLOWER than u_darcy -- whether it is
    depends on how rho_cp_water_J_m3K compares to rho_cp_soil_J_m3K, and
    water's own volumetric heat capacity is usually the LARGER of the two
    for a real saturated formation (a first version of this docstring
    claimed u_T < u_darcy unconditionally; caught by
    test_groundwater.py::test_thermally_retarded_velocity_scales_by_the_heat_capacity_ratio
    using realistic water/formation values before it shipped -- see that
    test for both directions demonstrated explicitly). What IS always true,
    and is what "retarded" actually refers to, is relative to the water's
    own faster INTERSTITIAL (seepage) velocity u_darcy / porosity, not to
    u_darcy itself -- this function only ever computes the former
    (Darcy-velocity-based) form, since that is what the governing PDE here
    needs directly.
    """
    if darcy_velocity_m_s < 0:
        raise ValueError(f"darcy_velocity_m_s must be >= 0, got {darcy_velocity_m_s}")
    if rho_cp_water_J_m3K <= 0 or rho_cp_soil_J_m3K <= 0:
        raise ValueError("rho_cp_water_J_m3K and rho_cp_soil_J_m3K must both be > 0")
    return darcy_velocity_m_s * rho_cp_water_J_m3K / rho_cp_soil_J_m3K


def moving_line_source_temperature_rise(
    q_prime_W_m: float, x_m: float, y_m: float, k_s: float, alpha: float, u_T_m_s: float,
) -> float:
    """Steady-state temperature rise (K, positive = warmer than undisturbed)
    at position (x_m, y_m) relative to an infinite line source of heat rate
    q_prime_W_m (W/m, positive = heat INJECTED into the ground), with uniform
    thermally-retarded groundwater velocity u_T_m_s in the +x direction. For
    heat EXTRACTION (the sign convention the rest of this package uses, e.g.
    simulation.run_hourly_simulation's hourly_load_W), pass -q_prime_W_m.

    u_T_m_s must be > 0 -- a stationary source has no steady state at all
    (see module docstring); use geothermal.gfunctions/simulation for the
    ordinary (correctly transient, non-advective) response instead.
    """
    if u_T_m_s <= 0:
        raise ValueError(
            f"u_T_m_s must be > 0 (got {u_T_m_s}) -- a stationary line source has no steady "
            "state; this function is only meaningful under nonzero groundwater advection"
        )
    if k_s <= 0 or alpha <= 0:
        raise ValueError("k_s and alpha must both be > 0")
    r_m = math.hypot(x_m, y_m)
    if r_m <= 0:
        raise ValueError("(x_m, y_m) must not be the source location itself (r = 0)")
    beta = u_T_m_s / (2.0 * alpha)
    return q_prime_W_m / (2.0 * math.pi * k_s) * math.exp(beta * x_m) * float(k0(beta * r_m))


def groundwater_steady_state_effect(
    q_prime_W_m: float, r_b: float, k_s: float, alpha: float,
    darcy_velocity_m_s: float, rho_cp_water_J_m3K: float, rho_cp_soil_J_m3K: float,
) -> dict[str, Any]:
    """Screening summary for a single borehole under steady groundwater
    advection: how much colder/warmer the DOWNSTREAM and UPSTREAM sides of
    the borehole wall get once advective steady state is reached, for a
    constant heat-extraction rate q_prime_W_m (W/m, positive = extracted).

    Also reports the Peclet number Pe = u_T * r_b / (2*alpha) -- the
    dimensionless ratio of advective to diffusive heat transport at the
    borehole-wall length scale. Pe << 1 means advection has essentially no
    effect at the borehole itself even once steady (the plume is governed by
    diffusion at this scale, advection only matters much farther away); Pe
    of order 1 or larger means the downstream/upstream asymmetry below is a
    real, design-relevant effect. No hard pass/fail cutoff is asserted here
    (that would overstate a precision this screening check doesn't have) --
    read Pe and the downstream/upstream spread together.

    Counterintuitive but mathematically exact (derived, not guessed -- e_z*K0(z)
    has derivative e^z*(K0(z) - K1(z)), and K1(z) > K0(z) for every z > 0, so
    e^z*K0(z) is strictly decreasing in z everywhere): at a FIXED extraction
    rate, the magnitude of both delta_T_downstream_C and delta_T_upstream_C
    gets SMALLER as darcy_velocity_m_s increases, not larger -- faster
    groundwater flow carries heat away more efficiently, which lowers the
    local steady-state temperature buildup right at the borehole wall, even
    though it also means that (smaller) steady state is reached sooner. Don't
    read "bigger flow -> bigger local delta T" into this function's output;
    the site-relevant effect of faster flow is reaching a (lower) steady
    state faster and a wider/further-reaching downstream plume, not a larger
    temperature swing at the borehole itself.
    """
    u_T = thermally_retarded_velocity(darcy_velocity_m_s, rho_cp_water_J_m3K, rho_cp_soil_J_m3K)
    if u_T <= 0:
        raise ValueError(
            "darcy_velocity_m_s must be > 0 -- at zero flow there is no steady state to report "
            "(see module docstring: a stationary line source keeps warming forever)"
        )
    # Heat EXTRACTION cools the ground: pass -q_prime_W_m into the injection-convention formula.
    dT_downstream = moving_line_source_temperature_rise(-q_prime_W_m, r_b, 0.0, k_s, alpha, u_T)
    dT_upstream = moving_line_source_temperature_rise(-q_prime_W_m, -r_b, 0.0, k_s, alpha, u_T)
    return {
        "thermally_retarded_velocity_m_s": u_T,
        "peclet_number": u_T * r_b / (2.0 * alpha),
        "delta_T_downstream_C": dT_downstream,
        "delta_T_upstream_C": dT_upstream,
    }
