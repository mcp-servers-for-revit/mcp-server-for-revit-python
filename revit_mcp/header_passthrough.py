# -*- coding: utf-8 -*-
"""Pass incoming HTTP headers through to pyRevit route requests.

Some pyRevit releases (seen in 7.0.0) build each ``base.Request`` without
copying the HTTP headers, so ``request.headers`` is always empty and pairing
(``revit_mcp.pairing.validate_request``) can never succeed. This wraps the
routes handler's ``_prepare_request`` to copy the headers onto the request only
when pyRevit left them empty. Authentication itself is unchanged.

pyRevit stops the routes server at session start, runs extension startup
scripts, and only then starts a new server, possibly in another script
engine. So the class is patched at load time and again, once the server exists,
through the live server object kept in pyRevit's shared registry.
"""
import logging

logger = logging.getLogger(__name__)

_MARKER = "_revit_mcp_header_passthrough"


def _copy_headers(source, request):
    """Copy headers from the HTTP handler's message onto a route request."""
    if source is None or getattr(request, "headers", None):
        return
    for key in source.keys():
        value = source.get(key)
        if value is not None:
            request.add_header(key, value)


def _wrap(original):
    def _prepare_request(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        try:
            _copy_headers(getattr(self, "headers", None), request)
        except Exception as error:  # never break request handling
            logger.debug("Header passthrough failed: %s", error)
        return request

    setattr(_prepare_request, _MARKER, True)
    return _prepare_request


def patch_handler_class(handler_class):
    """Patch one handler class. Returns True if the class is now patched."""
    original = getattr(handler_class, "_prepare_request", None)
    if original is None:
        return False
    if getattr(original, _MARKER, False):
        return True
    handler_class._prepare_request = _wrap(original)
    return True


def _active_handler_class():
    from pyrevit.routes import server as routes_server
    active = routes_server.get_active_server()
    if active is None:
        return None
    return getattr(getattr(active, "server", None), "RequestHandlerClass", None)


def patch_active_server():
    """Patch the running server's handler class, if a server is running."""
    try:
        handler_class = _active_handler_class()
    except Exception as error:
        logger.debug("Could not reach the active routes server: %s", error)
        return False
    return handler_class is not None and patch_handler_class(handler_class)


def install(uiapp=None):
    """Patch now and, if the server is not up yet, on Revit's next idle."""
    try:
        from pyrevit.routes.server import server as server_module
        patch_handler_class(server_module.HttpRequestHandler)
    except Exception as error:
        logger.debug("Could not patch routes handler class: %s", error)

    if patch_active_server() or uiapp is None:
        return

    def _on_idling(sender, args):
        if patch_active_server():
            try:
                uiapp.Idling -= _on_idling
            except Exception:
                pass

    try:
        uiapp.Idling += _on_idling
    except Exception as error:
        logger.debug("Could not subscribe to Idling: %s", error)
