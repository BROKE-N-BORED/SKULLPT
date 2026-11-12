"""The Unreal hop — a package Unreal can import, with lineage.

Given a built scene job, this assembles <dest>/<slug>-unreal/:

  <slug>.glb (+ .blend, stats, LOD + collision GLBs when present)
  description.json        the source Description File
  import_into_unreal.py   an Unreal Editor Python script: imports the
                          GLB to /Game/DesignShell/<slug>/ via an
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

_IMPORT_TEMPLATE = '''"""Design Shell -> Unreal import. Run inside the Unreal Editor:
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
    unreal.log("DesignShell import: " + json.dumps(result))


main()
'''


def package_unreal(job, dest_root: str | Path) -> Path:
    """Assemble the Unreal package for a built job; return its folder."""
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
        if not glbs:
            raise RuntimeError(
                "this job has no GLB output to package — build the scene first")
        main_glb = glbs[0]

    desc_bytes = job.desc_path.read_bytes()
    shutil.copy2(job.desc_path, dest / "description.json")
    digest = hashlib.sha256(desc_bytes).hexdigest()

    destination = f"/Game/DesignShell/{slug}"
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
    (dest / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
    (dest / "README.txt").write_text(
        f"Unreal package for {desc['name']}\n\n"
        f"1. Open your Unreal project.\n"
        f"2. In the editor: Tools > Execute Python Script, choose "
        f"import_into_unreal.py from this folder.\n"
        f"3. The model imports to {destination}. UCX_-named meshes in the "
        f"GLB become collision there.\n"
        f"4. import-result.json appears in this folder listing what "
        f"Unreal created — that is the proof of import.\n",
        encoding="utf-8")
    return dest
