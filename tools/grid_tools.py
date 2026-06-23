# -*- coding: utf-8 -*-
"""Grid editing tools"""

from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response


def register_grid_tools(mcp, revit_post):
    """Register grid-related tools"""

    @mcp.tool()
    async def crop_grids_to_view(
        view_id: Optional[int] = None,
        grid_ids: Optional[List[int]] = None,
        margin_mm: float = 0.0,
        ctx: Context = None,
    ) -> str:
        """
        Trim grid lines' view-specific extents so they stop exactly at the
        view's crop region boundary, instead of extending past it.

        If view_id is omitted, the active view is used. If grid_ids is
        omitted, the current selection is used, falling back to every grid
        visible in the view.

        margin_mm: positive extends the trimmed grid line slightly past the
        crop edge, negative insets it from the edge. Default 0 (exact cut).
        """
        data = {
            "view_id": view_id,
            "grid_ids": grid_ids,
            "margin_mm": margin_mm,
        }
        response = await revit_post("/crop_grids_to_view/", data, ctx)
        return format_response(response)
