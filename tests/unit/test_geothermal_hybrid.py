"""Tests for geothermal.hybrid: the peak-shaving supplemental cooling tower,
and the ground-temperature deadband tower (see hybrid.py's module docstring
for the Yu et al. 2026 paper this second strategy follows).

Also demonstrates the intended use with geothermal.sizing -- clip the load
with apply_cooling_tower() before handing it to size_field(), and a smaller
field satisfies the same fluid-temperature limits.
"""
import pytest

from geothermal import hybrid, sizing
from geothermal.sizing import build_field_at_depth

FIELD = {"layout": "rectangle", "N_1": 4, "N_2": 3, "B_1": 6.0, "B_2": 6.0, "D": 2.0, "r_b": 0.075}
PIPE = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}

# Strongly cooling-dominated: this is the case the user asked about --
# summer loop temperatures pushed up around 35-40 C by heavy heat rejection.
BASELOAD_HEATING_KWH = [500, 400, 300, 100, 0, 0, 0, 0, 0, 100, 300, 500]
BASELOAD_COOLING_KWH = [0, 0, 500, 2000, 5000, 9000, 12000, 11000, 6000, 2000, 200, 0]
PEAK_HEATING_KW = [8, 7, 5, 2, 0, 0, 0, 0, 0, 2, 5, 8]
PEAK_COOLING_KW = [0, 0, 10, 30, 60, 90, 110, 105, 70, 30, 5, 0]

COMMON_KWARGS = dict(
    k_s=2.0, k_g=1.5, T_g=18.0, pipe_config=PIPE, m_flow_borehole=0.30,
    fluid_str="MPG", fluid_percent=25.0, fluid_temperature_C=20.0,
)


def test_apply_cooling_tower_rejects_tower_capacity_error():
    with pytest.raises(ValueError):
        hybrid.apply_cooling_tower([0.0], tower_capacity_kW=-1.0)


def test_apply_cooling_tower_never_exceeds_its_own_rated_capacity():
    # +2000 W heating (untouched), -1000 W cooling (a 5 kW tower fully covers
    # it), -8000 W cooling (a 5 kW tower can only cover 5 kW of the 8 kW
    # needed -- the remaining 3 kW must still reach the ground).
    load = [2000.0, -1000.0, -8000.0]
    result = hybrid.apply_cooling_tower(load, tower_capacity_kW=5.0)

    assert result["ground_load_W"] == [2000.0, 0.0, -3000.0]
    assert result["tower_load_W"] == [0.0, 1000.0, 5000.0]
    assert result["tower_hours"] == 2
    assert result["tower_peak_kW"] == pytest.approx(5.0)  # never exceeds the 5 kW rating
    assert result["tower_energy_kWh"] == pytest.approx(6.0)


def test_apply_cooling_tower_zero_capacity_removes_nothing():
    load = [1000.0, -1000.0, -50000.0]
    result = hybrid.apply_cooling_tower(load, tower_capacity_kW=0.0)

    assert result["ground_load_W"] == load
    assert result["tower_hours"] == 0


def test_apply_cooling_tower_large_capacity_fully_offsets_all_cooling():
    # A tower rated far above any hour's cooling demand fully covers every
    # cooling hour -- the ground sees only the untouched heating hour.
    load = [1000.0, -500.0, -2000.0]
    result = hybrid.apply_cooling_tower(load, tower_capacity_kW=1000.0)

    assert result["ground_load_W"] == [1000.0, 0.0, 0.0]
    assert result["tower_hours"] == 2
    assert result["tower_peak_kW"] == pytest.approx(2.0)  # the -2000 W hour, well under the 1000 kW rating


def test_tower_lets_a_smaller_field_satisfy_the_same_temperature_limit():
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)

    size_kwargs = dict(
        field_template=FIELD, alpha=1.0e-6, simulation_period_years=10,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=40.0, H_min=20.0, H_max=400.0, tol_m=1.0,
        **COMMON_KWARGS,
    )

    without_tower = sizing.size_field(hourly_load_W=hourly, **size_kwargs)

    tower_result = hybrid.apply_cooling_tower(hourly, tower_capacity_kW=60.0)
    assert tower_result["tower_hours"] > 0  # sanity: the tower actually does something on this profile

    with_tower = sizing.size_field(hourly_load_W=tower_result["ground_load_W"], **size_kwargs)

    print(f"\nwithout tower: H={without_tower['H_m']:.1f} m  |  "
          f"with 60 kW tower: H={with_tower['H_m']:.1f} m "
          f"(tower ran {tower_result['tower_hours']} h, "
          f"{tower_result['tower_energy_kWh']:.0f} kWh/yr, peak {tower_result['tower_peak_kW']:.1f} kW)")

    assert with_tower["H_m"] < without_tower["H_m"]
    assert with_tower["T_f_max_C"] <= without_tower["T_f_max_C"] + 0.5


# ---- deadband_tower_controller: pure state-machine tests, no thermal model needed ----

def test_deadband_controller_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=-1.0, tower_capacity_kW=5.0)
    with pytest.raises(ValueError):
        hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=-1.0)


def test_deadband_controller_never_assists_heating_hours():
    control = hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=5.0)
    # Heating hour (+ve load), ground temperature far above the on-threshold: still untouched.
    load, tower = control(0, 3000.0, prev_T_b_C=30.0)
    assert (load, tower) == (3000.0, 0.0)


def test_deadband_controller_caps_duty_at_its_own_rated_capacity():
    control = hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=5.0)
    control(0, -1.0, prev_T_b_C=100.0)  # any cooling hour with the ground already hot: forces it ON
    load, tower = control(1, -20000.0, prev_T_b_C=100.0)
    assert tower == pytest.approx(5000.0)
    assert load == pytest.approx(-15000.0)  # the remaining 15 kW still reaches the ground


def test_deadband_controller_hysteresis_avoids_short_cycling_at_a_single_setpoint():
    # Same on/off rule as Yu et al. 2026's ground-temperature-based control strategy:
    # ON once the ground exceeds T0+deadband, OFF only once it falls back below T0 --
    # NOT back below T0+deadband, which is what would happen without the hysteresis band.
    control = hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=5.0)

    _, tower = control(0, -8000.0, prev_T_b_C=17.0)   # below T0: off
    assert tower == 0.0
    _, tower = control(1, -8000.0, prev_T_b_C=18.2)   # inside the deadband, was off: stays off
    assert tower == 0.0
    _, tower = control(2, -8000.0, prev_T_b_C=18.6)   # crosses T0+deadband: turns on
    assert tower == 5000.0
    _, tower = control(3, -8000.0, prev_T_b_C=18.2)   # back inside the deadband: hysteresis keeps it on
    assert tower == 5000.0
    _, tower = control(4, -8000.0, prev_T_b_C=17.9)   # drops below T0: turns off
    assert tower == 0.0


# ---- run_hourly_simulation_with_deadband_tower: the flat, JSON-friendly entry point ----

def test_tower_control_hook_defaults_to_unchanged_behavior():
    # Regression guard: run_hourly_simulation's new tower_control=None default must reproduce
    # its pre-existing behavior exactly (no tower_load_W/ground_load_W keys either).
    from geothermal import simulation
    field = build_field_at_depth(FIELD, H=100.0)
    load = [-5000.0] * 24 + [3000.0] * 24
    result = simulation.run_hourly_simulation(field, alpha=1.0e-6, hourly_load_W=load, **COMMON_KWARGS)
    assert "tower_load_W" not in result
    assert "ground_load_W" not in result


def test_deadband_tower_holds_ground_temperature_closer_to_baseline_than_no_tower():
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    field = build_field_at_depth(FIELD, H=100.0)
    years = 5
    repeated = hourly * years

    from geothermal import simulation
    without_tower = simulation.run_hourly_simulation(field, alpha=1.0e-6, hourly_load_W=repeated, **COMMON_KWARGS)
    with_tower = hybrid.run_hourly_simulation_with_deadband_tower(
        field, alpha=1.0e-6, hourly_load_W=repeated, tower_capacity_kW=60.0, tower_deadband_C=0.5, **COMMON_KWARGS)

    assert "tower_load_W" in with_tower
    assert sum(with_tower["tower_load_W"]) > 0  # the tower actually ran on this cooling-dominated profile
    # The whole point: holding the wall temperature closer to T_g (18 C here) than doing nothing.
    assert max(with_tower["T_b_C"]) < max(without_tower["T_b_C"])
    assert with_tower["T_f_max_C"] < without_tower["T_f_max_C"]


# ---- minimum_deadband_tower_capacity: the deadband strategy wired into capacity sizing ----

def test_minimum_tower_capacity_calls_the_deadband_factory_once_per_capacity_trial_not_once_overall():
    # Same regression concern as size_field's tower_control_factory, one level down: a
    # controller with on/off hysteresis state must not be shared across different
    # candidate CAPACITIES either, since a bigger candidate keeps the ground cooler and
    # changes when the controller fires.
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    made: list[object] = []

    def factory(capacity_kW):
        controller = hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=capacity_kW)
        made.append(controller)
        return controller

    hybrid.minimum_tower_capacity(
        FIELD, H=30.0, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=3,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=35.0, capacity_max_kW=300.0, tol_kW=10.0,
        tower_control_factory=factory, **COMMON_KWARGS,
    )

    assert len(made) >= 2, "expected at least the capacity_max_kW and 0.0 kW checks"
    assert len(set(id(c) for c in made)) == len(made), "every capacity trial must get its own, distinct controller"


def test_minimum_deadband_tower_capacity_finds_a_working_capacity_at_a_capped_depth():
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)

    result = hybrid.minimum_deadband_tower_capacity(
        FIELD, H=30.0, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=3,
        tower_deadband_C=0.5, T_f_min_limit_C=-2.0, T_f_max_limit_C=35.0,
        capacity_max_kW=300.0, tol_kW=2.0,
        **COMMON_KWARGS,
    )

    assert result["tower_capacity_kW"] > 0, "H=30 m alone should not satisfy a 35 C limit on this cooling-heavy profile"
    assert result["T_f_max_C"] <= 35.0 + 1e-6
    assert result["T_f_min_C"] >= -2.0 - 1e-6
    assert result["tower"]["tower_hours"] > 0


# ---- size_field_with_deadband_tower: the deadband strategy wired into depth sizing ----

def test_size_field_calls_the_tower_control_factory_once_per_depth_trial_not_once_overall():
    # Regression guard for the exact bug tower_control_factory exists to avoid: a
    # controller with on/off hysteresis state must never be shared across different
    # depth trials in size_field's bisection, or an earlier (rejected) trial's state
    # would leak into a later trial's result.
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    made: list[object] = []

    def factory():
        controller = hybrid.deadband_tower_controller(T0_C=18.0, deadband_C=0.5, tower_capacity_kW=60.0)
        made.append(controller)
        return controller

    sizing.size_field(
        FIELD, alpha=1.0e-6, hourly_load_W=hourly, simulation_period_years=3,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=40.0, H_min=20.0, H_max=400.0, tol_m=5.0,
        tower_control_factory=factory, **COMMON_KWARGS,
    )

    assert len(made) > 1, "expected at least H_max, H_min, and one bisection step"
    assert len(set(id(c) for c in made)) == len(made), "every depth trial must get its own, distinct controller"


def test_size_field_with_deadband_tower_sizes_no_deeper_than_without_a_tower():
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    size_kwargs = dict(
        field_template=FIELD, alpha=1.0e-6, simulation_period_years=10,
        T_f_min_limit_C=-2.0, T_f_max_limit_C=40.0, H_min=20.0, H_max=400.0, tol_m=1.0,
        **COMMON_KWARGS,
    )

    without_tower = sizing.size_field(hourly_load_W=hourly, **size_kwargs)
    with_tower = hybrid.size_field_with_deadband_tower(
        hourly_load_W=hourly, tower_capacity_kW=60.0, tower_deadband_C=0.5, **size_kwargs)

    assert with_tower["H_m"] <= without_tower["H_m"]
    assert sum(with_tower["tower_load_W"]) > 0  # sanity: the tower actually ran somewhere in the search
    assert with_tower["tower_capacity_kW"] == pytest.approx(60.0)
    assert with_tower["tower_hours"] > 0


def test_deadband_tower_runs_fewer_hours_than_shaving_every_cooling_hour():
    # The paper's actual point vs. plain peak-shaving: dispatching off the ground's own
    # temperature, not off load alone, should mean fewer tower run-hours for a comparable
    # capacity, since it only steps in once the ground has actually drifted -- not on every
    # single cooling hour regardless of whether the field still has headroom.
    hourly = sizing.synthesize_hourly_load(
        BASELOAD_HEATING_KWH, BASELOAD_COOLING_KWH, PEAK_HEATING_KW, PEAK_COOLING_KW)
    field = build_field_at_depth(FIELD, H=100.0)
    repeated = hourly * 3

    peak_shaved = hybrid.apply_cooling_tower(repeated, tower_capacity_kW=60.0)
    deadband_result = hybrid.run_hourly_simulation_with_deadband_tower(
        field, alpha=1.0e-6, hourly_load_W=repeated, tower_capacity_kW=60.0, tower_deadband_C=0.5, **COMMON_KWARGS)
    deadband_hours = sum(1 for w in deadband_result["tower_load_W"] if w > 0.0)

    assert deadband_hours < peak_shaved["tower_hours"]
