# -*- coding: UTF-8 -*-
"""
Formwork Shared Parameters Module for Revit MCP
Idempotent setup of the shared parameters used by the formwork
(encofrado) generator. Safe to call on every request: it's a no-op once
the parameters already exist and are bound.
"""

from pyrevit import DB
import os
import logging

logger = logging.getLogger(__name__)

GROUP_NAME = "Encofrado"
SHARED_PARAM_FILE_NAME = "EncofradoSharedParams.txt"

# name -> (is_text, [BuiltInCategory,...])
PANEL_PARAMS = [
    ("EF_Elemento_Origen_Id", True),
    ("EF_Categoria_Origen", True),
    ("EF_Area_m2", False),
    ("EF_Material_Encofrado", True),
]

PANEL_CATEGORIES = [DB.BuiltInCategory.OST_GenericModel]

_FILE_HEADER = (
    "# This is a Revit shared parameter file.\n"
    "# Do not edit manually.\n"
    "*META\tVERSION\tMINVERSION\n"
    "META\t2\t1\n"
    "*GROUP\tID\tNAME\n"
    "*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n"
)


def _shared_param_file_path():
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
    return os.path.join(data_dir, SHARED_PARAM_FILE_NAME)


def _spec_id(is_text):
    """Return the type spec for the parameter, handling both the modern
    ForgeTypeId (SpecTypeId, Revit 2022+) and legacy ParameterType API."""
    try:
        return DB.SpecTypeId.String.Text if is_text else DB.SpecTypeId.Number
    except AttributeError:
        return DB.ParameterType.Text if is_text else DB.ParameterType.Number


def _group_type_id():
    try:
        return DB.GroupTypeId.General
    except AttributeError:
        return DB.BuiltInParameterGroup.PG_GENERAL


def _get_or_create_group(def_file):
    for group in def_file.Groups:
        if group.Name == GROUP_NAME:
            return group
    return def_file.Groups.Create(GROUP_NAME)


def _get_or_create_definition(group, name, is_text):
    for definition in group.Definitions:
        if definition.Name == name:
            return definition
    options = DB.ExternalDefinitionCreationOptions(name, _spec_id(is_text))
    return group.Definitions.Create(options)


def _category_set(doc, built_in_categories):
    cat_set = doc.Application.Create.NewCategorySet()
    for bic in built_in_categories:
        category = doc.Settings.Categories.get_Item(bic)
        if category:
            cat_set.Insert(category)
    return cat_set


def _ensure_binding(doc, definition, built_in_categories, is_instance=True):
    binding_map = doc.ParameterBindings
    if binding_map.Contains(definition):
        return False  # already bound somewhere; leave it as the user configured it

    cat_set = _category_set(doc, built_in_categories)
    binding = (
        doc.Application.Create.NewInstanceBinding(cat_set)
        if is_instance
        else doc.Application.Create.NewTypeBinding(cat_set)
    )
    binding_map.Insert(definition, binding, _group_type_id())
    return True


def ensure_shared_parameters(doc):
    """Make sure every formwork panel parameter exists and is bound to
    the Generic Models category. Must be called inside an active
    Transaction. Returns a list of human-readable warnings (empty on
    full success)."""

    warnings = []
    app = doc.Application
    file_path = _shared_param_file_path()

    if not os.path.isfile(file_path):
        with open(file_path, "w") as f:
            f.write(_FILE_HEADER)

    previous_file = app.SharedParametersFilename
    app.SharedParametersFilename = file_path
    def_file = app.OpenSharedParameterFile()

    if def_file is None:
        warnings.append(
            "No se pudo abrir/crear el archivo de parametros compartidos en {}".format(
                file_path
            )
        )
        if previous_file:
            app.SharedParametersFilename = previous_file
        return warnings

    group = _get_or_create_group(def_file)

    for name, is_text in PANEL_PARAMS:
        try:
            definition = _get_or_create_definition(group, name, is_text)
            _ensure_binding(doc, definition, PANEL_CATEGORIES, is_instance=True)
        except Exception as e:
            warnings.append(
                "No se pudo crear/enlazar el parametro {}: {}".format(name, str(e))
            )

    if previous_file:
        app.SharedParametersFilename = previous_file

    return warnings
