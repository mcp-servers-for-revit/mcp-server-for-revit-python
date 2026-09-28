# -*- coding: utf-8 -*-
"""Genera geometria de encofrado (paneles) y calcula cantidades para
columnas, vigas, losas, muros y cimentacion de concreto, evitando doble
conteo en caras de contacto entre elementos estructurales."""

__title__ = "Encofrado"
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
import utils as fw_utils

# pyRevit reuses one interpreter/sys.modules across button clicks, so a
# plain `import` here would silently keep serving whatever version of
# these modules was cached the first time this script ran this session -
# force a fresh read from disk every click instead.
reload(fw_utils)
reload(fw_spatial)
reload(fw_geom)
reload(fw_params)

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List

output = script.get_output()
doc = revit.doc

# (label, category_key, checkbox x:Name, material combobox x:Name) - order
# matches the rows laid out in CategoriesForm.xaml
CATEGORY_ROWS = [
    ("Columnas", "columns", "chk_columns", "cbo_columns"),
    ("Vigas", "beams", "chk_beams", "cbo_beams"),
    ("Losas", "slabs", "chk_slabs", "cbo_slabs"),
    ("Muros", "walls", "chk_walls", "cbo_walls"),
    ("Cimentacion", "foundations", "chk_foundations", "cbo_foundations"),
    ("Escaleras", "stairs", "chk_stairs", "cbo_stairs"),
]


class CategoriesWindow(forms.WPFWindow):
    """Category picker with an independent formwork-material TAG per
    category. This never filters which elements get processed - ALL
    elements of the checked categories are always included. The picked
    material (any of the project's real materials, or free text) is only
    recorded on the created panels to identify what the formwork itself
    is made of (e.g. Madera, Metalico, Aluminio).
    """

    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.confirmed = False
        self.selected_keys = []
        self.formwork_materials = {}

        try:
            material_names = sorted(
                set(
                    m.Name
                    for m in DB.FilteredElementCollector(doc)
                    .OfClass(DB.Material)
                    .ToElements()
                    if getattr(m, "Name", None)
                )
            )
        except Exception:
            material_names = []

        combo_items = List[str]([""] + material_names)
        for _, _, _, cbo_name in CATEGORY_ROWS:
            getattr(self, cbo_name).ItemsSource = combo_items

        # Ground level for "poured against soil": every level, the "NTN"
        # one preselected.
        self.levels = sorted(
            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(),
            key=lambda lv: lv.ProjectElevation,
        )
        self.cbo_ground.ItemsSource = List[str]([lv.Name for lv in self.levels])
        default_ground = fw_geom.find_ground_elevation_ft(doc)
        for idx, lv in enumerate(self.levels):
            if abs(lv.ProjectElevation - default_ground) < 1e-6:
                self.cbo_ground.SelectedIndex = idx
                break
        self.pour_against_soil = True
        self.ground_elevation_ft = default_ground

        # "Por nivel" scope: preselect the active view's level, if any.
        self.cbo_scope_level.ItemsSource = List[str]([lv.Name for lv in self.levels])
        view_level = getattr(doc.ActiveView, "GenLevel", None)
        for idx, lv in enumerate(self.levels):
            if view_level is not None and lv.Id == view_level.Id:
                self.cbo_scope_level.SelectedIndex = idx
                break
        self.scope = "model"
        self.scope_level = None

    def select_all_click(self, sender, args):
        for _, _, chk_name, _ in CATEGORY_ROWS:
            getattr(self, chk_name).IsChecked = True

    def select_none_click(self, sender, args):
        for _, _, chk_name, _ in CATEGORY_ROWS:
            getattr(self, chk_name).IsChecked = False

    def continue_click(self, sender, args):
        selected_keys = []
        formwork_materials = {}
        for _, key, chk_name, cbo_name in CATEGORY_ROWS:
            if getattr(self, chk_name).IsChecked:
                selected_keys.append(key)
                formwork_materials[key] = getattr(self, cbo_name).Text or ""

        if not selected_keys:
            forms.alert("Selecciona al menos una categoria.", title="Encofrado")
            return

        if self.rb_scope_level.IsChecked:
            if self.cbo_scope_level.SelectedIndex < 0:
                forms.alert("Elige el nivel a encofrar.", title="Encofrado")
                return
            self.scope = "level"
            self.scope_level = self.levels[self.cbo_scope_level.SelectedIndex]
        elif self.rb_scope_pick.IsChecked:
            self.scope = "pick"
        else:
            self.scope = "model"

        self.selected_keys = selected_keys
        self.formwork_materials = formwork_materials
        self.pour_against_soil = bool(self.chk_soil.IsChecked)
        if self.cbo_ground.SelectedIndex >= 0:
            self.ground_elevation_ft = self.levels[
                self.cbo_ground.SelectedIndex
            ].ProjectElevation
        self.confirmed = True
        self.Close()


xaml_path = os.path.join(SCRIPT_DIR, "CategoriesForm.xaml")
categories_window = CategoriesWindow(xaml_path)
categories_window.ShowDialog()

if not categories_window.confirmed:
    script.exit()

selected_keys = categories_window.selected_keys
formwork_materials = categories_window.formwork_materials
scope = categories_window.scope


def collect(bic):
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(bic)
        .WhereElementIsNotElementType()
        .ToElements()
    )


class _CategoryFilter(ISelectionFilter):
    """Only lets the user pick elements of the checked categories."""

    def AllowElement(self, element):
        key, _ = fw_geom.category_key_of(element)
        return key in selected_keys

    def AllowReference(self, reference, point):
        return False


def picked_elements_by_category():
    """Current selection if there is one, else ask the user to pick in the
    view; grouped by category key (stair runs/landings -> their stair)."""
    uidoc = revit.uidoc
    picked = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if not picked:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element,
                _CategoryFilter(),
                "Selecciona los elementos a encofrar y pulsa Finalizar",
            )
        except OperationCanceledException:
            script.exit()
        picked = [doc.GetElement(r.ElementId) for r in refs]
    grouped = {}
    seen = set()
    for element in picked:
        if element is None:
            continue
        key, element = fw_geom.category_key_of(element)
        if key not in selected_keys:
            continue
        element_id = fw_utils.element_id_value(element.Id)
        if element_id in seen:
            continue
        seen.add(element_id)
        grouped.setdefault(key, []).append(element)
    return grouped


if scope == "pick":
    elements_by_category = picked_elements_by_category()
    scope_label = "elementos seleccionados"
    empty_message = (
        "Ninguno de los elementos seleccionados pertenece a las categorias marcadas."
    )
else:
    elements_by_category = {}
    for key in selected_keys:
        elements_by_category[key] = collect(fw_geom.CATEGORY_MAP[key]["bic"])
    scope_label = "todo el modelo"
    empty_message = "No se encontraron elementos en las categorias seleccionadas."
    if scope == "level":
        level = categories_window.scope_level
        levels = categories_window.levels
        for key in selected_keys:
            elements_by_category[key] = [
                e
                for e in elements_by_category[key]
                if fw_geom.element_pour_level_id(e, key, levels) == level.Id
            ]
        scope_label = u"vaciado del nivel {}".format(level.Name)
        empty_message = u"No hay elementos de las categorias marcadas en el nivel {}.".format(
            level.Name
        )

total_candidates = sum(len(v) for v in elements_by_category.values())
if total_candidates == 0:
    forms.alert(empty_message, title="Encofrado")
    script.exit()

mode = forms.CommandSwitchWindow.show(
    ["Vista previa (sin cambios en el modelo)", "Generar geometria y cantidades"],
    message=u"Modo de ejecucion ({} elementos, {}):".format(total_candidates, scope_label),
)

if not mode:
    script.exit()

dry_run = mode.startswith("Vista previa")

# Every structural category, used only as neighbor context for contact
# trimming (never reported/paneled unless also selected above) - so a
# wall's panel still gets trimmed against a beam it touches even if you
# only picked "Muros" this run.
context_elements_by_category = {
    key: collect(fw_geom.CATEGORY_MAP[key]["bic"]) for key in fw_geom.CATEGORY_MAP
}

config = {
    "contact_tolerance_ft": 5.0 / 304.8,
    "panel_thickness_ft": 18.0 / 304.8,
    "exclude_top_faces": True,
    "exclude_foundation_bottom": True,
    "create_geometry": not dry_run,
    "formwork_materials": formwork_materials,
    "pour_against_soil": categories_window.pour_against_soil,
    "ground_elevation_ft": categories_window.ground_elevation_ft,
}

warnings = []

if dry_run:
    report = fw_geom.process_formwork(
        doc, elements_by_category, config, warnings, context_elements_by_category
    )
else:
    with revit.Transaction("Encofrado"):
        warnings.extend(fw_params.ensure_shared_parameters(doc))
        report = fw_geom.process_formwork(
            doc, elements_by_category, config, warnings, context_elements_by_category
        )

output.print_md(
    "# Resultado Encofrado {}".format("(vista previa)" if dry_run else "")
)
output.print_md(u"**Alcance:** {}".format(scope_label))
output.print_md("**Elementos procesados:** {}".format(report["element_count"]))
if not dry_run:
    output.print_md("**Paneles creados:** {}".format(report["panels_created"]))

output.print_md("\n| Categoria | Elementos | Area (m2) |")
output.print_md("|---|---|---|")
grand_total = 0.0
for cat_label, totals in sorted(report["category_totals"].items()):
    grand_total += totals["included_area_m2"]
    output.print_md(
        "| {} | {} | {:.2f} |".format(
            cat_label, totals["element_count"], totals["included_area_m2"]
        )
    )
output.print_md("| **Total** | | **{:.2f}** |".format(grand_total))

soil_total = sum(e.get("excluded_soil_area_m2", 0.0) for e in report["elements"])
if config["pour_against_soil"]:
    output.print_md(
        "\n**Vaciado contra terreno (sin encofrado):** {:.2f} m2".format(soil_total)
    )
if report.get("excluded_masonry_walls"):
    output.print_md(
        "**Muros de albanileria excluidos:** {}".format(report["excluded_masonry_walls"])
    )

if warnings:
    output.print_md("\n### Advertencias ({})".format(len(warnings)))
    for w in warnings[:50]:
        output.print_md("- {}".format(w))
