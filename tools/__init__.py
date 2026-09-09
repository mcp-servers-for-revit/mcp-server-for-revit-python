# -*- coding: utf-8 -*-
"""Tool registration system for Revit MCP Server"""


def register_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func,
                   instance_api=None):
    """Register all tools with the MCP server"""
    # Import all tool modules
    from .status_tools import register_status_tools
    from .view_tools import register_view_tools
    from .family_tools import register_family_tools
    from .model_tools import register_model_tools
    from .colors_tools import register_colors_tools
    from .code_execution_tools import register_code_execution_tools
    from .launch_tools import register_launch_tools
    from .document_tools import register_document_tools

    # Register tools from each module
    register_status_tools(mcp_server, revit_get_func)
    register_view_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_family_tools(mcp_server, revit_get_func, revit_post_func)
    register_model_tools(mcp_server, revit_get_func)
    register_colors_tools(mcp_server, revit_get_func, revit_post_func)
    register_code_execution_tools(
        mcp_server, revit_get_func, revit_post_func, revit_image_func
    )
    register_launch_tools(mcp_server, revit_get_func)
    register_document_tools(mcp_server, revit_get_func, revit_post_func)

    if instance_api is not None:
        from .instance_tools import register_instance_tools
        register_instance_tools(
            mcp_server,
            instance_api["discover_instances"],
            instance_api["select_instance"],
            instance_api["get_active_port"],
        )
