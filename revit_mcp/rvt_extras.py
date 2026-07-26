# -*- coding: UTF-8 -*-
"""
Net-new tools cherry-picked from rvt-mcp (bimwright): QA/lint, organization,
and view utilities. Reimplemented natively against the pyRevit routes API.
"""

from utils import get_element_name, element_id_value
from pyrevit import routes, revit, DB
import json, re, traceback, logging

logger = logging.getLogger(__name__)


def _parse(request):
    if not request or not request.data:
        return {}
    return json.loads(request.data) if isinstance(request.data, str) else request.data


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


def _cat_bic(name):
    key = name if name.startswith("OST_") else "OST_" + name.replace(" ", "")
    return getattr(DB.BuiltInCategory, key, None)


def _err(e):
    return routes.make_response(data={"error": str(e), "traceback": traceback.format_exc()}, status=500)


def register_rvt_extras_routes(api):

    @api.route("/get_model_warnings/", methods=["GET"])
    def get_model_warnings(doc):
        """Summarize all Revit warnings: description + failing element ids, grouped by message."""
        try:
            if not doc:
                return routes.make_response(data={"error": "No active document"}, status=503)
            from collections import Counter
            warns = doc.GetWarnings()
            groups = Counter()
            items = []
            for w in warns:
                desc = w.GetDescriptionText()
                groups[desc] += 1
                ids = [element_id_value(i) for i in w.GetFailingElements()]
                items.append({"description": desc, "severity": str(w.GetSeverity()), "element_ids": ids})
            return routes.make_response(data={"status": "success", "total": len(items),
                                              "by_message": dict(groups.most_common()), "warnings": items[:300]})
        except Exception as e: return _err(e)

    @api.route("/analyze_view_naming/", methods=["POST"])
    def analyze_view_naming(doc, request):
        """Check view names against a regex pattern. Body: {pattern: '^S-..', view_type: 'FloorPlan'(opt)}.
        Returns matching/non-matching views."""
        try:
            b = _parse(request)
            pat = re.compile(b["pattern"]) if b.get("pattern") else None
            vt = b.get("view_type")
            ok, bad = [], []
            for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                if v.IsTemplate:
                    continue
                if vt and str(v.ViewType) != vt:
                    continue
                nm = get_element_name(v)
                rec = {"id": element_id_value(v.Id), "name": nm, "type": str(v.ViewType)}
                if pat is None or pat.search(nm):
                    ok.append(rec)
                else:
                    bad.append(rec)
            return routes.make_response(data={"status": "success", "compliant": len(ok),
                                              "noncompliant": len(bad), "noncompliant_views": bad[:200]})
        except Exception as e: return _err(e)

    @api.route("/find_untagged_elements/", methods=["POST"])
    def find_untagged_elements(doc, request):
        """Find elements of a category in a view that have no tag. Body: {category, view_name}."""
        try:
            b = _parse(request); view = _view(doc, b)
            bic = _cat_bic(b.get("category", ""))
            if bic is None:
                return routes.make_response(data={"error": "valid 'category' required"}, status=400)
            tagged = set()
            for t in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.IndependentTag):
                try:
                    for r in t.GetTaggedLocalElementIds():
                        tagged.add(r.IntegerValue)
                except Exception:
                    try: tagged.add(t.TaggedLocalElementId.IntegerValue)
                    except Exception: pass
            untagged = []
            for e in DB.FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType():
                if element_id_value(e.Id) not in tagged:
                    untagged.append({"id": element_id_value(e.Id), "name": get_element_name(e)})
            return routes.make_response(data={"status": "success", "untagged_count": len(untagged),
                                              "untagged": untagged[:300]})
        except Exception as e: return _err(e)

    @api.route("/cleanup_empty_tags/", methods=["POST"])
    def cleanup_empty_tags(doc, request):
        """Delete tags with empty text in a view (or whole model if all_views=true). Body: {view_name, all_views:false}."""
        try:
            b = _parse(request)
            col = (DB.FilteredElementCollector(doc).OfClass(DB.IndependentTag) if b.get("all_views")
                   else DB.FilteredElementCollector(doc, _view(doc, b).Id).OfClass(DB.IndependentTag))
            victims = []
            for t in col:
                try:
                    txt = t.TagText
                except Exception:
                    txt = None
                if txt is None or not str(txt).strip():
                    victims.append(t.Id)
            t0 = DB.Transaction(doc, "Cleanup Empty Tags"); t0.Start()
            n = 0
            try:
                for vid in victims:
                    doc.Delete(vid); n += 1
                t0.Commit()
            except Exception as tx:
                if t0.HasStarted() and not t0.HasEnded(): t0.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "deleted": n})
        except Exception as e: return _err(e)

    @api.route("/create_group/", methods=["POST"])
    def create_group(doc, request):
        """Create a model group from elements. Body: {element_ids:[...], name:'...'}."""
        try:
            b = _parse(request)
            from System.Collections.Generic import List
            ids = List[DB.ElementId]([DB.ElementId(int(i)) for i in b.get("element_ids", [])])
            if ids.Count == 0:
                return routes.make_response(data={"error": "element_ids required"}, status=400)
            t = DB.Transaction(doc, "Create Group"); t.Start()
            try:
                g = doc.Create.NewGroup(ids)
                if b.get("name"):
                    try: g.GroupType.Name = b["name"]
                    except Exception: pass
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded(): t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "group_id": element_id_value(g.Id),
                                              "name": get_element_name(g.GroupType)})
        except Exception as e: return _err(e)

    @api.route("/purge_unused_families/", methods=["POST"])
    def purge_unused_families(doc, request):
        """Delete loadable families that have zero placed instances. Body: {dry_run:true} to only report."""
        try:
            b = _parse(request); dry = bool(b.get("dry_run", True))
            # count instances per family symbol
            used = set()
            for fi in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance).WhereElementIsNotElementType():
                try: used.add(fi.Symbol.Family.Id.IntegerValue)
                except Exception: pass
            victims = []
            for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
                if fam.Id.IntegerValue not in used and fam.IsEditable:
                    victims.append((fam.Id, get_element_name(fam)))
            names = [v[1] for v in victims]
            if dry:
                return routes.make_response(data={"status": "success", "dry_run": True,
                                                  "unused_count": len(victims), "unused_families": names[:300]})
            t = DB.Transaction(doc, "Purge Unused Families"); t.Start(); n = 0
            try:
                for fid, _ in victims:
                    try: doc.Delete(fid); n += 1
                    except Exception: pass
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded(): t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "deleted": n, "families": names[:300]})
        except Exception as e: return _err(e)

    @api.route("/set_project_info/", methods=["POST"])
    def set_project_info(doc, request):
        """Set Project Information parameters. Body: {params:{'Project Name':'...','Project Number':'...'}}."""
        try:
            b = _parse(request); params = b.get("params") or {}
            pi = doc.ProjectInformation
            t = DB.Transaction(doc, "Set Project Info"); t.Start()
            done, failed = [], []
            try:
                for k, v in params.items():
                    p = pi.LookupParameter(k)
                    if p and not p.IsReadOnly and p.StorageType == DB.StorageType.String:
                        p.Set(str(v)); done.append(k)
                    else:
                        failed.append(k)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded(): t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "set": done, "failed": failed})
        except Exception as e: return _err(e)

    @api.route("/set_view_crop_scale/", methods=["POST"])
    def set_view_crop_scale(doc, request):
        """Set a view's scale and/or crop. Body: {view_name, scale:48, crop_active:true}."""
        try:
            b = _parse(request); view = _view(doc, b)
            t = DB.Transaction(doc, "Set View Crop/Scale"); t.Start()
            try:
                if b.get("scale") is not None:
                    view.Scale = int(b["scale"])
                if b.get("crop_active") is not None:
                    view.CropBoxActive = bool(b["crop_active"])
                    view.CropBoxVisible = bool(b["crop_active"])
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded(): t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "view": get_element_name(view),
                                              "scale": view.Scale, "crop_active": view.CropBoxActive})
        except Exception as e: return _err(e)

    @api.route("/show_element/", methods=["POST"])
    def show_element(doc, request):
        """Select and zoom to elements in the UI. Body: {element_ids:[...]}."""
        try:
            b = _parse(request)
            from System.Collections.Generic import List
            ids = List[DB.ElementId]([DB.ElementId(int(i)) for i in b.get("element_ids", [])])
            if ids.Count == 0:
                return routes.make_response(data={"error": "element_ids required"}, status=400)
            revit.uidoc.Selection.SetElementIds(ids)
            try: revit.uidoc.ShowElements(ids)
            except Exception: pass
            return routes.make_response(data={"status": "success", "shown": ids.Count})
        except Exception as e: return _err(e)

    @api.route("/create_callout/", methods=["POST"])
    def create_callout(doc, request):
        """Create a callout on a parent view. Body: {parent_view_name|parent_view_id,
        p1:{x,y}, p2:{x,y}}  (feet). Uses a Detail callout view family type."""
        try:
            b = _parse(request)
            pv = None
            if b.get("parent_view_id"):
                el = doc.GetElement(DB.ElementId(int(b["parent_view_id"])))
                pv = el if isinstance(el, DB.View) else None
            elif b.get("parent_view_name"):
                for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
                    if not v.IsTemplate and get_element_name(v) == b["parent_view_name"]:
                        pv = v; break
            if pv is None:
                pv = doc.ActiveView
            vft = None
            for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
                if v.ViewFamily == DB.ViewFamily.Detail:
                    vft = v; break
            if vft is None:
                return routes.make_response(data={"error": "no Detail view family type"}, status=404)
            p1 = b.get("p1", {}); p2 = b.get("p2", {})
            a = DB.XYZ(float(p1.get("x", 0)), float(p1.get("y", 0)), 0)
            c = DB.XYZ(float(p2.get("x", 10)), float(p2.get("y", 10)), 0)
            t = DB.Transaction(doc, "Create Callout"); t.Start()
            try:
                cv = DB.ViewSection.CreateCallout(doc, pv.Id, vft.Id, a, c)
                t.Commit()
            except Exception as tx:
                if t.HasStarted() and not t.HasEnded(): t.RollBack()
                raise tx
            return routes.make_response(data={"status": "success", "callout_view_id": element_id_value(cv.Id),
                                              "parent_view": get_element_name(pv)})
        except Exception as e: return _err(e)

    logger.info("rvt-mcp extras routes registered successfully")
