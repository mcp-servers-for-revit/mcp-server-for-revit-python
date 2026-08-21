# -*- coding: utf-8 -*-
"""Text-to-plan floor plan builder tools (Maket-style)"""

from mcp.server.fastmcp import Context
from typing import List, Dict, Optional
from .utils import format_response


def register_text_to_plan_tools(mcp, revit_get, revit_post):
    """Register text-to-plan floor plan builder tools"""

    @mcp.tool()
    async def generate_floor_plan(
        boundary_width: float,
        boundary_depth: float,
        rooms: List[Dict],
        level_name: str = "Level 1",
        corridor_width: float = 5.0,
        wall_height: float = 10.0,
        lot_width: Optional[float] = None,
        lot_depth: Optional[float] = None,
        setback_front: float = 0.0,
        setback_side: float = 0.0,
        setback_rear: float = 0.0,
        exterior_wall_type: str = None,
        interior_wall_type: str = None,
        door_type_name: str = None,
        ctx: Context = None,
    ) -> str:
        """
        Generate a real floor plan (walls, rooms, doors) from a room program.

        This tool builds geometry from a structured spec - it does not parse
        natural language. Parse the user's plain-English brief yourself first
        (e.g. "3-bed ranch, open kitchen/living, one bath, 40x35 footprint")
        into the `rooms` list before calling this.

        Args:
            boundary_width: Buildable footprint width in feet.
            boundary_depth: Buildable footprint depth in feet.
            rooms: List of room dicts. Each needs "name" and either:
                - "width" + "depth" (exact-size walled room, e.g. bedrooms,
                  baths) - optionally "max_width"/"max_depth" for a size range,
                  and "qty" for multiple identical rooms.
                - "target_area" (open zone that fills leftover space, e.g. an
                  open kitchen/living area) - optionally "min_width".
                Optional on either kind: "category" (for grouping/coloring)
                and "adjacency" ("window"/"entry"/"core" preference).
            level_name: Level to build on; created at elevation 0 (or above
                the highest existing level) if it doesn't already exist.
            corridor_width: Width in feet of the circulation corridor.
            wall_height: Unconnected wall height in feet.
            lot_width, lot_depth: Optional overall lot dimensions, used only
                to check the building + setbacks fit (a warning, not enforced).
            setback_front, setback_side, setback_rear: Optional setbacks in
                feet, checked against lot_width/lot_depth if given.
            exterior_wall_type, interior_wall_type: Wall type names to build
                with. Falls back to the document's default wall type if
                omitted or not found.
            door_type_name: Door family type name. Falls back to the first
                loaded door type if omitted or not found.

        Returns a summary including any rooms that didn't fit, zoning
        warnings, and rule-of-thumb egress flags. This is a design aid, not
        a code-compliance review - always verify against the applicable code.
        """
        data = {
            "boundary": {"width": boundary_width, "depth": boundary_depth},
            "rooms": rooms,
            "level_name": level_name,
            "corridor_width": corridor_width,
            "wall_height": wall_height,
            "exterior_wall_type": exterior_wall_type,
            "interior_wall_type": interior_wall_type,
            "door_type_name": door_type_name,
        }
        if lot_width is not None and lot_depth is not None:
            data["lot"] = {"width": lot_width, "depth": lot_depth}
            data["setback"] = {"front": setback_front, "side": setback_side, "rear": setback_rear}

        response = await revit_post("/generate_floor_plan/", data, ctx)
        return format_response(response)
