# -*- coding: utf-8 -*-
"""Compatibility shims for pyRevit Routes versions.

Some pyRevit releases (e.g. 7.0) build the route Request without copying the
incoming HTTP headers, so ``request.headers`` is always empty. Pairing
authentication and the Origin check in ``security.validate_request`` depend on
those headers, so copy them onto the Request when pyRevit has not.

pyRevit starts the Routes server only after extension startup scripts have run,
so the shim is applied to the live server's handler class once Revit is idle.
"""

# Stop waiting for a Routes server (e.g. when it is disabled in pyRevit settings)
MAX_IDLING_ATTEMPTS = 500


def install_request_headers_shim(handler_class):
    """Patch a pyRevit HTTP handler class so route requests carry the HTTP headers."""
    original = handler_class._prepare_request
    if getattr(original, "_revit_mcp_headers_shim", False):
        return

    def _prepare_request(self, *args, **kwargs):
        request = original(self, *args, **kwargs)
        if not request.headers and self.headers is not None:
            for key, value in self.headers.items():
                request.add_header(key, value)
        return request

    _prepare_request._revit_mcp_headers_shim = True
    handler_class._prepare_request = _prepare_request


def get_live_handler_class():
    """Return the request handler class of the running Routes server, if any."""
    from pyrevit.coreutils import envvars
    routes_server = envvars.get_pyrevit_env_var(envvars.ROUTES_SERVER)
    http_server = getattr(routes_server, "server", None)
    return getattr(http_server, "RequestHandlerClass", None)


def install_when_server_starts(uiapp, logger=None):
    """Apply the shim to the live Routes server on Revit's first idle after it starts."""
    state = {"attempts": 0}

    def on_idling(sender, args):
        state["attempts"] += 1
        try:
            handler_class = get_live_handler_class()
            if handler_class is not None:
                install_request_headers_shim(handler_class)
            elif state["attempts"] < MAX_IDLING_ATTEMPTS:
                return
            elif logger:
                logger.warning("Routes server not found; MCP request headers shim not installed")
        except Exception as ex:
            if logger:
                logger.error("Failed to install MCP request headers shim: %s", str(ex))
        uiapp.Idling -= on_idling

    uiapp.Idling += on_idling
