# -*- coding: utf-8 -*-
"""Element management tools"""

from typing import Dict, Any, List
from mcp.server.fastmcp import Context
from .utils import format_response


def register_element_tools(mcp, revit_get, revit_post):
    """Register element management tools"""

    @mcp.tool()
    async def modify_element(
        element_id: int,
        properties: Dict[str, Any],
        ctx: Context = None,
    ) -> str:
        """
        Modify instance parameters of an existing element already in the Revit model.

        Args:
            element_id: The ID of the element to modify.
            properties: Dict of parameter_name -> new_value to set on the element
                (e.g. {"Mark": "A2", "Comments": "Updated via MCP"}).
        """
        data = {"element_id": element_id, "properties": properties}
        response = await revit_post("/modify_element/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def delete_elements(
        element_ids: List[int],
        ctx: Context = None,
    ) -> str:
        """
        Delete one or more elements from the Revit model.

        This modifies the model and cannot be undone through this API (only
        through Revit's own Undo command). Deleting an element may cascade to
        its dependents (e.g. deleting a wall also deletes its inserts).

        Args:
            element_ids: List of element IDs to delete.
        """
        data = {"element_ids": element_ids}
        response = await revit_post("/delete_elements/", data, ctx)
        return format_response(response)
