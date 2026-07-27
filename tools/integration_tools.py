# -*- coding: utf-8 -*-
"""Integration MCP tools (extension discovery + invocation)."""

from typing import Optional, List, Dict, Any
from mcp.server.fastmcp import Context
from .utils import format_response


def register_integration_tools(mcp, revit_get, revit_post):
    """Register module-discovery and module-invocation tools."""

    @mcp.tool()
    async def search_modules(
        query: Optional[str] = None,
        ctx: Context = None,
    ) -> str:
        """
        List installed pyRevit extensions on this machine, optionally filtered
        by name substring.

        Args:
            query: Case-insensitive substring filter on extension folder name.
                   Omit to list all extensions.

        Returns one entry per extension with: name, path, type ('ui' or 'lib'),
        has_lib (whether it has a lib/ directory you can import from), and
        command_count (number of .pushbutton folders, UI extensions only).
        """
        payload = {"query": query} if query else {}
        # GET when no body, POST when filtering
        if query:
            response = await revit_post("/search_modules/", payload, ctx)
        else:
            response = await revit_get("/search_modules/", ctx)
        return format_response(response)

    @mcp.tool()
    async def use_module(
        extension_name: str,
        module_path: str,
        function_name: str,
        args: Optional[List[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        confirm: bool = False,
        ctx: Context = None,
    ) -> str:
        """
        Import a named module from a named pyRevit extension's lib/ directory
        and call a named function. Stricter than execute_revit_code — the
        callable must already exist in the extension; no arbitrary statements.

        Two-step gate: must call with confirm=True to actually invoke. First
        call returns a preview describing what would be invoked.

        Args:
            extension_name: Full folder name (e.g. "MyTools.extension" or
                            "MyLib.lib"). Use search_modules to find it.
            module_path: Dotted import path under <extension>/lib/
                         (e.g. "submodule.helpers").
            function_name: Name of the callable to invoke.
            args: Positional args list (JSON-serialisable).
            kwargs: Keyword args dict (JSON-serialisable).
            confirm: Must be True to actually invoke. False = preview only.

        Return value: whatever the called function returns, best-effort
        JSON-serialised (non-JSON types stringified).

        Caveat: invoked code runs inside Revit's IronPython process with full
        Revit API access. Treat the extension list as you would `sudo` — only
        invoke functions from extensions you trust.
        """
        payload = {
            "extension_name": extension_name,
            "module_path": module_path,
            "function_name": function_name,
            "args": args or [],
            "kwargs": kwargs or {},
            "confirm": bool(confirm),
        }
        response = await revit_post("/use_module/", payload, ctx)
        return format_response(response)
