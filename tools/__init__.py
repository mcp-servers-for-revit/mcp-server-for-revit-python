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
    # Added 2026-05-18 (9 new tools): selection / creation / management / annotation / integration
    from .selection_tools import register_selection_tools
    from .element_creation_tools import register_element_creation_tools
    from .element_management_tools import register_element_management_tools
    from .annotation_tools import register_annotation_tools
    from .integration_tools import register_integration_tools
    # Added 2026-05-18 (+1): grids
    from .grids_tools import register_grids_tools
    # Added 2026-05-18 (+1): levels
    from .levels_tools import register_levels_tools
    # Added 2026-05-19 (+1): rooms (ported from Sparx)
    from .rooms_tools import register_rooms_tools
    # Added 2026-05-19 (+1): dimensions (ported from Sparx)
    from .dimensions_tools import register_dimensions_tools
    # Added 2026-05-19 (+1): material quantities (ported from Sparx)
    from .material_quantities_tools import register_material_quantities_tools
    # Added 2026-05-19 (+1): structured element filter (ported from Sparx AIElementFilter)
    from .element_filter_tools import register_element_filter_tools
    # Added 2026-05-19 (+1): model-statistics rollup (ported from Sparx AnalyzeModelStatistics)
    from .model_statistics_tools import register_model_statistics_tools
    # Added 2026-05-19 (+1): room tagging (ported from Sparx TagRooms)
    from .room_annotation_tools import register_room_annotation_tools
    # Added 2026-05-19 (+1): room-data export (ported from Sparx ExportRoomData)
    from .room_data_tools import register_room_data_tools
    # Added 2026-05-20 (+1): multi-action element operations (ported from Sparx OperateElementCommand)
    from .element_operations_tools import register_element_operations_tools

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
    # 2026-05-18 additions
    register_selection_tools(mcp_server, revit_get_func)
    register_element_creation_tools(mcp_server, revit_get_func, revit_post_func)
    register_element_management_tools(mcp_server, revit_get_func, revit_post_func)
    register_annotation_tools(mcp_server, revit_get_func, revit_post_func)
    register_integration_tools(mcp_server, revit_get_func, revit_post_func)
    register_grids_tools(mcp_server, revit_get_func, revit_post_func)
    register_levels_tools(mcp_server, revit_get_func, revit_post_func)
    register_rooms_tools(mcp_server, revit_get_func, revit_post_func)
    register_dimensions_tools(mcp_server, revit_get_func, revit_post_func)
    register_material_quantities_tools(mcp_server, revit_get_func, revit_post_func)
    register_element_filter_tools(mcp_server, revit_get_func, revit_post_func)
    register_model_statistics_tools(mcp_server, revit_get_func, revit_post_func)
    register_room_annotation_tools(mcp_server, revit_get_func, revit_post_func)
    register_room_data_tools(mcp_server, revit_get_func, revit_post_func)
    register_element_operations_tools(mcp_server, revit_get_func, revit_post_func)
