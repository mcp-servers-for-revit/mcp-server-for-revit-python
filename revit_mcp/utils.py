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
    Safely find a family symbol by name
    """
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)

        for symbol in collector:
            if symbol.Family.Name == target_family_name:
                if not target_type_name or symbol.Name == target_type_name:
                    return symbol
        return None
    except Exception as e:
        logger.error("Error finding family symbol: %s", str(e))
        return None
