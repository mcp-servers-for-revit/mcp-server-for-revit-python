# -*- coding: utf-8 -*-
"""Element-management MCP tools (delete / modify / reset).

All mutating tools enforce a two-step confirmation: the first call without
`confirm=True` returns a preview of what would change; the user must then
re-invoke with `confirm=True` to actually mutate the model. This is enforced
on both the MCP wrapper (here) AND the pyRevit route (defence in depth).
"""

from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_element_management_tools(mcp, revit_get, revit_post):
    """Register delete / modify / reset tools."""

    @mcp.tool()
    async def delete_elements(
        element_ids: List[int],
        confirm: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Delete elements from the Revit model. DESTRUCTIVE.

        Two-step gate: invoke first with confirm=False (default) to see a
        preview of what would be deleted. Then re-invoke the same call with
        confirm=True to actually delete.

        Args:
            element_ids: Integer element IDs to delete.
            confirm: Must be True to actually execute. False = preview only.

        Walls hosting doors/windows: deleting the wall will also delete the
        hosted openings (Revit's own behaviour). This is not always desired —
        prefer modifying over deleting when possible.
        """
        payload = {"element_ids": element_ids, "confirm": bool(confirm)}
        response = await revit_post("/delete_elements/", payload, ctx)
        return format_response(response)

    @mcp.tool()
    async def modify_element(
        element_id: int,
        parameters: Dict[str, Any],
        ctx: Context = None,
    ) -> str:
        """
        Modify instance parameters of a single element.

        Args:
            element_id: Element ID (int) to modify.
            parameters: Dict of {parameter_name: new_value}. Read-only and
                        non-existent parameters are skipped (reported in the
                        response). Numeric parameters use raw values in Revit's
                        internal units (decimal feet for length).

        No preview/confirm gate — parameter changes are routinely reversible
        via Revit's undo stack. For bulk changes across many elements, prefer
        calling this multiple times rather than execute_revit_code.
        """
        payload = {"element_id": int(element_id), "parameters": parameters}
        response = await revit_post("/modify_element/", payload, ctx)
        return format_response(response)

    @mcp.tool()
    async def reset_model(
        categories: Optional[List[str]] = None,
        confirm: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Delete all instances of the specified categories from the model. HIGHLY DESTRUCTIVE.

        Does NOT delete views, levels, sheets, family types, or other "project
        skeleton" content — only the geometric instances in the named categories.

        Two-step gate: must call with confirm=True to actually wipe.

        Args:
            categories: List of category names (e.g. ["walls", "doors", "windows"]).
                        Supported aliases: walls, floors, ceilings, roofs, doors,
                        windows, furniture, generic_model, stairs, railings,
                        columns, structural_framing, structural_columns,
                        structural_foundation. If omitted, uses a built-in safe
                        default list covering the architectural categories.
            confirm: Must be True to actually execute. False = preview only.

        Recovery: only via Revit's undo stack (Ctrl+Z) immediately after. If
        the file is saved before noticing, recovery requires opening a backup.
        """
        payload = {"confirm": bool(confirm)}
        if categories:
            payload["categories"] = categories
        response = await revit_post("/reset_model/", payload, ctx)
        return format_response(response)
