# -*- coding: utf-8 -*-
"""Element management tools: delete, modify and reset elements"""

from mcp.server.fastmcp import Context
from typing import Dict, Any, List
from .utils import format_response


def register_element_management_tools(mcp, revit_get, revit_post):
    """Register element management tools"""

    @mcp.tool()
    async def delete_elements(
        element_ids: List[int],
        ctx: Context = None,
    ) -> str:
        """Delete specified elements from the Revit model by their element IDs"""
        data = {"element_ids": element_ids}
        response = await revit_post("/delete_elements/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def modify_element(
        element_id: int,
        properties: Dict[str, Any],
        ctx: Context = None,
    ) -> str:
        """Modify instance parameters of an existing element in the Revit model

        Args:
            element_id: The ID of the element to modify.
            properties: Mapping of parameter name to new value.
        """
        data = {"element_id": element_id, "properties": properties}
        response = await revit_post("/modify_element/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def reset_model(
        confirm: bool = False,
        ctx: Context = None,
    ) -> str:
        """Delete elements created via MCP tools during this Revit session.

        Call with confirm=False (default) first to preview what would be
        deleted, then call again with confirm=True to actually delete them.
        Never touches elements that already existed in the model.
        """
        data = {"confirm": confirm}
        response = await revit_post("/reset_model/", data, ctx)
        return format_response(response)
