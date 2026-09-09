# -*- coding: utf-8 -*-
import os
import sys
import asyncio
import httpx
import anyio
from mcp.server.fastmcp import FastMCP, Image, Context
import base64
from typing import Optional, Dict, Any, List, Union

# Create a generic MCP server for interacting with Revit
# Use stateless_http=True and json_response=True for better compatibility
mcp = FastMCP(
    "Revit MCP Server", 
    host="127.0.0.1", 
    port=8000,
    stateless_http=True,
    json_response=True
)

# Configuration
# pyRevit's Routes server assigns ports per Revit instance in launch order:
# the first instance gets PORT_SCAN_START, the next one PORT_SCAN_START + 1, etc.
# The active port is therefore discovered by probing the scan range rather than
# assumed. Set the REVIT_PORT environment variable to pin a specific port.
REVIT_HOST = os.environ.get("REVIT_HOST", "127.0.0.1")
PORT_SCAN_START = int(os.environ.get("REVIT_PORT_SCAN_START", "48884"))
PORT_SCAN_COUNT = int(os.environ.get("REVIT_PORT_SCAN_COUNT", "6"))

_active_port: Optional[int] = int(os.environ["REVIT_PORT"]) if os.environ.get("REVIT_PORT") else None
# When pinned (via REVIT_PORT or select_revit_instance), never fail over to a
# different instance automatically.
_port_pinned: bool = _active_port is not None


def _base_url(port: int) -> str:
    return f"http://{REVIT_HOST}:{port}/revit_mcp"


async def _probe_port(client: httpx.AsyncClient, port: int) -> Optional[Dict[str, Any]]:
    """Probe one port for a Routes server. Returns instance info or None.

    A 503 still means a live server (Revit open with no active document),
    so any HTTP response counts as an instance.
    """
    try:
        response = await client.get(f"{_base_url(port)}/status/", timeout=2.0)
    except Exception:
        return None
    info: Dict[str, Any] = {"port": port}
    try:
        data = response.json()
        info["status"] = data.get("status", "unknown")
        info["document"] = data.get("document_title")
        if data.get("revit_version"):
            info["revit_version"] = data["revit_version"]
        if data.get("error"):
            info["error"] = data["error"]
    except Exception:
        info["status"] = f"unparseable response (HTTP {response.status_code})"
    return info


async def discover_revit_instances() -> List[Dict[str, Any]]:
    """Scan the Routes port range and return all responding Revit instances."""
    ports = range(PORT_SCAN_START, PORT_SCAN_START + PORT_SCAN_COUNT)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_probe_port(client, p) for p in ports))
    return [r for r in results if r is not None]


async def _resolve_port(rescan: bool = False) -> Optional[int]:
    """Return the port to talk to, discovering it on first use or after a failure."""
    global _active_port
    if _port_pinned or (_active_port is not None and not rescan):
        return _active_port
    instances = await discover_revit_instances()
    if instances:
        _active_port = instances[0]["port"]
    elif rescan:
        _active_port = None
    return _active_port


async def select_revit_instance(port: int) -> Dict[str, Any]:
    """Pin all subsequent requests to the Revit instance on the given port."""
    global _active_port, _port_pinned
    async with httpx.AsyncClient() as client:
        info = await _probe_port(client, port)
    if info is None:
        return {"status": "error", "message": f"No Revit Routes server is responding on port {port}."}
    _active_port = port
    _port_pinned = True
    info["message"] = f"Now using the Revit instance on port {port}."
    return info


def _no_instance_error() -> str:
    last_port = PORT_SCAN_START + PORT_SCAN_COUNT - 1
    return (
        f"Error: No Revit Routes server found on ports {PORT_SCAN_START}-{last_port}. "
        "Make sure Revit is running with the pyRevit extension loaded."
    )


async def revit_get(endpoint: str, ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple GET request to Revit API"""
    return await _revit_call("GET", endpoint, ctx=ctx, **kwargs)


async def revit_post(endpoint: str, data: Dict[str, Any], ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple POST request to Revit API"""
    return await _revit_call("POST", endpoint, data=data, ctx=ctx, **kwargs)


async def revit_image(endpoint: str, ctx: Context = None) -> Union[Image, str]:
    """GET request that returns an Image object"""
    port = await _resolve_port()
    if port is None:
        return _no_instance_error()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{_base_url(port)}{endpoint}")

            if response.status_code == 200:
                data = response.json()
                image_bytes = base64.b64decode(data["image_data"])
                return Image(data=image_bytes, format="png")
            else:
                return f"Error: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        return "Error: Image export timed out after 60 seconds."
    except Exception as e:
        msg = str(e) or type(e).__name__
        return f"Error: {msg}"


async def _revit_call(method: str, endpoint: str, data: Dict = None, ctx: Context = None,
                     timeout: float = 30.0, params: Dict = None) -> Union[Dict, str]:
    """Internal function handling all HTTP calls"""
    port = await _resolve_port()
    if port is None:
        return _no_instance_error()
    try:
        return await _revit_request(method, port, endpoint, data=data, timeout=timeout, params=params)
    except httpx.ConnectError:
        # The cached port went stale (Revit closed/relaunched on another port).
        # Rediscover once and retry, unless the port is pinned.
        if not _port_pinned:
            port = await _resolve_port(rescan=True)
            if port is None:
                return _no_instance_error()
            try:
                return await _revit_request(method, port, endpoint, data=data, timeout=timeout, params=params)
            except Exception as e:
                msg = str(e) or type(e).__name__
                return f"Error: {msg}"
        return f"Error: Could not connect to the Revit instance on port {port} (port is pinned)."
    except httpx.TimeoutException:
        return f"Error: Request timed out after {timeout} seconds. The operation may still be running in Revit."
    except Exception as e:
        msg = str(e) or type(e).__name__
        return f"Error: {msg}"


async def _revit_request(method: str, port: int, endpoint: str, data: Dict = None,
                        timeout: float = 30.0, params: Dict = None) -> Union[Dict, str]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        url = f"{_base_url(port)}{endpoint}"

        if method == "GET":
            response = await client.get(url, params=params)
        else:  # POST
            response = await client.post(url, json=data, headers={"Content-Type": "application/json"})

        return response.json() if response.status_code == 200 else f"Error: {response.status_code} - {response.text}"


# Register all tools BEFORE the main block
from tools import register_tools
register_tools(
    mcp, revit_get, revit_post, revit_image,
    instance_api={
        "discover_instances": discover_revit_instances,
        "select_instance": select_revit_instance,
        "get_active_port": lambda: _active_port,
    },
)


async def run_combined_async():
    """Run server with both SSE and streamable-http endpoints.

    This allows clients to connect via either:
    - SSE: GET /sse, POST /messages/
    - Streamable-HTTP: POST/GET /mcp
    """
    import uvicorn

    # Get the streamable-http app first - it has the proper lifespan
    # that initializes the session manager's task group
    http_app = mcp.streamable_http_app()

    # Get SSE routes (SSE doesn't need special lifespan - it creates
    # task groups per-request in connect_sse())
    sse_app = mcp.sse_app()

    # Add SSE routes to the http app (preserving its lifespan)
    for route in sse_app.routes:
        http_app.routes.append(route)

    config = uvicorn.Config(
        http_app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    transport = "stdio"

    if "--sse" in sys.argv:
        transport = "sse"
    elif "--http" in sys.argv or "--streamable-http" in sys.argv:
        transport = "streamable-http"
    elif "--combined" in sys.argv:
        # Run both SSE and streamable-http transports simultaneously
        print("Starting combined server with SSE (/sse, /messages/) and streamable-http (/mcp) endpoints...")
        anyio.run(run_combined_async)
        sys.exit(0)

    mcp.run(transport=transport)