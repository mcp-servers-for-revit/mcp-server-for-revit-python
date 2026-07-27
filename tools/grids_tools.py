# -*- coding: utf-8 -*-
"""Grid-generation MCP tools."""

from typing import Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_grids_tools(mcp, revit_get, revit_post):
    """Register grid-generation tools."""

    @mcp.tool()
    async def create_grids_from_walls(
        tolerance_mm: float = 10.0,
        padding_mm: float = 3000.0,
        start_vertical_number: int = 1,
        start_horizontal_letter: str = "A",
        vertical_prefix: str = "",
        horizontal_prefix: str = "",
        dry_run: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Generate a project grid system from existing wall centerlines.

        Walks every wall in the active document; for each axis-aligned wall
        (vertical or horizontal in plan), takes its centerline X (vertical
        walls) or Y (horizontal walls) and emits a grid line there. Walls
        within `tolerance_mm` of an existing centerline collapse into the
        same grid (averaged position).

        - Vertical grids run along the Y axis (constant X), numbered "1",
          "2", "3", ... from the LEFT (smallest X)
        - Horizontal grids run along the X axis (constant Y), lettered "A",
          "B", "C", ..., "AA", "AB", ... from the SOUTH (smallest Y)

        All grids are created in a SINGLE transaction so the entire grid
        system reverts on a single Ctrl+Z in Revit.

        Args:
            tolerance_mm: Walls whose centerlines are within this distance
                          on the same axis collapse into one grid. Default 10mm.
            padding_mm: Each grid line extends this distance beyond the
                        building bounding box on both ends (so bubbles sit
                        outside the building). Default 3000mm.
            start_vertical_number: First number for vertical grids.
                                   Default 1 → grids "1", "2", "3", ...
            start_horizontal_letter: First letter for horizontal grids.
                                     Default "A" → grids "A", "B", "C", ...
                                     Use "G" to start at "G".
            vertical_prefix: Optional prefix on each vertical grid name.
                             E.g. "V-" → grids "V-1", "V-2", ...
            horizontal_prefix: Optional prefix on each horizontal grid name.
                               E.g. "H-" → grids "H-A", "H-B", ...
            dry_run: If True, report what would be created without modifying
                     the model. Safe preview before committing.

        Returns counts + per-grid (name, coordinate, element ID) lists.
        Fails cleanly with a descriptive status if there are no walls,
        no axis-aligned walls, or grid-name collisions with existing grids.

        Curved walls and diagonal walls are reported in the response but
        do not contribute grid lines (axis-aligned only).
        """
        payload = {
            "tolerance_mm": tolerance_mm,
            "padding_mm": padding_mm,
            "start_vertical_number": start_vertical_number,
            "start_horizontal_letter": start_horizontal_letter,
            "vertical_prefix": vertical_prefix,
            "horizontal_prefix": horizontal_prefix,
            "dry_run": dry_run,
        }
        response = await revit_post("/create_grids_from_walls/", payload, ctx)
        return format_response(response)
