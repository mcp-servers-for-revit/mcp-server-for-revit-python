# -*- coding: utf-8 -*-
"""Annotation MCP tools (wall tagging)."""

from typing import Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_annotation_tools(mcp, revit_get, revit_post):
    """Register annotation tools."""

    @mcp.tool()
    async def tag_walls(
        tag_family_name: Optional[str] = None,
        tag_type_name: Optional[str] = None,
        leader: bool = False,
        orientation: str = "horizontal",
        ctx: Context = None,
    ) -> str:
        """
        Tag every wall visible in the currently active plan view.

        Requires the active view to be a Floor Plan, Ceiling Plan, Engineering
        Plan, or Area Plan — fails on 3D / Elevation / Section views.

        Args:
            tag_family_name: Wall-tag family name. Optional; defaults to the
                             first available wall tag family in the project.
                             Get the full list of available tag families from
                             list_families with category_filter="Wall Tags".
            tag_type_name: Specific tag type within the family. Optional.
            leader: If True, places each tag with a leader line.
            orientation: "horizontal" (default) or "vertical".

        Returns tagged_count and (up to 50) wall-id → tag-id pairs.
        """
        payload = {
            "leader": bool(leader),
            "orientation": orientation,
        }
        if tag_family_name:
            payload["tag_family_name"] = tag_family_name
        if tag_type_name:
            payload["tag_type_name"] = tag_type_name
        response = await revit_post("/tag_walls/", payload, ctx)
        return format_response(response)
