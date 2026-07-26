# -*- coding: UTF-8 -*-
"""
Geometry routes (curves, splines, arcs, points, transforms, curve math).
Ported from the Revit-2026-MCP-Server C# command set. Units in FEET, angles in DEGREES.
Curve-operation tools reference an existing curve element via 'curve_element_id'
(its GeometryCurve). Creation tools make Model/Detail curves.
"""

from utils import get_element_name, element_id_value
from pyrevit import routes, DB
import json, math, traceback, logging

logger = logging.getLogger(__name__)


def _parse(request):
    if not request or not request.data:
        return {}
    return json.loads(request.data) if isinstance(request.data, str) else request.data


def _pt(d):
    return DB.XYZ(float(d.get("x", 0)), float(d.get("y", 0)), float(d.get("z", 0)))


def _ptlist(arr):
    return [_pt(p) for p in arr]


def _curve_elem(doc, eid):
    el = doc.GetElement(DB.ElementId(int(eid)))
    if not isinstance(el, DB.CurveElement):
        return None, None
    return el, el.GeometryCurve


def _sketchplane_z(doc, origin):
    pl = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, origin)
    return DB.SketchPlane.Create(doc, pl)


def _new_like(doc, src_elem, new_curve):
    """Create a model/detail curve element matching the source element's context."""
    if isinstance(src_elem, DB.DetailCurve):
        v = doc.GetElement(src_elem.OwnerViewId)
        return doc.Create.NewDetailCurve(v, new_curve)
    sp = src_elem.SketchPlane if isinstance(src_elem, DB.ModelCurve) else None
    if sp is None:
        sp = _sketchplane_z(doc, new_curve.GetEndPoint(0))
    return doc.Create.NewModelCurve(new_curve, sp)


def _resp(d):
    return routes.make_response(data=d)


def _err(e):
    return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)


def register_geometry_routes(api):

    @api.route("/create_bounded_line/", methods=["POST"])
    def create_bounded_line(doc, request):
        """Model line. Body: {start:{x,y,z}, end:{x,y,z}}"""
        try:
            b = _parse(request)
            a, c = _pt(b["start"]), _pt(b["end"])
            t = DB.Transaction(doc, "Create Bounded Line"); t.Start()
            mc = doc.Create.NewModelCurve(DB.Line.CreateBound(a, c), _sketchplane_z(doc, a))
            t.Commit()
            return _resp({"status": "success", "line_id": element_id_value(mc.Id)})
        except Exception as e: return _err(e)

    @api.route("/create_curves_from_points/", methods=["POST"])
    def create_curves_from_points(doc, request):
        """Connected model lines. Body: {points:[{x,y,z},...], closed:false}"""
        try:
            b = _parse(request); pts = _ptlist(b["points"]); closed = bool(b.get("closed"))
            if len(pts) < 2: return routes.make_response(data={"error": "need >=2 points"}, status=400)
            sp = _sketchplane_z(doc, pts[0])
            t = DB.Transaction(doc, "Create Curves From Points"); t.Start()
            ids = []
            n = len(pts) if closed else len(pts) - 1
            for i in range(n):
                a = pts[i]; c = pts[0] if i == len(pts) - 1 else pts[i + 1]
                ids.append(element_id_value(doc.Create.NewModelCurve(DB.Line.CreateBound(a, c), sp).Id))
            t.Commit()
            return _resp({"status": "success", "curve_ids": ids, "closed": closed})
        except Exception as e: return _err(e)

    @api.route("/create_hermite_spline/", methods=["POST"])
    def create_hermite_spline(doc, request):
        """Body: {points:[...], closed:false}"""
        try:
            b = _parse(request); pts = _ptlist(b["points"]); closed = bool(b.get("closed"))
            from System.Collections.Generic import List
            lp = List[DB.XYZ](pts)
            sp = _sketchplane_z(doc, pts[0])
            t = DB.Transaction(doc, "Create Hermite Spline"); t.Start()
            spl = DB.HermiteSpline.Create(lp, closed)
            mc = doc.Create.NewModelCurve(spl, sp); t.Commit()
            return _resp({"status": "success", "spline_id": element_id_value(mc.Id), "points": len(pts)})
        except Exception as e: return _err(e)

    @api.route("/create_hermite_spline_with_tangents/", methods=["POST"])
    def create_hermite_spline_with_tangents(doc, request):
        """Body: {points:[...], start_tangent:{x,y,z}, end_tangent:{x,y,z}, closed:false}"""
        try:
            b = _parse(request); pts = _ptlist(b["points"]); closed = bool(b.get("closed"))
            from System.Collections.Generic import List
            lp = List[DB.XYZ](pts)
            tan = DB.HermiteSplineTangents()
            tan.StartTangent = _pt(b["start_tangent"]); tan.EndTangent = _pt(b["end_tangent"])
            sp = _sketchplane_z(doc, pts[0])
            t = DB.Transaction(doc, "Create Hermite Spline Tangents"); t.Start()
            spl = DB.HermiteSpline.Create(lp, closed, tan)
            mc = doc.Create.NewModelCurve(spl, sp); t.Commit()
            return _resp({"status": "success", "spline_id": element_id_value(mc.Id)})
        except Exception as e: return _err(e)

    @api.route("/create_offset_curve/", methods=["POST"])
    def create_offset_curve(doc, request):
        """Body: {curve_element_id:int, offset:ft, normal:{x,y,z}=Z}"""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            nrm = _pt(b["normal"]) if b.get("normal") else DB.XYZ.BasisZ
            t = DB.Transaction(doc, "Offset Curve"); t.Start()
            ne = _new_like(doc, el, cv.CreateOffset(float(b["offset"]), nrm)); t.Commit()
            return _resp({"status": "success", "offset_curve_id": element_id_value(ne.Id)})
        except Exception as e: return _err(e)

    @api.route("/create_clone_curve/", methods=["POST"])
    def create_clone_curve(doc, request):
        """Body: {curve_element_id:int}"""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            t = DB.Transaction(doc, "Clone Curve"); t.Start()
            ne = _new_like(doc, el, cv.Clone()); t.Commit()
            return _resp({"status": "success", "cloned_curve_id": element_id_value(ne.Id)})
        except Exception as e: return _err(e)

    @api.route("/create_grid_line/", methods=["POST"])
    def create_grid_line(doc, request):
        """Straight grid. Body: {start:{x,y,z}, end:{x,y,z}, name:'A'}"""
        try:
            b = _parse(request)
            t = DB.Transaction(doc, "Create Grid Line"); t.Start()
            g = DB.Grid.Create(doc, DB.Line.CreateBound(_pt(b["start"]), _pt(b["end"])))
            if b.get("name"):
                try: g.Name = b["name"]
                except Exception: pass
            t.Commit()
            return _resp({"status": "success", "grid_id": element_id_value(g.Id), "name": get_element_name(g)})
        except Exception as e: return _err(e)

    @api.route("/create_grid_arc/", methods=["POST"])
    def create_grid_arc(doc, request):
        """Curved (radial) grid. Body: {center:{x,y,z}, radius:ft, start_angle:deg, end_angle:deg, name:'A'}"""
        try:
            b = _parse(request)
            cen = _pt(b["center"]); r = float(b["radius"])
            a0 = math.radians(float(b.get("start_angle", 0))); a1 = math.radians(float(b.get("end_angle", 90)))
            arc = DB.Arc.Create(cen, r, a0, a1, DB.XYZ.BasisX, DB.XYZ.BasisY)
            t = DB.Transaction(doc, "Create Grid Arc"); t.Start()
            g = DB.Grid.Create(doc, arc)
            if b.get("name"):
                try: g.Name = b["name"]
                except Exception: pass
            t.Commit()
            return _resp({"status": "success", "grid_id": element_id_value(g.Id), "name": get_element_name(g)})
        except Exception as e: return _err(e)

    @api.route("/create_point/", methods=["POST"])
    def create_point(doc, request):
        """Reference point (family docs). Body: {x,y,z}. In projects returns the coordinates."""
        try:
            b = _parse(request); p = _pt(b)
            if doc.IsFamilyDocument:
                t = DB.Transaction(doc, "Create Point"); t.Start()
                rp = doc.FamilyCreate.NewReferencePoint(p); t.Commit()
                return _resp({"status": "success", "reference_point_id": element_id_value(rp.Id),
                              "point": {"x": p.X, "y": p.Y, "z": p.Z}})
            return _resp({"status": "success", "note": "ReferencePoints require a family document",
                          "point": {"x": p.X, "y": p.Y, "z": p.Z}})
        except Exception as e: return _err(e)

    @api.route("/create_point_on_element/", methods=["POST"])
    def create_point_on_element(doc, request):
        """Project a point onto an element's geometry, returning the nearest point + face reference.
        Body: {element_id:int, point:{x,y,z}}"""
        try:
            b = _parse(request); el = doc.GetElement(DB.ElementId(int(b["element_id"]))); p = _pt(b["point"])
            opt = DB.Options(); opt.ComputeReferences = True
            best = None
            geo = el.get_Geometry(opt)
            def faces(g):
                for o in g:
                    if isinstance(o, DB.Solid):
                        for f in o.Faces: yield f
                    elif isinstance(o, DB.GeometryInstance):
                        for x in faces(o.GetInstanceGeometry()): yield x
            for f in faces(geo):
                ir = f.Project(p)
                if ir is not None and (best is None or ir.Distance < best[0]):
                    best = (ir.Distance, ir.XYZPoint, f.Reference)
            if best is None:
                return routes.make_response(data={"error": "could not project onto element"}, status=404)
            pt = best[1]
            return _resp({"status": "success", "point": {"x": pt.X, "y": pt.Y, "z": pt.Z},
                          "distance": round(best[0], 4),
                          "reference": best[2].ConvertToStableRepresentation(doc) if best[2] else None})
        except Exception as e: return _err(e)

    @api.route("/calculate_line_direction/", methods=["POST"])
    def calculate_line_direction(doc, request):
        """Body: {start:{x,y,z}, end:{x,y,z}} -> normalized direction + length."""
        try:
            b = _parse(request); a, c = _pt(b["start"]), _pt(b["end"])
            v = c - a; ln = v.GetLength(); d = v.Normalize()
            return _resp({"status": "success", "direction": {"x": d.X, "y": d.Y, "z": d.Z}, "length": ln})
        except Exception as e: return _err(e)

    @api.route("/rotate_elements/", methods=["POST"])
    def rotate_elements(doc, request):
        """Body: {element_ids:[...], angle:deg, axis_point:{x,y,z}=origin, axis_dir:{x,y,z}=Z}"""
        try:
            from System.Collections.Generic import List
            b = _parse(request)
            ids = List[DB.ElementId]([DB.ElementId(int(i)) for i in b["element_ids"]])
            ap = _pt(b["axis_point"]) if b.get("axis_point") else DB.XYZ.Zero
            ad = _pt(b["axis_dir"]) if b.get("axis_dir") else DB.XYZ.BasisZ
            axis = DB.Line.CreateUnbound(ap, ad)
            t = DB.Transaction(doc, "Rotate Elements"); t.Start()
            DB.ElementTransformUtils.RotateElements(doc, ids, axis, math.radians(float(b["angle"]))); t.Commit()
            return _resp({"status": "success", "rotated": len(b["element_ids"]), "angle_deg": b["angle"]})
        except Exception as e: return _err(e)

    # ---- curve math (operate on an existing curve_element_id) ----
    @api.route("/evaluate_curve/", methods=["POST"])
    def evaluate_curve(doc, request):
        """Body: {curve_element_id, parameter, normalized:false} -> point on curve."""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            p = cv.Evaluate(float(b["parameter"]), bool(b.get("normalized", False)))
            return _resp({"status": "success", "point": {"x": p.X, "y": p.Y, "z": p.Z}})
        except Exception as e: return _err(e)

    @api.route("/curve_distance_to_point/", methods=["POST"])
    def curve_distance_to_point(doc, request):
        """Body: {curve_element_id, point:{x,y,z}} -> distance."""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            return _resp({"status": "success", "distance": cv.Distance(_pt(b["point"]))})
        except Exception as e: return _err(e)

    @api.route("/curve_get_end_point/", methods=["POST"])
    def curve_get_end_point(doc, request):
        """Body: {curve_element_id, end:0|1}"""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            p = cv.GetEndPoint(int(b.get("end", 0)))
            return _resp({"status": "success", "point": {"x": p.X, "y": p.Y, "z": p.Z}})
        except Exception as e: return _err(e)

    @api.route("/curve_get_end_parameter/", methods=["POST"])
    def curve_get_end_parameter(doc, request):
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            return _resp({"status": "success", "parameter": cv.GetEndParameter(int(b.get("end", 0)))})
        except Exception as e: return _err(e)

    @api.route("/curve_get_end_point_reference/", methods=["POST"])
    def curve_get_end_point_reference(doc, request):
        """Returns a stable Reference to a curve endpoint (for dimensioning/constraints)."""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            r = cv.GetEndPointReference(int(b.get("end", 0)))
            return _resp({"status": "success", "reference": r.ConvertToStableRepresentation(doc) if r else None})
        except Exception as e: return _err(e)

    @api.route("/curve_compute_derivatives/", methods=["POST"])
    def curve_compute_derivatives(doc, request):
        """Body: {curve_element_id, parameter, normalized:false} -> origin + 1st/2nd derivative vectors."""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            tr = cv.ComputeDerivatives(float(b["parameter"]), bool(b.get("normalized", False)))
            v = lambda x: {"x": x.X, "y": x.Y, "z": x.Z}
            return _resp({"status": "success", "origin": v(tr.Origin), "first": v(tr.BasisX),
                          "second": v(tr.BasisY)})
        except Exception as e: return _err(e)

    @api.route("/curve_compute_normalized_parameter/", methods=["POST"])
    def curve_compute_normalized_parameter(doc, request):
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            return _resp({"status": "success", "normalized": cv.ComputeNormalizedParameter(float(b["raw_parameter"]))})
        except Exception as e: return _err(e)

    @api.route("/curve_compute_raw_parameter/", methods=["POST"])
    def curve_compute_raw_parameter(doc, request):
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            return _resp({"status": "success", "raw": cv.ComputeRawParameter(float(b["normalized_parameter"]))})
        except Exception as e: return _err(e)

    @api.route("/curve_point_location_on_curve/", methods=["POST"])
    def curve_point_location_on_curve(doc, request):
        """Project a point onto the curve. Body: {curve_element_id, point:{x,y,z}}"""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            ir = cv.Project(_pt(b["point"]))
            if ir is None: return routes.make_response(data={"error": "projection failed"}, status=404)
            p = ir.XYZPoint
            return _resp({"status": "success", "point": {"x": p.X, "y": p.Y, "z": p.Z},
                          "parameter": ir.Parameter, "distance": ir.Distance})
        except Exception as e: return _err(e)

    @api.route("/curve_compute_closest_points/", methods=["POST"])
    def curve_compute_closest_points(doc, request):
        """Closest approach between two curve elements (best-effort via endpoint projection).
        Body: {curve_element_id_a, curve_element_id_b}"""
        try:
            b = _parse(request)
            _, ca = _curve_elem(doc, b["curve_element_id_a"])
            _, cb = _curve_elem(doc, b["curve_element_id_b"])
            if ca is None or cb is None: return routes.make_response(data={"error": "need two curve elements"}, status=400)
            best = None
            for src, dst in ((ca, cb), (cb, ca)):
                for u in (0.0, 0.25, 0.5, 0.75, 1.0):
                    p = src.Evaluate(u, True); ir = dst.Project(p)
                    if ir and (best is None or ir.Distance < best["distance"]):
                        q = ir.XYZPoint
                        best = {"distance": ir.Distance, "point_a": {"x": p.X, "y": p.Y, "z": p.Z},
                                "point_b": {"x": q.X, "y": q.Y, "z": q.Z}}
            return _resp({"status": "success", "closest": best})
        except Exception as e: return _err(e)

    @api.route("/curve_create_reversed/", methods=["POST"])
    def curve_create_reversed(doc, request):
        """Body: {curve_element_id} -> new reversed curve element."""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            t = DB.Transaction(doc, "Reverse Curve"); t.Start()
            ne = _new_like(doc, el, cv.CreateReversed()); t.Commit()
            return _resp({"status": "success", "reversed_curve_id": element_id_value(ne.Id)})
        except Exception as e: return _err(e)

    @api.route("/curve_create_transformed/", methods=["POST"])
    def curve_create_transformed(doc, request):
        """Body: {curve_element_id, translation:{x,y,z}, rotation_deg:0, axis_point:{}, axis_dir:{}}"""
        try:
            b = _parse(request); el, cv = _curve_elem(doc, b["curve_element_id"])
            if cv is None: return routes.make_response(data={"error": "not a curve element"}, status=400)
            xf = DB.Transform.Identity
            if b.get("rotation_deg"):
                ap = _pt(b["axis_point"]) if b.get("axis_point") else DB.XYZ.Zero
                ad = _pt(b["axis_dir"]) if b.get("axis_dir") else DB.XYZ.BasisZ
                xf = DB.Transform.CreateRotationAtPoint(ad, math.radians(float(b["rotation_deg"])), ap)
            if b.get("translation"):
                xf = DB.Transform.CreateTranslation(_pt(b["translation"])).Multiply(xf)
            t = DB.Transaction(doc, "Transform Curve"); t.Start()
            ne = _new_like(doc, el, cv.CreateTransformed(xf)); t.Commit()
            return _resp({"status": "success", "transformed_curve_id": element_id_value(ne.Id)})
        except Exception as e: return _err(e)

    @api.route("/curve_intersect/", methods=["POST"])
    def curve_intersect(doc, request):
        """Intersection of two curve elements. Body: {curve_element_id_a, curve_element_id_b}"""
        try:
            b = _parse(request)
            _, ca = _curve_elem(doc, b["curve_element_id_a"])
            _, cb = _curve_elem(doc, b["curve_element_id_b"])
            if ca is None or cb is None: return routes.make_response(data={"error": "need two curve elements"}, status=400)
            # IronPython returns the .NET out-parameter as part of a tuple
            comp, arr = ca.Intersect(cb)
            pts = []
            if arr is not None:
                try:
                    for ir in arr:
                        p = ir.XYZPoint; pts.append({"x": p.X, "y": p.Y, "z": p.Z})
                except Exception:
                    pass
            return _resp({"status": "success", "result": str(comp), "points": pts})
        except Exception as e: return _err(e)

    logger.info("Geometry routes registered successfully")
