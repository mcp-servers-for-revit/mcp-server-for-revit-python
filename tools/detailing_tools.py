# -*- coding: utf-8 -*-
"""2D detailing tools: markups, detail/model/symbolic shapes, detail components,
filled & masking regions. Units in FEET, angles in DEGREES."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_detailing_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def create_point_markup(points: List[Dict[str, float]], markup_type: str = "cross",
                                  size: float = 1.0, ctx: Context = None) -> str:
        """Create detail markups (cross | circle | square) at points in the active view."""
        return format_response(await revit_post("/create_point_markup/",
                               {"points": points, "markup_type": markup_type, "size": size}, ctx))

    @mcp.tool()
    async def create_detail_shapes(shape_type: str, center_x: float = 0, center_y: float = 0,
                                   width: float = 5, height: float = 5, radius: float = 5,
                                   sides: int = 6, rotation: float = 0, view_name: str = None, ctx: Context = None) -> str:
        """Draw a 2D shape (rectangle | circle | polygon) as detail lines in a view."""
        d = {"shape_type": shape_type, "center_x": center_x, "center_y": center_y, "width": width,
             "height": height, "radius": radius, "sides": sides, "rotation": rotation}
        if view_name: d["view_name"] = view_name
        return format_response(await revit_post("/create_detail_shapes/", d, ctx))

    @mcp.tool()
    async def create_model_shapes(shape_type: str, center_x: float = 0, center_y: float = 0, center_z: float = 0,
                                  width: float = 5, height: float = 5, radius: float = 5,
                                  sides: int = 6, rotation: float = 0, ctx: Context = None) -> str:
        """Draw a 2D shape as model lines in 3D space."""
        return format_response(await revit_post("/create_model_shapes/",
                               {"shape_type": shape_type, "center_x": center_x, "center_y": center_y,
                                "center_z": center_z, "width": width, "height": height, "radius": radius,
                                "sides": sides, "rotation": rotation}, ctx))

    @mcp.tool()
    async def create_symbolic_shapes(shape_type: str, center_x: float = 0, center_y: float = 0,
                                     width: float = 5, height: float = 5, radius: float = 5,
                                     sides: int = 6, rotation: float = 0, ctx: Context = None) -> str:
        """Draw a 2D shape as symbolic lines (family document only)."""
        return format_response(await revit_post("/create_symbolic_shapes/",
                               {"shape_type": shape_type, "center_x": center_x, "center_y": center_y,
                                "width": width, "height": height, "radius": radius,
                                "sides": sides, "rotation": rotation}, ctx))

    @mcp.tool()
    async def place_detail_component(family: str, type: str, x: float, y: float,
                                     view_name: str = None, rotation: float = 0, ctx: Context = None) -> str:
        """Place a 2D detail-component family instance in a detail/drafting view (feet, degrees)."""
        d = {"family": family, "type": type, "x": x, "y": y, "rotation": rotation}
        if view_name: d["view_name"] = view_name
        return format_response(await revit_post("/place_detail_component/", d, ctx))

    @mcp.tool()
    async def create_filled_region(boundary: List[Dict[str, float]], view_name: str = None,
                                   type_name: str = None, ctx: Context = None) -> str:
        """Create a filled region from a closed boundary loop of points (feet)."""
        d = {"boundary": boundary}
        if view_name: d["view_name"] = view_name
        if type_name: d["type_name"] = type_name
        return format_response(await revit_post("/create_filled_region/", d, ctx))

    @mcp.tool()
    async def create_masking_region(boundary: List[Dict[str, float]], view_name: str = None, ctx: Context = None) -> str:
        """Create a masking region from a closed boundary loop of points (feet)."""
        d = {"boundary": boundary}
        if view_name: d["view_name"] = view_name
        return format_response(await revit_post("/create_masking_region/", d, ctx))
