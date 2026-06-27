# -*- coding: utf-8 -*-
"""Interop tools — IFC export and external file linking"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_interop_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register interop tools with the MCP server."""
    _ = revit_image  # Acknowledge unused parameter

    @mcp.tool()
    async def export_ifc(
        file_path: str,
        ifc_version: str = "IFC2x3",
        export_base_quantities: bool = True,
        view_name: str = None,
        ctx: Context = None,
    ) -> str:
        """Export the Revit model to IFC format.

        Creates an IFC file at the specified path. Optionally filter by view
        to export only visible elements. Supports IFC2x3 and IFC4.

        Args:
            file_path: Output file path (must end in .ifc)
            ifc_version: "IFC2x3" (default) or "IFC4"
            export_base_quantities: Include IFC base quantities (default: true)
            view_name: Export only elements visible in this view (optional)
            ctx: MCP context for logging
        """
        data = {
            "file_path": file_path,
            "ifc_version": ifc_version,
            "export_base_quantities": export_base_quantities,
        }
        if view_name is not None:
            data["view_name"] = view_name
        response = await revit_post("/export_ifc/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def link_file(
        file_path: str,
        mode: str = "link",
        position: dict = None,
        ctx: Context = None,
    ) -> str:
        """Link or import an external file into the Revit model.

        Supports DWG, DXF, DGN (CAD files) and RVT (Revit links).
        Linked files maintain a live connection; imported files are embedded.

        Args:
            file_path: Path to the file (DWG, DXF, DGN, or RVT)
            mode: "link" (default, maintains connection) or "import" (embeds copy)
            position: Optional placement offset {"x", "y", "z"} in mm
            ctx: MCP context for logging
        """
        data = {"file_path": file_path, "mode": mode}
        if position is not None:
            data["position"] = position
        response = await revit_post("/link_file/", data, ctx)
        return format_response(response)
