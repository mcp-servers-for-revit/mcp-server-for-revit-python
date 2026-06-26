# -*- coding: UTF-8 -*-
"""
Ported Query/Edit Routes for Revit MCP
Python implementations of TypeScript tools from tools/*.ts that previously
forwarded to a separate Revit plugin. Implemented here directly against the
pyRevit routes API. Lengths are in feet (Revit internal units).

Ports: get_current_view_info, get_selected_elements, get_current_view_elements,
       get_available_family_types, get_material_quantities, tag_all_walls,
       delete_elements, modify_element
"""

from utils import get_element_name, element_id_value
from pyrevit import routes, revit, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def _parse(request):
    if not request or not request.data:
        return {}
    if isinstance(request.data, str):
        try:
            return json.loads(request.data)
        except Exception:
            return {}
    return request.data


def _cat_to_bic(name):
    """Resolve 'OST_Walls' or 'Walls' to a BuiltInCategory, else None."""
    if not name:
        return None
    key = name if name.startswith("OST_") else "OST_" + name.replace(" ", "")
    return getattr(DB.BuiltInCategory, key, None)


def register_ported_query_routes(api):

    @api.route("/get_current_view_info/", methods=["GET"])
    def get_current_view_info(doc):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            v = doc.ActiveView
            data = {
                "name": get_element_name(v),
                "id": element_id_value(v.Id),
                "view_type": str(v.ViewType),
                "scale": getattr(v, "Scale", None),
                "detail_level": str(v.DetailLevel) if hasattr(v, "DetailLevel") else None,
                "is_template": v.IsTemplate,
            }
            try:
                if v.GenLevel:
                    data["level"] = get_element_name(v.GenLevel)
            except Exception:
                pass
            return routes.make_response(data={"status": "success", "view": data})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_selected_elements/", methods=["POST"])
    def get_selected_elements(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            limit = int(_parse(request).get("limit", 100))
            ids = list(revit.uidoc.Selection.GetElementIds())
            out = []
            for eid in ids[:limit]:
                e = doc.GetElement(eid)
                if not e:
                    continue
                out.append({
                    "id": element_id_value(eid),
                    "name": get_element_name(e),
                    "category": e.Category.Name if e.Category else None,
                    "type": (get_element_name(doc.GetElement(e.GetTypeId()))
                             if e.GetTypeId() and e.GetTypeId().IntegerValue > 0 else None),
                })
            return routes.make_response(data={"status": "success", "count": len(out),
                                              "total_selected": len(ids), "elements": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_current_view_elements/", methods=["POST"])
    def get_current_view_elements(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            body = _parse(request)
            limit = int(body.get("limit", 200))
            col = DB.FilteredElementCollector(doc, doc.ActiveView.Id).WhereElementIsNotElementType()
            bic = _cat_to_bic(body.get("category"))
            if bic is not None:
                col = col.OfCategory(bic)
            out = []
            for e in col:
                if len(out) >= limit:
                    break
                out.append({
                    "id": element_id_value(e.Id),
                    "name": get_element_name(e),
                    "category": e.Category.Name if e.Category else None,
                })
            return routes.make_response(data={"status": "success", "count": len(out), "elements": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_available_family_types/", methods=["POST"])
    def get_available_family_types(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            body = _parse(request)
            limit = int(body.get("limit", 200))
            contains = (body.get("contains") or "").lower()
            bic = _cat_to_bic(body.get("category"))
            col = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)
            if bic is not None:
                col = col.OfCategory(bic)
            out = []
            for s in col:
                if len(out) >= limit:
                    break
                fam = get_element_name(s.Family)
                typ = get_element_name(s)
                if contains and contains not in fam.lower() and contains not in typ.lower():
                    continue
                out.append({"family": fam, "type": typ,
                            "category": s.Category.Name if s.Category else None,
                            "id": element_id_value(s.Id), "is_active": s.IsActive})
            return routes.make_response(data={"status": "success", "count": len(out), "types": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_material_quantities/", methods=["POST"])
    def get_material_quantities(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            body = _parse(request)
            cats = body.get("categoryFilters")
            sel_only = bool(body.get("selectedElementsOnly", False))
            if sel_only:
                ids = list(revit.uidoc.Selection.GetElementIds())
                elements = [doc.GetElement(i) for i in ids]
            else:
                elements = list(DB.FilteredElementCollector(doc).WhereElementIsNotElementType())
            wanted = None
            if cats:
                wanted = set([c if c.startswith("OST_") else "OST_" + c.replace(" ", "") for c in cats])
            mats = {}
            for e in elements:
                try:
                    if wanted is not None:
                        if not e.Category:
                            continue
                        # category enum name
                        bic = e.Category.Id.IntegerValue
                        cname = str(DB.Category.GetCategory(doc, e.Category.Id).Name) if e.Category else ""
                        # match by display name or OST key best-effort
                        if not any(w.replace("OST_", "").lower() in cname.replace(" ", "").lower() for w in wanted):
                            continue
                    for mid in e.GetMaterialIds(False):
                        m = doc.GetElement(mid)
                        if not m:
                            continue
                        key = get_element_name(m)
                        rec = mats.setdefault(key, {"material": key, "area_sf": 0.0, "volume_cf": 0.0, "elements": 0})
                        rec["area_sf"] += e.GetMaterialArea(mid, False)
                        rec["volume_cf"] += e.GetMaterialVolume(mid)
                        rec["elements"] += 1
                except Exception:
                    continue
            result = sorted(mats.values(), key=lambda r: -r["volume_cf"])
            for r in result:
                r["area_sf"] = round(r["area_sf"], 2)
                r["volume_cf"] = round(r["volume_cf"], 2)
            return routes.make_response(data={"status": "success", "materials": result, "count": len(result)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/tag_all_walls/", methods=["POST"])
    def tag_all_walls(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            body = _parse(request)
            use_leader = bool(body.get("useLeader", False))
            view = doc.ActiveView
            walls = list(DB.FilteredElementCollector(doc, view.Id)
                         .OfCategory(DB.BuiltInCategory.OST_Walls)
                         .WhereElementIsNotElementType())
            t = DB.Transaction(doc, "MCP Tag Walls")
            t.Start()
            n = 0
            try:
                for w in walls:
                    try:
                        loc = w.Location
                        if not isinstance(loc, DB.LocationCurve):
                            continue
                        m = loc.Curve.Evaluate(0.5, True)
                        DB.IndependentTag.Create(
                            doc, view.Id, DB.Reference(w), use_leader,
                            DB.TagMode.TM_ADDBY_CATEGORY, DB.TagOrientation.Horizontal,
                            DB.XYZ(m.X, m.Y, m.Z))
                        n += 1
                    except Exception:
                        continue
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "tagged": n, "walls": len(walls)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/delete_elements/", methods=["POST"])
    def delete_elements(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            ids = _parse(request).get("element_ids") or []
            if not ids:
                return routes.make_response(data={"error": "No element_ids provided"}, status=400)
            from System.Collections.Generic import List
            eids = List[DB.ElementId]([DB.ElementId(int(i)) for i in ids])
            t = DB.Transaction(doc, "MCP Delete Elements")
            t.Start()
            try:
                deleted = doc.Delete(eids)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "deleted": len(list(deleted))})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/modify_element/", methods=["POST"])
    def modify_element(doc, request):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            body = _parse(request)
            eid = body.get("element_id")
            params = body.get("parameters") or {}
            if eid is None:
                return routes.make_response(data={"error": "No element_id provided"}, status=400)
            e = doc.GetElement(DB.ElementId(int(eid)))
            if not e:
                return routes.make_response(data={"error": "Element not found"}, status=404)
            t = DB.Transaction(doc, "MCP Modify Element")
            t.Start()
            done = []
            failed = []
            try:
                for name, value in params.items():
                    p = e.LookupParameter(name)
                    if not p or p.IsReadOnly:
                        failed.append(name)
                        continue
                    st = p.StorageType
                    if st == DB.StorageType.String:
                        p.Set(str(value))
                    elif st == DB.StorageType.Integer:
                        p.Set(int(value))
                    elif st == DB.StorageType.Double:
                        p.Set(float(value))
                    elif st == DB.StorageType.ElementId:
                        p.Set(DB.ElementId(int(value)))
                    else:
                        failed.append(name)
                        continue
                    done.append(name)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "set": done, "failed": failed})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Ported query/edit routes registered successfully")
