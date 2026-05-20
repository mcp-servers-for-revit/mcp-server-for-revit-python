# -*- coding: utf-8 -*-
"""MEP-to-grid dimensioning MCP tool."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_mep_dimensions_tools(mcp, revit_get, revit_post):
    """Register the dimension_mep_to_grids tool."""

    @mcp.tool()
    async def dimension_mep_to_grids(
        view_id: Optional[int] = None,
        element_ids: Optional[List[int]] = None,
        categories: Optional[List[str]] = None,
        string_style: str = "continuous",
        grid_scope: str = "nearest",
        offset_mm: float = 2500.0,
        gap_mm: float = 1200.0,
        coordinate_tolerance_mm: float = 10.0,
        max_elements: int = 200,
        dimension_style_id: Optional[int] = None,
        dry_run: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Automatically dimension MEP elements (ducts, cable trays, pipes) to
        the project grid system in a plan view, laid out with clean spacing.

        This is the "locate the services against the grid" workflow from an
        MEP coordination drawing. Each MEP run is dimensioned to the grid
        lines that box it in:

        - A horizontal (East-West) run is located by its Y coordinate, so it
          is dimensioned against the HORIZONTAL grids with a VERTICAL
          dimension string.
        - A vertical (North-South) run is located by its X coordinate, so it
          is dimensioned against the VERTICAL grids with a HORIZONTAL string.
        - Vertical risers (a point in plan) and diagonal runs are skipped and
          reported.

        Witness references lock onto grid lines and MEP centerlines. A
        continuous string is one multi-segment dimension witnessing the
        chosen grids plus every MEP centerline in coordinate order.

        Args:
            view_id: Optional plan-view ElementId. Defaults to the active
                view. Must be a plan view (FloorPlan / CeilingPlan /
                EngineeringPlan / AreaPlan).
            element_ids: Optional explicit list of MEP element ids to
                dimension. STRONGLY RECOMMENDED for real models -- a busy
                MEP view can hold hundreds of ducts and dimensioning them all
                is unreadable. When omitted, every MEP element of the
                requested categories visible in the view is used, subject to
                max_elements.
            categories: Which MEP categories to include when falling back to
                view-visible elements. Any of "ducts", "cable_trays",
                "pipes" (aliases like "duct", "OST_PipeCurves" also accepted).
                Defaults to all three. Ignored when element_ids is given.
            string_style: "continuous" (default) = ONE continuous
                multi-segment dimension string per orientation, witnessing
                the chosen grids and every MEP centerline. "individual" =
                one separate grid->run->grid dimension per MEP run, greedily
                lane-stacked to avoid overlaps.
            grid_scope: "nearest" (default) = only the grids immediately
                bracketing the MEP runs are witnessed. "all" = every grid of
                the relevant orientation visible in the view is witnessed.
            offset_mm: Distance the dimension line is placed clear of the
                dimensioned MEP geometry, in mm. Default 2500.
            gap_mm: Gap between stacked dimension strings (individual mode
                only), in mm. Default 1200.
            coordinate_tolerance_mm: MEP runs whose locating coordinate is
                within this distance collapse to a single witnessed
                coordinate; a run sitting on a grid line within this
                tolerance is dropped. Default 10.
            max_elements: Safety cap on the view-visible fallback. If more
                MEP elements than this are visible, the tool returns
                'too_many_elements' instead of producing an unreadable
                drawing. Default 200. Ignored when element_ids is supplied.
            dimension_style_id: Optional DimensionType ElementId to apply.
            dry_run: If true, report the planned strings and witnesses
                without modifying the model.

        Returns the created dimension ids with per-string segment values
        (mm), the grids/MEP counts, and diagnostics: skipped_mep (with
        reasons such as 'vertical_riser', 'diagonal_run',
        'coincides_with_grid'), warnings, and create_errors.

        Recoverable status values:
            - 'view_not_plan'        : target view is not a plan view
            - 'view_not_found'       : view_id invalid / no active view
            - 'no_grids'             : no axis-aligned grids in the view
            - 'no_mep_elements'      : no dimensionable MEP found
            - 'no_dimensionable_mep' : MEP found but all risers/diagonal
            - 'too_many_elements'    : view-visible count exceeds max_elements
            - 'nothing_created'      : nothing could be planned
            - 'create_failed'        : Revit refused every string

        All strings are created in one transaction -- a single Ctrl+Z in
        Revit reverts the whole set.
        """
        payload = {
            "view_id": view_id,
            "element_ids": element_ids,
            "categories": categories,
            "string_style": string_style,
            "grid_scope": grid_scope,
            "offset_mm": offset_mm,
            "gap_mm": gap_mm,
            "coordinate_tolerance_mm": coordinate_tolerance_mm,
            "max_elements": max_elements,
            "dimension_style_id": dimension_style_id,
            "dry_run": dry_run,
        }
        response = await revit_post("/dimension_mep_to_grids/", payload, ctx)
        return format_response(response)
