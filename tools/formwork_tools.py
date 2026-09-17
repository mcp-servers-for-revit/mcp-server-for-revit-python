# -*- coding: utf-8 -*-
"""Formwork (encofrado) automation tools"""

from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response


def register_formwork_tools(mcp, revit_get, revit_post):
    """Register formwork-related tools"""

    @mcp.tool()
    async def generate_formwork(
        scope: str = "model",
        element_ids: Optional[List[int]] = None,
        categories: Optional[List[str]] = None,
        material_filter: str = "",
        panel_thickness_mm: float = 18.0,
        contact_tolerance_mm: float = 5.0,
        exclude_top_faces: bool = True,
        exclude_foundation_bottom: bool = True,
        create_geometry: bool = True,
        write_quantities: bool = True,
        dry_run: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Generate structural formwork (encofrado) for concrete columns, beams,
        slabs, walls and foundations in the Revit model.

        Avoids double-counting formwork area on faces where two structural
        elements touch (e.g. a beam-column joint) by detecting contact
        between neighboring elements, and skips top faces (pour openings)
        and the underside of foundations (resting on ground/blinding) by
        default.

        Use dry_run=True to get a quantity/area report without creating any
        geometry or writing any parameters, to preview before committing.

        `categories` accepts any of: "foundations", "walls", "columns",
        "beams", "slabs" (default: all of them).

        Set scope="selection" and pass `element_ids` to restrict the run to
        specific elements instead of the whole model.
        """
        data = {
            "scope": scope,
            "material_filter": material_filter,
            "panel_thickness_mm": panel_thickness_mm,
            "contact_tolerance_mm": contact_tolerance_mm,
            "exclude_top_faces": exclude_top_faces,
            "exclude_foundation_bottom": exclude_foundation_bottom,
            "create_geometry": create_geometry,
            "write_quantities": write_quantities,
            "dry_run": dry_run,
        }
        if categories:
            data["categories"] = categories
        if element_ids:
            data["element_ids"] = element_ids

        response = await revit_post("/generate_formwork/", data, ctx, timeout=120.0)
        return format_response(response)
