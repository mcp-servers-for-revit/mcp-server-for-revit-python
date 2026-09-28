# -*- coding: utf-8 -*-
"""Tests for element management tool wrappers — verify correct payloads."""
import pytest
from tools.element_management_tools import register_element_management_tools


@pytest.fixture
def element_tools(mock_mcp, mock_revit_get, mock_revit_post):
    mock_revit_post.return_value = {"status": "success"}
    register_element_management_tools(mock_mcp, mock_revit_get, mock_revit_post)
    return mock_mcp.tools


class TestDeleteElements:
    async def test_sends_correct_payload(self, element_tools, mock_revit_post):
        await element_tools["delete_elements"](element_ids=[1, 2, 3], ctx=None)
        mock_revit_post.assert_called_once_with(
            "/delete_elements/", {"element_ids": [1, 2, 3]}, None
        )


class TestModifyElement:
    async def test_sends_correct_payload(self, element_tools, mock_revit_post):
        await element_tools["modify_element"](
            element_id=42, properties={"Comments": "hi"}, ctx=None
        )
        mock_revit_post.assert_called_once_with(
            "/modify_element/",
            {"element_id": 42, "properties": {"Comments": "hi"}},
            None,
        )


class TestResetModel:
    async def test_default_is_preview(self, element_tools, mock_revit_post):
        await element_tools["reset_model"](ctx=None)
        mock_revit_post.assert_called_once_with(
            "/reset_model/", {"confirm": False}, None
        )

    async def test_confirm_true(self, element_tools, mock_revit_post):
        await element_tools["reset_model"](confirm=True, ctx=None)
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["confirm"] is True
