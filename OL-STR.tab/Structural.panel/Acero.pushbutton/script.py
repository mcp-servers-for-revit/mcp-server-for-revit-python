# -*- coding: utf-8 -*-
"""Genera el acero de refuerzo (barras 3D) y su metrado en kg para
columnas rectangulares de concreto, a partir de una tabla de armado por
tipo de columna."""

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
import rebar_spec
import rebar_columns as rc
import utils as fw_utils

# pyRevit keeps one interpreter across clicks: reload so edits on disk
# are picked up (same as the Encofrado button).
reload(fw_utils)
reload(fw_spatial)
reload(fw_geom)
reload(fw_params)
reload(rebar_spec)
reload(rc)

import clr

clr.AddReference("System.Data")
from System.Data import DataTable
from System.Windows.Controls import DataGridEditingUnit

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List

output = script.get_output()
doc = revit.doc

COLUMNS_BIC = DB.BuiltInCategory.OST_StructuralColumns
TABLE_FIELDS = ("Longitudinal", "Estribo", "Distribucion", "Recubrimiento")


def all_columns():
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(COLUMNS_BIC)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def build_type_table(columns):
    """One row per column type in use, prefilled from its type params."""
    counts = {}
    for c in columns:
        key = fw_utils.element_id_value(c.GetTypeId())
        counts[key] = counts.get(key, 0) + 1
    table = DataTable("tipos")
    for name in ("TypeId", "Tipo", "Cantidad") + TABLE_FIELDS:
        table.Columns.Add(name)
    rows = []
    for type_id, count in counts.items():
        column_type = doc.GetElement(DB.ElementId(type_id))
        type_name = rc.element_name(column_type)
        longitudinal, stirrup, distribution, cover = rc.read_type_spec(column_type)
        rows.append((type_name, type_id, count, longitudinal, stirrup, distribution,
                     cover or str(rc.default_cover_cm(type_name))))
    for type_name, type_id, count, lon, sti, dis, cov in sorted(rows):
        row = table.NewRow()
        row["TypeId"] = str(type_id)
        row["Tipo"] = type_name
        row["Cantidad"] = str(count)
        row["Longitudinal"] = lon
        row["Estribo"] = sti
        row["Distribucion"] = dis
        row["Recubrimiento"] = cov
        table.Rows.Add(row)
    return table


class AceroWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, table):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.confirmed = False
        self.table = table
        self.grid_types.ItemsSource = table.DefaultView

        self.levels = sorted(
            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(),
            key=lambda lv: lv.ProjectElevation,
        )
        self.cbo_scope_level.ItemsSource = List[str]([lv.Name for lv in self.levels])
        view_level = getattr(doc.ActiveView, "GenLevel", None)
        for idx, lv in enumerate(self.levels):
            if view_level is not None and lv.Id == view_level.Id:
                self.cbo_scope_level.SelectedIndex = idx
                break
        self.scope = "model"
        self.scope_level = None
        self.specs = {}  # type id -> rc.ColumnSpec
        self.values = {}  # type id -> (lon, sti, dis, cov) to save

    def continue_click(self, sender, args):
        self.grid_types.CommitEdit(DataGridEditingUnit.Row, True)
        errors = []
        specs = {}
        values = {}
        for row in self.table.Rows:
            type_id = int(row["TypeId"])
            fields = tuple((u"{}".format(row[f]) if row[f] is not None else u"").strip()
                           for f in TABLE_FIELDS)
            values[type_id] = fields
            if not fields[0]:
                continue  # no longitudinal: type skipped
            try:
                specs[type_id] = rc.ColumnSpec(*fields)
            except rebar_spec.SpecError as e:
                errors.append(u"- {}: {}".format(row["Tipo"], e))
        if errors:
            forms.alert(
                u"Revisa el armado de estos tipos:\n\n" + u"\n".join(errors),
                title="Acero",
            )
            return
        if not specs:
            forms.alert(u"Llena el acero longitudinal de al menos un tipo.", title="Acero")
            return

        if self.rb_scope_level.IsChecked:
            if self.cbo_scope_level.SelectedIndex < 0:
                forms.alert("Elige el nivel.", title="Acero")
                return
            self.scope = "level"
            self.scope_level = self.levels[self.cbo_scope_level.SelectedIndex]
        elif self.rb_scope_pick.IsChecked:
            self.scope = "pick"
        else:
            self.scope = "model"

        self.specs = specs
        self.values = values
        self.confirmed = True
        self.Close()


class _ColumnFilter(ISelectionFilter):
    def AllowElement(self, element):
        category = element.Category
        return category is not None and fw_utils.element_id_value(category.Id) == int(COLUMNS_BIC)

    def AllowReference(self, reference, point):
        return False


def picked_columns():
    uidoc = revit.uidoc
    picked = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if not picked:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, _ColumnFilter(),
                "Selecciona las columnas a armar y pulsa Finalizar",
            )
        except OperationCanceledException:
            script.exit()
        picked = [doc.GetElement(r.ElementId) for r in refs]
    column_filter = _ColumnFilter()
    return [e for e in picked if e is not None and column_filter.AllowElement(e)]


columns = all_columns()
if not columns:
    forms.alert("El modelo no tiene columnas estructurales.", title="Acero")
    script.exit()

window = AceroWindow(os.path.join(SCRIPT_DIR, "AceroForm.xaml"), build_type_table(columns))
window.ShowDialog()
if not window.confirmed:
    script.exit()

# The table is the user's input: keep it even for a preview.
with revit.Transaction("Acero - armado por tipo"):
    param_warnings = rc.ensure_parameters(doc)
    for type_id, fields in window.values.items():
        rc.write_type_spec(doc.GetElement(DB.ElementId(type_id)), fields)

if window.scope == "pick":
    targets = picked_columns()
    scope_label = "columnas seleccionadas"
elif window.scope == "level":
    level = window.scope_level
    targets = [
        c for c in columns
        if fw_geom.element_pour_level_id(c, "columns", window.levels) == level.Id
    ]
    scope_label = u"vaciado del nivel {}".format(level.Name)
else:
    targets = columns
    scope_label = "todo el modelo"

with_spec = [c for c in targets if fw_utils.element_id_value(c.GetTypeId()) in window.specs]
skipped_no_spec = len(targets) - len(with_spec)
if not with_spec:
    forms.alert(
        u"Ninguna de las {} columnas del alcance tiene armado en la tabla.".format(len(targets)),
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
warnings = list(param_warnings)
stick_out = {}  # type name -> (columns whose stirrup hooks leave the section, hook)

by_type = {}  # type name -> [columns, long kg, stirrup kg, bars]
total_bars = 0
t = DB.Transaction(doc, "Acero")
t.Start()
try:
    done = []
    with forms.ProgressBar(title="Acero: {value} de {max_value} columnas") as pb:
        for i, column in enumerate(with_spec):
            type_name = rc.element_name(doc.GetElement(column.GetTypeId()))
            column_spec = window.specs[fw_utils.element_id_value(column.GetTypeId())]
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                created = rc.generate_column(
                    doc, column, column_spec, bar_types, hooks, rc.type_mark(type_name)
                )
                sub.Commit()
                done.append((column, type_name, hooks.get(column_spec.stirrup_key), created))
            except Exception as e:
                sub.RollBack()
                warnings.append(u"Columna {} ({}): {}".format(
                    fw_utils.element_id_value(column.Id), type_name, e))
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
if skipped_no_spec:
    output.print_md("**Columnas sin armado en la tabla (omitidas):** {}".format(skipped_no_spec))

output.print_md("\n| Tipo | Columnas | Longitudinal (kg) | Estribos (kg) | Total (kg) |")
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
    "Estribos en la luz libre, hasta el fondo de la viga o losa superior.*"
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
