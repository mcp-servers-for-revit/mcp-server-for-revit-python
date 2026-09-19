# -*- coding: utf-8 -*-
"""Tests for selection tool wrappers — verify correct payloads."""
from tools.selection_tools import register_selection_tools


class TestGetSelectedElements:
    async def test_calls_expected_endpoint(self, mock_mcp, mock_revit_get):
        mock_revit_get.return_value = {"status": "success", "elements": []}
        register_selection_tools(mock_mcp, mock_revit_get)

        await mock_mcp.tools["get_selected_elements"](ctx=None)

        mock_revit_get.assert_called_once_with("/selected_elements/", None)
