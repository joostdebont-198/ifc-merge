"""
merge_core.py  -  merge several IFC models into one IFC model.

Built on IfcOpenShell + ifcpatch (MergeProjects recipe) and adds:
  * pre-checks: schema, length unit, georeferencing, element counts
  * schema migration when models use different IFC versions (lossy, warns)
  * one IfcProject in the result
  * optional merging of sites / buildings / storeys that have the same name
  * optional tagging of every element with its source model (shared Pset)
  * GlobalId collision check
  * a plain-text report

Used by ifc_merge_app.py (GUI) and usable from the command line:
    python merge_core.py a.ifc b.ifc c.ifc -o merged.ifc
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit

LogFn = Callable[[str], None]


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------


@dataclass
class ModelInfo:
    path: str
    schema: str
    length_unit: str
    unit_scale_to_m: float
    app: str
    project_name: str
    sites: int
    buildings: int
    storeys: int
    elements: int
    origin_enh: tuple[float, float, float]
    grid_north: float
    has_crs: bool

    @property
    def name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]


@dataclass
class MergeOptions:
    # None = schema of the first model
    target_schema: Optional[str] = None
    combine_sites: bool = True
    combine_buildings: bool = True
    combine_storeys: bool = True
    # storeys with the same name are only merged if their elevations are
    # within this distance (metres)
    storey_tolerance_m: float = 0.3
    tag_source: bool = True
    project_name: Optional[str] = None
    fix_duplicate_guids: bool = True


@dataclass
class MergeReport:
    models: list[ModelInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    merged_sites: int = 0
    merged_buildings: int = 0
    merged_storeys: int = 0
    fixed_guids: int = 0
    output: str = ""
    out_schema: str = ""
    out_elements: int = 0
    out_sites: int = 0
    out_buildings: int = 0
    out_storeys: int = 0

    def text(self) -> str:
        lines = ["IFC MERGE REPORT", "=" * 60, ""]
        lines.append("Input models")
        for m in self.models:
            lines.append(
                f"  - {os.path.basename(m.path)}: {m.schema}, unit {m.length_unit}, "
                f"{m.elements} elements, {m.sites} site(s) / {m.buildings} building(s) / "
                f"{m.storeys} storey(s), app: {m.app}"
            )
        lines.append("")
        lines.append("Result")
        lines.append(f"  file:      {self.output}")
        lines.append(f"  schema:    {self.out_schema}")
        lines.append(f"  elements:  {self.out_elements}")
        lines.append(
            f"  structure: {self.out_sites} site(s) / {self.out_buildings} building(s) / "
            f"{self.out_storeys} storey(s)"
        )
        lines.append(
            f"  combined:  {self.merged_sites} site(s), {self.merged_buildings} building(s), "
            f"{self.merged_storeys} storey(s) with the same name"
        )
        if self.fixed_guids:
            lines.append(f"  GlobalIds regenerated: {self.fixed_guids}")
        lines.append("")
        if self.warnings:
            lines.append("Warnings")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        else:
            lines.append("No warnings.")
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# Inspection
# ----------------------------------------------------------------------------


def _unit_name(f: ifcopenshell.file) -> str:
    try:
        u = ifcopenshell.util.unit.get_project_unit(f, "LENGTHUNIT")
        return ifcopenshell.util.unit.get_full_unit_name(u) if u else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _app_name(f: ifcopenshell.file) -> str:
    try:
        apps = f.by_type("IfcApplication")
        if apps:
            a = apps[0]
            return f"{a.ApplicationFullName or ''} {a.Version or ''}".strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def inspect_model(f: ifcopenshell.file, path: str) -> ModelInfo:
    try:
        origin = tuple(
            float(v)
            for v in ifcopenshell.util.geolocation.auto_xyz2enh(f, 0, 0, 0, should_return_in_map_units=False)
        )
    except Exception:
        origin = (0.0, 0.0, 0.0)
    try:
        gn = float(ifcopenshell.util.geolocation.get_grid_north(f))
    except Exception:
        gn = 0.0
    has_crs = False
    if f.schema != "IFC2X3":
        has_crs = bool(f.by_type("IfcProjectedCRS"))
    else:
        proj = f.by_type("IfcProject")
        if proj and ifcopenshell.util.element.get_pset(proj[0], "ePSet_ProjectedCRS"):
            has_crs = True
    projects = f.by_type("IfcProject")
    try:
        scale = float(ifcopenshell.util.unit.calculate_unit_scale(f))
    except Exception:
        scale = 1.0
    return ModelInfo(
        path=path,
        schema=f.schema,
        length_unit=_unit_name(f),
        unit_scale_to_m=scale,
        app=_app_name(f),
        project_name=(projects[0].Name if projects and projects[0].Name else "") if projects else "",
        sites=len(f.by_type("IfcSite")),
        buildings=len(f.by_type("IfcBuilding")),
        storeys=len(f.by_type("IfcBuildingStorey")),
        elements=len(f.by_type("IfcElement")),
        origin_enh=origin,  # type: ignore[arg-type]
        grid_north=gn,
        has_crs=has_crs,
    )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _norm(name: Optional[str]) -> str:
    """Normalise a name for matching: lowercase, collapse spaces, strip."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip().lower()


def _ensure_owner(f: ifcopenshell.file) -> None:
    """IFC2X3 requires an IfcOwnerHistory on every new rooted entity. Reuse
    what the file already has, or create a minimal user/application."""
    if f.schema != "IFC2X3":
        return
    import ifcopenshell.api.owner.settings as s

    if not f.by_type("IfcPersonAndOrganization"):
        person = ifcopenshell.api.run("owner.add_person", f, identification="merge", family_name="IFC Merge")
        org = ifcopenshell.api.run("owner.add_organisation", f, identification="merge", name="IFC Merge")
        ifcopenshell.api.run("owner.add_person_and_organisation", f, person=person, organisation=org)
    if not f.by_type("IfcApplication"):
        ifcopenshell.api.run("owner.add_application", f, application_full_name="IFC Merge",
                             application_identifier="ifc_merge", version="1.0")
    # look up per file, never capture an entity of another file
    s.get_user = lambda ifc: ifc.by_type("IfcPersonAndOrganization")[0]
    s.get_application = lambda ifc: ifc.by_type("IfcApplication")[0]


def _tag_source(f: ifcopenshell.file, source_name: str) -> None:
    """Add ONE shared property set 'Pset_MergeSource' to all IfcElement +
    spatial elements of the model, with the file name as SourceModel.
    One pset + one relationship for the whole model: cheap, even for big files."""
    objects = list(f.by_type("IfcElement")) + list(f.by_type("IfcSpatialStructureElement"))
    if not objects:
        return
    _ensure_owner(f)
    first = objects[0]
    pset = ifcopenshell.api.run("pset.add_pset", f, product=first, name="Pset_MergeSource")
    ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"SourceModel": source_name})
    # find the relationship that was created and attach everything to it
    for rel in f.get_inverse(pset):
        if rel.is_a("IfcRelDefinesByProperties"):
            rel.RelatedObjects = objects
            break


def _move_children(f: ifcopenshell.file, dup, kept) -> None:
    """Move everything that hangs under `dup` (aggregated children, contained
    elements, property sets, ...) to `kept`, then delete `dup`.

    Only relationships are changed. Object placements stay as they are, so
    the geometry does not move."""
    for inverse in list(f.get_inverse(dup)):
        # the link from dup's parent to dup: just drop dup from it
        if inverse.is_a("IfcRelAggregates") and dup in (inverse.RelatedObjects or []):
            rest = [o for o in inverse.RelatedObjects if o != dup]
            if rest:
                inverse.RelatedObjects = rest
            else:
                f.remove(inverse)
            continue

        # dup is the parent of its children -> fold into kept's own aggregation
        if inverse.is_a("IfcRelAggregates") and inverse.RelatingObject == dup:
            existing = [r for r in (kept.IsDecomposedBy or [])]
            if existing:
                target = existing[0]
                target.RelatedObjects = list(target.RelatedObjects) + list(inverse.RelatedObjects)
                f.remove(inverse)
            else:
                inverse.RelatingObject = kept
            continue

        # dup contains elements -> fold into kept's own containment
        if inverse.is_a("IfcRelContainedInSpatialStructure") and inverse.RelatingStructure == dup:
            existing = [r for r in (kept.ContainsElements or [])]
            if existing:
                target = existing[0]
                target.RelatedElements = list(target.RelatedElements) + list(inverse.RelatedElements)
                f.remove(inverse)
            else:
                inverse.RelatingStructure = kept
            continue

        # anything else that points at dup (psets, references, systems ...)
        _replace_in_entity(f, inverse, dup, kept)

    f.remove(dup)


def _replace_in_entity(f: ifcopenshell.file, entity, old, new) -> None:
    """Replace `old` by `new` in all attributes of `entity`. If `new` is already
    present in a list attribute, `old` is simply removed from that list."""
    for i in range(len(entity)):
        try:
            val = entity[i]
        except Exception:
            continue
        if val == old:
            entity[i] = new
        elif isinstance(val, (list, tuple)) and old in val:
            lst = [v for v in val if v != old]
            if new not in lst:
                lst.append(new)
            entity[i] = lst


def _parent_of(elem):
    """Spatial parent through IfcRelAggregates (or None)."""
    for rel in elem.Decomposes or []:
        if rel.is_a("IfcRelAggregates"):
            return rel.RelatingObject
    return None


def _combine_by_name(f: ifcopenshell.file, ifc_class: str, log: LogFn, report: MergeReport,
                     tolerance_units: Optional[float] = None) -> int:
    """Merge all `ifc_class` elements that share (parent, normalised name).
    For storeys, the elevation must also be within `tolerance_units`."""
    groups: dict[tuple, list] = {}
    for e in f.by_type(ifc_class):
        key = (_parent_of(e), _norm(e.Name))
        if not key[1]:
            continue  # unnamed: never merge blindly
        groups.setdefault(key, []).append(e)

    merged = 0
    for (parent, name), elems in groups.items():
        if len(elems) < 2:
            continue
        kept = elems[0]
        for dup in elems[1:]:
            if ifc_class == "IfcBuildingStorey" and tolerance_units is not None:
                e1 = kept.Elevation if kept.Elevation is not None else 0.0
                e2 = dup.Elevation if dup.Elevation is not None else 0.0
                if abs(e1 - e2) > tolerance_units:
                    msg = (f"Storey '{kept.Name}' appears twice with different elevation "
                           f"({e1:g} vs {e2:g}). Kept both; check the source models.")
                    log("  ! " + msg)
                    report.warnings.append(msg)
                    continue
            log(f"  merged {ifc_class[3:]} '{dup.Name}' (#{dup.id()}) into #{kept.id()}")
            _move_children(f, dup, kept)
            merged += 1
    return merged


def _fix_duplicate_guids(f: ifcopenshell.file, log: LogFn) -> int:
    seen: set[str] = set()
    fixed = 0
    for e in f.by_type("IfcRoot"):
        gid = e.GlobalId
        if gid in seen:
            e.GlobalId = ifcopenshell.guid.new()
            fixed += 1
        else:
            seen.add(gid)
    if fixed:
        log(f"  regenerated {fixed} duplicate GlobalId(s)")
    return fixed


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------


def merge(paths: list[str], output: str, options: Optional[MergeOptions] = None,
          log: Optional[LogFn] = None) -> MergeReport:
    """Merge the IFC files in `paths` (first one is the main model) into `output`."""
    options = options or MergeOptions()
    log = log or (lambda s: None)
    report = MergeReport(output=output)

    if len(paths) < 2:
        raise ValueError("Need at least two IFC files to merge.")

    # 1. open + inspect -----------------------------------------------------
    files: list[ifcopenshell.file] = []
    for p in paths:
        log(f"Opening {os.path.basename(p)} ...")
        f = ifcopenshell.open(p)
        info = inspect_model(f, p)
        report.models.append(info)
        log(f"  {info.schema}, unit {info.length_unit}, {info.elements} elements, "
            f"{info.sites}/{info.buildings}/{info.storeys} site/building/storey, app: {info.app}")
        if not f.by_type("IfcProject"):
            raise ValueError(f"{os.path.basename(p)} has no IfcProject; cannot merge.")
        files.append(f)

    # 2. checks ---------------------------------------------------------------
    target_schema = options.target_schema or report.models[0].schema
    schemas = {m.schema for m in report.models}
    if len(schemas) > 1 or target_schema not in schemas:
        msg = (f"Models use different IFC versions ({', '.join(sorted(schemas))}). "
               f"All will be migrated to {target_schema}. Migration is lossy: check the result.")
        log("! " + msg)
        report.warnings.append(msg)

    units = {m.length_unit for m in report.models}
    if len(units) > 1:
        msg = (f"Models use different length units ({', '.join(sorted(units))}). "
               f"Converted to {report.models[0].length_unit} of the first model.")
        log("! " + msg)
        report.warnings.append(msg)

    georef = {m.has_crs for m in report.models}
    if len(georef) > 1:
        msg = ("Some models are georeferenced (CRS) and some are not. Their relative position "
               "may be wrong. Check placement in a viewer.")
        log("! " + msg)
        report.warnings.append(msg)
    origins = {tuple(round(v, 3) for v in m.origin_enh) for m in report.models}
    if len(origins) > 1:
        log("  models have different origins; MergeProjects will offset them to match the first model")

    # 3. migrate if needed --------------------------------------------------------
    for i, f in enumerate(files):
        if f.schema != target_schema:
            log(f"Migrating {os.path.basename(paths[i])} {f.schema} -> {target_schema} ...")
            from ifcpatch.recipes import Migrate
            files[i] = _run_patcher(Migrate.Patcher(f, _silent_logger(), schema=target_schema))

    # 4. tag source ----------------------------------------------------------------
    if options.tag_source:
        for f, m in zip(files, report.models):
            log(f"Tagging elements of {m.name} with Pset_MergeSource ...")
            _tag_source(f, os.path.basename(m.path))

    # 5. merge projects (ifcpatch recipe handles units + georef offsets) -------
    main = files[0]
    from ifcpatch.recipes import MergeProjects
    log("Merging projects ...")
    main = _run_patcher(MergeProjects.Patcher(main, _silent_logger(), filepaths=files[1:]))

    if options.project_name:
        main.by_type("IfcProject")[0].Name = options.project_name

    # 6. combine duplicate spatial structure ---------------------------------------
    scale = report.models[0].unit_scale_to_m or 1.0
    tol_units = options.storey_tolerance_m / scale

    if options.combine_sites:
        log("Combining sites with the same name ...")
        report.merged_sites = _combine_by_name(main, "IfcSite", log, report)
    if options.combine_buildings:
        log("Combining buildings with the same name ...")
        report.merged_buildings = _combine_by_name(main, "IfcBuilding", log, report)
    if options.combine_storeys:
        log("Combining storeys with the same name ...")
        report.merged_storeys = _combine_by_name(main, "IfcBuildingStorey", log, report, tol_units)

    # 7. guids ---------------------------------------------------------------------
    if options.fix_duplicate_guids:
        report.fixed_guids = _fix_duplicate_guids(main, log)
        if report.fixed_guids:
            report.warnings.append(
                f"{report.fixed_guids} elements had the same GlobalId as another element "
                "(same model exported twice?). New ids were generated for them.")

    # 8. write ---------------------------------------------------------------------
    log(f"Writing {output} ...")
    main.write(output)

    report.out_schema = main.schema
    report.out_elements = len(main.by_type("IfcElement"))
    report.out_sites = len(main.by_type("IfcSite"))
    report.out_buildings = len(main.by_type("IfcBuilding"))
    report.out_storeys = len(main.by_type("IfcBuildingStorey"))
    log("Done.")
    return report


def _silent_logger() -> logging.Logger:
    lg = logging.getLogger("ifc_merge.ifcpatch")
    if not lg.handlers:
        lg.addHandler(logging.NullHandler())
    return lg


def _run_patcher(patcher) -> ifcopenshell.file:
    patcher.patch()
    out = patcher.get_output()
    assert isinstance(out, ifcopenshell.file)
    return out


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Merge several IFC models into one.")
    ap.add_argument("inputs", nargs="+", help="IFC files; the first one is the main model")
    ap.add_argument("-o", "--output", required=True, help="output .ifc")
    ap.add_argument("--schema", choices=["IFC2X3", "IFC4", "IFC4X3"], help="target schema (default: first model)")
    ap.add_argument("--keep-structure", action="store_true", help="do not combine same-name sites/buildings/storeys")
    ap.add_argument("--no-tag", action="store_true", help="do not add Pset_MergeSource")
    ap.add_argument("--tolerance", type=float, default=0.3, help="storey elevation tolerance in metres")
    ap.add_argument("--project-name", help="name for the merged IfcProject")
    a = ap.parse_args()

    opts = MergeOptions(
        target_schema=a.schema,
        combine_sites=not a.keep_structure,
        combine_buildings=not a.keep_structure,
        combine_storeys=not a.keep_structure,
        storey_tolerance_m=a.tolerance,
        tag_source=not a.no_tag,
        project_name=a.project_name,
    )
    rep = merge(a.inputs, a.output, opts, log=print)
    print()
    print(rep.text())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
