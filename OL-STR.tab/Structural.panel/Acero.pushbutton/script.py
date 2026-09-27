# -*- coding: utf-8 -*-
"""Genera el acero de refuerzo (barras 3D) y su metrado en kg para
columnas de concreto. La configuracion (longitudinal, estribos, nucleo y
el dibujo de la seccion con estribos, grapas y barras a mano) se guarda
en cada tipo de columna."""

__title__ = "Acero"
__author__ = "Revit MCP"

import io
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_spatial as fw_spatial
import formwork_params as fw_params
import rebar_spec as rs
import rebar_columns as rc
import rebar_views as rv
import utils as fw_utils

# pyRevit keeps one interpreter across clicks: reload so edits on disk
# are picked up (same as the Encofrado button).
reload(fw_utils)
reload(fw_spatial)
reload(fw_params)
reload(rs)
reload(rc)
reload(rv)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List
from System.Windows import (FontWeights, HorizontalAlignment, Point, Size, Thickness,
                            VerticalAlignment, Visibility)
from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
from Autodesk.Revit.DB.Structure import RebarShape, RebarStyle
from System.Windows.Input import Key, Keyboard, MouseButton
from System.Windows.Controls import (Button, Canvas, CheckBox, ListBoxItem, Orientation, StackPanel,
                                     TextBlock, TextBox)
from System.Windows.Media import Color, DoubleCollection, PointCollection, SolidColorBrush
from System.Windows.Shapes import Ellipse, Line, Polygon, Polyline

output = script.get_output()
doc = revit.doc

COLUMNS_BIC = DB.BuiltInCategory.OST_StructuralColumns
STIRRUP_DIAMETERS = [u"6mm", u"8mm", u'1/4"', u'3/8"', u"12mm", u'1/2"']
BAR_SLOT_DEFAULTS = [u'1/2"', u'5/8"', u'3/4"', u'1"']  # the four "Barras" options


def load_bar_slots():
    """The diameters of the four "Barras" options the user chose last time
    (pyRevit settings of this button), else the defaults."""
    try:
        saved = script.get_config().get_option("bar_slots", u"") or u""
        keys = [rs.parse_diameter(k) for k in saved.split(u"|") if k.strip()]
        if len(keys) == len(BAR_SLOT_DEFAULTS):
            return keys
    except Exception:
        pass
    return list(BAR_SLOT_DEFAULTS)


def save_bar_slots(keys):
    """Remember the four diameters (inches written 'pulg': no quotes in the
    settings file)."""
    try:
        config = script.get_config()
        config.bar_slots = u"|".join((k or u"").replace(u'"', u"pulg") for k in keys)
        script.save_config()
    except Exception:
        pass


SNAP_PX = 12  # a click this close to a bar snaps to it
MARGIN_PX = 30


def brush(r, g, b, a=255):
    return SolidColorBrush(Color.FromArgb(a, r, g, b))


C_CONCRETE = brush(225, 228, 232)
C_OUTLINE = brush(60, 60, 60)
C_COVER = brush(150, 150, 150)
C_BAR = brush(40, 40, 40)
C_STIRRUP = brush(214, 120, 60)  # perimeter ("borde") stirrup
C_CONFINEMENT = brush(40, 150, 90)  # confinement stirrups and ties
C_DRAFT = brush(40, 110, 220)
C_CURSOR = brush(40, 110, 220, 160)
C_LABEL_BG = brush(255, 255, 255, 200)
C_REFUSED = brush(192, 57, 43)  # cursor where a stirrup corner is refused


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
        return bool(cfg["EA_Estribo_Borde_Distribucion"] and cfg["EA_Seccion_Armado"])


class State(object):
    """What survives closing the window to pick columns in the model."""

    def __init__(self):
        self.checked = set()
        self.picked_ids = []
        self.active = None
        self.drafts = {}  # type id -> design being drawn (unsaved)
        self.form = None  # last form field values
        self.scope = "model"  # "pick" when columns were picked in the model


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


SHAPE_PREVIEW_W = 200
SHAPE_PREVIEW_H = 95


def read_rebar_shapes():
    """The project's rebar shapes as Revit's own Rebar Shape Browser draws
    them (GetCurvesForBrowser: segments, bends and hooks), read fresh so
    shapes loaded meanwhile show up."""
    shapes = []
    for shape in DB.FilteredElementCollector(doc).OfClass(RebarShape):
        try:
            curves = list(shape.GetCurvesForBrowser())
        except Exception:
            continue
        strokes, lines = [], []
        for curve in curves:
            strokes.append([(q.X, q.Y) for q in curve.Tessellate()])
            if isinstance(curve, DB.Line):
                a, b = curve.GetEndPoint(0), curve.GetEndPoint(1)
                lines.append(((a.X, a.Y), (b.X, b.Y)))
        if not strokes:
            continue
        hooks = []
        for end_index in (0, 1):
            try:
                hooks.append(bool(shape.GetDefaultHookAngle(end_index)))
            except Exception:
                hooks.append(False)
        shapes.append({
            "name": rc.element_name(shape),
            "stirrup": shape.RebarStyle == RebarStyle.StirrupTie,
            "strokes": strokes,
            "lines": lines,
            "hooks": hooks,
        })
    shapes.sort(key=lambda sh: _natural_key(sh["name"]))
    return shapes


def shape_preview(strokes):
    """The shape drawn like in Revit's browser: same proportions, dark
    strokes, bends and hooks included."""
    canvas = Canvas()
    canvas.Width, canvas.Height = SHAPE_PREVIEW_W, SHAPE_PREVIEW_H
    pts = [q for stroke in strokes for q in stroke]
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = min((SHAPE_PREVIEW_W - 24) / span, (SHAPE_PREVIEW_H - 16) / span)
    ox = SHAPE_PREVIEW_W / 2.0 - (max(xs) + min(xs)) / 2.0 * scale
    oy = SHAPE_PREVIEW_H / 2.0 + (max(ys) + min(ys)) / 2.0 * scale
    for stroke in strokes:
        line = Polyline()
        points = PointCollection()
        for x, y in stroke:
            points.Add(Point(ox + x * scale, oy - y * scale))
        line.Points = points
        line.Stroke = C_BAR
        line.StrokeThickness = 2
        canvas.Children.Add(line)
    return canvas

def _natural_key(name):
    """'Forma 2' before 'Forma 10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


class AceroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, types, state):
        # Esc must not close the window (it cancels the current stroke,
        # see window_key).
        forms.WPFWindow.__init__(self, xaml_file_path, handle_esc=False)
        self.types = types
        self.by_id = dict((t.id, t) for t in types)
        self.state = state
        self.action = None
        self.checkboxes = {}
        self.design = rs.empty_design()
        self.undo_stack = []
        self.draft = []  # stirrup vertices / tie start being drawn (meters)
        self.cursor_m = None
        self.cursor_on_bar = False
        self.dirty = False

        self.cbo_conf.ItemsSource = List[str](STIRRUP_DIAMETERS)
        self.cbo_edge.ItemsSource = List[str](STIRRUP_DIAMETERS)
        # "Ø acero" of the sketch: which stirrup family a new stirrup/tie
        # uses. A new longitudinal bar takes the diameter of the "Barras"
        # option chosen.
        self.kind_for = {"stirrup": rs.KIND_EDGE, "tie": rs.KIND_CONFINEMENT}
        # "Barras": four options, each with the diameter chosen in its own
        # list (remembered in the user's pyRevit settings).
        self.bar_tools = [(self.rb_bar_1, self.cbo_bar_1), (self.rb_bar_2, self.cbo_bar_2),
                          (self.rb_bar_3, self.cbo_bar_3), (self.rb_bar_4, self.cbo_bar_4)]
        self._filling_bars = True
        for (rb, cbo), key in zip(self.bar_tools, load_bar_slots()):
            cbo.ItemsSource = List[str](rs.bar_diameter_keys())
            cbo.SelectedItem = key
        self._filling_bars = False
        self.bar_key = self.cbo_bar_2.SelectedItem
        self._updating_steel = False
        self.selected = None  # index of the stirrup whose measures are shown
        # Plan / elevation / 3D views: zoom-pan state and what they last showed.
        self.plan_nav = rv.Nav2D()
        self.elev_nav = rv.Nav2D()
        self.scene = rv.Scene3D(self.view3d)
        self.cbo_detail.ItemsSource = List[str](list(rv.DETAIL_LEVELS))
        self.cbo_detail.SelectedItem = u"Medio"
        self._views_sig = None
        self._elev_data = None
        self._clear_cache = {}
        self._neighbor_cache = {}  # column id -> elements touching it
        self._section_cache = {}  # column id -> rc.Section
        self._view_columns = []  # the column(s), stacked, the 3D view and elevation show
        self._filling_columns = False

        self._refresh_steel()
        # The shape clicked in the browser: the stirrups (or, a straight one,
        # the ties) drawn next are created with it. None: Revit picks.
        self.active_shape = None
        self._filling_shapes = False
        self.rebar_shapes = read_rebar_shapes()
        self._fill_shapes()
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
                self.list_types.ScrollIntoView(item)
        cfg = t.config()
        self._set_form({
            "conf": cfg["EA_Estribo_Conf_Diametro"] or u'3/8"',
            "conf_dist": cfg["EA_Estribo_Conf_Distribucion"],
            "edge": cfg["EA_Estribo_Borde_Diametro"] or u'3/8"',
            "edge_dist": cfg["EA_Estribo_Borde_Distribucion"],
            "cover": cfg["EA_Recubrimiento_cm"] or str(rc.default_cover_cm(t.name)),
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
        self.selected = None
        self._fill_view_columns(t)
        self.plan_nav.reset()
        self.elev_nav.reset()
        self.scene._extent = None  # refit the 3D camera to the new column
        self._update_measures()
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
        for combo, key in ((self.cbo_conf, "conf"), (self.cbo_edge, "edge")):
            combo.SelectedItem = f.get(key) if f.get(key) in STIRRUP_DIAMETERS else u'3/8"'
        self.txt_conf_dist.Text = f.get("conf_dist") or u""
        self.txt_edge_dist.Text = f.get("edge_dist") or u""
        self.txt_cover.Text = f.get("cover") or u""
        self.chk_nucleo.IsChecked = bool(f.get("nucleo"))
        self.txt_nucleo.Text = f.get("nucleo") or u"10"

    def _get_form(self):
        return {
            "conf": self.cbo_conf.SelectedItem or u'3/8"',
            "conf_dist": (self.txt_conf_dist.Text or u"").strip(),
            "edge": self.cbo_edge.SelectedItem or u'3/8"',
            "edge_dist": (self.txt_edge_dist.Text or u"").strip(),
            "cover": (self.txt_cover.Text or u"").strip(),
            "nucleo": (self.txt_nucleo.Text or u"").strip() if self.chk_nucleo.IsChecked else u"",
        }

    def _config_from_form(self, with_drawing):
        f = self._get_form()
        config = {
            "EA_Estribo_Conf_Diametro": f["conf"],
            "EA_Estribo_Conf_Distribucion": f["conf_dist"],
            "EA_Estribo_Borde_Diametro": f["edge"],
            "EA_Estribo_Borde_Distribucion": f["edge_dist"],
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
        has_drawing = bool(self.design["bars"] or self.design["stirrups"] or self.design["ties"])
        outside = [i + 1 for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"])
                   if self._outside_cover(kind, poly, wrap, is_open)]
        if outside:
            forms.alert(
                u"El estribo {} se sale del recubrimiento. Seleccionalo con 'Editar' y pulsa "
                u"'Aplicar' para acomodarlo dentro (o borralo).".format(
                    u", ".join(str(i) for i in outside)),
                title="Acero")
            return False
        try:
            for type_id in targets:
                # The drawing is checked only where it gets saved.
                rc.ColumnSpec(
                    self._config_from_form(type_id in same_section),
                    require_design=has_drawing and type_id in same_section,
                )
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
        if skipped and has_drawing:
            forms.alert(
                u"Configuracion de estribos guardada. El dibujo solo se guardo en los tipos "
                u"con la misma seccion; estos necesitan su propio dibujo:\n- "
                + u"\n- ".join(skipped),
                title="Acero",
            )
        return True

    # -- configuration files --------------------------------------------------
    def _file_dialog(self, dialog, file_name=u""):
        dialog.Filter = u"Configuracion de acero (*.json)|*.json"
        dialog.FileName = file_name
        folder = os.path.dirname(doc.PathName or u"")
        if folder and os.path.isdir(folder):
            dialog.InitialDirectory = folder
        return dialog.FileName if dialog.ShowDialog(self) else None

    def file_save_click(self, sender, args):
        """Save the stirrup settings and the section drawing, as they are
        now, to a file."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            forms.alert(u"Elige un tipo con seccion valida.", title="Acero")
            return
        path = self._file_dialog(SaveFileDialog(), u"Acero_{}.json".format(re.sub(r'[\\/:*?"<>|]', u"_", t.name)))
        if not path:
            return
        size = (t.section.b * rc.FT * 100, t.section.h * rc.FT * 100)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(rs.config_file_text(t.name, size, self._get_form(), self.design))
        self._status(u"Configuracion guardada en {}.".format(os.path.basename(path)), error=False)

    def file_open_click(self, sender, args):
        """Load a saved configuration into the type being edited (saved
        into the types with 'Guardar configuracion en los tipos marcados')."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            forms.alert(u"Elige primero el tipo donde cargar la configuracion.", title="Acero")
            return
        path = self._file_dialog(OpenFileDialog())
        if not path:
            return
        try:
            with io.open(path, encoding="utf-8") as f:
                name, (b, h), form, design = rs.read_config_file(f.read())
        except (IOError, rs.SpecError) as e:
            forms.alert(u"No se pudo abrir {}: {}".format(os.path.basename(path), e), title="Acero")
            return
        tb, th = t.section.b * rc.FT * 100, t.section.h * rc.FT * 100
        if design is not None and (abs(b - tb) > 0.5 or abs(h - th) > 0.5):
            if not forms.alert(
                u"El archivo es de {} ({:.0f} x {:.0f} cm) y este tipo es de {:.0f} x {:.0f} cm: "
                u"su dibujo no encaja en esta seccion.\n\nCargar solo los estribos "
                u"(diametros, distribucion, recubrimiento y nucleo)?".format(name, b, h, tb, th),
                title="Acero", yes=True, no=True):
                return
            design = None
        self._push_undo()
        self._set_form(form)
        if design is not None:
            self.design = design
        self.dirty = True
        self.draft = []
        self._select(None)
        self._status(u"Cargada la configuracion de {}{}. Pulsa 'Guardar configuracion en los tipos "
                     u"marcados' para guardarla en el tipo.".format(
                         name, u"" if design is not None else u" (solo estribos)"), error=False)
        self.redraw()

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
        # The columns picked in the model, or else every column of the
        # checked types.
        if not self.state.picked_ids and not self.checked_ids():
            forms.alert(u"Selecciona columnas en el modelo o marca los tipos a generar.", title="Acero")
            return
        self._leave("run")

    def _leave(self, action):
        self._keep_draft()
        self.state.checked = set(self.checked_ids())
        self.state.form = self._get_form()
        self.state.scope = "pick" if self.state.picked_ids else "model"
        self.action = action
        self.Close()

    def _refresh_picked(self):
        n = len(self.state.picked_ids)
        self.txt_picked.Text = (
            u"{} columna(s) seleccionada(s) en el modelo.".format(n) if n
            else u"No hay columnas seleccionadas en el modelo."
        )

    def window_key(self, sender, args):
        """Esc cancels the stroke being drawn instead of closing the window;
        Supr deletes the selected stirrup (unless a box is being typed in)."""
        if args.Key == Key.Escape and getattr(self, "draft", None):
            self.draft = []
            self._status()
            self.redraw()
            args.Handled = True
        elif (args.Key == Key.Delete and getattr(self, "selected", None) is not None
              and not isinstance(Keyboard.FocusedElement, TextBox)):
            self.delete_stirrup_click(sender, args)
            args.Handled = True

    def delete_stirrup_click(self, sender, args):
        """Delete the selected stirrup, to draw it again."""
        if self.selected is None or self.selected >= len(self.design["stirrups"]):
            return
        self._push_undo()
        rs.remove_item(self.design, "stirrups", self.selected)
        self.draft = []
        self._select(None)
        self._status(u"Estribo eliminado (Deshacer lo recupera).")
        self.redraw()

    # -- sketch tool and its steel ----------------------------------------
    def _tool(self):
        if self.rb_stirrup.IsChecked:
            return "stirrup"
        if self.rb_rect.IsChecked:
            return "rect"
        if self.rb_tie.IsChecked:
            return "tie"
        if any(rb.IsChecked for rb, _ in self.bar_tools):
            return "bar"
        if self.rb_edit.IsChecked:
            return "edit"
        return "erase"

    def _steel_slot(self, tool):
        """Which remembered family a tool uses: both stirrup tools share
        one; Editar shows the selected stirrup's own family."""
        return {"stirrup": "stirrup", "rect": "stirrup", "tie": "tie"}.get(tool)

    def _refresh_steel(self):
        """Fill "Ø acero" for the current tool: the two stirrup families
        with the diameters set in "2. Configuracion de estribos" (a bar
        shows the diameter chosen in "Barras", read only)."""
        tool = self._tool()
        self._updating_steel = True
        try:
            editing = tool == "edit" and self.selected is not None
            if self._steel_slot(tool) or editing:
                self.cbo_steel.ItemsSource = List[str]([
                    u"Confinamiento Ø{}".format(self.cbo_conf.SelectedItem or u'3/8"'),
                    u"Borde Ø{}".format(self.cbo_edge.SelectedItem or u'3/8"'),
                ])
                kind = (self.design["stirrups"][self.selected][0] if editing
                        else self.kind_for[self._steel_slot(tool)])
                self.cbo_steel.SelectedIndex = 1 if kind == rs.KIND_EDGE else 0
                self.cbo_steel.IsEnabled = True
            elif tool == "bar":
                self.cbo_steel.ItemsSource = List[str]([u"Barra Ø" + self.bar_key])
                self.cbo_steel.SelectedIndex = 0
                self.cbo_steel.IsEnabled = False
            else:
                self.cbo_steel.ItemsSource = None
                self.cbo_steel.IsEnabled = False
        finally:
            self._updating_steel = False

    def tool_changed(self, sender, args):
        if not hasattr(self, "kind_for"):
            return  # fired while the XAML loads
        self.draft = []
        self._status()  # the previous tool's hint ("...para la grapa") no longer applies
        for rb, cbo in self.bar_tools:
            if rb.IsChecked and cbo.SelectedItem:
                self.bar_key = cbo.SelectedItem
        if self._tool() not in ("edit",):
            self.selected = None if self._tool() == "erase" else self.selected
        self._refresh_steel()
        self.redraw()

    def bar_slot_changed(self, sender, args):
        """A diameter chosen in one of the four "Barras" options: it is
        remembered, and that option becomes the bar tool."""
        if getattr(self, "_filling_bars", True):
            return
        save_bar_slots([cbo.SelectedItem for _, cbo in self.bar_tools])
        for rb, cbo in self.bar_tools:
            if cbo is not sender or not cbo.SelectedItem:
                continue
            if rb.IsChecked:
                self.bar_key = cbo.SelectedItem
                self._refresh_steel()
            else:
                rb.IsChecked = True  # tool_changed takes its diameter

    def steel_changed(self, sender, args):
        if getattr(self, "_updating_steel", True) or self.cbo_steel.SelectedIndex < 0:
            return
        tool = self._tool()
        kind = rs.KIND_EDGE if self.cbo_steel.SelectedIndex == 1 else rs.KIND_CONFINEMENT
        if tool == "edit" and self.selected is not None:
            _, poly, wrap, is_open = self.design["stirrups"][self.selected]
            if self.design["stirrups"][self.selected][0] != kind:
                self._push_undo()
                self.design["stirrups"][self.selected] = (kind, poly, wrap, is_open)
                self.redraw()
        elif self._steel_slot(tool):
            self.kind_for[self._steel_slot(tool)] = kind

    def stirrup_diameter_changed(self, sender, args):
        if not hasattr(self, "kind_for"):
            return
        self._refresh_steel()
        self._update_measures()  # outer measures depend on the diameter
        self.redraw()

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
        fitted = (scale, width / 2.0 - cx * scale, height / 2.0 + cy * scale)
        self._plan_fitted = fitted
        scale, ox, oy = self.plan_nav.resolve(fitted)
        return section, scale, ox, oy

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
        # Shapes never take the mouse: clicks go straight to the canvas,
        # and a shape redrawn under a still cursor can't set off another
        # MouseMove (a redraw loop that swallowed the clicks).
        shape.IsHitTestVisible = False
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

    def _family_key(self, kind):
        combo = self.cbo_edge if kind == rs.KIND_EDGE else self.cbo_conf
        return combo.SelectedItem or u'3/8"'

    def _label(self, frame, x, y, text, dx=0, dy=0):
        """Measure text centered on a section point (meters), shifted by
        (dx, dy) pixels."""
        tb = TextBlock()
        tb.Text = text
        tb.FontSize = 11
        tb.Foreground = C_DRAFT
        tb.Background = C_LABEL_BG
        tb.Measure(Size(1e4, 1e4))
        q = self.to_px(frame, x, y)
        Canvas.SetLeft(tb, q.X + dx - tb.DesiredSize.Width / 2.0)
        Canvas.SetTop(tb, q.Y + dy - tb.DesiredSize.Height / 2.0)
        return self._add(tb)

    def _side_labels(self, frame, outline, closed=True):
        """Length (cm) of each side of a stirrup's outer face, written just
        outside it; a rectangle shows only its width and its height."""
        if closed:
            pts = rs.counterclockwise(outline)
            sign = 1.0
        else:
            pts = list(outline)
            sign = 1.0 if len(pts) < 3 or rs.polygon_signed_area(pts) > 0 else -1.0
        n = len(pts)
        sides = range(n if closed else n - 1)
        numbered = True  # T1, T2... like the boxes of the measures panel
        if closed and rs.rect_measures(pts, frame[0].polygon_m) is not None:
            numbered = False
            horizontal = [k for k in range(n) if abs(pts[k][1] - pts[(k + 1) % n][1]) < 1e-6]
            vertical = [k for k in range(n) if abs(pts[k][0] - pts[(k + 1) % n][0]) < 1e-6]
            sides = horizontal[:1] + vertical[:1]
        for k in sides:
            (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % n]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if length < 1e-6:
                continue
            nx, ny = sign * (y2 - y1) / length, -sign * (x2 - x1) / length  # outward
            text = u"{:.1f}".format(length * 100)
            if numbered:
                text = u"T{} {}".format(k + 1, text)
            self._label(frame, (x1 + x2) / 2.0, (y1 + y2) / 2.0, text,
                        dx=nx * 22, dy=-ny * 22)

    def redraw(self):
        self.canvas.Children.Clear()
        frame = self._frame()
        if frame is None:
            return
        section, scale = frame[0], frame[1]
        bars = self.design["bars"]
        self._polyline(frame, section.polygon_m, C_OUTLINE, 2, closed=True, fill=C_CONCRETE)
        try:
            cover = float((self.txt_cover.Text or u"4").replace(u",", u".")) / 100.0
            self._polyline(frame, rs.offset_polygon_outward(section.polygon_m, -cover), C_COVER, 1,
                           closed=True, dash=True)
        except Exception:
            pass

        def color(kind):
            # Edge ("borde") steel in orange, confinement in green.
            return C_STIRRUP if kind == rs.KIND_EDGE else C_CONFINEMENT

        for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"]):
            key = self._family_key(kind)
            try:
                line = rs.stirrup_centerline(poly, bars, key, wrap, is_open)
            except rs.SpecError:
                line = poly
            thickness = max(2, rs.BAR_DIAMETERS_MM[key] / 1000.0 * scale)
            self._polyline(frame, line, color(kind), thickness, closed=not is_open)
            if not is_open:
                self._hook(frame, line, key, color(kind), thickness)
            if i == self.selected:
                try:
                    outline = rs.stirrup_outline(poly, bars, key, wrap, is_open)
                    self._polyline(frame, outline, C_DRAFT, 1.5, closed=not is_open, dash=True)
                    self._side_labels(frame, outline, closed=not is_open)
                except rs.SpecError:
                    pass
        for kind, a, b in self.design["ties"]:
            key = self._family_key(kind)
            try:
                a2, b2 = rs.tie_centerline(a, b, bars, key)
            except rs.SpecError:
                a2, b2 = a, b
            self._polyline(frame, [a2, b2], color(kind), max(2, rs.BAR_DIAMETERS_MM[key] / 1000.0 * scale))
        for x, y, key in bars:
            self._dot(frame, x, y, max(3.5, rs.BAR_DIAMETERS_MM[key] / 2000.0 * scale), C_BAR)

        tool = self._tool()
        if tool == "tie" and self.cursor_m:
            # Preview of the tie a click here would place.
            try:
                a, b = rs.auto_tie(self.cursor_m, bars)
                self._polyline(frame, [a, b], C_DRAFT, 2, dash=True)
            except rs.SpecError:
                pass
        if tool == "rect" and self.draft and self.cursor_m:
            # Rectangle between the first corner and the cursor, with the
            # outer measures it would have.
            rect = self._fitted_rect(self.draft[0], self.cursor_m)
            if rect:
                self._polyline(frame, rect, C_DRAFT, 2, closed=True, dash=True)
                key = self._family_key(self.kind_for["stirrup"])
                try:
                    self._side_labels(frame, rs.stirrup_outline(rect, bars, key))
                except rs.SpecError:
                    pass
        elif self.draft:
            pts = list(self.draft) + ([self.cursor_m] if self.cursor_m else [])
            self._polyline(frame, pts, C_DRAFT, 2)
        for x, y in self.draft:
            self._dot(frame, x, y, 4, C_DRAFT)
        if self.cursor_m:
            refused = tool in ("stirrup", "rect") and self._accept_vertex(
                self.cursor_m, self.cursor_on_bar) is None
            self._dot(frame, self.cursor_m[0], self.cursor_m[1], 4 if refused else 3,
                      C_REFUSED if refused else C_CURSOR)
        signature = (self.state.active, tuple(id_of(c.Id) for c in self._view_columns),
                     rs.design_to_text(self.design),
                     tuple(sorted(self._get_form().items())))
        if signature != self._views_sig:
            self._views_sig = signature
            self._refresh_views()

    def _hook(self, frame, line, key, color, thickness):
        """The 135-degree hook of a closed stirrup at its first corner (where
        Revit puts it): a short leg into the core along the bisector."""
        pts = rs.counterclockwise(line)
        if len(pts) < 3:
            return
        c, nxt, prv = pts[0], pts[1], pts[-1]
        u = [nxt[0] - c[0], nxt[1] - c[1]]
        v = [prv[0] - c[0], prv[1] - c[1]]
        lu, lv = (u[0] ** 2 + u[1] ** 2) ** 0.5, (v[0] ** 2 + v[1] ** 2) ** 0.5
        if lu < 1e-9 or lv < 1e-9:
            return
        bx, by = u[0] / lu + v[0] / lv, u[1] / lu + v[1] / lv
        lb = (bx ** 2 + by ** 2) ** 0.5
        if lb < 1e-9:
            return
        length = max(6 * rs.BAR_DIAMETERS_MM[key] / 1000.0, 0.075)
        end = (c[0] + bx / lb * length, c[1] + by / lb * length)
        self._polyline(frame, [c, end], color, thickness)

    @staticmethod
    def _rect(a, b):
        """Axis-aligned rectangle with opposite corners a and b (None if
        it would be a line)."""
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            return None
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def _cover_bounds(self):
        """Where a rectangular stirrup's outer face must stay (the section
        faces moved in by the cover), or None for irregular sections."""
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        if section is None or not section.is_rectangle:
            return None
        try:
            cover = float((self.txt_cover.Text or u"").replace(u",", u".")) / 100.0
        except ValueError:
            return None
        return rs.cover_bounds(section.polygon_m, cover)

    def _cover_m(self):
        try:
            return float((self.txt_cover.Text or u"").replace(u",", u".")) / 100.0
        except ValueError:
            return None

    def _status(self, text=u"", error=True):
        """Warning line over the drawing (hidden when empty); a hint (not
        an error) in blue."""
        self.txt_status.Text = text
        self.txt_status.Foreground = C_REFUSED if error else C_DRAFT
        self.txt_status.Visibility = Visibility.Visible if text else Visibility.Collapsed

    def _accept_vertex(self, point, on_bar):
        """The clicked stirrup corner, moved onto the cover limit when just
        inside it; None when the stirrup would leave the cover there."""
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if section is None or cover is None:
            return point
        radius = 0.0
        if on_bar:
            for x, y, key in self.design["bars"]:
                if abs(x - point[0]) < 1e-6 and abs(y - point[1]) < 1e-6:
                    radius = rs.BAR_DIAMETERS_MM[key] / 2000.0
        key = self._family_key(self.kind_for["stirrup"])
        # Two-point rectangles pull their corners onto the cover from 2.5
        # cm; the Estribo tool only from 1 cm, so small jogs survive.
        snap = rs.COVER_SNAP if self._tool() == "rect" else rs.FREE_CORNER_SNAP
        corner = rs.fit_vertex(point, section.polygon_m, cover, key, radius,
                               rectangular=section.is_rectangle, snap=snap)
        if corner is not None and not on_bar and section.is_rectangle:
            # The whole stirrup is pushed out by the radius of the bars it
            # wraps: a free corner must leave room for that too.
            wrap = rs.wrap_radius(list(self.draft), self.design["bars"])
            if wrap:
                corner = rs.fit_vertex(point, section.polygon_m, cover, key, wrap, rectangular=True,
                                       snap=snap)
                if corner is not None:
                    corner = self._clamp_free(corner, wrap, key, section, cover, snap)
        return corner

    def _clamp_free(self, point, wrap, key, section, cover, snap=rs.FREE_CORNER_SNAP):
        """Keep a free (not on a bar) corner of a stirrup that wraps bars of
        radius `wrap` inside the cover, snapping it onto the limit when it
        is just inside (rectangular sections)."""
        x0, y0, x1, y1 = rs.cover_bounds(section.polygon_m, cover + rs.BAR_DIAMETERS_MM[key] / 1000.0 + wrap)
        x = min(max(point[0], x0), x1)
        y = min(max(point[1], y0), y1)
        x = x0 if x - x0 <= snap else (x1 if x1 - x <= snap else x)
        y = y0 if y - y0 <= snap else (y1 if y1 - y <= snap else y)
        return (x, y)

    def _outside_cover(self, kind, poly, wrap, is_open=False):
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if section is None or cover is None:
            return False
        try:
            return not rs.stirrup_inside_cover(poly, self.design["bars"], self._family_key(kind),
                                               wrap, section.polygon_m, cover, is_open=is_open)
        except rs.SpecError:
            return True

    def _fitted_rect(self, a, b):
        """Two-point stirrup between corners a and b, with its sides that
        reach (or nearly reach) the cover line placed on it."""
        rect = self._rect(a, b)
        bounds = self._cover_bounds()
        if rect is None or bounds is None:
            return rect
        bars = self.design["bars"]
        key = self._family_key(self.kind_for["stirrup"])
        wrap = rs.wrap_radius(rect, bars)
        try:
            outer = rs.snap_rect_to_cover(rs.outer_rect(rect, bars, key, wrap), bounds)
            fitted = rs.rect_vertices(outer, key, wrap)
        except rs.SpecError:
            return rect
        # Keep the corners on the bars they were snapped to, if any.
        return fitted if wrap == rs.wrap_radius(fitted, bars) or wrap == 0 else rect

    # -- measures of the selected stirrup ------------------------------------
    def _select(self, index):
        self.selected = index
        self._refresh_steel()
        self._update_measures()

    def _update_measures(self):
        """Show (or hide) the outer measures of the selected stirrup. Only
        called when the selection or the drawing changes, never on a mouse
        move, so it can't overwrite what is being typed."""
        stirrups = self.design["stirrups"]
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        if self.selected is None or self.selected >= len(stirrups) or section is None:
            self.selected = None
            self.panel_measures.Visibility = Visibility.Collapsed
            return
        kind, poly, wrap, is_open = stirrups[self.selected]
        try:
            outline = rs.stirrup_outline(poly, self.design["bars"], self._family_key(kind), wrap, is_open)
        except rs.SpecError:
            self.panel_measures.Visibility = Visibility.Collapsed
            return
        self.panel_measures.Visibility = Visibility.Visible
        shape = rs.design_shapes(self.design, "stirrups")[self.selected]
        self.txt_measures_title.Text = u"Estribo {}de {}{} seleccionado - medidas exteriores (cm):".format(
            u"abierto " if is_open else u"", u"borde" if kind == rs.KIND_EDGE else u"confinamiento",
            u" ({})".format(shape) if shape else u"")
        measures = None if is_open else rs.rect_measures(outline, section.polygon_m)
        self._fill_segment_boxes(rs.side_lengths(outline, closed=False) if is_open else [])
        if measures:
            self.panel_rect_fields.Visibility = Visibility.Visible
            for box, value in zip((self.txt_m_width, self.txt_m_height), measures[:2]):
                box.Text = u"{:.1f}".format(value * 100)
            self.txt_m_sides.Text = u""
        else:
            self.panel_rect_fields.Visibility = Visibility.Collapsed
            # Open stirrups edit their segments in boxes; other shapes
            # just list their sides.
            self.txt_m_sides.Text = u"" if is_open else u"Lados:  " + u"   ".join(
                u"T{} {:.1f}".format(k, v * 100)
                for k, v in enumerate(rs.side_lengths(rs.counterclockwise(outline)), 1))

    def _fill_segment_boxes(self, lengths):
        """One editable box per segment of an open stirrup (outer face, cm)."""
        self.panel_segments.Children.Clear()
        self.segment_boxes = []
        if not lengths:
            return
        for number, value in enumerate(lengths, 1):
            label = TextBlock()
            label.Text = u"T{} ".format(number)
            label.FontWeight = FontWeights.Bold
            label.Foreground = C_DRAFT
            label.VerticalAlignment = VerticalAlignment.Center
            self.panel_segments.Children.Add(label)
            box = TextBox()
            box.Width = 46
            box.Margin = Thickness(0, 0, 4, 0)
            box.Text = u"{:.1f}".format(value * 100)
            box.KeyDown += self.segment_key
            self.panel_segments.Children.Add(box)
            self.segment_boxes.append(box)
        button = Button()
        button.Content = u"Aplicar"
        button.Width = 65
        button.Margin = Thickness(4, 0, 0, 0)
        button.Click += self.segments_apply
        self.panel_segments.Children.Add(button)

    def segment_key(self, sender, args):
        if args.Key == Key.Enter:
            self.segments_apply(sender, args)

    def segments_apply(self, sender, args):
        """Resize the open stirrup's segments to the typed outer lengths."""
        if self.selected is None:
            return
        kind, poly, wrap, is_open = self.design["stirrups"][self.selected]
        if not is_open:
            return
        key = self._family_key(kind)
        if wrap is None:
            wrap = rs.wrap_radius(poly, self.design["bars"])
        try:
            current = rs.side_lengths(rs.stirrup_outline(poly, self.design["bars"], key, wrap, True),
                                      closed=False)
            wanted = [float((box.Text or u"").replace(u",", u".")) / 100.0 for box in self.segment_boxes]
            if len(wanted) != len(current) or min(wanted) <= 0:
                raise ValueError()
            pts = list(poly)
            for index, (now, new) in enumerate(zip(current, wanted)):
                if abs(new - now) > 0.0005:
                    pts = rs.resize_segment(pts, index, new - now)
        except ValueError:
            forms.alert(u"Escribe los tramos en cm (ej. 7.5).", title="Acero")
            return
        except rs.SpecError as e:
            forms.alert(u"{}".format(e), title="Acero")
            return
        if self._outside_cover(kind, pts, wrap, True):
            self._status(u"Con esas medidas el estribo se sale del recubrimiento; no se aplicaron.")
            return
        self._status()
        self._push_undo()
        self.design["stirrups"][self.selected] = (kind, pts, wrap, True)
        self.redraw()
        self._update_measures()

    def measures_apply(self, sender, args):
        t = self.by_id.get(self.state.active)
        if self.selected is None or t is None or t.section is None:
            return
        kind, poly, wrap, is_open = self.design["stirrups"][self.selected]
        if is_open:
            return  # open stirrups: edit them by redrawing
        if wrap is None:
            wrap = rs.wrap_radius(poly, self.design["bars"])
        key = self._family_key(kind)
        try:
            width, height = [float((box.Text or u"").replace(u",", u".")) / 100.0
                             for box in (self.txt_m_width, self.txt_m_height)]
            if width <= 0 or height <= 0:
                raise ValueError()
            outer = rs.outer_rect(poly, self.design["bars"], key, wrap)
            bounds = self._cover_bounds()
            if bounds:
                # Around its own center, shifted to stay inside the cover.
                outer = rs.resize_rect_in_cover(outer, width, height, bounds)
            else:
                cx, cy = (outer[0] + outer[2]) / 2.0, (outer[1] + outer[3]) / 2.0
                outer = (cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)
            new_poly = rs.rect_vertices(outer, key, wrap)
        except ValueError:
            forms.alert(u"Escribe las medidas en cm (ej. 22 o 22.5).", title="Acero")
            return
        except rs.SpecError as e:
            forms.alert(u"{}".format(e), title="Acero")
            return
        self._push_undo()
        self.design["stirrups"][self.selected] = (kind, new_poly, wrap, False)
        self.redraw()
        self._update_measures()

    def measure_key(self, sender, args):
        if args.Key == Key.Enter:
            self.measures_apply(sender, args)

    def _push_undo(self):
        self.undo_stack.append(rs.design_to_text(self.design))
        self.dirty = True

    # -- zoom / pan of the plan ---------------------------------------------
    def plan_wheel(self, sender, args):
        if self._frame() is None:
            return
        self.plan_nav.wheel(self._plan_fitted, args.GetPosition(self.canvas), args.Delta)
        self.redraw()
        args.Handled = True

    def plan_mouse_down(self, sender, args):
        if args.ChangedButton == MouseButton.Middle and self._frame() is not None:
            self.plan_nav.start_pan(self._plan_fitted, args.GetPosition(self.canvas))
            self.canvas.CaptureMouse()

    def plan_mouse_up(self, sender, args):
        if args.ChangedButton == MouseButton.Middle:
            self.plan_nav.end_pan()
            self.canvas.ReleaseMouseCapture()

    def plan_fit_click(self, sender, args):
        self.plan_nav.reset()
        self.redraw()

    # -- elevation and 3D views ----------------------------------------------
    def _column_info(self, column, fallback):
        """(section, clear height m, neighbors) of one column, cached."""
        key = id_of(column.Id)
        if key not in self._section_cache:
            try:
                self._section_cache[key] = rc.Section(column)
            except Exception:
                self._section_cache[key] = fallback
        section = self._section_cache[key]
        if key not in self._clear_cache:
            try:
                top = rc.clear_top(doc, column, section)
            except Exception:
                top = section.z_top
            self._clear_cache[key] = (top - section.z_bottom) * rc.FT
        if key not in self._neighbor_cache:
            try:
                self._neighbor_cache[key] = rc.column_neighbors(doc, column, section)
            except Exception:
                self._neighbor_cache[key] = []
        return section, self._clear_cache[key], self._neighbor_cache[key]

    def _fill_view_columns(self, t):
        """List the type's columns for the 3D view / elevation, each with
        what it touches; with two or more of them picked in the model, first
        an entry showing those stacked. First choice: the picked ones (all
        of them), else the first one a beam frames into, else the first."""
        self._filling_columns = True
        try:
            labels, options = [], []
            beam_at = None
            picked = set(self.state.picked_ids)
            stack = sorted([c for c in t.columns if id_of(c.Id) in picked],
                           key=lambda c: self._column_info(c, t.section)[0].z_bottom
                           if t.section is not None else 0)
            if len(stack) > 1:
                labels.append(u"Las {} seleccionadas, apiladas ({})".format(
                    len(stack), u", ".join(self._level_name(c) for c in stack)))
                options.append(stack)
            for column in t.columns:
                kinds = []
                if t.section is not None:
                    for n in self._column_info(column, t.section)[2]:
                        name = n["label"].lower()
                        if name not in kinds:
                            kinds.append(name)
                labels.append(u"{} - {}{}".format(
                    id_of(column.Id), self._level_name(column),
                    u"  ({})".format(u", ".join(kinds)) if kinds else u""))
                options.append([column])
                if beam_at is None and u"viga" in kinds:
                    beam_at = len(options) - 1
            if stack:
                default = 0 if len(stack) > 1 else options.index([stack[0]])
            else:
                default = beam_at or 0
            self._view_options = options
            self.cbo_view_column.ItemsSource = List[str](labels)
            self.cbo_view_column.SelectedIndex = default
            self._view_columns = options[default]
        finally:
            self._filling_columns = False

    def _level_name(self, column):
        level = doc.GetElement(column.LevelId)
        return level.Name if level else u"sin nivel"

    def view_column_changed(self, sender, args):
        if getattr(self, "_filling_columns", True):
            return
        index = self.cbo_view_column.SelectedIndex
        options = getattr(self, "_view_options", [])
        if index < 0 or index >= len(options):
            return
        self._view_columns = options[index]
        self.elev_nav.reset()
        self.scene._extent = None
        self._views_sig = None  # force the views to redraw for this column
        self.redraw()

    def config_changed(self, sender, args):
        if hasattr(self, "_views_sig"):  # also fired while the XAML loads
            self.redraw()

    def _family_view(self, key_text, dist_text, clear):
        """{"key", "zones", "rest", "tagged"} of a stirrup family for the
        views, or None (message) when its settings don't read."""
        if not (dist_text or u"").strip():
            return None, None
        try:
            key = rs.parse_diameter(key_text)
            zones, rest = rs.parse_distribution(dist_text)
        except rs.SpecError as e:
            return None, u"{}".format(e)
        return {"key": key, "zones": zones, "rest": rest,
                "tagged": rs.stirrup_zone_positions(clear, zones, rest)}, None

    def _views_data(self):
        """(elevation data, 3D data) of the active type as configured and
        drawn right now (unsaved changes included)."""
        t = self.by_id.get(self.state.active)
        if t is None or t.section is None:
            return None, None
        # The column(s) chosen in "Columna:" (each has its own height and
        # its own beams, slab and footing); several are drawn stacked, in
        # the frame of the lowest one.
        columns = [c for c in self._view_columns if c in t.columns] or t.columns[:1]
        f = self._get_form()
        bars = self.design["bars"]
        base = self._column_info(columns[0], t.section)[0]
        to_base = base.transform.Inverse
        segments, neighbors, loops = [], [], []
        messages = []
        for index, column in enumerate(columns):
            section, clear, touching = self._column_info(column, t.section)
            height = (section.z_top - section.z_bottom) * rc.FT
            origin = to_base.OfPoint(section.point_m(0.0, 0.0, section.z_bottom))
            dx = (origin.X - base.center[0]) * rc.FT
            dy = (origin.Y - base.center[1]) * rc.FT
            dz = (section.z_bottom - base.z_bottom) * rc.FT
            edge, edge_msg = self._family_view(f["edge"], f["edge_dist"], clear)
            conf, conf_msg = self._family_view(f["conf"], f["conf_dist"], clear)
            joint = []
            if f["nucleo"] and height - clear > 0.1:
                try:
                    joint = rs.joint_positions(height - clear, float(f["nucleo"].replace(u",", u".")) / 100.0)
                except ValueError:
                    pass
            messages += [m for m in (edge_msg and u"Borde: " + edge_msg,
                                     conf_msg and u"Confinamiento: " + conf_msg) if m]
            if edge is None and not edge_msg:
                messages.append(u"Falta la distribucion del estribo de borde")
            segments.append({"x": dx, "y": dy, "z": dz, "height": height, "clear": clear,
                             "edge": edge, "conf": conf, "joint": joint})
            for n in touching:
                x0, y0, z0, x1, y1, z1 = n["box"]
                neighbors.append({
                    "label": n["label"], "seg": index,
                    "box": (x0 + dx, y0 + dy, z0 + dz, x1 + dx, y1 + dy, z1 + dz),
                    "triangles": [tuple((p[0] + dx, p[1] + dy, p[2] + dz) for p in tri)
                                  for tri in n["triangles"]],
                })
            families = {rs.KIND_EDGE: edge, rs.KIND_CONFINEMENT: conf}
            drawn = [(kind, poly, wrap, is_open) for kind, poly, wrap, is_open in self.design["stirrups"]]
            drawn += [(kind, [a, b], None, None) for kind, a, b in self.design["ties"]]
            drawn = [item for item in drawn if families.get(item[0]) is not None]
            # stacked like they are generated (see rc.generate_column)
            lifts = rs.stack_lifts([(kind, rs.BAR_DIAMETERS_MM[families[kind]["key"]] / 1000.0, is_open is None)
                                    for kind, _, _, is_open in drawn])
            for (kind, poly, wrap, is_open), lift in zip(drawn, lifts):
                family = families[kind]
                try:
                    if is_open is None:  # a tie
                        line, closed = list(rs.tie_centerline(poly[0], poly[1], bars, family["key"])), False
                    else:
                        line = rs.stirrup_centerline(poly, bars, family["key"], wrap, is_open)
                        closed = not is_open
                except rs.SpecError:
                    continue
                # stacked towards the middle, like rc._runs (down in the top half)
                zs = ([dz + z + (-lift if z > clear / 2.0 + 1e-6 else lift) for z, _ in family["tagged"]]
                      + [dz + lift + clear + j for j in joint])
                loops.append((kind, [(x + dx, y + dy) for x, y in line], closed, zs,
                              rs.BAR_DIAMETERS_MM[family["key"]] / 2000.0))
        top = max(s["z"] + s["height"] for s in segments)
        xs = [p[0] for p in base.polygon_m]
        elev = {
            "width": max(xs) - min(xs),
            "height": top,
            "bars_x": sorted(set(round(x, 3) for x, _, _ in bars)),
            "message": messages[0] if messages else None,
            "neighbors": neighbors,
            "segments": segments,
        }
        if len(segments) == 1:  # the single-column keys too
            elev.update(dict((k, segments[0][k]) for k in ("clear", "edge", "conf", "joint")))
        scene = {
            "polygon": base.polygon_m,
            "height": top,
            "bars": [(x, y, rs.BAR_DIAMETERS_MM[k] / 2000.0) for x, y, k in bars],
            "loops": loops,
            "neighbors": neighbors,
            "segments": segments,
        }
        return elev, scene

    def _refresh_views(self):
        try:
            elev, scene = self._views_data()
        except Exception as e:
            elev, scene = None, None
            self._status(u"No se pudieron dibujar el alzado y la vista 3D: {}".format(e))
        self._elev_data = elev
        self._scene_data = scene
        self._draw_elevation()
        self._build_3d()

    def _elev_fitted(self):
        width, height = self.canvas_elev.ActualWidth, self.canvas_elev.ActualHeight
        if self._elev_data is None or width < 50 or height < 50:
            return None
        return rv.fit_frame(width, height, *rv.elevation_extent(self._elev_data),
                            margins=(20, 16, 20, 16))

    def _draw_elevation(self):
        fitted = self._elev_fitted()
        if fitted is None:
            self.canvas_elev.Children.Clear()
            return
        rv.draw_elevation(self.canvas_elev, self._elev_data, self.elev_nav.resolve(fitted))

    def _build_3d(self):
        self.scene.build(getattr(self, "_scene_data", None), self.cbo_detail.SelectedItem or u"Medio")

    def elev_resized(self, sender, args):
        if hasattr(self, "_views_sig"):
            self._draw_elevation()

    def elev_wheel(self, sender, args):
        fitted = self._elev_fitted()
        if fitted is None:
            return
        self.elev_nav.wheel(fitted, args.GetPosition(self.canvas_elev), args.Delta)
        self._draw_elevation()
        args.Handled = True

    def elev_mouse_down(self, sender, args):
        fitted = self._elev_fitted()
        if fitted is not None and args.ChangedButton in (MouseButton.Middle, MouseButton.Left):
            self.elev_nav.start_pan(fitted, args.GetPosition(self.canvas_elev))
            self.canvas_elev.CaptureMouse()

    def elev_mouse_move(self, sender, args):
        if self.elev_nav.pan(args.GetPosition(self.canvas_elev)):
            self._draw_elevation()

    def elev_mouse_up(self, sender, args):
        self.elev_nav.end_pan()
        self.canvas_elev.ReleaseMouseCapture()

    def elev_fit_click(self, sender, args):
        self.elev_nav.reset()
        self._draw_elevation()

    def detail_changed(self, sender, args):
        if hasattr(self, "scene"):
            self._build_3d()

    def view3d_wheel(self, sender, args):
        self.scene.wheel(args.Delta)
        args.Handled = True

    def view3d_mouse_down(self, sender, args):
        if args.ChangedButton in (MouseButton.Left, MouseButton.Middle):
            self.scene.start_drag(args.GetPosition(self.border3d), args.ChangedButton == MouseButton.Left)
            self.border3d.CaptureMouse()

    def view3d_mouse_move(self, sender, args):
        self.scene.drag(args.GetPosition(self.border3d))

    def view3d_mouse_up(self, sender, args):
        self.scene.end_drag()
        self.border3d.ReleaseMouseCapture()

    def view3d_fit_click(self, sender, args):
        self.scene.fit()

    def view3d_resized(self, sender, args):
        if hasattr(self, "scene"):
            self.scene.fit()  # the fit depends on the viewport's shape

    def canvas_resized(self, sender, args):
        self.redraw()

    def canvas_move(self, sender, args):
        if self.plan_nav.pan(args.GetPosition(self.canvas)):
            self.redraw()
            return
        frame = self._frame()
        if frame is None:
            return
        cursor, on_bar = self.snap(frame, args.GetPosition(self.canvas))
        if self._tool() == "bar" and not on_bar:
            # preview where the bar will go (against a stirrup, lined up)
            cursor = self._place_bar(self.to_m(frame, args.GetPosition(self.canvas)))
        if cursor == self.cursor_m:
            return  # same snapped point: nothing to redraw
        self.cursor_m = cursor
        self.cursor_on_bar = on_bar
        self.redraw()

    def canvas_left(self, sender, args):
        frame = self._frame()
        if frame is None:
            return
        p = args.GetPosition(self.canvas)
        point, on_bar = self.snap(frame, p)
        tool = self._tool()
        if tool == "bar":
            if not on_bar:
                # against the stirrup, facing the bars already there
                point = self._place_bar(self.to_m(frame, p))
                self._push_undo()
                self.design["bars"].append((point[0], point[1], self.bar_key))
        elif tool in ("stirrup", "rect"):
            if tool == "stirrup" and self.draft:
                # Free corner within 1 cm of the previous corner's x or y:
                # line them up, so legs come out straight (moving the free
                # one when the other is a bar).
                px, py = self.draft[-1]
                prev_on_bar = any(abs(x - px) < 1e-6 and abs(y - py) < 1e-6
                                  for x, y, _ in self.design["bars"])
                if not on_bar:
                    point = (px if abs(point[0] - px) <= 0.01 else point[0],
                             py if abs(point[1] - py) <= 0.01 else point[1])
                elif not prev_on_bar:
                    self.draft[-1] = (point[0] if abs(point[0] - px) <= 0.01 else px,
                                      point[1] if abs(point[1] - py) <= 0.01 else py)
            corner = self._accept_vertex(point, on_bar)
            if corner is None:
                self._status(u"Fuera del recubrimiento: haz clic dentro de la linea punteada.")
            elif tool == "stirrup":
                self._status()
                if len(self.draft) >= 3 and corner == self.draft[0] and not self.rb_shape_open.IsChecked:
                    self._close_stirrup()
                elif not self.draft or corner != self.draft[-1]:
                    self.draft.append(corner)
            elif not self.draft:
                self._status()
                self.draft = [corner]
            else:
                rect = self._fitted_rect(self.draft[0], corner)
                if rect:
                    self.draft = rect
                    if not self._close_stirrup():
                        self.draft = []
        elif tool == "tie":
            self._place_tie(self.to_m(frame, p))
        elif tool == "edit":
            self._select(self._stirrup_at(frame, p))
        elif tool == "erase":
            self._erase(frame, p)
        self.redraw()

    def canvas_right(self, sender, args):
        is_open = bool(self.rb_shape_open.IsChecked)
        if self._tool() == "stirrup" and len(self.draft) >= (2 if is_open else 3):
            self._close_stirrup(is_open)
        else:
            self.draft = []
        self.redraw()

    def _close_stirrup(self, is_open=False):
        """Add the drawn stirrup (it keeps the radius of the bars it wraps,
        so editing its measures later can't shift it) and show them.
        `is_open`: a U-shaped stirrup, its ends not joined."""
        pts = list(self.draft)
        kind = self.kind_for["stirrup"]
        wrap = rs.wrap_radius(pts, self.design["bars"])
        t = self.by_id.get(self.state.active)
        section = t.section if t else None
        cover = self._cover_m()
        if wrap and section is not None and section.is_rectangle and cover is not None:
            # Free corners placed before the first wrapped bar: bring them
            # inside the limit that bar's radius sets.
            bar_points = set((round(x, 6), round(y, 6)) for x, y, _ in self.design["bars"])
            key = self._family_key(kind)
            pts = [p if (round(p[0], 6), round(p[1], 6)) in bar_points
                   else self._clamp_free(p, wrap, key, section, cover) for p in pts]
        pts = rs.clean_polyline(pts, closed=not is_open)
        if len(pts) < (2 if is_open else 3):
            self._status(u"El estribo necesita mas puntos (quedaron en linea recta).")
            return False
        if self._outside_cover(kind, pts, wrap, is_open):
            self._status(u"Ese estribo se sale del recubrimiento; corrige sus esquinas (Deshacer quita la ultima).")
            return False
        shape = self.active_shape if self.active_shape and not self.active_shape["tie"] else None
        if shape and (shape["closed"] == is_open or shape["points"] != len(pts)):
            self._status(u"La forma {} es un estribo {} de {} {}; el dibujado tiene {}. "
                         u"Corrigelo o quita la forma elegida.".format(
                             shape["name"], u"cerrado" if shape["closed"] else u"abierto",
                             shape["points"], u"esquinas" if shape["closed"] else u"puntos",
                             len(pts)))
            return False
        self._status()
        self._push_undo()
        rs.add_item(self.design, "stirrups", (kind, pts, wrap, is_open), shape["name"] if shape else None)
        self.draft = []
        self._select(len(self.design["stirrups"]) - 1)
        return True

    def _stirrup_at(self, frame, p):
        """Index of the stirrup under the click, or None: the one whose
        outline passes nearest (within SNAP_PX), else the smallest closed
        stirrup the click falls inside."""
        m = self.to_m(frame, p)
        tol = SNAP_PX / frame[1]
        near = None
        inside = None
        for i, (kind, poly, wrap, is_open) in enumerate(self.design["stirrups"]):
            try:
                outline = rs.stirrup_outline(poly, self.design["bars"], self._family_key(kind), wrap, is_open)
            except rs.SpecError:
                outline = poly
            dist = rs.distance_to_polyline if is_open else rs.distance_to_polygon
            d = min(dist(m, outline), dist(m, poly))
            if d <= tol and (near is None or d < near[0]):
                near = (d, i)
            if not is_open and rs.point_in_polygon(m, outline):
                area = abs(rs.polygon_signed_area(outline))
                if inside is None or area < inside[0]:
                    inside = (area, i)
        if near:
            return near[1]
        return inside[1] if inside else None

    def _place_tie(self, click_m):
        """One click = one crosstie between the facing bars nearest to it."""
        try:
            a, b = rs.auto_tie(click_m, self.design["bars"])
        except rs.SpecError:
            forms.alert(u"No hay dos barras enfrentadas cerca de ese punto para la grapa.",
                        title="Acero")
            return
        for _, c, d in self.design["ties"]:
            if set([c, d]) == set([a, b]):
                return  # already there
        self._push_undo()
        shape = self.active_shape
        rs.add_item(self.design, "ties", (self.kind_for["tie"], a, b),
                    shape["name"] if shape and shape["tie"] else None)

    def _erase(self, frame, p):
        """Remove the bar, tie or stirrup nearest to the click."""
        _, scale = frame[0], frame[1]
        m = self.to_m(frame, p)
        tol = SNAP_PX / scale
        candidates = []
        for i, (x, y, _) in enumerate(self.design["bars"]):
            candidates.append((((x - m[0]) ** 2 + (y - m[1]) ** 2) ** 0.5, "bars", i))
        for i, (_, a, b) in enumerate(self.design["ties"]):
            candidates.append((rs.distance_to_polygon(m, [a, b]), "ties", i))
        for i, (_, poly, _, is_open) in enumerate(self.design["stirrups"]):
            dist = rs.distance_to_polyline if is_open else rs.distance_to_polygon
            candidates.append((dist(m, poly), "stirrups", i))
        candidates = [c for c in candidates if c[0] <= tol]
        if not candidates:
            # Not on any bar, tie or edge: the stirrup the click is inside.
            index = self._stirrup_at(frame, p)
            if index is not None:
                candidates = [(0.0, "stirrups", index)]
        if candidates:
            _, kind, i = min(candidates)
            self._push_undo()
            if kind == "bars":
                del self.design[kind][i]
            else:
                rs.remove_item(self.design, kind, i)  # with its shape
            self._select(None)

    def _place_bar(self, click_m):
        """Where a new bar goes (rs.place_bar): against the inner face of
        the nearest closed stirrup (in its corner near one), lined up with
        the bars already placed."""
        stirrups = []
        for kind, poly, wrap, is_open in self.design["stirrups"]:
            if is_open:
                continue
            key = self._family_key(kind)
            try:
                stirrups.append((rs.stirrup_outline(poly, self.design["bars"], key, wrap), key))
            except rs.SpecError:
                continue
        x, y = rs.place_bar(click_m, self.bar_key, self.design["bars"], stirrups)
        return (round(x, 4), round(y, 4))

    def undo_click(self, sender, args):
        if self.draft:
            self.draft = self.draft[:-1]
        elif self.undo_stack:
            self.design = rs.design_from_text(self.undo_stack.pop()) or rs.empty_design()
            self._select(None)
        self.redraw()

    # -- rebar shape browser (right panel) ---------------------------------
    def _fill_shapes(self):
        text = (self.txt_shape_filter.Text or u"").strip().lower()
        only_stirrups = bool(self.chk_shape_stirrups.IsChecked)
        self._filling_shapes = True
        try:
            self.list_shapes.Items.Clear()
            for shape in self.rebar_shapes:
                if text and text not in shape["name"].lower():
                    continue
                if only_stirrups and not shape["stirrup"]:
                    continue
                panel = StackPanel()
                panel.Children.Add(shape_preview(shape["strokes"]))
                name = TextBlock()
                name.Text = shape["name"]
                name.HorizontalAlignment = HorizontalAlignment.Center
                panel.Children.Add(name)
                item = ListBoxItem()
                item.Content = panel
                item.Tag = shape
                self.list_shapes.Items.Add(item)
                if self.active_shape and self.active_shape["name"] == shape["name"]:
                    self.list_shapes.SelectedItem = item
        finally:
            self._filling_shapes = False

    def shape_filter_changed(self, sender, args):
        if hasattr(self, "rebar_shapes"):  # also fired while the XAML loads
            self._fill_shapes()

    def shape_load_click(self, sender, args):
        """Load rebar shape families (.rfa) without leaving Acero (Revit is
        blocked while this window is open)."""
        dialog = OpenFileDialog()
        dialog.Filter = u"Formas de armadura (*.rfa)|*.rfa"
        dialog.Multiselect = True
        if not dialog.ShowDialog(self):
            return
        loaded = 0
        with revit.Transaction("Acero - cargar formas de armadura"):
            for path in dialog.FileNames:
                try:
                    if doc.LoadFamily(path):
                        loaded += 1
                except Exception:
                    pass
        self.rebar_shapes = read_rebar_shapes()
        self._fill_shapes()
        self._status(u"Formas cargadas: {} de {}.".format(loaded, len(dialog.FileNames)))

    def shape_selected(self, sender, args):
        """A click on a shape chooses it for the next stirrups (a straight
        one, for the ties): nothing is drawn, the stirrup is drawn by hand."""
        if getattr(self, "_filling_shapes", True):
            return
        item = self.list_shapes.SelectedItem
        if item is None:
            return
        shape = item.Tag
        try:
            vertices, closed = rs.shape_outline(shape["lines"], shape["hooks"][0], shape["hooks"][1])
        except rs.SpecError:
            vertices, closed = [], False
        if len(vertices) < 2:
            self._set_active_shape(None)
            self._status(u"La forma {} no se puede usar para estribos ni grapas.".format(shape["name"]))
            return
        straight = not closed and len(vertices) == 2
        if straight and not (shape["stirrup"] or any(shape["hooks"])):
            # A straight bar without hooks (M_00) is the longitudinal bar,
            # not a tie: Revit already gives it to the vertical bars.
            self._set_active_shape(None)
            if self._tool() != "bar":
                self.rb_bar_2.IsChecked = True
            self._status(u"{} es la barra recta de las barras longitudinales (Revit ya la usa en ellas): "
                         u"elige el diametro en 'Barras' y haz clic en la planta.".format(shape["name"]),
                         error=False)
            return
        self._set_active_shape({
            "name": shape["name"],
            "closed": closed,
            "points": len(vertices),
            "tie": straight,
        })

    def shape_clear_click(self, sender, args):
        self._set_active_shape(None)

    def _set_active_shape(self, shape):
        """Make `shape` the one new stirrups/ties take and set the sketch
        up for it: a closed or open (U) stirrup, or the Grapa tool."""
        self.active_shape = shape
        self.draft = []
        if shape is None:
            self._filling_shapes = True
            self.list_shapes.SelectedItem = None
            self._filling_shapes = False
            self.txt_active_shape.Text = u"la que elija Revit"
            self.rb_shape_closed.IsEnabled = self.rb_shape_open.IsEnabled = True
            self._status()
        else:
            self.txt_active_shape.Text = shape["name"]
            if shape["tie"]:
                self.rb_tie.IsChecked = True
                how = u"haz clic entre dos barras enfrentadas para la grapa"
            else:
                (self.rb_shape_closed if shape["closed"] else self.rb_shape_open).IsChecked = True
                self.rb_shape_closed.IsEnabled = self.rb_shape_open.IsEnabled = False
                if not shape["closed"] or not self.rb_rect.IsChecked:
                    self.rb_stirrup.IsChecked = True
                how = (u"dibuja el estribo en la planta ({} {})".format(
                    shape["points"], u"esquinas" if shape["closed"] else u"puntos, clic derecho para terminar"))
            self._status(u"Forma {}: {}.".format(shape["name"], how), error=False)
        self.redraw()

    def clear_click(self, sender, args):
        self._push_undo()
        self.design = rs.empty_design()
        self.draft = []
        self._select(None)
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
        # show the type (and, in the views, the column) just picked
        state.active = id_of(doc.GetElement(DB.ElementId(state.picked_ids[0])).GetTypeId())


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
    state.active = id_of(preselected[0].GetTypeId())

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
if state.scope == "pick":
    targets = [doc.GetElement(DB.ElementId(i)) for i in state.picked_ids]
    targets = [c for c in targets if c is not None]
    scope_label = "columnas seleccionadas"
    # The type's configuration fits all its columns: offer them all, on
    # every level, not just the ones picked.
    picked_types = sorted(set(id_of(c.GetTypeId()) for c in targets))
    same_type = [c for t in picked_types for c in by_id[t].columns]
    if len(same_type) > len(targets):
        only_picked = u"Solo las columnas seleccionadas ({})".format(len(targets))
        all_levels = u"Todas las columnas de {} en todos los niveles ({})".format(
            u", ".join(by_id[t].name for t in picked_types), len(same_type))
        choice = forms.CommandSwitchWindow.show(
            [only_picked, all_levels], message=u"Columnas donde generar el acero:")
        if not choice:
            script.exit()
        if choice == all_levels:
            targets = same_type
            scope_label = u"todas las columnas de su tipo, en todos los niveles"
else:
    targets = [c for t in state.checked for c in by_id[t].columns]
    scope_label = "tipos marcados, todo el modelo"

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
rebar_shapes = rc.RebarShapes(doc)
warnings = [u"{}: sin generar - {}".format(by_id[t].name, e) for t, e in spec_errors.items()]
stick_out = {}  # type name -> columns where a stirrup/tie hook leaves the section
KINDS = (rc.LONGITUDINAL, rc.EDGE, rc.CONFINEMENT)
by_type = {}  # type name -> {"n": columns, kind: kg}
total_bars = 0

t = DB.Transaction(doc, "Acero")
t.Start()
try:
    rc.ensure_parameters(doc)
    done = []
    with forms.ProgressBar(title="Acero: {value} de {max_value} columnas") as pb:
        for i, column in enumerate(with_spec):
            ct = by_id[id_of(column.GetTypeId())]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                created = rc.generate_column(
                    doc, column, specs[ct.id], bar_types, hooks, rc.type_mark(ct.name), rebar_shapes
                )
                sub.Commit()
                done.append((column, ct.name, created))
            except Exception as e:
                sub.RollBack()
                warnings.append(u"Columna {} ({}): {}".format(id_of(column.Id), ct.name, e))
            pb.update_progress(i + 1, len(with_spec))

    doc.Regenerate()  # bar lengths are only known after a regeneration
    for column, type_name, created in done:
        kg, bars, sticks = rc.record_weight(column, created)
        if sticks:
            stick_out[type_name] = stick_out.get(type_name, 0) + 1
        agg = by_type.setdefault(type_name, dict([("n", 0)] + [(k, 0.0) for k in KINDS]))
        agg["n"] += 1
        for kind in KINDS:
            agg[kind] += kg[kind]
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
output.print_md("**Columnas armadas:** {}".format(sum(v["n"] for v in by_type.values())))
if not dry_run:
    output.print_md("**Barras creadas:** {}".format(total_bars))
skipped = len(targets) - len(with_spec)
if skipped:
    output.print_md("**Columnas sin configuracion (omitidas):** {}".format(skipped))

output.print_md(
    u"\n| Tipo | Columnas | Longitudinal (kg) | Estribo de borde (kg) "
    u"| Confinamiento y grapas (kg) | Total (kg) |"
)
output.print_md("|---|---|---|---|---|---|")
grand = dict((k, 0.0) for k in KINDS)
for type_name in sorted(by_type):
    agg = by_type[type_name]
    for kind in KINDS:
        grand[kind] += agg[kind]
    output.print_md(u"| {} | {} | {:.2f} | {:.2f} | {:.2f} | {:.2f} |".format(
        type_name, agg["n"], agg[rc.LONGITUDINAL], agg[rc.EDGE], agg[rc.CONFINEMENT],
        sum(agg[k] for k in KINDS)))
output.print_md("| **Total** | | **{:.2f}** | **{:.2f}** | **{:.2f}** | **{:.2f}** |".format(
    grand[rc.LONGITUDINAL], grand[rc.EDGE], grand[rc.CONFINEMENT], sum(grand.values())))

output.print_md(
    "\n*Longitudinales rectas de piso a piso (sin empalmes ni anclajes). "
    "Estribos en la luz libre, distribuidos desde cada extremo: desde la base hasta el "
    "fondo de la viga de mayor peralte (o la cara inferior de la losa si no hay viga); "
    "en el nucleo si se configuro.*"
)
for type_name in sorted(stick_out):
    warnings.append(
        u"{}: en {} columna(s) el gancho de algun estribo o grapa sobresale de la seccion; "
        u"conviene un gancho mas corto para ese diametro.".format(type_name, stick_out[type_name])
    )
mismatched = {}  # shape name -> columns where Revit refused it
for column_id, shape_name in rebar_shapes.mismatched:
    mismatched.setdefault(shape_name, set()).add(column_id)
for shape_name in sorted(mismatched):
    warnings.append(
        u"Forma {}: el estribo dibujado no coincide con ella en {} columna(s); "
        u"Revit uso la forma que corresponde al dibujo.".format(shape_name, len(mismatched[shape_name]))
    )
if warnings:
    output.print_md("\n### Advertencias ({})".format(len(warnings)))
    for w in warnings[:50]:
        output.print_md(u"- {}".format(w))
