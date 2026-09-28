"""Supplemental heat rejection (cooling tower / dry cooler) for hybrid GSHP sizing.

pygfunction and GHEtool have no concept of hybrid systems -- this is our own
addition, on top of geothermal.sizing, for the common real-world case where a
cooling-dominated building would otherwise need an oversized field to keep
summer loop temperatures under the equipment's entering-fluid-temperature
limit. Adding a tower that sheds peak heat rejection lets a smaller field
satisfy the same limit.

Control strategy modeled: the tower runs at up to its rated capacity in every
cooling hour, offsetting as much of that hour's ground heat rejection as it
can. This is the one detail that is easy to get wrong: "tower capacity" must
bound how much heat the tower itself removes in any single hour, not the
ground's resulting load -- capping the *ground's* load at a threshold (an
earlier version of this module did that) makes the required tower duty
unbounded, so a "5 kW tower" could be asked to reject 50 kW in a bad hour,
which no real 5 kW tower can do. Here the tower's duty each cooling hour is
min(tower_capacity_kW, that hour's cooling demand), so it never exceeds its
own nameplate rating; whatever the tower can't cover still reaches the
ground. It is not a real controls model: no wet-bulb-temperature-dependent
tower performance, no deadband/staging to avoid short-cycling on small
loads, no approach temperature -- the tower is treated as an ideal
capacity-limited heat sink, always available, the same "deliberately simple
stand-in" precedent as sizing.synthesize_hourly_load. Good enough to answer
"how much smaller does the field get with a tower of this size," not a
substitute for a full hybrid plant design.

Sign convention matches the rest of this package (see
sizing.synthesize_hourly_load): +ve hourly_load_W = heat extracted from the
ground (heating), -ve = heat rejected to the ground (cooling). The tower only
ever assists cooling; it never adds heat, so heating-mode hours are untouched.

A second control strategy lives below apply_cooling_tower():
deadband_tower_controller()/run_hourly_simulation_with_deadband_tower() run
the tower only once the ground itself has drifted a set number of degrees
above a target, instead of every cooling hour -- "ground-temperature-based
control" in Yu et al. 2026 (Buildings 16(18), 3714,
https://doi.org/10.3390/buildings16183714), who found it holds long-term
ground temperature near-flat (+0.28 C over 10 years in their case) for
noticeably less tower run-time than shaving every cooling hour. Unlike
apply_cooling_tower (a one-shot array transform), this dispatch rule depends
on the ground's own simulated response, so it has to run *inside* the hourly
loop -- see simulation.run_hourly_simulation's tower_control hook.

size_field_with_deadband_tower() extends the same strategy to depth SIZING
(bisecting H, not just simulating a fixed one): unlike apply_cooling_tower,
the deadband controller's behavior is depth-dependent (a shallower field
warms up faster, so the tower would need to run sooner/longer there) -- it
can't be precomputed once and reused across every H the bisection tries, the
way apply_cooling_tower's output can. sizing.size_field's tower_control_factory
hook exists so a *fresh* controller (with reset on/off state) is built for
every depth tried, and its bisection stays the single source of truth for
"how to size a field" either way.

minimum_deadband_tower_capacity() extends it a third way, to CAPACITY sizing
at a fixed depth: minimum_tower_capacity's own tower_control_factory hook (one
argument this time -- the candidate capacity_kW, since capacity is what its
bisection varies, not depth) is called once per capacity trial for the same
reason: a bigger candidate capacity keeps the ground cooler, which changes
when the controller's own on/off hysteresis fires, so a fresh controller has
to be built for every capacity tried, not reused.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from geothermal import simulation, sizing


def _tower_summary(tower_load_W: list[float]) -> dict[str, Any]:
    """The run-hours/energy/peak stats both apply_cooling_tower and the deadband dispatch
    report -- factored out so the two control strategies stay comparable on the same terms."""
    return {
        "tower_hours": sum(1 for x in tower_load_W if x > 0.0),
        "tower_energy_kWh": sum(tower_load_W) / 1000.0,
        "tower_peak_kW": (max(tower_load_W) / 1000.0) if tower_load_W else 0.0,
    }


def apply_cooling_tower(hourly_load_W: list[float], tower_capacity_kW: float) -> dict[str, Any]:
    """Offset hourly ground cooling load with a tower rated at tower_capacity_kW.

    For every cooling hour (hourly_load_W[i] < 0), the tower removes
    min(tower_capacity_kW * 1000, -hourly_load_W[i]) -- as much of that
    hour's rejection as it can, up to its own rated capacity, never more.
    Heating hours (hourly_load_W[i] >= 0) are untouched; the tower never
    assists heating. tower_capacity_kW = 0 is a no-op (no tower installed).
    """
    if tower_capacity_kW < 0:
        raise ValueError("tower_capacity_kW must be >= 0")

    capacity_W = tower_capacity_kW * 1000.0
    ground_load_W: list[float] = []
    tower_load_W: list[float] = []
    for q in hourly_load_W:
        if q < 0.0:
            duty_W = min(capacity_W, -q)
            ground_load_W.append(q + duty_W)
            tower_load_W.append(duty_W)
        else:
            ground_load_W.append(q)
            tower_load_W.append(0.0)

    return {
        "ground_load_W": ground_load_W,
        "tower_load_W": tower_load_W,
        "tower_capacity_kW": tower_capacity_kW,
        **_tower_summary(tower_load_W),
    }


def deadband_tower_controller(
    T0_C: float, deadband_C: float, tower_capacity_kW: float,
) -> simulation.TowerControl:
    """Ground-temperature deadband dispatch: turn the tower ON once the ground (borehole
    wall) rises above T0_C + deadband_C, OFF once it falls back below T0_C. Between those
    two thresholds it holds whatever state it was already in -- that hysteresis is the
    "deadband" and is what stops the tower short-cycling on/off near a single setpoint.
    Matches the "ground-temperature-based control strategy" in Yu et al. 2026 (see this
    module's docstring), whose case used T0_C = the undisturbed ground temperature and
    deadband_C = 0.5.

    Returns a stateful callable usable as simulation.run_hourly_simulation's
    tower_control= argument -- construct a fresh one per simulation run (the returned
    closure's on/off memory is not reset between calls, so reusing one across two separate
    runs would leak the first run's final state into the second).
    """
    if deadband_C < 0:
        raise ValueError("deadband_C must be >= 0")
    if tower_capacity_kW < 0:
        raise ValueError("tower_capacity_kW must be >= 0")

    capacity_W = tower_capacity_kW * 1000.0
    on_threshold_C = T0_C + deadband_C
    is_on = False

    def control(hour_index: int, raw_load_W: float, prev_T_b_C: float) -> tuple[float, float]:
        nonlocal is_on
        if raw_load_W >= 0.0:  # heating hour: the tower never assists heating
            return raw_load_W, 0.0
        if not is_on and prev_T_b_C > on_threshold_C:
            is_on = True
        elif is_on and prev_T_b_C < T0_C:
            is_on = False
        if not is_on:
            return raw_load_W, 0.0
        duty_W = min(capacity_W, -raw_load_W)
        return raw_load_W + duty_W, duty_W

    return control


def run_hourly_simulation_with_deadband_tower(
    field: dict[str, Any], alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], tower_capacity_kW: float,
    tower_deadband_C: float = 0.5, tower_setpoint_C: Optional[float] = None,
    dt_s: float = 3600.0, algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
) -> dict[str, Any]:
    """run_hourly_simulation with a ground-temperature deadband tower (see
    deadband_tower_controller) -- the flat, JSON-friendly entry point engine_cli/the WPF
    UI call, since a Python closure can't cross that subprocess boundary itself.

    tower_setpoint_C defaults to T_g (the undisturbed ground temperature), matching Yu et
    al. 2026's own T0 = initial ground temperature -- pass a different value to target
    something else (e.g. a value already known to keep the fluid temperature in range).
    """
    controller = deadband_tower_controller(
        T0_C=T_g if tower_setpoint_C is None else tower_setpoint_C,
        deadband_C=tower_deadband_C, tower_capacity_kW=tower_capacity_kW,
    )
    result = simulation.run_hourly_simulation(
        field, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
        fluid_str, fluid_percent, fluid_temperature_C, hourly_load_W, dt_s=dt_s,
        algorithm=algorithm, gfunc_method=gfunc_method, tower_control=controller,
    )
    # tower_control is always passed above, so "tower_load_W" is always present here --
    # add the same run-hours/energy/peak summary apply_cooling_tower reports, for parity.
    return {**result, "tower_capacity_kW": tower_capacity_kW, **_tower_summary(result["tower_load_W"])}


def size_field_with_deadband_tower(
    field_template: dict[str, Any], alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], simulation_period_years: int,
    tower_capacity_kW: float, tower_deadband_C: float = 0.5, tower_setpoint_C: Optional[float] = None,
    T_f_min_limit_C: Optional[float] = None, T_f_max_limit_C: Optional[float] = None,
    H_min: float = 20.0, H_max: float = 400.0, tol_m: float = 0.5, max_iter: int = 30,
    algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
) -> dict[str, Any]:
    """sizing.size_field with a ground-temperature deadband tower (see
    deadband_tower_controller) active at every depth the bisection tries -- the flat,
    JSON-friendly entry point engine_cli/the WPF UI call.

    Pass the RAW (pre-tower) hourly_load_W, same as size_field without a tower -- unlike
    the apply_cooling_tower + size_field two-call pattern, the tower here must be
    dispatched fresh inside each depth trial (see this module's docstring), so there is
    nothing useful to precompute before calling this.

    tower_setpoint_C defaults to T_g, matching Yu et al. 2026's own T0 = initial ground
    temperature -- see run_hourly_simulation_with_deadband_tower's docstring.
    """
    T0_C = T_g if tower_setpoint_C is None else tower_setpoint_C
    result = sizing.size_field(
        field_template, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
        fluid_str, fluid_percent, fluid_temperature_C, hourly_load_W, simulation_period_years,
        T_f_min_limit_C=T_f_min_limit_C, T_f_max_limit_C=T_f_max_limit_C,
        H_min=H_min, H_max=H_max, tol_m=tol_m, max_iter=max_iter,
        algorithm=algorithm, gfunc_method=gfunc_method,
        tower_control_factory=lambda: deadband_tower_controller(T0_C, tower_deadband_C, tower_capacity_kW),
    )
    return {**result, "tower_capacity_kW": tower_capacity_kW, **_tower_summary(result["tower_load_W"])}


def minimum_tower_capacity(
    field_template: dict[str, Any], H: float, alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], simulation_period_years: int,
    T_f_min_limit_C: Optional[float] = None, T_f_max_limit_C: Optional[float] = None,
    capacity_max_kW: Optional[float] = None, tol_kW: float = 0.5, max_iter: int = 20,
    algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
    tower_control_factory: Optional[Callable[[float], simulation.TowerControl]] = None,
) -> dict[str, Any]:
    """At a FIXED borehole depth H, find the minimum tower capacity that
    keeps the fluid temperature within the given limits over the design
    life. Companion to sizing.size_field, which instead bisects on H with no
    tower -- use this when H is capped (limited drilling depth or site
    access) and a supplemental tower is the only remaining lever.

    tower_control_factory (optional): a one-arg callable -- factory(capacity_kW) ->
    simulation.TowerControl -- called once per CAPACITY this bisects tries, for the same
    reason sizing.size_field's own tower_control_factory hook exists: a controller with
    on/off hysteresis state (see hybrid.deadband_tower_controller) must not be shared
    across trials, since a bigger candidate capacity keeps the ground cooler and changes
    when the controller fires. One argument here (vs. sizing.size_field's zero-arg
    version) because capacity is what THIS bisection varies, not depth -- the controller
    needs to know which candidate capacity it's being built for. None (the default)
    reproduces this function's original apply_cooling_tower-based behavior exactly.

    The search direction bisects on the **max-temperature (cooling) margin
    only** -- more tower capacity always helps it (monotonically), which a
    plain bisection needs. It is tempting to bisect on the combined
    min(low_margin, high_margin) the way sizing.size_field bisects on H, but
    that is NOT safe here: more tower capacity also means less cooling
    "recharge" reaches the ground each year, which pushes the *minimum*
    fluid temperature down over the design life. So the low-temperature
    margin gets WORSE as capacity increases -- the combined margin is not
    monotonic, and bisecting on it directly can wrongly declare a solvable
    case infeasible. Instead: find the minimum capacity that satisfies the
    max-temperature limit (well-behaved), then check the min-temperature
    limit at that capacity. If it also passes, that capacity is the answer.
    If not, no tower capacity satisfies both -- more would only fail the min
    limit harder, less would fail the max limit again -- so this is
    correctly reported as infeasible rather than silently returning a
    capacity that breaks the other limit.

    capacity_max_kW defaults to the single worst cooling hour in
    hourly_load_W -- a tower that size fully offsets the worst hour, a
    reasonable upper bound to start the search from.
    """
    if T_f_min_limit_C is None and T_f_max_limit_C is None:
        raise ValueError("at least one of T_f_min_limit_C / T_f_max_limit_C must be given")
    if len(hourly_load_W) != 8760:
        raise ValueError("hourly_load_W must be one representative 8760-hour year; it is repeated internally")

    field = sizing.build_field_at_depth(field_template, H)
    if capacity_max_kW is None:
        capacity_max_kW = max(0.0, -min(hourly_load_W) / 1000.0)

    def _run(capacity_kW: float) -> tuple[dict[str, Any], float, dict[str, Any]]:
        if tower_control_factory is not None:
            # Deadband (or any future state-dependent strategy): the tower must be
            # dispatched fresh, inside the hourly loop, against this trial's own
            # capacity -- see this function's tower_control_factory docstring.
            controller = tower_control_factory(capacity_kW)
            repeated_load = list(hourly_load_W) * simulation_period_years
            result = simulation.run_hourly_simulation(
                field, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
                fluid_str, fluid_percent, fluid_temperature_C, repeated_load,
                algorithm=algorithm, gfunc_method=gfunc_method, tower_control=controller,
            )
            tower = {
                "ground_load_W": result["ground_load_W"], "tower_load_W": result["tower_load_W"],
                "tower_capacity_kW": capacity_kW, **_tower_summary(result["tower_load_W"]),
            }
        else:
            # Depth-independent AND capacity-behaves-the-same-every-hour strategy
            # (apply_cooling_tower): safe to precompute once per capacity, outside the
            # hourly loop, same as always.
            tower = apply_cooling_tower(hourly_load_W, capacity_kW)
            repeated_load = tower["ground_load_W"] * simulation_period_years
            result = simulation.run_hourly_simulation(
                field, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
                fluid_str, fluid_percent, fluid_temperature_C, repeated_load,
                algorithm=algorithm, gfunc_method=gfunc_method,
            )
        cooling_margin = (T_f_max_limit_C - result["T_f_max_C"]) if T_f_max_limit_C is not None else float("inf")
        return result, cooling_margin, tower

    def _satisfies_both(result: dict[str, Any]) -> bool:
        ok_low = T_f_min_limit_C is None or result["T_f_min_C"] >= T_f_min_limit_C
        ok_high = T_f_max_limit_C is None or result["T_f_max_C"] <= T_f_max_limit_C
        return ok_low and ok_high

    result_hi, cooling_margin_hi, tower_hi = _run(capacity_max_kW)
    if cooling_margin_hi < 0:
        raise ValueError(
            f"even a {capacity_max_kW:.1f} kW tower doesn't bring the max fluid temperature "
            f"under the limit at H={H} m (still {result_hi['T_f_max_C']:.1f} C); "
            f"raise H or capacity_max_kW and retry"
        )
    result_lo, cooling_margin_lo, tower_lo = _run(0.0)
    if cooling_margin_lo >= 0:
        return {"tower_capacity_kW": 0.0, **result_lo, "tower": tower_lo, "iterations": 0}

    lo, hi = 0.0, capacity_max_kW
    result_hi_iter, tower_hi_iter = result_hi, tower_hi
    iterations = 0
    for iterations in range(1, max_iter + 1):
        mid = 0.5 * (lo + hi)
        result_mid, cooling_margin_mid, tower_mid = _run(mid)
        if cooling_margin_mid >= 0:
            hi, result_hi_iter, tower_hi_iter = mid, result_mid, tower_mid
        else:
            lo = mid
        if hi - lo < tol_kW:
            break

    if not _satisfies_both(result_hi_iter):
        raise ValueError(
            f"no tower capacity satisfies both limits at H={H} m: the smallest capacity that "
            f"brings the max fluid temperature under {T_f_max_limit_C} C ({hi:.1f} kW) leaves the "
            f"min fluid temperature at {result_hi_iter['T_f_min_C']:.1f} C, below the "
            f"{T_f_min_limit_C} C limit -- less cooling reaching the ground each year drifts the "
            f"field colder over the design life. A dry cooler alone can't fix this; the field "
            f"needs more depth/boreholes, or supplemental heating too."
        )

    return {"tower_capacity_kW": hi, **result_hi_iter, "tower": tower_hi_iter, "iterations": iterations}


def minimum_deadband_tower_capacity(
    field_template: dict[str, Any], H: float, alpha: float, k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    hourly_load_W: list[float], simulation_period_years: int,
    tower_deadband_C: float = 0.5, tower_setpoint_C: Optional[float] = None,
    T_f_min_limit_C: Optional[float] = None, T_f_max_limit_C: Optional[float] = None,
    capacity_max_kW: Optional[float] = None, tol_kW: float = 0.5, max_iter: int = 20,
    algorithm: str = "ClaessonJaved", gfunc_method: str = "equivalent",
) -> dict[str, Any]:
    """minimum_tower_capacity with a ground-temperature deadband tower (see
    deadband_tower_controller) instead of peak-shaving -- the flat, JSON-friendly entry
    point engine_cli/the WPF UI call, mirroring size_field_with_deadband_tower's shape.

    tower_setpoint_C defaults to T_g, matching Yu et al. 2026's own T0 = initial ground
    temperature -- see run_hourly_simulation_with_deadband_tower's docstring.
    """
    T0_C = T_g if tower_setpoint_C is None else tower_setpoint_C
    return minimum_tower_capacity(
        field_template, H, alpha, k_s, k_g, T_g, pipe_config, m_flow_borehole,
        fluid_str, fluid_percent, fluid_temperature_C, hourly_load_W, simulation_period_years,
        T_f_min_limit_C=T_f_min_limit_C, T_f_max_limit_C=T_f_max_limit_C,
        capacity_max_kW=capacity_max_kW, tol_kW=tol_kW, max_iter=max_iter,
        algorithm=algorithm, gfunc_method=gfunc_method,
        tower_control_factory=lambda capacity_kW: deadband_tower_controller(T0_C, tower_deadband_C, capacity_kW),
    )
