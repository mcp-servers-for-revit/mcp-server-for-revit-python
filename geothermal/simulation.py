"""Multi-year hourly simulation via load aggregation: borehole-wall and mean
fluid temperature for a given hourly ground-load history.
"""
from __future__ import annotations

import math
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
    gfunc_boundary_condition: str = "UBWT", bore_connectivity: Optional[list[int]] = None,
    T_g_drift_C_per_year: float = 0.0,
    tower_control: Optional[TowerControl] = None,
    borehole_capacitance_J_mK: Optional[float] = None,
    multipole_order: int = 2,
) -> dict[str, Any]:
    """hourly_load_W: positive = heat extracted from the ground, W, one value per hour.

    T_g_drift_C_per_year (optional): a long-term linear trend applied to the undisturbed
    ground temperature itself (climate warming or urban heat island), independent of
    whatever the borehole's own heat extraction/injection does. 0.0 (the default)
    reproduces this function's original behavior exactly. When nonzero, hour i's
    reference temperature is T_g + T_g_drift_C_per_year * (elapsed years), and the
    borehole-wall/fluid temperatures are computed relative to THAT instead of a constant
    T_g. Modeled as a simple additive trend on the far-field boundary condition -- a
    deliberate simplification (same "documented, not hidden" precedent as
    sizing.synthesize_hourly_load's monthly-to-hourly load synthesis): a fully rigorous
    treatment would solve a second, separate surface-boundary-temperature-step Green's
    function and superimpose it on the borehole's own Q-driven response, which this does
    not attempt. Treating the regional drift as decoupled from the borehole's own
    (comparatively localized) thermal plume is a reasonable approximation for a slow,
    spatially broad forcing like decadal climate warming -- not appropriate for a fast or
    spatially tight temperature change at the ground surface.

    gfunc_boundary_condition (optional): "UBWT" (default, reproduces this function's
    original behavior exactly, byte for byte) or "MIFT". UBWT convolves a uniform-
    borehole-wall-temperature g-function -- an idealized assumption that every
    borehole sits at the same wall temperature regardless of where it is in the
    field or how the fluid actually flows past it -- then converts wall temperature
    to fluid temperature with a flat R_b* offset computed once, at the design fluid
    temperature, from geothermal.pipes.effective_resistance for ONE representative
    borehole. This is the common "ASHRAE" approximation, and what every version of
    this function before 2026-09-28 always did. MIFT (mixed inlet fluid
    temperatures, Cimmino 2015/2019 -- the accuracy-audit's "UIFT" pick) instead
    builds the actual all-parallel pipe NETWORK for the whole field
    (geothermal.networks.build_network) and solves for how each borehole's heat
    extraction actually differs given a shared inlet fluid temperature and real
    thermal interference between boreholes, rather than assuming they are all
    identical.

    Per pygfunction's own solver internals, a MIFT g-function is ALSO an "effective
    borehole wall temperature" response (same convolution shape as UBWT), so it
    still needs an R_b*-shaped offset to reach real fluid temperature -- this
    function uses geothermal.networks.effective_network_resistance for that. For an
    all-parallel field of otherwise-identical boreholes, that network resistance
    turns out to be numerically IDENTICAL to the single-borehole R_b* used in UBWT
    mode (verified directly: same value to full float precision across several
    field sizes/flow rates during development) -- so the offset step is not where
    MIFT and UBWT actually disagree. The real difference is entirely in the
    g-function itself: UBWT's uniform-wall-temperature idealization understates how
    much boreholes further from the inlet, or a lower per-borehole flow rate, add
    real spread across the field. Confirmed directly (see
    tests/unit/test_mift_boundary_condition.py): the two agree closely (sub-0.1 C)
    for a single borehole or a modestly-sized field at a normal flow rate, and
    diverge measurably (>0.5 C) for a larger field at a low per-borehole flow rate
    -- exactly the regime this discrepancy should matter in.

    Defaults to all-parallel connectivity (every borehole its own circuit, matching
    every other caller of this function) -- pass bore_connectivity (below) for
    series/mixed-string wiring instead. Costs noticeably more per call than UBWT
    (building the actual pipe network is heavier than one representative borehole's
    resistance) -- per this project's own accuracy-over-speed priority, that trade
    is intentional, not an oversight.

    bore_connectivity (optional): only meaningful under gfunc_boundary_condition=
    "MIFT" -- same convention as geothermal.networks (None/all-parallel by default;
    see networks.all_series_connectivity/series_strings_connectivity to build one
    for series or mixed strings). Given with gfunc_boundary_condition="UBWT" raises
    ValueError rather than silently ignoring it: UBWT's Borefield-based g-function
    has no concept of hydraulic connectivity at all (every borehole is treated as
    thermally independent with a uniform wall temperature), so a connectivity
    argument there would either be quietly meaningless or misleadingly suggest it
    changed something.

    tower_control (optional): a per-hour dispatch hook -- see the TowerControl docstring
    above. None (the default) reproduces this function's original behavior exactly, byte
    for byte; passing one adds "tower_load_W"/"ground_load_W" to the returned dict (the
    load actually applied to the ground is hourly_load_W with the tower's contribution
    already netted out, same as geothermal.hybrid.apply_cooling_tower's ground_load_W).
    Unlike apply_cooling_tower (a one-shot array transform, usable when the dispatch rule
    doesn't depend on the ground's own response), this hook can implement a *stateful*
    controller -- e.g. "run the tower once the ground gets N degrees above baseline" --
    because it sees each hour's actual simulated T_b before the next hour is computed.
    Under gfunc_boundary_condition="MIFT", the T_b this hook sees is MIFT's "effective
    borehole wall temperature" (see above) rather than a literal wall temperature --
    still a real, directly ground-state-coupled signal, so the hook's own logic (e.g.
    hybrid.deadband_tower_controller's hysteresis) is unaffected either way.

    borehole_capacitance_J_mK (optional): the borehole's own internal thermal mass
    (grout + pipe + fluid, per unit length -- see geothermal.thermal_mass.
    borehole_thermal_capacitance). None (the default) reproduces this function's
    original behavior exactly, byte for byte: the fluid-to-wall offset -q'*R_b*/H is
    applied instantly, as if that internal mass had none of its own thermal inertia.
    When given, that offset instead relaxes toward its quasi-steady value with time
    constant tau_b = R_eff * borehole_capacitance_J_mK (geothermal.thermal_mass.
    borehole_time_constant_s) via an exact first-order (RC) step response,
    T_b stays exactly as computed either way -- only the fluid-side offset lags,
    since it is specifically the borehole's OWN internal mass (not the ground's,
    already correctly transient through the g-function convolution) that this
    approximates. A coarse, single-lump simplification of the borehole cross-section
    (see thermal_mass.py's own module docstring) -- captures the right order of
    magnitude and qualitative shape of the short-term lag, not a detailed multi-node
    internal temperature distribution. Matters most at sub-hourly-equivalent duty
    swings (e.g. a heat pump cycling within an hour) or the first few hours of a
    TRT-style step-load history; usually negligible for ordinary hourly building-load
    simulation once tau_b is well under the 3600 s timestep.

    multipole_order: pygfunction's own J parameter (default 2, matching
    pipes.build_pipe's own default -- this was previously hardcoded at that
    same default with no way to change it from here specifically; raising it
    from the g-Function tab's standalone effective_borehole_resistance/
    build_pipe calls had NO effect on an actual simulation/sizing run's own
    R_b* until this parameter existed). See pipes.build_pipe's own docstring
    for when raising it matters.
    """
    if algorithm not in LOAD_AGGREGATION_ALGORITHMS:
        raise ValueError(f"unknown algorithm {algorithm!r}, expected one of {tuple(LOAD_AGGREGATION_ALGORITHMS)}")
    if gfunc_boundary_condition not in ("UBWT", "MIFT"):
        raise ValueError(f"gfunc_boundary_condition must be 'UBWT' or 'MIFT', got {gfunc_boundary_condition!r}")
    if bore_connectivity is not None and gfunc_boundary_condition != "MIFT":
        raise ValueError(
            "bore_connectivity requires gfunc_boundary_condition='MIFT' -- UBWT's g-function "
            "has no concept of hydraulic connectivity between boreholes"
        )

    bf = to_borefield(field)
    total_length_m = float(bf.H.sum())
    n_hours = len(hourly_load_W)
    if n_hours == 0:
        raise ValueError("hourly_load_W is empty")
    t_max_s = dt_s * n_hours

    LoadAggCls = LOAD_AGGREGATION_ALGORITHMS[algorithm]
    load_agg = LoadAggCls(dt_s, t_max_s)
    times = load_agg.get_times_for_simulation()

    if gfunc_boundary_condition == "MIFT":
        from geothermal import networks
        # "m_flow_borehole" generalizes to "flow per parallel CIRCUIT at the network
        # inlet" -- identical to flow-per-borehole for the default all-parallel case
        # (every borehole is its own circuit), and to flow-per-STRING for series/mixed
        # connectivity (every borehole in a series string carries that string's flow).
        n_circuits = len(bf) if bore_connectivity is None else sum(1 for c in bore_connectivity if c == -1)
        m_flow_network = m_flow_borehole * n_circuits
        g = np.asarray(networks.evaluate(
            field, alpha, times.tolist(), pipe_config, m_flow_network, k_s, k_g,
            fluid_str, fluid_percent, fluid_temperature_C,
            bore_connectivity=bore_connectivity, method=gfunc_method, multipole_order=multipole_order,
        )["g"])
        R_eff = networks.effective_network_resistance(
            field, pipe_config, k_s, k_g, m_flow_network, fluid_str, fluid_percent, fluid_temperature_C,
            bore_connectivity=bore_connectivity, multipole_order=multipole_order,
        )["network_thermal_resistance_mK_W"]
    else:
        g = bf.evaluate_g_function(alpha, times, method=gfunc_method, boundary_condition="UBWT")
        R_eff = effective_resistance(
            pipe_config, float(bf.H.mean()), float(bf.D.mean()), float(bf.r_b.mean()), k_s, k_g,
            m_flow_borehole, fluid_str, fluid_percent, fluid_temperature_C,
            multipole_order=multipole_order,
        )["R_b_star_mK_W"]
    load_agg.initialize(g / (2 * np.pi * k_s))

    seconds_per_year = 8760.0 * 3600.0
    Q = np.asarray(hourly_load_W, dtype=float)
    T_b = np.zeros(n_hours)
    T_f = np.zeros(n_hours)
    tower_load_W = np.zeros(n_hours) if tower_control is not None else None
    tau_b = borehole_capacitance_J_mK * R_eff if borehole_capacitance_J_mK is not None else None
    # Lumped-capacitance state: starts at 0 (the borehole's internal mass is at rest,
    # fluid == wall temperature, before hour 0's load is ever applied).
    offset_lag = 0.0
    for i in range(n_hours):
        q_i = float(Q[i])
        # T_g at hour i's own elapsed time -- constant (== T_g) whenever drift is 0.
        T_g_i = T_g + T_g_drift_C_per_year * ((i + 1) * dt_s / seconds_per_year)
        if tower_control is not None:
            prev_T_b = float(T_b[i - 1]) if i > 0 else T_g
            q_i, tower_load_W[i] = tower_control(i, q_i, prev_T_b)
        load_agg.next_time_step((i + 1) * dt_s)
        load_agg.set_current_load(q_i / total_length_m)
        T_b[i] = T_g_i - load_agg.temporal_superposition()
        offset_qs = -q_i / total_length_m * R_eff
        if tau_b is not None:
            offset_lag += (offset_qs - offset_lag) * (1.0 - math.exp(-dt_s / tau_b))
            T_f[i] = T_b[i] + offset_lag
        else:
            T_f[i] = T_b[i] + offset_qs
        if tower_control is not None:
            Q[i] = q_i  # so the returned "ground_load_W" reflects what the ground actually saw

    result = {
        "T_b_C": T_b.tolist(),
        "T_f_C": T_f.tolist(),
        # Kept under its original key for backward compatibility even under MIFT,
        # where it is really the NETWORK's effective resistance, not one borehole's
        # R_b* -- gfunc_boundary_condition (below) disambiguates which one it is.
        "R_b_star_mK_W": R_eff,
        "total_length_m": total_length_m,
        "algorithm": algorithm,
        "gfunc_boundary_condition": gfunc_boundary_condition,
        "T_f_min_C": float(T_f.min()),
        "T_f_max_C": float(T_f.max()),
    }
    if tau_b is not None:
        result["borehole_time_constant_s"] = tau_b
    if tower_load_W is not None:
        result["tower_load_W"] = tower_load_W.tolist()
        result["ground_load_W"] = Q.tolist()
    return result
