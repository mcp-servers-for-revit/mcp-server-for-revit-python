# -*- coding: UTF-8 -*-
"""
Element Filter Module for Revit MCP

A structured multi-criteria element filter that returns rich per-element
information dispatched on the element's kind. Useful for queries like:

    - "Find every wall named 'Generic - 200mm'"
    - "Get all doors visible in the current view"
    - "List rooms on Level 2 with their area / volume / perimeter"
    - "Find all family instances inside this bounding box"

Filters (any combination, AND'd together):

    filter_category               BuiltInCategory name, e.g. "OST_Walls"
    filter_element_type           .NET class name under Autodesk.Revit.DB,
                                  e.g. "Wall", "FamilyInstance"
    filter_family_symbol_id       FamilySymbol ElementId (instances only)
    include_types                 include ElementType subclasses
    include_instances             include element instances (default true)
    filter_visible_in_current_view restrict instances to the active view
    view_id                       restrict instances to this specific view
                                  (takes precedence over the active view)
    bounding_box_min / _max       3D filter, both in mm, [x, y, z]
    name_contains                 case-insensitive substring on Element.Name
    max_elements                  cap on result size (0 = unlimited)

Per-element info shape depends on the element kind:

    HasMaterialQuantities (Wall, Floor, Column, Door, Window, ...)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, type_id, room_id, level, bounding_box,
        parameters (thickness_mm, bbox_height_mm)

    ElementType (WallType, FloorType, FamilySymbol, ...)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, parameters (Length/Area/Volume/Angle params, with
        thickness_mm appended when applicable)

    Positioning (Level, Grid)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box, level, plus:
            Level -> elevation_mm
            Grid  -> grid_line (start_mm, end_mm)

    Spatial (Room, Area)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box, level, number, plus:
            Room -> volume_m3
            Both -> area_m2, perimeter_mm (when ROOM_AREA / ROOM_PERIMETER
                                            are populated)

    View
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box, view_type, scale, is_template,
        detail_level, associated_level (ViewPlan only), is_open, is_active

    Annotation (TextNote, Dimension, IndependentTag, SpotDimension,
                AnnotationSymbol when present)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box, owner_view, plus:
            TextNote   -> text_content, position_mm
            Dimension  -> dimension_value, position_mm
            others     -> position_mm (from LocationPoint)

    Group / RevitLinkInstance
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box, plus:
            Group              -> member_count, group_type
            RevitLinkInstance  -> link_path, link_status, position_mm

    Fallback (anything else)
        id, unique_id, name, family_name, category, built_in_category,
        element_class, bounding_box

Ported from Sparx mcp-servers-for-revit's AIElementFilterCommand
(Apache-2.0 licensed C# source at commandset/Services/
AIElementFilterEventHandler.cs) into our IronPython pyRevit Routes pattern.

Differences from the C# source:
- Length / area / volume outputs use UnitUtils.ConvertFromInternalUnits to
  produce honest mm / m2 / m3 values (Sparx multiplied by 304.8^N which
  conflated "mm in project units" with raw scaled feet).
- Adds explicit view_id input on top of filter_visible_in_current_view —
  useful when the caller knows the target view (e.g. from list_revit_views)
  but isn't asking the user to focus it first.
- Adds name_contains substring filter — Sparx had no name filter.
- Returns applied_filters diagnostic list so the caller can see exactly
  which filters got applied (matches Sparx's Trace.WriteLine output but
  exposed in the response).
- Returns matched_count separately from returned_count so the caller can
  see when the cap was hit (Sparx mixed "got N elements" into the message
  string).

Read-only — no transactions.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64, Type
from System.Collections.Generic import List as NetList
import json
import traceback
import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_ELEMENTS = 100

# Cached BuiltInCategory int -> name map. Built lazily once per pyRevit load.
_BIC_NAME_CACHE = None


def _bic_name_for(cat_id_v):
    """Return 'OST_Walls' style name for a Category.Id value, or None."""
    global _BIC_NAME_CACHE
    if _BIC_NAME_CACHE is None:
        cache = {}
        for name in dir(DB.BuiltInCategory):
            if name.startswith("OST_"):
                try:
                    v = getattr(DB.BuiltInCategory, name)
                    cache[int(v)] = name
                except Exception:
                    continue
        _BIC_NAME_CACHE = cache
    return _BIC_NAME_CACHE.get(int(cat_id_v))


# ---------- JSON / unit helpers ----------

def _parse_json_request(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _mm_to_feet(v):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(v), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertToInternalUnits(float(v), DB.DisplayUnitType.DUT_MILLIMETERS)


def _ft_to_mm(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_MILLIMETERS)


def _ft2_to_m2(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.SquareMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_SQUARE_METERS)


def _ft3_to_m3(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.CubicMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_CUBIC_METERS)


def _xyz_to_mm(p):
    if p is None:
        return None
    return {
        "x_mm": round(_ft_to_mm(p.X), 3),
        "y_mm": round(_ft_to_mm(p.Y), 3),
        "z_mm": round(_ft_to_mm(p.Z), 3),
    }


def _get_bbox_info(elem):
    try:
        bb = elem.get_BoundingBox(None)
        if bb is None:
            return None
        return {"min": _xyz_to_mm(bb.Min), "max": _xyz_to_mm(bb.Max)}
    except Exception:
        return None


def _safe_name(elem):
    try:
        return normalize_string(get_element_name(elem))
    except Exception:
        return None


def _param_value_string(elem, bip):
    try:
        p = elem.get_Parameter(bip)
        if p and p.HasValue:
            return normalize_string(p.AsValueString())
    except Exception:
        pass
    return None


def _level_for_element(doc, elem):
    """Resolve the level for an element via the same hierarchy Sparx uses."""
    level = None
    try:
        if isinstance(elem, DB.Wall):
            level = doc.GetElement(elem.LevelId)
        elif isinstance(elem, DB.Floor):
            p = elem.get_Parameter(DB.BuiltInParameter.LEVEL_PARAM)
            if p and p.HasValue:
                level = doc.GetElement(p.AsElementId())
        elif isinstance(elem, DB.FamilyInstance):
            p = elem.get_Parameter(DB.BuiltInParameter.FAMILY_LEVEL_PARAM)
            if p and p.HasValue:
                level = doc.GetElement(p.AsElementId())
            if level is None:
                p = elem.get_Parameter(DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM)
                if p and p.HasValue:
                    level = doc.GetElement(p.AsElementId())
        else:
            p = elem.get_Parameter(DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM)
            if p and p.HasValue:
                level = doc.GetElement(p.AsElementId())
    except Exception:
        level = None
    if level is None or not isinstance(level, DB.Level):
        return None
    return {
        "id": element_id_value(level.Id),
        "name": normalize_string(level.Name),
        "elevation_mm": round(_ft_to_mm(level.Elevation), 3),
    }


# ---------- Filter parsing ----------

def _parse_category(cat_name):
    """Return (BuiltInCategory member, None) or (None, error_message)."""
    if not cat_name:
        return None, None
    if not isinstance(cat_name, (str, unicode)):
        return None, "filter_category must be a string"
    cname = cat_name if cat_name.startswith("OST_") else "OST_" + cat_name
    member = getattr(DB.BuiltInCategory, cname, None)
    if member is None:
        return None, "unknown BuiltInCategory: {}".format(cat_name)
    return member, None


def _resolve_element_class(class_name):
    """Resolve a .NET Type for the given Revit DB class name. Returns (Type, error)."""
    if not class_name:
        return None, None
    if not isinstance(class_name, (str, unicode)):
        return None, "filter_element_type must be a string"
    bare = class_name
    if "." in bare:
        bare = bare.rsplit(".", 1)[-1]
    # First try getattr on pyRevit's DB module (covers Autodesk.Revit.DB.*)
    candidate = getattr(DB, bare, None)
    if candidate is not None:
        try:
            # Confirm we got a .NET type rather than a Python sub-module
            if hasattr(candidate, "Assembly"):
                return candidate, None
        except Exception:
            pass
    # Then try System.Type.GetType with the common assembly hints
    candidates = [
        class_name,
        "Autodesk.Revit.DB.{}, RevitAPI".format(bare),
        "{}, RevitAPI".format(class_name),
    ]
    for tn in candidates:
        try:
            t = Type.GetType(tn)
            if t is not None:
                return t, None
        except Exception:
            continue
    return None, "could not resolve .NET type '{}' under Autodesk.Revit.DB".format(class_name)


def _resolve_view(doc, view_id):
    """(view, source) or ('ERROR', reason). view_id=None means no view filter."""
    if view_id is None:
        return None, "no_view_filter"
    try:
        vid = DB.ElementId(Int64(int(view_id)))
    except Exception:
        return "ERROR", "view_id_must_be_int"
    el = doc.GetElement(vid)
    if el is None or not isinstance(el, DB.View):
        return "ERROR", "view_id_not_found"
    return el, "explicit_view_id"


# ---------- Validation ----------

def _validate(data, settings):
    """Mutates `settings` in-place with resolved filter values. Returns (ok, error)."""
    include_types = bool(data.get("include_types", False))
    include_instances = bool(data.get("include_instances", True))
    if not include_types and not include_instances:
        return False, "include_types and include_instances cannot both be false"
    settings["include_types"] = include_types
    settings["include_instances"] = include_instances

    cat_raw = data.get("filter_category")
    elem_type_raw = data.get("filter_element_type")
    family_symbol_id = data.get("filter_family_symbol_id")
    if family_symbol_id in (None, ""):
        family_symbol_id = -1
    try:
        family_symbol_id = int(family_symbol_id)
    except Exception:
        return False, "filter_family_symbol_id must be an integer"

    settings["filter_category"] = cat_raw
    settings["filter_element_type"] = elem_type_raw
    settings["filter_family_symbol_id"] = family_symbol_id
    settings["filter_visible_in_current_view"] = bool(data.get("filter_visible_in_current_view", False))
    settings["name_contains"] = data.get("name_contains") or None

    bb_min = data.get("bounding_box_min")
    bb_max = data.get("bounding_box_max")
    if (bb_min is None) != (bb_max is None):
        return False, "bounding_box_min and bounding_box_max must both be set or both omitted"
    if bb_min is not None:
        for arr, key in [(bb_min, "bounding_box_min"), (bb_max, "bounding_box_max")]:
            if not (isinstance(arr, list) and len(arr) == 3):
                return False, "{} must be a 3-element list [x_mm, y_mm, z_mm]".format(key)
            for v in arr:
                if not isinstance(v, (int, float)):
                    return False, "{} values must be numbers".format(key)
        if bb_min[0] > bb_max[0] or bb_min[1] > bb_max[1] or bb_min[2] > bb_max[2]:
            return False, "bounding_box_min must be <= bounding_box_max on every axis"
    settings["bounding_box_min"] = bb_min
    settings["bounding_box_max"] = bb_max

    has_filter = (
        bool(cat_raw) or bool(elem_type_raw) or family_symbol_id > 0
        or bb_min is not None or settings["name_contains"] is not None
    )
    if not has_filter:
        return False, ("at least one filter must be specified: filter_category, "
                       "filter_element_type, filter_family_symbol_id, "
                       "bounding_box_min/max, or name_contains")

    if include_types and not include_instances:
        invalid = []
        if family_symbol_id > 0:
            invalid.append("filter_family_symbol_id")
        if settings["filter_visible_in_current_view"]:
            invalid.append("filter_visible_in_current_view")
        if settings.get("_view_obj") is not None:
            invalid.append("view_id")
        if invalid:
            return False, ("when only element types are included, these filters do not apply: "
                           + ", ".join(invalid))

    if cat_raw:
        enum_val, err = _parse_category(cat_raw)
        if err:
            return False, err
        settings["_category_enum"] = enum_val
    else:
        settings["_category_enum"] = None

    if elem_type_raw:
        t, err = _resolve_element_class(elem_type_raw)
        if err:
            return False, err
        settings["_class_type"] = t
    else:
        settings["_class_type"] = None

    return True, None


# ---------- Collection ----------

def _collect(doc, is_element_type, settings, applied_filters):
    """Mirror of Sparx GetElementsByKind. Returns list[Element]."""
    explicit_view = settings.get("_view_obj")
    use_active = bool(settings.get("filter_visible_in_current_view"))

    if not is_element_type and explicit_view is not None:
        collector = DB.FilteredElementCollector(doc, explicit_view.Id)
        applied_filters.append("view_id={}".format(element_id_value(explicit_view.Id)))
    elif not is_element_type and use_active and doc.ActiveView is not None:
        collector = DB.FilteredElementCollector(doc, doc.ActiveView.Id)
        applied_filters.append("current_view_visible")
    else:
        collector = DB.FilteredElementCollector(doc)

    if is_element_type:
        collector = collector.WhereElementIsElementType()
        applied_filters.append("element_types_only")
    else:
        collector = collector.WhereElementIsNotElementType()
        applied_filters.append("instances_only")

    filters = []
    bic = settings.get("_category_enum")
    if bic is not None:
        filters.append(DB.ElementCategoryFilter(bic))
        applied_filters.append("category={}".format(settings.get("filter_category")))

    klass = settings.get("_class_type")
    if klass is not None:
        try:
            filters.append(DB.ElementClassFilter(klass))
            applied_filters.append("class={}".format(klass.Name))
        except Exception as ex:
            logger.warning("ElementClassFilter failed for %s: %s", getattr(klass, "Name", "?"), ex)
            applied_filters.append("class_filter_failed")

    fs_id = settings.get("filter_family_symbol_id")
    if not is_element_type and fs_id and fs_id > 0:
        try:
            sid = DB.ElementId(Int64(int(fs_id)))
            symbol_el = doc.GetElement(sid)
            if symbol_el is not None and isinstance(symbol_el, DB.FamilySymbol):
                filters.append(DB.FamilyInstanceFilter(doc, sid))
                applied_filters.append("family_symbol_id={}".format(fs_id))
            else:
                applied_filters.append("family_symbol_id={}_invalid".format(fs_id))
        except Exception as ex:
            logger.warning("FamilyInstanceFilter failed: %s", ex)
            applied_filters.append("family_symbol_id_filter_failed")

    bb_min = settings.get("bounding_box_min")
    bb_max = settings.get("bounding_box_max")
    if bb_min is not None and bb_max is not None:
        min_xyz = DB.XYZ(_mm_to_feet(bb_min[0]), _mm_to_feet(bb_min[1]), _mm_to_feet(bb_min[2]))
        max_xyz = DB.XYZ(_mm_to_feet(bb_max[0]), _mm_to_feet(bb_max[1]), _mm_to_feet(bb_max[2]))
        outline = DB.Outline(min_xyz, max_xyz)
        filters.append(DB.BoundingBoxIntersectsFilter(outline))
        applied_filters.append("bbox_intersects")

    if filters:
        if len(filters) == 1:
            collector = collector.WherePasses(filters[0])
        else:
            net_filters = NetList[DB.ElementFilter]()
            for f in filters:
                net_filters.Add(f)
            collector = collector.WherePasses(DB.LogicalAndFilter(net_filters))

    return list(collector.ToElements())


def _post_filter_name(elements, name_contains):
    if not name_contains:
        return elements
    nc = name_contains.lower()
    out = []
    for el in elements:
        n = _safe_name(el)
        if n and nc in n.lower():
            out.append(el)
    return out


# ---------- Info builders ----------

def _common_header(elem):
    cat = None
    try:
        cat = elem.Category
    except Exception:
        cat = None
    cat_name = None
    bic = None
    if cat is not None:
        try:
            cat_name = normalize_string(cat.Name)
        except Exception:
            cat_name = None
        try:
            bic = _bic_name_for(element_id_value(cat.Id))
        except Exception:
            bic = None
    return {
        "id": element_id_value(elem.Id),
        "unique_id": elem.UniqueId,
        "name": _safe_name(elem),
        "family_name": _param_value_string(elem, DB.BuiltInParameter.ELEM_FAMILY_PARAM),
        "category": cat_name,
        "built_in_category": bic,
        "element_class": type(elem).__name__,
    }


def _thickness_mm_from_type(elem_type):
    """Return thickness in mm for system-family type elements (or None)."""
    if elem_type is None:
        return None
    try:
        param = None
        if isinstance(elem_type, DB.WallType):
            param = elem_type.get_Parameter(DB.BuiltInParameter.WALL_ATTR_WIDTH_PARAM)
        elif isinstance(elem_type, DB.FloorType):
            param = elem_type.get_Parameter(DB.BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)
        elif isinstance(elem_type, DB.CeilingType):
            param = elem_type.get_Parameter(DB.BuiltInParameter.CEILING_THICKNESS)
        elif isinstance(elem_type, DB.FamilySymbol):
            cat = elem_type.Category
            if cat is not None:
                cv = element_id_value(cat.Id)
                doors_id = int(DB.BuiltInCategory.OST_Doors)
                windows_id = int(DB.BuiltInCategory.OST_Windows)
                if cv in (doors_id, windows_id):
                    param = elem_type.get_Parameter(DB.BuiltInParameter.FAMILY_THICKNESS_PARAM)
        if param is not None and param.HasValue:
            return _ft_to_mm(param.AsDouble())
    except Exception:
        pass
    return None


def _is_dimension_parameter(param):
    """True iff param represents a Length/Area/Volume/Angle quantity."""
    try:
        d = param.Definition
        if hasattr(d, "GetDataType"):
            try:
                dt = d.GetDataType()
                for spec in ("Length", "Angle", "Area", "Volume"):
                    s = getattr(DB.SpecTypeId, spec, None)
                    if s is not None and dt.Equals(s):
                        return True
                return False
            except Exception:
                pass
        if hasattr(d, "ParameterType"):
            try:
                pt = d.ParameterType
                pt_enum = getattr(DB, "ParameterType", None)
                if pt_enum is None:
                    return False
                return pt in (pt_enum.Length, pt_enum.Angle, pt_enum.Area, pt_enum.Volume)
            except Exception:
                pass
    except Exception:
        pass
    return False


def _build_instance_info(doc, elem):
    info = _common_header(elem)
    info["type_id"] = element_id_value(elem.GetTypeId())
    if isinstance(elem, DB.FamilyInstance):
        try:
            room = elem.Room
            info["room_id"] = element_id_value(room.Id) if room is not None else -1
        except Exception:
            info["room_id"] = -1
    info["level"] = _level_for_element(doc, elem)
    bb = _get_bbox_info(elem)
    info["bounding_box"] = bb
    params = []
    et = doc.GetElement(elem.GetTypeId()) if elem.GetTypeId() else None
    t = _thickness_mm_from_type(et)
    if t is not None:
        params.append({"name": "thickness_mm", "value": round(t, 3)})
    if bb is not None and bb.get("min") and bb.get("max"):
        h = abs(bb["max"]["z_mm"] - bb["min"]["z_mm"])
        params.append({"name": "bbox_height_mm", "value": round(h, 3)})
    info["parameters"] = params
    return info


def _build_type_info(doc, elem_type):
    info = _common_header(elem_type)
    try:
        info["family_name"] = normalize_string(elem_type.FamilyName)
    except Exception:
        pass
    params = []
    try:
        for p in elem_type.Parameters:
            try:
                if not p.HasValue or p.IsReadOnly:
                    continue
                if not _is_dimension_parameter(p):
                    continue
                v = p.AsValueString()
                if not v:
                    continue
                params.append({
                    "name": normalize_string(p.Definition.Name),
                    "value": normalize_string(v),
                })
            except Exception:
                continue
    except Exception:
        pass
    t = _thickness_mm_from_type(elem_type)
    if t is not None:
        params.append({"name": "thickness_mm", "value": round(t, 3)})
    info["parameters"] = sorted(params, key=lambda x: (x.get("name") or u""))
    return info


def _build_positioning_info(doc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    if isinstance(elem, DB.Level):
        info["elevation_mm"] = round(_ft_to_mm(elem.Elevation), 3)
    elif isinstance(elem, DB.Grid):
        try:
            curve = elem.Curve
            if curve is not None:
                p0 = curve.GetEndPoint(0)
                p1 = curve.GetEndPoint(1)
                info["grid_line"] = {"start_mm": _xyz_to_mm(p0), "end_mm": _xyz_to_mm(p1)}
        except Exception:
            pass
    info["level"] = _level_for_element(doc, elem)
    return info


def _build_spatial_info(doc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    Room = getattr(DB.Architecture, "Room", None) if hasattr(DB, "Architecture") else None
    Area = getattr(DB, "Area", None)
    if Room is not None and isinstance(elem, Room):
        try:
            info["number"] = normalize_string(elem.Number)
        except Exception:
            pass
        try:
            vol_ft3 = float(elem.Volume)
            if vol_ft3:
                info["volume_m3"] = round(_ft3_to_m3(vol_ft3), 4)
        except Exception:
            pass
    elif Area is not None and isinstance(elem, Area):
        try:
            info["number"] = normalize_string(elem.Number)
        except Exception:
            pass
    try:
        ap = elem.get_Parameter(DB.BuiltInParameter.ROOM_AREA)
        if ap and ap.HasValue:
            info["area_m2"] = round(_ft2_to_m2(ap.AsDouble()), 4)
    except Exception:
        pass
    try:
        pp = elem.get_Parameter(DB.BuiltInParameter.ROOM_PERIMETER)
        if pp and pp.HasValue:
            info["perimeter_mm"] = round(_ft_to_mm(pp.AsDouble()), 3)
    except Exception:
        pass
    info["level"] = _level_for_element(doc, elem)
    return info


def _build_view_info(doc, uidoc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    try:
        info["view_type"] = str(elem.ViewType)
    except Exception:
        info["view_type"] = None
    try:
        info["scale"] = elem.Scale
    except Exception:
        info["scale"] = None
    try:
        info["is_template"] = bool(elem.IsTemplate)
    except Exception:
        info["is_template"] = None
    try:
        info["detail_level"] = str(elem.DetailLevel)
    except Exception:
        info["detail_level"] = None
    if isinstance(elem, DB.ViewPlan):
        try:
            lv = elem.GenLevel
            if lv is not None:
                info["associated_level"] = {
                    "id": element_id_value(lv.Id),
                    "name": normalize_string(lv.Name),
                    "elevation_mm": round(_ft_to_mm(lv.Elevation), 3),
                }
        except Exception:
            pass
    info["is_open"] = False
    info["is_active"] = False
    try:
        if uidoc is not None:
            this_id = element_id_value(elem.Id)
            for uiv in uidoc.GetOpenUIViews():
                try:
                    if element_id_value(uiv.ViewId) == this_id:
                        info["is_open"] = True
                        active = uidoc.ActiveView
                        if active is not None and element_id_value(active.Id) == this_id:
                            info["is_active"] = True
                        break
                except Exception:
                    continue
    except Exception:
        pass
    return info


def _build_annotation_info(doc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    try:
        if elem.OwnerViewId != DB.ElementId.InvalidElementId:
            ov = doc.GetElement(elem.OwnerViewId)
            if ov is not None:
                info["owner_view"] = normalize_string(ov.Name)
    except Exception:
        pass
    if isinstance(elem, DB.TextNote):
        try:
            info["text_content"] = normalize_string(elem.Text)
        except Exception:
            pass
        try:
            info["position_mm"] = _xyz_to_mm(elem.Coord)
        except Exception:
            pass
    elif isinstance(elem, DB.Dimension):
        try:
            v = elem.Value
            info["dimension_value"] = str(v) if v is not None else None
        except Exception:
            pass
        try:
            info["position_mm"] = _xyz_to_mm(elem.Origin)
        except Exception:
            pass
    else:
        try:
            loc = elem.Location
            if isinstance(loc, DB.LocationPoint):
                info["position_mm"] = _xyz_to_mm(loc.Point)
        except Exception:
            pass
    return info


def _build_group_or_link_info(doc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    if isinstance(elem, DB.Group):
        try:
            members = elem.GetMemberIds()
            info["member_count"] = len(list(members)) if members is not None else 0
        except Exception:
            info["member_count"] = None
        try:
            gt = elem.GroupType
            info["group_type"] = normalize_string(gt.Name) if gt is not None else None
        except Exception:
            info["group_type"] = None
    elif isinstance(elem, DB.RevitLinkInstance):
        info["link_path"] = None
        info["link_status"] = None
        try:
            link_type = doc.GetElement(elem.GetTypeId())
            if isinstance(link_type, DB.RevitLinkType):
                try:
                    ext = link_type.GetExternalFileReference()
                    if ext is not None:
                        path = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(ext.GetAbsolutePath())
                        info["link_path"] = normalize_string(path)
                except Exception:
                    pass
                try:
                    info["link_status"] = str(link_type.GetLinkedFileStatus())
                except Exception:
                    pass
            else:
                info["link_status"] = "Invalid"
        except Exception:
            pass
        try:
            loc = elem.Location
            if isinstance(loc, DB.LocationPoint):
                info["position_mm"] = _xyz_to_mm(loc.Point)
        except Exception:
            pass
    return info


def _build_basic_info(doc, elem):
    info = _common_header(elem)
    info["bounding_box"] = _get_bbox_info(elem)
    return info


def _info_for_element(doc, uidoc, elem):
    """Dispatch on element kind."""
    try:
        cat = elem.Category
    except Exception:
        cat = None

    # Specific kinds first — many of these (WallType, Room, etc.) inherit
    # categories whose HasMaterialQuantities=True, so checking that flag
    # earlier would mis-route them into the generic instance branch.
    if isinstance(elem, DB.ElementType):
        return _build_type_info(doc, elem)
    if isinstance(elem, DB.Level) or isinstance(elem, DB.Grid):
        return _build_positioning_info(doc, elem)
    if isinstance(elem, DB.SpatialElement):
        return _build_spatial_info(doc, elem)
    if isinstance(elem, DB.View):
        return _build_view_info(doc, uidoc, elem)
    annot_types = [DB.TextNote, DB.Dimension, DB.IndependentTag, DB.SpotDimension]
    AnnotSym = getattr(DB, "AnnotationSymbol", None)
    if AnnotSym is not None:
        annot_types.append(AnnotSym)
    for tp in annot_types:
        if isinstance(elem, tp):
            return _build_annotation_info(doc, elem)
    if isinstance(elem, DB.Group) or isinstance(elem, DB.RevitLinkInstance):
        return _build_group_or_link_info(doc, elem)
    # Generic solid-model instance (Wall, Floor, Door, Window, Column, ...)
    if cat is not None and getattr(cat, "HasMaterialQuantities", False):
        return _build_instance_info(doc, elem)
    return _build_basic_info(doc, elem)


# ---------- Route ----------

def register_element_filter_routes(api):
    """Register the structured element filter route."""

    @api.route("/ai_element_filter/", methods=["POST"])
    def ai_element_filter(doc, uidoc, request):
        """
        Find elements matching a structured filter and return rich per-element info.

        See the module docstring for the input schema and per-kind output shape.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)

            view_or_err, view_source = _resolve_view(doc, data.get("view_id"))
            if view_or_err == "ERROR":
                return routes.make_response(data={
                    "status": "view_not_found",
                    "error": view_source,
                })

            settings = {"_view_obj": view_or_err if view_or_err is not None else None}
            ok, err = _validate(data, settings)
            if not ok:
                return routes.make_response(data={
                    "status": "invalid_filter",
                    "error": err,
                })

            try:
                max_elements = int(data.get("max_elements", DEFAULT_MAX_ELEMENTS))
            except Exception:
                max_elements = DEFAULT_MAX_ELEMENTS

            applied_filters = []
            elements = []
            if settings["include_instances"]:
                elements.extend(_collect(doc, False, settings, applied_filters))
            if settings["include_types"]:
                elements.extend(_collect(doc, True, settings, applied_filters))

            elements = _post_filter_name(elements, settings.get("name_contains"))
            if settings.get("name_contains"):
                applied_filters.append("name_contains={}".format(settings["name_contains"]))

            total_matched = len(elements)
            truncated = False
            if max_elements > 0 and total_matched > max_elements:
                elements = elements[:max_elements]
                truncated = True
                applied_filters.append("max_elements={}".format(max_elements))

            infos = []
            for el in elements:
                try:
                    info = _info_for_element(doc, uidoc, el)
                    if info is not None:
                        infos.append(info)
                except Exception as ex:
                    logger.warning("info build failed for id=%s: %s",
                                   element_id_value(el.Id) if hasattr(el, "Id") else "?", ex)
                    continue

            return routes.make_response(data={
                "status": "success",
                "matched_count": total_matched,
                "returned_count": len(infos),
                "truncated": truncated,
                "applied_filters": applied_filters,
                "view_source": view_source,
                "view_id": element_id_value(view_or_err.Id) if view_or_err is not None else None,
                "view_name": normalize_string(view_or_err.Name) if view_or_err is not None else None,
                "elements": infos,
            })

        except Exception as e:
            logger.error("ai_element_filter failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Element filter (ai_element_filter) routes registered successfully")
