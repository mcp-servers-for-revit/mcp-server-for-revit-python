# -*- coding: UTF-8 -*-
"""
Ported Operations Routes for Revit MCP (from tools/operate_element.ts, tag_all_rooms.ts,
analyze_model_statistics.ts, ai_element_filter.ts) + a smart schedule generator.
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
    return json.loads(request.data) if isinstance(request.data, str) else request.data


def _ids(doc, id_list):
    from System.Collections.Generic import List
    return List[DB.ElementId]([DB.ElementId(int(i)) for i in id_list])


def _cat_id(doc, name):
    key = name if name.startswith("OST_") else "OST_" + name.replace(" ", "")
    bic = getattr(DB.BuiltInCategory, key, None)
    return DB.ElementId(bic) if bic is not None else None


def register_ported_ops_routes(api):

    @api.route("/operate_element/", methods=["POST"])
    def operate_element(doc, request):
        """Operate on elements. Body: {"elementIds":[..], "action":"Select|Hide|Unhide|
        Isolate|ResetIsolate|SetColor|SetTransparency|Delete|Highlight",
        "colorValue":[r,g,b], "transparencyValue":0-100}."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            action = (b.get("action") or "").lower()
            ids = b.get("elementIds") or []
            view = doc.ActiveView
            if action == "select":
                revit.uidoc.Selection.SetElementIds(_ids(doc, ids))
                return routes.make_response(data={"status": "success", "action": "Select", "count": len(ids)})
            t = DB.Transaction(doc, "MCP Operate Elements")
            t.Start()
            try:
                if action in ("hide",):
                    view.HideElements(_ids(doc, ids))
                elif action == "unhide":
                    view.UnhideElements(_ids(doc, ids))
                elif action == "isolate":
                    view.IsolateElementsTemporary(_ids(doc, ids))
                elif action == "resetisolate":
                    view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate)
                elif action == "delete":
                    doc.Delete(_ids(doc, ids))
                elif action in ("setcolor", "highlight", "settransparency"):
                    ogs = DB.OverrideGraphicSettings()
                    if action in ("setcolor", "highlight"):
                        rgb = b.get("colorValue", [255, 0, 0]) if action == "setcolor" else [255, 0, 0]
                        col = DB.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                        ogs.SetProjectionLineColor(col)
                        ogs.SetSurfaceForegroundPatternColor(col)
                        sfp = DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement).FirstElement()
                        if sfp:
                            ogs.SetSurfaceForegroundPatternId(sfp.Id)
                    if action == "settransparency":
                        ogs.SetSurfaceTransparency(int(b.get("transparencyValue", 50)))
                    for i in ids:
                        view.SetElementOverrides(DB.ElementId(int(i)), ogs)
                else:
                    t.RollBack()
                    return routes.make_response(data={"error": "unsupported action: %s" % action}, status=400)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "action": action, "count": len(ids)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/tag_all_rooms/", methods=["POST"])
    def tag_all_rooms(doc, request):
        """Tag all rooms in the active view at their centers."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            use_leader = bool(_parse(request).get("useLeader", False))
            view = doc.ActiveView
            rooms = list(DB.FilteredElementCollector(doc, view.Id)
                         .OfCategory(DB.BuiltInCategory.OST_Rooms).WhereElementIsNotElementType())
            t = DB.Transaction(doc, "MCP Tag Rooms")
            t.Start()
            n = 0
            try:
                for r in rooms:
                    try:
                        loc = r.Location
                        pt = loc.Point if hasattr(loc, "Point") else None
                        if pt is None:
                            continue
                        uv = DB.UV(pt.X, pt.Y)
                        DB.RoomTag  # ensure available
                        doc.Create.NewRoomTag(DB.LinkElementId(r.Id), uv, view.Id)
                        n += 1
                    except Exception:
                        continue
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "tagged": n, "rooms": len(rooms)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/analyze_model_statistics/", methods=["GET"])
    def analyze_model_statistics(doc):
        """Model statistics: element counts by category, totals."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            from collections import Counter
            cats = Counter()
            total = 0
            for e in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
                try:
                    if e.Category:
                        cats[e.Category.Name] += 1
                        total += 1
                except Exception:
                    continue
            types = DB.FilteredElementCollector(doc).WhereElementIsElementType().GetElementCount()
            top = dict(sorted(cats.items(), key=lambda kv: -kv[1])[:40])
            return routes.make_response(data={"status": "success", "total_instances": total,
                                              "total_types": types, "by_category": top})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/ai_element_filter/", methods=["POST"])
    def ai_element_filter(doc, request):
        """Filter elements by criteria. Body: {"category":"OST_StructuralFraming",
        "parameter":"Generic Size","operator":"contains|equals|gt|lt","value":"W16"}.
        Returns matching element ids (+ values)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            col = DB.FilteredElementCollector(doc).WhereElementIsNotElementType()
            cid = _cat_id(doc, b.get("category", "")) if b.get("category") else None
            if cid is not None:
                col = col.OfCategoryId(cid)
            pname = b.get("parameter")
            op = (b.get("operator") or "contains").lower()
            val = b.get("value")
            limit = int(b.get("limit", 300))
            out = []
            for e in col:
                if len(out) >= limit:
                    break
                if not pname:
                    out.append({"id": element_id_value(e.Id), "name": get_element_name(e)})
                    continue
                p = e.LookupParameter(pname)
                if not p:
                    continue
                if p.StorageType == DB.StorageType.String:
                    pv = p.AsString() or ""
                    ok = (val.lower() in pv.lower()) if op == "contains" else (pv == val)
                else:
                    try:
                        pv = p.AsDouble()
                        fv = float(val)
                        ok = {"gt": pv > fv, "lt": pv < fv, "equals": abs(pv - fv) < 1e-6}.get(op, False)
                    except Exception:
                        ok = False
                    pv = round(pv, 4) if isinstance(pv, float) else pv
                if ok:
                    out.append({"id": element_id_value(e.Id), "name": get_element_name(e), "value": pv})
            return routes.make_response(data={"status": "success", "count": len(out), "matches": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_schedule/", methods=["POST"])
    def create_schedule(doc, request):
        """Create a schedule for a category with chosen fields (incl. KPFF shared params).
        Body: {"category":"OST_StructuralColumns","name":"STEEL COLUMN SCHEDULE",
        "fields":["Type","Generic Size","Comments"]}."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            cid = _cat_id(doc, b.get("category", ""))
            if cid is None:
                return routes.make_response(data={"error": "invalid category"}, status=400)
            wanted = b.get("fields") or []
            t = DB.Transaction(doc, "MCP Create Schedule")
            t.Start()
            try:
                sched = DB.ViewSchedule.CreateSchedule(doc, cid)
                if b.get("name"):
                    sched.Name = b["name"]
                defn = sched.Definition
                # map available schedulable fields by display name
                avail = {}
                for sf in defn.GetSchedulableFields():
                    try:
                        avail[sf.GetName(doc)] = sf
                    except Exception:
                        continue
                added = []
                missing = []
                for fn in wanted:
                    sf = avail.get(fn)
                    if sf:
                        defn.AddField(sf)
                        added.append(fn)
                    else:
                        missing.append(fn)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "schedule_id": element_id_value(sched.Id),
                                              "name": get_element_name(sched), "fields_added": added,
                                              "fields_missing": missing})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Ported ops routes registered successfully")
