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
        formwork_material: str = "",
        formwork_materials: Optional[Dict[str, str]] = None,
        panel_thickness_mm: float = 18.0,
        contact_tolerance_mm: float = 5.0,
        exclude_top_faces: bool = True,
        exclude_foundation_bottom: bool = True,
        pour_against_soil: bool = True,
        ground_level_name: Optional[str] = None,
        ground_level_m: Optional[float] = None,
        create_geometry: bool = True,
        dry_run: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Generate structural formwork (encofrado) for columns, beams, slabs,
        walls, foundations and concrete stairs in the Revit model.

        Avoids double-counting formwork area on faces where two structural
        elements touch (e.g. a beam-column joint) by detecting contact
        between neighboring elements, and skips top faces (pour openings)
        and the underside of foundations (resting on ground/blinding) by
        default.

        Use dry_run=True to get a quantity/area report without creating any
        geometry, to preview before committing. Quantity takeoff is always
        returned in the report; build a native Revit schedule off the
        created panels' EF_Area_m2 / EF_Categoria_Origen / EF_Material_Encofrado
        parameters if you need a table in the model.

        `categories` accepts any of: "foundations", "walls", "columns",
        "beams", "slabs", "stairs" (default: all of them). For stairs,
        risers, side faces and the sloped flight/landing soffits get
        formwork; treads are left open like any top face. ALL elements in the
        requested categories/scope are always processed, regardless of
        their own material.

        formwork_material is a free-text tag (e.g. "Madera", "Metalico",
        "Aluminio") recorded on the created panels to identify what the
        formwork itself is made of. Use formwork_materials to set an
        independent tag per category, e.g. {"beams": "Metalico",
        "columns": "Aluminio"} — any category not listed there falls back
        to formwork_material. This never filters which elements get
        processed.

        Set scope="selection" and pass `element_ids` to restrict the run to
        specific elements instead of the whole model.

        When geometry is created, each processed element also gets its
        results in instance parameters (for schedules): EF_Area_Encofrado_m2,
        EF_Paneles and EF_Material_Encofrado.

        Masonry walls (albanileria) never get formwork; they still count as
        neighbors (a column cast against a brick wall needs none there).

        pour_against_soil (default True): faces cast against the ground get
        no formwork, reported as excluded_soil_area_m2 - every foundation
        face below ground level, and vertical faces below ground level that
        look out of the building (a retaining wall's back face, perimeter
        columns below grade). Ground level is the level named
        `ground_level_name`, else `ground_level_m` (internal elevation, m),
        else the model's "NTN" level. Per wall, the instance parameter
        EF_Cara_Contra_Terreno overrides the automatic detection:
        "Exterior" / "Interior" (the wall's own sides), "Ambas" or
        "Ninguna"; blank = automatic. Chosen faces count as against the
        ground over their full height.
        """
        data = {
            "scope": scope,
            "formwork_material": formwork_material,
            "panel_thickness_mm": panel_thickness_mm,
            "contact_tolerance_mm": contact_tolerance_mm,
            "exclude_top_faces": exclude_top_faces,
            "exclude_foundation_bottom": exclude_foundation_bottom,
            "create_geometry": create_geometry,
            "dry_run": dry_run,
        }
        if categories:
            data["categories"] = categories
        if formwork_materials:
            data["formwork_materials"] = formwork_materials
        if element_ids:
            data["element_ids"] = element_ids
        data["pour_against_soil"] = pour_against_soil
        if ground_level_name:
            data["ground_level_name"] = ground_level_name
        if ground_level_m is not None:
            data["ground_level_m"] = ground_level_m

        response = await revit_post("/generate_formwork/", data, ctx, timeout=120.0)
        return format_response(response)
