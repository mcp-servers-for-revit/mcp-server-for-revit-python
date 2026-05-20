# -*- coding: UTF-8 -*-
"""
Revit MCP Extension Startup
Registers all MCP routes and initializes the API
"""

from pyrevit import routes
import logging

logger = logging.getLogger(__name__)

# Initialize the main API
api = routes.API("revit_mcp")


def register_routes():
    """Register all MCP route modules"""
    try:
        # Import and register status routes
        from revit_mcp.status import register_status_routes

        register_status_routes(api)

        from revit_mcp.model_info import register_model_info_routes

        register_model_info_routes(api)

        from revit_mcp.views import register_views_routes

        register_views_routes(api)

        from revit_mcp.placement import register_placement_routes

        register_placement_routes(api)

        from revit_mcp.colors import register_color_routes

        register_color_routes(api)

        from revit_mcp.code_execution import register_code_execution_routes

        register_code_execution_routes(api)

        from revit_mcp.document import register_document_routes

        register_document_routes(api)

        # ---- Tools added 2026-05-18: 9 new endpoints ----
        from revit_mcp.selection import register_selection_routes

        register_selection_routes(api)

        from revit_mcp.element_creation import register_element_creation_routes

        register_element_creation_routes(api)

        from revit_mcp.element_management import register_element_management_routes

        register_element_management_routes(api)

        from revit_mcp.annotation import register_annotation_routes

        register_annotation_routes(api)

        from revit_mcp.integration import register_integration_routes

        register_integration_routes(api)

        from revit_mcp.grids import register_grids_routes

        register_grids_routes(api)

        from revit_mcp.levels import register_levels_routes

        register_levels_routes(api)
        # ---- end 2026-05-18 additions ----

        # ---- 2026-05-19: rooms (ported from Sparx) ----
        from revit_mcp.rooms import register_rooms_routes

        register_rooms_routes(api)

        # ---- 2026-05-19: dimensions (ported from Sparx) ----
        from revit_mcp.dimensions import register_dimensions_routes

        register_dimensions_routes(api)

        # ---- 2026-05-19: material quantities (ported from Sparx) ----
        from revit_mcp.material_quantities import register_material_quantities_routes

        register_material_quantities_routes(api)

        # ---- 2026-05-19: structured element filter (ported from Sparx AIElementFilter) ----
        from revit_mcp.element_filter import register_element_filter_routes

        register_element_filter_routes(api)

        # ---- 2026-05-19: model-statistics rollup (ported from Sparx AnalyzeModelStatistics) ----
        from revit_mcp.model_statistics import register_model_statistics_routes

        register_model_statistics_routes(api)

        # ---- 2026-05-19: room tagging (ported from Sparx TagRooms) ----
        from revit_mcp.room_annotation import register_room_annotation_routes

        register_room_annotation_routes(api)

        # ---- 2026-05-19: room data export (ported from Sparx ExportRoomData) ----
        from revit_mcp.room_data import register_room_data_routes

        register_room_data_routes(api)
        # ---- end 2026-05-19 additions ----

        # ---- 2026-05-20: multi-action element operations (ported from Sparx OperateElementCommand) ----
        from revit_mcp.element_operations import register_element_operations_routes

        register_element_operations_routes(api)

        # ---- 2026-05-20: workset creation ----
        from revit_mcp.worksets import register_workset_routes

        register_workset_routes(api)
        # ---- end 2026-05-20 additions ----

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


# Register all routes when the extension loads
register_routes()
