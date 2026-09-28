"""Boreholes connected in parallel, series or mixed circuits, via
pygfunction's Network class: reversed flow direction and variable-flow-rate
g-functions (Cimmino 2024).

The common case -- every borehole its own parallel circuit -- is already
covered by geothermal.gfunctions.evaluate_mift (it builds an all-parallel
Network internally). Reach for this module when boreholes are wired in
series, or in mixed parallel/series strings, or when the network's flow
rate itself varies.
"""
from __future__ import annotations

import warnings
from typing import Any, Optional

import numpy as np
import pygfunction as gt

from geothermal.fields import FieldDict, to_borefield
from geothermal.pipes import pygfunction_pipe_type_str

SOLVERS = ("equivalent", "similarities", "detailed")


def all_parallel_connectivity(n_boreholes: int) -> list[int]:
    """Every borehole its own circuit, fed directly from the network inlet.
    Equivalent to bore_connectivity=None -- spelled out for clarity."""
    return [-1] * n_boreholes


def all_series_connectivity(n_boreholes: int) -> list[int]:
    """One string: borehole i's inlet is fed from borehole i-1's outlet."""
    return [-1] + list(range(n_boreholes - 1))


def series_strings_connectivity(n_strings: int, n_per_string: int) -> list[int]:
    """n_strings parallel circuits, each n_per_string boreholes long in
    series. Boreholes must be indexed string-major to match: string 0 is
    field indices 0..n_per_string-1, string 1 the next block, and so on --
    build the field (e.g. geothermal.fields.custom_field) with that ordering.
    """
    connectivity = []
    for s in range(n_strings):
        for i in range(n_per_string):
            connectivity.append(-1 if i == 0 else s * n_per_string + i - 1)
    return connectivity


def _is_parallel(bore_connectivity: Optional[list[int]]) -> bool:
    return bore_connectivity is None or all(c == -1 for c in bore_connectivity)


def build_network(
    field: FieldDict, pipe_config: dict[str, Any], k_s: float, k_g: float,
    m_flow_network: float, fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    bore_connectivity: Optional[list[int]] = None, reversible_flow: bool = True,
    epsilon: float = 1.0e-6,
) -> gt.networks.Network:
    bf = to_borefield(field)
    boreholes = bf.to_boreholes()
    if bore_connectivity is not None and len(bore_connectivity) != len(boreholes):
        raise ValueError(
            f"bore_connectivity has {len(bore_connectivity)} entries for {len(boreholes)} boreholes")
    pipe_type_str = pygfunction_pipe_type_str(pipe_config)
    return gt.networks.Network.from_static_params(
        boreholes, pipe_type_str, pipe_config["pos"], pipe_config["r_in"], pipe_config["r_out"],
        k_s, k_g, pipe_config["k_p"], m_flow_network, epsilon,
        fluid_str, fluid_percent, fluid_temperature_C,
        reversible_flow=reversible_flow, bore_connectivity=bore_connectivity,
    )


def evaluate(
    field: FieldDict, alpha: float, time: list[float], pipe_config: dict[str, Any],
    m_flow_network: float, k_s: float, k_g: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    bore_connectivity: Optional[list[int]] = None, reversible_flow: bool = True,
    method: str = "equivalent",
) -> dict[str, Any]:
    """MIFT g-function for an arbitrary parallel/series/mixed network.

    m_flow_network < 0 reverses flow through the whole network (pygfunction's
    own convention: the sign flips direction, the magnitude is the flow
    rate used). 'equivalent' is only valid for all-parallel connectivity --
    pygfunction silently substitutes 'similarities' for series/mixed fields
    and only warns via the Python warnings module, which a GUI would never
    see; this wrapper does that substitution itself up front and reports it
    in the result instead.
    """
    if method not in SOLVERS:
        raise ValueError(f"unknown method {method!r}, expected one of {SOLVERS}")
    network = build_network(field, pipe_config, k_s, k_g, m_flow_network, fluid_str, fluid_percent,
                             fluid_temperature_C, bore_connectivity, reversible_flow)

    method_used = method
    if method == "equivalent" and not _is_parallel(bore_connectivity):
        method_used = "similarities"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gf = gt.gfunction.gFunction(
            network, alpha, time=np.asarray(time, dtype=float), method=method_used,
            boundary_condition="MIFT", m_flow_network=m_flow_network, cp_f=network.cp_f)

    return {
        "time_s": list(time),
        "g": [float(v) for v in gf.gFunc],
        "method_requested": method,
        "method_used": method_used,
        "reversed_flow": bool(m_flow_network < 0),
        "warnings": [str(w.message) for w in caught],
    }


def evaluate_variable_flow(
    field: FieldDict, alpha: float, time: list[float], pipe_config: dict[str, Any],
    m_flow_network_values: list[float], k_s: float, k_g: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    bore_connectivity: Optional[list[int]] = None, reversible_flow: bool = True,
    method: str = "similarities",
) -> dict[str, Any]:
    """Variable-flow-rate g-functions (Cimmino 2024): one g-function for
    every pair of (flow rate at evaluation, flow rate history) in
    m_flow_network_values. result["g"][i][j][k] is the g-function value at
    time[k] for a network evaluated at flow m_flow_network_values[i] having
    operated at flow m_flow_network_values[j] beforehand -- feed this into a
    load-aggregation scheme that tracks flow-rate history alongside load
    history to simulate a variable-speed-pump network over time (not wired
    into geothermal.simulation yet, which assumes constant flow).

    Caveat: pipe film resistance is computed once, at
    m_flow_network_values[0], not per swept value -- if the flow range spans
    very different Reynolds regimes, treat the result as approximate.
    'equivalent' does not support variable flow at all; only
    'similarities'/'detailed' do.
    """
    if method not in ("similarities", "detailed"):
        raise ValueError("variable-flow-rate g-functions require method='similarities' or 'detailed'")
    if len(m_flow_network_values) < 2:
        raise ValueError("m_flow_network_values needs at least 2 values; use evaluate() for a single flow rate")

    network = build_network(field, pipe_config, k_s, k_g, m_flow_network_values[0], fluid_str, fluid_percent,
                             fluid_temperature_C, bore_connectivity, reversible_flow)
    m_flows = np.asarray(m_flow_network_values, dtype=float)
    gf = gt.gfunction.gFunction(
        network, alpha, time=np.asarray(time, dtype=float), method=method,
        boundary_condition="MIFT", m_flow_network=m_flows, cp_f=network.cp_f)

    return {
        "time_s": list(time),
        "m_flow_network_values": list(m_flow_network_values),
        "g": np.asarray(gf.gFunc).tolist(),  # shape (nMassFlow, nMassFlow, nTimes)
        "method": method,
    }


def effective_network_resistance(
    field: FieldDict, pipe_config: dict[str, Any], k_s: float, k_g: float,
    m_flow_network: float, fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    bore_connectivity: Optional[list[int]] = None, reversible_flow: bool = True,
) -> dict[str, Any]:
    """Network-level effective thermal resistance -- like pipes.effective_resistance's
    R_b*, but accounting for the whole network's connection topology rather
    than one representative borehole."""
    network = build_network(field, pipe_config, k_s, k_g, m_flow_network, fluid_str, fluid_percent,
                             fluid_temperature_C, bore_connectivity, reversible_flow)
    R = gt.networks.network_thermal_resistance(network, m_flow_network, network.cp_f)
    return {"network_thermal_resistance_mK_W": float(R), "m_flow_network_kg_s": m_flow_network}
