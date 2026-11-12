"""Open-and-edit, step one: INGEST — a Description File FROM an asset.

Joshua's second mode (2026-11-10): open an existing object and edit it.
Ingest measures the asset and writes the description; from there every
existing tool applies (render window, plan gate, Blender exporter), and
edits stay parameter changes on the derived description. The original
file is never touched.

  .glb   parsed directly (stdlib): node names, translations, and the
         POSITION accessors' min/max give each part's box. glTF is
         Y-up metres; the shell is Z-up centimetres — converted here.
         Node rotations are NOT unfolded into the boxes (noted in the
         description's summary when present).
  .blend measured by Blender itself, headless: a small script dumps each
         mesh's world-space bounds as JSON.
"""
from __future__ import annotations

import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

from . import description as desc_mod
from .blender_export import find_blender


def _sanitize_id(name: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(name).lower()).strip("_")
    if not slug:
        slug = fallback
    if slug[0].isdigit():
        slug = "p_" + slug
    return slug


def _unique_id(base: str, seen: set[str]) -> str:
    candidate, n = base, 2
    while candidate in seen:
        candidate = f"{base}_{n}"
        n += 1
    seen.add(candidate)
    return candidate


def _gltf_to_shell(vec: list[float]) -> list[float]:
    """glTF (x, y-up, z) metres -> shell (x, y, z-up) centimetres."""
    return [vec[0] * 100.0, -vec[2] * 100.0, vec[1] * 100.0]


def parse_glb(path: str | Path) -> dict:
    """Read the JSON chunk of a binary glTF file."""
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"{path} is not a GLB file (bad magic)")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ValueError("GLB's first chunk is not JSON")
    return json.loads(data[20:20 + chunk_len])


def ingest_glb(path: str | Path) -> dict:
    gltf = parse_glb(path)
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])

    parents: dict[int, int] = {}
    for i, node in enumerate(nodes):
        for child in node.get("children", []) or []:
            parents[child] = i

    def world_translation(idx: int) -> list[float]:
        total = [0.0, 0.0, 0.0]
        seen = set()
        cur = idx
        while cur is not None and cur not in seen:
            seen.add(cur)
            t = nodes[cur].get("translation", [0, 0, 0])
            total = [total[i] + float(t[i]) for i in range(3)]
            cur = parents.get(cur)
        return total

    parts: list[dict] = []
    seen_ids: set[str] = set()
    rotated = False
    for i, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        mesh = meshes[node["mesh"]] if node["mesh"] < len(meshes) else {}
        prims = mesh.get("primitives", [])
        if not prims:
            continue
        pos_acc = accessors[prims[0]["attributes"]["POSITION"]]
        mn = [float(v) for v in pos_acc["min"]]
        mx = [float(v) for v in pos_acc["max"]]
        scale = [float(v) for v in node.get("scale", [1, 1, 1])]
        if "rotation" in node:
            rotated = True
        trans = world_translation(i)
        lo_gltf = [trans[k] + mn[k] * scale[k] for k in range(3)]
        size_gltf = [(mx[k] - mn[k]) * scale[k] for k in range(3)]
        lo = _gltf_to_shell(lo_gltf)
        size = [size_gltf[0] * 100.0, size_gltf[2] * 100.0, size_gltf[1] * 100.0]
        # shell y comes from glTF -z, so the shell min-y is -(glTF z max)
        lo[1] = -(lo_gltf[2] + size_gltf[2]) * 100.0
        name = node.get("name") or mesh.get("name") or f"part_{i}"
        parts.append({
            "id": _unique_id(_sanitize_id(name, f"part_{i}"), seen_ids),
            "type": "imported",
            "shape": "box",
            "position_cm": [round(v, 2) for v in lo],
            "size_cm": [round(v, 2) for v in size],
        })
    if not parts:
        raise ValueError(f"no mesh nodes found in {path}")
    stem = Path(path).stem
    notes = f"Ingested from {Path(path).name}: {len(parts)} mesh parts measured as boxes."
    if rotated:
        notes += " Some nodes carry rotations that were NOT unfolded into the boxes — check sizes in the render window."
    desc = {
        "name": stem.replace("_", " ").replace("-", " ").title(),
        "kind": "scene",
        "summary": notes,
        "parts": parts,
        "constraints": [],
        "cameras": [],
        "budget": {"preview_px": 640},
        "rubric": [],
        "style": {},
        "tags": ["ingested"],
    }
    problems = desc_mod.validate(desc)
    if problems:
        raise ValueError("ingested description failed validation: " + "; ".join(problems))
    return desc


_BLEND_DUMP_SCRIPT = r'''
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out_path = argv[0]
rows = []
for obj in bpy.context.scene.objects:
    if obj.type != "MESH":
        continue
    corners = [obj.matrix_world @ __import__("mathutils").Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]; ys = [c.y for c in corners]; zs = [c.z for c in corners]
    rows.append({
        "name": obj.name,
        "min_m": [min(xs), min(ys), min(zs)],
        "size_m": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
    })
with open(out_path, "w") as fh:
    json.dump(rows, fh)
print("DS_INGEST", len(rows))
'''


def ingest_blend(path: str | Path) -> dict:
    exe = find_blender()
    if exe is None:
        raise RuntimeError(
            "Blender was not found, so the .blend file cannot be measured. "
            "Install Blender (or set DESIGNSHELL_BLENDER) and try again.")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "ingest_dump.py"
        script.write_text(_BLEND_DUMP_SCRIPT, encoding="utf-8")
        out_json = Path(tmp) / "objects.json"
        proc = subprocess.run(
            [exe, "--background", str(path), "--python", str(script), "--", str(out_json)],
            capture_output=True, text=True, timeout=600)
        if not out_json.exists():
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-6:])
            raise RuntimeError(f"Blender could not read {path}. Last lines:\n{tail}")
        rows = json.loads(out_json.read_text(encoding="utf-8"))
    parts: list[dict] = []
    seen_ids: set[str] = set()
    for i, row in enumerate(rows):
        parts.append({
            "id": _unique_id(_sanitize_id(row["name"], f"part_{i}"), seen_ids),
            "type": "imported",
            "shape": "box",
            "position_cm": [round(v * 100, 2) for v in row["min_m"]],
            "size_cm": [round(v * 100, 2) for v in row["size_m"]],
        })
    if not parts:
        raise ValueError(f"no mesh objects found in {path}")
    stem = Path(path).stem
    desc = {
        "name": stem.replace("_", " ").replace("-", " ").title(),
        "kind": "scene",
        "summary": f"Ingested from {Path(path).name}: {len(parts)} mesh objects measured in Blender as boxes.",
        "parts": parts,
        "constraints": [],
        "cameras": [],
        "budget": {"preview_px": 640},
        "rubric": [],
        "style": {},
        "tags": ["ingested"],
    }
    problems = desc_mod.validate(desc)
    if problems:
        raise ValueError("ingested description failed validation: " + "; ".join(problems))
    return desc


def ingest(path: str | Path) -> dict:
    suffix = Path(path).suffix.lower()
    if suffix == ".glb":
        return ingest_glb(path)
    if suffix == ".blend":
        return ingest_blend(path)
    raise ValueError(f"ingest supports .glb and .blend files, not {suffix!r}")
