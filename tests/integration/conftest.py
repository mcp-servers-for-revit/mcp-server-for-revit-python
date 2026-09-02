# -*- coding: utf-8 -*-
"""Integration test fixtures — manage Revit lifecycle."""
import os
import pytest
import httpx

BASE_URL = "http://127.0.0.1:48884/revit_mcp"
TEST_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test.rvt")


async def _revit_get(endpoint, ctx=None, **kwargs):
    """Real HTTP GET to pyRevit Routes."""
    timeout = kwargs.pop("timeout", 30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{BASE_URL}{endpoint}")
        if response.status_code == 200:
            return response.json()
        return f"Error: {response.status_code} - {response.text}"


async def _revit_post(endpoint, data, ctx=None, **kwargs):
    """Real HTTP POST to pyRevit Routes."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{BASE_URL}{endpoint}", json=data)
        if response.status_code == 200:
            return response.json()
        return f"Error: {response.status_code} - {response.text}"


@pytest.fixture(scope="session")
async def revit_ready():
    """Ensure Revit is running with pyRevit Routes active.

    Tries to connect first. If Revit isn't running, launches it
    and waits for readiness. Runs once per test session.
    """
    from tools.launch_tools import (
        _find_revit_installations,
        _select_revit,
        _build_launch_command,
        _wait_for_revit_ready,
    )
    import subprocess

    # Check if already running
    try:
        response = await _revit_get("/status/", timeout=10.0)
        if isinstance(response, dict):
            return response
        # A 5xx string means pyRevit Routes is up (e.g. no document open)
        if isinstance(response, str) and response.startswith("Error: 5"):
            return {"status": "active_no_document"}
    except Exception:
        pass

    # Launch Revit
    installations = _find_revit_installations()
    selected = _select_revit(installations)
    assert selected, "No Revit installation found"
    cmd = _build_launch_command(selected["path"])
    subprocess.Popen(cmd)
    ready, status = await _wait_for_revit_ready(_revit_get, None, timeout=180)
    assert ready, "Revit did not become ready within 180 seconds"
    return status


@pytest.fixture(scope="session")
def test_file_path():
    """Path to the test .rvt file."""
    assert os.path.isfile(TEST_FILE), f"Test file not found: {TEST_FILE}"
    return TEST_FILE


@pytest.fixture
def revit_get():
    return _revit_get


@pytest.fixture
def revit_post():
    return _revit_post
