"""Generate small test IFC models for merge_core."""
import os
import sys

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.project
import ifcopenshell.api.aggregate
import ifcopenshell.api.spatial
import ifcopenshell.api.geometry

OUT = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(OUT, exist_ok=True)


def build(name, schema, unit_prefix, building_name, storeys, element_class, n_per_storey, offset_x=0.0,
          site_name="Gasfabriek"):
    f = ifcopenshell.file(schema=schema)
    if schema == "IFC2X3":
        import ifcopenshell.api.owner.settings as s
        person = ifcopenshell.api.run("owner.add_person", f, identification="t", family_name="Test")
        org = ifcopenshell.api.run("owner.add_organisation", f, identification="t", name="Test")
        user = ifcopenshell.api.run("owner.add_person_and_organisation", f, person=person, organisation=org)
        app = ifcopenshell.api.run("owner.add_application", f, application_full_name="TestGen",
                                   application_identifier="testgen", version="1")
        s.get_user = lambda ifc: ifc.by_type("IfcPersonAndOrganization")[0]
        s.get_application = lambda ifc: ifc.by_type("IfcApplication")[0]
    else:
        import ifcopenshell.api.owner.settings as s
        s.get_user = lambda ifc: None
        s.get_application = lambda ifc: None
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name=f"Project {name}")
    ifcopenshell.api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS" if unit_prefix == "MILLI" else "METERS"})
    scale = 1000.0 if unit_prefix == "MILLI" else 1.0
    ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name=site_name)
    bld = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name=building_name)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=site)
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=bld)
    for sname, elev in storeys:
        st = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=sname)
        st.Elevation = elev * scale
        ifcopenshell.api.run("aggregate.assign_object", f, products=[st], relating_object=bld)
        import numpy as np
        m = np.eye(4)
        m[2, 3] = elev  # api matrices are in metres (SI)
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=st, matrix=m)
        for i in range(n_per_storey):
            el = ifcopenshell.api.run("root.create_entity", f, ifc_class=element_class, name=f"{element_class[3:]} {sname}-{i}")
            m2 = np.eye(4)
            m2[0, 3] = offset_x + i * 2.0
            ifcopenshell.api.run("geometry.edit_object_placement", f, product=el, matrix=m2)
            rep = ifcopenshell.api.run("geometry.add_wall_representation", f, context=body,
                                       length=1.0 * scale, height=3.0 * scale, thickness=0.2 * scale)
            ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
            ifcopenshell.api.run("spatial.assign_container", f, products=[el], relating_structure=st)
    path = os.path.join(OUT, f"{name}.ifc")
    f.write(path)
    print("wrote", path, f.schema, len(f.by_type("IfcElement")), "elements")
    return path


storeys_a = [("Begane grond", 0.0), ("1e verdieping", 3.5), ("2e verdieping", 7.0)]
# architecture, IFC4, metres
build("A_architect", "IFC4", "", "Gebouw A", storeys_a, "IfcWall", 3)
# MEP of the same building: same storey names (one written differently), IFC4, metres
build("A_mep", "IFC4", "", "Gebouw A", [("begane grond ", 0.0), ("1e verdieping", 3.5), ("Dak", 10.5)], "IfcPipeSegment", 2, offset_x=10)
# structure of same building: IFC2X3 in millimetres -> tests migration + unit conversion
build("A_structure", "IFC2X3", "MILLI", "Gebouw A", storeys_a, "IfcColumn", 2, offset_x=20)
# another building on the same site
build("B_architect", "IFC4", "", "Gebouw B", [("Begane grond", 0.0), ("1e verdieping", 4.0)], "IfcWall", 2, offset_x=50)
