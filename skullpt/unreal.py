"""The Unreal hop - a package Unreal can import, with lineage.

Given a built scene job, this assembles <dest>/<slug>-unreal/:

  <slug>.glb (+ .blend, stats, LOD + collision GLBs when present)
  <slug>-<part>-<map>.png  baked PBR maps (v1.13, M12), when the job
                          has been baked - colour, roughness, normal,
                          height, metallic
  description.json        the source Description File
  import_into_unreal.py   an Unreal Editor Python script: imports the
                          GLB (and the baked maps, when present) to
                          /Game/SKULLPT/<slug>/ via AssetImportTasks
                          and writes import-result.json
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
Imports the packaged GLB (and the baked PBR maps, when the job was
baked) to {dest} and writes import-result.json.
"""
import json
import os

import unreal

PACKAGE_DIR = r"{package_dir}"
GLB_NAME = "{glb_name}"
DESTINATION = "{dest}"
# (file name, sRGB on?) - colour maps are sRGB; roughness, normal,
# height and metallic are data, so sRGB is off for them.
TEXTURES = {textures}


def main():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    glb_path = os.path.join(PACKAGE_DIR, GLB_NAME)
    task = unreal.AssetImportTask()
    task.filename = glb_path
    task.destination_path = DESTINATION
    task.automated = True
    task.save = True
    task.replace_existing = True
    tools.import_asset_tasks([task])
    imported = [str(p) for p in task.get_editor_property("imported_object_paths")]
    textures = []
    for tex_file, srgb in TEXTURES:
        ttask = unreal.AssetImportTask()
        ttask.filename = os.path.join(PACKAGE_DIR, tex_file)
        ttask.destination_path = DESTINATION
        ttask.automated = True
        ttask.save = True
        ttask.replace_existing = True
        tools.import_asset_tasks([ttask])
        for p in ttask.get_editor_property("imported_object_paths"):
            textures.append(str(p))
            if not srgb:
                # A data map imported as sRGB renders wrong. The
                # flag is best-effort: a failure here must not kill
                # the import - the texture is still in the project.
                try:
                    asset = unreal.load_asset(str(p))
                    if asset is not None:
                        asset.set_editor_property("srgb", False)
                except Exception:
                    pass
    result = {{"glb": GLB_NAME, "destination": DESTINATION,
               "imported": imported, "textures": textures}}
    out = os.path.join(PACKAGE_DIR, "import-result.json")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    unreal.log("SKULLPT import: " + json.dumps(result))


main()
'''


def _is_baked_map(path: Path, slug: str) -> bool:
    """A baked PBR map in the job's output folder: <slug>-<part>-<map>
    .png, where <map> is one of the bake map names. Render PNGs are
    render-*.png, so the slug prefix keeps them out."""
    if path.suffix != ".png" or not path.name.startswith(slug + "-"):
        return False
    stem = path.name[:-4]
    map_name = stem.rsplit("-", 1)[-1]
    return map_name in ("color", "roughness", "normal", "height",
                        "metallic")


def package_unreal(job, dest_root: str | Path) -> Path:
    """Assemble the Unreal package for a built job; return its folder.

    A job that carries a real mesh (cloud-mesh GLB or an ingested
    .glb/.obj/.fbx - T21) has that mesh packaged too: it is copied in
    as <slug>-mesh.<ext> and, when the job has no built GLB output
    yet, it becomes the main asset the import script imports - a
    cloud-meshed job reaches Unreal without a Blender build first.
    An .stl source is copied for reference but never the main asset
    (Unreal's importer does not take STL)."""
    desc = job.load_description()
    slug = _slug(desc["name"])
    dest = Path(dest_root) / f"{slug}-unreal"
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    textures: list[str] = []
    main_glb = None
    for path in sorted(job.output_dir.iterdir()):
        if path.suffix == ".png" and _is_baked_map(path, slug):
            # Baked PBR maps (v1.13, M12): the imported asset
            # carries its textures.
            shutil.copy2(path, dest / path.name)
            copied.append(path.name)
            textures.append(path.name)
            continue
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
    # when the recorded file is gone - the built outputs may still be
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
            "this job has no GLB output to package - build the scene first")

    desc_bytes = job.desc_path.read_bytes()
    shutil.copy2(job.desc_path, dest / "description.json")
    digest = hashlib.sha256(desc_bytes).hexdigest()

    destination = f"/Game/SKULLPT/{slug}"
    # Colour maps are sRGB; the data maps are not.
    texture_list = [(name, name.endswith("-color.png"))
                    for name in textures]
    script = _IMPORT_TEMPLATE.format(
        package_dir=str(dest), glb_name=main_glb, dest=destination,
        textures=repr(texture_list))
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
    if textures:
        lineage["textures"] = textures
    (dest / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
    mesh_line = ""
    if mesh_is_main:
        mesh_line = (f"\nThis job has no Blender build yet, so the main "
                     f"asset is the source mesh itself ({mesh_name}) - the "
                     f"mesh the job was made from.\n")
    texture_line = ""
    if textures:
        texture_line = (f"\nThis package also carries {len(textures)} "
                        f"baked texture maps (colour, roughness, normal, "
                        f"height, metallic) - they import alongside the "
                        f"model, into the same folder.\n")
    (dest / "README.txt").write_text(
        f"Unreal package for {desc['name']}\n\n"
        f"1. Open your Unreal project.\n"
        f"2. In the editor: Tools > Execute Python Script, choose "
        f"import_into_unreal.py from this folder.\n"
        f"3. The model imports to {destination}. UCX_-named meshes in the "
        f"GLB become collision there.\n"
        f"4. import-result.json appears in this folder listing what "
        f"Unreal created - that is the proof of import.\n"
        f"{mesh_line}{texture_line}",
        encoding="utf-8")
    return dest
