# -*- coding: utf-8 -*-
"""Dimension-creation MCP tools."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_dimensions_tools(mcp, revit_get, revit_post):
    """Register dimension-creation tools."""

    @mcp.tool()
    async def create_dimension(
        start_x_mm: float,
        start_y_mm: float,
        start_z_mm: float,
        end_x_mm: float,
        end_y_mm: float,
        end_z_mm: float,
        line_x_mm: Optional[float] = None,
        line_y_mm: Optional[float] = None,
        line_z_mm: Optional[float] = None,
        element_ids: Optional[List[int]] = None,
        view_id: Optional[int] = None,
        dimension_style_id: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a linear Dimension between two points in a view.

        The dimension's measurement axis is defined by (start -> end). The
        dimension LINE (where the dim text + witness lines sit) is anchored
        by `line_*_mm` if supplied, otherwise it is placed 1 ft (304.8 mm)
        perpendicular to the start->end direction from the midpoint, in the
        XY plane.

        If `element_ids` is supplied, the dimension is locked to geometric
        face references on those elements (the typical "dimension between
        two parallel walls" workflow). For each element, the planar face
        whose normal is most aligned with the dim direction is picked. Top
        and bottom horizontal faces (|normal.Z| > 0.9) are skipped — only
        vertical wall faces are useful in a plan dim.

        If `element_ids` is empty/omitted, the tool falls back to finding
        the nearest element in the view to each of start/end (within 5 ft)
        and locking onto the best face there.

        Args:
            start_x_mm, start_y_mm, start_z_mm: First measured point, in
                millimetres. Typically lies on or near the first element to
                dimension (e.g. the centerline of the first wall).
            end_x_mm, end_y_mm, end_z_mm: Second measured point, in mm.
                Defines the dim direction together with the start point.
            line_x_mm, line_y_mm, line_z_mm: Optional dim-line anchor in mm.
                If supplied, the dim line passes through this point parallel
                to the start->end direction. If omitted, defaults to a 1 ft
                perpendicular offset from the midpoint of start/end in XY.
            element_ids: Optional list of Revit ElementIds (e.g. wall IDs)
                to dimension between. Provide at least 2 for a clean
                measurement. Order doesn't matter — references are picked by
                face-normal alignment with the dim direction.
            view_id: Optional explicit View ElementId. Defaults to the
                currently active view in Revit. Must be a 2D view that
                supports dimensions (FloorPlan, Section, Elevation, etc.).
            dimension_style_id: Optional DimensionType ElementId to apply
                to the created dimension. Defaults to the view's current
                default style.

        Returns the created dimension's id, references_count, measured
        value (mm and ft), view info, and a diagnostics block showing
        which path the reference picker took and what it locked onto.

        Status values for recoverable failures:
            - 'no_references_found'        : zero refs picked
            - 'fewer_than_2_references'    : only 1 ref picked
            - 'create_failed'              : Revit refused the dimension
            - 'invalid_dim_line'           : line construction failed
            - 'view_not_found'             : view_id invalid or no active view

        Single Ctrl+Z in Revit reverts the creation atomically.
        """
        payload = {
            "start_x_mm": start_x_mm,
            "start_y_mm": start_y_mm,
            "start_z_mm": start_z_mm,
            "end_x_mm": end_x_mm,
            "end_y_mm": end_y_mm,
            "end_z_mm": end_z_mm,
            "line_x_mm": line_x_mm,
            "line_y_mm": line_y_mm,
            "line_z_mm": line_z_mm,
            "element_ids": element_ids,
            "view_id": view_id,
            "dimension_style_id": dimension_style_id,
        }
        response = await revit_post("/create_dimension/", payload, ctx)
        return format_response(response)
