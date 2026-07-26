# -*- coding: utf-8 -*-
"""Structural framing tools (grids, levels, beams/joists, beam systems, tags, sheets)."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_structure_tools(mcp, revit_get, revit_post):
    """Register structural-framing tools with the MCP server."""

    @mcp.tool()
    async def create_grids(grids: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Create multiple grids in one call. Each grid is a straight line at z=0.
        grids: [{"name": "A", "x0": 0, "y0": -5, "x1": 0, "y1": 120}, ...]  (feet)
        """
        return format_response(await revit_post("/create_grids/", {"grids": grids}, ctx))

    @mcp.tool()
    async def configure_levels(levels: List[Dict[str, Any]], ctx: Context = None) -> str:
        """
        Set elevation and/or rename existing levels.
        levels: [{"name": "Level 2", "elevation": 16.583, "new_name": "2nd Level"}, ...]  (feet)
        """
        return format_response(await revit_post("/configure_levels/", {"levels": levels}, ctx))

    @mcp.tool()
    async def place_framing(
        members: List[Dict[str, Any]], level: str = None, ctx: Context = None
    ) -> str:
        """
        Batch-place line-based structural framing (beams or joists) on a level.
        members: [{"family": "5 W Shapes", "type": "W16x26", "x0":0,"y0":0,"x1":20,"y1":0,
                   "level": "2nd Level", "z_justification": "top", "z_offset": 0.0}, ...]
        `level` sets a default reference level for members that omit their own.
        z_justification: top|center|bottom|origin. Coordinates in feet.
        """
        data = {"members": members}
        if level:
            data["level"] = level
        return format_response(await revit_post("/place_framing/", data, ctx))

    @mcp.tool()
    async def create_beam_system(
        level: str,
        family: str,
        type: str,
        rect: Dict[str, float],
        direction: Dict[str, float] = None,
        layout: Dict[str, Any] = None,
        z_justification: str = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a structural beam system over a rectangular bay.
        rect: {"xmin":..,"xmax":..,"ymin":..,"ymax":..} (feet)
        direction: {"x":0,"y":1} (beam direction; default N-S)
        layout: {"rule":"fixed_number","value":11} or {"rule":"fixed_distance","value":6.0}
        z_justification: top|center|bottom (e.g. 'bottom' so joist seats bear on supporting beams)
        """
        data = {
            "level": level, "family": family, "type": type, "rect": rect,
            "direction": direction or {"x": 0, "y": 1},
            "layout": layout or {"rule": "fixed_number", "value": 2},
        }
        if z_justification:
            data["z_justification"] = z_justification
        return format_response(await revit_post("/create_beam_system/", data, ctx))

    @mcp.tool()
    async def tag_all_framing(
        view_name: str,
        tag_family: str = "KPFF_Tag_Framing",
        tag_type: str = "Type",
        level: str = None,
        include_columns: bool = False,
        column_tag_family: str = "KPFF_Tag_Column",
        column_tag_type: str = "Type Name",
        ctx: Context = None,
    ) -> str:
        """
        Tag every structural framing member in a plan view (optionally columns too).
        `level` restricts tagging to members on that reference level.
        """
        data = {
            "view_name": view_name, "tag_family": tag_family, "tag_type": tag_type,
            "include_columns": include_columns,
            "column_tag_family": column_tag_family, "column_tag_type": column_tag_type,
        }
        if level:
            data["level"] = level
        return format_response(await revit_post("/tag_all_framing/", data, ctx))

    @mcp.tool()
    async def create_sheet(
        title_block_family: str,
        title_block_type: str,
        sheet_number: str,
        sheet_name: str,
        view_name: str = None,
        x: float = 1.25,
        y: float = 1.0,
        ctx: Context = None,
    ) -> str:
        """
        Create a sheet with a title block and optionally place a view on it.
        x, y are the viewport center on the sheet, in feet.
        """
        data = {
            "title_block_family": title_block_family, "title_block_type": title_block_type,
            "sheet_number": sheet_number, "sheet_name": sheet_name,
            "view_name": view_name, "x": x, "y": y,
        }
        return format_response(await revit_post("/create_sheet/", data, ctx))

    @mcp.tool()
    async def set_parameters_bulk(
        elements: List[Dict[str, Any]], ctx: Context = None
    ) -> str:
        """
        Set parameters (including KPFF shared parameters) on many elements at once.
        elements: [{"id": 12345, "params": {"Generic Size": "W16x26", "Number of Studs": 20}}, ...]
        Drives KPFF tag/schedule data (WF Wt, Framing, Joist Elevation Parameters, etc.).
        """
        return format_response(await revit_post("/set_parameters_bulk/", {"elements": elements}, ctx))

    @mcp.tool()
    async def tag_framing_standard(
        view_name: str,
        tag_family: str = "KPFF_Tag_Framing",
        tag_type: str = "Standard - T/Elevation",
        level: str = None,
        ctx: Context = None,
    ) -> str:
        """
        Tag all framing in a view with the KPFF standard framing tag, oriented parallel to
        each member (Size [studs] C{camber} (drop) T/STL {elev}). Optionally restrict to a level.
        """
        data = {"view_name": view_name, "tag_family": tag_family, "tag_type": tag_type}
        if level:
            data["level"] = level
        return format_response(await revit_post("/tag_framing_standard/", data, ctx))

    @mcp.tool()
    async def create_slab_callout(
        view_name: str,
        x: float,
        y: float,
        t_slab: str = "0'-0\"",
        nw_thickness: str = "3\"",
        deck: str = "2VLI20",
        total_thickness: str = "5\"",
        reinf: str = "#4@12\" OC",
        text: str = None,
        ctx: Context = None,
    ) -> str:
        """
        Place the KPFF composite-floor slab callout as a text note at (x,y) in a view.
        Defaults match the KPFF standard: T/SLAB {t_slab} / {nw} NORMAL-WEIGHT OVER {deck}
        GALV COMPOSITE FLOOR DECK ({total} TOTAL THICKNESS) REINF w/ {reinf} (TYP-UNO).
        Pass `text` to fully override.
        """
        data = {"view_name": view_name, "x": x, "y": y, "t_slab": t_slab,
                "nw_thickness": nw_thickness, "deck": deck,
                "total_thickness": total_thickness, "reinf": reinf}
        if text:
            data["text"] = text
        return format_response(await revit_post("/create_slab_callout/", data, ctx))
