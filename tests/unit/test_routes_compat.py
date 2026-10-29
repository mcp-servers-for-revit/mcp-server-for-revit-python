"""The headers shim must expose HTTP headers to routes on pyRevit versions that drop them."""
from revit_mcp import routes_compat
from revit_mcp.routes_compat import install_request_headers_shim, install_when_server_starts


class FakeEvent(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self):
        for handler in list(self.handlers):
            handler(None, None)


class FakeUIApp(object):
    def __init__(self):
        self.Idling = FakeEvent()


class FakeRequest(object):
    def __init__(self, headers=None):
        self._headers = dict(headers or {})

    @property
    def headers(self):
        return self._headers

    def add_header(self, key, value):
        self._headers[key] = value


def _handler_class(request_headers=None):
    class Handler(object):
        headers = {"Authorization": "Bearer abc", "Origin": "https://example.com"}

        def _prepare_request(self, route, path, method, query_string=None):
            return FakeRequest(request_headers)

    return Handler


def test_shim_copies_http_headers_onto_request():
    handler_class = _handler_class()
    install_request_headers_shim(handler_class)
    request = handler_class()._prepare_request("route", "/status/", "GET")
    assert request.headers == {"Authorization": "Bearer abc", "Origin": "https://example.com"}


def test_shim_keeps_headers_pyrevit_already_set():
    handler_class = _handler_class({"Authorization": "Bearer from-pyrevit"})
    install_request_headers_shim(handler_class)
    request = handler_class()._prepare_request("route", "/status/", "GET")
    assert request.headers == {"Authorization": "Bearer from-pyrevit"}


def test_shim_installs_once():
    handler_class = _handler_class()
    install_request_headers_shim(handler_class)
    patched = handler_class._prepare_request
    install_request_headers_shim(handler_class)
    assert handler_class._prepare_request is patched


def test_waits_for_live_server_then_patches_and_unsubscribes(monkeypatch):
    handler_class = _handler_class()
    live = {"class": None}
    monkeypatch.setattr(routes_compat, "get_live_handler_class", lambda: live["class"])
    uiapp = FakeUIApp()
    install_when_server_starts(uiapp)

    uiapp.Idling.fire()
    assert uiapp.Idling.handlers
    assert not getattr(handler_class._prepare_request, "_revit_mcp_headers_shim", False)

    live["class"] = handler_class
    uiapp.Idling.fire()
    assert not uiapp.Idling.handlers
    request = handler_class()._prepare_request("route", "/status/", "GET")
    assert request.headers["Authorization"] == "Bearer abc"


def test_gives_up_when_no_server_starts(monkeypatch):
    monkeypatch.setattr(routes_compat, "get_live_handler_class", lambda: None)
    monkeypatch.setattr(routes_compat, "MAX_IDLING_ATTEMPTS", 3)
    uiapp = FakeUIApp()
    install_when_server_starts(uiapp)
    for _ in range(3):
        uiapp.Idling.fire()
    assert not uiapp.Idling.handlers
