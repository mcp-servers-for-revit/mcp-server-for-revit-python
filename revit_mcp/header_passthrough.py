# -*- coding: utf-8 -*-
"""Forward incoming HTTP headers to pyRevit Routes handlers.

pyRevit's Routes server (observed in 6.5.x) builds ``base.Request`` objects
without copying the HTTP request headers, so ``request.headers`` is always
empty. The pairing check in ``security.authorize_request`` needs the
``Authorization`` header (and the ``Origin`` header to refuse browsers), so
without this every request is rejected with 401.

pyRevit runs extension startup scripts in their own engine *before* it creates
the Routes server, and shares the live server through pyRevit env vars. So the
patch must target the live server's ``RequestHandlerClass``, which only exists
after session load; ``install_deferred_patch`` waits for Revit's first idle.

This does not relax any check: requests still need the paired bearer token,
and browser requests carrying ``Origin`` are still refused.
"""
import logging

logger = logging.getLogger(__name__)

_PATCH_FLAG = "_revit_mcp_header_passthrough"
# Revit idles many times per second; give up after roughly a minute of idling
# when the Routes server is disabled so the handler does not linger.
_MAX_IDLE_ATTEMPTS = 2000


def _iter_headers(raw_headers):
    """Yield (name, value) pairs from py2 mimetools or py3 email messages."""
    if raw_headers is None:
        return
    items = getattr(raw_headers, "items", None)
    if items is None:
        return
    for key, value in items():
        yield key, value


def apply_header_passthrough(handler_class):
    """Patch a Routes request handler class once; return True if headers flow."""
    if handler_class is None:
        return False
    if getattr(handler_class, _PATCH_FLAG, False):
        return True

    original = getattr(handler_class, "_prepare_request", None)
    if original is None:
        return False

    def _prepare_request(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        # Runs on the HTTP server thread: never log or print here, because
        # pyRevit routes output to a WPF window and Revit crashes when that
        # window is created off the UI thread. On any failure headers stay
        # empty and the pairing check fails closed (401).
        try:
            existing = getattr(request, "headers", None)
            # Only fill in when pyRevit did not already provide headers.
            if not existing and hasattr(request, "add_header"):
                for key, value in _iter_headers(getattr(self, "headers", None)):
                    request.add_header(key, value)
        except Exception:
            pass
        return request

    handler_class._prepare_request = _prepare_request
    setattr(handler_class, _PATCH_FLAG, True)
    return True


def patch_active_server():
    """Patch the live Routes server's handler class; False if none is running."""
    try:
        from pyrevit.routes.server import get_active_server
    except Exception:
        return False
    routes_server = get_active_server()
    http_server = getattr(routes_server, "server", None)
    handler_class = getattr(http_server, "RequestHandlerClass", None)
    return apply_header_passthrough(handler_class)


def install_deferred_patch(uiapp):
    """Patch the live Routes server on Revit's first idle after session load."""
    state = {"attempts": 0}

    def on_idling(sender, args):
        state["attempts"] += 1
        done = False
        try:
            done = patch_active_server()
            if done:
                logger.info("Revit MCP: Routes header pass-through enabled.")
        except Exception as error:
            logger.warning("Revit MCP: cannot patch Routes headers: %s", error)
            done = True
        if done or state["attempts"] >= _MAX_IDLE_ATTEMPTS:
            try:
                uiapp.Idling -= on_idling
            except Exception:
                pass

    uiapp.Idling += on_idling
    return on_idling
