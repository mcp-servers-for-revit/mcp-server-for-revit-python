# -*- coding: utf-8 -*-
"""Annotation tools"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_annotation_tools(mcp, revit_post):
    """Register annotation tools"""

    @mcp.tool()
    async def tag_walls(
        tag_type_name: str = None,
        ctx: Context = None,
    ) -> str:
        """Tag all untagged walls visible in the current Revit view.

        Args:
            tag_type_name: Optional substring to match a specific wall tag
                type name. If omitted, the first available wall tag type
                in the project is used.
        """
        data = {"tag_type_name": tag_type_name}
        response = await revit_post("/tag_walls/", data, ctx)
        return format_response(response)
