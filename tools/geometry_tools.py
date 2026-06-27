# -*- coding: utf-8 -*-
"""Geometry tools (curves, splines, arcs, points, transforms, curve math).
Ported from Revit-2026-MCP-Server. Units in FEET, angles in DEGREES."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_geometry_tools(mcp, revit_get, revit_post):
    P = lambda path, data, ctx: revit_post(path, data, ctx)

    @mcp.tool()
    async def create_bounded_line(start: Dict[str, float], end: Dict[str, float], ctx: Context = None) -> str:
        """Create a model line between two points (feet)."""
        return format_response(await revit_post("/create_bounded_line/", {"start": start, "end": end}, ctx))

    @mcp.tool()
    async def create_curves_from_points(points: List[Dict[str, float]], closed: bool = False, ctx: Context = None) -> str:
        """Create connected model lines through a list of points."""
        return format_response(await revit_post("/create_curves_from_points/", {"points": points, "closed": closed}, ctx))

    @mcp.tool()
    async def create_hermite_spline(points: List[Dict[str, float]], closed: bool = False, ctx: Context = None) -> str:
        """Create a Hermite spline model curve through points (for curved steel/concrete/sweeps)."""
        return format_response(await revit_post("/create_hermite_spline/", {"points": points, "closed": closed}, ctx))

    @mcp.tool()
    async def create_hermite_spline_with_tangents(points: List[Dict[str, float]], start_tangent: Dict[str, float],
                                                  end_tangent: Dict[str, float], closed: bool = False, ctx: Context = None) -> str:
        """Hermite spline with specified end tangents."""
        return format_response(await revit_post("/create_hermite_spline_with_tangents/",
                               {"points": points, "start_tangent": start_tangent, "end_tangent": end_tangent, "closed": closed}, ctx))

    @mcp.tool()
    async def create_offset_curve(curve_element_id: int, offset: float, normal: Dict[str, float] = None, ctx: Context = None) -> str:
        """Offset an existing curve element by `offset` feet (normal defaults to Z)."""
        d = {"curve_element_id": curve_element_id, "offset": offset}
        if normal: d["normal"] = normal
        return format_response(await revit_post("/create_offset_curve/", d, ctx))

    @mcp.tool()
    async def create_clone_curve(curve_element_id: int, ctx: Context = None) -> str:
        """Clone a curve element."""
        return format_response(await revit_post("/create_clone_curve/", {"curve_element_id": curve_element_id}, ctx))

    @mcp.tool()
    async def create_grid_line(start: Dict[str, float], end: Dict[str, float], name: str = None, ctx: Context = None) -> str:
        """Create a straight grid from two points."""
        d = {"start": start, "end": end}
        if name: d["name"] = name
        return format_response(await revit_post("/create_grid_line/", d, ctx))

    @mcp.tool()
    async def create_grid_arc(center: Dict[str, float], radius: float, start_angle: float = 0,
                              end_angle: float = 90, name: str = None, ctx: Context = None) -> str:
        """Create a curved (radial/arc) grid — for round/curved building layouts."""
        d = {"center": center, "radius": radius, "start_angle": start_angle, "end_angle": end_angle}
        if name: d["name"] = name
        return format_response(await revit_post("/create_grid_arc/", d, ctx))

    @mcp.tool()
    async def create_point(x: float, y: float, z: float = 0.0, ctx: Context = None) -> str:
        """Create a reference point (family docs) or echo coordinates (projects)."""
        return format_response(await revit_post("/create_point/", {"x": x, "y": y, "z": z}, ctx))

    @mcp.tool()
    async def create_point_on_element(element_id: int, point: Dict[str, float], ctx: Context = None) -> str:
        """Project a point onto an element's geometry; returns nearest point + a face Reference (for hosting/dimensioning)."""
        return format_response(await revit_post("/create_point_on_element/", {"element_id": element_id, "point": point}, ctx))

    @mcp.tool()
    async def calculate_line_direction(start: Dict[str, float], end: Dict[str, float], ctx: Context = None) -> str:
        """Return the normalized direction and length between two points."""
        return format_response(await revit_post("/calculate_line_direction/", {"start": start, "end": end}, ctx))

    @mcp.tool()
    async def rotate_elements(element_ids: List[int], angle: float, axis_point: Dict[str, float] = None,
                              axis_dir: Dict[str, float] = None, ctx: Context = None) -> str:
        """Rotate elements by `angle` degrees about an axis (defaults to Z through the origin)."""
        d = {"element_ids": element_ids, "angle": angle}
        if axis_point: d["axis_point"] = axis_point
        if axis_dir: d["axis_dir"] = axis_dir
        return format_response(await revit_post("/rotate_elements/", d, ctx))

    @mcp.tool()
    async def evaluate_curve(curve_element_id: int, parameter: float, normalized: bool = False, ctx: Context = None) -> str:
        """Point on a curve element at a parameter (normalized 0-1 if normalized=true)."""
        return format_response(await revit_post("/evaluate_curve/", {"curve_element_id": curve_element_id, "parameter": parameter, "normalized": normalized}, ctx))

    @mcp.tool()
    async def curve_distance_to_point(curve_element_id: int, point: Dict[str, float], ctx: Context = None) -> str:
        """Shortest distance from a point to a curve element."""
        return format_response(await revit_post("/curve_distance_to_point/", {"curve_element_id": curve_element_id, "point": point}, ctx))

    @mcp.tool()
    async def curve_get_end_point(curve_element_id: int, end: int = 0, ctx: Context = None) -> str:
        """Start (0) or end (1) point of a curve element."""
        return format_response(await revit_post("/curve_get_end_point/", {"curve_element_id": curve_element_id, "end": end}, ctx))

    @mcp.tool()
    async def curve_get_end_parameter(curve_element_id: int, end: int = 0, ctx: Context = None) -> str:
        """Raw parameter at the start/end of a curve element."""
        return format_response(await revit_post("/curve_get_end_parameter/", {"curve_element_id": curve_element_id, "end": end}, ctx))

    @mcp.tool()
    async def curve_get_end_point_reference(curve_element_id: int, end: int = 0, ctx: Context = None) -> str:
        """Stable Reference to a curve endpoint (for dimensioning/constraints)."""
        return format_response(await revit_post("/curve_get_end_point_reference/", {"curve_element_id": curve_element_id, "end": end}, ctx))

    @mcp.tool()
    async def curve_compute_derivatives(curve_element_id: int, parameter: float, normalized: bool = False, ctx: Context = None) -> str:
        """Origin + 1st/2nd derivative vectors of a curve at a parameter."""
        return format_response(await revit_post("/curve_compute_derivatives/", {"curve_element_id": curve_element_id, "parameter": parameter, "normalized": normalized}, ctx))

    @mcp.tool()
    async def curve_compute_normalized_parameter(curve_element_id: int, raw_parameter: float, ctx: Context = None) -> str:
        """Convert a raw parameter to normalized (0-1)."""
        return format_response(await revit_post("/curve_compute_normalized_parameter/", {"curve_element_id": curve_element_id, "raw_parameter": raw_parameter}, ctx))

    @mcp.tool()
    async def curve_compute_raw_parameter(curve_element_id: int, normalized_parameter: float, ctx: Context = None) -> str:
        """Convert a normalized parameter (0-1) to raw."""
        return format_response(await revit_post("/curve_compute_raw_parameter/", {"curve_element_id": curve_element_id, "normalized_parameter": normalized_parameter}, ctx))

    @mcp.tool()
    async def curve_point_location_on_curve(curve_element_id: int, point: Dict[str, float], ctx: Context = None) -> str:
        """Project a point onto a curve element; returns point, parameter, distance."""
        return format_response(await revit_post("/curve_point_location_on_curve/", {"curve_element_id": curve_element_id, "point": point}, ctx))

    @mcp.tool()
    async def curve_compute_closest_points(curve_element_id_a: int, curve_element_id_b: int, ctx: Context = None) -> str:
        """Closest approach between two curve elements (point pair + distance)."""
        return format_response(await revit_post("/curve_compute_closest_points/", {"curve_element_id_a": curve_element_id_a, "curve_element_id_b": curve_element_id_b}, ctx))

    @mcp.tool()
    async def curve_create_reversed(curve_element_id: int, ctx: Context = None) -> str:
        """Create a new curve element with reversed direction."""
        return format_response(await revit_post("/curve_create_reversed/", {"curve_element_id": curve_element_id}, ctx))

    @mcp.tool()
    async def curve_create_transformed(curve_element_id: int, translation: Dict[str, float] = None, rotation_deg: float = 0,
                                       axis_point: Dict[str, float] = None, axis_dir: Dict[str, float] = None, ctx: Context = None) -> str:
        """Create a new curve element transformed by a translation and/or rotation."""
        d = {"curve_element_id": curve_element_id, "rotation_deg": rotation_deg}
        if translation: d["translation"] = translation
        if axis_point: d["axis_point"] = axis_point
        if axis_dir: d["axis_dir"] = axis_dir
        return format_response(await revit_post("/curve_create_transformed/", d, ctx))

    @mcp.tool()
    async def curve_intersect(curve_element_id_a: int, curve_element_id_b: int, ctx: Context = None) -> str:
        """Intersect two curve elements; returns the comparison result and any intersection points."""
        return format_response(await revit_post("/curve_intersect/", {"curve_element_id_a": curve_element_id_a, "curve_element_id_b": curve_element_id_b}, ctx))
