# -*- coding: utf-8 -*-
"""Element-operations MCP tool.

A single multi-action dispatcher for view-state, selection, and visibility
operations on a set of elements. Ported from the Sparx mcp-servers-for-revit
`OperateElementCommand`.
"""

from typing import List, Optional
from mcp.server.fastmcp import Context
from .utils import format_response


def register_element_operations_tools(mcp, revit_get, revit_post):
    """Register the operate_element tool."""

    @mcp.tool()
    async def operate_element(
        action: str,
        element_ids: Optional[List[int]] = None,
        color: Optional[List[int]] = None,
        transparency: Optional[int] = None,
        view_id: Optional[int] = None,
        confirm: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Perform a view-state, selection, or visibility operation on a set of
        elements. One tool, many actions.

        Args:
            action: One of --
                select           -- set the Revit UI selection to these elements
                selection_box    -- enable a 3D section box around these elements
                set_color        -- override colour in a view (needs `color`)
                set_transparency -- override surface transparency (needs `transparency`)
                hide             -- permanently hide elements in a view
                unhide           -- un-hide elements in a view
                temp_hide        -- temporarily hide (session-only view mode)
                isolate          -- temporarily isolate (session-only view mode)
                reset_isolate    -- clear the temporary hide/isolate view mode
                delete           -- delete elements (confirm-gated)
            element_ids: Integer element IDs to operate on. Required for every
                action except reset_isolate.
            color: [r, g, b] with values 0-255 -- required for set_color.
            transparency: 0 (opaque) to 100 (fully transparent) -- required for
                set_transparency.
            view_id: Optional view to target. Defaults to the active view; for
                selection_box it must be a 3D view (a 3D view is auto-found and
                activated if omitted).
            confirm: For action="delete" only -- must be True to actually
                delete. False (default) returns a preview. Ignored otherwise.

        The `delete` action is confirm-gated for safety; for delete-only
        workflows prefer the dedicated delete_elements tool. Temporary view
        modes set by temp_hide / isolate are cleared with action=reset_isolate.
        """
        payload = {"action": action, "confirm": bool(confirm)}
        if element_ids is not None:
            payload["element_ids"] = element_ids
        if color is not None:
            payload["color"] = color
        if transparency is not None:
            payload["transparency"] = transparency
        if view_id is not None:
            payload["view_id"] = int(view_id)
        response = await revit_post("/operate_element/", payload, ctx)
        return format_response(response)
