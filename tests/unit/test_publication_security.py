"""Security boundary tests; no production pairing or Revit model is touched."""
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from revit_mcp import pairing
from tools.http_security import PairingMiddleware

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def paired_user(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows DPAPI required")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    pairing.enable_pairing()
    return pairing.get_token()


def test_dpapi_pairing_rotation_and_revocation(paired_user):
    token = paired_user
    assert token.encode() not in Path(pairing.pairing_path()).read_bytes()
    assert pairing.validate_request({"Authorization": "Bearer " + token})
    assert not pairing.validate_request({})
    assert not pairing.validate_request({"Authorization": "Bearer wrong"})
    assert not pairing.validate_request({"Authorization": "Bearer " + token, "Origin": "https://example.com"})
    assert not pairing.validate_request({"Authorization": "Bearer " + token, "ORIGIN": "null"})
    pairing.enable_pairing()
    assert pairing.get_token() != token
    assert not pairing.validate_request({"Authorization": "Bearer " + token})
    pairing.disable_pairing()
    with pytest.raises(pairing.PairingError):
        pairing.get_token()


def test_body_token_authenticates_when_headers_are_hidden(paired_user):
    assert pairing.validate_request({}, paired_user)
    assert pairing.validate_request(None, paired_user)
    assert not pairing.validate_request({}, "wrong")
    assert not pairing.validate_request({}, None)
    assert not pairing.validate_request({"Origin": "null"}, paired_user)
    # A header, when pyRevit exposes one, is authoritative over the body.
    assert not pairing.validate_request({"Authorization": "Bearer wrong"}, paired_user)
    assert pairing.authorization_body({"a": 1}) == {"a": 1, pairing.AUTH_FIELD: paired_user}


def test_corrupted_pairing_fails_closed(paired_user):
    Path(pairing.pairing_path()).write_bytes(b"corrupted")
    assert not pairing.validate_request({"Authorization": "Bearer " + paired_user})


async def test_optional_http_modes_reject_unpaired_and_browser_calls(paired_user):
    target = Mock()
    async def app(scope, receive, send):
        target()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    transport = httpx.ASGITransport(app=PairingMiddleware(app))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        for endpoint in ("/mcp", "/sse", "/messages/"):
            assert (await client.post(endpoint)).status_code == 401
            assert (await client.post(endpoint, headers={"Authorization": "Bearer bad"})).status_code == 401
            headers = {"Authorization": "Bearer " + paired_user, "Origin": "https://example.com"}
            assert (await client.post(endpoint, headers=headers)).status_code == 401
        target.assert_not_called()
        assert (await client.post("/mcp", headers=pairing.authorization_headers())).status_code == 204
        target.assert_called_once()


@pytest.fixture
def route_security(monkeypatch):
    alert = Mock(return_value=False)
    routes = SimpleNamespace(make_response=lambda data, status=200: SimpleNamespace(data=data, status=status))
    monkeypatch.setitem(sys.modules, "pyrevit", SimpleNamespace(routes=routes, forms=SimpleNamespace(alert=alert)))
    spec = importlib.util.spec_from_file_location("revit_mcp._tested_security", ROOT / "revit_mcp/security.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, routes, alert


def test_route_rejects_callback_without_dispatching_it(route_security, paired_user):
    security, _, alert = route_security
    request = SimpleNamespace(data={"callbackUrl": "https://example.com/private"}, headers=pairing.authorization_headers())
    assert security.authorize_request(request).status == 400
    assert "callbackUrl" not in request.data
    alert.assert_not_called()


def test_revit_review_decline_and_accept_show_request(route_security, paired_user):
    security, _, alert = route_security
    request = SimpleNamespace(data={"code": "print('review me')"}, headers=pairing.authorization_headers())
    assert security.authorize_request(request, "Execute Python code").status == 403
    assert "NOT sandboxed" in alert.call_args.args[0]
    assert "review me" in alert.call_args.kwargs["sub_msg"]
    assert alert.call_args.kwargs["ok"] is False
    alert.return_value = True
    assert security.authorize_request(request, "Execute Python code") is None
    request.data = "{invalid"
    assert security.authorize_request(request, "Execute Python code").status == 400


@pytest.mark.parametrize("as_json_text", [False, True])
def test_route_accepts_body_token_and_strips_it_before_review(route_security, paired_user, as_json_text):
    security, _, alert = route_security
    payload = pairing.authorization_body({"code": "print('review me')"})
    request = SimpleNamespace(data=json.dumps(payload) if as_json_text else payload, headers={})
    alert.return_value = True
    assert security.authorize_request(request, "Execute Python code") is None
    assert request.data == {"code": "print('review me')"}
    assert paired_user not in alert.call_args.kwargs["sub_msg"]


def test_route_rejects_wrong_body_token(route_security, paired_user):
    security, _, alert = route_security
    request = SimpleNamespace(data={pairing.AUTH_FIELD: "wrong"}, headers={})
    assert security.authorize_request(request).status == 401
    assert request.data == {}
    alert.assert_not_called()


def _route_functions():
    functions = []
    for path in (ROOT / "revit_mcp").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "api"
                and decorator.func.attr == "route" for decorator in node.decorator_list
            ):
                functions.append((path, node))
    return functions


@pytest.mark.parametrize("path,node", _route_functions(), ids=lambda item: getattr(item, "name", str(item)))
def test_every_route_rejects_before_model_access(path, node, route_security, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    security, routes, alert = route_security
    node.decorator_list = []
    namespace = {"authorize_request": security.authorize_request, "routes": routes}
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    request = SimpleNamespace(data={}, headers={})
    arguments = {argument.arg: None for argument in node.args.args}
    assert "request" in arguments
    arguments["request"] = request
    assert namespace[node.name](**arguments).status == 401
    alert.assert_not_called()
    if node.name in {"revit_status", "get_model_info"}:
        assert "doc" in arguments  # pyRevit must dispatch these on its API thread.


async def test_runtime_manifest_and_unpaired_tool_surface(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from main import mcp
    from scripts.export_mcp_manifest import build_manifest
    from tools.catalog import TOOL_DESCRIPTIONS
    manifest = await build_manifest()
    assert manifest == json.loads((ROOT / "mcp-tool-manifest.json").read_text(encoding="utf-8"))
    assert {tool["name"] for tool in manifest["tools"]} == set(TOOL_DESCRIPTIONS)
    # Local discovery never accesses the registry before consent.
    response = await mcp.call_tool("list_revit_installations", {})
    content = response[0] if isinstance(response, tuple) else response
    assert "Connect button" in content[0].text


async def test_revit_http_client_has_auth_and_ignores_proxies(paired_user, monkeypatch):
    import main
    original = httpx.AsyncClient
    observed = []
    def handler(request):
        observed.append(request)
        return httpx.Response(200, json={"status": "active"})
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(main.httpx, "AsyncClient", client)
    assert await main.revit_get("/status/") == {"status": "active"}
    assert await main.revit_post("/execute_code/", {"code": "1"}) == {"status": "active"}
    # pyRevit Routes hides headers from handlers: the secret must be in the body.
    assert json.loads(observed[0].content) == {pairing.AUTH_FIELD: paired_user}
    assert json.loads(observed[1].content) == {"code": "1", pairing.AUTH_FIELD: paired_user}
    for request in observed:
        assert "authorization" not in request.headers
        assert paired_user not in str(request.url)
        assert request.url.host in {"127.0.0.1", "localhost", "::1"}


@pytest.mark.parametrize("transport_name", ["sse", "streamable-http", "combined"])
async def test_real_http_apps_apply_pairing_gate(transport_name, paired_user, monkeypatch):
    import main
    from mcp.server.fastmcp import FastMCP
    from tools import register_tools
    server = FastMCP("Revit MCP Server", host="127.0.0.1", port=8000,
                     stateless_http=True, json_response=True)
    register_tools(server, main.revit_get, main.revit_post, main.revit_image)
    monkeypatch.setattr(main, "mcp", server)
    build_http_app = main.build_http_app
    app = build_http_app(transport_name)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
            for endpoint in ("/mcp", "/sse", "/messages/"):
                assert (await client.post(endpoint)).status_code == 401
            if transport_name != "sse":
                headers = dict(pairing.authorization_headers(), Accept="application/json, text/event-stream")
                payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "publication-test", "version": "1.0"}}}
                response = await client.post("/mcp", headers=headers, json=payload)
                assert response.status_code == 200
                assert response.json()["result"]["protocolVersion"] == "2025-11-25"
