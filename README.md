# IFC Merge

Small desktop tool to merge several IFC models into **one IFC file** that you can work further in.
Drop the files, press Merge, done.

Built on [IfcOpenShell](https://ifcopenshell.org) (open source, same engine as Bonsai/BlenderBIM).

## What it does

1. Opens every model and shows schema (IFC2X3 / IFC4 / IFC4X3), length unit, element counts and software.
2. Makes **one IfcProject**. Units are converted to the units of the first model.
   Georeferenced models get their offset/rotation fixed so they land in the right place.
3. If models have **different IFC versions**, they are migrated to one version (default: the first model's).
   This is lossy. The tool warns you; check the result.
4. **Combines spatial structure with the same name** (optional, on by default):
   - sites with the same name become one site
   - buildings with the same name (in the same site) become one building
   - storeys with the same name (in the same building) become one storey, but only
     if their elevation is within the tolerance (default 0.30 m). Otherwise both stay and you get a warning.
   Matching ignores capitals and extra spaces: `begane grond ` = `Begane grond`.
   Only relationships are changed, never placements, so geometry does not move.
5. **Tags every element** with `Pset_MergeSource / SourceModel = <file name>` (optional, on by default),
   so you can always filter per discipline or building later in Revit, Solibri, Bonsai, Speckle...
6. Checks for **duplicate GlobalIds** (happens when the same export is dropped twice) and gives
   the duplicates new ids.
7. Writes the merged IFC plus a small `*_merge_report.txt` next to it.

## Typical use at KCAP

| Case | What to drop | Options |
|---|---|---|
| Architect + structure + MEP of one building | the 3 discipline models | defaults |
| Several buildings on one site (masterplan) | one model per building | defaults; buildings keep their own names so they stay separate, the site is combined |
| Both at once | everything | defaults |
| You want to keep every model's own site/building | anything | untick "sites / buildings / storeys" |

**Order matters a bit.** The first model in the list is the main model: its IFC version, length unit,
origin and project info are kept. Put the architect model (or the cleanest model) first.

## Install (Windows, Python route)

```
pip install -r requirements.txt
python ifc_merge_app.py
```

Needs Python 3.10 or newer. Drag-and-drop uses `tkinterdnd2`; without it you still have the "Add files..." button.

## Build a standalone .exe

Run `build_exe.bat` once on a Windows PC with Python. You get `dist\IFC Merge.exe` that you can
copy to any colleague (no Python needed there). On a Mac use `build_app_mac.sh`.

## Command line

```
python merge_core.py architect.ifc structure.ifc mep.ifc -o merged.ifc
python merge_core.py a.ifc b.ifc -o out.ifc --keep-structure --no-tag --schema IFC4 --project-name "Gasfabriek"
```

## Limits / things to know

- **Migration between IFC versions loses data** (IFC4-only classes become proxies in IFC2X3; some
  property/type info may drop). Best: ask all parties to export the same version.
- **Non-georeferenced models with different origins** are simply placed origin on origin.
  If a consultant modelled "somewhere else", fix the placement in the source or in a viewer first.
- Storeys are matched on **name + elevation**, not on geometry. "Level 1" vs "1e verdieping" are two storeys.
  Rename in the source if needed, or untick storey combining and tidy afterwards.
- Properties on duplicate storeys/buildings are moved to the kept one. If both had a pset with the same
  name you end up with two psets with that name on one storey. Harmless, but visible.
- Owner history, classification systems, styles and types are **not** de-duplicated across models.
  `ifcpatch` has `MergeDuplicateTypes` / `MergeStyles` / `Optimise` recipes if the file gets too big.
- Very large models (hundreds of MB) take a few minutes and a lot of RAM; this is normal for IfcOpenShell.

## Files

- `ifc_merge_app.py` – the GUI
- `merge_core.py` – the merge logic (also a CLI)
- `tests/make_test_models.py` – generates small test models (3 disciplines of one building + a second building,
  mixed IFC2X3/IFC4 and mm/m) to try the tool
