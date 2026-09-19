# -*- coding: utf-8 -*-
"""Formwork (encofrado) automation tools"""

from mcp.server.fastmcp import Context
from typing import Dict, List, Optional
from .utils import format_response


def register_formwork_tools(mcp, revit_get, revit_post):
    """Register formwork-related tools"""

    @mcp.tool()
    async def generate_formwork(
        scope: str = "model",
        element_ids: Optional[List[int]] = None,
        categories: Optional[List[str]] = None,
        material_filter: str = "",
        material_filters: Optional[Dict[str, str]] = None,
        panel_thickness_mm: float = 18.0,
        contact_tolerance_mm: float = 5.0,
        exclude_top_faces: bool = True,
        exclude_foundation_bottom: bool = True,
        create_geometry: bool = True,
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
        geometry, to preview before committing. Quantity takeoff is always
        returned in the report; build a native Revit schedule off the
        created panels' EF_Area_m2 / EF_Categoria_Origen parameters if you
        need a table in the model.

        `categories` accepts any of: "foundations", "walls", "columns",
        "beams", "slabs" (default: all of them).

        material_filter is the default material substring filter (matched
        against structural material, type name or family name) applied to
        every category. Use material_filters to set an independent filter
        per category, e.g. {"beams": "f'c=210", "columns": "f'c=280"} —
        any category not listed there falls back to material_filter.

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
            "dry_run": dry_run,
        }
        if categories:
            data["categories"] = categories
        if material_filters:
            data["material_filters"] = material_filters
        if element_ids:
            data["element_ids"] = element_ids

        response = await revit_post("/generate_formwork/", data, ctx, timeout=120.0)
        return format_response(response)
