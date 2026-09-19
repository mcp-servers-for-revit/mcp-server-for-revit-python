# -*- coding: UTF-8 -*-
"""
Creation Tracker for Revit MCP

Keeps an in-memory record, for the lifetime of the running Revit process,
of element ids created through MCP tools. This lets `reset_model` roll
back what MCP created without ever touching elements that already existed
in the model.
"""
from utils import element_id_value

_created_element_ids = set()


def register_created(element_ids):
    """Record one or more created elements (ElementId objects or raw ints)."""
    for eid in element_ids:
        try:
            value = eid if isinstance(eid, int) else element_id_value(eid)
            _created_element_ids.add(value)
        except Exception:
            continue


def get_created_ids():
    """Return a copy of the tracked element id values (ints)."""
    return set(_created_element_ids)


def discard(element_ids):
    """Stop tracking ids, e.g. once they've been deleted."""
    for eid in element_ids:
        _created_element_ids.discard(eid)


def clear():
    """Clear all tracked ids."""
    _created_element_ids.clear()
