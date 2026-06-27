# -*- coding: utf-8 -*-
"""Shared-parameter, reference-plane, and graphic-override tools.
Ported from the Revit-2026-MCP-Server C# command set."""

from mcp.server.fastmcp import Context
from typing import List, Dict, Any
from .utils import format_response


def register_shared_param_tools(mcp, revit_get, revit_post):

    @mcp.tool()
    async def detect_document_type(ctx: Context = None) -> str:
        """Report whether the active document is a project (.rvt) or family (.rfa) and what param ops are allowed."""
        return format_response(await revit_get("/detect_document_type/", ctx))

    @mcp.tool()
    async def add_project_shared_parameter(
        shared_parameter_file: str,
        parameter_name: str,
        categories: List[str],
        parameter_group: str = "Structural",
        is_instance: bool = True,
        ctx: Context = None,
    ) -> str:
        """
        Bind a shared parameter (from a shared-parameter .txt file, e.g. KPFF's) to project categories.
        categories: display names or OST_ ids (e.g. ['Structural Framing','Structural Columns']).
        parameter_group: where it shows in Properties (Structural, Data, Identity Data, Text, General...).
        """
        data = {"shared_parameter_file": shared_parameter_file, "parameter_name": parameter_name,
                "categories": categories, "parameter_group": parameter_group, "is_instance": is_instance}
        return format_response(await revit_post("/add_project_shared_parameter/", data, ctx))

    @mcp.tool()
    async def remove_project_shared_parameter(parameter_name: str, ctx: Context = None) -> str:
        """Remove a project parameter binding by name."""
        return format_response(await revit_post("/remove_project_shared_parameter/", {"parameter_name": parameter_name}, ctx))

    @mcp.tool()
    async def get_project_shared_parameters(ctx: Context = None) -> str:
        """List all parameters bound in the project (name, instance/type, bound categories, GUID if shared)."""
        return format_response(await revit_get("/get_project_shared_parameters/", ctx))

    @mcp.tool()
    async def add_family_shared_parameter(
        shared_parameter_file: str,
        parameter_name: str,
        parameter_group: str = "General",
        is_instance: bool = True,
        ctx: Context = None,
    ) -> str:
        """Add a shared parameter to the open FAMILY document (Family Editor only)."""
        data = {"shared_parameter_file": shared_parameter_file, "parameter_name": parameter_name,
                "parameter_group": parameter_group, "is_instance": is_instance}
        return format_response(await revit_post("/add_family_shared_parameter/", data, ctx))

    @mcp.tool()
    async def remove_family_parameter(parameter_name: str, ctx: Context = None) -> str:
        """Remove a parameter from the open FAMILY document (Family Editor only)."""
        return format_response(await revit_post("/remove_family_parameter/", {"parameter_name": parameter_name}, ctx))

    @mcp.tool()
    async def get_family_parameters(ctx: Context = None) -> str:
        """List parameters in the open FAMILY document (Family Editor only)."""
        return format_response(await revit_get("/get_family_parameters/", ctx))

    @mcp.tool()
    async def create_reference_plane(
        bubble: Dict[str, float],
        free: Dict[str, float],
        cut_vector: Dict[str, float] = None,
        name: str = None,
        view_name: str = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a reference plane between bubble and free points (feet) in a view.
        cut_vector (optional) is the plane normal; defaults perpendicular to the bubble->free line.
        """
        data = {"bubble": bubble, "free": free}
        if cut_vector: data["cut_vector"] = cut_vector
        if name: data["name"] = name
        if view_name: data["view_name"] = view_name
        return format_response(await revit_post("/create_reference_plane/", data, ctx))

    @mcp.tool()
    async def get_reference_planes(name: str = None, include_unnamed: bool = True, ctx: Context = None) -> str:
        """List reference planes (id, name, bubble/free ends), optionally filtered by name."""
        return format_response(await revit_post("/get_reference_planes/", {"name": name, "include_unnamed": include_unnamed}, ctx))

    @mcp.tool()
    async def set_graphic_overrides(
        category: str = None,
        element_ids: List[int] = None,
        view_name: str = None,
        halftone: bool = None,
        transparency: int = None,
        projection_line_color: List[int] = None,
        projection_line_weight: int = None,
        cut_line_color: List[int] = None,
        surface_foreground_color: List[int] = None,
        detail_level: str = None,
        reset: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Apply view graphic overrides to a category or specific elements (active view unless view_name given).
        Colors are [r,g,b]. detail_level: coarse|medium|fine. reset=true clears overrides.
        """
        data = {"reset": reset}
        for k, v in (("category", category), ("element_ids", element_ids), ("view_name", view_name),
                     ("halftone", halftone), ("transparency", transparency),
                     ("projection_line_color", projection_line_color),
                     ("projection_line_weight", projection_line_weight),
                     ("cut_line_color", cut_line_color),
                     ("surface_foreground_color", surface_foreground_color),
                     ("detail_level", detail_level)):
            if v is not None:
                data[k] = v
        return format_response(await revit_post("/set_graphic_overrides/", data, ctx))
