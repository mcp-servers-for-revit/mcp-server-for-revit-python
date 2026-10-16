# -*- coding: utf-8 -*-
import os
import sys
import httpx
import anyio
from mcp.server.fastmcp import FastMCP, Image, Context
import base64
from typing import Optional, Dict, Any, Union
from revit_mcp.pairing import authorization_body
from tools.http_security import PairingMiddleware

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
# The submitted local architecture only connects to the same machine.
# Remote pyRevit Routes requires a separately reviewed authenticated HTTPS design.
REVIT_HOST = os.environ.get("REVIT_HOST", "127.0.0.1")
if REVIT_HOST not in {"127.0.0.1", "localhost", "::1"}:
    raise ValueError("REVIT_HOST must be loopback; remote HTTP bridges are not supported.")
REVIT_PORT = int(os.environ.get("REVIT_PORT", "48884"))
if not 1 <= REVIT_PORT <= 65535:
    raise ValueError("REVIT_PORT must be between 1 and 65535.")
URL_HOST = "[::1]" if REVIT_HOST == "::1" else REVIT_HOST
BASE_URL = f"http://{URL_HOST}:{REVIT_PORT}/revit_mcp"


async def revit_get(endpoint: str, ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple GET request to Revit API"""
    return await _revit_call("GET", endpoint, ctx=ctx, **kwargs)


async def revit_post(endpoint: str, data: Dict[str, Any], ctx: Context = None, **kwargs) -> Union[Dict, str]:
    """Simple POST request to Revit API"""
    return await _revit_call("POST", endpoint, data=data, ctx=ctx, **kwargs)


async def revit_image(endpoint: str, ctx: Context = None) -> Union[Image, str]:
    """GET request that returns an Image object"""
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
            response = await client.request("GET", f"{BASE_URL}{endpoint}", json=authorization_body())
            
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
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            url = f"{BASE_URL}{endpoint}"

            # pyRevit Routes hides headers from handlers, so the pairing secret
            # travels in the JSON body of every call, GET included.
            if method == "GET":
                response = await client.request("GET", url, params=params, json=authorization_body())
            else:  # POST
                response = await client.post(url, json=authorization_body(data))

            return response.json() if response.status_code == 200 else f"Error: {response.status_code} - {response.text}"
    except httpx.TimeoutException:
        return f"Error: Request timed out after {timeout} seconds. The operation may still be running in Revit."
    except Exception as e:
        msg = str(e) or type(e).__name__
        return f"Error: {msg}"


# Register all tools BEFORE the main block
from tools import register_tools
register_tools(mcp, revit_get, revit_post, revit_image)


def build_http_app(transport):
    """Build an authenticated app for the selected loopback transport."""
    if transport not in {"sse", "streamable-http", "combined"}:
        raise ValueError("Unknown HTTP transport.")
    if transport == "sse":
        http_app = mcp.sse_app()
    else:
        http_app = mcp.streamable_http_app()
        if transport == "combined":
            http_app.routes.extend(mcp.sse_app().routes)
    http_app.add_middleware(PairingMiddleware)
    return http_app


async def run_http_async(transport):
    """Run paired, loopback-only SSE, streamable HTTP, or both."""
    import uvicorn
    http_app = build_http_app(transport)

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
        transport = "combined"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        anyio.run(run_http_async, transport)