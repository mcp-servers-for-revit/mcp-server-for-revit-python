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

        logger.info("All MCP routes registered successfully")

    except Exception as e:
        logger.error("Failed to register MCP routes: %s", str(e))
        raise


def install_header_passthrough():
    """Let route handlers see the pairing header; without it requests get 401.

    pyRevit runs this script in its own engine and starts the routes server
    afterwards, so patch the live server's handler class once it exists.
    """
    import os
    import threading
    import time
    from revit_mcp.pairing import pairing_path
    from revit_mcp.security import install_header_passthrough as install

    log_path = os.path.join(os.path.dirname(pairing_path()), "bridge.log")

    def log(message):
        # Messages carry header names and errors only, never header values.
        try:
            with open(log_path, "a") as stream:
                stream.write("{} {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), message))
        except Exception:
            pass

    def wait_for_server():
        for _ in range(240):
            active_server = routes.get_active_server()
            if active_server:
                try:
                    handler_class = active_server.server.RequestHandlerClass
                    patched = install(handler_class, log)
                    log("Routes server found; header passthrough {} on {}".format(
                        "installed" if patched else "already present", handler_class))
                except Exception as e:
                    log("Failed to pass request headers to MCP routes: {}".format(e))
                return
            time.sleep(0.5)
        log("Routes server not running; MCP requests will be rejected.")

    log("Waiting for routes server")
    thread = threading.Thread(target=wait_for_server)
    thread.daemon = True
    thread.start()


# Register all routes when the extension loads
install_header_passthrough()
register_routes()
