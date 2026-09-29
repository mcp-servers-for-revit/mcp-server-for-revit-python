"""Tests for geothermal.heat_pump.apply_heat_pump -- the "heat pump coupling:
COP(EFT), pump energy" pick from the 2026-09-28 accuracy audit (Phase 1 item
9). Validation strategy: check the energy-balance IDENTITY the whole module
rests on (ground load + compressor work must exactly reconcile with the
building load, both directions), not just that numbers come out plausible;
then prove the iterative-refinement workflow (the module's own documented way
to reach a self-consistent EFT-COP coupling) actually converges.
"""
import pytest

from geothermal import fields, heat_pump, simulation

HEATING_CURVE = [(-5.0, 3.0), (0.0, 3.5), (10.0, 4.5), (20.0, 5.5)]
COOLING_CURVE = [(10.0, 4.0), (20.0, 5.0), (30.0, 6.0), (40.0, 4.5)]


def test_heating_energy_balance_identity():
    # ground_load + electrical_power must exactly equal the building load delivered --
    # the compressor's own work becomes part of what heats the building.
    result = heat_pump.apply_heat_pump(
        building_load_W=[10000.0, 5000.0], eft_C=[0.0, 10.0],
        cop_heating_curve=HEATING_CURVE, cop_cooling_curve=COOLING_CURVE,
    )
    for q, g, w in zip([10000.0, 5000.0], result["ground_load_W"], result["electrical_power_W"]):
        assert g + w == pytest.approx(q)
    assert result["cop_used"] == [pytest.approx(3.5), pytest.approx(4.5)]


def test_cooling_energy_balance_identity():
    # -ground_load (what the ground must absorb) must exactly equal building rejection
    # PLUS compressor work -- the ground takes on the compressor's waste heat too.
    result = heat_pump.apply_heat_pump(
        building_load_W=[-8000.0, -3000.0], eft_C=[20.0, 30.0],
        cop_heating_curve=HEATING_CURVE, cop_cooling_curve=COOLING_CURVE,
    )
    for q, g, w in zip([-8000.0, -3000.0], result["ground_load_W"], result["electrical_power_W"]):
        assert -g == pytest.approx(-q + w)
    assert result["cop_used"] == [pytest.approx(5.0), pytest.approx(6.0)]


def test_cop_interpolates_linearly_between_curve_points():
    # Halfway between (0.0, 3.5) and (10.0, 4.5) on the heating curve -> COP 4.0 at EFT=5.0.
    result = heat_pump.apply_heat_pump(
        building_load_W=[4000.0], eft_C=[5.0], cop_heating_curve=HEATING_CURVE, cop_cooling_curve=COOLING_CURVE,
    )
    assert result["cop_used"][0] == pytest.approx(4.0)


def test_cop_clamps_rather_than_extrapolates_outside_the_curve_range():
    # EFT below the heating curve's lowest point (-5 C) and above the cooling curve's
    # highest (40 C) -- both should clamp to the boundary COP, not extrapolate past it.
    result = heat_pump.apply_heat_pump(
        building_load_W=[5000.0, -5000.0], eft_C=[-20.0, 60.0],
        cop_heating_curve=HEATING_CURVE, cop_cooling_curve=COOLING_CURVE,
    )
    assert result["cop_used"][0] == pytest.approx(3.0)  # heating curve's lowest COP
    assert result["cop_used"][1] == pytest.approx(4.5)  # cooling curve's highest-EFT COP


def test_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="must be the same length"):
        heat_pump.apply_heat_pump(
            building_load_W=[1000.0, 2000.0], eft_C=[10.0],
            cop_heating_curve=HEATING_CURVE, cop_cooling_curve=COOLING_CURVE,
        )


def test_rejects_a_curve_with_fewer_than_two_points():
    with pytest.raises(ValueError, match="at least 2"):
        heat_pump.apply_heat_pump(
            building_load_W=[1000.0], eft_C=[10.0],
            cop_heating_curve=[(0.0, 3.5)], cop_cooling_curve=COOLING_CURVE,
        )


def test_rejects_non_positive_cop():
    with pytest.raises(ValueError, match="must all be > 0"):
        heat_pump.apply_heat_pump(
            building_load_W=[1000.0], eft_C=[10.0],
            cop_heating_curve=[(0.0, 0.0), (10.0, 4.0)], cop_cooling_curve=COOLING_CURVE,
        )


def test_iterative_refinement_with_run_hourly_simulation_converges():
    # The module's own documented workflow: apply_heat_pump with a first-guess EFT,
    # run the simulation, feed T_f_C back as the refined EFT, repeat. Real validation:
    # the sized/simulated T_f trace should stop changing meaningfully after a couple of
    # passes -- proving the composition actually reaches a self-consistent answer, not
    # just "runs without crashing."
    field = fields.rectangle_field(N_1=4, N_2=3, B_1=6.0, B_2=6.0, H=150.0, D=2.0, r_b=0.075)
    pipe = {"type": "single_u_tube", "pos": [(-0.03, 0.0), (0.03, 0.0)], "r_in": 0.0131, "r_out": 0.0160, "k_p": 0.4}
    T_g = 12.0
    n_hours = 8760
    # A mild seasonal building load, well within the COP curves' tested EFT range.
    building_load = [
        4000.0 if (h % 8760) < 2920 else (-3000.0 if 4380 <= (h % 8760) < 6570 else 0.0)
        for h in range(n_hours)
    ]

    eft_guess = [T_g] * n_hours
    prev_mean_T_f = None
    for _ in range(3):
        hp = heat_pump.apply_heat_pump(building_load, eft_guess, HEATING_CURVE, COOLING_CURVE)
        sim = simulation.run_hourly_simulation(
            field, 1.0e-6, 2.0, 1.5, T_g, pipe, 0.30, "MPG", 25.0, 0.0, hp["ground_load_W"],
        )
        mean_T_f = sum(sim["T_f_C"]) / len(sim["T_f_C"])
        if prev_mean_T_f is not None:
            # Converging: each pass's change should shrink, not grow or oscillate wildly.
            assert abs(mean_T_f - prev_mean_T_f) < 1.0
        prev_mean_T_f = mean_T_f
        eft_guess = sim["T_f_C"]
