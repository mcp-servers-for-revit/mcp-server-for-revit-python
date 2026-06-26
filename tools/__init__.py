# -*- coding: utf-8 -*-
"""Tool registration system for Revit MCP Server"""


def register_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func):
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
    from .structure_tools import register_structure_tools
    from .query_tools import register_query_tools
    from .create_tools import register_create_tools
    from .ops_tools import register_ops_tools

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
    register_structure_tools(mcp_server, revit_get_func, revit_post_func)
    register_query_tools(mcp_server, revit_get_func, revit_post_func)
    register_create_tools(mcp_server, revit_get_func, revit_post_func)
    register_ops_tools(mcp_server, revit_get_func, revit_post_func)
