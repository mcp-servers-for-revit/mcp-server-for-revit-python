# -*- coding: utf-8 -*-
"""Model-statistics rollup MCP tools."""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_model_statistics_tools(mcp, revit_get, revit_post):
    """Register the model-statistics rollup tool."""

    @mcp.tool()
    async def analyze_model_statistics(
        category_filter: Optional[List[str]] = None,
        view_id: Optional[int] = None,
        include_detailed_types: bool = True,
        top_n_categories: Optional[int] = None,
        top_n_types_per_category: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Roll up project-level statistics: counts, categories, types, levels.

        Walks every element in the chosen scope and produces a structured
        breakdown of:
            - project-wide totals (elements / types / families / views / sheets)
            - per-category counts with optional drill-down into types
              (type_name + family_name + instance_count)
            - per-level element counts with elevations in mm

        This is the right tool for questions like:
            - "How many walls / doors / windows are in this project?"
            - "What categories carry the most elements?"
            - "Which generic-model families have the most instances?"
            - "What's on Level 2 vs Level 1?"

        Args:
            category_filter: Optional list of BuiltInCategory names
                (e.g. ["OST_Walls", "OST_Doors"]; with or without OST_
                prefix). Restricts the per-category drill-down. The headline
                totals (elements/types/families/views/sheets) and per-level
                rollup are always project-wide. None = all categories.
            view_id: Optional View ElementId. When provided, the
                per-category drill-down is limited to elements visible in
                this view. The headline totals and per-level rollup remain
                project-wide.
            include_detailed_types: When True (default), each category in
                the result carries a `types` list with type_name /
                family_name / instance_count. When False, only the counts
                are returned (much smaller payload for large models).
            top_n_categories: Optional cap on the categories returned.
                Categories are sorted by element_count desc, then name.
                When applied, `truncated_categories=true` in the response.
            top_n_types_per_category: Optional cap on the types inside each
                category. Types within a category are sorted by
                instance_count desc, then type_name. When applied to any
                category, `truncated_types_per_category=true` and the
                per-category `types_truncated=true` flag is set.

        Returns a JSON-encoded breakdown:
            {
                "status": "success",
                "project_name": "Project1",
                "totals": {
                    "elements": N, "types": N, "families": N,
                    "views": N, "sheets": N
                },
                "categories": [
                    {
                        "category_name": "Walls",
                        "element_count": N,
                        "type_count": N,
                        "family_count": N,
                        "types": [
                            {"type_name": "Generic - 200mm",
                             "family_name": "Basic Wall",
                             "instance_count": N}
                        ],
                        "types_truncated": false
                    }
                ],
                "levels": [
                    {"level_id": 311,
                     "level_name": "Level 1",
                     "elevation_mm": 0.0,
                     "element_count": N}
                ],
                "applied_filters": [...],
                "view_source": "...",
                "view_id": ...,
                "view_name": ...,
                "invalid_category_names": [...],
                "truncated_categories": false,
                "truncated_types_per_category": false
            }

        Status values for recoverable failures:
            - 'view_not_found' : view_id was supplied but invalid

        No transactions — pure read.
        """
        payload = {
            "category_filter": category_filter,
            "view_id": view_id,
            "include_detailed_types": include_detailed_types,
            "top_n_categories": top_n_categories,
            "top_n_types_per_category": top_n_types_per_category,
        }
        response = await revit_post("/analyze_model_statistics/", payload, ctx)
        return format_response(response)
