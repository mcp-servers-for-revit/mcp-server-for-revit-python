# -*- coding: utf-8 -*-
"""Tests for tools.utils.format_response()"""
import json
import pytest
from tools.utils import format_response


class TestSuccessResponses:
    def test_success_with_output(self):
        result = format_response({"status": "success", "output": "hello"})
        assert result == "hello"

    def test_success_with_message(self):
        result = format_response({"status": "success", "message": "Done"})
        assert result == "Done"

    def test_success_with_result(self):
        result = format_response({"status": "success", "result": 42})
        assert result == "42"

    def test_success_with_data(self):
        result = format_response({"status": "success", "data": [1, 2]})
        assert result == "[1, 2]"

    def test_success_fallback_json(self):
        resp = {"status": "success"}
        result = format_response(resp)
        assert json.loads(result) == resp

    def test_priority_output_over_message(self):
        result = format_response(
            {"status": "success", "output": "A", "message": "B"}
        )
        assert result == "A"


class TestActiveStatusResponses:
    def test_active_healthy_status(self):
        resp = {
            "status": "active",
            "health": "healthy",
            "document_title": "Test",
        }
        result = format_response(resp)
        assert "=== REVIT STATUS ===" in result
        assert "Document: Test" in result

    def test_active_revit_available(self):
        resp = {"status": "active", "revit_available": True}
        result = format_response(resp)
        assert "=== REVIT STATUS ===" in result
        assert "Revit Available: True" in result

    def test_active_healthy_with_api_name(self):
        resp = {
            "status": "active",
            "health": "healthy",
            "api_name": "RevitMCP",
        }
        result = format_response(resp)
        assert "API: RevitMCP" in result


class TestErrorResponses:
    def test_error_response(self):
        result = format_response({"status": "error", "error": "fail"})
        assert "=== ERROR DETAILS ===" in result
        assert "Error: fail" in result

    def test_error_with_traceback(self):
        result = format_response(
            {"error": "fail", "traceback": "line 1\nline 2"}
        )
        assert "=== TRACEBACK ===" in result
        assert "line 1" in result

    def test_error_with_details(self):
        result = format_response(
            {"status": "error", "error": "fail", "details": "more info"}
        )
        assert "Details: more info" in result

    def test_error_with_debug_fields(self):
        result = format_response(
            {
                "status": "error",
                "error": "fail",
                "code_attempted": "print(1)",
                "endpoint": "/test/",
            }
        )
        assert "Code Attempted: print(1)" in result
        assert "Endpoint: /test/" in result

    def test_error_message_fallback_when_no_error_key(self):
        # An error response carrying only "message" should surface it instead
        # of the generic "Unknown error occurred" placeholder.
        result = format_response({"status": "error", "message": "boom"})
        assert "Error: boom" in result
        assert "Unknown error occurred" not in result

    def test_unlisted_failure_status_still_error_framed(self):
        # create_failed is a genuine failure -- NOT a recoverable status.
        result = format_response(
            {"status": "create_failed", "error": "Revit returned no element."}
        )
        assert "=== ERROR DETAILS ===" in result
        assert "Error: Revit returned no element." in result


class TestRecoverableResponses:
    """Recoverable statuses are expected, non-error outcomes -- they must not
    be dressed up with error/traceback framing."""

    def test_empty_result_not_error_framed(self):
        result = format_response(
            {
                "status": "no_rooms_found",
                "applied_filters": ["level=Level 1"],
                "skipped_unplaced": 2,
            }
        )
        assert "=== NO ROOMS FOUND ===" in result
        assert "=== ERROR DETAILS ===" not in result
        assert "Unknown error occurred" not in result

    def test_recoverable_preserves_structured_data(self):
        result = format_response(
            {
                "status": "no_rooms_found",
                "applied_filters": ["level=Level 1"],
                "skipped_unplaced": 2,
            }
        )
        assert "applied_filters" in result
        assert "skipped_unplaced" in result

    def test_note_read_from_error_key(self):
        # Some recoverable responses carry their explanation under "error".
        result = format_response(
            {
                "status": "area_already_occupied",
                "error": "The area is already occupied by another room.",
                "level_name": "Level 1",
            }
        )
        assert "=== ERROR DETAILS ===" not in result
        assert "The area is already occupied by another room." in result

    def test_note_read_from_message_key(self):
        result = format_response(
            {
                "status": "no_op",
                "message": "Nothing to delete in the requested categories.",
            }
        )
        assert "=== ERROR DETAILS ===" not in result
        assert "Nothing to delete in the requested categories." in result

    def test_preview_status_is_recoverable(self):
        result = format_response(
            {
                "status": "preview",
                "confirm_required": True,
                "would_delete_count": 5,
            }
        )
        assert "=== PREVIEW ===" in result
        assert "=== ERROR DETAILS ===" not in result
        assert "would_delete_count" in result

    def test_dry_run_status_is_recoverable(self):
        result = format_response({"status": "dry_run", "planned": 12})
        assert "=== DRY RUN ===" in result
        assert "=== ERROR DETAILS ===" not in result

    def test_lookup_miss_is_recoverable(self):
        result = format_response(
            {"status": "view_not_found", "error": "View 999 does not exist."}
        )
        assert "=== ERROR DETAILS ===" not in result
        assert "Unknown error occurred" not in result
        assert "View 999 does not exist." in result

    def test_recoverable_with_details(self):
        result = format_response(
            {
                "status": "view_not_supported",
                "message": "View is not a plan view.",
                "details": "Active view type is Section.",
            }
        )
        assert "Details: Active view type is Section." in result
        assert "=== ERROR DETAILS ===" not in result


class TestStringPassthrough:
    def test_string_passthrough(self):
        result = format_response("Error: connection refused")
        assert result == "Error: connection refused"

    def test_empty_string(self):
        result = format_response("")
        assert result == ""

    def test_non_string_non_dict(self):
        result = format_response(123)
        assert result == "123"
