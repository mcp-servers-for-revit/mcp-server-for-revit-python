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

INTEGER = "integer"  # `is_text` value for a whole-number parameter

# name -> (is_text, [BuiltInCategory,...])
PANEL_PARAMS = [
    ("EF_Elemento_Origen_Id", True),
    ("EF_Categoria_Origen", True),
    ("EF_Area_m2", False),
    ("EF_Material_Encofrado", True),
]

PANEL_CATEGORIES = [DB.BuiltInCategory.OST_GenericModel]

# Instance parameters the user fills on source elements to steer the run.
# EF_Cara_Contra_Terreno (walls): which face is cast against the ground -
# "Exterior" / "Interior" (the wall's own exterior/interior side, as its
# flip arrows show), "Ambas", "Ninguna", or blank for automatic detection.
ELEMENT_PARAMS = [
    ("EF_Cara_Contra_Terreno", True, [DB.BuiltInCategory.OST_Walls]),
]

# Every category that gets formwork (CATEGORY_MAP in formwork_geometry).
STRUCTURAL_CATEGORIES = [
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_StructuralFoundation,
    DB.BuiltInCategory.OST_Stairs,
]

# Formwork results written on each processed source element by every run
# that creates geometry (name, is_text). EF_Material_Encofrado is shared
# with the panels.
ELEMENT_RESULT_PARAMS = [
    ("EF_Area_Encofrado_m2", False),
    ("EF_Paneles", INTEGER),
    ("EF_Material_Encofrado", True),
]

# Element parameters earlier versions created and no longer use: their
# bindings are removed from the project on the next run.
OBSOLETE_ELEMENT_PARAMS = ["EF_Area_Contacto_m2", "EF_Area_Contra_Terreno_m2"]


def remove_obsolete_parameters(doc):
    """Unbind OBSOLETE_ELEMENT_PARAMS from the project (their values go
    with them). Must run inside an active Transaction. Returns the names
    removed."""
    return unbind_parameters(doc, OBSOLETE_ELEMENT_PARAMS)


def unbind_parameters(doc, names):
    """Unbind the named project parameters (their values go with them).
    Must run inside an active Transaction. Returns the names removed."""
    binding_map = doc.ParameterBindings
    found = []
    iterator = binding_map.ForwardIterator()
    while iterator.MoveNext():
        if iterator.Key.Name in names:
            found.append(iterator.Key)
    for definition in found:
        binding_map.Remove(definition)
    return [d.Name for d in found]

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
    """Return the type spec for the parameter (`is_text`: True text, False
    number, INTEGER whole number), handling both the modern ForgeTypeId
    (SpecTypeId, Revit 2022+) and legacy ParameterType API."""
    try:
        if is_text == INTEGER:
            return DB.SpecTypeId.Int.Integer
        return DB.SpecTypeId.String.Text if is_text else DB.SpecTypeId.Number
    except AttributeError:
        if is_text == INTEGER:
            return DB.ParameterType.Integer
        return DB.ParameterType.Text if is_text else DB.ParameterType.Number


def _group_type_id():
    try:
        return DB.GroupTypeId.General
    except AttributeError:
        return DB.BuiltInParameterGroup.PG_GENERAL


def _get_or_create_group(def_file, group_name=GROUP_NAME):
    for group in def_file.Groups:
        if group.Name == group_name:
            return group
    return def_file.Groups.Create(group_name)


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
        # Already bound: only add the categories it's missing, keeping the
        # rest as the user configured it.
        binding = binding_map.get_Item(definition)
        missing = [
            c
            for c in _category_set(doc, built_in_categories)
            if not binding.Categories.Contains(c)
        ]
        if not missing:
            return False
        for category in missing:
            binding.Categories.Insert(category)
        binding_map.ReInsert(definition, binding, _group_type_id())
        return True

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
    the Generic Models category, the per-element control parameters
    (ELEMENT_PARAMS) to their categories, and the per-element results
    (ELEMENT_RESULT_PARAMS) to every structural category. Must be called
    inside an active
    Transaction. Returns a list of human-readable warnings (empty on
    full success)."""
    specs = [(name, is_text, PANEL_CATEGORIES, True) for name, is_text in PANEL_PARAMS]
    specs += [(name, is_text, categories, True) for name, is_text, categories in ELEMENT_PARAMS]
    specs += [
        (name, is_text, STRUCTURAL_CATEGORIES, True)
        for name, is_text in ELEMENT_RESULT_PARAMS
    ]
    warnings = ensure_parameters(doc, GROUP_NAME, specs)

    try:
        remove_obsolete_parameters(doc)
    except Exception as e:
        warnings.append("No se pudieron quitar parametros obsoletos: {}".format(str(e)))

    return warnings


def ensure_parameters(doc, group_name, specs):
    """Create (in this extension's shared parameter file, under
    `group_name`) and bind every parameter in `specs`:
    [(name, is_text, [BuiltInCategory, ...], is_instance)]. Must be called
    inside an active Transaction. Returns human-readable warnings."""

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

    group = _get_or_create_group(def_file, group_name)

    for name, is_text, categories, is_instance in specs:
        try:
            definition = _get_or_create_definition(group, name, is_text)
            _ensure_binding(doc, definition, categories, is_instance=is_instance)
        except Exception as e:
            warnings.append(
                "No se pudo crear/enlazar el parametro {}: {}".format(name, str(e))
            )

    if previous_file:
        app.SharedParametersFilename = previous_file

    return warnings
