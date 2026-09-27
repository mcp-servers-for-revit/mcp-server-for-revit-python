# -*- coding: UTF-8 -*-
"""
Keeps the pyRevit Routes server from crashing Revit.

When a request thread fails (typically: the client dropped the connection
before its response was written), SocketServer's default `handle_error`
*prints* the traceback. In the engine that runs the server, stdout is
pyRevit's output window, which opens on the first write - and opening a
WPF window from that non-STA server thread throws an unhandled
InvalidOperationException that terminates Revit.

The server only exists once pyRevit finishes loading (after extension
startup scripts), so `install()` patches it on Revit's first Idling event.
"""
import datetime
import os
import tempfile
import traceback

ERROR_LOG = os.path.join(tempfile.gettempdir(), "revit_mcp_routes_errors.log")


def _quiet_handle_error(request, client_address):
    """Replacement for SocketServer.handle_error: never prints, only
    appends to ERROR_LOG."""
    try:
        with open(ERROR_LOG, "a") as f:
            f.write(
                "{} {}\n{}\n".format(
                    datetime.datetime.now(), client_address, traceback.format_exc()
                )
            )
    except Exception:
        pass


def harden_active_server():
    """Patch the running Routes server; True once it is patched."""
    from pyrevit import routes

    http_server = getattr(routes.get_active_server(), "server", None)
    if http_server is None:
        return False
    if not getattr(http_server, "_revit_mcp_quiet_errors", False):
        http_server.handle_error = _quiet_handle_error
        http_server._revit_mcp_quiet_errors = True
    return True


def install(uiapp):
    """Patch the server on the first Idling event where it exists."""

    def on_idling(sender, args):
        try:
            done = harden_active_server()
        except Exception:
            done = True  # never retry every idle tick on an unexpected error
        if done:
            uiapp.Idling -= on_idling

    uiapp.Idling += on_idling
