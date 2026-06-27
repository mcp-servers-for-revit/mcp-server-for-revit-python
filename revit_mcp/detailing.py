# -*- coding: UTF-8 -*-
"""
2D detailing routes: markups, detail/model/symbolic shapes (ported from
Revit-2026-MCP-Server) + detail-component placement, filled regions, and
masking regions (new). Units in FEET, angles in DEGREES.
"""

from utils import get_element_name, find_family_symbol_safely, element_id_value
from pyrevit import routes, DB
import json, math, traceback, logging

logger = logging.getLogger(__name__)


def _parse(request):
    if not request or not request.data:
        return {}
    return json.loads(request.data) if isinstance(request.data, str) else request.data


def _pt(d):
    return DB.XYZ(float(d.get("x", 0)), float(d.get("y", 0)), float(d.get("z", 0)))


def _view(doc, b):
    if b.get("view_id"):
        el = doc.GetElement(DB.ElementId(int(b["view_id"])))
        if isinstance(el, DB.View):
            return el
    if b.get("view_name"):
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
            if not v.IsTemplate and get_element_name(v) == b["view_name"]:
                return v
    return doc.ActiveView


def _shape_points(shape, center, w, h, r, sides, rot_deg):
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy, cz = center.X, center.Y, center.Z
    def rotxy(dx, dy):
        return DB.XYZ(cx + dx * ca - dy * sa, cy + dx * sa + dy * ca, cz)
    s = shape.lower()
    if s == "rectangle":
        hw, hh = w / 2.0, h / 2.0
        return [rotxy(-hw, -hh), rotxy(hw, -hh), rotxy(hw, hh), rotxy(-hw, hh)]
    if s == "circle":
        n = 36
        return [rotxy(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    # polygon
    n = max(3, int(sides))
    return [rotxy(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _err(e):
    return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)


def register_detailing_routes(api):

    @api.route("/create_point_markup/", methods=["POST"])
    def create_point_markup(doc, request):
        """Detail markups at points. Body: {points:[{x,y,z},...], markup_type:'cross|circle|square', size:1.0}"""
        try:
            b = _parse(request); view = doc.ActiveView
            pts = b.get("points") or []
            mt = (b.get("markup_type") or "cross").lower(); size = float(b.get("size", 1.0))
            t = DB.Transaction(doc, "Point Markups"); t.Start(); made = []
            for pd in pts:
                c = _pt(pd)
                if mt == "circle":
                    for (a0, a1) in ((0, math.pi), (math.pi, 2 * math.pi)):
                        arc = DB.Arc.Create(c, size, a0, a1, DB.XYZ.BasisX, DB.XYZ.BasisY)
                        made.append(element_id_value(doc.Create.NewDetailCurve(view, arc).Id))
                elif mt == "square":
                    pts4 = [DB.XYZ(c.X - size, c.Y - size, c.Z), DB.XYZ(c.X + size, c.Y - size, c.Z),
                            DB.XYZ(c.X + size, c.Y + size, c.Z), DB.XYZ(c.X - size, c.Y + size, c.Z)]
                    for i in range(4):
                        made.append(element_id_value(doc.Create.NewDetailCurve(view, DB.Line.CreateBound(pts4[i], pts4[(i + 1) % 4])).Id))
                else:  # cross
                    h = DB.Line.CreateBound(DB.XYZ(c.X - size, c.Y, c.Z), DB.XYZ(c.X + size, c.Y, c.Z))
                    v = DB.Line.CreateBound(DB.XYZ(c.X, c.Y - size, c.Z), DB.XYZ(c.X, c.Y + size, c.Z))
                    made.append(element_id_value(doc.Create.NewDetailCurve(view, h).Id))
                    made.append(element_id_value(doc.Create.NewDetailCurve(view, v).Id))
            t.Commit()
            return routes.make_response(data={"status": "success", "markup_type": mt, "curve_ids": made})
        except Exception as e: return _err(e)

    def _shapes(doc, request, mode):
        b = _parse(request)
        shape = b["shape_type"]
        center = DB.XYZ(float(b.get("center_x", 0)), float(b.get("center_y", 0)), float(b.get("center_z", 0)))
        pts = _shape_points(shape, center, float(b.get("width", 5)), float(b.get("height", 5)),
                            float(b.get("radius", 5)), int(b.get("sides", 6)), float(b.get("rotation", 0)))
        view = _view(doc, b)
        sp = None
        if mode == "model":
            sp = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, pts[0]))
        elif mode == "symbolic":
            if not doc.IsFamilyDocument:
                return routes.make_response(data={"error": "symbolic shapes require a family document"}, status=400)
            sp = DB.SketchPlane.Create(doc, DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, pts[0]))
        t = DB.Transaction(doc, "Create %s Shape" % mode); t.Start(); ids = []
        for i in range(len(pts)):
            ln = DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)])
            if mode == "detail":
                ids.append(element_id_value(doc.Create.NewDetailCurve(view, ln).Id))
            elif mode == "model":
                ids.append(element_id_value(doc.Create.NewModelCurve(ln, sp).Id))
            else:
                ids.append(element_id_value(doc.FamilyCreate.NewSymbolicCurve(ln, sp).Id))
        t.Commit()
        return routes.make_response(data={"status": "success", "shape_type": shape, "curve_ids": ids})

    @api.route("/create_detail_shapes/", methods=["POST"])
    def create_detail_shapes(doc, request):
        """Body: {shape_type:'rectangle|circle|polygon', center_x,center_y, width,height,radius,sides,rotation}"""
        try: return _shapes(doc, request, "detail")
        except Exception as e: return _err(e)

    @api.route("/create_model_shapes/", methods=["POST"])
    def create_model_shapes(doc, request):
        try: return _shapes(doc, request, "model")
        except Exception as e: return _err(e)

    @api.route("/create_symbolic_shapes/", methods=["POST"])
    def create_symbolic_shapes(doc, request):
        try: return _shapes(doc, request, "symbolic")
        except Exception as e: return _err(e)

    @api.route("/place_detail_component/", methods=["POST"])
    def place_detail_component(doc, request):
        """Place a 2D detail-component family instance in a detail/drafting view.
        Body: {family:'...', type:'...', x, y, view_name:'...', rotation:0}"""
        try:
            b = _parse(request)
            sym = find_family_symbol_safely(doc, b.get("family"), b.get("type"))
            if not sym:
                return routes.make_response(data={"error": "detail component type not found"}, status=404)
            view = _view(doc, b)
            t = DB.Transaction(doc, "Place Detail Component"); t.Start()
            if not sym.IsActive:
                sym.Activate(); doc.Regenerate()
            pt = DB.XYZ(float(b.get("x", 0)), float(b.get("y", 0)), 0)
            inst = doc.Create.NewFamilyInstance(pt, sym, view)
            if b.get("rotation"):
                ax = DB.Line.CreateBound(pt, pt + DB.XYZ.BasisZ)
                DB.ElementTransformUtils.RotateElement(doc, inst.Id, ax, math.radians(float(b["rotation"])))
            t.Commit()
            return routes.make_response(data={"status": "success", "element_id": element_id_value(inst.Id),
                                              "view": get_element_name(view)})
        except Exception as e: return _err(e)

    @api.route("/create_filled_region/", methods=["POST"])
    def create_filled_region(doc, request):
        """Filled region from a boundary loop. Body: {boundary:[{x,y,z},...], view_name, type_name(opt)}"""
        try:
            b = _parse(request); view = _view(doc, b)
            pts = [_pt(p) for p in b["boundary"]]
            from System.Collections.Generic import List
            loop = DB.CurveLoop()
            for i in range(len(pts)):
                loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
            loops = List[DB.CurveLoop](); loops.Add(loop)
            frt = None
            for ft in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType):
                if not b.get("type_name") or get_element_name(ft) == b["type_name"]:
                    frt = ft; break
            if frt is None:
                return routes.make_response(data={"error": "no FilledRegionType available"}, status=404)
            t = DB.Transaction(doc, "Create Filled Region"); t.Start()
            fr = DB.FilledRegion.Create(doc, frt.Id, view.Id, loops); t.Commit()
            return routes.make_response(data={"status": "success", "filled_region_id": element_id_value(fr.Id),
                                              "type": get_element_name(frt)})
        except Exception as e: return _err(e)

    @api.route("/create_masking_region/", methods=["POST"])
    def create_masking_region(doc, request):
        """Masking region (filled region whose type is masking, else first type).
        Body: {boundary:[{x,y,z},...], view_name}"""
        try:
            b = _parse(request); view = _view(doc, b)
            pts = [_pt(p) for p in b["boundary"]]
            from System.Collections.Generic import List
            loop = DB.CurveLoop()
            for i in range(len(pts)):
                loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % len(pts)]))
            loops = List[DB.CurveLoop](); loops.Add(loop)
            frt = None
            for ft in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType):
                try:
                    if ft.IsMasking:
                        frt = ft; break
                except Exception:
                    pass
                if frt is None:
                    frt = ft
            if frt is None:
                return routes.make_response(data={"error": "no FilledRegionType available"}, status=404)
            t = DB.Transaction(doc, "Create Masking Region"); t.Start()
            fr = DB.FilledRegion.Create(doc, frt.Id, view.Id, loops); t.Commit()
            return routes.make_response(data={"status": "success", "masking_region_id": element_id_value(fr.Id),
                                              "type": get_element_name(frt)})
        except Exception as e: return _err(e)

    logger.info("Detailing routes registered successfully")
