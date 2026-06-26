# -*- coding: UTF-8 -*-
"""
Ported Creation Routes for Revit MCP (from tools/create_*.ts).
Implemented against the pyRevit routes API. Units are in FEET (Revit internal),
not mm as in the original TypeScript tools.

Ports: create_point_based_element, create_line_based_element,
       create_surface_based_element (floors), create_room
"""

from utils import get_element_name, find_family_symbol_safely, element_id_value
from pyrevit import routes, DB
import json
import traceback
import logging

logger = logging.getLogger(__name__)
ST = DB.Structure.StructuralType


def _parse(request):
    if not request or not request.data:
        return {}
    return json.loads(request.data) if isinstance(request.data, str) else request.data


def _level_by_name(doc, name):
    for l in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Levels)
              .WhereElementIsNotElementType().ToElements()):
        if get_element_name(l) == name:
            return l
    return None


def _level_nearest(doc, z):
    best = None
    bd = 1e9
    for l in (DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Levels)
              .WhereElementIsNotElementType().ToElements()):
        d = abs(l.Elevation - z)
        if d < bd:
            bd = d
            best = l
    return best


def _resolve_type(doc, item):
    """Resolve a type from 'typeId' (int) or 'family'+'type' names."""
    if item.get("typeId") is not None:
        el = doc.GetElement(DB.ElementId(int(item["typeId"])))
        return el
    if item.get("family") and item.get("type"):
        return find_family_symbol_safely(doc, item["family"], item["type"])
    return None


def register_ported_create_routes(api):

    @api.route("/create_point_based_element/", methods=["POST"])
    def create_point_based_element(doc, request):
        """Batch-create point-based family instances. data:[{family,type|typeId,
        locationPoint:{x,y,z}, level?, rotation?}] (feet, degrees)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            items = _parse(request).get("data") or []
            if not items:
                return routes.make_response(data={"error": "No 'data' provided"}, status=400)
            t = DB.Transaction(doc, "MCP Create Point Elements")
            t.Start()
            made = []
            errs = []
            try:
                for it in items:
                    sym = _resolve_type(doc, it)
                    if not sym or not isinstance(sym, DB.FamilySymbol):
                        errs.append("type not found for %s" % it.get("name"))
                        continue
                    if not sym.IsActive:
                        sym.Activate()
                        doc.Regenerate()
                    lp = it.get("locationPoint", {})
                    pt = DB.XYZ(float(lp.get("x", 0)), float(lp.get("y", 0)), float(lp.get("z", 0)))
                    lvl = _level_by_name(doc, it.get("level")) or _level_nearest(doc, pt.Z)
                    inst = doc.Create.NewFamilyInstance(pt, sym, lvl, ST.NonStructural)
                    if it.get("rotation"):
                        ax = DB.Line.CreateBound(pt, pt.Add(DB.XYZ(0, 0, 1)))
                        inst.Location.Rotate(ax, float(it["rotation"]) * 3.14159265 / 180.0)
                    made.append(element_id_value(inst.Id))
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_line_based_element/", methods=["POST"])
    def create_line_based_element(doc, request):
        """Batch-create line-based elements (walls or structural framing).
        data:[{family,type|typeId, locationLine:{p0:{x,y,z},p1:{x,y,z}},
        height?, baseLevel?, baseOffset?, level?}] (feet). Walls use WallType; framing uses a FamilySymbol."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            items = _parse(request).get("data") or []
            if not items:
                return routes.make_response(data={"error": "No 'data' provided"}, status=400)
            t = DB.Transaction(doc, "MCP Create Line Elements")
            t.Start()
            made = []
            errs = []
            try:
                for it in items:
                    ll = it.get("locationLine", {})
                    p0 = ll.get("p0", {})
                    p1 = ll.get("p1", {})
                    a = DB.XYZ(float(p0.get("x", 0)), float(p0.get("y", 0)), float(p0.get("z", 0)))
                    b = DB.XYZ(float(p1.get("x", 0)), float(p1.get("y", 0)), float(p1.get("z", 0)))
                    if a.DistanceTo(b) < 0.1:
                        errs.append("degenerate %s" % it.get("name"))
                        continue
                    line = DB.Line.CreateBound(a, b)
                    et = _resolve_type(doc, it)
                    lvl = _level_by_name(doc, it.get("level")) or _level_nearest(doc, a.Z)
                    if isinstance(et, DB.WallType):
                        h = float(it.get("height", 10.0))
                        off = float(it.get("baseOffset", 0.0))
                        w = DB.Wall.Create(doc, line, et.Id, lvl.Id, h, off, False, False)
                        made.append(element_id_value(w.Id))
                    elif isinstance(et, DB.FamilySymbol):
                        if not et.IsActive:
                            et.Activate()
                            doc.Regenerate()
                        inst = doc.Create.NewFamilyInstance(line, et, lvl, ST.Beam)
                        made.append(element_id_value(inst.Id))
                    else:
                        errs.append("type not found for %s" % it.get("name"))
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_surface_based_element/", methods=["POST"])
    def create_surface_based_element(doc, request):
        """Batch-create floors from boundary loops. data:[{family,type|typeId,
        boundary:{outerLoop:[{p0,p1},...]}, level?, baseOffset?}] (feet)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            items = _parse(request).get("data") or []
            if not items:
                return routes.make_response(data={"error": "No 'data' provided"}, status=400)
            from System.Collections.Generic import List
            t = DB.Transaction(doc, "MCP Create Floors")
            t.Start()
            made = []
            errs = []
            try:
                for it in items:
                    seg = (it.get("boundary", {}) or {}).get("outerLoop") or []
                    if len(seg) < 3:
                        errs.append("need >=3 boundary segments for %s" % it.get("name"))
                        continue
                    loop = DB.CurveLoop()
                    for s in seg:
                        p0 = s["p0"]
                        p1 = s["p1"]
                        loop.Append(DB.Line.CreateBound(
                            DB.XYZ(float(p0["x"]), float(p0["y"]), float(p0["z"])),
                            DB.XYZ(float(p1["x"]), float(p1["y"]), float(p1["z"]))))
                    ft = _resolve_type(doc, it)
                    if not ft:
                        ft = doc.GetElement(DB.FilteredElementCollector(doc)
                                            .OfClass(DB.FloorType).FirstElementId())
                    lvl = _level_by_name(doc, it.get("level")) or _level_nearest(doc, float(seg[0]["p0"]["z"]))
                    loops = List[DB.CurveLoop]()
                    loops.Add(loop)
                    fl = DB.Floor.Create(doc, loops, ft.Id, lvl.Id)
                    if it.get("baseOffset"):
                        pp = fl.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
                        if pp:
                            pp.Set(float(it["baseOffset"]))
                    made.append(element_id_value(fl.Id))
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_room/", methods=["POST"])
    def create_room(doc, request):
        """Create rooms at points. data:[{level, x, y, name?, number?}] (feet)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            items = _parse(request).get("data") or _parse(request).get("rooms") or []
            if not items:
                return routes.make_response(data={"error": "No room data provided"}, status=400)
            t = DB.Transaction(doc, "MCP Create Rooms")
            t.Start()
            made = []
            errs = []
            try:
                for it in items:
                    lvl = _level_by_name(doc, it.get("level"))
                    if not lvl:
                        errs.append("level not found: %s" % it.get("level"))
                        continue
                    rm = doc.Create.NewRoom(lvl, DB.UV(float(it.get("x", 0)), float(it.get("y", 0))))
                    if it.get("name"):
                        rm.get_Parameter(DB.BuiltInParameter.ROOM_NAME).Set(str(it["name"]))
                    if it.get("number"):
                        rm.get_Parameter(DB.BuiltInParameter.ROOM_NUMBER).Set(str(it["number"]))
                    made.append(element_id_value(rm.Id))
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "created": made, "errors": errs[:10]})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Ported create routes registered successfully")
