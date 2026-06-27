# -*- coding: UTF-8 -*-
"""
Shared-parameter, reference-plane, and graphic-override routes.
Ported from the Revit-2026-MCP-Server C# command set (MCPCommandHandler.cs)
to native pyRevit routes. Lengths in FEET.

Routes: detect_document_type, add_project_shared_parameter,
        remove_project_shared_parameter, get_project_shared_parameters,
        add_family_shared_parameter, remove_family_parameter, get_family_parameters,
        create_reference_plane, get_reference_planes, set_graphic_overrides
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


def _group_type_id(name):
    """Map a parameter-group name to a DB.GroupTypeId (Revit 2024+)."""
    G = DB.GroupTypeId
    m = {
        "general": G.General, "geometry": G.Geometry, "identity": G.IdentityData,
        "identitydata": G.IdentityData, "construction": G.Construction,
        "materials": G.Materials, "structural": G.Structural, "text": G.Text,
        "graphics": G.Graphics, "constraints": G.Constraints, "data": G.Data,
    }
    key = (name or "general").lower().replace("pg_", "").replace("_", "").replace(" ", "")
    return m.get(key, G.General)


def _find_category(doc, name):
    if not name:
        return None
    for c in doc.Settings.Categories:
        if c.Name.lower() == name.lower():
            return c
    ost = name if name.startswith("OST_") else "OST_" + name.replace(" ", "")
    bic = getattr(DB.BuiltInCategory, ost, None)
    if bic is not None:
        try:
            return DB.Category.GetCategory(doc, bic)
        except Exception:
            return None
    return None


def _open_shared_def(doc, path, param_name):
    """Open shared param file, return (ExternalDefinition, group_name) or (None, err)."""
    try:
        doc.Application.SharedParametersFilename = path
        df = doc.Application.OpenSharedParameterFile()
    except Exception as e:
        return None, "Failed to open shared parameter file: %s" % e
    if df is None:
        return None, "Could not open shared parameter file at '%s'" % path
    for grp in df.Groups:
        for d in grp.Definitions:
            if d.Name.lower() == param_name.lower():
                return d, grp.Name
    return None, "Parameter '%s' not found in shared parameter file" % param_name


def _parse_color(v):
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return DB.Color(int(v[0]), int(v[1]), int(v[2]))
    if isinstance(v, str):
        parts = [int(x) for x in v.replace(" ", "").split(",")]
        if len(parts) >= 3:
            return DB.Color(parts[0], parts[1], parts[2])
    return None


def register_shared_param_routes(api):

    @api.route("/detect_document_type/", methods=["GET"])
    def detect_document_type(doc):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            fam = doc.IsFamilyDocument
            return routes.make_response(data={
                "status": "success", "is_family_document": fam, "is_project_document": not fam,
                "document_type": "Family (.rfa)" if fam else "Project (.rvt)",
                "document_title": doc.Title,
                "can_add_project_shared_parameters": not fam,
                "can_edit_family_parameters": fam,
            })
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/add_project_shared_parameter/", methods=["POST"])
    def add_project_shared_parameter(doc, request):
        """Bind a shared parameter (from the shared-param file) to categories in a project.
        Body: {"shared_parameter_file": "...txt", "parameter_name": "Generic Size",
               "categories": ["Structural Framing","Structural Columns"],
               "parameter_group": "Structural", "is_instance": true}"""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            if doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Project command; use add_family_shared_parameter in families."}, status=400)
            b = _parse(request)
            for req in ("shared_parameter_file", "parameter_name", "categories"):
                if not b.get(req):
                    return routes.make_response(data={"error": "%s is required" % req}, status=400)
            ext_def, info = _open_shared_def(doc, b["shared_parameter_file"], b["parameter_name"])
            if ext_def is None:
                return routes.make_response(data={"error": info}, status=404)
            gid = _group_type_id(b.get("parameter_group", "General"))
            is_inst = bool(b.get("is_instance", True))
            catset = doc.Application.Create.NewCategorySet()
            added, failed = [], []
            for cn in b["categories"]:
                cat = _find_category(doc, cn)
                if cat is not None and cat.AllowsBoundParameters:
                    catset.Insert(cat); added.append(cat.Name)
                else:
                    failed.append(cn)
            if catset.Size == 0:
                return routes.make_response(data={"error": "No valid bindable categories", "failed": failed}, status=400)
            binding = (doc.Application.Create.NewInstanceBinding(catset) if is_inst
                       else doc.Application.Create.NewTypeBinding(catset))
            t = DB.Transaction(doc, "Add Project Shared Parameter"); t.Start()
            try:
                bm = doc.ParameterBindings
                existing = None
                it = bm.ForwardIterator()
                while it.MoveNext():
                    if it.Key.Name.lower() == b["parameter_name"].lower():
                        existing = it.Key; break
                ok = bm.ReInsert(existing, binding, gid) if existing else bm.Insert(ext_def, binding, gid)
                if not ok:
                    t.RollBack()
                    return routes.make_response(data={"error": "Failed to bind parameter"}, status=500)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "parameter_name": b["parameter_name"],
                                              "is_instance": is_inst, "shared_param_group": info,
                                              "bound_categories": added, "failed_categories": failed,
                                              "was_update": existing is not None})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/remove_project_shared_parameter/", methods=["POST"])
    def remove_project_shared_parameter(doc, request):
        """Body: {"parameter_name": "Generic Size"}"""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            if doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Project command."}, status=400)
            name = _parse(request).get("parameter_name")
            if not name:
                return routes.make_response(data={"error": "parameter_name is required"}, status=400)
            bm = doc.ParameterBindings
            target, binding = None, None
            it = bm.ForwardIterator()
            while it.MoveNext():
                if it.Key.Name.lower() == name.lower():
                    target = it.Key; binding = it.Current; break
            if target is None:
                return routes.make_response(data={"error": "Parameter '%s' not bound" % name}, status=404)
            cats = [c.Name for c in binding.Categories] if binding else []
            t = DB.Transaction(doc, "Remove Project Shared Parameter"); t.Start()
            try:
                ok = bm.Remove(target)
                if not ok:
                    t.RollBack(); return routes.make_response(data={"error": "Failed to remove"}, status=500)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "removed_parameter": name, "was_bound_to": cats})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_project_shared_parameters/", methods=["GET"])
    def get_project_shared_parameters(doc):
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            if doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Project command."}, status=400)
            out = []
            it = doc.ParameterBindings.ForwardIterator()
            while it.MoveNext():
                d = it.Key; binding = it.Current
                cats = [c.Name for c in binding.Categories] if binding else []
                rec = {"name": d.Name, "is_instance": isinstance(binding, DB.InstanceBinding),
                       "bound_categories": cats, "category_count": len(cats),
                       "is_shared": isinstance(d, DB.ExternalDefinition)}
                if isinstance(d, DB.ExternalDefinition):
                    try:
                        rec["guid"] = str(d.GUID)
                    except Exception:
                        pass
                out.append(rec)
            return routes.make_response(data={"status": "success", "parameter_count": len(out), "parameters": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/add_family_shared_parameter/", methods=["POST"])
    def add_family_shared_parameter(doc, request):
        """In a family doc: add a shared parameter. Body like add_project_shared_parameter (no categories)."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            if not doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Family Editor only."}, status=400)
            b = _parse(request)
            for req in ("shared_parameter_file", "parameter_name"):
                if not b.get(req):
                    return routes.make_response(data={"error": "%s is required" % req}, status=400)
            fm = doc.FamilyManager
            ext_def, info = _open_shared_def(doc, b["shared_parameter_file"], b["parameter_name"])
            if ext_def is None:
                return routes.make_response(data={"error": info}, status=404)
            for fp in fm.Parameters:
                if fp.Definition.Name.lower() == b["parameter_name"].lower():
                    return routes.make_response(data={"error": "Parameter already exists in family"}, status=400)
            gid = _group_type_id(b.get("parameter_group", "General"))
            is_inst = bool(b.get("is_instance", True))
            t = DB.Transaction(doc, "Add Family Shared Parameter"); t.Start()
            try:
                np = fm.AddParameter(ext_def, gid, is_inst)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "parameter_name": np.Definition.Name,
                                              "is_instance": np.IsInstance, "is_shared": np.IsShared,
                                              "shared_param_group": info})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/remove_family_parameter/", methods=["POST"])
    def remove_family_parameter(doc, request):
        """In a family doc: Body {"parameter_name": "..."}"""
        try:
            if not doc or not doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Family Editor only."}, status=400)
            name = _parse(request).get("parameter_name")
            if not name:
                return routes.make_response(data={"error": "parameter_name is required"}, status=400)
            fm = doc.FamilyManager
            target = None
            for fp in fm.Parameters:
                if fp.Definition.Name.lower() == name.lower():
                    target = fp; break
            if target is None:
                return routes.make_response(data={"error": "Parameter not found"}, status=404)
            t = DB.Transaction(doc, "Remove Family Parameter"); t.Start()
            try:
                fm.RemoveParameter(target); t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "removed_parameter": name})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_family_parameters/", methods=["GET"])
    def get_family_parameters(doc):
        try:
            if not doc or not doc.IsFamilyDocument:
                return routes.make_response(data={"error": "Family Editor only."}, status=400)
            fm = doc.FamilyManager
            out = []
            for fp in fm.Parameters:
                out.append({"name": fp.Definition.Name, "is_instance": fp.IsInstance,
                            "is_shared": fp.IsShared, "storage_type": str(fp.StorageType),
                            "is_read_only": fp.IsReadOnly})
            return routes.make_response(data={"status": "success", "parameter_count": len(out), "parameters": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/create_reference_plane/", methods=["POST"])
    def create_reference_plane(doc, request):
        """Body: {"bubble":{"x","y","z"}, "free":{"x","y","z"}, "cut_vector":{...}(opt),
                  "name":"...", "view_name":"...", "view_id":int}  (feet)"""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            if not b.get("bubble") or not b.get("free"):
                return routes.make_response(data={"error": "bubble and free points required"}, status=400)
            P = lambda d: DB.XYZ(float(d.get("x", 0)), float(d.get("y", 0)), float(d.get("z", 0)))
            bubble, free = P(b["bubble"]), P(b["free"])
            if b.get("cut_vector"):
                cut = P(b["cut_vector"]).Normalize()
            else:
                direction = (free - bubble).Normalize()
                cut = direction.CrossProduct(DB.XYZ.BasisZ)
                cut = DB.XYZ.BasisY if cut.IsZeroLength() else cut.Normalize()
            view = None
            if b.get("view_id"):
                el = doc.GetElement(DB.ElementId(int(b["view_id"])))
                view = el if isinstance(el, DB.View) else None
            elif b.get("view_name"):
                for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                    if not v.IsTemplate and get_element_name(v) == b["view_name"]:
                        view = v; break
            if view is None:
                view = doc.ActiveView
            t = DB.Transaction(doc, "Create Reference Plane"); t.Start()
            try:
                rp = doc.Create.NewReferencePlane(bubble, free, cut, view)
                if b.get("name"):
                    try: rp.Name = b["name"]
                    except Exception: pass
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "reference_plane_id": element_id_value(rp.Id),
                                              "name": rp.Name, "view": get_element_name(view)})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/get_reference_planes/", methods=["POST"])
    def get_reference_planes(doc, request):
        """Body: {"name":"filter"(opt), "include_unnamed":true}"""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            nf = (b.get("name") or "").lower()
            inc_unnamed = bool(b.get("include_unnamed", True))
            out = []
            for rp in DB.FilteredElementCollector(doc).OfClass(DB.ReferencePlane):
                nm = rp.Name or ""
                if nf and nf not in nm.lower():
                    continue
                if not inc_unnamed and not nm.strip():
                    continue
                rec = {"id": element_id_value(rp.Id), "name": nm, "is_named": bool(nm.strip())}
                try:
                    be, fe = rp.BubbleEnd, rp.FreeEnd
                    rec["bubble_end"] = {"x": round(be.X, 3), "y": round(be.Y, 3), "z": round(be.Z, 3)}
                    rec["free_end"] = {"x": round(fe.X, 3), "y": round(fe.Y, 3), "z": round(fe.Z, 3)}
                except Exception:
                    pass
                out.append(rec)
            return routes.make_response(data={"status": "success", "count": len(out), "reference_planes": out})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    @api.route("/set_graphic_overrides/", methods=["POST"])
    def set_graphic_overrides(doc, request):
        """Apply view graphic overrides by category or element(s).
        Body: {"category":"Structural Framing" | "element_ids":[..],
               "view_name":"..."(opt), "halftone":bool, "transparency":0-100,
               "projection_line_color":[r,g,b], "projection_line_weight":int,
               "cut_line_color":[r,g,b], "surface_foreground_color":[r,g,b],
               "detail_level":"coarse|medium|fine", "reset":bool}"""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            b = _parse(request)
            view = None
            if b.get("view_id"):
                el = doc.GetElement(DB.ElementId(int(b["view_id"])))
                view = el if isinstance(el, DB.View) else None
            elif b.get("view_name"):
                for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                    if not v.IsTemplate and get_element_name(v) == b["view_name"]:
                        view = v; break
            if view is None:
                view = doc.ActiveView
            ogs = DB.OverrideGraphicSettings()
            if not b.get("reset"):
                if b.get("halftone") is not None: ogs.SetHalftone(bool(b["halftone"]))
                if b.get("transparency") is not None:
                    ogs.SetSurfaceTransparency(max(0, min(100, int(b["transparency"]))))
                if b.get("projection_line_color"):
                    c = _parse_color(b["projection_line_color"]);  c and ogs.SetProjectionLineColor(c)
                if b.get("projection_line_weight") is not None:
                    ogs.SetProjectionLineWeight(int(b["projection_line_weight"]))
                if b.get("cut_line_color"):
                    c = _parse_color(b["cut_line_color"]); c and ogs.SetCutLineColor(c)
                if b.get("surface_foreground_color"):
                    c = _parse_color(b["surface_foreground_color"]); c and ogs.SetSurfaceForegroundPatternColor(c)
                if b.get("detail_level"):
                    dl = {"coarse": DB.ViewDetailLevel.Coarse, "medium": DB.ViewDetailLevel.Medium,
                          "fine": DB.ViewDetailLevel.Fine}.get(str(b["detail_level"]).lower())
                    if dl is not None: ogs.SetDetailLevel(dl)
            t = DB.Transaction(doc, "Set Graphic Overrides"); t.Start()
            n = 0
            try:
                if b.get("category"):
                    cat = _find_category(doc, b["category"])
                    if cat is None:
                        t.RollBack(); return routes.make_response(data={"error": "category not found"}, status=404)
                    view.SetCategoryOverrides(cat.Id, ogs); n = 1
                else:
                    ids = b.get("element_ids") or ([b["element_id"]] if b.get("element_id") else [])
                    for i in ids:
                        view.SetElementOverrides(DB.ElementId(int(i)), ogs); n += 1
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "view": get_element_name(view),
                                              "applied_to": n, "reset": bool(b.get("reset"))})
        except Exception as e:
            return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)

    logger.info("Shared-parameter routes registered successfully")
