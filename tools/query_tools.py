# -*- coding: utf-8 -*-
"""Query / edit tools ported from the TypeScript tool set (tools/*.ts)."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_query_tools(mcp, revit_get, revit_post):
    """Register ported query/edit tools."""

    # NOTE: get_current_view_info / get_current_view_elements are provided by
    # the base view_tools module; not redefined here to avoid duplicate tool names.

    @mcp.tool()
    async def get_selected_elements(limit: int = 100, ctx: Context = None) -> str:
        """Get the elements currently selected in Revit (id, name, category, type)."""
        return format_response(await revit_post("/get_selected_elements/", {"limit": limit}, ctx))

    @mcp.tool()
    async def get_available_family_types(
        category: str = None, contains: str = None, limit: int = 200, ctx: Context = None
    ) -> str:
        """List available family types, optionally filtered by category and/or a name substring."""
        data = {"limit": limit}
        if category:
            data["category"] = category
        if contains:
            data["contains"] = contains
        return format_response(await revit_post("/get_available_family_types/", data, ctx))

    @mcp.tool()
    async def get_material_quantities(
        category_filters: List[str] = None, selected_elements_only: bool = False, ctx: Context = None
    ) -> str:
        """Material takeoff: area, volume, and element counts per material. Optionally filter by categories or selection."""
        data = {"categoryFilters": category_filters, "selectedElementsOnly": selected_elements_only}
        return format_response(await revit_post("/get_material_quantities/", data, ctx))

    @mcp.tool()
    async def tag_all_walls(use_leader: bool = False, ctx: Context = None) -> str:
        """Tag all walls in the active view at their midpoints (by-category wall tag)."""
        return format_response(await revit_post("/tag_all_walls/", {"useLeader": use_leader}, ctx))

    @mcp.tool()
    async def delete_elements(element_ids: List[int], ctx: Context = None) -> str:
        """Delete elements by their integer element ids."""
        return format_response(await revit_post("/delete_elements/", {"element_ids": element_ids}, ctx))

    @mcp.tool()
    async def modify_element(
        element_id: int, parameters: Dict[str, Any], ctx: Context = None
    ) -> str:
        """Set instance parameters on one element by id. parameters = {param_name: value}."""
        return format_response(
            await revit_post("/modify_element/", {"element_id": element_id, "parameters": parameters}, ctx))
