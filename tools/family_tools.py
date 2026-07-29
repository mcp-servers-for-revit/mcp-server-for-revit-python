# -*- coding: utf-8 -*-
"""Family and placement tools"""

from mcp.server.fastmcp import Context
from typing import Dict, Any, Optional
from .utils import format_response


def register_family_tools(mcp, revit_get, revit_post):
    """Register family-related tools"""

    @mcp.tool()
    async def place_family(
        family_name: str,
        type_name: str = None,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        rotation: float = 0.0,
        level_name: str = None,
        properties: Dict[str, Any] = None,
        ctx: Context = None,
    ) -> str:
        """Place a family instance at a specified location in the Revit model"""
        data = {
            "family_name": family_name,
            "type_name": type_name,
            "location": {"x": x, "y": y, "z": z},
            "rotation": rotation,
            "level_name": level_name,
            "properties": properties or {},
        }
        response = await revit_post("/place_family/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def list_families(
        contains: str = None, limit: int = 50, ctx: Context = None
    ) -> str:
        """
        Get a flat list of available family types in the current Revit model.
        Use `contains` to filter by a substring of the family or type name (case-insensitive).
        """
        data = {"limit": limit}
        if contains:
            data["contains"] = contains
        result = await revit_post("/list_families/", data, ctx)
        return format_response(result)

    @mcp.tool()
    async def list_family_categories(ctx: Context = None) -> str:
        """Get a list of all family categories in the current Revit model"""
        response = await revit_get("/list_family_categories/", ctx)
        return format_response(response)

    @mcp.tool()
    async def create_line_based_element(
        wall_type_name: str,
        start_x: float,
        start_y: float,
        start_z: float,
        end_x: float,
        end_y: float,
        end_z: float,
        level_name: str,
        height: float = 10.0,
        offset: float = 0.0,
        flip: bool = False,
        structural: bool = False,
        properties: Optional[Dict[str, Any]] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a line-based element in the Revit model. Currently only walls
        are supported (other line-based categories like structural framing or
        piping are not implemented yet).

        Coordinates and height are in Revit internal units (feet).

        Args:
            wall_type_name: Name of the wall type to use (e.g. "Generic - 200mm").
            start_x, start_y, start_z: Start point of the wall's location line, in feet.
            end_x, end_y, end_z: End point of the wall's location line, in feet.
            level_name: Name of the level to host the wall on.
            height: Unconnected wall height, in feet. Default 10.0 (~3m).
            offset: Location line offset, in feet. Default 0.0.
            flip: Flip the wall's orientation. Default false.
            structural: Mark the wall as structural. Default false.
            properties: Optional dict of parameter_name -> value to set after creation.
        """
        data = {
            "element_type": "wall",
            "wall_type_name": wall_type_name,
            "start_point": {"x": start_x, "y": start_y, "z": start_z},
            "end_point": {"x": end_x, "y": end_y, "z": end_z},
            "level_name": level_name,
            "height": height,
            "offset": offset,
            "flip": flip,
            "structural": structural,
            "properties": properties or {},
        }
        response = await revit_post("/create_line_based_element/", data, ctx)
        return format_response(response)
