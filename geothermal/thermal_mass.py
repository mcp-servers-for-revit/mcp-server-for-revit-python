"""Borehole internal thermal capacitance (grout + pipe wall + circulating
fluid) and the short-term fluid-temperature lag it causes -- the "short-term
capacitance" pick from the 2026-09-28 accuracy audit (Phase 1 category 2:
Loads & thermal response).

pygfunction's borehole resistance model (geothermal.pipes.effective_resistance)
is QUASI-STEADY: R_b* relates fluid temperature to borehole-wall temperature
with no time lag at all, as if the grout/pipe/fluid inside the borehole had
zero thermal mass. In reality that internal mass takes real time (minutes to
a couple hours, depending on borehole diameter and grout type) to warm up
after a load step, so the fluid temperature actually LAGS behind what R_b*
alone predicts -- a real effect most GSHP design tools ignore at the hourly
timestep this package uses (usually fine, since the lag time constant is
typically shorter than one hour -- see the module docstring on
simulation.run_hourly_simulation's borehole_capacitance_J_mK parameter for
when it stops being fine).

This is the standard "lumped capacitance" simplification of the borehole
cross-section (one thermal mass, one resistance, one time constant) --
Bauer et al. (2011), "Thermal resistance and capacity models for borehole
heat exchangers", Int. J. Energy Res. 35(4), describe the fuller multi-node
version of this idea (their TRCM, with SEPARATE nodes and time constants for
pipe wall, grout, and fluid); a single lumped node is a coarser, cheaper
approximation of the same physics -- captures the right ORDER of magnitude
and the right qualitative behavior (a damped, delayed approach to the
quasi-steady offset after a load step), not the detailed multi-node internal
temperature distribution the full TRCM would resolve.

Material volumetric heat capacities (grout, pipe) are NOT guessed here --
same precedent as trt.py's rho_cp_J_m3K (ground) being a required, not-fit
input: a wrong guess would silently bias the time constant, so the caller
must supply real values for the grout/pipe products actually being modeled.
Fluid volumetric heat capacity is computed from geothermal.fluids' own
pygfunction-backed density/specific-heat lookup instead of being asked for
separately, since that data already exists in this codebase and is sourced.
"""
from __future__ import annotations

import math
from typing import Any

from geothermal.fluids import to_pygfunction_fluid


def borehole_thermal_capacitance(
    config: dict[str, Any], r_b: float,
    rho_cp_grout_J_m3K: float, rho_cp_pipe_J_m3K: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
) -> dict[str, Any]:
    """Per-unit-length thermal capacitance of everything inside the borehole
    wall (J/m.K): circulating fluid + pipe wall material + grout filling the
    rest of the cross-section. Same pipe config dict shape as pipes.build_pipe.
    """
    if rho_cp_grout_J_m3K <= 0:
        raise ValueError(f"rho_cp_grout_J_m3K must be > 0, got {rho_cp_grout_J_m3K}")
    if rho_cp_pipe_J_m3K <= 0:
        raise ValueError(f"rho_cp_pipe_J_m3K must be > 0, got {rho_cp_pipe_J_m3K}")

    fluid = to_pygfunction_fluid(fluid_str, fluid_percent, fluid_temperature_C)
    rho_cp_fluid_J_m3K = float(fluid.rho) * float(fluid.cp)

    ptype = config["type"]
    if ptype == "coaxial":
        r_in_i, r_in_o = config["r_in"]
        r_out_i, r_out_o = config["r_out"]
        A_fluid_m2 = math.pi * r_in_i**2 + math.pi * (r_in_o**2 - r_out_i**2)
        A_pipe_m2 = math.pi * (r_out_i**2 - r_in_i**2) + math.pi * (r_out_o**2 - r_in_o**2)
        A_grout_m2 = math.pi * r_b**2 - math.pi * r_out_o**2
    else:
        pos = config["pos"]
        r_in, r_out = config["r_in"], config["r_out"]
        n_legs = len(pos)
        A_fluid_m2 = n_legs * math.pi * r_in**2
        A_pipe_m2 = n_legs * math.pi * (r_out**2 - r_in**2)
        A_grout_m2 = math.pi * r_b**2 - n_legs * math.pi * r_out**2

    if A_grout_m2 <= 0:
        raise ValueError(
            f"pipe legs do not fit inside the borehole (r_b={r_b} m) with room left for grout -- "
            f"implied grout area is {A_grout_m2:.6f} m2"
        )

    C_fluid_J_mK = rho_cp_fluid_J_m3K * A_fluid_m2
    C_pipe_J_mK = rho_cp_pipe_J_m3K * A_pipe_m2
    C_grout_J_mK = rho_cp_grout_J_m3K * A_grout_m2

    return {
        "C_b_J_mK": C_fluid_J_mK + C_pipe_J_mK + C_grout_J_mK,
        "C_fluid_J_mK": C_fluid_J_mK,
        "C_pipe_J_mK": C_pipe_J_mK,
        "C_grout_J_mK": C_grout_J_mK,
        "A_fluid_m2": A_fluid_m2,
        "A_pipe_m2": A_pipe_m2,
        "A_grout_m2": A_grout_m2,
    }


def borehole_time_constant_s(R_b_star_mK_W: float, C_b_J_mK: float) -> float:
    """tau_b = R_b* x C_b -- the lumped-capacitance RC time constant (seconds)
    governing how fast the fluid-to-wall temperature offset settles after a
    load step. Both R and C are already per-unit-length, so length cancels
    out of the product exactly like it would in an ordinary RC circuit.
    """
    if R_b_star_mK_W <= 0:
        raise ValueError(f"R_b_star_mK_W must be > 0, got {R_b_star_mK_W}")
    if C_b_J_mK <= 0:
        raise ValueError(f"C_b_J_mK must be > 0, got {C_b_J_mK}")
    return R_b_star_mK_W * C_b_J_mK
