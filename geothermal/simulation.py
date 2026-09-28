"""Multi-year hourly simulation via load aggregation: borehole-wall and mean
fluid temperature for a given hourly ground-load history.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
import pygfunction as gt

from geothermal.fields import FieldDict, to_borefield
from geothermal.pipes import effective_resistance

LOAD_AGGREGATION_ALGORITHMS = {
    "ClaessonJaved": gt.load_aggregation.ClaessonJaved,
    "MLAA": gt.load_aggregation.MLAA,
    "Liu": gt.load_aggregation.Liu,
}

# Called once per hour, BEFORE that hour's load reaches the ground, as
# tower_control(hour_index, raw_load_W, prev_T_b_C) -> (adjusted_load_W, tower_load_W).
# prev_T_b_C is the borehole-wall temperature computed for the previous hour (or T_g for
# hour 0) -- the temperature a real controller would actually have measured before deciding
# what to do this hour. See geothermal/hybrid.py for the ground-temperature deadband
# controller this hook exists for.
TowerControl = Callable[[int, float, float], "tuple[float, float]"]


def run_hourly_simulation(
    field: FieldDict, alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], dt_s: float = 3600.0,
    algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
    tower_control: Optional[TowerControl] = None,
) -> dict[str, Any]:
    """hourly_load_W: positive = heat extracted from the ground, W, one value per hour.

    tower_control (optional): a per-hour dispatch hook -- see the TowerControl docstring
    above. None (the default) reproduces this function's original behavior exactly, byte
    for byte; passing one adds "tower_load_W"/"ground_load_W" to the returned dict (the
    load actually applied to the ground is hourly_load_W with the tower's contribution
    already netted out, same as geothermal.hybrid.apply_cooling_tower's ground_load_W).
    Unlike apply_cooling_tower (a one-shot array transform, usable when the dispatch rule
    doesn't depend on the ground's own response), this hook can implement a *stateful*
    controller -- e.g. "run the tower once the ground gets N degrees above baseline" --
    because it sees each hour's actual simulated T_b before the next hour is computed.
    """
    if algorithm not in LOAD_AGGREGATION_ALGORITHMS:
        raise ValueError(f"unknown algorithm {algorithm!r}, expected one of {tuple(LOAD_AGGREGATION_ALGORITHMS)}")

    bf = to_borefield(field)
    total_length_m = float(bf.H.sum())
    n_hours = len(hourly_load_W)
    if n_hours == 0:
        raise ValueError("hourly_load_W is empty")
    t_max_s = dt_s * n_hours

    LoadAggCls = LOAD_AGGREGATION_ALGORITHMS[algorithm]
    load_agg = LoadAggCls(dt_s, t_max_s)
    times = load_agg.get_times_for_simulation()
    g = bf.evaluate_g_function(alpha, times, method=gfunc_method, boundary_condition="UBWT")
    load_agg.initialize(g / (2 * np.pi * k_s))

    R_b_star = effective_resistance(
        pipe_config, float(bf.H.mean()), float(bf.D.mean()), float(bf.r_b.mean()), k_s, k_g,
        m_flow_borehole, fluid_str, fluid_percent, fluid_temperature_C,
    )["R_b_star_mK_W"]

    Q = np.asarray(hourly_load_W, dtype=float)
    T_b = np.zeros(n_hours)
    T_f = np.zeros(n_hours)
    tower_load_W = np.zeros(n_hours) if tower_control is not None else None
    for i in range(n_hours):
        q_i = float(Q[i])
        if tower_control is not None:
            prev_T_b = float(T_b[i - 1]) if i > 0 else T_g
            q_i, tower_load_W[i] = tower_control(i, q_i, prev_T_b)
        load_agg.next_time_step((i + 1) * dt_s)
        load_agg.set_current_load(q_i / total_length_m)
        T_b[i] = T_g - load_agg.temporal_superposition()
        T_f[i] = T_b[i] - q_i / total_length_m * R_b_star
        if tower_control is not None:
            Q[i] = q_i  # so the returned "ground_load_W" reflects what the ground actually saw

    result = {
        "T_b_C": T_b.tolist(),
        "T_f_C": T_f.tolist(),
        "R_b_star_mK_W": R_b_star,
        "total_length_m": total_length_m,
        "algorithm": algorithm,
        "T_f_min_C": float(T_f.min()),
        "T_f_max_C": float(T_f.max()),
    }
    if tower_load_W is not None:
        result["tower_load_W"] = tower_load_W.tolist()
        result["ground_load_W"] = Q.tolist()
    return result
