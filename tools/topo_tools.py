# -*- coding: utf-8 -*-
"""Toposolid editing tools"""

from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response


def register_topo_tools(mcp, revit_post):
    """Register toposolid-related tools"""

    @mcp.tool()
    async def align_toposolid_points(
        toposolid_ids: Optional[List[int]] = None,
        line_ids: Optional[List[int]] = None,
        tolerance_mm: float = 50.0,
        target_height_m: Optional[float] = None,
        height_tolerance_m: float = 0.01,
        ctx: Context = None,
    ) -> str:
        """
        Snap toposolid edit points that are within `tolerance_mm` of one or more
        detail/model lines onto those lines, in plan (X/Y) only. Point elevation
        (Z) is never changed.

        If target_height_m is given, only points whose elevation is within
        height_tolerance_m of that value (default 1cm window) are considered.

        If toposolid_ids and/or line_ids are omitted, the current Revit selection
        is used to fill in whichever side is missing (toposolids and lines can be
        selected together).
        """
        data = {
            "toposolid_ids": toposolid_ids,
            "line_ids": line_ids,
            "tolerance_mm": tolerance_mm,
            "target_height_m": target_height_m,
            "height_tolerance_m": height_tolerance_m,
        }
        response = await revit_post("/align_toposolid_points/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def create_toposolid_subdivision(
        toposolid_id: Optional[int] = None,
        filled_region_ids: Optional[List[int]] = None,
        type_name: Optional[str] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a Toposolid subdivision shaped by one or more Filled Region
        boundaries. One subdivision is created per filled region.

        If type_name is omitted, each subdivision keeps the host toposolid's
        type/material. If given, the subdivision is created with that
        toposolid type instead.

        If toposolid_id and/or filled_region_ids are omitted, the current
        Revit selection is used to fill in whichever side is missing (select
        the host toposolid plus one or more filled regions together).
        """
        data = {
            "toposolid_id": toposolid_id,
            "filled_region_ids": filled_region_ids,
            "type_name": type_name,
        }
        response = await revit_post("/create_toposolid_subdivision/", data, ctx)
        return format_response(response)
