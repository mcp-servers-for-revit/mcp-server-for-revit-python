"""Geothermal borehole field design engine, built on pygfunction.

Public functions accept and return plain dicts/lists (JSON-serializable) so
this package can be called from tools/geothermal_tools.py (MCP) or, later,
from a native GUI over a JSON contract, without exposing pygfunction objects
across that boundary.
"""
