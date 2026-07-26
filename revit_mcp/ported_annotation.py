# -*- coding: UTF-8 -*-
"""
Ported from the mcp-servers-for-revit C# command set (CreateDimension, CreateLevel,
ExportRoomData, SayHello). Reimplemented natively against the pyRevit routes API.
Units are FEET (the C# originals used mm; converted to feet to match this extension).
"""

from utils import get_element_name, element_id_value
from pyrevit import routes, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)


def _parse(request):
    if not request or not request.data:
        return {}
    return json.loads(request.data) if isinstance(request.data, str) else request.data


def _view(doc, name, vid):
    if vid and int(vid) > 0:
        el = doc.GetElement(DB.ElementId(int(vid)))
        if isinstance(el, DB.View):
            return el
    if name:
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
            if not v.IsTemplate and get_element_name(v) == name:
                return v
    return doc.ActiveView


def register_ported_annotation_routes(api):

    @api.route("/create_dimensions/", methods=["POST"])
    def create_dimensions(doc, request):
        """
        Create linear dimensions referencing elements (e.g. grids).
        Body: {"view_name":"2 - Level 2",  (or "view_id":int)
               "dimensions":[{"element_ids":[111,222,...],
                              "line":{"p0":{"x":..,"y":..,"z":0},"p1":{"x":..,"y":..,"z":0}}}]}
        The dimension line is placed along p0->p1; references come from element_ids
        (grids and datum elements work directly). Feet.
        """
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            dims = b.get("dimensions")
            if not dims and b.get("element_ids"):
                dims = [{"element_ids": b.get("element_ids"), "line": b.get("line")}]
            if not dims:
                return routes.make_response(data={"error": "No 'dimensions' provided"}, status=400)
            view = _view(doc, b.get("view_name"), b.get("view_id"))
            t = DB.Transaction(doc, "MCP Create Dimensions")
            t.Start()
            made = []
            errs = []
            try:
                for di in dims:
                    try:
                        ln = di.get("line") or {}
                        p0 = ln.get("p0", {})
                        p1 = ln.get("p1", {})
                        a = DB.XYZ(float(p0.get("x", 0)), float(p0.get("y", 0)), float(p0.get("z", 0)))
                        c = DB.XYZ(float(p1.get("x", 0)), float(p1.get("y", 0)), float(p1.get("z", 0)))
                        refs = DB.ReferenceArray()
                        for eid in di.get("element_ids", []):
                            el = doc.GetElement(DB.ElementId(int(eid)))
                            if el is None:
                                continue
                            try:
                                refs.Append(DB.Reference(el))
                            except Exception:
                                # datum elements (grids/levels) expose .Curve based refs
                                cur = getattr(el, "Curve", None)
                                if cur is not None and hasattr(cur, "Reference") and cur.Reference:
                                    refs.Append(cur.Reference)
                        if refs.Size < 2:
                            errs.append("need >=2 valid references")
                            continue
                        dim = doc.Create.NewDimension(view, DB.Line.CreateBound(a, c), refs)
                        made.append(element_id_value(dim.Id))
                    except Exception as de:
                        errs.append(str(de))
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_levels/", methods=["POST"])
    def create_levels(doc, request):
        """Create new levels. Body: {"levels":[{"name":"3rd Level","elevation":31.33}]} (feet)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            specs = _parse(request).get("levels") or []
            if not specs:
                return routes.make_response(data={"error": "No 'levels' provided"}, status=400)
            t = DB.Transaction(doc, "MCP Create Levels")
            t.Start()
            made = []
            errs = []
            try:
                for s in specs:
                    lvl = DB.Level.Create(doc, float(s.get("elevation", 0)))
                    if s.get("name"):
                        try:
                            lvl.Name = s["name"]
                        except Exception as ne:
                            errs.append("name '%s': %s" % (s.get("name"), ne))
                    made.append({"id": element_id_value(lvl.Id), "name": get_element_name(lvl),
                                 "elevation": round(lvl.Elevation, 4)})
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/export_room_data/", methods=["GET"])
    def export_room_data(doc):
        """Export all rooms with key data (name, number, area, level, perimeter, comments)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            rooms = (DB.FilteredElementCollector(doc)
                     .OfCategory(DB.BuiltInCategory.OST_Rooms)
                     .WhereElementIsNotElementType().ToElements())
            out = []
            for r in rooms:
                def gv(bip):
                    p = r.get_Parameter(bip)
                    return p.AsString() if p else None
                area = r.get_Parameter(DB.BuiltInParameter.ROOM_AREA)
                out.append({
                    "id": element_id_value(r.Id),
                    "name": gv(DB.BuiltInParameter.ROOM_NAME),
                    "number": gv(DB.BuiltInParameter.ROOM_NUMBER),
                    "area_sf": round(area.AsDouble(), 2) if area else None,
                    "level": get_element_name(doc.GetElement(r.LevelId)) if r.LevelId and r.LevelId.IntegerValue > 0 else None,
                    "comments": gv(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS),
                })
            return routes.make_response(data={"status": "success", "count": len(out), "rooms": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/say_hello/", methods=["GET"])
    def say_hello(doc):
        """Connection test: returns a greeting + basic document info (no modal dialog)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            return routes.make_response(data={
                "status": "success",
                "message": "Hello from the Revit MCP (Python) — connection OK.",
                "document": doc.Title,
                "active_view": get_element_name(doc.ActiveView),
            })
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Ported annotation/misc routes registered successfully")
