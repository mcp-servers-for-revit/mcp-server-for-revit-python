# -*- coding: utf-8 -*-
"""Annotation/misc tools ported from the mcp-servers-for-revit C# command set."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_annotation_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def create_dimensions(
        view_name: str = None,
        dimensions: List[Dict[str, Any]] = None,
        element_ids: List[int] = None,
        line: Dict[str, Any] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create linear dimensions referencing elements (e.g. grids).
        Provide either `dimensions` (a list of {element_ids, line}) or a single
        `element_ids` + `line`. line = {"p0":{"x","y","z"},"p1":{"x","y","z"}} (feet);
        the dimension string is placed along p0->p1.
        """
        data = {}
        if view_name:
            data["view_name"] = view_name
        if dimensions:
            data["dimensions"] = dimensions
        if element_ids:
            data["element_ids"] = element_ids
        if line:
            data["line"] = line
        return format_response(await revit_post("/create_dimensions/", data, ctx))

    @mcp.tool()
    async def create_levels(levels: List[Dict[str, Any]], ctx: Context = None) -> str:
        """Create new levels. levels=[{"name":"3rd Level","elevation":31.33}] (feet)."""
        return format_response(await revit_post("/create_levels/", {"levels": levels}, ctx))

    @mcp.tool()
    async def export_room_data(ctx: Context = None) -> str:
        """Export all rooms with name, number, area, level, and comments."""
        return format_response(await revit_get("/export_room_data/", ctx))

    @mcp.tool()
    async def say_hello(ctx: Context = None) -> str:
        """Connection test: greeting + active document/view info."""
        return format_response(await revit_get("/say_hello/", ctx))
