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
    from .annotation_tools import register_annotation_tools
    from .mep_tools import register_mep_tools
    from .detail_tools import register_detail_tools
    from .transform_tools import register_transform_tools
    from .interop_tools import register_interop_tools
    from .parameter_tools import register_parameter_tools
    from .view_management_tools import register_view_management_tools
    from .tag_tools import register_tag_tools
    from .upstream_structure_tools import register_upstream_structure_tools
    from .shared_param_tools import register_shared_param_tools
    from .geometry_tools import register_geometry_tools
    from .detailing_tools import register_detailing_tools

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
    register_annotation_tools(mcp_server, revit_get_func, revit_post_func)
    register_mep_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_detail_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_transform_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_interop_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_parameter_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_view_management_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_tag_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_upstream_structure_tools(mcp_server, revit_get_func, revit_post_func, revit_image_func)
    register_shared_param_tools(mcp_server, revit_get_func, revit_post_func)
    register_geometry_tools(mcp_server, revit_get_func, revit_post_func)
    register_detailing_tools(mcp_server, revit_get_func, revit_post_func)
