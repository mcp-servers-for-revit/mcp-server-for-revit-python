# -*- coding: utf-8 -*-
"""Tests for annotation tool wrappers — verify correct payloads."""
from tools.annotation_tools import register_annotation_tools


class TestTagWalls:
    async def test_default_payload(self, mock_mcp, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "tagged_count": 0}
        register_annotation_tools(mock_mcp, mock_revit_post)

        await mock_mcp.tools["tag_walls"](ctx=None)

        mock_revit_post.assert_called_once_with(
            "/tag_walls/", {"tag_type_name": None}, None
        )

    async def test_custom_tag_type(self, mock_mcp, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "tagged_count": 0}
        register_annotation_tools(mock_mcp, mock_revit_post)

        await mock_mcp.tools["tag_walls"](tag_type_name="Wall Tag : Standard", ctx=None)

        call_data = mock_revit_post.call_args[0][1]
        assert call_data["tag_type_name"] == "Wall Tag : Standard"
