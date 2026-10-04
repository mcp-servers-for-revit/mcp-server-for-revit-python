# -*- coding: utf-8 -*-
"""Selection MCP tools."""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_selection_tools(mcp, revit_get):
    """Register selection-management tools."""

    @mcp.tool()
    async def get_selected_elements(ctx: Context) -> str:
        """
        Get information about elements currently selected by the user in Revit.

        Returns id, name, category, type_id, type_name, and level_id (if applicable)
        for each selected element. If nothing is selected, returns count=0.

        Useful as the first step in any "look at what the user is pointing at then
        do X" workflow.
        """
        response = await revit_get("/selection/", ctx)
        return format_response(response)
