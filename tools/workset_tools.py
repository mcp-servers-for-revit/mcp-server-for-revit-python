# -*- coding: utf-8 -*-
"""Workset MCP tool -- create user worksets in a workshared Revit model."""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_workset_tools(mcp, revit_get, revit_post):
    """Register the create_workset tool."""

    @mcp.tool()
    async def create_workset(
        name: str,
        ctx: Context = None,
    ) -> str:
        """
        Create a user workset in the active Revit model.

        Worksets only exist in workshared models. If the model is not
        workshared this returns a 'not_workshared' status -- enable
        worksharing in Revit (Collaborate tab) first; this tool will not
        enable worksharing for you, since that is a one-way, model-wide change.

        Args:
            name: Name for the new workset. Must be unique within the project;
                a duplicate returns a 'name_collision' status with the id of
                the existing workset.

        To create several worksets, call this once per name.
        """
        payload = {"name": name}
        response = await revit_post("/create_workset/", payload, ctx)
        return format_response(response)
