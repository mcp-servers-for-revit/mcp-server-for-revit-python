# -*- coding: utf-8 -*-
"""Tools for discovering and selecting between running Revit instances"""

import json
from mcp.server.fastmcp import Context


def register_instance_tools(mcp, discover_instances, select_instance, get_active_port):
    """Register Revit instance discovery/selection tools"""

    @mcp.tool()
    async def list_revit_instances(ctx: Context) -> str:
        """List all running Revit instances and their Routes server ports.

        Each Revit instance (e.g. Revit 2024 and Revit 2025 running side by
        side) gets its own port in launch order. Use this to see which
        instance is reachable on which port and which one is currently
        active, then select_revit_instance to switch between them.
        """
        instances = await discover_instances()
        if not instances:
            return "No running Revit instances with a pyRevit Routes server were found."
        active_port = get_active_port()
        for info in instances:
            info["is_active"] = info["port"] == active_port
        return json.dumps({"count": len(instances), "instances": instances}, indent=2)

    @mcp.tool()
    async def select_revit_instance(port: int, ctx: Context) -> str:
        """Direct all subsequent Revit tools to the instance on the given port.

        Use list_revit_instances first to see the available ports and which
        document each instance has open.
        """
        result = await select_instance(port)
        return json.dumps(result, indent=2)
