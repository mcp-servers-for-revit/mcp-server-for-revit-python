# -*- coding: utf-8 -*-
"""Genera geometria de encofrado (paneles) y calcula cantidades para
columnas, vigas, losas, muros y cimentacion de concreto, evitando doble
conteo en caras de contacto entre elementos estructurales."""

__title__ = "Generar\nEncofrado"
__author__ = "Revit MCP"

import os
import sys

SCRIPT_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REVIT_MCP_DIR = os.path.join(EXT_ROOT, "revit_mcp")
if REVIT_MCP_DIR not in sys.path:
    sys.path.append(REVIT_MCP_DIR)

import formwork_geometry as fw_geom
import formwork_params as fw_params

from pyrevit import revit, DB, forms, script

output = script.get_output()
doc = revit.doc

CATEGORY_LABELS = [
    ("Columnas", "columns"),
    ("Vigas", "beams"),
    ("Losas", "slabs"),
    ("Muros", "walls"),
    ("Cimentacion", "foundations"),
]

selected_labels = forms.SelectFromList.show(
    [label for label, _ in CATEGORY_LABELS],
    multiselect=True,
    title="Encofrado - Categorias a procesar",
    button_name="Continuar",
)

if not selected_labels:
    script.exit()

selected_keys = [key for label, key in CATEGORY_LABELS if label in selected_labels]

mode = forms.CommandSwitchWindow.show(
    ["Vista previa (sin cambios en el modelo)", "Generar geometria y cantidades"],
    message="Modo de ejecucion:",
)

if not mode:
    script.exit()

dry_run = mode.startswith("Vista previa")

material_filter = forms.ask_for_string(
    default="",
    prompt="Filtro de material (opcional, ej. 'concreto'). Vacio = sin filtro.",
    title="Encofrado",
)
if material_filter is None:
    script.exit()


def collect(bic):
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(bic)
        .WhereElementIsNotElementType()
        .ToElements()
    )


elements_by_category = {}
for key in selected_keys:
    cat_info = fw_geom.CATEGORY_MAP[key]
    all_elements = collect(cat_info["bic"])
    elements_by_category[key] = [
        el
        for el in all_elements
        if fw_geom.element_matches_material(el, material_filter)
    ]

total_candidates = sum(len(v) for v in elements_by_category.values())
if total_candidates == 0:
    forms.alert(
        "No se encontraron elementos que cumplan los criterios.", title="Encofrado"
    )
    script.exit()

config = {
    "contact_tolerance_ft": 5.0 / 304.8,
    "panel_thickness_ft": 18.0 / 304.8,
    "exclude_top_faces": True,
    "exclude_foundation_bottom": True,
    "create_geometry": not dry_run,
    "write_quantities": not dry_run,
}

warnings = []

if dry_run:
    report = fw_geom.process_formwork(doc, elements_by_category, config, warnings)
else:
    with revit.Transaction("Generar Encofrado"):
        warnings.extend(fw_params.ensure_shared_parameters(doc))
        report = fw_geom.process_formwork(doc, elements_by_category, config, warnings)

output.print_md(
    "# Resultado Encofrado {}".format("(vista previa)" if dry_run else "")
)
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

if warnings:
    output.print_md("\n### Advertencias ({})".format(len(warnings)))
    for w in warnings[:50]:
        output.print_md("- {}".format(w))
