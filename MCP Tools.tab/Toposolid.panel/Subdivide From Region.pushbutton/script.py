# -*- coding: utf-8 -*-
__title__ = "Subdivide\nFrom Region"
__doc__ = (
    "Create a Toposolid subdivision shaped by one or more Filled Region "
    "boundaries.\n\n"
    "Select a Toposolid plus one or more Filled Regions, then run this tool. "
    "You'll be asked which Toposolid type to use for the new subdivision(s)."
)

from pyrevit import revit, DB, forms
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc

SAME_AS_HOST = "<Same as host toposolid>"

selected_ids = uidoc.Selection.GetElementIds()
selected_elems = [doc.GetElement(eid) for eid in selected_ids]

toposolids = [e for e in selected_elems if isinstance(e, DB.Toposolid)]
filled_regions = [e for e in selected_elems if isinstance(e, DB.FilledRegion)]

if not toposolids:
    forms.alert(
        "Select a Toposolid (plus one or more Filled Regions) first.",
        exitscript=True,
    )
if not filled_regions:
    forms.alert(
        "Select one or more Filled Regions (plus the Toposolid) first.",
        exitscript=True,
    )

host = toposolids[0]

topo_types = list(DB.FilteredElementCollector(doc).OfClass(DB.ToposolidType))
type_names_by_id = {}
for tt in topo_types:
    name_param = tt.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
    name = name_param.AsString() if name_param else None
    if name:
        type_names_by_id[name] = tt.Id

options = [SAME_AS_HOST] + sorted(type_names_by_id.keys())

chosen_name = forms.SelectFromList.show(
    options,
    title="Choose Toposolid Type for Subdivision",
    button_name="Create Subdivision",
)

if not chosen_name:
    import sys

    sys.exit()

target_type_id = None
if chosen_name != SAME_AS_HOST:
    target_type_id = type_names_by_id[chosen_name]

created = 0
errors = []
with revit.Transaction("Create Toposolid Subdivision From Filled Region"):
    for fr in filled_regions:
        loops = list(fr.GetBoundaries())
        if not loops:
            errors.append("Filled region {} has no boundary loops".format(fr.Id))
            continue

        profiles = List[DB.CurveLoop](loops)

        try:
            if target_type_id is not None:
                host.CreateSubDivision(doc, target_type_id, profiles)
            else:
                host.CreateSubDivision(doc, profiles)
            created += 1
        except Exception as create_err:
            errors.append("Filled region {}: {}".format(fr.Id, create_err))

message = "Created {} subdivision(s).".format(created)
if errors:
    message += "\n\nErrors:\n" + "\n".join(errors)

forms.alert(message)
