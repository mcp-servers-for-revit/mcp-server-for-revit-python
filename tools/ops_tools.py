# -*- coding: utf-8 -*-
"""Operations / analysis tools ported from tools/*.ts + a smart schedule generator."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_ops_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def operate_element(
        element_ids: List[int],
        action: str,
        color_value: List[int] = None,
        transparency_value: int = 50,
        ctx: Context = None,
    ) -> str:
        """
        Operate on elements. action: Select | Hide | Unhide | Isolate | ResetIsolate |
        SetColor | SetTransparency | Delete | Highlight.
        color_value=[r,g,b] for SetColor; transparency_value 0-100 for SetTransparency.
        """
        data = {"elementIds": element_ids, "action": action,
                "colorValue": color_value or [255, 0, 0], "transparencyValue": transparency_value}
        return format_response(await revit_post("/operate_element/", data, ctx))

    @mcp.tool()
    async def tag_all_rooms(use_leader: bool = False, ctx: Context = None) -> str:
        """Tag all rooms in the active view at their centers (name + number)."""
        return format_response(await revit_post("/tag_all_rooms/", {"useLeader": use_leader}, ctx))

    @mcp.tool()
    async def analyze_model_statistics(ctx: Context = None) -> str:
        """Model statistics: instance counts by category, total instances, total types."""
        return format_response(await revit_get("/analyze_model_statistics/", ctx))

    @mcp.tool()
    async def ai_element_filter(
        category: str = None,
        parameter: str = None,
        operator: str = "contains",
        value: str = None,
        limit: int = 300,
        ctx: Context = None,
    ) -> str:
        """
        Filter elements by criteria. operator: contains | equals | gt | lt.
        e.g. category='OST_StructuralFraming', parameter='Generic Size', operator='contains', value='W16'.
        """
        data = {"category": category, "parameter": parameter, "operator": operator,
                "value": value, "limit": limit}
        return format_response(await revit_post("/ai_element_filter/", data, ctx))

    @mcp.tool()
    async def create_schedule(
        category: str, name: str = None, fields: List[str] = None, ctx: Context = None
    ) -> str:
        """
        Create a schedule for a category with chosen fields (including KPFF shared parameters).
        e.g. category='OST_StructuralColumns', fields=['Type','Generic Size','Comments'].
        """
        data = {"category": category, "name": name, "fields": fields or []}
        return format_response(await revit_post("/create_schedule/", data, ctx))
