"""Layered-ground input preparation: turn a geological log (conductivity,
volumetric heat capacity, and optionally undisturbed temperature per stratum)
into the single set of effective homogeneous ground properties every other
module in this package (simulation.py, sizing.py, trt.py, ...) actually
consumes.

pygfunction 2.3.1's g-function solvers all take one scalar `alpha` (thermal
diffusivity) -- there is no layered-medium finite line source in the library,
and building one from scratch (a genuinely different Green's function, not an
extra parameter) is a much larger undertaking than this pass attempts. This
module instead implements the standard, widely-used engineering approximation
for when a full layered analysis isn't performed: a THICKNESS-WEIGHTED
arithmetic average of each layer's properties over the borehole's own buried
length (see Chiasson, "Geothermal Heat Pump and Heat Engine Systems", 2016,
Sec. 4.3, and the ASHRAE Geothermal Energy design chapter's treatment of
non-uniform lithology -- both recommend exactly this averaging when a
full multi-layer numerical model isn't warranted).

Physical basis for arithmetic (not harmonic) weighting: this package's whole
g-function/load-aggregation machinery already treats heat transfer as
per-unit-BOREHOLE-LENGTH (q' = Q/H, uniform along the borehole -- the same
idealization Eskilson's g-function itself rests on), so an "effective medium"
representing that same per-length model is the LENGTH-weighted mean of each
layer's own conductivity/capacity, not a resistances-in-series harmonic mean
(harmonic weighting would be correct for heat flowing IN SERIES through
layers stacked along the flow direction, e.g. through a wall -- here, each
layer conducts heat radially outward independently at its own depth, i.e.
layers are structurally in PARALLEL to each other along the borehole's axis).

This is a documented approximation, not a rigorous layered solution: it
cannot capture a genuinely different response from, say, a thin high-
conductivity layer sandwiched between two insulating ones (a full layered
model would show a locally different fluid temperature response there; this
one only ever returns one bulk number). Good enough for design-stage sizing
when detailed geological logs exist but a full layered numerical model is not
warranted -- not a substitute for one when the lithology contrast is severe
or a specific depth's behavior needs to be resolved.
"""
from __future__ import annotations

from typing import Any, Optional


def weighted_average_ground_properties(
    layers: list[dict[str, Any]], D: float, H: float,
) -> dict[str, Any]:
    """layers: geological log ordered top-to-bottom, each
    {"top_m": float, "bottom_m": float, "k_s": float (W/m.K),
    "rho_cp_J_m3K": float (volumetric heat capacity), "T_g_C": float (optional,
    that layer's own undisturbed ground temperature)}. Depths are measured
    from grade (top_m=0 at the surface), matching D/H's own convention
    elsewhere in this package (D = buried depth, H = borehole length, so the
    borehole spans [D, D+H]).

    Returns the length-weighted effective k_s/alpha/rho_cp (always) and
    T_g_C (only if every layer supplied one -- a partial temperature log
    would silently bias the average, so this raises instead if some layers
    have it and others don't) over exactly the borehole's own buried span,
    plus "layer_overlaps_m" (how much of each input layer actually
    contributed) for the caller to show/inspect.
    """
    if not layers:
        raise ValueError("layers must not be empty")
    if D < 0:
        raise ValueError(f"D must be >= 0, got {D}")
    if H <= 0:
        raise ValueError(f"H must be > 0, got {H}")

    sorted_layers = sorted(layers, key=lambda lyr: lyr["top_m"])
    for lyr in sorted_layers:
        if lyr["bottom_m"] <= lyr["top_m"]:
            raise ValueError(f"layer bottom_m must be > top_m: {lyr}")
        if lyr["k_s"] <= 0:
            raise ValueError(f"layer k_s must be > 0: {lyr}")
        if lyr["rho_cp_J_m3K"] <= 0:
            raise ValueError(f"layer rho_cp_J_m3K must be > 0: {lyr}")
    for a, b in zip(sorted_layers, sorted_layers[1:]):
        if b["top_m"] < a["bottom_m"] - 1e-9:
            raise ValueError(f"layers overlap: {a} and {b}")
        if b["top_m"] > a["bottom_m"] + 1e-9:
            raise ValueError(f"gap in layer log between {a} and {b}")

    have_T_g = [("T_g_C" in lyr and lyr["T_g_C"] is not None) for lyr in sorted_layers]
    if any(have_T_g) and not all(have_T_g):
        raise ValueError("either every layer must supply T_g_C, or none of them")

    z_top, z_bot = D, D + H
    overlaps: list[float] = []
    k_weighted = 0.0
    rho_cp_weighted = 0.0
    T_g_weighted = 0.0
    for lyr in sorted_layers:
        overlap = max(0.0, min(lyr["bottom_m"], z_bot) - max(lyr["top_m"], z_top))
        overlaps.append(overlap)
        k_weighted += lyr["k_s"] * overlap
        rho_cp_weighted += lyr["rho_cp_J_m3K"] * overlap
        if all(have_T_g):
            T_g_weighted += lyr["T_g_C"] * overlap

    total_overlap = sum(overlaps)
    if total_overlap < H - 1e-6:
        raise ValueError(
            f"the layer log does not fully cover the borehole's span [{z_top}, {z_bot}] m "
            f"(only {total_overlap:.3f} of {H:.3f} m covered) -- extend the log to at least "
            f"D+H before averaging"
        )

    k_eff = k_weighted / total_overlap
    rho_cp_eff = rho_cp_weighted / total_overlap
    result: dict[str, Any] = {
        "k_s_eff": k_eff,
        "rho_cp_eff_J_m3K": rho_cp_eff,
        "alpha_eff_m2_s": k_eff / rho_cp_eff,
        "layer_overlaps_m": overlaps,
    }
    if all(have_T_g):
        result["T_g_eff_C"] = T_g_weighted / total_overlap
    return result
