# -*- coding: utf-8 -*-
"""Utility functions for MCP tools"""


def format_response(response):
    """Helper function to format API responses consistently for MCP tools.

    Args:
        response: The response from a revit_get or revit_post call, can be dict or string

    Returns:
        str: Formatted string response suitable for MCP tool return values
    """
    if isinstance(response, dict):
        # Check for different success patterns
        status = response.get("status", "").lower()
        health = response.get("health", "").lower()
        
        # Success conditions: status="success" OR status="active" with health="healthy"
        is_success = (status == "success" or 
                     (status == "active" and health == "healthy") or
                     (status == "active" and "revit_available" in response and response["revit_available"]))
        
        if is_success:
            # For successful responses, return the most relevant data
            if "output" in response:  # Code execution responses
                return response["output"]
            elif "message" in response:
                return response["message"]
            elif "result" in response:
                return str(response["result"])
            elif "data" in response:
                return str(response["data"])
            elif status == "active":  # Status check responses
                # Format status response nicely
                status_parts = ["=== REVIT STATUS ==="]
                status_parts.append("Status: {}".format(response.get("status", "Unknown")))
                status_parts.append("Health: {}".format(response.get("health", "Unknown")))
                
                if "api_name" in response:
                    status_parts.append("API: {}".format(response["api_name"]))
                if "document_title" in response:
                    status_parts.append("Document: {}".format(response["document_title"]))
                if "revit_available" in response:
                    status_parts.append("Revit Available: {}".format(response["revit_available"]))
                
                # Add any other fields that might be present
                known_fields = {"status", "health", "api_name", "document_title", "revit_available"}
                other_fields = set(response.keys()) - known_fields
                if other_fields:
                    status_parts.append("")
                    for field in sorted(other_fields):
                        status_parts.append("{}: {}".format(field.replace("_", " ").title(), response[field]))
                
                return "\n".join(status_parts)
            else:
                import json
                return json.dumps(response, indent=2)
        else:
            # Error case - provide verbose debugging information
            error_msg = response.get("error", "Unknown error occurred")
            traceback_info = response.get("traceback", "")
            details = response.get("details", "")
            status = response.get("status", "unknown")
            
            # Build comprehensive error message
            error_parts = ["=== ERROR DETAILS ==="]
            error_parts.append("Status: {}".format(status))
            error_parts.append("Error: {}".format(error_msg))
            
            if details:
                error_parts.append("Details: {}".format(details))
            
            if traceback_info:  # Code execution error with traceback
                error_parts.append("\n=== TRACEBACK ===")
                error_parts.append(traceback_info)
            
            # Add any additional fields that might be helpful for debugging
            debug_fields = ["code_attempted", "endpoint", "request_data", "response_code"]
            for field in debug_fields:
                if field in response:
                    error_parts.append("{}: {}".format(field.replace("_", " ").title(), response[field]))
            
            # Include full response for debugging if it has unexpected fields
            response_keys = set(response.keys()) - {"error", "traceback", "details", "status", "code_attempted", "endpoint", "request_data", "response_code"}
            if response_keys:
                error_parts.append("\n=== ADDITIONAL RESPONSE DATA ===")
                for key in sorted(response_keys):
                    error_parts.append("{}: {}".format(key, response[key]))
            
            return "\n".join(error_parts)
    else:
        # If response is already a string (error case from _revit_call)
        return str(response)
