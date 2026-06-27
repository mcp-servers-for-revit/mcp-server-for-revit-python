# -*- coding: utf-8 -*-
"""View management tools — create views and set active view"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_view_management_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register view management tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def create_view(
        view_type: str,
        name: str,
        level_name: str = None,
        section_box: dict = None,
        ctx: Context = None,
    ) -> str:
        """Create a new view in the Revit model.

        Supports floor plans, ceiling plans, sections, elevations, and 3D views.
        Floor plans and ceiling plans require a level name. Sections require a
        section box definition with origin, direction, and dimensions.

        Args:
            view_type: Type of view — "floor_plan", "ceiling_plan", "section",
                "elevation", or "3d"
            name: Display name for the new view
            level_name: Required for floor_plan and ceiling_plan — the level to show
            section_box: Required for section — defines the cut plane:
                - origin (dict): {"x", "y", "z"} center point in mm
                - direction (dict): {"x", "y", "z"} view direction vector
                - up (dict): {"x", "y", "z"} up direction vector
                - width (float): Section width in mm
                - height (float): Section height in mm
                - depth (float): Section depth in mm
            ctx: MCP context for logging
        """
        data = {"view_type": view_type, "name": name}
        if level_name is not None:
            data["level_name"] = level_name
        if section_box is not None:
            data["section_box"] = section_box
        response = await revit_post("/create_view/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def set_active_view(
        view_name: str,
        ctx: Context = None,
    ) -> str:
        """Switch the active view in Revit to the specified view.

        Changes which view is displayed in the Revit UI. Use list_revit_views
        first to find available view names.

        Args:
            view_name: Name of the view to activate
            ctx: MCP context for logging
        """
        data = {"view_name": view_name}
        response = await revit_post("/set_active_view/", data, ctx)
        return format_response(response)
