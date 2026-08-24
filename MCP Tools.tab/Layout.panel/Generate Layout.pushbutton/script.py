# -*- coding: utf-8 -*-
__title__ = "Generate\nLayout"
__author__ = "Revit MCP"
__doc__ = """Generate candidate parametric floor-plan layouts for a boundary.

Pick a Room or Floor as the boundary, optionally pick existing elements
that must stay (stair/elevator cores, fixed walls), describe the program
(rooms/occupancy needed, with categories for color-coding), and this
generates a few candidate layouts as color-coded filled regions + text
labels in new Drafting Views - a reference to build real walls/doors/
windows from, not finished construction documents.

Boundary is treated as the selected element's bounding rectangle
(irregular boundaries get a warning, not full support). Explicitly
selected obstacles are actually excluded from the room subdivision, not
just flagged. Nearby existing walls/doors/stairs are also reported and
used to bias the corridor toward a door.

This is a design aid, not a code compliance tool. Corridor width and exit
travel distance checks are rule-of-thumb heuristics only - always verify
against the applicable code and consult a code reviewer / AHJ.
"""

import os
import json
import random

import clr
clr.AddReference("System.Data")
clr.AddReference("PresentationCore")
from System.Data import DataTable
from System.Windows.Media import Brushes
from pyrevit import DB, revit, forms
from Autodesk.Revit.UI.Selection import ObjectType

# layout_algorithm.py now lives in revit_mcp/ (not this pushbutton's own
# folder) so the /generate_floor_plan/ MCP route can share the same
# space-planning solver instead of duplicating it. pyRevit only
# auto-adds a pushbutton's own folder to sys.path, so revit_mcp/ has to
# be added explicitly here.
import sys
_extension_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_revit_mcp_dir = os.path.join(_extension_root, "revit_mcp")
if _revit_mcp_dir not in sys.path:
    sys.path.append(_revit_mcp_dir)

# pyRevit re-reads this script fresh on every click, but a plain `import`
# resolves to whatever's already in sys.modules - so an edited copy of
# layout_algorithm.py can silently keep running a stale cached version
# for the rest of the Revit session unless the cache is busted here
# first. Force a fresh reload every time so edits always take effect
# without requiring a full Revit restart.
if "layout_algorithm" in sys.modules:
    del sys.modules["layout_algorithm"]
from layout_algorithm import (
    build_layout, check_egress_heuristics, assign_colors, generate_scored_candidates,
    _nearest_edge_for_point, parse_size_range, format_size_range, compute_boundary_exclusion_rects,
)

doc = revit.doc
uidoc = revit.uidoc

ROOMS_CATEGORY_ID = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Rooms).Id
FLOORS_CATEGORY_ID = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Floors).Id
STAIRS_CATEGORY_ID = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Stairs).Id
# Revit has no dedicated "Elevators" category (OST_Elevators does not exist in the API -
# confirmed live against the open document before relying on it). Elevator families are
# conventionally modeled under Specialty Equipment (note the API's "Speciality" spelling),
# so that's used as a heuristic proxy - it will also catch other specialty-equipment
# obstacles the user picks, not elevators exclusively, which is an acceptable tradeoff for
# a design aid (worth a wider access buffer around a fire extinguisher cabinet too).
ELEVATORS_CATEGORY_ID = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_SpecialityEquipment).Id


# ---------------------------------------------------------------------------
# Saved program persistence (remember the last-entered program between runs)
# ---------------------------------------------------------------------------

LAST_PROGRAM_PATH = os.path.join(os.path.dirname(__file__), "last_program.json")


def load_last_program():
    """Returns the last-saved program dict, or None if there isn't one yet
    or it can't be read (corrupt/missing file, older schema, etc.) - a
    read failure here should never block the tool from opening, it just
    means the form falls back to its hardcoded sample rows."""
    if not os.path.exists(LAST_PROGRAM_PATH):
        return None
    try:
        with open(LAST_PROGRAM_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_last_program(data):
    """Best-effort save - a write failure (e.g. read-only folder) should
    never block the user's actual Generate action, so this never raises."""
    try:
        with open(LAST_PROGRAM_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Small Element.Name helpers (Revit 2025+ needs the descriptor fallback here)
# ---------------------------------------------------------------------------

def get_name(element):
    try:
        return element.Name
    except AttributeError:
        return DB.Element.Name.__get__(element)


def set_name(element, name):
    try:
        element.Name = name
    except AttributeError:
        DB.Element.Name.__set__(element, name)


def read_adjacency_cell(row, column_name):
    """Pulls the raw Adjacency text out of a DataGrid row, tolerantly (a
    blank/missing cell just means no preference). Final normalization to
    "window"/"entry"/"core"/"" happens downstream in expand_program/
    expand_enclosed_program - this just extracts the text safely."""
    if row.IsNull(column_name):
        return ""
    return str(row[column_name]).strip()


def unique_view_name(base_name):
    existing = set()
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            existing.add(get_name(v))
        except Exception:
            pass
    if base_name not in existing:
        return base_name
    i = 2
    while "{} ({})".format(base_name, i) in existing:
        i += 1
    return "{} ({})".format(base_name, i)


# ---------------------------------------------------------------------------
# Boundary selection (Room or Floor)
# ---------------------------------------------------------------------------

def is_boundary_element(element):
    return (
        element is not None
        and element.Category is not None
        and element.Category.Id in (ROOMS_CATEGORY_ID, FLOORS_CATEGORY_ID)
    )


def get_boundary_element():
    sel_ids = list(uidoc.Selection.GetElementIds())
    if len(sel_ids) == 1:
        el = doc.GetElement(sel_ids[0])
        if is_boundary_element(el):
            return el
    try:
        ref = uidoc.Selection.PickObject(ObjectType.Element, "Select a Room or Floor to lay out")
    except Exception:
        return None
    el = doc.GetElement(ref.ElementId)
    return el if is_boundary_element(el) else None


def get_element_area(element):
    """Room exposes .Area as a native property; Floor exposes it via the
    'Area' parameter. Returns 0.0 if neither is available."""
    area = getattr(element, "Area", None)
    if area is not None:
        return area
    area_param = element.LookupParameter("Area")
    return area_param.AsDouble() if area_param else 0.0


def get_obstacles(exclude_id):
    """Prompts for zero or more existing elements that must stay (stair/
    elevator cores, fixed walls). Escape/Finish with none selected = no
    obstacles. Returns a list of Elements (excluding the boundary element
    itself, in case it gets re-picked by accident)."""
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            "Select existing elements that must stay (stair/elevator cores, fixed walls). "
            "Click Finish with none selected to skip.",
        )
    except Exception:
        return []
    obstacles = []
    for ref in refs:
        if ref.ElementId == exclude_id:
            continue
        el = doc.GetElement(ref.ElementId)
        if el is not None:
            obstacles.append(el)
    return obstacles


def get_window_elements(exclude_id):
    """Prompts for zero or more curtain wall/window elements to mark which
    boundary edge(s) have windows - a soft placement/scoring preference for
    program rows with Adjacency="Window"/"Core", never a hard requirement.
    Escape/Finish with none selected = no window preference used. Mirrors
    get_obstacles's selection UX exactly."""
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            "Select curtain wall/window elements to mark boundary edges with window preference. "
            "Click Finish with none selected to skip.",
        )
    except Exception:
        return []
    elements = []
    for ref in refs:
        if ref.ElementId == exclude_id:
            continue
        el = doc.GetElement(ref.ElementId)
        if el is not None:
            elements.append(el)
    return elements


def get_boundary_polygon(boundary_element, origin_x, origin_y):
    """Extracts the REAL boundary shape (not the bounding rectangle) as a
    list of closed curve loops, in the same local coordinate system as
    obstacles_local (subtracting origin_x/origin_y). Room -> its real
    GetBoundarySegments loops. Floor -> its sketch profile (Floor has no
    GetBoundarySegments-equivalent API).

    Returns (loops, is_orthogonal). is_orthogonal is False (and loops is
    []) the instant ANY of: a curve isn't a straight DB.Line (an Arc/spline
    must never fall through to an endpoint check - its endpoints alone
    can't reveal it bulges away from a straight line); a Line's endpoints
    aren't purely horizontal or vertical within tolerance; consecutive
    curves (including the closing curve back to the loop's start) don't
    chain endpoint-to-endpoint within tolerance; or extraction raises for
    any reason (missing sketch, unplaced element, etc. - matches this
    file's load_last_program/get_element_area "never let an auxiliary read
    blow up the tool" pattern). Callers MUST gate on is_orthogonal before
    using loops - same never-guess contract the existing rect_ratio warning
    already established; a non-orthogonal or unextractable boundary just
    falls back to today's bounding-box-only behavior, no exception raised.

    tol=1e-3 ft (~1/64"), not 1e-6 - live-verified against a real Room in
    the field (USABLE SQUARE FOOTAGE 226, a genuinely rectilinear boundary):
    GetBoundarySegments returned one joint with a ~4.4e-5 ft gap between
    consecutive segment endpoints - ordinary Revit boundary-computation
    noise, not a real discontinuity. A 1e-6 tolerance rejected this
    perfectly good rectilinear room as "non-orthogonal"; 1e-3 accepts it
    (confirmed: the resulting polygon's shoelace area matched the real
    Room.Area property within 0.003 sf) while still being far tighter than
    any real diagonal wall's dx/dy (which spans feet, not thousandths of a
    foot), so a genuinely angled boundary still correctly fails this check."""
    tol = 1e-3
    try:
        loops_raw = []
        if boundary_element.Category.Id == ROOMS_CATEGORY_ID:
            options = DB.SpatialElementBoundaryOptions()
            for loop_segments in boundary_element.GetBoundarySegments(options):
                loops_raw.append([seg.GetCurve() for seg in loop_segments])
        elif boundary_element.Category.Id == FLOORS_CATEGORY_ID:
            sketch = doc.GetElement(boundary_element.SketchId)
            if sketch is None:
                return [], False
            for curve_array in sketch.Profile:
                loops_raw.append([c for c in curve_array])
        else:
            return [], False

        loops = []
        for curves in loops_raw:
            if len(curves) < 3:
                return [], False
            pts = []
            first_start = None
            prev_end = None
            for c in curves:
                if not isinstance(c, DB.Line):
                    return [], False
                p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
                if abs(p1.X - p0.X) > tol and abs(p1.Y - p0.Y) > tol:
                    return [], False  # not axis-aligned
                if prev_end is not None and (abs(prev_end.X - p0.X) > tol or abs(prev_end.Y - p0.Y) > tol):
                    return [], False  # doesn't chain to the previous curve
                if first_start is None:
                    first_start = p0
                pts.append((p0.X - origin_x, p0.Y - origin_y))
                prev_end = p1
            if abs(prev_end.X - first_start.X) > tol or abs(prev_end.Y - first_start.Y) > tol:
                return [], False  # loop doesn't close
            loops.append(pts)
        return loops, True
    except Exception:
        return [], False


# ---------------------------------------------------------------------------
# Existing-conditions scan
# ---------------------------------------------------------------------------

def bbox_overlap_2d(a, b):
    return not (
        a.Max.X < b.Min.X or b.Max.X < a.Min.X
        or a.Max.Y < b.Min.Y or b.Max.Y < a.Min.Y
    )


def expand_bbox(bbox, margin):
    expanded = DB.BoundingBoxXYZ()
    expanded.Min = DB.XYZ(bbox.Min.X - margin, bbox.Min.Y - margin, bbox.Min.Z - margin)
    expanded.Max = DB.XYZ(bbox.Max.X + margin, bbox.Max.Y + margin, bbox.Max.Z + margin)
    return expanded


def scan_existing_conditions(expanded_bbox):
    cats = {
        "wall": DB.BuiltInCategory.OST_Walls,
        "door": DB.BuiltInCategory.OST_Doors,
        "stair": DB.BuiltInCategory.OST_Stairs,
    }
    found = {"wall": [], "door": [], "stair": []}
    for key, cat in cats.items():
        elems = DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType().ToElements()
        for el in elems:
            bb = el.get_BoundingBox(None)
            if bb is None:
                continue
            if bbox_overlap_2d(bb, expanded_bbox):
                found[key].append(el)
    return found


def door_points_world(doors):
    pts = []
    for d in doors:
        loc = d.Location
        pt = getattr(loc, "Point", None)
        if pt is not None:
            pts.append((pt.X, pt.Y))
    return pts


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def rect_corners(x, y, w, h):
    return [DB.XYZ(x, y, 0), DB.XYZ(x + w, y, 0), DB.XYZ(x + w, y + h, 0), DB.XYZ(x, y + h, 0)]


def rect_lines(x, y, w, h):
    pts = rect_corners(x, y, w, h)
    return [DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]) for i in range(4)]


def rect_curveloop(x, y, w, h):
    loop = DB.CurveLoop()
    for ln in rect_lines(x, y, w, h):
        loop.Append(ln)
    return loop


def create_drafting_view(name):
    vft = next(
        v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType)
        if v.ViewFamily == DB.ViewFamily.Drafting
    )
    view = DB.ViewDrafting.Create(doc, vft.Id)
    set_name(view, unique_view_name(name))
    return view


def get_solid_fill_pattern_id():
    fill_patterns = DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement).ToElements()
    solid = next(fp for fp in fill_patterns if fp.GetFillPattern().IsSolidFill)
    return solid.Id


def get_or_create_category_type(category, color, base_type, solid_pattern_id, cache):
    """Find-or-create (and cache) a solid-color FilledRegionType for a
    category, so repeated categories across rooms/options reuse the same
    type instead of duplicating on every call."""
    if category in cache:
        return cache[category]
    existing = next(
        (t for t in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType).ToElements()
         if get_name(t) == "Layout - {}".format(category)),
        None,
    )
    if existing:
        cache[category] = existing.Id
        return existing.Id
    new_type = base_type.Duplicate("Layout - {}".format(category))
    new_type.ForegroundPatternId = solid_pattern_id
    new_type.ForegroundPatternColor = DB.Color(color[0], color[1], color[2])
    cache[category] = new_type.Id
    return new_type.Id


def draw_layout(view, layout, category_type_ids, default_type_id, text_type_id, origin_xy=None):
    # Small "+" marker at the layout's local (0,0) plus a label reporting the
    # matching real Revit model coordinate - lets you align this drafting
    # view against the real floor plan by hand (e.g. Move/Align using this
    # point as the reference), since drafting views have no built-in
    # relationship to model/project coordinates.
    if origin_xy is not None:
        ox, oy = origin_xy
        mark_len = 1.0
        doc.Create.NewDetailCurve(view, DB.Line.CreateBound(DB.XYZ(-mark_len, 0, 0), DB.XYZ(mark_len, 0, 0)))
        doc.Create.NewDetailCurve(view, DB.Line.CreateBound(DB.XYZ(0, -mark_len, 0), DB.XYZ(0, mark_len, 0)))
        DB.TextNote.Create(
            doc, view.Id, DB.XYZ(mark_len + 0.5, -0.5, 0),
            "Origin (0,0) = Revit model coord ({:.2f}, {:.2f})".format(ox, oy),
            text_type_id,
        )

    for room in layout["rooms"]:
        loop = rect_curveloop(room["x"], room["y"], room["w"], room["h"])
        type_id = category_type_ids.get(room["category"], default_type_id)
        DB.FilledRegion.Create(doc, type_id, view.Id, [loop])
        label = "{}\n{:.0f} sf".format(room["name"], room["area"])
        label_pt = DB.XYZ(room["x"] + 0.5, room["y"] + room["h"] - 0.5, 0)
        DB.TextNote.Create(doc, view.Id, label_pt, label, text_type_id)

    for p in layout["corridor_pieces"]:
        for ln in rect_lines(p["x"], p["y"], p["w"], p["h"]):
            doc.Create.NewDetailCurve(view, ln)
    c = layout["corridor"]
    corridor_label_pt = DB.XYZ(c["x"] + 0.5, c["y"] + c["h"] / 2.0, 0)
    DB.TextNote.Create(doc, view.Id, corridor_label_pt, "Corridor", text_type_id)

    for i, b in enumerate(layout.get("branches", []), start=1):
        label_pt = DB.XYZ(b["x"] + 0.5, b["y"] + b["h"] / 2.0, 0)
        DB.TextNote.Create(doc, view.Id, label_pt, "Hall {}".format(i), text_type_id)

    for obs in layout["obstacles"]:
        for ln in rect_lines(obs["x"], obs["y"], obs["w"], obs["h"]):
            doc.Create.NewDetailCurve(view, ln)
        label_pt = DB.XYZ(obs["x"] + 0.5, obs["y"] + obs["h"] - 0.5, 0)
        DB.TextNote.Create(doc, view.Id, label_pt, "Existing - stays", text_type_id)

    for excl in layout.get("boundary_exclusions", []):
        for ln in rect_lines(excl["x"], excl["y"], excl["w"], excl["h"]):
            doc.Create.NewDetailCurve(view, ln)
        label_pt = DB.XYZ(excl["x"] + 0.5, excl["y"] + excl["h"] - 0.5, 0)
        DB.TextNote.Create(doc, view.Id, label_pt, "Outside room footprint", text_type_id)

    # Leftover/unallocated space is intentionally NOT drawn - it's still
    # counted in the results dialog, but outlining every fragment (there
    # can be a dozen or more) was pure visual clutter with little value.


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

class ProgramForm(forms.WPFWindow):
    def __init__(self, xaml_file, boundary_element, boundary_w, boundary_h, rect_ratio, found, obstacle_count,
                 vertical_circulation_count=0, window_edges=None, is_orthogonal=False, boundary_exclusion_count=0):
        forms.WPFWindow.__init__(self, xaml_file)
        self.boundary_element = boundary_element
        self.boundary_w = boundary_w
        self.boundary_h = boundary_h
        self.result = None

        self.boundaryInfoText = self.FindName("boundaryInfoText")
        self.enclosedGrid = self.FindName("enclosedGrid")
        self.openGrid = self.FindName("openGrid")
        self.corridorWidthBox = self.FindName("corridorWidthBox")
        self.optionCountBox = self.FindName("optionCountBox")
        self.fixedModeRadio = self.FindName("fixedModeRadio")
        self.randomModeRadio = self.FindName("randomModeRadio")
        self.programSummaryText = self.FindName("programSummaryText")

        boundary_kind = "Room" if boundary_element.Category.Id == ROOMS_CATEGORY_ID else "Floor"
        info_lines = [
            "Boundary: {} {} (bbox {:.1f}' x {:.1f}' = {:.0f} sf)".format(
                boundary_kind, get_name(boundary_element) or "", boundary_w, boundary_h, boundary_w * boundary_h),
            "Nearby existing: {} walls, {} doors, {} stairs".format(
                len(found["wall"]), len(found["door"]), len(found["stair"])),
            "Fixed obstacles selected (excluded from layout): {}".format(obstacle_count),
            "Vertical-circulation obstacles detected (stairs/elevators): {}".format(vertical_circulation_count),
            "Window edges marked: {}".format(", ".join(sorted(window_edges)) if window_edges else "none"),
        ]
        if rect_ratio < 0.85:
            if is_orthogonal:
                info_lines.append(
                    "Boundary is irregular ({:.0f}% rectangular) - exact shape auto-detected: "
                    "{} area(s) outside the real footprint will be automatically excluded "
                    "from every generated option.".format(rect_ratio * 100, boundary_exclusion_count)
                )
            else:
                info_lines.append(
                    "Warning: boundary is only {:.0f}% rectangular (area vs. bounding box) - "
                    "irregular boundaries aren't fully supported yet, using the bounding rectangle.".format(
                        rect_ratio * 100)
                )
        self.boundaryInfoText.Text = "\n".join(info_lines)

        saved = load_last_program()

        enclosed_dt = DataTable()
        enclosed_dt.Columns.Add("Room Name", str)
        enclosed_dt.Columns.Add("Category", str)
        # Width/Depth are text columns, not Double - typing a range like "10-14"
        # into a Double-typed DataGrid column fails WPF's commit-time type
        # coercion and the edit is silently rejected before it ever reaches
        # read_enclosed_program(). str lets a cell hold either an exact number
        # ("10") or a range ("10-14"), parsed tolerantly by parse_size_range.
        enclosed_dt.Columns.Add("Width (ft)", str)
        enclosed_dt.Columns.Add("Depth (ft)", str)
        enclosed_dt.Columns.Add("Qty", int)
        enclosed_dt.Columns.Add("Adjacency (Window/Core)", str)
        if saved and saved.get("enclosed_program"):
            for row in saved["enclosed_program"]:
                w_min = float(row.get("width", 0.0))
                w_max = float(row.get("max_width", w_min) or w_min)
                d_min = float(row.get("depth", 0.0))
                d_max = float(row.get("max_depth", d_min) or d_min)
                enclosed_dt.Rows.Add(
                    row.get("name", ""), row.get("category", ""),
                    format_size_range(w_min, w_max), format_size_range(d_min, d_max),
                    int(row.get("qty", 1)), row.get("adjacency", ""),
                )
        else:
            enclosed_dt.Rows.Add("Private Office", "Private Office", "10", "12", 1, "")
            enclosed_dt.Rows.Add("Conference Room", "Meeting", "14", "18", 1, "")
        self.enclosedGrid.ItemsSource = enclosed_dt.DefaultView

        open_dt = DataTable()
        open_dt.Columns.Add("Zone Name", str)
        open_dt.Columns.Add("Category", str)
        open_dt.Columns.Add("Target Area (sf)", float)
        open_dt.Columns.Add("Min Width (ft)", float)
        open_dt.Columns.Add("Qty", int)
        open_dt.Columns.Add("Adjacency (Window/Entry/Core)", str)
        if saved and saved.get("open_program"):
            for row in saved["open_program"]:
                open_dt.Rows.Add(
                    row.get("name", ""), row.get("category", ""),
                    float(row.get("target_area", 0.0)), float(row.get("min_width", 6.0)),
                    int(row.get("qty", 1)), row.get("adjacency", ""),
                )
        else:
            open_dt.Rows.Add("Open Workspace", "Open Office", 400.0, 15.0, 1, "")
            # Reception's "Entry" default is just a convenient seed value for this
            # sample row - not a name-matching rule anywhere in code. Any row in
            # either grid gets the same entry-proximity preference by typing
            # "Entry" here, regardless of its name/category.
            open_dt.Rows.Add("Reception", "Reception", 150.0, 8.0, 1, "Entry")
        self.openGrid.ItemsSource = open_dt.DefaultView

        if saved:
            if saved.get("corridor_width") is not None:
                self.corridorWidthBox.Text = str(saved["corridor_width"])
            if saved.get("option_count") is not None:
                self.optionCountBox.Text = str(saved["option_count"])
            if saved.get("mode") == "randomized":
                self.randomModeRadio.IsChecked = True
                self.fixedModeRadio.IsChecked = False

    def read_enclosed_program(self):
        dt = self.enclosedGrid.ItemsSource.Table
        program = []
        for row in dt.Rows:
            if row.IsNull("Room Name"):
                continue
            name = str(row["Room Name"]).strip()
            if not name or row.IsNull("Width (ft)") or row.IsNull("Depth (ft)"):
                continue
            # A single number ("10") is an exact size, same as before. A range
            # ("10-14") means each instance of this row gets its own randomized
            # size within that range - see build_layout's size_rng.
            width_range = parse_size_range(row["Width (ft)"])
            depth_range = parse_size_range(row["Depth (ft)"])
            if width_range is None or depth_range is None:
                continue
            width, max_width = width_range
            depth, max_depth = depth_range
            category = name
            if not row.IsNull("Category"):
                cat_val = str(row["Category"]).strip()
                if cat_val:
                    category = cat_val
            qty = 1
            if not row.IsNull("Qty"):
                try:
                    qty = max(int(row["Qty"]), 1)
                except Exception:
                    qty = 1
            adjacency = read_adjacency_cell(row, "Adjacency (Window/Core)")
            program.append({
                "name": name, "category": category,
                "width": width, "max_width": max_width,
                "depth": depth, "max_depth": max_depth,
                "qty": qty, "adjacency": adjacency,
            })
        return program

    def read_open_program(self):
        dt = self.openGrid.ItemsSource.Table
        program = []
        for row in dt.Rows:
            if row.IsNull("Zone Name"):
                continue
            name = str(row["Zone Name"]).strip()
            if not name or row.IsNull("Target Area (sf)"):
                continue
            try:
                area = float(row["Target Area (sf)"])
            except Exception:
                continue
            if area <= 0:
                continue
            category = name
            if not row.IsNull("Category"):
                cat_val = str(row["Category"]).strip()
                if cat_val:
                    category = cat_val
            # Default keeps an open zone from being squeezed into an
            # unusably thin sliver if the space it lands in is narrow -
            # open zones don't need a *specific* shape, but they still
            # need to be a usable one.
            min_w = 6.0
            if not row.IsNull("Min Width (ft)"):
                try:
                    min_w = float(row["Min Width (ft)"])
                except Exception:
                    min_w = 6.0
            qty = 1
            if not row.IsNull("Qty"):
                try:
                    qty = max(int(row["Qty"]), 1)
                except Exception:
                    qty = 1
            adjacency = read_adjacency_cell(row, "Adjacency (Window/Entry/Core)")
            program.append({
                "name": name, "category": category, "target_area": area, "min_width": min_w, "qty": qty,
                "adjacency": adjacency,
            })
        return program

    def on_recalculate(self, sender, args):
        enclosed_program = self.read_enclosed_program()
        open_program = self.read_open_program()
        # width/depth are minimums for ranged rows - use the range midpoint for
        # the estimate (matches expand_enclosed_program's own size_rng=None
        # fallback), or the exact value for unranged rows (max_width==width).
        enclosed_area = sum(
            (row["width"] + row.get("max_width", row["width"])) / 2.0
            * (row["depth"] + row.get("max_depth", row["depth"])) / 2.0
            * row["qty"]
            for row in enclosed_program
        )
        open_area = sum(row["target_area"] * row["qty"] for row in open_program)
        programmed = enclosed_area + open_area
        boundary_area = self.boundary_w * self.boundary_h
        remaining = boundary_area - programmed
        pct = (programmed / boundary_area * 100.0) if boundary_area > 1e-6 else 0.0
        self.programSummaryText.Text = (
            "Programmed: {:,.0f} sf enclosed + {:,.0f} sf open = {:,.0f} sf of {:,.0f} sf boundary "
            "({:+,.0f} sf remaining, {:.0f}% used)".format(
                enclosed_area, open_area, programmed, boundary_area, remaining, pct)
        )
        self.programSummaryText.Foreground = Brushes.Firebrick if remaining < 0 else Brushes.Black

    def on_cancel(self, sender, args):
        self.Close()

    def on_generate(self, sender, args):
        enclosed_program = self.read_enclosed_program()
        open_program = self.read_open_program()
        if not enclosed_program and not open_program:
            forms.alert("Add at least one enclosed room or open zone.", title="Generate Layout")
            return
        try:
            corridor_width = float(self.corridorWidthBox.Text)
        except Exception:
            corridor_width = 5.0
        try:
            option_count = int(self.optionCountBox.Text)
        except Exception:
            option_count = 3
        option_count = min(max(option_count, 1), 5)
        mode = "randomized" if (self.randomModeRadio and self.randomModeRadio.IsChecked) else "fixed"

        self.result = {
            "enclosed_program": enclosed_program, "open_program": open_program,
            "corridor_width": corridor_width, "option_count": option_count, "mode": mode,
        }
        save_last_program(self.result)
        self.Close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    boundary_element = get_boundary_element()
    if boundary_element is None:
        forms.alert("No Room or Floor selected. Select one and run this tool again.", title="Generate Layout")
        return

    bbox = boundary_element.get_BoundingBox(None)
    if bbox is None:
        forms.alert("Selected element has no boundary (e.g. an unplaced Room). Place/enclose it first.", title="Generate Layout")
        return

    boundary_w = bbox.Max.X - bbox.Min.X
    boundary_h = bbox.Max.Y - bbox.Min.Y
    bbox_area = boundary_w * boundary_h
    element_area = get_element_area(boundary_element)
    rect_ratio = (element_area / bbox_area) if bbox_area > 1e-6 else 0.0

    origin_x, origin_y = bbox.Min.X, bbox.Min.Y

    # Auto-detect the real (possibly irregular/L-shaped) boundary shape and
    # compute the bounding-box area that ISN'T actually part of it, so the
    # algorithm never places rooms outside the real Room/Floor footprint -
    # falls back to today's bounding-box-only behavior (empty list) whenever
    # the boundary isn't purely rectilinear or extraction fails for any reason.
    boundary_loops, boundary_is_orthogonal = get_boundary_polygon(boundary_element, origin_x, origin_y)
    boundary_exclusion_rects_local = compute_boundary_exclusion_rects(
        boundary_w, boundary_h, boundary_loops if boundary_is_orthogonal else None)

    forms.alert(
        "STEP 1 of 2 - Fixed obstacles.\n\n"
        "Select any existing elements that must stay (stair/elevator cores, walls that "
        "can't move) - they'll be excluded from the layout entirely.\n\n"
        "Don't select windows/curtain walls here - those get their own step next.\n\n"
        "Click OK, then in the selection prompt either pick elements or click Finish "
        "immediately with nothing selected to skip this step.",
        title="Generate Layout - Step 1: Obstacles",
    )
    obstacle_elements = get_obstacles(boundary_element.Id)
    obstacles_local = []
    vertical_circulation_local = []
    for el in obstacle_elements:
        obb = el.get_BoundingBox(None)
        if obb is None:
            continue
        ox, oy = obb.Min.X - origin_x, obb.Min.Y - origin_y
        ow, oh = obb.Max.X - obb.Min.X, obb.Max.Y - obb.Min.Y
        obstacles_local.append((ox, oy, ow, oh))
        cat_id = el.Category.Id if el.Category is not None else None
        if cat_id in (STAIRS_CATEGORY_ID, ELEVATORS_CATEGORY_ID):
            vertical_circulation_local.append((ox, oy, ow, oh))

    forms.alert(
        "STEP 2 of 2 - Windows.\n\n"
        "Select curtain wall / storefront / window elements to mark which boundary "
        "edge(s) have windows. This is only used as a soft placement preference for "
        "rows you mark Window/Core in the program grid - it's optional and never "
        "excludes anything from the layout.\n\n"
        "Click OK, then in the selection prompt either pick elements or click Finish "
        "immediately with nothing selected to skip this step.",
        title="Generate Layout - Step 2: Windows",
    )
    window_elements = get_window_elements(boundary_element.Id)
    window_edges_local = set()
    for el in window_elements:
        wbb = el.get_BoundingBox(None)
        if wbb is None:
            continue
        wcx = (wbb.Min.X + wbb.Max.X) / 2.0 - origin_x
        wcy = (wbb.Min.Y + wbb.Max.Y) / 2.0 - origin_y
        window_edges_local.add(_nearest_edge_for_point(wcx, wcy, boundary_w, boundary_h))

    found = scan_existing_conditions(expand_bbox(bbox, 2.0))
    doors_local = [(px - origin_x, py - origin_y) for px, py in door_points_world(found["door"])]

    form = ProgramForm(
        "program_form.xaml", boundary_element, boundary_w, boundary_h, rect_ratio, found, len(obstacles_local),
        vertical_circulation_count=len(vertical_circulation_local), window_edges=window_edges_local,
        is_orthogonal=boundary_is_orthogonal, boundary_exclusion_count=len(boundary_exclusion_rects_local),
    )
    form.ShowDialog()

    if not form.result:
        return  # cancelled

    enclosed_program = form.result["enclosed_program"]
    open_program = form.result["open_program"]
    corridor_width = form.result["corridor_width"]
    option_count = form.result["option_count"]
    mode = form.result["mode"]

    # RANDOMIZED_CANDIDATE_COUNT: how many candidates generate_scored_candidates
    # tries per click before keeping the top `option_count` - generous enough
    # for real diversity, cheap enough to stay instant (pure Python, no Revit
    # API calls happen until the kept candidates are drawn below).
    RANDOMIZED_CANDIDATE_COUNT = 20

    # One seed for this whole click, reused (via a freshly re-seeded
    # random.Random(size_seed) per build_layout call) so enclosed rooms with a
    # size RANGE resolve to the same concrete sizes across every option shown -
    # you're comparing the same rooms under different corridor/layout
    # strategies, not adding room size as a candidate-diversity axis.
    size_seed = random.randint(0, 2 ** 31 - 1)

    candidate_scores = None  # only populated in randomized mode, for the results dialog
    if mode == "randomized":
        scored = generate_scored_candidates(
            boundary_w=boundary_w, boundary_h=boundary_h,
            enclosed_program=enclosed_program, open_program=open_program,
            corridor_width=corridor_width, obstacles=obstacles_local,
            candidate_count=RANDOMIZED_CANDIDATE_COUNT, keep_count=option_count,
            door_points=doors_local, window_edges=window_edges_local,
            vertical_circulation=vertical_circulation_local,
            size_seed=size_seed, boundary_exclusion_rects=boundary_exclusion_rects_local,
        )
        layout_flag_pairs = [(c["layout"], c["flags"]) for c in scored]
        candidate_scores = [c["score"] for c in scored]
    else:
        if doors_local:
            avg_x = sum(p[0] for p in doors_local) / len(doors_local)
            avg_y = sum(p[1] for p in doors_local) / len(doors_local)
            bias_h = avg_y / boundary_h if boundary_h > 1e-6 else 0.5
            bias_v = avg_x / boundary_w if boundary_w > 1e-6 else 0.5
        else:
            bias_h = bias_v = 0.5

        all_variations = [
            ("horizontal", bias_h, "largest_first"),
            ("vertical", bias_v, "largest_first"),
            ("horizontal", min(max(bias_h + 0.15, 0.3), 0.7), "smallest_first"),
            ("vertical", min(max(bias_v - 0.15, 0.3), 0.7), "smallest_first"),
            ("horizontal", 0.5, "smallest_first"),
        ]
        variations = all_variations[:option_count]

        layout_flag_pairs = []
        for axis, bias, order in variations:
            layout = build_layout(
                boundary_w=boundary_w, boundary_h=boundary_h,
                enclosed_program=enclosed_program, open_program=open_program,
                corridor_width=corridor_width, corridor_axis=axis, corridor_bias=bias, item_order=order,
                obstacles=obstacles_local, window_edges=window_edges_local, door_points=doors_local,
                vertical_circulation=vertical_circulation_local,
                size_rng=random.Random(size_seed), boundary_exclusion_rects=boundary_exclusion_rects_local,
            )
            flags = check_egress_heuristics(layout, door_points=doors_local)
            layout_flag_pairs.append((layout, flags))

    boundary_name = get_name(boundary_element) or "Boundary"
    categories = assign_colors(
        [row["category"] for row in enclosed_program] + [row["category"] for row in open_program]
    )

    t = DB.Transaction(doc, "Generate layout options")
    t.Start()
    summary = []
    try:
        fr_type = list(DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType).ToElements())[0]
        text_type = list(DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType).ToElements())[0]
        solid_pattern_id = get_solid_fill_pattern_id()

        category_type_cache = {}
        for category, color in categories.items():
            get_or_create_category_type(category, color, fr_type, solid_pattern_id, category_type_cache)

        for i, (layout, flags) in enumerate(layout_flag_pairs, start=1):
            view = create_drafting_view("Layout - {} - Option {}".format(boundary_name, i))
            draw_layout(view, layout, category_type_cache, fr_type.Id, text_type.Id, origin_xy=(origin_x, origin_y))

            summary.append((i, view, layout, flags))

        t.Commit()
    except Exception as e:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert("Failed to generate layouts:\n\n{}".format(str(e)), title="Generate Layout", warn_icon=True)
        return

    if summary:
        uidoc.ActiveView = summary[0][1]

    mode_label = "randomized, best of {}".format(RANDOMIZED_CANDIDATE_COUNT) if mode == "randomized" else "fixed"
    lines = [
        "Generated {} option(s) for {} ({} fixed obstacle(s) excluded, {} mode):".format(
            len(summary), boundary_name, len(obstacles_local), mode_label),
        "Each option's local origin (0,0) - marked with a '+' in the drafting view - "
        "= Revit model coordinate ({:.2f}, {:.2f}). Use that to align a view against "
        "the real floor plan by hand.".format(origin_x, origin_y),
    ]
    if boundary_exclusion_rects_local:
        lines.append(
            "Boundary shape auto-detected as irregular: {} area(s) outside the real footprint "
            "excluded from every option (shown as 'Outside room footprint' outlines).".format(
                len(boundary_exclusion_rects_local)))
    lines.append("")
    for i, view, layout, flags in summary:
        score_suffix = ""
        if candidate_scores is not None:
            score_suffix = ", score {:.1f}".format(candidate_scores[i - 1])
        lines.append("Option {} ({}): {} rooms placed, {} unplaced, {} unallocated area(s), {} branch hall(s){}".format(
            i, get_name(view), len(layout["rooms"]), len(layout["unplaced"]), len(layout["leftover"]),
            layout["branch_count"], score_suffix))
        if layout["unplaced"]:
            lines.append("    Unplaced: {}".format(", ".join(it["name"] for it in layout["unplaced"])))
        for f in flags:
            lines.append("    ! {}".format(f))
    lines.append("")
    lines.append("Design aid only - not a code compliance review. Verify egress, corridor width, "
                  "and occupancy against the applicable code before use.")

    forms.alert("\n".join(lines), title="Generate Layout - Results")


main()
