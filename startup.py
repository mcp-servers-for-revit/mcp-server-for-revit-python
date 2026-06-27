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

        from revit_mcp.structure_framing import register_structure_framing_routes

        register_structure_framing_routes(api)

        from revit_mcp.ported_query import register_ported_query_routes

        register_ported_query_routes(api)

        from revit_mcp.ported_create import register_ported_create_routes

        register_ported_create_routes(api)

        from revit_mcp.ported_ops import register_ported_ops_routes

        register_ported_ops_routes(api)

        from revit_mcp.ported_annotation import register_ported_annotation_routes

        register_ported_annotation_routes(api)

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


# Register all routes when the extension loads
register_routes()
