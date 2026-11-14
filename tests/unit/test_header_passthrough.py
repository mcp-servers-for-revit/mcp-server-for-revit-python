"""pyRevit Routes header pass-through; uses stand-ins, no Revit required."""
from email.message import Message

from revit_mcp.header_passthrough import apply_header_passthrough


class FakeRequest(object):
    def __init__(self):
        self._headers = {}

    @property
    def headers(self):
        return self._headers

    def add_header(self, key, value):
        self._headers[key] = value


def make_handler_class():
    class FakeHandler(object):
        def __init__(self, raw_headers):
            self.headers = raw_headers

        def _prepare_request(self, route, path, method, query_string=None):
            # Mirrors pyRevit 6.5.x: headers are not copied onto the request.
            return FakeRequest()

    return FakeHandler


def raw(**headers):
    message = Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return message


def test_headers_are_forwarded_to_request():
    cls = make_handler_class()
    assert apply_header_passthrough(cls)
    request = cls(raw(Authorization="Bearer abc", Content_Type="application/json"))._prepare_request("r", "/status/", "GET")
    assert request.headers["Authorization"] == "Bearer abc"
    assert request.headers["Content-Type"] == "application/json"


def test_origin_is_forwarded_so_browsers_stay_blocked():
    cls = make_handler_class()
    apply_header_passthrough(cls)
    request = cls(raw(Authorization="Bearer abc", Origin="https://evil.example"))._prepare_request("r", "/", "GET")
    assert request.headers["Origin"] == "https://evil.example"


def test_patch_is_idempotent():
    cls = make_handler_class()
    apply_header_passthrough(cls)
    first = cls._prepare_request
    apply_header_passthrough(cls)
    assert cls._prepare_request is first


def test_existing_headers_are_not_overwritten():
    cls = make_handler_class()

    def prepare_with_headers(self, *args, **kwargs):
        request = FakeRequest()
        request.add_header("Authorization", "Bearer from-pyrevit")
        return request

    cls._prepare_request = prepare_with_headers
    apply_header_passthrough(cls)
    request = cls(raw(Authorization="Bearer other"))._prepare_request("r", "/", "GET")
    assert request.headers == {"Authorization": "Bearer from-pyrevit"}


def test_missing_handler_returns_false():
    class NoPrepare(object):
        pass

    assert apply_header_passthrough(NoPrepare) is False


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


def install_fake_routes(monkeypatch, active_server):
    import sys
    import types

    pyrevit = types.ModuleType("pyrevit")
    routes = types.ModuleType("pyrevit.routes")
    server = types.ModuleType("pyrevit.routes.server")
    server.get_active_server = lambda: active_server[0]
    monkeypatch.setitem(sys.modules, "pyrevit", pyrevit)
    monkeypatch.setitem(sys.modules, "pyrevit.routes", routes)
    monkeypatch.setitem(sys.modules, "pyrevit.routes.server", server)


def test_deferred_patch_targets_live_server_and_unsubscribes(monkeypatch):
    from types import SimpleNamespace
    from revit_mcp.header_passthrough import install_deferred_patch

    cls = make_handler_class()
    active = [None]  # server not started yet when startup.py runs
    install_fake_routes(monkeypatch, active)
    uiapp = FakeUIApp()
    install_deferred_patch(uiapp)

    uiapp.Idling.fire()
    assert len(uiapp.Idling.handlers) == 1  # still waiting for the server
    assert not getattr(cls, "_revit_mcp_header_passthrough", False)

    active[0] = SimpleNamespace(server=SimpleNamespace(RequestHandlerClass=cls))
    uiapp.Idling.fire()
    assert uiapp.Idling.handlers == []
    request = cls(raw(Authorization="Bearer abc"))._prepare_request("r", "/", "GET")
    assert request.headers["Authorization"] == "Bearer abc"
