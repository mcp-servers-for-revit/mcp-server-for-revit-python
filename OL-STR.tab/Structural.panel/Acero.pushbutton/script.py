# -*- coding: utf-8 -*-
"""Genera el acero de refuerzo (barras 3D) y su metrado en kg para
columnas de concreto. La configuracion (longitudinal, estribos, nucleo y
el dibujo de la seccion con estribos, grapas y barras a mano) se guarda
en cada tipo de columna."""

__title__ = "Acero"
__author__ = "Revit MCP"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_spatial as fw_spatial
import formwork_geometry as fw_geom
import formwork_params as fw_params
import rebar_spec as rs
import rebar_columns as rc
import utils as fw_utils

# pyRevit keeps one interpreter across clicks: reload so edits on disk
# are picked up (same as the Encofrado button).
reload(fw_utils)
reload(fw_spatial)
reload(fw_geom)
reload(fw_params)
reload(rs)
reload(rc)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
from System.Windows import Point, Thickness, VerticalAlignment
from System.Windows.Controls import Canvas, CheckBox, ListBoxItem, Orientation, StackPanel, TextBlock
from System.Windows.Media import Color, DoubleCollection, PointCollection, SolidColorBrush
from System.Windows.Shapes import Ellipse, Line, Polygon, Polyline

output = script.get_output()
doc = revit.doc

COLUMNS_BIC = DB.BuiltInCategory.OST_StructuralColumns
STIRRUP_DIAMETERS = [u"6mm", u"8mm", u'1/4"', u'3/8"', u"12mm", u'1/2"']
BAR_DIAMETERS = [u'3/8"', u"12mm", u'1/2"', u'5/8"', u'3/4"', u'1"', u'1 3/8"']
SNAP_PX = 12  # a click this close to a bar snaps to it
MARGIN_PX = 30


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_CONCRETE = brush(225, 228, 232)
C_OUTLINE = brush(60, 60, 60)
C_COVER = brush(150, 150, 150)
C_BAR = brush(40, 40, 40)
C_STIRRUP = brush(214, 120, 60)
C_DRAFT = brush(40, 110, 220)
C_CURSOR = brush(40, 110, 220, 160)


def id_of(element_id):
    return fw_utils.element_id_value(element_id)


class ColumnType(object):
    """A column type in use in the model, with a representative column."""

    def __init__(self, type_id, columns):
        self.id = type_id
        self.element = doc.GetElement(DB.ElementId(type_id))
        self.name = rc.element_name(self.element)
        self.columns = columns
        self._section = None
        self.section_error = None

    @property
    def section(self):
        if self._section is None and self.section_error is None:
            try:
                self._section = rc.Section(self.columns[0])
            except Exception as e:
                self.section_error = u"{}".format(e)
        return self._section

    def config(self):
        return rc.read_type_config(self.element)

    def configured(self):
        cfg = self.config()
        return bool(cfg["EA_Estribo_Distribucion"] and (cfg["EA_Longitudinal"] or cfg["EA_Seccion_Armado"]))


class State(object):
    """What survives closing the window to pick columns in the model."""

    def __init__(self):
        self.checked = set()
        self.picked_ids = []
        self.active = None
        self.drafts = {}  # type id -> design being drawn (unsaved)
        self.form = None  # last form field values
        self.scope = "model"
        self.scope_level_index = -1


class _ColumnFilter(ISelectionFilter):
    def AllowElement(self, element):
        category = element.Category
        return category is not None and id_of(category.Id) == int(COLUMNS_BIC)

    def AllowReference(self, reference, point):
        return False


def collect_types():
    by_type = {}
    for c in (
        DB.FilteredElementCollector(doc).OfCategory(COLUMNS_BIC).WhereElementIsNotElementType()
    ):
        by_type.setdefault(id_of(c.GetTypeId()), []).append(c)
    return sorted(
        [ColumnType(t, cols) for t, cols in by_type.items()], key=lambda ct: ct.name
    )


class AceroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, types, state):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.types = types
        self.by_id = dict((t.id, t) for t in types)
        self.state = state
        self.action = None
        self.checkboxes = {}
        self.design = rs.empty_design()
        self.undo_stack = []
        self.draft = []  # stirrup vertices / tie start being drawn (meters)
        self.cursor_m = None
        self.dirty = False

        self.cbo_stirrup.ItemsSource = List[str](STIRRUP_DIAMETERS)
        self.cbo_bar.ItemsSource = List[str](BAR_DIAMETERS)
        self.cbo_bar.SelectedItem = u'5/8"'
        self.levels = sorted(
            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(),
            key=lambda lv: lv.ProjectElevation,
        )
        self.cbo_scope_level.ItemsSource = List[str]([lv.Name for lv in self.levels])
        self.cbo_scope_level.SelectedIndex = state.scope_level_index
        {"level": self.rb_scope_level, "pick": self.rb_scope_pick}.get(
            state.scope, self.rb_scope_model
        ).IsChecked = True

        for t in types:
            self.list_types.Items.Add(self._type_item(t))
        self._refresh_picked()
        if state.form:
            self._set_form(state.form)
        active = state.active if state.active in self.by_id else (types[0].id if types else None)
        if active is not None:
            self._select_type(active)

    # -- type list ---------------------------------------------------------
    def _type_item(self, t):
        panel = StackPanel()
        panel.Orientation = Orientation.Horizontal
        box = CheckBox()
        box.IsChecked = t.id in self.state.checked
        box.VerticalAlignment = VerticalAlignment.Center
        box.Margin = Thickness(0, 0, 6, 0)
        self.checkboxes[t.id] = box
        label = TextBlock()
        label.Text = u"{}{}  ({})".format(u"✓ " if t.configured() else u"", t.name, len(t.columns))
        panel.Children.Add(box)
        panel.Children.Add(label)
        item = ListBoxItem()
        item.Content = panel
        item.Tag = t.id
        return item

    def _refresh_type_labels(self):
        for item in self.list_types.Items:
            t = self.by_id[item.Tag]
            item.Content.Children[1].Text = u"{}{}  ({})".format(
                u"✓ " if t.configured() else u"", t.name, len(t.columns))

    def checked_ids(self):
        return [t for t, box in self.checkboxes.items() if box.IsChecked]

    def all_click(self, sender, args):
        for box in self.checkboxes.values():
            box.IsChecked = bool(self.chk_all.IsChecked)

    def type_selected(self, sender, args):
        item = self.list_types.SelectedItem
        if item is None or item.Tag == self.state.active:
            return
        self._keep_draft()
        self._select_type(item.Tag)

    def _select_type(self, type_id):
        self.state.active = type_id
        t = self.by_id[type_id]
        for item in self.list_types.Items:
            if item.Tag == type_id and self.list_types.SelectedItem is not item:
                self.list_types.SelectedItem = item
        cfg = t.config()
        self._set_form({
            "long": cfg["EA_Longitudinal"],
            "stirrup": cfg["EA_Estribo_Diametro"] or u'3/8"',
            "cover": cfg["EA_Recubrimiento_cm"] or str(rc.default_cover_cm(t.name)),
            "dist": cfg["EA_Estribo_Distribucion"],
            "nucleo": cfg["EA_Nucleo_cm"],
        })
        if type_id in self.state.drafts:
            self.design = self.state.drafts[type_id]
            self.dirty = True
        else:
            try:
                self.design = rs.design_from_text(cfg["EA_Seccion_Armado"]) or rs.empty_design()
            except rs.SpecError:
                self.design = rs.empty_design()
            self.dirty = False
        self.undo_stack = []
        self.draft = []
        section = t.section
        if section is None:
            self.txt_section.Text = u"Seccion {}: {}".format(t.name, t.section_error)
        else:
            self.txt_section.Text = u"Seccion {}  ({:.0f} x {:.0f} cm{})".format(
                t.name, section.b * rc.FT * 100, section.h * rc.FT * 100,
                u"" if section.is_rectangle else u", irregular")
        self.redraw()

    def _keep_draft(self):
        if self.dirty and self.state.active is not None:
            self.state.drafts[self.state.active] = self.design

    # -- form --------------------------------------------------------------
    def _set_form(self, f):
        self.txt_long.Text = f.get("long") or u""
        self.cbo_stirrup.SelectedItem = f.get("stirrup") if f.get("stirrup") in STIRRUP_DIAMETERS else u'3/8"'
        self.txt_cover.Text = f.get("cover") or u""
        self.txt_dist.Text = f.get("dist") or u""
        self.chk_nucleo.IsChecked = bool(f.get("nucleo"))
        self.txt_nucleo.Text = f.get("nucleo") or u"10"

    def _get_form(self):
        return {
            "long": (self.txt_long.Text or u"").strip(),
            "stirrup": self.cbo_stirrup.SelectedItem or u'3/8"',
            "cover": (self.txt_cover.Text or u"").strip(),
            "dist": (self.txt_dist.Text or u"").strip(),
            "nucleo": (self.txt_nucleo.Text or u"").strip() if self.chk_nucleo.IsChecked else u"",
        }

    def _config_from_form(self, with_drawing):
        f = self._get_form()
        config = {
            "EA_Longitudinal": f["long"],
            "EA_Estribo_Diametro": f["stirrup"],
            "EA_Estribo_Distribucion": f["dist"],
            "EA_Recubrimiento_cm": f["cover"],
            "EA_Nucleo_cm": f["nucleo"],
        }
        if with_drawing:
            config["EA_Seccion_Armado"] = rs.design_to_text(self.design)
        return config

    def save_click(self, sender, args):
        self.save()

    def save(self):
        """Write the form (and, for types with the active type's section,
        the drawing) into the checked types. False if nothing was saved."""
        targets = self.checked_ids() or ([self.state.active] if self.state.active else [])
        if not targets:
            forms.alert(u"Marca al menos un tipo.", title="Acero")
            return False
        active = self.by_id.get(self.state.active)
        same_section = []
        for type_id in targets:
            s, a = self.by_id[type_id].section, active.section if active else None
            if s is not None and a is not None and all(
                abs(p[0] - q[0]) < 0.002 and abs(p[1] - q[1]) < 0.002
                for p, q in zip(s.polygon_m, a.polygon_m)
            ) and len(s.polygon_m) == len(a.polygon_m):
                same_section.append(type_id)
        try:
            for type_id in targets:
                rc.ColumnSpec(self._config_from_form(type_id in same_section))
        except rs.SpecError as e:
            forms.alert(u"Revisa la configuracion: {}".format(e), title="Acero")
            return False
        with revit.Transaction("Acero - configuracion de columnas"):
            rc.ensure_parameters(doc)
            for type_id in targets:
                rc.write_type_config(
                    self.by_id[type_id].element,
                    self._config_from_form(type_id in same_section),
                )
        for type_id in same_section:
            self.state.drafts.pop(type_id, None)
        self.dirty = False
        self._refresh_type_labels()
        skipped = [self.by_id[t].name for t in targets if t not in same_section]
        if skipped and (self.design["bars"] or self.design["stirrups"] or self.design["ties"]):
            forms.alert(
                u"Configuracion guardada. El dibujo solo se guardo en los tipos con la "
                u"misma seccion; estos quedan con la distribucion automatica:\n- "
                + u"\n- ".join(skipped),
                title="Acero",
            )
        return True

    # -- actions -----------------------------------------------------------
    def pick_click(self, sender, args):
        self._leave("pick")

    def run_click(self, sender, args):
        if self.dirty:
            if forms.alert(
                u"El dibujo de la seccion tiene cambios sin guardar. "
                u"Guardarlos en los tipos marcados antes de generar?",
                title="Acero", yes=True, no=True,
            ) and not self.save():
                return
        if self.rb_scope_level.IsChecked and self.cbo_scope_level.SelectedIndex < 0:
            forms.alert(u"Elige el nivel.", title="Acero")
            return
        if self.rb_scope_pick.IsChecked and not self.state.picked_ids:
            forms.alert(u"Primero selecciona columnas en el modelo.", title="Acero")
            return
        if not self.rb_scope_pick.IsChecked and not self.checked_ids():
            forms.alert(u"Marca los tipos a generar.", title="Acero")
            return
        self._leave("run")

    def _leave(self, action):
        self._keep_draft()
        self.state.checked = set(self.checked_ids())
        self.state.form = self._get_form()
        self.state.scope = "level" if self.rb_scope_level.IsChecked else (
            "pick" if self.rb_scope_pick.IsChecked else "model")
        self.state.scope_level_index = self.cbo_scope_level.SelectedIndex
        self.action = action
        self.Close()

    def _refresh_picked(self):
        n = len(self.state.picked_ids)
        self.txt_picked.Text = (
            u"{} columna(s) seleccionada(s) en el modelo.".format(n) if n
            else u"No hay columnas seleccionadas en el modelo."
        )

    # -- drawing -----------------------------------------------------------
    def _frame(self):
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        width, height = self.canvas.ActualWidth, self.canvas.ActualHeight
        if section is None or width < 50 or height < 50:
            return None
        xs = [p[0] for p in section.polygon_m]
        ys = [p[1] for p in section.polygon_m]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        scale = min((width - 2 * MARGIN_PX) / span_x, (height - 2 * MARGIN_PX) / span_y)
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        return section, scale, width / 2.0 - cx * scale, height / 2.0 + cy * scale

    def to_px(self, frame, x, y):
        _, scale, ox, oy = frame
        return Point(ox + x * scale, oy - y * scale)

    def to_m(self, frame, p):
        _, scale, ox, oy = frame
        return ((p.X - ox) / scale, (oy - p.Y) / scale)

    def snap(self, frame, p):
        """Nearest bar center within SNAP_PX, else the point on a 1 cm grid."""
        best = None
        for x, y, _ in self.design["bars"]:
            q = self.to_px(frame, x, y)
            d = ((q.X - p.X) ** 2 + (q.Y - p.Y) ** 2) ** 0.5
            if d <= SNAP_PX and (best is None or d < best[0]):
                best = (d, (x, y))
        if best:
            return best[1], True
        x, y = self.to_m(frame, p)
        return (round(x, 2), round(y, 2)), False

    def _add(self, shape):
        self.canvas.Children.Add(shape)
        return shape

    def _polyline(self, frame, points, stroke, thickness, closed=False, dash=False, fill=None):
        shape = Polygon() if closed else Polyline()
        pts = PointCollection()
        for x, y in points:
            pts.Add(self.to_px(frame, x, y))
        shape.Points = pts
        shape.Stroke = stroke
        shape.StrokeThickness = thickness
        if dash:
            dashes = DoubleCollection()
            dashes.Add(4)
            dashes.Add(3)
            shape.StrokeDashArray = dashes
        if fill is not None:
            shape.Fill = fill
        return self._add(shape)

    def _dot(self, frame, x, y, radius_px, fill):
        e = Ellipse()
        e.Width = e.Height = 2 * radius_px
        e.Fill = fill
        p = self.to_px(frame, x, y)
        Canvas.SetLeft(e, p.X - radius_px)
        Canvas.SetTop(e, p.Y - radius_px)
        return self._add(e)

    def redraw(self):
        self.canvas.Children.Clear()
        frame = self._frame()
        if frame is None:
            return
        section, scale = frame[0], frame[1]
        self._polyline(frame, section.polygon_m, C_OUTLINE, 2, closed=True, fill=C_CONCRETE)
        stirrup_key = self.cbo_stirrup.SelectedItem or u'3/8"'
        try:
            cover = float((self.txt_cover.Text or u"4").replace(u",", u".")) / 100.0
            self._polyline(frame, rs.offset_polygon_outward(section.polygon_m, -cover), C_COVER, 1,
                           closed=True, dash=True)
        except Exception:
            pass
        for poly in self.design["stirrups"]:
            try:
                line = rs.stirrup_centerline(poly, self.design["bars"], stirrup_key)
            except rs.SpecError:
                line = poly
            self._polyline(frame, line, C_STIRRUP, max(2, rs.BAR_DIAMETERS_MM[stirrup_key] / 1000.0 * scale),
                           closed=True)
        for a, b in self.design["ties"]:
            try:
                a2, b2 = rs.tie_centerline(a, b, self.design["bars"], stirrup_key)
            except rs.SpecError:
                a2, b2 = a, b
            self._polyline(frame, [a2, b2], C_STIRRUP, max(2, rs.BAR_DIAMETERS_MM[stirrup_key] / 1000.0 * scale))
        for x, y, key in self.design["bars"]:
            self._dot(frame, x, y, max(3.5, rs.BAR_DIAMETERS_MM[key] / 2000.0 * scale), C_BAR)
        if self.draft:
            pts = list(self.draft) + ([self.cursor_m] if self.cursor_m else [])
            self._polyline(frame, pts, C_DRAFT, 2)
            for x, y in self.draft:
                self._dot(frame, x, y, 4, C_DRAFT)
        if self.cursor_m:
            self._dot(frame, self.cursor_m[0], self.cursor_m[1], 3, C_CURSOR)

    def _push_undo(self):
        self.undo_stack.append(rs.design_to_text(self.design))
        self.dirty = True

    def canvas_resized(self, sender, args):
        self.redraw()

    def canvas_move(self, sender, args):
        frame = self._frame()
        if frame is None:
            return
        self.cursor_m, _ = self.snap(frame, args.GetPosition(self.canvas))
        self.redraw()

    def canvas_left(self, sender, args):
        frame = self._frame()
        if frame is None:
            return
        p = args.GetPosition(self.canvas)
        point, on_bar = self.snap(frame, p)
        if self.rb_bar.IsChecked:
            if not on_bar:
                self._push_undo()
                self.design["bars"].append((point[0], point[1], self.cbo_bar.SelectedItem or u'5/8"'))
        elif self.rb_stirrup.IsChecked:
            if len(self.draft) >= 3 and point == self.draft[0]:
                self._close_stirrup()
            elif not self.draft or point != self.draft[-1]:
                self.draft.append(point)
        elif self.rb_tie.IsChecked:
            if not self.draft:
                self.draft = [point]
            elif point != self.draft[0]:
                self._push_undo()
                self.design["ties"].append([self.draft[0], point])
                self.draft = []
        elif self.rb_erase.IsChecked:
            self._erase(frame, p)
        self.redraw()

    def canvas_right(self, sender, args):
        if self.rb_stirrup.IsChecked and len(self.draft) >= 3:
            self._close_stirrup()
        else:
            self.draft = []
        self.redraw()

    def _close_stirrup(self):
        self._push_undo()
        self.design["stirrups"].append(list(self.draft))
        self.draft = []

    def _erase(self, frame, p):
        """Remove the bar, tie or stirrup nearest to the click."""
        _, scale = frame[0], frame[1]
        m = self.to_m(frame, p)
        tol = SNAP_PX / scale
        candidates = []
        for i, (x, y, _) in enumerate(self.design["bars"]):
            candidates.append((((x - m[0]) ** 2 + (y - m[1]) ** 2) ** 0.5, "bars", i))
        for i, (a, b) in enumerate(self.design["ties"]):
            candidates.append((rs.distance_to_polygon(m, [a, b]), "ties", i))
        for i, poly in enumerate(self.design["stirrups"]):
            candidates.append((rs.distance_to_polygon(m, poly), "stirrups", i))
        candidates = [c for c in candidates if c[0] <= tol]
        if candidates:
            _, kind, i = min(candidates)
            self._push_undo()
            del self.design[kind][i]

    def auto_click(self, sender, args):
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        if section is None or not section.is_rectangle:
            forms.alert(u"La distribucion automatica es solo para secciones rectangulares; "
                        u"dibuja las barras y estribos.", title="Acero")
            return
        f = self._get_form()
        try:
            groups = rs.parse_longitudinal(f["long"])
            cover = float(f["cover"].replace(u",", u".")) / 100.0
            design = rs.auto_design(section.b * rc.FT, section.h * rc.FT, cover, f["stirrup"], groups)
        except (rs.SpecError, ValueError) as e:
            forms.alert(u"Revisa Longitudinal/Recubrimiento: {}".format(e), title="Acero")
            return
        self._push_undo()
        self.design = design
        self.draft = []
        self.redraw()

    def undo_click(self, sender, args):
        if self.draft:
            self.draft = self.draft[:-1]
        elif self.undo_stack:
            self.design = rs.design_from_text(self.undo_stack.pop()) or rs.empty_design()
        self.redraw()

    def clear_click(self, sender, args):
        self._push_undo()
        self.design = rs.empty_design()
        self.draft = []
        self.redraw()


def pick_columns(state):
    uidoc = revit.uidoc
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, _ColumnFilter(),
            "Selecciona las columnas y pulsa Finalizar",
        )
    except OperationCanceledException:
        return
    picked = [doc.GetElement(r.ElementId) for r in refs]
    state.picked_ids = [id_of(c.Id) for c in picked if c is not None]
    state.checked |= set(id_of(c.GetTypeId()) for c in picked if c is not None)
    if state.picked_ids:
        state.scope = "pick"


# --- main -------------------------------------------------------------------
types = collect_types()
if not types:
    forms.alert("El modelo no tiene columnas estructurales.", title="Acero")
    script.exit()

state = State()
preselected = [doc.GetElement(i) for i in revit.uidoc.Selection.GetElementIds()]
preselected = [e for e in preselected if e is not None and _ColumnFilter().AllowElement(e)]
if preselected:
    state.picked_ids = [id_of(c.Id) for c in preselected]
    state.checked = set(id_of(c.GetTypeId()) for c in preselected)
    state.scope = "pick"

xaml = os.path.join(SCRIPT_DIR, "AceroForm.xaml")
while True:
    window = AceroWindow(xaml, types, state)
    window.ShowDialog()
    if window.action == "pick":
        pick_columns(state)
        continue
    if window.action != "run":
        script.exit()
    break

by_id = dict((t.id, t) for t in types)
levels = window.levels
if state.scope == "pick":
    targets = [doc.GetElement(DB.ElementId(i)) for i in state.picked_ids]
    targets = [c for c in targets if c is not None]
    scope_label = "columnas seleccionadas"
else:
    targets = [c for t in state.checked for c in by_id[t].columns]
    scope_label = "tipos marcados, todo el modelo"
    if state.scope == "level":
        level = levels[state.scope_level_index]
        targets = [
            c for c in targets
            if fw_geom.element_pour_level_id(c, "columns", levels) == level.Id
        ]
        scope_label = u"tipos marcados, vaciado del nivel {}".format(level.Name)

specs = {}
spec_errors = {}
for t in set(id_of(c.GetTypeId()) for c in targets):
    try:
        specs[t] = rc.ColumnSpec(by_id[t].config())
    except rs.SpecError as e:
        spec_errors[t] = u"{}".format(e)
with_spec = [c for c in targets if id_of(c.GetTypeId()) in specs]
if not with_spec:
    forms.alert(
        u"Ninguna de las {} columnas del alcance tiene su tipo configurado.".format(len(targets)),
        title="Acero",
    )
    script.exit()

mode = forms.CommandSwitchWindow.show(
    ["Vista previa (sin cambios en el modelo)", "Generar barras y metrado"],
    message=u"Modo de ejecucion ({} columnas, {}):".format(len(with_spec), scope_label),
)
if not mode:
    script.exit()
dry_run = mode.startswith("Vista previa")

bar_types = rc.BarTypes(doc)
hooks = rc.StirrupHooks(doc)
warnings = [u"{}: sin generar - {}".format(by_id[t].name, e) for t, e in spec_errors.items()]
stick_out = {}  # type name -> (columns whose stirrup hooks leave the section, hook)
by_type = {}  # type name -> [columns, long kg, stirrup kg, bars]
total_bars = 0

t = DB.Transaction(doc, "Acero")
t.Start()
try:
    rc.ensure_parameters(doc)
    done = []
    with forms.ProgressBar(title="Acero: {value} de {max_value} columnas") as pb:
        for i, column in enumerate(with_spec):
            ct = by_id[id_of(column.GetTypeId())]
            column_spec = specs[ct.id]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                created = rc.generate_column(
                    doc, column, column_spec, bar_types, hooks, rc.type_mark(ct.name)
                )
                sub.Commit()
                done.append((column, ct.name, hooks.get(column_spec.stirrup_key), created))
            except Exception as e:
                sub.RollBack()
                warnings.append(u"Columna {} ({}): {}".format(id_of(column.Id), ct.name, e))
            pb.update_progress(i + 1, len(with_spec))

    doc.Regenerate()  # bar lengths are only known after a regeneration
    for column, type_name, hook, created in done:
        long_kg, stirrup_kg, bars, sticks = rc.record_weight(column, created)
        if sticks:
            stick_out[type_name] = (stick_out.get(type_name, (0, hook))[0] + 1, hook)
        agg = by_type.setdefault(type_name, [0, 0.0, 0.0, 0])
        agg[0] += 1
        agg[1] += long_kg
        agg[2] += stirrup_kg
        agg[3] += bars
        total_bars += bars

    if dry_run:
        t.RollBack()
    else:
        t.Commit()
except Exception:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    raise

output.print_md("# Resultado Acero - Columnas {}".format("(vista previa)" if dry_run else ""))
output.print_md(u"**Alcance:** {}".format(scope_label))
output.print_md("**Columnas armadas:** {}".format(sum(v[0] for v in by_type.values())))
if not dry_run:
    output.print_md("**Barras creadas:** {}".format(total_bars))
skipped = len(targets) - len(with_spec)
if skipped:
    output.print_md("**Columnas sin configuracion (omitidas):** {}".format(skipped))

output.print_md("\n| Tipo | Columnas | Longitudinal (kg) | Estribos y grapas (kg) | Total (kg) |")
output.print_md("|---|---|---|---|---|")
grand = [0.0, 0.0]
for type_name in sorted(by_type):
    n, long_kg, stirrup_kg, _ = by_type[type_name]
    grand[0] += long_kg
    grand[1] += stirrup_kg
    output.print_md(u"| {} | {} | {:.2f} | {:.2f} | {:.2f} |".format(
        type_name, n, long_kg, stirrup_kg, long_kg + stirrup_kg))
output.print_md("| **Total** | | **{:.2f}** | **{:.2f}** | **{:.2f}** |".format(
    grand[0], grand[1], grand[0] + grand[1]))

output.print_md(
    "\n*Longitudinales rectas de piso a piso (sin empalmes ni anclajes). "
    "Estribos en la luz libre, hasta el fondo de la viga o losa superior "
    "(y en el nucleo si se configuro).*"
)
for type_name in sorted(stick_out):
    count, hook = stick_out[type_name]
    warnings.append(
        u"{}: en {} columna(s) el gancho de los estribos ({}) sobresale de la seccion; "
        u"conviene un gancho mas corto para ese diametro.".format(
            type_name, count, rc.element_name(hook) if hook else u"sin gancho")
    )
if warnings:
    output.print_md("\n### Advertencias ({})".format(len(warnings)))
    for w in warnings[:50]:
        output.print_md(u"- {}".format(w))
