# -*- coding: utf-8 -*-
"""Element creation tools ported from tools/create_*.ts. Units are FEET."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_create_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def create_point_based_element(data: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Batch-create point-based family instances (doors, windows, furniture, equipment).
        data: [{"name":"...", "family":"...", "type":"..." (or "typeId":int),
                "locationPoint":{"x":..,"y":..,"z":..}, "level":"Level 1", "rotation":0}]  (feet, deg)
        """
        return format_response(await revit_post("/create_point_based_element/", {"data": data}, ctx))

    @mcp.tool()
    async def create_line_based_element(data: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Batch-create line-based elements (walls via WallType, or framing via a FamilySymbol).
        data: [{"name":"...", "family":"...", "type":"..." (or "typeId":int),
                "locationLine":{"p0":{x,y,z},"p1":{x,y,z}}, "height":.., "baseOffset":.., "level":".."}]  (feet)
        """
        return format_response(await revit_post("/create_line_based_element/", {"data": data}, ctx))

    @mcp.tool()
    async def create_surface_based_element(data: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Batch-create floors from a boundary loop.
        data: [{"name":"...", "family":"...", "type":"..." (or "typeId":int),
                "boundary":{"outerLoop":[{"p0":{x,y,z},"p1":{x,y,z}}, ...]}, "level":"..", "baseOffset":..}]  (feet)
        """
        return format_response(await revit_post("/create_surface_based_element/", {"data": data}, ctx))

    @mcp.tool()
    async def create_room(data: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Create rooms at points. data: [{"level":"Level 1","x":..,"y":..,"name":"OFFICE","number":"200"}]  (feet)
        """
        return format_response(await revit_post("/create_room/", {"data": data}, ctx))
