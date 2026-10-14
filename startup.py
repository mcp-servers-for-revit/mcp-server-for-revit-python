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


# --- Blocking-dialog suppression -------------------------------------------
# Some models reference Autodesk cloud paths. Opening them raises a modal
# dialog ("Autodesk Desktop Connector isn't installed") which blocks the Revit
# UI thread. While it is up, every MCP call that needs the Revit API context
# times out with no useful error. This dismisses only those dialogs, with the
# same answer a user would click, and leaves all other dialogs alone.

IDOK = 1
IDCANCEL = 2
CMDLINK1 = 1001  # first command link of a Revit TaskDialog

# needle -> result to return. Matched against DialogId + Message, lowercased.
# "unresolved references" answers with the first command link, which is
# "Ignore and continue opening the project" - authorised by the user.
SUPPRESS_DIALOGS = [
    ("desktop connector", IDOK),
    ("unresolved references", CMDLINK1),
    ("unresolvedreferences", CMDLINK1),
    ("could not find", CMDLINK1),
]

# Written to by the handler so a caller can see what was dismissed.
DISMISSED = []


def _on_dialog_showing(sender, args):
    """Auto-dismiss known non-actionable dialogs that would deadlock MCP calls.

    These fire on the Revit UI thread during API document operations. If one
    goes unanswered the whole API context deadlocks: no dialog window is ever
    rendered, so nothing outside the process can click it.
    """
    try:
        parts = []
        for attr in ("DialogId", "Message", "HelpId"):
            val = getattr(args, attr, None)
            if val:
                parts.append(str(val))
        text = " ".join(parts).lower()
        for needle, result in SUPPRESS_DIALOGS:
            if needle in text:
                try:
                    args.OverrideResult(result)
                except Exception:
                    args.OverrideResult(IDOK)
                DISMISSED.append(" ".join(parts)[:200])
                logger.info("Auto-dismissed dialog (%s): %s", result, " ".join(parts)[:200])
                return
        # Not suppressed - record it so we can see what blocked us
        if parts:
            logger.info("Dialog shown (not suppressed): %s", " ".join(parts)[:200])
    except Exception as e:
        # Never let this handler raise - it would break every dialog in Revit
        logger.error("Dialog suppression handler failed: %s", str(e))


def register_dialog_suppression():
    """Subscribe to DialogBoxShowing on the Revit UI application."""
    try:
        __revit__.DialogBoxShowing += _on_dialog_showing
        logger.info("Dialog suppression registered")
    except Exception as e:
        logger.error("Could not register dialog suppression: %s", str(e))


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
register_dialog_suppression()
