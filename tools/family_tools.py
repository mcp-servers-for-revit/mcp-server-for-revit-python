# -*- coding: utf-8 -*-
"""Family and placement tools"""

from mcp.server.fastmcp import Context
from typing import Any, Dict, Optional
from .utils import format_response


def register_family_tools(mcp, revit_get, revit_post):
    """Register family-related tools"""

    @mcp.tool()
    async def place_family(
        family_name: str,
        type_name: Optional[str] = None,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        rotation: float = 0.0,
        level_name: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
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
        contains: Optional[str] = None, limit: int = 50, ctx: Context = None
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
