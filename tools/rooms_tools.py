# -*- coding: utf-8 -*-
"""Room-creation MCP tools."""

from typing import Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_rooms_tools(mcp, revit_get, revit_post):
    """Register room-creation tools."""

    @mcp.tool()
    async def create_room(
        x_mm: float,
        y_mm: float,
        level_id: Optional[int] = None,
        elevation_mm: Optional[float] = None,
        name: str = "Room",
        number: Optional[str] = None,
        upper_limit_id: Optional[int] = None,
        limit_offset_mm: Optional[float] = None,
        base_offset_mm: Optional[float] = 0,
        department: Optional[str] = None,
        comments: Optional[str] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a Room element at a 2D point on a level.

        The point must lie inside an area enclosed by room-bounding elements
        (walls or room separators); otherwise Revit refuses to create the room
        and the tool returns status='not_in_enclosed_area'.

        Args:
            x_mm: Room location X in millimetres.
            y_mm: Room location Y in millimetres.
            level_id: Optional explicit Level element id. Takes precedence over
                      elevation_mm if both are provided.
            elevation_mm: Optional elevation in mm — picks the nearest level.
                          Used when level_id is not provided.
            name: Room name (e.g. "Office", "Bathroom"). Default "Room".
            number: Optional room number. If blank, auto-generated from the
                    next available integer. If provided but already taken,
                    the number is made unique (e.g. "101" -> "102", or
                    "101A" if numeric increment exhausted).
            upper_limit_id: Optional Level id for the room's upper limit.
            limit_offset_mm: Optional offset above the upper limit, in mm.
            base_offset_mm: Optional offset above the base level, in mm.
                            Default 0.
            department: Optional Department parameter value.
            comments: Optional Comments parameter value.

        If both level_id and elevation_mm are omitted, the lowest-elevation
        level in the project is used.

        Returns the created room's id, unique_id, assigned name/number (may
        differ from requested if deduped), level info, area (m² and ft²),
        and perimeter (m and ft).

        Single Ctrl+Z in Revit reverts the creation atomically.
        """
        payload = {
            "x_mm": x_mm,
            "y_mm": y_mm,
            "level_id": level_id,
            "elevation_mm": elevation_mm,
            "name": name,
            "number": number,
            "upper_limit_id": upper_limit_id,
            "limit_offset_mm": limit_offset_mm,
            "base_offset_mm": base_offset_mm,
            "department": department,
            "comments": comments,
        }
        response = await revit_post("/create_room/", payload, ctx)
        return format_response(response)
