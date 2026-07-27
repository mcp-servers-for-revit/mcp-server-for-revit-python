# -*- coding: utf-8 -*-
"""Structured element-filter MCP tools (ported from Sparx AIElementFilter)."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_element_filter_tools(mcp, revit_get, revit_post):
    """Register the structured element-filter tool."""

    @mcp.tool()
    async def ai_element_filter(
        filter_category: Optional[str] = None,
        filter_element_type: Optional[str] = None,
        filter_family_symbol_id: Optional[int] = None,
        include_types: bool = False,
        include_instances: bool = True,
        filter_visible_in_current_view: bool = False,
        view_id: Optional[int] = None,
        bounding_box_min: Optional[List[float]] = None,
        bounding_box_max: Optional[List[float]] = None,
        name_contains: Optional[str] = None,
        max_elements: int = 100,
        ctx: Context = None,
    ) -> str:
        """
        Find Revit elements matching a structured multi-criteria filter and
        return rich per-element info.

        Filters are AND'd together. At least one filter is required (the tool
        will refuse a wide-open scan). The return payload shape depends on
        each element's kind — see "Returns" below.

        This is the right tool for questions like:
            - "Find every wall named 'Generic - 200mm'"
            - "Get all doors visible in the current view"
            - "List rooms on Level 2 with area / volume / perimeter"
            - "Find all family instances inside this bounding box"

        Args:
            filter_category: Optional BuiltInCategory name (with or without
                the OST_ prefix) to restrict the scan, e.g. "OST_Walls",
                "Walls", "OST_Doors". Unknown names error out as
                status='invalid_filter'.
            filter_element_type: Optional .NET class name under
                Autodesk.Revit.DB to restrict by class, e.g. "Wall",
                "FamilyInstance", "Floor". Both bare names and fully
                qualified names ("Autodesk.Revit.DB.Wall") are accepted.
            filter_family_symbol_id: Optional FamilySymbol ElementId to
                restrict to instances of one specific family type. Only
                meaningful when include_instances=true.
            include_types: When true, ElementType subclasses (WallType,
                FloorType, FamilySymbol, ...) are included.
            include_instances: When true (default), element instances are
                included. At least one of include_types / include_instances
                must be true.
            filter_visible_in_current_view: When true, restrict instances to
                what's visible in the user's active view. Mutually exclusive
                with view_id (view_id takes precedence when both are set).
            view_id: Optional explicit View ElementId. When supplied,
                instances are restricted to that view's visible set. Useful
                when you know the target view (e.g. from list_revit_views)
                without asking the user to focus it.
            bounding_box_min: Optional [x_mm, y_mm, z_mm] min corner of a
                3D bounding box. When supplied, only elements whose own
                bbox intersects this box are returned. Must be provided
                together with bounding_box_max.
            bounding_box_max: Optional [x_mm, y_mm, z_mm] max corner of the
                3D filter. Each axis must be >= the matching min axis.
            name_contains: Optional case-insensitive substring filter on
                Element.Name. Applied AFTER the Revit-side collector for
                element types that don't carry a queryable name.
            max_elements: Cap on returned element count (default 100). When
                the matched count exceeds the cap, the response's
                'truncated' field is true and matched_count > returned_count.
                Pass 0 for unlimited (use with care on big models).

        Returns a JSON-encoded payload:
            {
                "status": "success",
                "matched_count": 142,          // total before max_elements cap
                "returned_count": 100,         // actually returned (after cap)
                "truncated": true,
                "applied_filters": [           // diagnostics
                    "instances_only",
                    "category=OST_Walls",
                    "name_contains=Generic",
                    "max_elements=100"
                ],
                "view_source": "explicit_view_id" | "no_view_filter",
                "view_id": 12345 | null,
                "view_name": "Level 2 Floor Plan" | null,
                "elements": [
                    { id, unique_id, name, family_name, category,
                      built_in_category, element_class, ... kind-specific
                      fields like bounding_box, level, parameters,
                      volume_m3, area_m2, position_mm, ... }
                ]
            }

        Status values for recoverable failures:
            - 'invalid_filter' : validation failed (e.g. no filters supplied,
                                 bad category name, missing one of min/max,
                                 conflicting include_types+visible_in_view)
            - 'view_not_found' : view_id was supplied but doesn't resolve

        Per-kind output fields (in addition to the common header
        id / unique_id / name / family_name / category / built_in_category /
        element_class):

            HasMaterialQuantities (Wall, Floor, Column, Door, Window, ...)
                type_id, room_id, level, bounding_box,
                parameters: [{name:'thickness_mm'}, {name:'bbox_height_mm'}]

            ElementType
                family_name, parameters: dimensional params + thickness_mm

            Level / Grid
                bounding_box, level, plus elevation_mm (Level) or
                grid_line.start_mm/end_mm (Grid)

            Room / Area
                bounding_box, level, number, area_m2, perimeter_mm
                (Room also gets volume_m3)

            View
                bounding_box, view_type, scale, is_template, detail_level,
                associated_level (ViewPlan only), is_open, is_active

            TextNote / Dimension / IndependentTag / SpotDimension
                bounding_box, owner_view, position_mm
                (TextNote -> text_content; Dimension -> dimension_value)

            Group / RevitLinkInstance
                bounding_box, plus member_count + group_type (Group) or
                link_path + link_status + position_mm (RevitLinkInstance)

            Fallback (anything else)
                bounding_box

        No transactions — pure read.
        """
        payload = {
            "filter_category": filter_category,
            "filter_element_type": filter_element_type,
            "filter_family_symbol_id": filter_family_symbol_id,
            "include_types": include_types,
            "include_instances": include_instances,
            "filter_visible_in_current_view": filter_visible_in_current_view,
            "view_id": view_id,
            "bounding_box_min": bounding_box_min,
            "bounding_box_max": bounding_box_max,
            "name_contains": name_contains,
            "max_elements": max_elements,
        }
        response = await revit_post("/ai_element_filter/", payload, ctx)
        return format_response(response)
