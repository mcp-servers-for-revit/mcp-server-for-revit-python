# -*- coding: utf-8 -*-
"""Tagging tools — tag elements with annotation symbols"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_tag_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register tag tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def tag_elements(
        element_ids: list[int],
        view_name: str = None,
        tag_type_name: str = None,
        add_leader: bool = False,
        orientation: str = "horizontal",
        offset: dict = None,
        ctx: Context = None,
    ) -> str:
        """Tag elements with annotation symbols in a view.

        Places tags on the specified elements. Tags display element properties
        like type name, mark, or room name/number. Works with walls, doors,
        windows, rooms, and other taggable categories.

        Args:
            element_ids: List of element IDs to tag
            view_name: View to place tags in (defaults to active view)
            tag_type_name: Tag family type name (auto-detects appropriate tag if omitted)
            add_leader: Show leader line from tag to element (default: false)
            orientation: Tag orientation — "horizontal" or "vertical" (default: "horizontal")
            offset: Tag offset from element center {"x": float, "y": float} in mm
            ctx: MCP context for logging
        """
        data = {
            "element_ids": element_ids,
            "add_leader": add_leader,
            "orientation": orientation,
        }
        if view_name is not None:
            data["view_name"] = view_name
        if tag_type_name is not None:
            data["tag_type_name"] = tag_type_name
        if offset is not None:
            data["offset"] = offset
        response = await revit_post("/tag_elements/", data, ctx)
        return format_response(response)
