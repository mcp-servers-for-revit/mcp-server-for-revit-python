# -*- coding: utf-8 -*-
"""Material-quantity extraction MCP tools."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_material_quantities_tools(mcp, revit_get, revit_post):
    """Register material-quantity extraction tools."""

    @mcp.tool()
    async def get_material_quantities(
        category_filter: Optional[List[str]] = None,
        element_ids: Optional[List[int]] = None,
        view_id: Optional[int] = None,
        include_zero_volume: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Roll up material volumes (m3) and areas (m2) across the Revit model.

        Walks every element in the chosen scope, calls GetMaterialIds /
        GetMaterialVolume / GetMaterialArea on each, and groups the results
        by material. Returns a per-material breakdown (name, class, volume,
        area, element count, categories the material appeared on) and a
        totals block.

        This is the right tool for questions like:
            - "How much concrete is in the building?"
            - "What's the total area of Brick - Common in walls and floors?"
            - "Which material has the largest volume?"

        Args:
            category_filter: Optional list of BuiltInCategory names to
                restrict the scan to (e.g. ["OST_Walls", "OST_Floors",
                "OST_StructuralColumns"]). Names with or without the OST_
                prefix are both accepted. Unknown names are skipped and
                returned in `invalid_category_names`. None = all categories.
            element_ids: Optional list of Revit ElementIds to restrict the
                scan to. When provided, supersedes view_id. None = all
                elements in scope.
            view_id: Optional View ElementId. When provided (and element_ids
                isn't), the scan is limited to elements visible in this
                view. Useful for "what's in the third-floor plan view?".
            include_zero_volume: If False (default), drops materials that
                contribute zero m3 AND zero m2. These usually come from
                thin-paint applications that GetMaterialIds(False) shouldn't
                include but occasionally leak through.

        Returns a JSON-encoded breakdown sorted by volume descending:
            {
                "status": "success",
                "materials": [
                    {
                        "material_id": 123,
                        "material_name": "Concrete - Cast-in-Place",
                        "material_class": "Concrete",
                        "volume_m3": 14.732,
                        "area_m2": 65.4,
                        "element_count": 18,
                        "categories": ["Floors", "Walls"]
                    },
                    ...
                ],
                "totals": { "material_count": N, "volume_m3": X,
                            "area_m2": Y, "element_count": Z },
                "picker_path": ...,
                "elements_scanned": ...
            }

        Status values for recoverable failures:
            - 'no_elements_found' : filter produced an empty element set
            - 'view_not_found'    : view_id was supplied but invalid

        No transactions — pure read.
        """
        payload = {
            "category_filter": category_filter,
            "element_ids": element_ids,
            "view_id": view_id,
            "include_zero_volume": include_zero_volume,
        }
        response = await revit_post("/get_material_quantities/", payload, ctx)
        return format_response(response)
