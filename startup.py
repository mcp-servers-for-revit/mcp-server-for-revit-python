# -*- coding: UTF-8 -*-
"""
Revit MCP Extension Startup
Registers all MCP routes and initializes the API
"""

from pyrevit import routes
import logging

logger = logging.getLogger(__name__)

def forward_request_headers():
    """Copy HTTP headers onto pyRevit route requests.

    pyRevit's Routes server builds Request objects without the incoming
    headers, so request.headers is always empty and pairing auth fails (401).
    The running server survives pyRevit reloads with its original handler
    class, so patch that class as well as the freshly imported one.
    """
    from pyrevit.coreutils import envvars
    from pyrevit.routes.server import server

    handler_classes = [server.HttpRequestHandler]
    running = envvars.get_pyrevit_env_var(envvars.ROUTES_SERVER)
    running_cls = getattr(getattr(running, "server", None), "RequestHandlerClass", None)
    if running_cls is not None and running_cls not in handler_classes:
        handler_classes.append(running_cls)

    for handler_cls in handler_classes:
        original = handler_cls.__dict__["_prepare_request"]
        # Re-wrap pyRevit's original so a reload installs the current wrapper
        original = getattr(original, "_mcp_original", original)

        def _prepare_request(self, route, path, method, query_string=None,
                             _original=original):
            request = _original(self, route, path, method, query_string)
            for key in self.headers.keys():
                request.add_header(key, self.headers.get(key))
            return request

        _prepare_request._mcp_forwards_headers = True
        _prepare_request._mcp_original = original
        handler_cls._prepare_request = _prepare_request


forward_request_headers()

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

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


# Register all routes when the extension loads
register_routes()
