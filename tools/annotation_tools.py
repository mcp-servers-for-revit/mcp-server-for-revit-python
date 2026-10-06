# -*- coding: utf-8 -*-
"""Annotation tools"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_annotation_tools(mcp, revit_get, revit_post):
    """Register annotation-related tools"""

    @mcp.tool()
    async def tag_walls(
        skip_tagged: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Tag all walls visible in the currently active Revit view.

        Requires a Wall Tag family to be loaded in the project, otherwise this
        will return an error asking you to load one.

        Args:
            skip_tagged: If true (default), walls that already have a tag in
                this view are skipped instead of being tagged again.
        """
        data = {"skip_tagged": skip_tagged}
        response = await revit_post("/tag_walls/", data, ctx)
        return format_response(response)
