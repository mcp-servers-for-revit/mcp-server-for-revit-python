# -*- coding: utf-8 -*-
"""Detail tools — detail lines for view-specific annotation"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_detail_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register detail tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def create_detail_line(
        start_point: dict,
        end_point: dict,
        view_name: str = None,
        line_style: str = None,
        ctx: Context = None,
    ) -> str:
        """Create a detail line in a Revit view for annotation purposes.

        Detail lines are view-specific 2D annotation elements. They only
        appear in the view where they are created (unlike model lines).
        Must be created in a plan, section, or detail view.

        Args:
            start_point: Start point {"x", "y", "z"} in mm
            end_point: End point {"x", "y", "z"} in mm
            view_name: Target view name (defaults to active view)
            line_style: Line style name (e.g., "Medium Lines"). Uses default if omitted
            ctx: MCP context for logging
        """
        data = {"start_point": start_point, "end_point": end_point}
        if view_name is not None:
            data["view_name"] = view_name
        if line_style is not None:
            data["line_style"] = line_style
        response = await revit_post("/create_detail_line/", data, ctx)
        return format_response(response)
