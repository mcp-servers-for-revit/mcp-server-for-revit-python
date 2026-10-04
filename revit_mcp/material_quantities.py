# -*- coding: UTF-8 -*-
"""
Material Quantities Module for Revit MCP
Walk model elements and sum up material volumes / areas, grouped by material.

This is a READ-ONLY data-extraction tool — no transactions. The output rolls
up every element that carries each material (via Element.GetMaterialIds /
GetMaterialVolume / GetMaterialArea) so you can answer "how much concrete is
in the model?" or "what's the total brick area in walls + floors?" in a
single call.

Units: outputs are in m3 (`volume_m3`) and m2 (`area_m2`). Revit's internal
units are ft3 / ft2; conversion is via UnitUtils.

Ported from Sparx mcp-servers-for-revit's GetMaterialQuantitiesCommand
(Apache-2.0 licensed C# source at commandset/Services/DataExtraction/
GetMaterialQuantitiesEventHandler.cs) into our IronPython pyRevit Routes
pattern.

Differences from the C# source:
- Output units are m3 / m2 (Sparx returned ft3 / ft2 verbatim).
- Adds optional `element_ids` (restrict to a caller-supplied set) and
  `view_id` (restrict to elements visible in a view) inputs; Sparx only had
  category_filters + selectedElementsOnly.
- Returns per-material `categories` list (every category the material was
  found on) — useful for the "is this brick on walls only or also on
  floors?" question. Sparx didn't expose this.
- `include_zero_volume=False` by default drops Material entries that
  contribute zero m3 (these are usually thin-paint or decorative
  applications that GetMaterialIds(False) shouldn't include but
  occasionally leak through).
"""

from pyrevit import routes, revit, DB
from utils import normalize_string, element_id_value
from System import Int64
import json
import traceback
import logging

logger = logging.getLogger(__name__)

FT3_TO_M3 = 0.0283168
FT2_TO_M2 = 0.092903


def _parse_json_request(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        return json.loads(request.data)
    return request.data


def _ft3_to_m3(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.CubicMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_CUBIC_METERS)
    except Exception:
        return float(v) * FT3_TO_M3


def _ft2_to_m2(v):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.UnitTypeId.SquareMeters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(float(v), DB.DisplayUnitType.DUT_SQUARE_METERS)
    except Exception:
        return float(v) * FT2_TO_M2


def _safe_category_name(elem):
    try:
        cat = elem.Category
        if cat is None:
            return None
        return normalize_string(cat.Name)
    except Exception:
        return None


def _resolve_view(doc, view_id):
    """Return (view, source) or (None, reason). view_id=None means no view filter."""
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
        # Accept both "OST_Walls" and "Walls" forms; the former is the
        # canonical BuiltInCategory enum member.
        ename = name if name.startswith("OST_") else "OST_" + name
        member = getattr(DB.BuiltInCategory, ename, None)
        if member is None:
            invalid.append(name)
        else:
            enums.append(member)
    return enums, invalid


def _collect_elements(doc, view, category_enums, element_ids):
    """
    Build the element set per the input filters. Returns (list[Element], picker_path, invalid_element_ids).

    Precedence: explicit element_ids > view filter > whole model.
    Category filter is layered on top of any of those.
    """
    invalid_ids = []
    if element_ids:
        elements = []
        for eid in element_ids:
            try:
                el = doc.GetElement(DB.ElementId(Int64(int(eid))))
            except Exception:
                el = None
            if el is None:
                invalid_ids.append(eid)
                continue
            # Skip ElementType subclasses (analogous to WhereElementIsNotElementType)
            if isinstance(el, DB.ElementType):
                invalid_ids.append(eid)
                continue
            elements.append(el)
        # Apply category filter in-process for the explicit-id path
        if category_enums:
            allowed = set(int(e) for e in category_enums)
            kept = []
            for el in elements:
                cat = el.Category
                if cat is None:
                    continue
                try:
                    if int(cat.Id.IntegerValue if hasattr(cat.Id, "IntegerValue") else cat.Id.Value) in allowed:
                        kept.append(el)
                except Exception:
                    continue
            elements = kept
        return elements, "explicit_element_ids", invalid_ids

    if view is not None:
        collector = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
        picker_path = "view_visible_elements"
    else:
        collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType()
        picker_path = "whole_model"

    if category_enums:
        try:
            # ElementMulticategoryFilter wants a .NET List[BuiltInCategory]
            from System.Collections.Generic import List as NetList
            bic_list = NetList[DB.BuiltInCategory]()
            for e in category_enums:
                bic_list.Add(e)
            collector = collector.WherePasses(DB.ElementMulticategoryFilter(bic_list))
            picker_path = picker_path + "+category_filter"
        except Exception as ex:
            logger.warning("category filter failed, falling back to no-filter: %s", str(ex))

    return list(collector), picker_path, invalid_ids


def register_material_quantities_routes(api):
    """Register material-quantity extraction routes."""

    @api.route("/get_material_quantities/", methods=["POST"])
    def get_material_quantities(doc, request):
        """
        Roll up material volumes and areas across the model (or a subset).

        Expected payload (all fields optional):
        {
            "category_filter":     ["OST_Walls", "OST_Floors"],  # null = all categories
            "element_ids":         [123, 456],                   # null = all elements
            "view_id":             null,                          # only elements visible in this view
            "include_zero_volume": false                          # include materials with 0 m3
        }

        Returns status='success' with a `materials` list rolled up by material,
        plus a `totals` block. Returns status='no_elements_found' when the
        filter set has no elements.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)

            data = _parse_json_request(request)

            # ----- Parse inputs -----
            category_filter_raw = data.get("category_filter")
            element_ids_raw = data.get("element_ids") or []
            if not isinstance(element_ids_raw, list):
                return routes.make_response(data={
                    "error": "element_ids must be a list of integers"
                }, status=400)
            include_zero_volume = bool(data.get("include_zero_volume", False))

            # Resolve view (None = no view filter; "ERROR" = invalid view_id supplied)
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

            # ----- Build element set -----
            elements, picker_path, invalid_ids = _collect_elements(doc, view, category_enums, element_ids_raw)

            if not elements:
                return routes.make_response(data={
                    "status": "no_elements_found",
                    "picker_path": picker_path,
                    "view_source": view_source,
                    "invalid_element_ids": invalid_ids,
                    "invalid_category_names": invalid_cats,
                })

            # ----- Accumulate material quantities -----
            # Key on material ElementId int value.
            # Each entry holds: dict with name, class, volume_ft3, area_ft2,
            # element_ids set, categories set.
            mat_data = {}

            for elem in elements:
                try:
                    material_ids = elem.GetMaterialIds(False)
                except Exception:
                    # Element type doesn't carry materials (e.g. annotation, view, etc.)
                    continue

                if material_ids is None:
                    continue

                elem_id_v = element_id_value(elem.Id)
                elem_cat = _safe_category_name(elem)

                for mat_id in material_ids:
                    material = doc.GetElement(mat_id)
                    if material is None or not isinstance(material, DB.Material):
                        continue

                    try:
                        vol = float(elem.GetMaterialVolume(mat_id))
                    except Exception:
                        vol = 0.0
                    try:
                        area = float(elem.GetMaterialArea(mat_id, False))
                    except Exception:
                        area = 0.0

                    mid_v = element_id_value(mat_id)
                    entry = mat_data.get(mid_v)
                    if entry is None:
                        entry = {
                            "material_id": mid_v,
                            "material_name": normalize_string(material.Name),
                            "material_class": normalize_string(material.MaterialClass) if material.MaterialClass else None,
                            "volume_ft3": 0.0,
                            "area_ft2": 0.0,
                            "element_ids": set(),
                            "categories": set(),
                        }
                        mat_data[mid_v] = entry

                    entry["volume_ft3"] += vol
                    entry["area_ft2"] += area
                    entry["element_ids"].add(elem_id_v)
                    if elem_cat:
                        entry["categories"].add(elem_cat)

            # ----- Project to output shape -----
            materials_out = []
            total_vol_ft3 = 0.0
            total_area_ft2 = 0.0
            total_element_id_set = set()

            for entry in mat_data.values():
                vol_m3 = _ft3_to_m3(entry["volume_ft3"])
                area_m2 = _ft2_to_m2(entry["area_ft2"])

                if not include_zero_volume and vol_m3 <= 0.0 and area_m2 <= 0.0:
                    continue

                materials_out.append({
                    "material_id": entry["material_id"],
                    "material_name": entry["material_name"],
                    "material_class": entry["material_class"],
                    "volume_m3": round(vol_m3, 4),
                    "area_m2": round(area_m2, 4),
                    "element_count": len(entry["element_ids"]),
                    "categories": sorted(list(entry["categories"])),
                })
                total_vol_ft3 += entry["volume_ft3"]
                total_area_ft2 += entry["area_ft2"]
                total_element_id_set.update(entry["element_ids"])

            # Sort by volume descending, then by area, then by name
            materials_out.sort(key=lambda m: (-m["volume_m3"], -m["area_m2"], m["material_name"]))

            return routes.make_response(data={
                "status": "success",
                "materials": materials_out,
                "totals": {
                    "material_count": len(materials_out),
                    "volume_m3": round(_ft3_to_m3(total_vol_ft3), 4),
                    "area_m2": round(_ft2_to_m2(total_area_ft2), 4),
                    "element_count": len(total_element_id_set),
                },
                "picker_path": picker_path,
                "view_source": view_source,
                "view_name": normalize_string(view.Name) if view is not None else None,
                "view_id": element_id_value(view.Id) if view is not None else None,
                "elements_scanned": len(elements),
                "invalid_element_ids": invalid_ids,
                "invalid_category_names": invalid_cats,
            })

        except Exception as e:
            logger.error("get_material_quantities failed: {}".format(traceback.format_exc()))
            return routes.make_response(data={
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, status=500)

    logger.info("Material quantities routes registered successfully")
