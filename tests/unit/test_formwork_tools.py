# -*- coding: utf-8 -*-
"""Tests for the formwork (encofrado) tool wrapper — verify endpoint and payload."""
from tools.formwork_tools import register_formwork_tools


class TestFormworkTools:
    def setup_method(self):
        self._setup_done = False

    def _setup(self, mock_mcp, mock_revit_post):
        mock_revit_post.return_value = {
            "status": "success",
            "elements": [],
            "category_totals": {},
            "panels_created": 0,
        }
        register_formwork_tools(mock_mcp, revit_get=None, revit_post=mock_revit_post)
        return mock_mcp.tools

    async def test_generate_formwork_defaults(self, mock_mcp, mock_revit_post):
        tools = self._setup(mock_mcp, mock_revit_post)
        await tools["generate_formwork"](ctx=None)

        mock_revit_post.assert_called_once()
        args, kwargs = mock_revit_post.call_args
        assert args[0] == "/generate_formwork/"
        call_data = args[1]
        assert call_data["scope"] == "model"
        assert call_data["formwork_material"] == ""
        assert call_data["panel_thickness_mm"] == 18.0
        assert call_data["contact_tolerance_mm"] == 5.0
        assert call_data["exclude_top_faces"] is True
        assert call_data["exclude_foundation_bottom"] is True
        assert call_data["create_geometry"] is True
        assert call_data["dry_run"] is False
        assert "categories" not in call_data
        assert "formwork_materials" not in call_data
        assert "element_ids" not in call_data
        assert kwargs["timeout"] == 120.0

    async def test_generate_formwork_dry_run_with_categories(self, mock_mcp, mock_revit_post):
        tools = self._setup(mock_mcp, mock_revit_post)
        await tools["generate_formwork"](
            categories=["columns", "beams"],
            dry_run=True,
            ctx=None,
        )
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["categories"] == ["columns", "beams"]
        assert call_data["dry_run"] is True

    async def test_generate_formwork_selection_scope(self, mock_mcp, mock_revit_post):
        tools = self._setup(mock_mcp, mock_revit_post)
        await tools["generate_formwork"](
            scope="selection",
            element_ids=[111, 222],
            ctx=None,
        )
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["scope"] == "selection"
        assert call_data["element_ids"] == [111, 222]

    async def test_generate_formwork_per_category_formwork_materials(
        self, mock_mcp, mock_revit_post
    ):
        tools = self._setup(mock_mcp, mock_revit_post)
        await tools["generate_formwork"](
            formwork_materials={"beams": "Metalico", "columns": "Aluminio"},
            ctx=None,
        )
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["formwork_materials"] == {
            "beams": "Metalico",
            "columns": "Aluminio",
        }

    async def test_generate_formwork_soil_options(self, mock_mcp, mock_revit_post):
        tools = self._setup(mock_mcp, mock_revit_post)
        await tools["generate_formwork"](ctx=None)
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["pour_against_soil"] is True
        assert "ground_level_name" not in call_data
        assert "ground_level_m" not in call_data

        await tools["generate_formwork"](
            pour_against_soil=False, ground_level_name="NTN. +-0.00", ground_level_m=1.2, ctx=None
        )
        call_data = mock_revit_post.call_args[0][1]
        assert call_data["pour_against_soil"] is False
        assert call_data["ground_level_name"] == "NTN. +-0.00"
        assert call_data["ground_level_m"] == 1.2
