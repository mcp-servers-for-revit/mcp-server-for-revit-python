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

        from revit_mcp.formwork import register_formwork_routes

        register_formwork_routes(api)

        from revit_mcp.selection import register_selection_routes

        register_selection_routes(api)

        from revit_mcp.element_management import register_element_management_routes

        register_element_management_routes(api)

        from revit_mcp.annotation import register_annotation_routes

        register_annotation_routes(api)

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


# Register all routes when the extension loads
register_routes()

# Keep a failed request (e.g. a dropped client) from crashing Revit.
try:
    from revit_mcp.server_guard import install as install_server_guard

    install_server_guard(__revit__)  # noqa: F821 - provided by pyRevit
except Exception as e:
    logger.error("Failed to install Routes server guard: %s", str(e))
