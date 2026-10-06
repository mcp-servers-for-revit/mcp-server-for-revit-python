# -*- coding: utf-8 -*-
"""Optional tool arguments that default to None must accept an explicit null.

Some MCP clients send "arg": null for an optional argument they leave unset.
Annotated `str = None`, the schema advertises "default": null on a string-only
field and FastMCP rejects the call before the tool runs.
"""
from main import mcp


def _sample(prop):
    return "x" if prop.get("type") == "string" else 1


async def test_null_default_args_accept_explicit_null():
    rejected = []
    checked = 0
    for tool in await mcp.list_tools():
        props = tool.inputSchema.get("properties", {})
        required = tool.inputSchema.get("required", [])
        arg_model = mcp._tool_manager.get_tool(tool.name).fn_metadata.arg_model
        for name, prop in props.items():
            if "default" not in prop or prop["default"] is not None:
                continue
            checked += 1
            args = {r: _sample(props[r]) for r in required}
            args[name] = None
            try:
                arg_model.model_validate(args)
            except Exception:
                rejected.append(f"{tool.name}.{name}")

    assert checked >= 9
    assert rejected == []
