# -*- coding: UTF-8 -*-
"""
Formwork Module for Revit MCP
Route for automated structural formwork (encofrado) generation and
quantity takeoff: columns, beams, slabs, walls and foundations, with
contact-face detection to avoid double counting shared/monolithic faces.
"""

from pyrevit import routes, DB
import json
import traceback
import logging

from utils import element_id_value, element_id_from_value
import formwork_geometry as fw_geom
import formwork_params as fw_params

logger = logging.getLogger(__name__)

MM_TO_FT = 1.0 / 304.8


def _resolve_by_category(doc, category_keys, material_filters):
    elements_by_category = {}
    for key in category_keys:
        cat_info = fw_geom.CATEGORY_MAP.get(key)
        if not cat_info:
            continue
        collector = (
            DB.FilteredElementCollector(doc)
            .OfCategory(cat_info["bic"])
            .WhereElementIsNotElementType()
        )
        material_filter = material_filters.get(key, "")
        matched = [
            el
            for el in collector
            if fw_geom.element_matches_material(el, material_filter)
        ]
        elements_by_category[key] = matched
    return elements_by_category


def _resolve_by_selection(doc, element_ids, category_keys, material_filters):
    elements_by_category = {}
    skipped = []

    for raw_id in element_ids:
        try:
            elem = doc.GetElement(element_id_from_value(raw_id))
        except Exception:
            elem = None
        if not elem or not elem.Category:
            skipped.append(raw_id)
            continue

        key = None
        # Match by category Id directly against the map (more robust than
        # round-tripping through BuiltInCategory casts across API versions).
        for cand_key, cand_info in fw_geom.CATEGORY_MAP.items():
            try:
                cand_cat = doc.Settings.Categories.get_Item(cand_info["bic"])
                if cand_cat and cand_cat.Id.Equals(elem.Category.Id):
                    key = cand_key
                    break
            except Exception:
                continue

        if not key or (category_keys and key not in category_keys):
            skipped.append(raw_id)
            continue
        if not fw_geom.element_matches_material(elem, material_filters.get(key, "")):
            continue

        elements_by_category.setdefault(key, []).append(elem)

    return elements_by_category, skipped


def register_formwork_routes(api):
    """Register formwork generation routes with the API"""

    @api.route("/generate_formwork/", methods=["POST"])
    def generate_formwork(doc, request):
        """
        Generate structural formwork (encofrado) geometry and/or quantity
        takeoff for concrete structural elements.

        Expected request data:
        {
            "scope": "model" | "selection",
            "element_ids": [123, 456],              # required if scope == "selection"
            "categories": ["columns","beams","slabs","walls","foundations"],
            "material_filter": "concreto",              # default, all categories
            "material_filters": {                        # optional per-category override
                "beams": "f'c=210",
                "columns": "f'c=280"
            },
            "panel_thickness_mm": 18,
            "contact_tolerance_mm": 5,
            "exclude_top_faces": true,
            "exclude_foundation_bottom": true,
            "create_geometry": true,
            "dry_run": false
        }
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided or invalid request format"},
                    status=400,
                )

            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            if not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"},
                    status=400,
                )

            scope = data.get("scope", "model")
            requested_categories = data.get("categories") or list(
                fw_geom.DEFAULT_CATEGORIES
            )
            invalid_categories = [
                c for c in requested_categories if c not in fw_geom.CATEGORY_MAP
            ]
            if invalid_categories:
                return routes.make_response(
                    data={
                        "error": "Categorias invalidas: {}".format(
                            ", ".join(invalid_categories)
                        ),
                        "valid_categories": list(fw_geom.CATEGORY_MAP.keys()),
                    },
                    status=400,
                )

            material_filter = data.get("material_filter", "")
            material_filters_input = data.get("material_filters") or {}
            if not isinstance(material_filters_input, dict):
                return routes.make_response(
                    data={
                        "error": "material_filters debe ser un objeto {categoria: filtro}"
                    },
                    status=400,
                )
            invalid_filter_categories = [
                c for c in material_filters_input if c not in fw_geom.CATEGORY_MAP
            ]
            if invalid_filter_categories:
                return routes.make_response(
                    data={
                        "error": "Categorias invalidas en material_filters: {}".format(
                            ", ".join(invalid_filter_categories)
                        ),
                        "valid_categories": list(fw_geom.CATEGORY_MAP.keys()),
                    },
                    status=400,
                )
            material_filters = {
                key: material_filters_input.get(key, material_filter)
                for key in requested_categories
            }

            dry_run = bool(data.get("dry_run", False))
            create_geometry = bool(data.get("create_geometry", True)) and not dry_run

            config = {
                "panel_thickness_ft": float(data.get("panel_thickness_mm", 18.0)) * MM_TO_FT,
                "contact_tolerance_ft": float(data.get("contact_tolerance_mm", 5.0)) * MM_TO_FT,
                "exclude_top_faces": bool(data.get("exclude_top_faces", True)),
                "exclude_foundation_bottom": bool(
                    data.get("exclude_foundation_bottom", True)
                ),
                "create_geometry": create_geometry,
            }

            skipped_ids = []
            if scope == "selection":
                element_ids = data.get("element_ids") or []
                if not element_ids:
                    return routes.make_response(
                        data={"error": "scope='selection' requiere element_ids"},
                        status=400,
                    )
                elements_by_category, skipped_ids = _resolve_by_selection(
                    doc, element_ids, requested_categories, material_filters
                )
            elif scope == "model":
                elements_by_category = _resolve_by_category(
                    doc, requested_categories, material_filters
                )
            else:
                return routes.make_response(
                    data={"error": "scope debe ser 'model' o 'selection'"}, status=400
                )

            total_candidates = sum(len(v) for v in elements_by_category.values())
            if total_candidates == 0:
                return routes.make_response(
                    data={
                        "status": "success",
                        "message": "No se encontraron elementos que cumplan los criterios",
                        "elements": [],
                        "category_totals": {},
                        "panels_created": 0,
                        "dry_run": dry_run,
                    }
                )

            warnings = []

            if dry_run:
                report = fw_geom.process_formwork(
                    doc, elements_by_category, config, warnings
                )
            else:
                t = DB.Transaction(doc, "Generar Encofrado via MCP")
                t.Start()
                try:
                    if create_geometry:
                        warnings.extend(fw_params.ensure_shared_parameters(doc))
                    report = fw_geom.process_formwork(
                        doc, elements_by_category, config, warnings
                    )
                    t.Commit()
                except Exception as tx_error:
                    if t.HasStarted() and not t.HasEnded():
                        t.RollBack()
                        logger.error("Transaction rolled back due to error")
                    raise tx_error

            if skipped_ids:
                warnings.append(
                    "{} elemento(s) de element_ids omitidos (no encontrados o categoria no solicitada)".format(
                        len(skipped_ids)
                    )
                )

            response_data = {
                "status": "success",
                "dry_run": dry_run,
                "scope": scope,
                "categories": requested_categories,
                "elements": report["elements"],
                "category_totals": report["category_totals"],
                "panels_created": report["panels_created"],
                "element_count": report["element_count"],
                "warnings": warnings,
            }
            return routes.make_response(data=response_data)

        except Exception as e:
            logger.error("Failed to generate formwork: {}".format(str(e)))
            error_trace = traceback.format_exc()
            return routes.make_response(
                data={"error": str(e), "traceback": error_trace}, status=500
            )

    logger.info("Formwork routes registered successfully")
