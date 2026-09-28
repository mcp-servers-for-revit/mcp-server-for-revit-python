"""Heat-carrier fluid properties, via pygfunction's SecondaryCoolantProps binding."""
from __future__ import annotations

from typing import Any

import pygfunction as gt

FLUID_TYPES = ("Water", "MEG", "MPG", "MEA", "MMA")

FLUID_NAMES = {
    "Water": "Water",
    "MEG": "Ethylene glycol",
    "MPG": "Propylene glycol",
    "MEA": "Ethanol",
    "MMA": "Methanol",
}


def to_pygfunction_fluid(fluid_str: str, percent: float, temperature_C: float) -> gt.media.Fluid:
    if fluid_str not in FLUID_TYPES:
        raise ValueError(f"unknown fluid_str {fluid_str!r}, expected one of {FLUID_TYPES}")
    return gt.media.Fluid(fluid_str, percent, temperature_C)


def properties(fluid_str: str, percent: float, temperature_C: float) -> dict[str, Any]:
    """Return density, viscosity, conductivity, specific heat, Prandtl number at T."""
    fluid = to_pygfunction_fluid(fluid_str, percent, temperature_C)
    return {
        "fluid_str": fluid_str,
        "name": FLUID_NAMES[fluid_str],
        "percent": percent,
        "temperature_C": temperature_C,
        "density_kg_m3": float(fluid.rho),
        "viscosity_Pa_s": float(fluid.mu),
        "conductivity_W_mK": float(fluid.k),
        "specific_heat_J_kgK": float(fluid.cp),
        "prandtl": float(fluid.mu * fluid.cp / fluid.k),
    }
