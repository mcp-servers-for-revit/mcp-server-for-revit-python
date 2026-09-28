"""g-function evaluation: UHTR, UBWT (via Borefield.evaluate_g_function) and
MIFT (via gFunction.from_static_params, real pipes/fluid/network in one call).
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pygfunction as gt

from geothermal.fields import FieldDict, to_borefield
from geothermal.pipes import pygfunction_pipe_type_str

SOLVERS = ("equivalent", "similarities", "detailed")
BOUNDARY_CONDITIONS = ("UHTR", "UBWT", "MIFT")


def time_grid(t_min_s: float, t_max_s: float, num: int) -> list[float]:
    return [float(t) for t in gt.utilities.time_geometric(t_min_s, t_max_s, num)]


def evaluate(
    field: FieldDict, alpha: float, time: list[float],
    method: str = "equivalent", boundary_condition: str = "UBWT",
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """g-function for UHTR or UBWT boundary conditions (no pipes/fluid needed)."""
    if boundary_condition not in ("UHTR", "UBWT"):
        raise ValueError("evaluate() handles UHTR/UBWT; use evaluate_mift() for MIFT")
    if method not in SOLVERS:
        raise ValueError(f"unknown method {method!r}, expected one of {SOLVERS}")
    bf = to_borefield(field)
    g = bf.evaluate_g_function(alpha, np.asarray(time, dtype=float), method=method,
                                boundary_condition=boundary_condition, options=options)
    return {"time_s": list(time), "g": [float(v) for v in g], "method": method,
            "boundary_condition": boundary_condition}


def evaluate_mift(
    field: FieldDict, alpha: float, time: list[float], pipe_config: dict[str, Any],
    m_flow_network: float, k_s: float, k_g: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    method: str = "equivalent", epsilon: float = 1.0e-6,
) -> dict[str, Any]:
    if method not in SOLVERS:
        raise ValueError(f"unknown method {method!r}, expected one of {SOLVERS}")
    bf = to_borefield(field)
    pipe_type_str = pygfunction_pipe_type_str(pipe_config)
    kwargs: dict[str, Any] = dict(
        H=bf.H, D=bf.D, r_b=bf.r_b, x=bf.x, y=bf.y, alpha=alpha, time=np.asarray(time, dtype=float),
        method=method, boundary_condition="MIFT", m_flow_network=m_flow_network,
        pipe_type_str=pipe_type_str, pos=pipe_config["pos"],
        r_in=pipe_config["r_in"], r_out=pipe_config["r_out"],
        k_s=k_s, k_g=k_g, k_p=pipe_config["k_p"],
        fluid_str=fluid_str, fluid_concentration_pct=fluid_percent,
        fluid_temperature=fluid_temperature_C, epsilon=epsilon,
    )
    gf = gt.gfunction.gFunction.from_static_params(**kwargs)
    return {"time_s": list(time), "g": [float(v) for v in gf.gFunc], "method": method,
            "boundary_condition": "MIFT"}
