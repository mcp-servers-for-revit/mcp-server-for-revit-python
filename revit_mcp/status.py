# -*- coding: UTF-8 -*-
"""
Status Module for Revit MCP
Handles API status and health check endpoints
"""

from pyrevit import routes
import logging

logger = logging.getLogger(__name__)

def register_status_routes(api):
    """Register all status-related routes with the API"""
    
    @api.route('/status/', methods=["GET"])
    def revit_status():
        """
        Health check endpoint that verifies Revit context availability
        
        Returns:
            dict: Health status with Revit document information
        """
        try:
            from pyrevit import revit, HOST_APP

            try:
                revit_version = HOST_APP.pretty_name
            except Exception:
                revit_version = None

            doc = revit.doc
            if doc:
                return routes.make_response(data={
                    "status": "active",
                    "health": "healthy",
                    "revit_available": True,
                    "document_title": doc.Title if doc.Title else "Untitled",
                    "revit_version": revit_version,
                    "api_name": "revit_mcp"
                })
            else:
                return routes.make_response(data={
                    "status": "unhealthy",
                    "revit_available": False,
                    "error": "No active Revit document",
                    "revit_version": revit_version,
                    "api_name": "revit_mcp"
                }, status=503)
                
        except Exception as e:
            logger.error("Health check failed:{}".format(str(e)))
            return routes.make_response(data={
                "status": "unhealthy",
                "revit_available": False, 
                "error": str(e),
                "api_name": "revit_mcp"
            }, status=503)
    
    logger.info("Status routes registered successfully")