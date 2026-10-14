# -*- coding: utf-8 -*-
"""Level-creation MCP tools."""

from typing import Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_levels_tools(mcp, revit_get, revit_post):
    """Register level-creation tools."""

    @mcp.tool()
    async def create_level(
        name: str,
        elevation_mm: float,
        create_floor_plan: bool = True,
        create_ceiling_plan: bool = False,
        view_name: Optional[str] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a level at a given elevation and (optionally) matching plan views.

        All elements (level + views) are created in a single transaction so
        one Ctrl+Z in Revit reverts the entire creation.

        Args:
            name: Level name as it should appear in Revit (e.g. "Level 3").
                  Must be unique in the project — call fails with
                  status=name_collision if a level with the same name exists.
            elevation_mm: Elevation in MILLIMETRES (positive = up from project
                          base). Internally converted to Revit's decimal feet.
            create_floor_plan: If True (default), also creates a Floor Plan
                               view linked to the new level.
            create_ceiling_plan: If True, also creates a Ceiling Plan view.
                                 Default False. When both plans are created,
                                 the ceiling plan name gets " - Ceiling"
                                 suffix to avoid duplicate-name collision.
            view_name: Custom name for the created view(s). Defaults to the
                       level name. Useful if you want the view to be named
                       differently from the level (e.g. level "L+3" with
                       view "Third Floor").

        Returns the new level + view element IDs on success.
        """
        payload = {
            "name": name,
            "elevation_mm": float(elevation_mm),
            "create_floor_plan": bool(create_floor_plan),
            "create_ceiling_plan": bool(create_ceiling_plan),
        }
        if view_name:
            payload["view_name"] = view_name
        response = await revit_post("/create_level/", payload, ctx)
        return format_response(response)
