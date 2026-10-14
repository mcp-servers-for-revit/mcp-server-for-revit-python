# -*- coding: utf-8 -*-
"""Room-data extraction MCP tools."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_room_data_tools(mcp, revit_get, revit_post):
    """Register the room-data export tool."""

    @mcp.tool()
    async def export_room_data(
        room_ids: Optional[List[int]] = None,
        level_id: Optional[int] = None,
        level_name: Optional[str] = None,
        view_id: Optional[int] = None,
        phase_id: Optional[int] = None,
        include_unplaced: bool = False,
        include_not_enclosed: bool = False,
        sort_by: str = "number",
        ctx: Context = None,
    ) -> str:
        """
        Export every Room in the project (or a filtered subset) as JSON.

        Returns the full parameter set for each room: name, number, level,
        phase, area (m²), volume (m³), perimeter (mm), unbounded height
        (mm), department, occupancy, comments, location_mm, bounding_box_mm.
        Each room is classified by `placement`: 'placed' / 'unplaced' /
        'not_enclosed'.

        This is the right tool for questions like:
            - "Give me a list of every room with area and number."
            - "Export rooms on Level 2 as a schedule."
            - "Which rooms are unplaced or not enclosed?"
            - "Total area of all bedrooms on the second floor."

        Args:
            room_ids: Optional explicit list of Room ElementIds. When
                provided, supersedes level/view/phase filters.
            level_id: Optional Level ElementId to restrict the export to.
            level_name: Optional Level name (alternate to level_id).
                Either works; level_id takes precedence if both supplied.
            view_id: Optional View ElementId — when provided, restricts
                rooms to those visible in this view AND uses this view for
                bounding-box clipping.
            phase_id: Optional Phase ElementId — restrict to rooms in this
                phase. Useful for renovation projects where rooms split
                across Existing / New Construction.
            include_unplaced: When True, rooms with no Location (Revit's
                "Not Placed" schedule bucket) are included. Default False
                (omitted from results, counted in `skipped_unplaced`).
            include_not_enclosed: When True, rooms with a Location but
                Area==0 (boundary doesn't form a closed loop) are
                included. Default False.
            sort_by: One of "number" (default; natural-numeric: 101, 101A,
                102), "name", "level", or "area" (descending).

        Returns a JSON-encoded breakdown:
            {
                "status": "success",
                "rooms": [
                    {
                        "id": 354978,
                        "unique_id": "...-00056abd",
                        "name": "Office",
                        "number": "101",
                        "placement": "placed",
                        "level_id": 311, "level_name": "Level 1",
                        "phase_id": ..., "phase_name": "New Construction",
                        "area_m2": 18.42, "volume_m3": 55.27,
                        "perimeter_mm": 17400.0,
                        "unbounded_height_mm": 3000.0,
                        "department": "Admin", "occupancy": "Office",
                        "comments": "...",
                        "location_mm": {"x": ..., "y": ..., "z": ...},
                        "bounding_box_mm": {"min": {...}, "max": {...}}
                    }
                ],
                "totals": {
                    "room_count": N, "area_m2": ..., "volume_m3": ...,
                    "placed_count": N, "unplaced_count": N,
                    "not_enclosed_count": N
                },
                "applied_filters": [...],
                "skipped_unplaced": N, "skipped_not_enclosed": N,
                "skipped_other_level": N, "skipped_other_phase": N,
                "invalid_room_ids": [...],
                "level_filter": "Level 1", "phase_filter": null,
                "view_filter": null, "sort_by": "number"
            }

        Status values for recoverable failures:
            - 'no_rooms_found'   : filter produced an empty set
            - 'level_not_found'  : explicit level_id or level_name invalid
            - 'phase_not_found'  : explicit phase_id invalid
            - 'view_not_found'   : explicit view_id invalid

        No transactions — pure read.
        """
        payload = {
            "include_unplaced": bool(include_unplaced),
            "include_not_enclosed": bool(include_not_enclosed),
            "sort_by": sort_by,
        }
        if room_ids is not None:
            payload["room_ids"] = list(room_ids)
        if level_id is not None:
            payload["level_id"] = level_id
        if level_name:
            payload["level_name"] = level_name
        if view_id is not None:
            payload["view_id"] = view_id
        if phase_id is not None:
            payload["phase_id"] = phase_id
        response = await revit_post("/export_room_data/", payload, ctx)
        return format_response(response)
