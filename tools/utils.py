# -*- coding: utf-8 -*-
"""Utility functions for MCP tools"""

import json


# Statuses that are valid, expected, recoverable outcomes -- NOT errors.
#
# A tool returns one of these when it ran correctly but the result is
# something other than a plain success: an empty query result, a dry-run or
# confirm-gate preview, an unmet precondition the caller can fix, or a lookup
# that did not resolve. They must not be rendered with the
# "=== ERROR DETAILS ===" / "=== TRACEBACK ===" framing -- that misleads
# callers into treating a normal branch as a crash, and the generic
# "Unknown error occurred" fallback discards the structured data these
# responses carry (applied_filters, existing_rooms_on_level, ...).
#
# Only TOP-LEVEL statuses belong here; per-item statuses nested inside a
# results/preview list (e.g. modify_element's per-parameter "read_only") never
# reach format_response().
RECOVERABLE_STATUSES = frozenset({
    # Preview / dry-run outcomes -- confirm-gate previews and --dry_run runs
    "preview", "dry_run", "no_op",
    # Empty-result outcomes -- the tool ran fine, nothing matched the query
    "no_walls", "no_axis_aligned_walls", "no_references_found",
    "no_elements_found", "no_rooms_found", "no_rooms_in_view",
    # Unmet preconditions -- the caller can adjust inputs and retry
    "not_in_enclosed_area", "area_already_occupied", "name_collision",
    "view_not_supported", "no_room_tag_family",
    # Lookup misses -- a supplied id or name did not resolve
    "view_not_found", "level_not_found", "phase_not_found",
    # Revit is running but no document is open
    "active_no_document",
    # Generic non-error signals
    "ok", "warning",
})


def _format_recoverable(response):
    """Render a recoverable, non-error status without alarming error framing.

    See RECOVERABLE_STATUSES. The human-readable note may live under either
    "message" or "error"; any remaining fields are emitted as a JSON block so
    callers can still consume the structured data the response carries.

    Args:
        response: A dict response whose "status" is in RECOVERABLE_STATUSES.

    Returns:
        str: A neutrally formatted, multi-line summary.
    """
    status = response.get("status", "unknown")
    parts = ["=== {} ===".format(str(status).replace("_", " ").upper())]

    note = response.get("message") or response.get("error") or ""
    if note:
        parts.append(note)

    details = response.get("details", "")
    if details:
        parts.append("Details: {}".format(details))

    extra = {k: v for k, v in response.items()
             if k not in ("status", "message", "error", "details")}
    if extra:
        parts.append("")
        parts.append(json.dumps(extra, indent=2, default=str, sort_keys=True))

    return "\n".join(parts)


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
                return json.dumps(response, indent=2)
        elif status in RECOVERABLE_STATUSES:
            # Documented, expected, non-error outcome -- render it neutrally
            # instead of dressing it up as a crash. See RECOVERABLE_STATUSES.
            return _format_recoverable(response)
        else:
            # Error case - provide verbose debugging information
            error_msg = (response.get("error") or
                         response.get("message") or
                         "Unknown error occurred")
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
            response_keys = set(response.keys()) - {"error", "message", "traceback", "details", "status", "code_attempted", "endpoint", "request_data", "response_code"}
            if response_keys:
                error_parts.append("\n=== ADDITIONAL RESPONSE DATA ===")
                for key in sorted(response_keys):
                    error_parts.append("{}: {}".format(key, response[key]))

            return "\n".join(error_parts)
    else:
        # If response is already a string (error case from _revit_call)
        return str(response)
