"""Heat pump coupling: convert a BUILDING-side thermal load into the GROUND-side
load geothermal.simulation/sizing actually consume, via a coefficient of
performance that varies with entering fluid temperature (COP(EFT)) -- not a
single fixed COP, which silently assumes the heat pump performs the same in a
35 C summer loop as a 2 C winter one. Also reports the compressor's own
electrical energy, needed for a real energy/cost comparison rather than just a
ground-temperature-stability proxy.

Sign convention matches the rest of this package (see
sizing.synthesize_hourly_load): +ve building_load_W = heating demand at the
building, -ve = cooling demand. This module's OUTPUT (ground_load_W) is in that
same +ve-extracted/-ve-rejected convention, ready to feed straight into
simulation.run_hourly_simulation or sizing.size_field as their hourly_load_W.

Heating: the building needs Q_h from the heat pump; the compressor adds
W_elec = Q_h / COP_h(EFT) of electrical work, which becomes additional heat
delivered to the building -- so the GROUND only has to supply the rest:
ground_load_W = Q_h - W_elec = Q_h * (1 - 1/COP_h(EFT)).

Cooling: the building rejects Q_c to the heat pump; the compressor adds
W_elec = Q_c / COP_c(EFT) of electrical work, which becomes ADDITIONAL heat
the ground must absorb on top of what it took from the building:
ground_load_W = -(Q_c + W_elec) = -Q_c * (1 + 1/COP_c(EFT)).

The circular dependency this can't avoid: COP(EFT) depends on the entering
fluid temperature, which is an OUTPUT of running the simulation with the very
ground load this function is computing. This module does not attempt an
in-loop, self-consistent coupling (the same class of problem
hybrid.deadband_tower_controller solves by running inside
simulation.run_hourly_simulation's hourly loop) -- that would need a new
per-hour hook threaded through the whole simulation/sizing stack, a materially
bigger change than this module makes. Instead, apply_heat_pump takes eft_C as
a plain input array, supporting two honest workflows:
  1. One-pass, conservative: eft_C = [T_g] * n_hours (or another fixed design
     EFT) -- a reasonable first estimate before the field is even sized.
  2. Iterative refinement: call apply_heat_pump, run the resulting
     ground_load_W through run_hourly_simulation/size_field, then call
     apply_heat_pump AGAIN with the returned T_f_C as the new eft_C, and
     repeat until the sized depth / ground load stops changing meaningfully
     (typically converges in 2-3 passes) -- a genuinely self-consistent
     answer, without any new hook machinery.

COP curves (cop_heating_curve / cop_cooling_curve) are lists of (EFT_C, COP)
points from the heat pump's own published performance data -- piecewise
linear interpolation between them (numpy.interp), CLAMPED (not extrapolated)
outside the given range, so a design condition outside the manufacturer's
tested envelope reports the nearest tested COP rather than a fabricated
extrapolated one.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def apply_heat_pump(
    building_load_W: list[float], eft_C: list[float],
    cop_heating_curve: list[tuple[float, float]], cop_cooling_curve: list[tuple[float, float]],
) -> dict[str, Any]:
    if len(building_load_W) != len(eft_C):
        raise ValueError(
            f"building_load_W ({len(building_load_W)} hours) and eft_C ({len(eft_C)} hours) "
            "must be the same length -- one EFT reading per load hour"
        )
    if len(cop_heating_curve) < 2 or len(cop_cooling_curve) < 2:
        raise ValueError("cop_heating_curve and cop_cooling_curve need at least 2 (EFT_C, COP) points each")

    heating_pts = sorted(cop_heating_curve)
    cooling_pts = sorted(cop_cooling_curve)
    heating_efts, heating_cops = [p[0] for p in heating_pts], [p[1] for p in heating_pts]
    cooling_efts, cooling_cops = [p[0] for p in cooling_pts], [p[1] for p in cooling_pts]
    if min(heating_cops) <= 0 or min(cooling_cops) <= 0:
        raise ValueError("COP values must all be > 0")

    ground_load_W: list[float] = []
    electrical_power_W: list[float] = []
    cop_used: list[float] = []
    for q, eft in zip(building_load_W, eft_C):
        if q >= 0.0:
            cop = float(np.interp(eft, heating_efts, heating_cops))
            w_elec = q / cop
            ground_load_W.append(q - w_elec)
        else:
            cop = float(np.interp(eft, cooling_efts, cooling_cops))
            q_c = -q
            w_elec = q_c / cop
            ground_load_W.append(-(q_c + w_elec))
        electrical_power_W.append(w_elec)
        cop_used.append(cop)

    return {
        "ground_load_W": ground_load_W,
        "electrical_power_W": electrical_power_W,
        "cop_used": cop_used,
        # Assumes hourly time steps (one value per hour), same as every other
        # *_kWh figure elsewhere in this package (e.g. hybrid._tower_summary).
        "total_electrical_energy_kWh": sum(electrical_power_W) / 1000.0,
        "mean_cop": float(np.mean(cop_used)),
    }
