# -*- coding: utf-8 -*-
"""Element-creation MCP tools (line-based and surface-based)."""

from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_element_creation_tools(mcp, revit_get, revit_post):
    """Register line-based + surface-based creation tools."""

    @mcp.tool()
    async def create_line_based_element(
        type_name: str,
        level_name: str,
        start: Dict[str, float],
        end: Dict[str, float],
        height_mm: float,
        category: str = "wall",
        structural: bool = False,
        properties: Optional[Dict[str, Any]] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a line-based element. Currently supports walls only; beams and
        pipes need MEP/Structural discipline and are not yet wired.

        Args:
            type_name: Wall type name as it appears in Revit (e.g. "Generic - 200mm").
                       Look up valid names with list_families if unsure.
            level_name: Name of the host level (e.g. "Level 1"). Use list_levels.
            start: Wall start point in MILLIMETRES, project coordinates: {"x":, "y":, "z":}
            end: Wall end point in mm.
            height_mm: Wall unconnected height in mm.
            category: Only "wall" is implemented today. Other values return an error.
            structural: If True, marks the wall as structural.
            properties: Optional instance-parameter overrides, e.g. {"Comments": "via MCP"}.

        Returns the new wall's element_id on success.
        """
        payload = {
            "category": category,
            "type_name": type_name,
            "level_name": level_name,
            "start": start,
            "end": end,
            "height_mm": height_mm,
            "structural": structural,
            "properties": properties or {},
        }
        response = await revit_post("/create_line_based_element/", payload, ctx)
        return format_response(response)

    @mcp.tool()
    async def create_surface_based_element(
        category: str,
        type_name: str,
        level_name: str,
        boundary: List[Dict[str, float]],
        properties: Optional[Dict[str, Any]] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a surface-based element (floor or ceiling) from a closed boundary.

        Args:
            category: "floor" or "ceiling".
            type_name: Type name as it appears in Revit (e.g. "Generic 150mm").
            level_name: Host level name. Use list_levels.
            boundary: List of {"x":, "y":, "z":} points in MILLIMETRES, project
                      coordinates, forming a closed polygon. Minimum 3 points.
                      The boundary will be auto-closed if the last point doesn't
                      match the first.
            properties: Optional instance-parameter overrides.

        Returns the new element's element_id on success. Single-loop boundaries
        only — slabs with holes (multi-loop) are not yet supported.
        """
        payload = {
            "category": category,
            "type_name": type_name,
            "level_name": level_name,
            "boundary": boundary,
            "properties": properties or {},
        }
        response = await revit_post("/create_surface_based_element/", payload, ctx)
        return format_response(response)
