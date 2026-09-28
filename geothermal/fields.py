"""Bore field geometry: build layouts, serialize to/from plain dicts.

A field is represented outside this module as:
    {"boreholes": [{"H":.., "D":.., "r_b":.., "x":.., "y":.., "tilt":.., "orientation":..}, ...]}
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pygfunction as gt

FieldDict = dict[str, Any]


def to_borefield(field: FieldDict) -> gt.borefield.Borefield:
    boreholes = field["boreholes"]
    if not boreholes:
        raise ValueError("field has no boreholes")
    H = [bh["H"] for bh in boreholes]
    D = [bh["D"] for bh in boreholes]
    r_b = [bh["r_b"] for bh in boreholes]
    x = [bh["x"] for bh in boreholes]
    y = [bh["y"] for bh in boreholes]
    tilt = [bh.get("tilt", 0.0) for bh in boreholes]
    orientation = [bh.get("orientation", 0.0) for bh in boreholes]
    return gt.borefield.Borefield(H, D, r_b, x, y, tilt, orientation)


def from_borefield(borefield: gt.borefield.Borefield) -> FieldDict:
    return {
        "boreholes": [
            {
                "H": float(b.H),
                "D": float(b.D),
                "r_b": float(b.r_b),
                "x": float(b.x),
                "y": float(b.y),
                "tilt": float(b.tilt),
                "orientation": float(b.orientation),
            }
            for b in borefield.to_boreholes()
        ]
    }


def rectangle_field(
    N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float, r_b: float,
    tilt: float = 0.0, origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.rectangle_field(N_1, N_2, B_1, B_2, H, D, r_b, tilt=tilt, origin=origin)
    return from_borefield(bf)


def staggered_rectangle_field(
    N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float, r_b: float,
    include_last_borehole: bool = True, tilt: float = 0.0,
    origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.staggered_rectangle_field(
        N_1, N_2, B_1, B_2, H, D, r_b, include_last_borehole, tilt=tilt, origin=origin)
    return from_borefield(bf)


def dense_rectangle_field(
    N_1: int, N_2: int, B: float, H: float, D: float, r_b: float,
    include_last_borehole: bool = True, tilt: float = 0.0,
    origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.dense_rectangle_field(
        N_1, N_2, B, H, D, r_b, include_last_borehole, tilt=tilt, origin=origin)
    return from_borefield(bf)


def box_shaped_field(
    N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float, r_b: float,
    tilt: float = 0.0, origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.box_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b, tilt=tilt, origin=origin)
    return from_borefield(bf)


def U_shaped_field(
    N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float, r_b: float,
    tilt: float = 0.0, origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.U_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b, tilt=tilt, origin=origin)
    return from_borefield(bf)


def L_shaped_field(
    N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float, r_b: float,
    tilt: float = 0.0, origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.L_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b, tilt=tilt, origin=origin)
    return from_borefield(bf)


def circle_field(
    N: int, R: float, H: float, D: float, r_b: float,
    tilt: float = 0.0, origin: Optional[tuple[float, float]] = None,
) -> FieldDict:
    bf = gt.borefield.Borefield.circle_field(N, R, H, D, r_b, tilt=tilt, origin=origin)
    return from_borefield(bf)


def custom_field(
    x: list[float], y: list[float], H: float | list[float], D: float | list[float],
    r_b: float | list[float], tilt: float | list[float] = 0.0,
    orientation: float | list[float] = 0.0,
) -> FieldDict:
    n = len(x)
    if len(y) != n:
        raise ValueError("x and y must have the same length")

    def _bcast(v):
        return list(v) if isinstance(v, (list, tuple, np.ndarray)) else [v] * n

    bf = gt.borefield.Borefield(_bcast(H), _bcast(D), _bcast(r_b), list(x), list(y), _bcast(tilt), _bcast(orientation))
    return from_borefield(bf)


def field_from_file(filename: str) -> FieldDict:
    bf = gt.borefield.Borefield.from_file(filename)
    return from_borefield(bf)


def combine_fields(*fields: FieldDict) -> FieldDict:
    if not fields:
        raise ValueError("no fields given")
    borefields = [to_borefield(f) for f in fields]
    combined = borefields[0]
    for bf in borefields[1:]:
        combined = combined + bf
    return from_borefield(combined)


def field_summary(field: FieldDict) -> dict[str, Any]:
    bf = to_borefield(field)
    xs, ys = bf.x, bf.y
    return {
        "n_boreholes": len(bf),
        "total_length_m": float(np.sum(bf.H)),
        "H_min_m": float(np.min(bf.H)),
        "H_max_m": float(np.max(bf.H)),
        "H_mean_m": float(np.mean(bf.H)),
        "extent_x_m": float(np.max(xs) - np.min(xs)) if len(xs) > 1 else 0.0,
        "extent_y_m": float(np.max(ys) - np.min(ys)) if len(ys) > 1 else 0.0,
    }


def characteristic_time(field: FieldDict, alpha: float) -> float:
    """t_s = H_mean^2 / (9 alpha), seconds. Used to normalize g-function plots."""
    bf = to_borefield(field)
    return float(bf.H.mean() ** 2 / (9.0 * alpha))
