# -*- coding: utf-8 -*-
"""Room-tagging MCP tools."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_room_annotation_tools(mcp, revit_get, revit_post):
    """Register the bulk room-tagging tool."""

    @mcp.tool()
    async def tag_rooms(
        room_ids: Optional[List[int]] = None,
        tag_family_name: Optional[str] = None,
        tag_type_name: Optional[str] = None,
        tag_type_id: Optional[int] = None,
        leader: bool = False,
        view_id: Optional[int] = None,
        auto_switch_view: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Bulk-place RoomTag elements over rooms in a plan view.

        This is the room equivalent of `tag_walls`. By default it tags every
        room visible in the active plan view; with `room_ids` it tags only
        the specified rooms (auto-switching the active view to a matching
        FloorPlan on the rooms' level if needed and `auto_switch_view=True`).

        Skip rules:
            - Rooms with Area <= 0 (unplaced)        -> counted in skipped_unplaced
            - Rooms that already have a tag in view  -> counted in skipped_existing

        Args:
            room_ids: Optional list of Room ElementIds to tag. When omitted,
                every placed room visible in the target view is tagged.
            tag_family_name: Room-tag family name (e.g. "Room Tag"). Optional;
                defaults to the first RoomTag family found in the project.
            tag_type_name: Specific tag type within the family. Optional.
            tag_type_id: Explicit RoomTag FamilySymbol ElementId. Overrides
                name-based lookup. Useful when the tag type's name collides
                with another family's type.
            leader: When True, places each tag with a leader line.
            view_id: Optional View ElementId to target. When omitted, uses
                `uidoc.ActiveView`. Either way, the view must be a plan view
                bound to the rooms' level — see `auto_switch_view`.
            auto_switch_view: When True (default), if the target view isn't
                a plan view or isn't on the rooms' level, automatically
                switches the active view to a matching FloorPlan view. When
                False, the call fails with `view_not_supported` instead.

        Returns a JSON-encoded breakdown:
            {
                "status": "success",
                "view_id": ..., "view_name": ..., "view_switched": false,
                "previous_view_name": null,
                "tag_family": "Room Tag", "tag_type": "Standard",
                "tag_type_id": ...,
                "total_rooms": N,
                "tagged_count": N,
                "skipped_existing": N,
                "skipped_unplaced": N,
                "tags": [
                    {"tag_id": ..., "room_id": ..., "room_name": "Office",
                     "room_number": "101",
                     "location_mm": {"x": ..., "y": ..., "z": ...}}
                ],
                "tags_truncated": false,   // true when more than 100 tags placed
                "errors": [...],
                "invalid_room_ids": [...]
            }

        Status values for recoverable failures:
            - 'view_not_supported'  : view isn't a plan view on the rooms'
                                      level and auto_switch_view is False
            - 'no_room_tag_family'  : no RoomTag FamilySymbol in the project
            - 'view_not_found'      : explicit view_id was invalid
            - 'no_rooms_in_view'    : view-scoped scan returned 0 rooms

        Single Ctrl+Z in Revit reverts the entire batch atomically.

        Duplicate-room-number warnings raised by Revit during tag placement
        are silently suppressed (matches Sparx behavior — these are usually
        noise from rooms across linked models).
        """
        payload = {
            "leader": bool(leader),
            "auto_switch_view": bool(auto_switch_view),
        }
        if room_ids is not None:
            payload["room_ids"] = list(room_ids)
        if tag_family_name:
            payload["tag_family_name"] = tag_family_name
        if tag_type_name:
            payload["tag_type_name"] = tag_type_name
        if tag_type_id is not None:
            payload["tag_type_id"] = tag_type_id
        if view_id is not None:
            payload["view_id"] = view_id
        response = await revit_post("/tag_rooms/", payload, ctx)
        return format_response(response)
