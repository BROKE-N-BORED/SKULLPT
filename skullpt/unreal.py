"""The Unreal hop — a package Unreal can import, with lineage.

Given a built scene job, this assembles <dest>/<slug>-unreal/:

  <slug>.glb (+ .blend, stats, LOD + collision GLBs when present)
  description.json        the source Description File
  import_into_unreal.py   an Unreal Editor Python script: imports the
                          GLB to /Game/SKULLPT/<slug>/ via an
                          AssetImportTask and writes import-result.json
  lineage.json            source description hash -> package contents ->
                          Unreal destination, so a source asset and its
                          in-project instance stay linked
  README.txt              the exact steps, in plain words

Honest limit: the shell cannot run Unreal on the build machine, so the
import script is generated and syntax-checked, and lineage records
"imported": false until the script's import-result.json says otherwise.
The engine run is Joshua's, on his PC, when Unreal is installed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .blender_export import _slug
from .governor import stamp

_IMPORT_TEMPLATE = '''"""SKULLPT -> Unreal import. Run inside the Unreal Editor:
Tools > Execute Python Script, pick this file. Or headless:
UnrealEditor-Cmd "<project>.uproject" -ExecutePythonScript="<this file>"
Imports the packaged GLB to {dest} and writes import-result.json.
"""
import json
import os

import unreal

PACKAGE_DIR = r"{package_dir}"
GLB_NAME = "{glb_name}"
DESTINATION = "{dest}"


def main():
    glb_path = os.path.join(PACKAGE_DIR, GLB_NAME)
    task = unreal.AssetImportTask()
    task.filename = glb_path
    task.destination_path = DESTINATION
    task.automated = True
    task.save = True
    task.replace_existing = True
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    tools.import_asset_tasks([task])
    imported = [str(p) for p in task.get_editor_property("imported_object_paths")]
    result = {{"glb": GLB_NAME, "destination": DESTINATION, "imported": imported}}
    out = os.path.join(PACKAGE_DIR, "import-result.json")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    unreal.log("SKULLPT import: " + json.dumps(result))


main()
'''


def package_unreal(job, dest_root: str | Path) -> Path:
    """Assemble the Unreal package for a built job; return its folder.

    A job that carries a real mesh (cloud-mesh GLB or an ingested
    .glb/.obj/.fbx — T21) has that mesh packaged too: it is copied in
    as <slug>-mesh.<ext> and, when the job has no built GLB output
    yet, it becomes the main asset the import script imports — a
    cloud-meshed job reaches Unreal without a Blender build first.
    An .stl source is copied for reference but never the main asset
    (Unreal's importer does not take STL)."""
    desc = job.load_description()
    slug = _slug(desc["name"])
    dest = Path(dest_root) / f"{slug}-unreal"
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    main_glb = None
    for path in sorted(job.output_dir.iterdir()):
        if path.suffix not in (".glb", ".blend", ".json"):
            continue
        if path.suffix == ".json" and not path.name.endswith(".stats.json"):
            continue
        shutil.copy2(path, dest / path.name)
        copied.append(path.name)
        if path.name == f"{slug}.glb":
            main_glb = path.name
    if main_glb is None:
        glbs = [n for n in copied if n.endswith(".glb")
                and "_lod" not in n and "_collision" not in n]
        if glbs:
            main_glb = glbs[0]

    # The job's own mesh (T21): resolve it without failing the package
    # when the recorded file is gone — the built outputs may still be
    # enough on their own.
    mesh_name = None
    mesh_is_main = False
    try:
        from .blender_export import _resolve_mesh_file
        resolved = _resolve_mesh_file(desc, job.output_dir)
    except RuntimeError:
        resolved = None
    if resolved is not None:
        mesh_path, mesh_fmt, _scale = resolved
        mesh_name = f"{slug}-mesh.{mesh_fmt}"
        shutil.copy2(mesh_path, dest / mesh_name)
        copied.append(mesh_name)
        if main_glb is None and mesh_fmt in ("glb", "fbx", "obj"):
            main_glb = mesh_name
            mesh_is_main = True
    if main_glb is None:
        raise RuntimeError(
            "this job has no GLB output to package — build the scene first")

    desc_bytes = job.desc_path.read_bytes()
    shutil.copy2(job.desc_path, dest / "description.json")
    digest = hashlib.sha256(desc_bytes).hexdigest()

    destination = f"/Game/SKULLPT/{slug}"
    script = _IMPORT_TEMPLATE.format(
        package_dir=str(dest), glb_name=main_glb, dest=destination)
    (dest / "import_into_unreal.py").write_text(script, encoding="utf-8")

    lineage = {
        "asset": desc["name"],
        "slug": slug,
        "source_description_sha256": digest,
        "source_job": job.dir.name,
        "package_files": sorted(copied + ["description.json",
                                          "import_into_unreal.py",
                                          "lineage.json", "README.txt"]),
        "unreal_destination": destination,
        "imported": False,
        "packaged": stamp(),
        "note": "imported flips to true when import-result.json appears "
                "after the script runs inside Unreal Editor.",
    }
    if mesh_name is not None:
        lineage["mesh_source"] = mesh_name
        lineage["main_asset_is_source_mesh"] = mesh_is_main
    (dest / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
    mesh_line = ""
    if mesh_is_main:
        mesh_line = (f"\nThis job has no Blender build yet, so the main "
                     f"asset is the source mesh itself ({mesh_name}) — the "
                     f"mesh the job was made from.\n")
    (dest / "README.txt").write_text(
        f"Unreal package for {desc['name']}\n\n"
        f"1. Open your Unreal project.\n"
        f"2. In the editor: Tools > Execute Python Script, choose "
        f"import_into_unreal.py from this folder.\n"
        f"3. The model imports to {destination}. UCX_-named meshes in the "
        f"GLB become collision there.\n"
        f"4. import-result.json appears in this folder listing what "
        f"Unreal created — that is the proof of import.\n"
        f"{mesh_line}",
        encoding="utf-8")
    return dest
