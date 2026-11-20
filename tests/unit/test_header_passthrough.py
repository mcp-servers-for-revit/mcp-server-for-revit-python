# -*- coding: utf-8 -*-
"""Header passthrough for pyRevit builds whose routes drop request headers."""
import sys
from email.message import Message
from types import SimpleNamespace

from revit_mcp import header_passthrough
from revit_mcp.header_passthrough import patch_active_server, patch_handler_class


class FakeRequest(object):
    def __init__(self, headers=None):
        self._headers = dict(headers or {})

    @property
    def headers(self):
        return self._headers

    def add_header(self, key, value):
        self._headers[key] = value


def make_handler_class(prepared_headers=None):
    class Handler(object):
        def __init__(self, headers):
            self.headers = headers

        def _prepare_request(self, route, path, method, query_string=None):
            return FakeRequest(prepared_headers)

    return Handler


def http_headers(**values):
    message = Message()
    for key, value in values.items():
        message[key.replace("_", "-")] = value
    return message


def test_copies_headers_when_pyrevit_drops_them():
    handler_class = make_handler_class()
    assert patch_handler_class(handler_class)

    handler = handler_class(http_headers(Authorization="Bearer abc", Host="127.0.0.1"))
    request = handler._prepare_request("route", "/status/", "GET")

    normalized = dict((k.lower(), v) for k, v in request.headers.items())
    assert normalized["authorization"] == "Bearer abc"


def test_leaves_headers_alone_when_pyrevit_provides_them():
    handler_class = make_handler_class({"authorization": "from-pyrevit"})
    patch_handler_class(handler_class)

    handler = handler_class(http_headers(Authorization="Bearer other"))
    request = handler._prepare_request("route", "/status/", "GET")

    assert request.headers == {"authorization": "from-pyrevit"}


def test_patching_twice_wraps_once():
    handler_class = make_handler_class()
    patch_handler_class(handler_class)
    first = handler_class.__dict__["_prepare_request"]
    patch_handler_class(handler_class)
    assert handler_class.__dict__["_prepare_request"] is first


def test_origin_header_reaches_validation(monkeypatch):
    from revit_mcp import pairing

    monkeypatch.setattr(pairing, "get_token", lambda: "a" * 64)
    handler_class = make_handler_class()
    patch_handler_class(handler_class)

    good = handler_class(http_headers(Authorization="Bearer " + "a" * 64))
    assert pairing.validate_request(good._prepare_request("r", "/", "GET").headers)

    browser = handler_class(http_headers(Authorization="Bearer " + "a" * 64, Origin="https://x"))
    assert not pairing.validate_request(browser._prepare_request("r", "/", "GET").headers)

    missing = handler_class(http_headers(Host="127.0.0.1"))
    assert not pairing.validate_request(missing._prepare_request("r", "/", "GET").headers)


def test_patch_active_server_uses_live_handler_class(monkeypatch):
    handler_class = make_handler_class()
    active = SimpleNamespace(server=SimpleNamespace(RequestHandlerClass=handler_class))
    server_pkg = SimpleNamespace(get_active_server=lambda: active)
    routes_pkg = SimpleNamespace(server=server_pkg)
    monkeypatch.setitem(sys.modules, "pyrevit", SimpleNamespace(routes=routes_pkg))
    monkeypatch.setitem(sys.modules, "pyrevit.routes", routes_pkg)
    monkeypatch.setitem(sys.modules, "pyrevit.routes.server", server_pkg)

    assert patch_active_server()
    assert getattr(handler_class._prepare_request, header_passthrough._MARKER)


def test_patch_active_server_without_server(monkeypatch):
    server_pkg = SimpleNamespace(get_active_server=lambda: None)
    routes_pkg = SimpleNamespace(server=server_pkg)
    monkeypatch.setitem(sys.modules, "pyrevit", SimpleNamespace(routes=routes_pkg))
    monkeypatch.setitem(sys.modules, "pyrevit.routes", routes_pkg)
    monkeypatch.setitem(sys.modules, "pyrevit.routes.server", server_pkg)

    assert not patch_active_server()
