"""GHEtool-based sizing, wrapped for cross-checking against geothermal.sizing
(our own, dependency-free sizing loop). Kept as a separate, optional module --
see geothermal/README.md for the ~850MB torch/optuna/scikit-learn dependency
tail GHEtool drags in, of which only a tiny fraction is ever exercised here.

GHEtool 2.4.1 bug, worked around below: constructing Borefield(...) with
ground_data passed as a keyword argument bypasses its ground_data property
setter, so self.custom_gfunction is never initialized and .size() crashes
with AttributeError. Fix: build a bare Borefield() and assign every field via
its property setters afterward.
"""
from __future__ import annotations

from typing import Any

import GHEtool as ghe
import pygfunction as gt

from geothermal.fields import to_borefield
from geothermal.fluids import to_pygfunction_fluid
from geothermal.sizing import build_field_at_depth

_SIZING_METHODS = {"L2": "L2_sizing", "L3": "L3_sizing", "L4": "L4_sizing"}


def _ghetool_pipe_data(pipe_config: dict[str, Any], k_g: float):
    ptype = pipe_config["type"]
    if ptype not in ("single_u_tube", "multiple_u_tube"):
        raise NotImplementedError(
            "sizing_ghetool cross-checks single_u_tube and multiple_u_tube (2-pipe) only; "
            f"got {ptype!r} (independent_multiple_u_tube/coaxial have no GHEtool equivalent wired up here)"
        )
    pos = pipe_config["pos"]
    D_s = abs(pos[0][0])  # shank spacing: distance from borehole axis to a leg centre (m)

    if ptype == "single_u_tube":
        return ghe.SingleUTube(
            k_g=k_g, r_in=pipe_config["r_in"], r_out=pipe_config["r_out"],
            k_p=pipe_config["k_p"], D_s=D_s, epsilon=1.0e-6,
        )

    # multiple_u_tube: GHEtool.MultipleUTube's own pipe_model() always builds pygfunction's
    # MultipleUTube with the library default config="parallel" (it never passes GHEtool's
    # own config='diagonal'/'adjacent' -- a *geometric* leg-placement choice, unrelated to
    # ours -- through to pygfunction's *hydraulic* config='parallel'/'series' kwarg). So a
    # "series"-wired double U-tube cannot be cross-checked: GHEtool would silently size a
    # parallel-wired one instead and the comparison would be misleading, not just approximate.
    if pipe_config.get("config", "parallel") != "parallel":
        raise NotImplementedError(
            "sizing_ghetool cross-checks multiple_u_tube only when wired 'parallel' -- "
            "GHEtool's MultipleUTube pipe model always uses pygfunction's default parallel "
            "hydraulic wiring, so a 'series' design would silently be sized as if it were "
            "parallel instead; that would not be a real cross-check"
        )
    n_pipes = pipe_config.get("nPipes", len(pos) // 2)
    return ghe.MultipleUTube(
        k_g=k_g, r_in=pipe_config["r_in"], r_out=pipe_config["r_out"],
        k_p=pipe_config["k_p"], D_s=D_s, number_of_pipes=n_pipes, epsilon=1.0e-6,
        config="diagonal",  # matches the cross leg layout MainWindow.BuildPipeConfig() sends
    )


def size_field(
    field_template: dict[str, Any], k_s: float, k_g: float, T_g: float,
    pipe_config: dict[str, Any], m_flow_borehole: float,
    fluid_str: str, fluid_percent: float, fluid_temperature_C: float,
    baseload_heating_kWh: list[float], baseload_cooling_kWh: list[float],
    peak_heating_kW: list[float], peak_cooling_kW: list[float],
    simulation_period_years: int,
    T_f_min_limit_C: float, T_f_max_limit_C: float,
    method: str = "L3", H_init: float = 100.0,
) -> dict[str, Any]:
    if method not in _SIZING_METHODS:
        raise ValueError(f"unknown method {method!r}, expected one of {tuple(_SIZING_METHODS)}")

    field = build_field_at_depth(field_template, H_init)
    pygfunction_borefield = to_borefield(field)
    fluid = to_pygfunction_fluid(fluid_str, fluid_percent, fluid_temperature_C)

    borefield = ghe.Borefield()
    borefield.ground_data = ghe.GroundConstantTemperature(k_s=k_s, T_g=T_g)
    borefield.fluid_data = ghe.ConstantFluidData(k_f=fluid.k, rho=fluid.rho, cp=fluid.cp, mu=fluid.mu)
    borefield.flow_data = ghe.ConstantFlowRate(mfr=m_flow_borehole, flow_per_borehole=True)
    borefield.pipe_data = _ghetool_pipe_data(pipe_config, k_g)
    borefield.load = ghe.MonthlyBuildingLoadAbsolute(
        baseload_heating=baseload_heating_kWh, baseload_cooling=baseload_cooling_kWh,
        peak_heating=peak_heating_kW, peak_cooling=peak_cooling_kW,
        simulation_period=simulation_period_years,
    )
    borefield.Tf_min = T_f_min_limit_C
    borefield.Tf_max = T_f_max_limit_C
    borefield.borefield = pygfunction_borefield

    H_m = float(borefield.size(**{_SIZING_METHODS[method]: True}))
    return {
        "H_m": H_m,
        "R_b_star_mK_W": float(borefield.Rb),
        "limiting_quadrant": int(borefield.limiting_quadrant),
        "method": method,
    }
