# -*- coding: utf-8 -*-
"""View-related tools for capturing and listing Revit views"""

from mcp.server.fastmcp import Context, Image
from typing import Union
from .utils import format_response


def register_view_tools(mcp, revit_get, revit_post, revit_image):
    """Register view-related tools"""

    @mcp.tool()
    async def get_revit_view(view_name: str, ctx: Context = None) -> Union[Image, str]:
        """Export a specific Revit view as an image"""
        return await revit_image(f"/get_view/{view_name}", ctx)

    @mcp.tool()
    async def list_revit_views(ctx: Context = None) -> str:
        """Get a list of all exportable views in the current Revit model"""
        response = await revit_get("/list_views/", ctx)
        return format_response(response)

    @mcp.tool()
    async def get_current_view_info(ctx: Context = None) -> str:
        """
        Get detailed information about the currently active view in Revit.

        Returns comprehensive information including:
        - View name, type, and ID
        - Scale and detail level
        - Crop box status
        - View family type
        - View discipline
        - Template status
        """
        if ctx:
            await ctx.info("Getting current view information...")
        response = await revit_get("/current_view_info/", ctx)
        return format_response(response)

    @mcp.tool()
    async def get_current_view_elements(ctx: Context = None) -> str:
        """
        Get all elements visible in the currently active view in Revit.

        Returns detailed information about each element including:
        - Element ID, name, and type
        - Category and category ID
        - Level information (if applicable)
        - Location information (point or curve)
        - Summary statistics grouped by category

        This is useful for understanding what elements are currently visible
        and analyzing the content of the active view.
        """
        if ctx:
            await ctx.info("Getting elements in current view...")
        response = await revit_get("/current_view_elements/", ctx)
        return format_response(response)
