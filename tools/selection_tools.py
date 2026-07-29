# -*- coding: utf-8 -*-
"""Selection-related tools"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_selection_tools(mcp, revit_get, revit_post):
    """Register selection-related tools"""

    @mcp.tool()
    async def get_selected_elements(
        limit: int = 5000,
        include_levels: bool = False,
        include_location: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Get information about the elements currently selected by the user in Revit.

        Returns per element: element_id, name, category, category_id.
        Also returns category_counts (always for ALL selected elements, even if truncated).

        If nothing is selected in Revit, returns an empty list with a message.

        Args:
            limit: Maximum number of elements to return (default 5000).
            include_levels: Include level name and level_id per element. Default false.
            include_location: Include location geometry (point or curve). Default false.
        """
        if ctx:
            await ctx.info("Getting currently selected elements...")
        data = {
            "limit": limit,
            "include_levels": include_levels,
            "include_location": include_location,
        }
        response = await revit_post("/selected_elements/", data, ctx)
        return format_response(response)
