# -*- coding: UTF-8 -*-
"""
Model-Statistics Module for Revit MCP
High-level rollup of what's in the project: element counts by category,
type/family breakdowns inside each category, per-level element counts,
and project-wide totals (elements / types / families / views / sheets).

This is a READ-ONLY data-extraction tool — no transactions.

Ported from Sparx mcp-servers-for-revit's AnalyzeModelStatistics command
(Apache-2.0 licensed C# source at commandset/Services/DataExtraction/
AnalyzeModelStatisticsEventHandler.cs) into our IronPython pyRevit Routes
pattern.

Differences from the C# source:
- Adds optional filters: category_filter, view_id, top_n_categories,
  top_n_types_per_category. Sparx scanned the entire model unconditionally.
- Level elevations are reported in mm (Sparx returned raw decimal feet).
- Uses the descriptor accessor (DB.Element.Name.__get__) via
  get_element_name() for Revit 2026 IronPython ElementType safety; Sparx
  used .Name directly which fails on Revit 2026.
- Categories are sorted by element_count descending (matches Sparx) but
  the per-category Types list is also sorted by instance_count descending
  (Sparx returned in insertion order).
- Adds `truncated_categories` and `truncated_types_per_category` diagnostic
  flags when caps are applied.
- Returns `applied_filters` list for diagnostics.
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value, get_element_name
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def _parse_json_request(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _ft_to_mm(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.Millimeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_MILLIMETERS)
    except Exception:
        return float(v) * 304.8


def _resolve_view(doc, view_id):
    """Return (view, source) or ("ERROR", reason). view_id=None means no view filter."""
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


def _parse_category_filter(category_filter):
    """
    Convert a list of BuiltInCategory names (e.g. "OST_Walls") into a list of
    enum members. Returns (enum_list, invalid_names). Unknown names are
    collected for diagnostics; they don't fail the request.
    """
    if not category_filter:
        return [], []
    if not isinstance(category_filter, list):
        return None, [str(category_filter)]
    enums = []
    invalid = []
    for name in category_filter:
        if not isinstance(name, (str, unicode)):
            invalid.append(name)
            continue
        ename = name if name.startswith("OST_") else "OST_" + name
        member = getattr(DB.BuiltInCategory, ename, None)
        if member is None:
            invalid.append(name)
        else:
            enums.append(member)
    return enums, invalid


def _build_instance_collector(doc, view, category_enums):
    """Element-instance collector with optional view + category filters."""
    if view is not None:
        collector = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
    else:
        collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType()

    if category_enums:
        try:
            from System.Collections.Generic import List as NetList
            bic_list = NetList[DB.BuiltInCategory]()
            for e in category_enums:
                bic_list.Add(e)
            collector = collector.WherePasses(DB.ElementMulticategoryFilter(bic_list))
        except Exception as ex:
            logger.warning("category filter failed, returning unfiltered collector: %s", str(ex))
    return collector


def register_model_statistics_routes(api):
    """Register model-statistics rollup routes."""

    @api.route("/analyze_model_statistics/", methods=["POST"])
    def analyze_model_statistics(doc, request):
        """
        Roll up project-level statistics: counts, categories, types, levels.

        Expected payload (all fields optional):
        {
            "category_filter":            ["OST_Walls"],   // null = all categories
            "view_id":                    null,             // restrict to view-visible
            "include_detailed_types":     true,
            "top_n_categories":           null,             // cap categories list
            "top_n_types_per_category":   null              // cap types per category
        }

        Returns:
            {
              "status": "success",
              "project_name": "Project1",
              "totals": {
                "elements": N, "types": N, "families": N,
                "views": N, "sheets": N
              },
              "categories": [
                {
                  "category_name": "Walls",
                  "element_count": N,
                  "type_count": N,
                  "family_count": N,
                  "types": [{"type_name": ..., "family_name": ..., "instance_count": N}]
                }
              ],
              "levels": [{"level_name": ..., "elevation_mm": ..., "element_count": N}],
              "applied_filters": [...],
              "view_source": ...,
              "view_id": ..., "view_name": ...,
              "invalid_category_names": [...],
              "truncated_categories": false,
              "truncated_types_per_category": false
            }
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)
            category_filter_raw = data.get("category_filter")
            include_detailed_types = bool(data.get("include_detailed_types", True))
            top_n_categories = data.get("top_n_categories")
            top_n_types_per_category = data.get("top_n_types_per_category")

            if top_n_categories is not None:
                try:
                    top_n_categories = int(top_n_categories)
                    if top_n_categories <= 0:
                        top_n_categories = None
                except Exception:
                    return routes.make_response(data={
                        "error": "top_n_categories must be a positive integer"
                    }, status=400)
            if top_n_types_per_category is not None:
                try:
                    top_n_types_per_category = int(top_n_types_per_category)
                    if top_n_types_per_category <= 0:
                        top_n_types_per_category = None
                except Exception:
                    return routes.make_response(data={
                        "error": "top_n_types_per_category must be a positive integer"
                    }, status=400)

            view_or_err, view_source = _resolve_view(doc, data.get("view_id"))
            if view_or_err == "ERROR":
                return routes.make_response(data={
                    "status": "view_not_found",
                    "error": view_source,
                })
            view = view_or_err  # None or a real View

            category_enums, invalid_cats = _parse_category_filter(category_filter_raw)
            if category_enums is None:
                return routes.make_response(data={
                    "error": "category_filter must be a list of BuiltInCategory names (e.g. ['OST_Walls'])"
                }, status=400)

            applied_filters = []
            if category_filter_raw:
                applied_filters.append("category_filter")
            if view is not None:
                applied_filters.append("view_id")
            if not include_detailed_types:
                applied_filters.append("no_detailed_types")
            if top_n_categories:
                applied_filters.append("top_n_categories={}".format(top_n_categories))
            if top_n_types_per_category:
                applied_filters.append("top_n_types_per_category={}".format(top_n_types_per_category))

            # ----- Project-wide totals -----
            project_name = normalize_string(doc.Title)

            # Note: TotalElements / TotalTypes / TotalViews / TotalSheets are
            # project-wide counts (Sparx behaviour) — they are NOT scoped by
            # category_filter or view_id, since the scope filters are meant
            # to narrow the per-category drill-down, not the headline numbers.
            total_elements = DB.FilteredElementCollector(doc) \
                .WhereElementIsNotElementType().GetElementCount()
            total_types = DB.FilteredElementCollector(doc) \
                .WhereElementIsElementType().GetElementCount()
            total_sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).GetElementCount()

            # Views: exclude templates (matches Sparx)
            total_views = 0
            for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                if v is None:
                    continue
                try:
                    if not v.IsTemplate:
                        total_views += 1
                except Exception:
                    total_views += 1

            # ----- Category + type rollup (scoped by filters) -----
            category_stats = {}    # cat_name -> dict
            family_names = set()   # project-wide unique families (matches Sparx)

            for elem in _build_instance_collector(doc, view, category_enums):
                cat = elem.Category
                if cat is None:
                    continue
                try:
                    cat_name = normalize_string(cat.Name)
                except Exception:
                    continue

                cs = category_stats.get(cat_name)
                if cs is None:
                    cs = {
                        "category_name": cat_name,
                        "element_count": 0,
                        "types": {},   # (family_name, type_name) -> count
                    }
                    category_stats[cat_name] = cs

                cs["element_count"] += 1

                # FamilyInstance gets family/type info
                if isinstance(elem, DB.FamilyInstance):
                    try:
                        symbol = elem.Symbol
                    except Exception:
                        symbol = None
                    if symbol is not None:
                        try:
                            family_name = normalize_string(symbol.Family.Name) if symbol.Family else None
                        except Exception:
                            family_name = None
                        try:
                            type_name = normalize_string(get_element_name(symbol))
                        except Exception:
                            type_name = None

                        if family_name:
                            family_names.add(family_name)

                        if include_detailed_types and type_name:
                            key = (family_name or u"", type_name)
                            cs["types"][key] = cs["types"].get(key, 0) + 1
                else:
                    # Non-FamilyInstance: still useful to know the wall/floor type
                    # WallType / FloorType / CeilingType inherit ElementType.
                    if include_detailed_types:
                        try:
                            tid = elem.GetTypeId()
                        except Exception:
                            tid = None
                        if tid is not None and tid != DB.ElementId.InvalidElementId:
                            type_el = doc.GetElement(tid)
                            if type_el is not None:
                                try:
                                    type_name = normalize_string(get_element_name(type_el))
                                except Exception:
                                    type_name = None
                                if type_name:
                                    key = (u"", type_name)
                                    cs["types"][key] = cs["types"].get(key, 0) + 1

            # Project to output shape
            categories_out = []
            truncated_types_per_cat = False
            for cs in category_stats.values():
                type_entries = []
                # types dict: (family_name, type_name) -> count
                for (fname, tname), cnt in cs["types"].items():
                    type_entries.append({
                        "type_name": tname,
                        "family_name": fname if fname else None,
                        "instance_count": cnt,
                    })
                # Sort by instance_count desc, then type_name
                type_entries.sort(key=lambda t: (-t["instance_count"], t["type_name"] or u""))

                cap_applied = False
                if top_n_types_per_category and len(type_entries) > top_n_types_per_category:
                    type_entries = type_entries[:top_n_types_per_category]
                    cap_applied = True
                    truncated_types_per_cat = True

                type_count = len(set(t["type_name"] for t in type_entries))
                fam_count = len(set((t["family_name"] or u"") for t in type_entries
                                    if t["family_name"]))

                cat_out = {
                    "category_name": cs["category_name"],
                    "element_count": cs["element_count"],
                    "type_count": type_count,
                    "family_count": fam_count,
                    "types": type_entries,
                    "types_truncated": cap_applied,
                }
                categories_out.append(cat_out)

            categories_out.sort(key=lambda c: (-c["element_count"], c["category_name"]))

            truncated_categories = False
            if top_n_categories and len(categories_out) > top_n_categories:
                categories_out = categories_out[:top_n_categories]
                truncated_categories = True

            # ----- Per-level rollup -----
            levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
            # Sort by elevation asc (matches Sparx)
            levels.sort(key=lambda l: l.Elevation)

            level_stats = []
            for lvl in levels:
                lvl_id = lvl.Id
                # Count instances at this level (project-wide; not scoped by
                # category_filter / view, to give honest level-occupancy data)
                try:
                    lvl_count = DB.FilteredElementCollector(doc) \
                        .WhereElementIsNotElementType() \
                        .WherePasses(DB.ElementLevelFilter(lvl_id)) \
                        .GetElementCount()
                except Exception:
                    # Fallback to manual iteration if ElementLevelFilter misbehaves
                    lvl_count = 0
                    for el in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
                        try:
                            if el.LevelId == lvl_id:
                                lvl_count += 1
                        except Exception:
                            continue

                level_stats.append({
                    "level_id": element_id_value(lvl_id),
                    "level_name": normalize_string(get_element_name(lvl)),
                    "elevation_mm": round(_ft_to_mm(lvl.Elevation), 3),
                    "element_count": lvl_count,
                })

            return routes.make_response(data={
                "status": "success",
                "project_name": project_name,
                "totals": {
                    "elements": total_elements,
                    "types": total_types,
                    "families": len(family_names),
                    "views": total_views,
                    "sheets": total_sheets,
                },
                "categories": categories_out,
                "levels": level_stats,
                "applied_filters": applied_filters,
                "view_source": view_source,
                "view_id": element_id_value(view.Id) if view is not None else None,
                "view_name": normalize_string(get_element_name(view)) if view is not None else None,
                "invalid_category_names": invalid_cats,
                "truncated_categories": truncated_categories,
                "truncated_types_per_category": truncated_types_per_cat,
            })

        except Exception as e:
            logger.error("analyze_model_statistics failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Model-statistics routes registered successfully")
