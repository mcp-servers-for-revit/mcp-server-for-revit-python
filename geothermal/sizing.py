"""Borefield depth sizing: pygfunction has no automatic sizing (per the
getting-started report, section 2). This is a from-scratch, auditable sizing
loop built directly on geothermal.simulation: given a field layout, ground
and pipe properties, and a monthly load profile, bisect on uniform borehole
depth H until the simulated fluid temperature over the design life respects
the given min/max limits.

geothermal.sizing_ghetool wraps GHEtool (which also sits on pygfunction) for
the same problem, so the two can be cross-checked against each other.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from geothermal import fields, simulation

FIELD_BUILDERS = {
    "rectangle": fields.rectangle_field,
    "staggered_rectangle": fields.staggered_rectangle_field,
    "dense_rectangle": fields.dense_rectangle_field,
    "box_shaped": fields.box_shaped_field,
    "U_shaped": fields.U_shaped_field,
    "L_shaped": fields.L_shaped_field,
    "circle": fields.circle_field,
}

_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
HOURS_IN_MONTH = [d * 24 for d in _DAYS_IN_MONTH]


def build_field_at_depth(
    field_template: dict[str, Any], H: float, depth_scale: Optional[list[float]] = None,
) -> fields.FieldDict:
    """depth_scale (optional): one relative depth multiplier per borehole (same order
    the chosen layout builder produces, e.g. row-major for rectangle_field), so a
    bisection on a single scalar H can still produce a field with genuinely different
    per-borehole depths -- e.g. boreholes forced shallower near a property line or an
    existing utility, kept at a fixed proportion of whatever depth the rest of the field
    needs. None (the default) reproduces this function's original uniform-depth
    behavior exactly. The layout is still built uniformly first (to get correct x/y
    positions, D, r_b from the chosen layout builder), then each borehole's H is
    overridden to H * depth_scale[i] -- this works for any layout without needing
    per-layout-specific geometry logic.
    """
    layout = field_template["layout"]
    if layout not in FIELD_BUILDERS:
        raise ValueError(f"unknown layout {layout!r}, expected one of {tuple(FIELD_BUILDERS)}")
    kwargs = {k: v for k, v in field_template.items() if k != "layout"}
    kwargs["H"] = H
    field = FIELD_BUILDERS[layout](**kwargs)
    if depth_scale is None:
        return field

    boreholes = field["boreholes"]
    if len(depth_scale) != len(boreholes):
        raise ValueError(f"depth_scale has {len(depth_scale)} entries for {len(boreholes)} boreholes")
    if any(s <= 0 for s in depth_scale):
        raise ValueError("depth_scale entries must all be > 0")
    return {"boreholes": [{**bh, "H": H * scale} for bh, scale in zip(boreholes, depth_scale)]}


def synthesize_hourly_load(
    baseload_heating_kWh: list[float], baseload_cooling_kWh: list[float],
    peak_heating_kW: list[float], peak_cooling_kW: list[float],
    peak_duration_h: int = 6,
) -> list[float]:
    """12 monthly values in -> 8760 hourly values out (W, +ve = extracted from ground).

    Simplification: each month's base load is spread flat across its hours;
    a peak_duration_h block at the start of the month carries that month's
    net peak demand instead. This is NOT GHEtool's/EED's ASHRAE convolution
    method -- it is a deliberately simple stand-in, good enough to exercise
    and cross-check the sizing loop, not a substitute for a validated
    monthly-to-hourly method.
    """
    if not (len(baseload_heating_kWh) == len(baseload_cooling_kWh)
            == len(peak_heating_kW) == len(peak_cooling_kW) == 12):
        raise ValueError("all four monthly load arrays must have 12 values")

    hourly: list[float] = []
    for m in range(12):
        h_month = HOURS_IN_MONTH[m]
        net_base_kWh = baseload_heating_kWh[m] - baseload_cooling_kWh[m]
        base_W = net_base_kWh * 1000.0 / h_month
        peak_net_W = (peak_heating_kW[m] - peak_cooling_kW[m]) * 1000.0
        month_hours = [base_W] * h_month
        for h in range(min(peak_duration_h, h_month)):
            month_hours[h] = peak_net_W
        hourly.extend(month_hours)
    return hourly


def field_layout_from_area(
    area_ambient_m2: float, area_under_building_m2: float, spacing_m: float,
    max_rows: Optional[int] = None,
) -> dict[str, Any]:
    """Largest rectangular borehole grid (spacing B_1 = B_2 = spacing_m)
    that fits the combined available area.

    By default this is the largest roughly-SQUARE grid (N_1 = N_2). Pass
    max_rows to cap N_2 (the "row" direction) instead -- for a site that
    isn't square, or whose boundary isn't at right angles to a natural
    grid, where only so many rows actually fit across the plot's narrow
    dimension. When max_rows is below what the square grid would use, N_1
    grows to keep roughly the same total borehole count (hence roughly the
    same total drilled length) the square grid would have given, just
    reshaped into a longer, narrower layout instead of a smaller one. N_1
    vs N_2 is just a label here -- this engine's rectangle layout treats
    both axes identically, so if your site's constrained dimension is
    conceptually "columns" rather than "rows," it makes no difference
    which one max_rows caps.

    Simplification (independent of max_rows): this engine (like
    pygfunction) has no separate thermal treatment for a borehole drilled
    under a building versus one in open ground -- the ground model doesn't
    take a surface boundary condition that would distinguish them -- so the
    two areas are summed into one equivalent footprint rather than modeled
    as two distinct sub-fields. Good enough for an early feasibility check
    ("does this much land get me a workable field"), not a site layout.
    """
    if spacing_m <= 0:
        raise ValueError("spacing_m must be > 0")
    if area_ambient_m2 < 0 or area_under_building_m2 < 0:
        raise ValueError("areas must be >= 0")
    if max_rows is not None and max_rows <= 0:
        raise ValueError("max_rows must be > 0")
    total_area_m2 = area_ambient_m2 + area_under_building_m2
    if total_area_m2 <= 0:
        raise ValueError("combined area (ambient + under building) must be > 0")

    side_m = total_area_m2 ** 0.5
    n_square = max(1, int(side_m // spacing_m) + 1)

    if max_rows is not None and max_rows < n_square:
        n2 = max_rows
        n1 = max(1, -(-(n_square * n_square) // n2))  # ceil division: keep ~n_square^2 total boreholes
    else:
        n1 = n2 = n_square

    footprint_area_m2 = ((n1 - 1) * spacing_m) * ((n2 - 1) * spacing_m)

    return {
        "N_1": n1, "N_2": n2, "B_1": spacing_m, "B_2": spacing_m,
        "n_boreholes": n1 * n2,
        "total_area_available_m2": total_area_m2,
        "footprint_area_m2": footprint_area_m2,
    }


def synthesize_peak_only_load(
    peak_heating_kW: float, peak_cooling_kW: float,
    heating_season_months: int = 4, cooling_season_months: int = 4,
) -> list[float]:
    """8760-hour load from just two design peak numbers, for when there's no
    monthly load data yet -- deliberately conservative: assumes the peak
    heating load runs continuously for heating_season_months months centered
    on January, and the peak cooling load runs continuously for
    cooling_season_months months centered on July, 0 the rest of the year.
    No part-load shape at all, so it over-predicts annual ground duty; a
    worst-case screening assumption, not a substitute for
    synthesize_hourly_load once real monthly data exists.
    """
    if not (0 <= heating_season_months <= 12) or not (0 <= cooling_season_months <= 12):
        raise ValueError("season months must be between 0 and 12")
    if heating_season_months + cooling_season_months > 12:
        raise ValueError("heating_season_months + cooling_season_months must be <= 12")

    def _season_months(center_month_index: int, length: int) -> set[int]:
        half = length // 2
        return {(center_month_index + offset) % 12 for offset in range(-half, length - half)}

    heating_months = _season_months(0, heating_season_months)   # centered on January
    cooling_months = _season_months(6, cooling_season_months)   # centered on July

    baseload_heating_kWh = [0.0] * 12
    baseload_cooling_kWh = [0.0] * 12
    peak_heating_kW_arr = [peak_heating_kW if m in heating_months else 0.0 for m in range(12)]
    peak_cooling_kW_arr = [peak_cooling_kW if m in cooling_months else 0.0 for m in range(12)]

    return synthesize_hourly_load(
        baseload_heating_kWh, baseload_cooling_kWh, peak_heating_kW_arr, peak_cooling_kW_arr,
        peak_duration_h=744,  # >= hours in any month, so the whole month gets the peak value
    )


def size_field(
    field_template: dict[str, Any], alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], simulation_period_years: int,
    T_f_min_limit_C: Optional[float] = None, T_f_max_limit_C: Optional[float] = None,
    H_min: float = 20.0, H_max: float = 400.0, tol_m: float = 0.5, max_iter: int = 30,
    algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
    gfunc_boundary_condition: str = "UBWT", bore_connectivity: Optional[list[int]] = None,
    T_g_drift_C_per_year: float = 0.0,
    depth_scale: Optional[list[float]] = None,
    borehole_capacitance_J_mK: Optional[float] = None,
    multipole_order: int = 2,
    tower_control_factory: Optional[Callable[[], simulation.TowerControl]] = None,
) -> dict[str, Any]:
    """tower_control_factory (optional): a zero-arg callable that mints a FRESH
    simulation.TowerControl for every depth this function tries. Needed because a
    ground-temperature-dependent controller (see hybrid.deadband_tower_controller)
    carries state -- e.g. on/off hysteresis -- across its own hourly loop, and that
    state must not leak from one candidate depth's trial run into the next one's; a
    single shared controller instance would do exactly that. A depth-INdependent
    transform like hybrid.apply_cooling_tower has no such state, so it doesn't need
    this hook -- apply it to hourly_load_W once, before calling size_field, as
    before. None (the default) reproduces size_field's original behavior exactly.

    gfunc_boundary_condition: passed straight through to every
    simulation.run_hourly_simulation trial this function runs -- see that function's
    own docstring. "UBWT" (default) reproduces size_field's original behavior
    exactly; "MIFT" sizes against the true mixed-inlet-fluid-temperature response
    instead of the UBWT+R_b* approximation, at a noticeably higher cost per trial
    (rebuilding the pipe network once per depth tried, not once per whole call).

    bore_connectivity: passed straight through to every simulation.run_hourly_simulation
    trial -- see that function's own docstring. Only meaningful with
    gfunc_boundary_condition="MIFT"; raises the same ValueError as that function if
    given under "UBWT".

    T_g_drift_C_per_year: passed straight through to every
    simulation.run_hourly_simulation trial -- see that function's own docstring. 0.0
    (default) reproduces size_field's original behavior exactly.

    depth_scale: passed straight through to build_field_at_depth at every H this
    function tries -- see that function's own docstring. None (default) reproduces
    size_field's original uniform-depth behavior exactly; when given, this still
    bisects on a single scalar H, but the field built at each trial has per-borehole
    depth H * depth_scale[i], so the FINAL sized field can have genuinely different
    borehole depths (e.g. boreholes forced shallower near a site constraint) while
    keeping the same well-tested single-scalar bisection.

    borehole_capacitance_J_mK: passed straight through to every
    simulation.run_hourly_simulation trial -- see that function's own docstring. None
    (default) reproduces size_field's original behavior exactly.

    multipole_order: passed straight through to every simulation.run_hourly_simulation
    trial -- see that function's own docstring. 2 (default) reproduces size_field's
    original behavior exactly.
    """
    if T_f_min_limit_C is None and T_f_max_limit_C is None:
        raise ValueError("at least one of T_f_min_limit_C / T_f_max_limit_C must be given")
    if len(hourly_load_W) != 8760:
        raise ValueError("hourly_load_W must be one representative 8760-hour year; it is repeated internally")

    repeated_load = list(hourly_load_W) * simulation_period_years

    def _run(H: float) -> tuple[dict[str, Any], float]:
        field = build_field_at_depth(field_template, H, depth_scale=depth_scale)
        tower_control = tower_control_factory() if tower_control_factory is not None else None
        result = simulation.run_hourly_simulation(
            field, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
            fluid_str, fluid_percent, fluid_temperature_C, repeated_load,
            algorithm=algorithm, gfunc_method=gfunc_method,
            gfunc_boundary_condition=gfunc_boundary_condition, bore_connectivity=bore_connectivity,
            T_g_drift_C_per_year=T_g_drift_C_per_year,
            borehole_capacitance_J_mK=borehole_capacitance_J_mK,
            multipole_order=multipole_order,
            tower_control=tower_control,
        )
        margin_low = (result["T_f_min_C"] - T_f_min_limit_C) if T_f_min_limit_C is not None else float("inf")
        margin_high = (T_f_max_limit_C - result["T_f_max_C"]) if T_f_max_limit_C is not None else float("inf")
        return result, min(margin_low, margin_high)

    result_hi, margin_hi = _run(H_max)
    if margin_hi < 0:
        raise ValueError(
            f"even H_max={H_max} m fails the fluid temperature limit "
            f"(worst margin {margin_hi:.2f} K); raise H_max and retry"
        )
    result_lo, margin_lo = _run(H_min)
    if margin_lo >= 0:
        return {"H_m": H_min, **result_lo, "iterations": 0}

    lo, hi = H_min, H_max
    iterations = 0
    for iterations in range(1, max_iter + 1):
        mid = 0.5 * (lo + hi)
        result_mid, margin_mid = _run(mid)
        if margin_mid >= 0:
            hi, result_hi = mid, result_mid
        else:
            lo = mid
        if hi - lo < tol_m:
            break

    return {"H_m": hi, **result_hi, "iterations": iterations}
