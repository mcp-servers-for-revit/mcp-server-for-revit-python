# -*- coding: utf-8 -*-
"""Selection tools"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_selection_tools(mcp, revit_get):
    """Register selection-related tools"""

    @mcp.tool()
    async def get_selected_elements(ctx: Context = None) -> str:
        """Get information about the elements currently selected by the user in Revit"""
        response = await revit_get("/selected_elements/", ctx)
        return format_response(response)
