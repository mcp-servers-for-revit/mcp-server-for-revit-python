# -*- coding: utf-8 -*-
from pyrevit import DB
import traceback
import logging

logger = logging.getLogger(__name__)


def normalize_string(text):
    """Safely normalize string values, always returning a unicode string.

    In IronPython 2, calling str() on a .NET System.String that contains
    non-ASCII characters (e.g. accented letters) produces a byte string
    encoded with the system default codec.  The pyRevit Routes JSON encoder
    then fails with 'unknown codec can't decode byte 0xNN'.

    By returning unicode we guarantee the JSON serialiser receives a proper
    text object regardless of the locale of the Revit model.
    """
    if text is None:
        return u"Unnamed"
    # Already a unicode string (normal case for .NET System.String in IronPython)
    if isinstance(text, unicode):
        return text.strip()
    # Byte string — decode with a permissive fallback
    if isinstance(text, str):
        try:
            return text.decode("utf-8").strip()
        except (UnicodeDecodeError, AttributeError):
            return text.decode("latin-1").strip()
    # Any other type (.NET object, int, etc.) — convert via unicode()
    try:
        return unicode(text).strip()
    except Exception:
        return u"Unnamed"


def element_id_value(element_id):
    """Get the integer value from an ElementId.

    Revit 2025+ uses .Value (int64), older versions use .IntegerValue (int32).
    Revit 2026 removed .IntegerValue entirely.
    """
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


def get_element_name(element):
    """
    Get the name of a Revit element.
    Useful for both FamilySymbol and other elements.
    """
    try:
        return element.Name
    except AttributeError:
        return DB.Element.Name.__get__(element)


def find_family_symbol_safely(doc, target_family_name, target_type_name=None):
    """
    Safely find a family symbol by name.

    Uses get_element_name() rather than symbol.Name directly: on Revit 2026,
    FamilySymbol.Name is not directly accessible through the IronPython
    binding for every symbol and raises AttributeError. Without a per-symbol
    guard, one bad symbol encountered while iterating the collector would
    trip the function's outer except-block and abort the whole search,
    making every lookup fail with "family not found" regardless of target.
    """
    collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)

    for symbol in collector:
        try:
            if symbol.Family.Name != target_family_name:
                continue
            if not target_type_name or get_element_name(symbol) == target_type_name:
                return symbol
        except Exception as e:
            logger.debug("Skipping symbol while searching for family: %s", str(e))
            continue
    return None
