# -*- coding: utf-8 -*-
"""MEP tools — ducts, pipes, and mechanical/plumbing systems"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_mep_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register MEP tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def create_duct(
        start_point: dict,
        end_point: dict,
        system_type: str = None,
        duct_type: str = None,
        level_name: str = None,
        diameter: float = None,
        width: float = None,
        height: float = None,
        ctx: Context = None,
    ) -> str:
        """Create a duct in the Revit model between two points.

        Requires a project with mechanical families loaded (MEP template).
        Specify diameter for round ducts, or width+height for rectangular.
        All dimensions in millimeters.

        Args:
            start_point: Start point {"x", "y", "z"} in mm
            end_point: End point {"x", "y", "z"} in mm
            system_type: System type name (e.g., "Supply Air"). Auto-detects if omitted
            duct_type: Duct type name (e.g., "Round Duct"). Auto-detects if omitted
            level_name: Level name. Defaults to nearest level
            diameter: Round duct diameter in mm
            width: Rectangular duct width in mm
            height: Rectangular duct height in mm
            ctx: MCP context for logging
        """
        data = {"start_point": start_point, "end_point": end_point}
        if system_type is not None:
            data["system_type"] = system_type
        if duct_type is not None:
            data["duct_type"] = duct_type
        if level_name is not None:
            data["level_name"] = level_name
        if diameter is not None:
            data["diameter"] = diameter
        if width is not None:
            data["width"] = width
        if height is not None:
            data["height"] = height
        response = await revit_post("/create_duct/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def create_pipe(
        start_point: dict,
        end_point: dict,
        system_type: str = None,
        pipe_type: str = None,
        level_name: str = None,
        diameter: float = None,
        ctx: Context = None,
    ) -> str:
        """Create a pipe in the Revit model between two points.

        Requires a project with plumbing families loaded (MEP template).
        All dimensions in millimeters.

        Args:
            start_point: Start point {"x", "y", "z"} in mm
            end_point: End point {"x", "y", "z"} in mm
            system_type: System type name (e.g., "Domestic Hot Water"). Auto-detects if omitted
            pipe_type: Pipe type name (e.g., "Copper"). Auto-detects if omitted
            level_name: Level name. Defaults to nearest level
            diameter: Pipe diameter in mm
            ctx: MCP context for logging
        """
        data = {"start_point": start_point, "end_point": end_point}
        if system_type is not None:
            data["system_type"] = system_type
        if pipe_type is not None:
            data["pipe_type"] = pipe_type
        if level_name is not None:
            data["level_name"] = level_name
        if diameter is not None:
            data["diameter"] = diameter
        response = await revit_post("/create_pipe/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def create_mep_system(
        system_type: str,
        system_name: str,
        element_ids: list[int] = None,
        ctx: Context = None,
    ) -> str:
        """Create a mechanical or piping system and optionally add elements to it.

        Groups ducts or pipes into a named system for organization and analysis.

        Args:
            system_type: "mechanical" or "piping"
            system_name: Display name for the system (e.g., "Level 1 Supply Air")
            element_ids: Optional list of duct/pipe element IDs to add to the system
            ctx: MCP context for logging
        """
        data = {"system_type": system_type, "system_name": system_name}
        if element_ids is not None:
            data["element_ids"] = element_ids
        response = await revit_post("/create_mep_system/", data, ctx)
        return format_response(response)
