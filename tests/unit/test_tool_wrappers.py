# -*- coding: utf-8 -*-
"""Tests for other tool module wrappers — verify endpoints and payloads."""
import pytest
from unittest.mock import AsyncMock
from tools.status_tools import register_status_tools
from tools.model_tools import register_model_tools
from tools.view_tools import register_view_tools
from tools.family_tools import register_family_tools
from tools.colors_tools import register_colors_tools
from tools.code_execution_tools import register_code_execution_tools
from tools.element_operations_tools import register_element_operations_tools
from tools.workset_tools import register_workset_tools


# ---- Status tools ----

class TestStatusTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get):
        mock_revit_get.return_value = {"status": "active", "health": "healthy"}
        register_status_tools(mock_mcp, mock_revit_get)
        self.tools = mock_mcp.tools
        self.mock_get = mock_revit_get

    async def test_get_revit_status(self):
        await self.tools["get_revit_status"](ctx=None)
        self.mock_get.assert_called_once_with("/status/", None, timeout=10.0)

    async def test_get_revit_model_info(self):
        await self.tools["get_revit_model_info"](ctx=None)
        self.mock_get.assert_called_once_with("/model_info/", None)


# ---- Model tools ----

class TestModelTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get):
        mock_revit_get.return_value = {
            "status": "success",
            "levels": [{"name": "Level 1"}],
        }
        register_model_tools(mock_mcp, mock_revit_get)
        self.tools = mock_mcp.tools
        self.mock_get = mock_revit_get

    async def test_list_levels(self):
        await self.tools["list_levels"](ctx=None)
        self.mock_get.assert_called_once_with("/list_levels/", None)


# ---- View tools ----

class TestViewTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image):
        mock_revit_get.return_value = {"status": "success", "data": []}
        mock_revit_post.return_value = {"status": "success", "elements": []}
        register_view_tools(mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image)
        self.tools = mock_mcp.tools
        self.mock_get = mock_revit_get
        self.mock_post = mock_revit_post
        self.mock_image = mock_revit_image

    async def test_list_revit_views(self):
        await self.tools["list_revit_views"](ctx=None)
        self.mock_get.assert_called_once_with("/list_views/", None)

    async def test_get_revit_view(self):
        await self.tools["get_revit_view"](view_name="Level 1", ctx=None)
        self.mock_image.assert_called_once_with("/get_view/Level 1", None)

    async def test_get_current_view_info(self):
        await self.tools["get_current_view_info"](ctx=None)
        self.mock_get.assert_called_once_with("/current_view_info/", None)

    async def test_get_current_view_elements(self):
        await self.tools["get_current_view_elements"](ctx=None)
        self.mock_post.assert_called_once_with(
            "/current_view_elements/",
            {"limit": 5000, "include_levels": False, "include_location": False},
            None,
        )


# ---- Family tools ----

class TestFamilyTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_get.return_value = {"status": "success", "data": []}
        mock_revit_post.return_value = {"status": "success", "message": "OK"}
        register_family_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tools = mock_mcp.tools
        self.mock_get = mock_revit_get
        self.mock_post = mock_revit_post

    async def test_list_families_default(self):
        await self.tools["list_families"](ctx=None)
        self.mock_post.assert_called_once_with(
            "/list_families/", {"limit": 50}, None
        )

    async def test_list_families_with_filter(self):
        await self.tools["list_families"](contains="Door", limit=10, ctx=None)
        call_data = self.mock_post.call_args[0][1]
        assert call_data["contains"] == "Door"
        assert call_data["limit"] == 10

    async def test_list_family_categories(self):
        await self.tools["list_family_categories"](ctx=None)
        self.mock_get.assert_called_once_with("/list_family_categories/", None)

    async def test_place_family(self):
        await self.tools["place_family"](
            family_name="Basic Wall",
            type_name="Generic - 200mm",
            x=1.0, y=2.0, z=0.0,
            rotation=90.0,
            level_name="Level 1",
            ctx=None,
        )
        call_data = self.mock_post.call_args[0][1]
        assert call_data["family_name"] == "Basic Wall"
        assert call_data["type_name"] == "Generic - 200mm"
        assert call_data["location"] == {"x": 1.0, "y": 2.0, "z": 0.0}
        assert call_data["rotation"] == 90.0
        assert call_data["level_name"] == "Level 1"
        assert call_data["properties"] == {}

    async def test_place_family_with_properties(self):
        await self.tools["place_family"](
            family_name="Door",
            properties={"Width": 1.0},
            ctx=None,
        )
        call_data = self.mock_post.call_args[0][1]
        assert call_data["properties"] == {"Width": 1.0}


# ---- Color tools ----

class TestColorTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image):
        mock_revit_post.return_value = {"status": "success", "message": "OK"}
        register_colors_tools(mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image)
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_color_splash(self):
        await self.tools["color_splash"](
            category_name="Walls", parameter_name="Type Name", ctx=None
        )
        call_data = self.mock_post.call_args[0][1]
        assert call_data["category_name"] == "Walls"
        assert call_data["parameter_name"] == "Type Name"
        assert call_data["use_gradient"] is False
        assert "custom_colors" not in call_data

    async def test_color_splash_with_options(self):
        await self.tools["color_splash"](
            category_name="Doors",
            parameter_name="Mark",
            use_gradient=True,
            custom_colors=["#FF0000", "#00FF00"],
            ctx=None,
        )
        call_data = self.mock_post.call_args[0][1]
        assert call_data["use_gradient"] is True
        assert call_data["custom_colors"] == ["#FF0000", "#00FF00"]

    async def test_clear_colors(self):
        await self.tools["clear_colors"](category_name="Walls", ctx=None)
        self.mock_post.assert_called_once_with(
            "/clear_colors/", {"category_name": "Walls"}, None
        )

    async def test_list_category_parameters(self):
        await self.tools["list_category_parameters"](
            category_name="Walls", ctx=None
        )
        self.mock_post.assert_called_once_with(
            "/list_category_parameters/", {"category_name": "Walls"}, None
        )


# ---- Code execution tools ----

class TestCodeExecutionTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image):
        mock_revit_post.return_value = {
            "status": "success",
            "output": "hello",
        }
        register_code_execution_tools(
            mock_mcp, mock_revit_get, mock_revit_post, mock_revit_image
        )
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_execute_code(self):
        result = await self.tools["execute_revit_code"](
            code="print('hello')", ctx=None
        )
        self.mock_post.assert_called_once_with(
            "/execute_code/",
            {"code": "print('hello')", "description": "Code execution"},
            None,
            timeout=60.0,
        )
        assert result == "hello"

    async def test_execute_code_custom_description(self):
        await self.tools["execute_revit_code"](
            code="x = 1", description="Set x", ctx=None
        )
        call_data = self.mock_post.call_args[0][1]
        assert call_data["description"] == "Set x"

    async def test_execute_code_connection_error(self):
        self.mock_post.side_effect = ConnectionError("refused")
        result = await self.tools["execute_revit_code"](
            code="print(1)", ctx=None
        )
        assert "Error during code execution" in result


# ---- Element-operations tool ----

class TestElementOperationsTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success", "action": "select"}
        register_element_operations_tools(
            mock_mcp, mock_revit_get, mock_revit_post
        )
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_select_payload(self):
        await self.tools["operate_element"](
            action="select", element_ids=[101, 202], ctx=None
        )
        endpoint, payload = self.mock_post.call_args[0][0], self.mock_post.call_args[0][1]
        assert endpoint == "/operate_element/"
        assert payload["action"] == "select"
        assert payload["element_ids"] == [101, 202]
        assert payload["confirm"] is False

    async def test_set_color_includes_color(self):
        await self.tools["operate_element"](
            action="set_color", element_ids=[1], color=[255, 0, 0], ctx=None
        )
        payload = self.mock_post.call_args[0][1]
        assert payload["color"] == [255, 0, 0]
        assert "transparency" not in payload

    async def test_set_transparency_includes_transparency(self):
        await self.tools["operate_element"](
            action="set_transparency", element_ids=[1], transparency=60, ctx=None
        )
        payload = self.mock_post.call_args[0][1]
        assert payload["transparency"] == 60
        assert "color" not in payload

    async def test_delete_confirm_passed_through(self):
        await self.tools["operate_element"](
            action="delete", element_ids=[7], confirm=True, ctx=None
        )
        payload = self.mock_post.call_args[0][1]
        assert payload["action"] == "delete"
        assert payload["confirm"] is True

    async def test_delete_defaults_to_preview(self):
        await self.tools["operate_element"](
            action="delete", element_ids=[7], ctx=None
        )
        payload = self.mock_post.call_args[0][1]
        assert payload["confirm"] is False

    async def test_reset_isolate_omits_element_ids(self):
        await self.tools["operate_element"](action="reset_isolate", ctx=None)
        payload = self.mock_post.call_args[0][1]
        assert payload["action"] == "reset_isolate"
        assert "element_ids" not in payload

    async def test_view_id_coerced_to_int(self):
        await self.tools["operate_element"](
            action="hide", element_ids=[1], view_id=42, ctx=None
        )
        payload = self.mock_post.call_args[0][1]
        assert payload["view_id"] == 42


# ---- Workset tool ----

class TestWorksetTools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {
            "status": "success",
            "workset": {"id": 1, "name": "Shell & Core"},
        }
        register_workset_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_create_workset_payload(self):
        await self.tools["create_workset"](name="Shell & Core", ctx=None)
        endpoint, payload = (
            self.mock_post.call_args[0][0],
            self.mock_post.call_args[0][1],
        )
        assert endpoint == "/create_workset/"
        assert payload == {"name": "Shell & Core"}
