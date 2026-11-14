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
  .obj   parsed directly (stdlib): groups/objects become parts, faces
         give the bounds. OBJ is Y-up; converted like glTF. One unit
         is read as one centimetre (said plainly in the summary —
         OBJ files carry no unit of their own).
  .stl   parsed directly (stdlib), binary and ASCII: facets give the
         bounds, one part per solid. STL is Z-up; read as-is. One
         unit is read as one centimetre (said plainly in the summary).
  .fbx   measured by Blender itself, headless: the file is imported
         into an empty scene and each mesh's world-space bounds are
         dumped as JSON (Blender reads FBX units and converts them).
  .blend measured by Blender itself, headless: a small script dumps each
         mesh's world-space bounds as JSON.

Mesh preservation (v1.6, T30): ingest used to reduce everything to
bounding boxes and throw the mesh away. Now the source mesh is kept —
copied into <workspace>/meshes/ when a workspace is given — and the
description records it in a "mesh" block, so the Blender build imports
the real mesh instead of rebuilding the boxes (T21). Each part also
carries its measured mesh data (source format, node, vertex and
triangle counts) so the budget gate judges the real mesh, not a
12-triangle box. .blend stays boxes-only: a .blend is a whole scene,
not a mesh file, and the build has no .blend importer — its parts
keep the box estimate, which is what the build actually produces.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from . import description as desc_mod
from .blender_export import find_blender

# Formats the build can import from a "mesh" block (kept in sync with
# description.MESH_FORMATS). .obj/.stl are measured as centimetres on
# ingest but Blender imports one source unit as one metre, so those
# two carry an import scale of 0.01; .glb/.fbx need none.
IMPORT_SCALE = {"glb": 1.0, "fbx": 1.0, "obj": 0.01, "stl": 0.01}


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


def _finish_desc(path: str | Path, parts: list[dict], notes: str,
                 mesh: dict | None = None) -> dict:
    """The shared description shape for every ingest path, validated
    before it goes back — an ingested description that fails its own
    validation is a bug in ingest, not a job to file."""
    if not parts:
        raise ValueError(f"no mesh parts found in {path}")
    stem = Path(path).stem
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
    if mesh is not None:
        desc["mesh"] = mesh
    problems = desc_mod.validate(desc)
    if problems:
        raise ValueError("ingested description failed validation: " + "; ".join(problems))
    return desc


def _preserve_mesh(path: str | Path, workspace: str | Path | None, fmt: str) -> dict:
    """The "mesh" block for an ingested asset: where the real mesh lives
    so the build can import it. Inside a workspace the source is copied
    to <workspace>/meshes/ (the original is never touched, and a
    same-named file with different bytes gets a _2 suffix instead of
    overwriting); without a workspace the absolute source path is
    recorded and the build imports it if it still exists."""
    src = Path(path)
    scale = IMPORT_SCALE[fmt]
    if workspace is None:
        return {"file": str(src.resolve()), "format": fmt, "import_scale": scale}
    dest_dir = Path(workspace) / "meshes"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists() and dest.read_bytes() != src.read_bytes():
        n = 2
        while (dest_dir / f"{src.stem}_{n}{src.suffix}").exists():
            n += 1
        dest = dest_dir / f"{src.stem}_{n}{src.suffix}"
    if not dest.exists():
        shutil.copyfile(src, dest)
    return {"file": f"meshes/{dest.name}", "format": fmt, "import_scale": scale}


def parse_glb(path: str | Path) -> dict:
    """Read the JSON chunk of a binary glTF file."""
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"{path} is not a GLB file (bad magic)")
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ValueError("GLB's first chunk is not JSON")
    return json.loads(data[20:20 + chunk_len])


def ingest_glb(path: str | Path, workspace: str | Path | None = None) -> dict:
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
        # Measured mesh data (T30): the POSITION count is the vertex
        # count; the triangle count comes from the index accessor when
        # there is one, else a third of the vertex count (non-indexed).
        mesh_info: dict = {"source": "glb", "node": i}
        vert_count = pos_acc.get("count")
        if isinstance(vert_count, int):
            mesh_info["vertices"] = vert_count
        idx_ref = prims[0].get("indices")
        if isinstance(idx_ref, int) and idx_ref < len(accessors):
            idx_count = accessors[idx_ref].get("count")
            if isinstance(idx_count, int):
                mesh_info["triangles"] = idx_count // 3
        elif isinstance(vert_count, int):
            mesh_info["triangles"] = vert_count // 3
        parts.append({
            "id": _unique_id(_sanitize_id(name, f"part_{i}"), seen_ids),
            "type": "imported",
            "shape": "box",
            "position_cm": [round(v, 2) for v in lo],
            "size_cm": [round(v, 2) for v in size],
            "mesh": mesh_info,
        })
    notes = f"Ingested from {Path(path).name}: {len(parts)} mesh parts measured as boxes."
    if rotated:
        notes += " Some nodes carry rotations that were NOT unfolded into the boxes — check sizes in the render window."
    notes += " The source mesh is kept and the build imports it; the boxes are only the measurements."
    return _finish_desc(path, parts, notes,
                        mesh=_preserve_mesh(path, workspace, "glb"))


def ingest_obj(path: str | Path, workspace: str | Path | None = None) -> dict:
    """Wavefront .obj, parsed with the stdlib. Groups ("g"/"o") become
    parts; a file with no groups becomes one part. OBJ is Y-up, so the
    same conversion as glTF applies; one unit is read as one
    centimetre (OBJ files carry no unit of their own)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    vertices: list[tuple[float, float, float]] = []
    groups: dict[str, list[list[int]]] = {}
    order: list[str] = []
    current = None

    def group_faces(name: str) -> list[list[int]]:
        if name not in groups:
            groups[name] = []
            order.append(name)
        return groups[name]

    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        head = tokens[0]
        if head == "v" and len(tokens) >= 4:
            try:
                vertices.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
            except ValueError:
                continue
        elif head in ("o", "g") and len(tokens) >= 2:
            current = " ".join(tokens[1:])
            group_faces(current)
        elif head == "f" and len(tokens) >= 4:
            if current is None:
                current = Path(path).stem
            face = []
            for tok in tokens[1:]:
                try:
                    idx = int(tok.split("/")[0])
                except ValueError:
                    continue
                # OBJ indices are 1-based; negative ones count back
                # from the most recent vertex.
                face.append(idx - 1 if idx > 0 else len(vertices) + idx)
            if len(face) >= 3:
                group_faces(current).append(face)
    if not vertices:
        raise ValueError(f"no vertices found in {path}")
    parts: list[dict] = []
    seen_ids: set[str] = set()
    for gi, name in enumerate(order):
        faces = groups[name]
        if not faces:
            continue
        used = sorted({idx for face in faces for idx in face
                       if 0 <= idx < len(vertices)})
        if not used:
            continue
        # Y-up -> Z-up, 1 unit = 1 cm: shell (x, -z, y).
        shell = [(vertices[i][0], -vertices[i][2], vertices[i][1]) for i in used]
        xs = [v[0] for v in shell]; ys = [v[1] for v in shell]; zs = [v[2] for v in shell]
        tris = sum(len(f) - 2 for f in faces)
        parts.append({
            "id": _unique_id(_sanitize_id(name, f"part_{gi}"), seen_ids),
            "type": "imported",
            "shape": "box",
            "position_cm": [round(min(xs), 2), round(min(ys), 2), round(min(zs), 2)],
            "size_cm": [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2),
                        round(max(zs) - min(zs), 2)],
            "mesh": {"source": "obj", "node": gi, "vertices": len(used), "triangles": tris},
        })
    notes = (f"Ingested from {Path(path).name}: {len(parts)} parts measured as boxes. "
             "Measured assuming 1 unit = 1 cm — check the size in the render window. "
             "The source mesh is kept and the build imports it; the boxes are only the measurements.")
    return _finish_desc(path, parts, notes,
                        mesh=_preserve_mesh(path, workspace, "obj"))


def _stl_triangles(data: bytes) -> tuple[list[dict], bool]:
    """Parse an .stl file (binary or ASCII) into solids:
    [{"name": str, "tris": [(v0, v1, v2), ...]}]. Returns (solids,
    is_ascii). Raises ValueError when neither format parses."""
    # ASCII starts with "solid" and contains facet lines; a binary
    # file can coincidentally start with "solid" too, so the facet
    # check decides.
    if data[:5] == b"solid" and b"facet" in data[:4096]:
        text = data.decode("utf-8", errors="replace")
        solids: list[dict] = []
        current: dict | None = None
        verts: list[tuple[float, float, float]] = []
        for line in text.splitlines():
            tokens = line.split()
            if not tokens:
                continue
            if tokens[0] == "solid":
                current = {"name": " ".join(tokens[1:]) or "solid", "tris": []}
                solids.append(current)
            elif tokens[0] == "vertex" and len(tokens) >= 4:
                try:
                    verts.append((float(tokens[1]), float(tokens[2]), float(tokens[3])))
                except ValueError:
                    continue
                if len(verts) == 3:
                    if current is None:
                        current = {"name": "solid", "tris": []}
                        solids.append(current)
                    current["tris"].append(tuple(verts))
                    verts = []
        solids = [s for s in solids if s["tris"]]
        if solids:
            return solids, True
    # Binary: 80-byte header, uint32 count, 50-byte records.
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if len(data) >= 84 + 50 * count:
            tris = []
            for i in range(count):
                base = 84 + 50 * i
                # Each 50-byte record: normal (12 bytes) + 3 vertices
                # (36 bytes) + attribute (2 bytes). The normal is
                # skipped; the 9 floats after it are the triangle.
                coords = struct.unpack_from("<9f", data, base + 12)
                tris.append(((coords[0], coords[1], coords[2]),
                             (coords[3], coords[4], coords[5]),
                             (coords[6], coords[7], coords[8])))
            if tris:
                return [{"name": "solid", "tris": tris}], False
    raise ValueError("not a readable STL file (neither ASCII nor binary)")


def ingest_stl(path: str | Path, workspace: str | Path | None = None) -> dict:
    """STL, binary or ASCII, parsed with the stdlib. One part per solid
    (ASCII) or one part (binary). STL is Z-up, read as-is; one unit is
    read as one centimetre (said plainly in the summary)."""
    data = Path(path).read_bytes()
    solids, _is_ascii = _stl_triangles(data)
    parts: list[dict] = []
    seen_ids: set[str] = set()
    for si, solid in enumerate(solids):
        verts = [v for tri in solid["tris"] for v in tri]
        xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
        parts.append({
            "id": _unique_id(_sanitize_id(solid["name"], f"part_{si}"), seen_ids),
            "type": "imported",
            "shape": "box",
            "position_cm": [round(min(xs), 2), round(min(ys), 2), round(min(zs), 2)],
            "size_cm": [round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2),
                        round(max(zs) - min(zs), 2)],
            "mesh": {"source": "stl", "node": si,
                     "vertices": len(verts), "triangles": len(solid["tris"])},
        })
    notes = (f"Ingested from {Path(path).name}: {len(parts)} parts measured as boxes. "
             "Measured assuming 1 unit = 1 cm — check the size in the render window. "
             "The source mesh is kept and the build imports it; the boxes are only the measurements.")
    return _finish_desc(path, parts, notes,
                        mesh=_preserve_mesh(path, workspace, "stl"))


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


def ingest_blend(path: str | Path, workspace: str | Path | None = None) -> dict:
    """A .blend is a whole scene, not a mesh file: it stays boxes-only
    (no "mesh" block — the build has no .blend importer, so the box
    estimate is what the build actually produces). The workspace
    argument exists only so the dispatcher can call every ingester
    the same way; it is not used here."""
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
    notes = (f"Ingested from {Path(path).name}: {len(parts)} mesh objects measured in Blender as boxes.")
    return _finish_desc(path, parts, notes)


# FBX: Blender imports the file into an empty scene (it reads the
# file's own units and converts them to metres), then the same bounds
# dump as .blend — plus vertex and triangle counts, which ARE the
# build's counts here because the build imports the same .fbx.
_FBX_DUMP_SCRIPT = r'''
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
fbx_path, out_path = argv[0], argv[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=fbx_path)
rows = []
for obj in bpy.context.scene.objects:
    if obj.type != "MESH":
        continue
    corners = [obj.matrix_world @ __import__("mathutils").Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]; ys = [c.y for c in corners]; zs = [c.z for c in corners]
    tris = sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons)
    rows.append({
        "name": obj.name,
        "min_m": [min(xs), min(ys), min(zs)],
        "size_m": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
        "vertices": len(obj.data.vertices),
        "triangles": tris,
    })
with open(out_path, "w") as fh:
    json.dump(rows, fh)
print("DS_INGEST", len(rows))
'''


def ingest_fbx(path: str | Path, workspace: str | Path | None = None) -> dict:
    """FBX via Blender's own importer — writing a binary FBX parser
    would be a project of its own, and Blender already does it right
    (units, axis conversion, hierarchy)."""
    exe = find_blender()
    if exe is None:
        raise RuntimeError(
            "Blender was not found, so the .fbx file cannot be measured. "
            "Install Blender (or set DESIGNSHELL_BLENDER) and try again.")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "ingest_fbx.py"
        script.write_text(_FBX_DUMP_SCRIPT, encoding="utf-8")
        out_json = Path(tmp) / "objects.json"
        proc = subprocess.run(
            [exe, "--background", "--python", str(script), "--",
             str(path), str(out_json)],
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
            "mesh": {"source": "fbx", "node": i,
                     "vertices": row["vertices"], "triangles": row["triangles"]},
        })
    notes = (f"Ingested from {Path(path).name}: {len(parts)} mesh objects measured in Blender as boxes. "
             "The source mesh is kept and the build imports it; the boxes are only the measurements.")
    return _finish_desc(path, parts, notes,
                        mesh=_preserve_mesh(path, workspace, "fbx"))


def ingest(path: str | Path, workspace: str | Path | None = None) -> dict:
    """Derive a description from an existing asset. With a workspace,
    the source mesh is preserved into <workspace>/meshes/ and the
    description records it; without one, the absolute source path is
    recorded instead."""
    suffix = Path(path).suffix.lower()
    if suffix == ".glb":
        return ingest_glb(path, workspace)
    if suffix == ".obj":
        return ingest_obj(path, workspace)
    if suffix == ".stl":
        return ingest_stl(path, workspace)
    if suffix == ".fbx":
        return ingest_fbx(path, workspace)
    if suffix == ".blend":
        return ingest_blend(path, workspace)
    raise ValueError(f"ingest supports .glb, .obj, .stl, .fbx and .blend files, not {suffix!r}")
