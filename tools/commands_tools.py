# -*- coding: utf-8 -*-
"""Commands tools for the MCP server."""
from mcp.server.fastmcp import Context
from .utils import format_response


def register_commands_tools(mcp, revit_get, revit_post, revit_image=None):
    """Register your tools with the MCP server."""
    
    # ---- Tool for the GET request ----
    @mcp.tool()
    async def list_commands(
        ctx: Context = None
    ) -> str:

        """
        Return a list of all pyrevit commands, including control & uniqueid
        """
        response = await revit_get("/commands_run", ctx)
        return format_response(response)


    # ---- Tool for the POST request ----
    @mcp.tool()
    async def run_command_by_control_id(control_id: str, config:bool = False, wait:bool=False ,ctx: Context) -> str:
        """
        Runs a command using its control_id
        
        Args:
            control_id: The ID of the command, as found in the revit journal or list_commands tool
            config: Run tool in config (shift+click) mode. 
            wait: Wait for tool to finish and return response

        """
        payload = {"control_id": control_id, "config": config, "wait" = wait}
        response = await revit_post("/commands_run", payload, ctx)
        return format_response(response)
