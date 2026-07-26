# -*- coding: utf-8 -*-
"""QA/lint, organization, and view-utility tools cherry-picked from rvt-mcp (bimwright)."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_rvt_extras_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def get_model_warnings(ctx: Context = None) -> str:
        """Summarize all Revit warnings in the model (description, severity, failing element ids), grouped by message."""
        return format_response(await revit_get("/get_model_warnings/", ctx))

    @mcp.tool()
    async def analyze_view_naming(pattern: str, view_type: str = None, ctx: Context = None) -> str:
        """Check view names against a regex `pattern` (optionally filtered to a view_type); reports non-compliant views."""
        d = {"pattern": pattern}
        if view_type: d["view_type"] = view_type
        return format_response(await revit_post("/analyze_view_naming/", d, ctx))

    @mcp.tool()
    async def find_untagged_elements(category: str, view_name: str = None, ctx: Context = None) -> str:
        """Find elements of a category in a view that have no tag (e.g. OST_StructuralFraming)."""
        d = {"category": category}
        if view_name: d["view_name"] = view_name
        return format_response(await revit_post("/find_untagged_elements/", d, ctx))

    @mcp.tool()
    async def cleanup_empty_tags(view_name: str = None, all_views: bool = False, ctx: Context = None) -> str:
        """Delete tags with empty text in a view (or the whole model if all_views=true)."""
        d = {"all_views": all_views}
        if view_name: d["view_name"] = view_name
        return format_response(await revit_post("/cleanup_empty_tags/", d, ctx))

    @mcp.tool()
    async def create_group(element_ids: List[int], name: str = None, ctx: Context = None) -> str:
        """Create a model group from elements."""
        d = {"element_ids": element_ids}
        if name: d["name"] = name
        return format_response(await revit_post("/create_group/", d, ctx))

    @mcp.tool()
    async def purge_unused_families(dry_run: bool = True, ctx: Context = None) -> str:
        """List (dry_run=true) or delete (dry_run=false) loadable families with zero placed instances."""
        return format_response(await revit_post("/purge_unused_families/", {"dry_run": dry_run}, ctx))

    @mcp.tool()
    async def set_project_info(params: Dict[str, Any], ctx: Context = None) -> str:
        """Set Project Information parameters, e.g. {'Project Name':'...','Project Number':'26-0003'}."""
        return format_response(await revit_post("/set_project_info/", {"params": params}, ctx))

    @mcp.tool()
    async def set_view_crop_scale(view_name: str = None, scale: int = None, crop_active: bool = None, ctx: Context = None) -> str:
        """Set a view's scale and/or crop-box active state."""
        d = {}
        if view_name is not None: d["view_name"] = view_name
        if scale is not None: d["scale"] = scale
        if crop_active is not None: d["crop_active"] = crop_active
        return format_response(await revit_post("/set_view_crop_scale/", d, ctx))

    @mcp.tool()
    async def show_element(element_ids: List[int], ctx: Context = None) -> str:
        """Select and zoom to elements in the Revit UI."""
        return format_response(await revit_post("/show_element/", {"element_ids": element_ids}, ctx))

    @mcp.tool()
    async def create_callout(parent_view_name: str = None, p1: Dict[str, float] = None,
                             p2: Dict[str, float] = None, ctx: Context = None) -> str:
        """Create a callout (detail) on a parent view between two points p1, p2 (feet)."""
        d = {}
        if parent_view_name: d["parent_view_name"] = parent_view_name
        if p1: d["p1"] = p1
        if p2: d["p2"] = p2
        return format_response(await revit_post("/create_callout/", d, ctx))
