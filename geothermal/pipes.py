"""Borehole internals: pipe configurations and thermal resistances.

A pipe configuration is a plain dict:
    Single/multiple U-tube:
        {"type": "single_u_tube" | "multiple_u_tube" | "independent_multiple_u_tube",
         "pos": [[x1,y1],[x2,y2],...],   # leg positions, m, relative to borehole axis
         "r_in": .., "r_out": .., "k_p": ..,        # pipe radii (m) and conductivity (W/m.K)
         "nPipes": 1,                                 # U-tubes per borehole (multiple_* only)
         "config": "parallel"}                        # multiple_u_tube only: "parallel"|"series"
    Coaxial:
        {"type": "coaxial",
         "r_in": [r_in_inner, r_in_outer], "r_out": [r_out_inner, r_out_outer], "k_p": [k_inner, k_outer]}

k_s (ground conductivity) and k_g (grout conductivity), and the representative
borehole geometry (H, D, r_b), are passed alongside the pipe config since the
pipe's thermal resistance depends on the borehole it sits in.
"""
from __future__ import annotations

import math
from typing import Any

import pygfunction as gt

from geothermal.fluids import to_pygfunction_fluid

PIPE_TYPES = ("single_u_tube", "multiple_u_tube", "independent_multiple_u_tube", "coaxial")

# pygfunction's from_static_params (used by gfunctions.evaluate_mift and networks.py)
# only supports these combinations -- not independent_multiple_u_tube or nPipes > 2.
_FROM_STATIC_PARAMS_PIPE_TYPE = {
    ("single_u_tube", None): "SINGLE_UTUBE",
    ("multiple_u_tube", "parallel"): "DOUBLE_UTUBE_PARALLEL",
    ("multiple_u_tube", "series"): "DOUBLE_UTUBE_SERIES",
}


def pygfunction_pipe_type_str(pipe_config: dict[str, Any]) -> str:
    """Map our pipe config dict to pygfunction's pipe_type_str, for
    gFunction.from_static_params / Network.from_static_params.
    """
    ptype = pipe_config["type"]
    if ptype == "coaxial":
        flow_path = pipe_config.get("flow_path", "annular_in")
        return "COAXIAL_ANNULAR_IN" if flow_path == "annular_in" else "COAXIAL_ANNULAR_OUT"
    key = (ptype, pipe_config.get("config") if ptype == "multiple_u_tube" else None)
    if key not in _FROM_STATIC_PARAMS_PIPE_TYPE:
        raise NotImplementedError(
            "from_static_params-based construction (MIFT g-functions, networks) only "
            "supports single U-tube, double U-tube (parallel/series) and coaxial pipes; "
            f"got {pipe_config!r}. independent_multiple_u_tube and nPipes > 2 need a "
            "hand-built Network (pos/r_in/r_out/k_p per pipe) -- not yet wrapped here."
        )
    return _FROM_STATIC_PARAMS_PIPE_TYPE[key]


def _pipe_flow_resistance(r_in: float, m_flow_pipe: float, fluid: gt.media.Fluid) -> tuple[float, float]:
    """Returns (h_f, R_f): convective coefficient (W/m2.K) and film resistance (m.K/W)."""
    h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
        m_flow_pipe, r_in, fluid.mu, fluid.rho, fluid.k, fluid.cp, 1.0e-6)
    R_f = 1.0 / (h_f * 2 * 3.141592653589793 * r_in)
    return float(h_f), float(R_f)


def build_pipe(
    config: dict[str, Any], H: float, D: float, r_b: float, k_s: float, k_g: float,
    m_flow_borehole: float, fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
) -> Any:
    """Build a pygfunction pipe object (SingleUTube/MultipleUTube/IndependentMultipleUTube/Coaxial)."""
    borehole = gt.boreholes.Borehole(H=H, D=D, r_b=r_b, x=0.0, y=0.0)
    fluid = to_pygfunction_fluid(fluid_str, fluid_percent, fluid_temperature_C)
    ptype = config["type"]

    if ptype in ("single_u_tube", "multiple_u_tube", "independent_multiple_u_tube"):
        pos = config["pos"]
        r_in, r_out, k_p = config["r_in"], config["r_out"], config["k_p"]
        n_legs = len(pos)
        n_pipes = config.get("nPipes", n_legs // 2)
        m_flow_pipe = m_flow_borehole / n_pipes if ptype != "single_u_tube" else m_flow_borehole
        R_p = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)
        _, R_f = _pipe_flow_resistance(r_in, m_flow_pipe, fluid)
        R_fp = R_f + R_p
        if ptype == "single_u_tube":
            return gt.pipes.SingleUTube(pos, r_in, r_out, borehole, k_s, k_g, R_fp)
        if ptype == "multiple_u_tube":
            return gt.pipes.MultipleUTube(
                pos, r_in, r_out, borehole, k_s, k_g, R_fp, n_pipes, config=config.get("config", "parallel"))
        return gt.pipes.IndependentMultipleUTube(pos, r_in, r_out, borehole, k_s, k_g, R_fp, n_pipes)

    if ptype == "coaxial":
        pos = (0.0, 0.0)
        r_in_i, r_in_o = config["r_in"]
        r_out_i, r_out_o = config["r_out"]
        k_p_i, k_p_o = config["k_p"]
        R_p_inner = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in_i, r_in_o, k_p_i)
        R_p_outer = gt.pipes.conduction_thermal_resistance_circular_pipe(r_out_i, r_out_o, k_p_o)
        _, R_f_in = _pipe_flow_resistance(r_in_i, m_flow_borehole, fluid)
        h_f_out = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m_flow_borehole, r_in_o, fluid.mu, fluid.rho, fluid.k, fluid.cp, 1.0e-6)
        R_f_out = 1.0 / (h_f_out * 2 * 3.141592653589793 * r_in_o)
        R_ff = R_f_in + R_p_inner
        R_fp = R_f_out + R_p_outer
        return gt.pipes.Coaxial(pos, (r_in_i, r_in_o), (r_out_i, r_out_o), borehole, k_s, k_g, R_ff, R_fp)

    raise ValueError(f"unknown pipe type {ptype!r}, expected one of {PIPE_TYPES}")


def suggest_flow_rate_kg_s(
    capacity_W: float, config: dict[str, Any],
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    design_delta_T_C: float = 5.0, min_reynolds: float = 4000.0,
) -> dict[str, Any]:
    """Suggest a per-borehole mass flow rate (kg/s) for a given per-borehole
    thermal duty and the selected pipe, as the larger of two candidates:

    1. From the design temperature difference across the loop -- the
       standard sizing method: m_dot = |capacity_W| / (cp * design_delta_T_C).
       design_delta_T_C is a design choice, not a measured constant; 5 C is
       a common ground-loop default, not the only valid one, and callers
       are free to pass their own.
    2. The minimum flow that keeps every individual pipe leg's Reynolds
       number at or above min_reynolds (turbulent flow) -- laminar flow
       inside the pipe sharply raises the convective film resistance
       component of R_b*, a real design constraint, not just a nice-to-have.
       Uses the same per-leg-flow convention as build_pipe() (m_flow_borehole
       split evenly across nPipes for multiple_u_tube, full flow through
       every leg for single_u_tube), so the suggestion stays consistent
       with what effective_resistance() will actually compute if it's used.

    Only single_u_tube/multiple_u_tube/independent_multiple_u_tube are
    supported -- coaxial's annular flow path needs a different Reynolds
    formula, not implemented here.
    """
    if config["type"] not in ("single_u_tube", "multiple_u_tube", "independent_multiple_u_tube"):
        raise NotImplementedError(
            f"suggest_flow_rate_kg_s only supports U-tube pipe types, got {config['type']!r}")
    if design_delta_T_C <= 0:
        raise ValueError("design_delta_T_C must be > 0")
    if min_reynolds <= 0:
        raise ValueError("min_reynolds must be > 0")

    fluid = to_pygfunction_fluid(fluid_str, fluid_percent, fluid_temperature_C)
    flow_from_capacity_kg_s = abs(capacity_W) / (fluid.cp * design_delta_T_C)

    n_legs = len(config["pos"])
    n_pipes = config.get("nPipes", n_legs // 2)
    leg_flow_divisor = 1 if config["type"] == "single_u_tube" else n_pipes
    d_h = 2.0 * config["r_in"]
    # Re = 4 * m_leg / (pi * D_h * mu); m_leg = m_flow_borehole / leg_flow_divisor.
    flow_from_turbulence_kg_s = min_reynolds * math.pi * d_h * fluid.mu * leg_flow_divisor / 4.0

    flow_rate_kg_s = max(flow_from_capacity_kg_s, flow_from_turbulence_kg_s)
    governing = "capacity" if flow_from_capacity_kg_s >= flow_from_turbulence_kg_s else "turbulence"

    return {
        "flow_rate_kg_s": float(flow_rate_kg_s),
        "flow_from_capacity_kg_s": float(flow_from_capacity_kg_s),
        "flow_from_turbulence_kg_s": float(flow_from_turbulence_kg_s),
        "governing": governing,
        "design_delta_T_C": design_delta_T_C,
        "min_reynolds": min_reynolds,
    }


def effective_resistance(
    config: dict[str, Any], H: float, D: float, r_b: float, k_s: float, k_g: float,
    m_flow_borehole: float, fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
) -> dict[str, Any]:
    """Effective borehole thermal resistance R_b* (m.K/W), for display/QA."""
    fluid = to_pygfunction_fluid(fluid_str, fluid_percent, fluid_temperature_C)
    pipe = build_pipe(config, H, D, r_b, k_s, k_g, m_flow_borehole, fluid_str, fluid_percent, fluid_temperature_C)
    R_b_star = float(pipe.effective_borehole_thermal_resistance(m_flow_borehole, fluid.cp))
    return {
        "type": config["type"],
        "R_b_star_mK_W": R_b_star,
        "m_flow_borehole_kg_s": m_flow_borehole,
        "fluid_str": fluid_str,
        "fluid_percent": fluid_percent,
        "fluid_temperature_C": fluid_temperature_C,
    }
