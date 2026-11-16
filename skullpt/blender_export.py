"""The making engine, step one: Description File -> real 3D scene.

Generates Blender Python scripts from the description (parts become real
meshes at true scale, cameras come from the description's own `cameras`,
lights from the style), runs Blender headless, and collects the outputs:

    <slug>.blend            the scene, openable in Blender
    <slug>.glb              the model, for Unreal / the web / anywhere
    render-<camera>.png     one render per saved camera + an auto iso
    <slug>.blender.py       the exact build script (kept, auditable)
    <slug>.render.py        the exact render script (kept, auditable)

Fail-loud contract: if Blender is not installed, this raises with a plain
message and the job fails - it never pretends a scene was built.

Two phases (v1.6, P3): the BUILD script makes the scene (geometry,
materials, stats, LODs, collision, GLB, .blend) and the RENDER script
opens the saved .blend and renders the cameras. A render-engine
fallback re-runs renders only - the build is not repeated.

Rendering tries EEVEE first (fast) and falls back to Cycles CPU when the
EEVEE renders do not land (headless machines without a GPU context),
which is recorded in the outputs note file. Before the full run, a
tiny EEVEE probe (v1.6, P4) decides whether EEVEE can run at all on
this machine, so a dead EEVEE never burns its full timeout.

A job that carries a real mesh (cloud-mesh GLB, or an ingested .glb /
.obj / .fbx / .stl - v1.6, T21/T30) imports that mesh into the build
instead of rebuilding its stand-in boxes: the ingested boxes measured
the mesh, the build uses the mesh itself.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import materials as materials_mod

from .expand import expand_parts
from .viewer import part_box, scene_data

# Same hues as the render window, so the window and the renders agree.
_PALETTE = ["#b0763a", "#7d8c5c", "#5c7d8c", "#8c5c74", "#a39a4f", "#6b5ca5",
            "#4f8c72", "#a55c4f", "#718355", "#946b54", "#546e7a", "#7a5c8f"]


def _slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "asset"


def _color_for(part_id: str, ptype: str) -> str:
    if ptype in ("floor", "ground"):
        return "#4a3d2c"
    h = 0
    for ch in part_id:
        h = (h * 31 + ord(ch)) % (2 ** 32)
    return _PALETTE[h % len(_PALETTE)]


def _hex_rgb(hex_color: str) -> tuple[float, float, float]:
    n = int(hex_color.lstrip("#"), 16)
    return ((n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255)


# Named materials: color, metallic, roughness. A part's `material` field
# names one of these, or gives a raw "#hex" (matte, non-metal).
MATERIALS = {
    "wood": ("#7a5230", 0.0, 0.65),
    "dark_wood": ("#4e3421", 0.0, 0.7),
    "maple": ("#c9a36a", 0.0, 0.6),
    "mahogany": ("#5d2f22", 0.0, 0.55),
    "cream": ("#ece1c8", 0.0, 0.5),
    "plastic_black": ("#17181a", 0.0, 0.45),
    "metal": ("#b9bdc2", 0.9, 0.35),
    "black_metal": ("#2c2e30", 0.85, 0.4),
    "chrome": ("#d7dade", 1.0, 0.12),
    "gold": ("#c9a227", 1.0, 0.3),
    "brass": ("#a87f3f", 1.0, 0.35),
    "glass": ("#cfd8dc", 0.0, 0.08),
    "leather": ("#5a4030", 0.0, 0.8),
    "stone": ("#8d8578", 0.0, 0.9),
    "paper": ("#e8e2d2", 0.0, 0.95),
}


def resolve_material(part: dict) -> tuple[str, float, float]:
    """(hex color, metallic, roughness) for a part."""
    name = part.get("material")
    if isinstance(name, dict):
        # object form (v1.11, M1/M2): the style's base colour and
        # gloss-adjusted roughness; the metallic comes from the
        # resolved spec (metal styles are metallic, the rest are not)
        resolved = materials_mod.resolve_spec(name)
        return (materials_mod.spec_base_color(name),
                resolved.get("metallic", 0.0),
                resolved["roughness"])
    if isinstance(name, str) and name.startswith("#") and len(name) == 7:
        return name, 0.0, 0.8
    if isinstance(name, str) and name in MATERIALS:
        return MATERIALS[name]
    return _color_for(part.get("id", ""), part.get("type", "")), 0.0, 0.85


# Procedural surface textures, generated inside Blender from noise - no
# image files, so they cost no disk and run on a small PC. A part's
# `texture` field wins; otherwise the material implies one.
TEXTURE_BY_MATERIAL = {
    "wood": "wood_grain", "dark_wood": "wood_grain",
    "maple": "wood_grain", "mahogany": "wood_grain",
    "metal": "brushed_metal", "black_metal": "brushed_metal",
    "chrome": "brushed_metal", "brass": "brushed_metal", "gold": "brushed_metal",
    "leather": "worn_leather",
}


def resolve_texture(part: dict) -> str | None:
    """Procedural texture name for a part, or None for a flat material.
    An object material owns its own recipe (v1.11, M2), so it never
    takes the flat noise texture."""
    if isinstance(part.get("material"), dict):
        return None
    explicit = part.get("texture")
    if isinstance(explicit, str):
        return None if explicit == "none" else explicit
    return TEXTURE_BY_MATERIAL.get(part.get("material"))


def find_blender() -> str | None:
    """Locate a Blender executable: env override, PATH, the /opt home."""
    env = os.environ.get("DESIGNSHELL_BLENDER")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    for cand in ("/opt/blender/blender", "/usr/bin/blender", "/snap/bin/blender"):
        if Path(cand).exists():
            return cand
    return None


def _grain_rotation(size_cm, flow: float) -> tuple[float, float, float]:
    """The Mapping rotation that runs the wood grain along the part's
    longest axis (v1.11, M2): the ring wave encircles texture Z, so
    the coordinates are rotated to put the longest part axis on Z.
    `flow` (0-1) blends the grain back toward the part's own Z axis.
    Verified against real renders in the v1.11 test suite."""
    x, y, z = (float(v) for v in size_cm)
    blend = math.pi / 2.0 * (1.0 - flow)
    if x >= y and x >= z:
        return (0.0, blend, 0.0)
    if y >= x and y >= z:
        return (blend, 0.0, 0.0)
    return (0.0, 0.0, 0.0)


def _face_frame(size_cm):
    """(rotation, row_axis, unit_axis) for a bond layout (M8): the
    Brick node reads texture X and Y, so the mapping rotation puts
    the part's largest face on texture XY. The row axis is the one
    the courses stack along (the block height), the unit axis the
    one the blocks run along (the block length). Axes are 0 (X) or
    1 (Y) into the separated texture vector."""
    x, y, z = (float(v) for v in size_cm)
    if z <= x and z <= y:
        # face XY (a floor): no rotation, rows stack along Y
        return (0.0, 0.0, 0.0), 1, 0
    if y <= x and y <= z:
        # face XZ (a wall): rotate about X, rows stack along Y
        return (math.pi / 2.0, 0.0, 0.0), 1, 0
    # face YZ (a wall facing X): rotate about Y, rows stack along X
    return (0.0, math.pi / 2.0, 0.0), 0, 1


def _chevron_fold_axis(size_cm):
    """Which texture axis the chevron fold runs on (M8): the face
    axis across the grain (texture Z holds the grain after the
    grain rotation). 0 is X, 1 is Y."""
    x, y, z = (float(v) for v in size_cm)
    order = sorted(((x, 0), (y, 1), (z, 2)), reverse=True)
    longest = order[0][1]
    second = order[1][1]
    if longest == 0:
        # rotation about Y: texture X is part Z, texture Y is part Y
        return 1 if second == 1 else 0
    if longest == 1:
        # rotation about X: texture X is part X, texture Y is part Z
        return 0 if second == 0 else 1
    # identity: texture axes are the part axes
    return 0 if second == 0 else 1


def _layout_rot_axis(size_cm, map_rot):
    """Which texture axis the wood layout rotation turns about
    (M8): the face normal in the texture frame. The grain rotation
    is a single-axis rotation, so the frame it is closer to (90
    degrees or identity) decides the mapping. A result of 2 (the
    grain axis itself, only when flow is high) falls back to 0:
    rotating about the grain axis would be a no-op, while any
    perpendicular axis tilts the grain visibly."""
    x, y, z = (float(v) for v in size_cm)
    smallest = min(((x, 0), (y, 1), (z, 2)))[1]
    rx, ry, _rz = map_rot
    if abs(ry) > math.pi / 4.0:
        # ~90 deg about Y: texture X is part Z, texture Y is part Y
        axis = 1 if smallest == 1 else 0
    elif abs(rx) > math.pi / 4.0:
        # ~90 deg about X: texture X is part X, texture Y is part Z
        axis = 0 if smallest == 0 else 1
    else:
        axis = smallest
    return axis if axis < 2 else 0


def _part_material_spec(src: dict, size_cm) -> dict | None:
    """The resolved material spec for a part, or None for a string
    material. Adds the size-dependent geometry: the grain rotation
    (wood only), the bond face frame and prescale (bond layouts),
    and the chevron fold axis (chevron)."""
    material = src.get("material")
    if not isinstance(material, dict):
        return None
    resolved = materials_mod.resolve_spec(material)
    bond = resolved.get("bond")
    if resolved["family"] == "wood":
        resolved["map_rot"] = _grain_rotation(size_cm, resolved["flow"])
        if resolved["layout"] in ("parquet", "herringbone"):
            resolved["layout_rot_axis"] = _layout_rot_axis(
                size_cm, resolved["map_rot"])
    elif bond:
        rot, row, unit = _face_frame(size_cm)
        resolved["map_rot"] = rot
        resolved["bond_row"] = row
        resolved["bond_unit"] = unit
        cs = resolved["coord_scale"]
        # The bond prescale is UNIFORM on the face axes (one cell
        # is one block height), so the Brick node's single Mortar
        # Size is the same real width on both axes. Basket weave
        # shows the brick's depth face, so its cell is one depth.
        if bond["kind"] == "basket_weave":
            cell_cm = materials_mod.BRICK_DEPTH_CM
            width = (materials_mod.BRICK_LENGTH_CM
                     / materials_mod.BRICK_DEPTH_CM)
        else:
            cell_cm = bond["height_cm"]
            width = bond["length_cm"] / bond["height_cm"]
        # The Brick node's Width runs along texture X: when the
        # blocks run along Y (a wall facing X), width and height
        # swap so the length still runs horizontally. Headers show
        # the brick's depth face (english/flemish only).
        if unit == 0:
            brick_width, row_height = width, 1.0
            header_width = (materials_mod.BRICK_DEPTH_CM
                            / bond["height_cm"])
            header_row_height = 1.0
        else:
            brick_width, row_height = 1.0, width
            header_width = 1.0
            header_row_height = (materials_mod.BRICK_DEPTH_CM
                                 / bond["height_cm"])
        resolved["bond_prescale"] = (
            1.0 / (cell_cm / 100.0 * cs),
            1.0 / (cell_cm / 100.0 * cs),
            1.0,
        )
        resolved["brick_width"] = brick_width
        resolved["row_height"] = row_height
        resolved["header_width"] = header_width
        resolved["header_row_height"] = header_row_height
        if bond["kind"] == "basket_weave":
            # Basket parity cells are one brick length square.
            basket = 1.0 / (materials_mod.BRICK_LENGTH_CM / 100.0 * cs)
            resolved["basket_prescale"] = (basket, basket, basket)
    else:
        resolved["map_rot"] = (0.0, 0.0, 0.0)
    if resolved["layout"] == "chevron":
        fold = _chevron_fold_axis(size_cm)
        cs = resolved["coord_scale"]
        cell = 1.0 / (resolved["chevron_cell_cm"] / 100.0 * cs)
        prescale = [1.0, 1.0, 1.0]
        prescale[fold] = cell
        prescale[2] = cell
        resolved["fold_axis"] = fold
        resolved["fold_prescale"] = tuple(prescale)
        resolved["fold_unscale"] = tuple(1.0 / v for v in prescale)
    return resolved


def build_payload(desc: dict, out_dir: Path, slug: str) -> dict:
    """The payload dict the build script embeds. Split out of
    generate_script (v1.7, T26) so the character exporter can reuse
    the same scene payload and add its character block to it."""
    data = scene_data(desc)
    style = desc.get("style", {}) or {}
    surface = str(style.get("surface", "#241d16"))
    src_by_id = {p["id"]: p for p in expand_parts(desc)}
    parts = []
    for box in data["boxes"]:
        src = src_by_id.get(box["id"], {})
        color, metallic, roughness = resolve_material(src)
        parts.append({
            "id": box["id"], "type": box["type"],
            "min": box["min"], "size": box["size"],
            "shape": src.get("shape", "box"),
            "profile": src.get("profile_cm"),
            "path": src.get("path_cm"),
            "radius": src.get("radius_cm", 2),
            "bevel": src.get("bevel_cm"),
            "texture": resolve_texture(src),
            "color": color, "metallic": metallic, "roughness": roughness,
            "glass": src.get("material") == "glass",
            "glow": box["type"] in ("lamp", "light", "lantern"),
            "material_spec": _part_material_spec(src, box["size"]),
        })
    cameras = [dict(c) for c in data["cameras"]]
    b = data["bounds"]
    span = b["span"] / 100.0
    center = [v / 100.0 for v in b["center"]]
    iso_pos = [center[0] - span * 0.85, center[1] - span * 1.15, center[2] + span * 0.8]
    cameras.append({"name": "Iso (auto)", "position_cm": [v * 100 for v in iso_pos],
                    "look_at_cm": list(b["center"])})
    for cam in cameras:  # one slug rule, shared by script + exporter
        cam["file"] = "render-" + _slug(str(cam["name"])) + ".png"
    payload = {
        "parts": parts, "cameras": cameras,
        "surface": surface,
        "out": str(out_dir), "slug": slug,
        "game_ready": bool(desc.get("game_ready")),
        "res": int((desc.get("budget", {}) or {}).get("preview_px", 640)),
    }
    # The real mesh behind this job, if it has one (T21/T30): the build
    # imports the mesh itself and skips its stand-in boxes. A missing
    # mesh file fails here, loudly, before Blender starts.
    mesh = _resolve_mesh_file(desc, out_dir)
    payload["mesh_file"] = str(mesh[0]) if mesh else None
    payload["mesh_format"] = mesh[1] if mesh else None
    payload["mesh_scale"] = mesh[2] if mesh else 1.0
    return payload


def render_build_template(payload: dict) -> str:
    """Fill the build template with a payload (v1.7, T26): the payload
    dict first, then the surface colour as an RGB tuple. Same two
    replaces, same order as generate_script always did."""
    script = _BUILD_TEMPLATE.replace("__PAYLOAD__", repr(payload))
    return script.replace("__SURFACE__", repr(_hex_rgb(payload["surface"])))


def generate_script(desc: dict, out_dir: Path, slug: str) -> str:
    """Build the Blender Python script for a description. Pure function."""
    return render_build_template(build_payload(desc, out_dir, slug))


def _resolve_mesh_file(desc: dict, out_dir: Path):
    """The real mesh behind this job, if it has one: (path, format, scale).

    From the description's "mesh" block (T30 ingest) or, for a cloud-mesh
    job, its "cloud_mesh" record (T21). The file may be named relative to
    the job folder (cloud GLB, ingested copy), to the workspace
    (workspace/meshes/...) or absolute; the first existing candidate
    wins. A missing file is NOT silent - this raises with a plain
    message naming it. Returns None when the job has no mesh at all
    (a built-from-parts job).
    """
    mesh = desc.get("mesh") or {}
    if mesh.get("file"):
        name, fmt = mesh["file"], mesh.get("format", "glb")
        scale = mesh.get("import_scale", 1.0)
    elif (desc.get("cloud_mesh") or {}).get("glb"):
        name, fmt = desc["cloud_mesh"]["glb"], "glb"
        scale = 1.0
    else:
        return None
    if not isinstance(scale, (int, float)) or scale <= 0:
        scale = 1.0
    # out_dir is <workspace>/jobs/<slug>/output: parent is the job
    # folder, parent.parent.parent is the workspace root. The job
    # folder wins (v1.9, N7); the launch directory is never a
    # candidate - a stray same-named file there must not shadow the
    # job's own mesh.
    path = Path(name)
    if path.is_absolute():
        candidates = [path]
    else:
        candidates = [out_dir.parent / name,
                      out_dir.parent.parent.parent / name]
    for cand in candidates:
        if cand.exists():
            return cand, fmt, float(scale)
    raise RuntimeError(
        f"The mesh file for this job is missing: {name}. "
        "The mesh was recorded when the job was made, but the file is "
        "no longer there - re-ingest the asset or rebuild the job from "
        "its description.")


def generate_render_script(desc: dict, out_dir: Path, slug: str) -> str:
    """The RENDER script (P3): opens the .blend the build script saved
    and renders every camera in it. The engine comes from the DS_ENGINE
    environment variable (set by the caller); resolution, samples and
    denoise come from the description's resolved "render" block, with
    the pre-v1.6 budget.preview_px as the resolution fallback."""
    data = scene_data(desc)
    cameras = [{"name": cam["name"],
                "file": "render-" + _slug(str(cam["name"])) + ".png"}
               for cam in list(data["cameras"]) + [{"name": "Iso (auto)"}]]
    render = desc.get("render") or {}
    res = render.get("resolution_px")
    if not isinstance(res, int):
        res = int((desc.get("budget", {}) or {}).get("preview_px", 640))
    payload = {
        "out": str(out_dir), "slug": slug, "cameras": cameras,
        "res": res, "samples": render.get("samples", 24),
        "denoise": bool(render.get("denoise", False)),
    }
    return _RENDER_TEMPLATE.replace("__PAYLOAD__", repr(payload))


_PROBE_SCRIPT = '''
import bpy, os, sys
bpy.ops.mesh.primitive_cube_add(size=1)
bpy.ops.object.camera_add(location=(3, -3, 2))
cam = bpy.context.active_object
cam.rotation_euler = (1.0, 0.0, 0.785)
bpy.context.scene.camera = cam
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x = 64
scene.render.resolution_y = 64
scene.render.filepath = os.path.join(sys.argv[sys.argv.index("--") + 1], "probe.png")
bpy.ops.render.render(write_still=True)
print("DS_PROBE_OK")
'''


def probe_engine(exe: str, timeout: int = 60, cancel=None) -> str:
    """Ask Blender whether EEVEE can render headless on this machine
    (v1.6, P4): one cube, one 64 px render, 60 seconds. Returns
    "BLENDER_EEVEE_NEXT" when the probe render lands, "CYCLES" when
    it does not - a failed probe is information, not an error, so it
    never raises; the full run simply starts with Cycles. A set
    `cancel` event is the one exception: it kills the probe and
    raises governor.Cancelled, so a cancelled build never silently
    starts a full Cycles render (v1.9, N8)."""
    from . import governor, subproc

    try:
        with tempfile.TemporaryDirectory(prefix="skullpt-eevee-probe-") as tmp:
            script = Path(tmp) / "probe.py"
            script.write_text(_PROBE_SCRIPT)
            rc, out = subproc.run_cancellable(
                [exe, "--background", "--python", str(script), "--", tmp],
                timeout=timeout, cancel=cancel)
            if rc == 0 and "DS_PROBE_OK" in out \
                    and (Path(tmp) / "probe.png").exists():
                return "BLENDER_EEVEE_NEXT"
    except governor.Cancelled:
        raise
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "CYCLES"


def preview_material_script(spec: dict, out_dir: Path, slug: str) -> str:
    """A minimal Blender script: one sample slab with the material,
    one camera, one sun, a 128x96 render. Pure function."""
    desc = {
        "name": "material preview", "kind": "scene",
        "parts": [{"id": "preview_slab", "type": "prop", "shape": "box",
                   "position_cm": [-30, -20, 0], "size_cm": [60, 40, 4],
                   "material": spec}],
        "cameras": [], "constraints": [], "rubric": [],
        "budget": {"max_tris": 40000, "preview_px": 640},
    }
    payload = build_payload(desc, out_dir, slug)
    return _PREVIEW_TEMPLATE.replace("__PAYLOAD__", repr(payload))


def render_material_preview(spec: dict, workspace, exe=None,
                            timeout: int = 180) -> Path:
    """Render a 128x96 preview of a material spec, cached by spec
    hash under the workspace (v1.11, M3). Returns the PNG path.
    Raises RuntimeError with a plain message when Blender is
    missing or the render does not land. The cache keeps the
    newest 20 previews."""
    from . import settings as settings_mod

    ws = Path(workspace)
    cache_dir = ws / materials_mod.PREVIEW_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = materials_mod.spec_hash(spec)
    png = cache_dir / f"{key}.png"
    if png.exists():
        return png
    exe = exe or find_blender()
    if not exe:
        raise RuntimeError("Blender is not installed, so no preview "
                           "can be rendered.")
    existing = sorted(cache_dir.glob("*.png"),
                      key=lambda p: p.stat().st_mtime)
    while len(existing) >= materials_mod.PREVIEW_CACHE_LIMIT:
        existing.pop(0).unlink(missing_ok=True)
    # The remembered engine avoids a probe run per preview; a
    # machine that has never built probes once, like a build does.
    remembered = settings_mod.last_render_engine(ws).get("engine")
    engine = (remembered if remembered in ("BLENDER_EEVEE_NEXT",
                                           "CYCLES")
              else probe_engine(exe))
    with tempfile.TemporaryDirectory(prefix="skullpt-preview-") as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "preview.py"
        script_path.write_text(preview_material_script(spec, tmp_path,
                                                       key))
        env = dict(os.environ, DS_ENGINE=engine)
        try:
            subprocess.run(
                [exe, "--background", "--factory-startup", "--python",
                 str(script_path)], env=env, capture_output=True,
                text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("the preview render timed out")
        rendered = tmp_path / f"{key}.png"
        if not rendered.exists():
            raise RuntimeError("Blender ran but the preview did not "
                               "render")
        shutil.copyfile(rendered, png)
    return png


_BUILD_TEMPLATE_HEAD = r'''
import bpy, math, os, json
from mathutils import Vector

D = __PAYLOAD__
OUT = D["out"]
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# World: dark room tone from the style tokens.
world = bpy.data.worlds.new("DSWorld")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
sr, sg, sb = (max(0.0, min(1.0, float(c))) for c in __SURFACE__)
bg.inputs[0].default_value = (sr, sg, sb, 1.0)
bg.inputs[1].default_value = 0.6
'''

# The material section, shared by the build script and the preview
# script (v1.11, M2/M3): one source of truth for make_material.
_MATERIAL_TEMPLATE = r'''
def _shade(hex_color, k):
    r = int(hex_color[1:3], 16) / 255.0 * k
    g = int(hex_color[3:5], 16) / 255.0 * k
    b = int(hex_color[5:7], 16) / 255.0 * k
    return (max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b)), 1.0)

_WEAR_GROUP = None

def _wear_group():
    """The shared SKULLPT Wear node group (v1.12, M9): one group per
    .blend, instanced by every worn material. 32 internal nodes
    (materials.WEAR_GROUP_NODE_COUNT). The group takes the base
    colour, base roughness, a coordinate vector and the ten wear
    amounts, and returns the worn colour and roughness. Wear that
    does not apply to a family is zeroed host-side, so a zero
    amount is a no-op pass, not a branch."""
    global _WEAR_GROUP
    if _WEAR_GROUP is not None:
        return _WEAR_GROUP
    grp = bpy.data.node_groups.new("SKULLPT Wear", "ShaderNodeTree")
    have_in = {s.name for s in grp.interface.items_tree
               if s.in_out == "INPUT"}
    have_out = {s.name for s in grp.interface.items_tree
                if s.in_out == "OUTPUT"}

    def _socket(name, io, stype):
        have = have_in if io == "INPUT" else have_out
        if name not in have:
            # Signature is new_socket(name, description="",
            # in_out=..., socket_type=...): everything after the
            # name must be a keyword (probed on Blender 4.5.9).
            grp.interface.new_socket(name, description="",
                                     in_out=io, socket_type=stype)

    _socket("Base Color", "INPUT", "NodeSocketColor")
    _socket("Base Roughness", "INPUT", "NodeSocketFloat")
    _socket("Vector", "INPUT", "NodeSocketVector")
    for _name in ("dirt", "grime", "edge_wear", "edge_width", "dust",
                  "wet", "moss", "rust", "chips", "scratches"):
        _socket(_name, "INPUT", "NodeSocketFloat")
    _socket("Color", "OUTPUT", "NodeSocketColor")
    _socket("Roughness", "OUTPUT", "NodeSocketFloat")
    nt = grp
    links = nt.links
    gin = nt.nodes.new("NodeGroupInput")
    gout = nt.nodes.new("NodeGroupOutput")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    links.new(geo.outputs["Normal"], sep.inputs["Vector"])
    top_range = nt.nodes.new("ShaderNodeMapRange")
    top_range.inputs["From Min"].default_value = 0.5
    top_range.inputs["From Max"].default_value = 1.0
    top_range.inputs["To Min"].default_value = 0.0
    top_range.inputs["To Max"].default_value = 1.0
    links.new(sep.outputs["Z"], top_range.inputs["Value"])
    edge_range = nt.nodes.new("ShaderNodeMapRange")
    edge_range.inputs["From Min"].default_value = 0.0
    edge_range.inputs["To Min"].default_value = 0.0
    edge_range.inputs["To Max"].default_value = 1.0
    links.new(geo.outputs["Pointiness"], edge_range.inputs["Value"])
    edge_mult = nt.nodes.new("ShaderNodeMath")
    edge_mult.operation = "MULTIPLY"
    edge_mult.inputs[1].default_value = 0.8
    links.new(gin.outputs["edge_width"], edge_mult.inputs[0])
    edge_sub = nt.nodes.new("ShaderNodeMath")
    edge_sub.operation = "SUBTRACT"
    edge_sub.inputs[0].default_value = 1.0
    links.new(edge_mult.outputs["Value"], edge_sub.inputs[1])
    links.new(edge_sub.outputs["Value"], edge_range.inputs["From Max"])
    ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = 1.0
    dep_noise = nt.nodes.new("ShaderNodeTexNoise")
    dep_noise.inputs["Scale"].default_value = 6.0
    dep_noise.inputs["Detail"].default_value = 3.0
    links.new(gin.outputs["Vector"], dep_noise.inputs["Vector"])
    dep_top = nt.nodes.new("ShaderNodeMath")
    dep_top.operation = "MULTIPLY"
    links.new(top_range.outputs["Result"], dep_top.inputs[0])
    links.new(dep_noise.outputs["Fac"], dep_top.inputs[1])
    dep_add = nt.nodes.new("ShaderNodeMath")
    dep_add.operation = "ADD"
    dep_add.use_clamp = True
    links.new(gin.outputs["dirt"], dep_add.inputs[0])
    links.new(gin.outputs["dust"], dep_add.inputs[1])
    dep_mask = nt.nodes.new("ShaderNodeMath")
    dep_mask.operation = "MULTIPLY"
    links.new(dep_top.outputs["Value"], dep_mask.inputs[0])
    links.new(dep_add.outputs["Value"], dep_mask.inputs[1])
    dep_mix = nt.nodes.new("ShaderNodeMix")
    dep_mix.data_type = "RGBA"
    dep_mix.inputs["B"].default_value = (0.35, 0.30, 0.25, 1.0)
    links.new(gin.outputs["Base Color"], dep_mix.inputs["A"])
    links.new(dep_mask.outputs["Value"], dep_mix.inputs["Factor"])
    grime_inv = nt.nodes.new("ShaderNodeMath")
    grime_inv.operation = "SUBTRACT"
    grime_inv.inputs[0].default_value = 1.0
    links.new(ao.outputs["AO"], grime_inv.inputs[1])
    grime_mask = nt.nodes.new("ShaderNodeMath")
    grime_mask.operation = "MULTIPLY"
    links.new(grime_inv.outputs["Value"], grime_mask.inputs[0])
    links.new(gin.outputs["grime"], grime_mask.inputs[1])
    grime_mix = nt.nodes.new("ShaderNodeMix")
    grime_mix.data_type = "RGBA"
    grime_mix.inputs["B"].default_value = (0.12, 0.11, 0.10, 1.0)
    links.new(dep_mix.outputs["Result"], grime_mix.inputs["A"])
    links.new(grime_mask.outputs["Value"], grime_mix.inputs["Factor"])
    moss_mask = nt.nodes.new("ShaderNodeMath")
    moss_mask.operation = "MULTIPLY"
    links.new(top_range.outputs["Result"], moss_mask.inputs[0])
    links.new(gin.outputs["moss"], moss_mask.inputs[1])
    moss_mix = nt.nodes.new("ShaderNodeMix")
    moss_mix.data_type = "RGBA"
    moss_mix.inputs["B"].default_value = (0.25, 0.35, 0.15, 1.0)
    links.new(grime_mix.outputs["Result"], moss_mix.inputs["A"])
    links.new(moss_mask.outputs["Value"], moss_mix.inputs["Factor"])
    rust_mask = nt.nodes.new("ShaderNodeMath")
    rust_mask.operation = "MULTIPLY"
    links.new(dep_noise.outputs["Fac"], rust_mask.inputs[0])
    links.new(gin.outputs["rust"], rust_mask.inputs[1])
    rust_mix = nt.nodes.new("ShaderNodeMix")
    rust_mix.data_type = "RGBA"
    rust_mix.inputs["B"].default_value = (0.45, 0.25, 0.12, 1.0)
    links.new(moss_mix.outputs["Result"], rust_mix.inputs["A"])
    links.new(rust_mask.outputs["Value"], rust_mix.inputs["Factor"])
    chip_ramp = nt.nodes.new("ShaderNodeValToRGB")
    chip_ramp.color_ramp.interpolation = "CONSTANT"
    chip_ramp.color_ramp.elements[0].position = 0.75
    chip_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    chip_ramp.color_ramp.elements[1].position = 0.85
    chip_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    links.new(dep_noise.outputs["Fac"], chip_ramp.inputs["Fac"])
    chip_mask = nt.nodes.new("ShaderNodeMath")
    chip_mask.operation = "MULTIPLY"
    links.new(chip_ramp.outputs["Color"], chip_mask.inputs[0])
    links.new(gin.outputs["chips"], chip_mask.inputs[1])
    chip_mix = nt.nodes.new("ShaderNodeMix")
    chip_mix.data_type = "RGBA"
    chip_mix.inputs["B"].default_value = (0.15, 0.13, 0.12, 1.0)
    links.new(rust_mix.outputs["Result"], chip_mix.inputs["A"])
    links.new(chip_mask.outputs["Value"], chip_mix.inputs["Factor"])
    scratch_wave = nt.nodes.new("ShaderNodeTexWave")
    scratch_wave.wave_type = "BANDS"
    scratch_wave.bands_direction = "X"
    scratch_wave.inputs["Scale"].default_value = 60.0
    links.new(gin.outputs["Vector"], scratch_wave.inputs["Vector"])
    scratch_mask = nt.nodes.new("ShaderNodeMath")
    scratch_mask.operation = "MULTIPLY"
    links.new(scratch_wave.outputs["Fac"], scratch_mask.inputs[0])
    links.new(gin.outputs["scratches"], scratch_mask.inputs[1])
    scratch_mix = nt.nodes.new("ShaderNodeMix")
    scratch_mix.data_type = "RGBA"
    scratch_mix.inputs["B"].default_value = (0.80, 0.78, 0.72, 1.0)
    links.new(chip_mix.outputs["Result"], scratch_mix.inputs["A"])
    links.new(scratch_mask.outputs["Value"], scratch_mix.inputs["Factor"])
    edge_mask = nt.nodes.new("ShaderNodeMath")
    edge_mask.operation = "MULTIPLY"
    links.new(edge_range.outputs["Result"], edge_mask.inputs[0])
    links.new(gin.outputs["edge_wear"], edge_mask.inputs[1])
    edge_mix = nt.nodes.new("ShaderNodeMix")
    edge_mix.data_type = "RGBA"
    edge_mix.inputs["B"].default_value = (0.85, 0.82, 0.75, 1.0)
    links.new(scratch_mix.outputs["Result"], edge_mix.inputs["A"])
    links.new(edge_mask.outputs["Value"], edge_mix.inputs["Factor"])
    links.new(edge_mix.outputs["Result"], gout.inputs["Color"])
    wet_mult = nt.nodes.new("ShaderNodeMath")
    wet_mult.operation = "MULTIPLY"
    wet_mult.inputs[1].default_value = 0.7
    links.new(gin.outputs["wet"], wet_mult.inputs[0])
    wet_sub = nt.nodes.new("ShaderNodeMath")
    wet_sub.operation = "SUBTRACT"
    wet_sub.inputs[0].default_value = 1.0
    links.new(wet_mult.outputs["Value"], wet_sub.inputs[1])
    rough_mult = nt.nodes.new("ShaderNodeMath")
    rough_mult.operation = "MULTIPLY"
    links.new(gin.outputs["Base Roughness"], rough_mult.inputs[0])
    links.new(wet_sub.outputs["Value"], rough_mult.inputs[1])
    links.new(rough_mult.outputs["Value"], gout.inputs["Roughness"])
    _WEAR_GROUP = grp
    return grp

def _parity_factor(nt, links, vec, prescale):
    """A 3-axis checkerboard parity (M8): 8 nodes. FLOORED_MODULO,
    not MODULO: plain MODULO gives -1 for negative coordinates and
    the Mix factor clamps it to 0, which eats half the cells."""
    pre = nt.nodes.new("ShaderNodeVectorMath")
    pre.operation = "MULTIPLY"
    pre.inputs[1].default_value = tuple(prescale)
    links.new(vec, pre.inputs[0])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    links.new(pre.outputs["Vector"], sep.inputs["Vector"])
    floors = []
    for out in ("X", "Y", "Z"):
        f = nt.nodes.new("ShaderNodeMath")
        f.operation = "FLOOR"
        links.new(sep.outputs[out], f.inputs[0])
        floors.append(f.outputs["Value"])
    a1 = nt.nodes.new("ShaderNodeMath")
    a1.operation = "ADD"
    links.new(floors[0], a1.inputs[0])
    links.new(floors[1], a1.inputs[1])
    a2 = nt.nodes.new("ShaderNodeMath")
    a2.operation = "ADD"
    links.new(a1.outputs["Value"], a2.inputs[0])
    links.new(floors[2], a2.inputs[1])
    mod = nt.nodes.new("ShaderNodeMath")
    mod.operation = "FLOORED_MODULO"
    mod.inputs[1].default_value = 2.0
    links.new(a2.outputs["Value"], mod.inputs[0])
    return mod.outputs["Value"]

def _rotate_90(nt, links, vec, axis, parity):
    """A per-cell 90-degree rotation about a texture axis (M8):
    4 nodes. axis 0 is X, 1 is Y (the face normal in the texture
    frame, host-side). The wave rings encircle texture Z, so
    rotating about the face normal flips the grain between the
    two face directions."""
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    links.new(vec, sep.inputs["Vector"])
    axes = ("X", "Y", "Z")
    # The two axes that are not the rotation axis swap, one negated.
    others = [a for a in (0, 1, 2) if a != axis]
    neg = nt.nodes.new("ShaderNodeMath")
    neg.operation = "MULTIPLY"
    neg.inputs[1].default_value = -1.0
    links.new(sep.outputs[axes[others[1]]], neg.inputs[0])
    rot = nt.nodes.new("ShaderNodeCombineXYZ")
    links.new(sep.outputs[axes[axis]], rot.inputs[axes[axis]])
    links.new(neg.outputs["Value"], rot.inputs[axes[others[0]]])
    links.new(sep.outputs[axes[others[0]]], rot.inputs[axes[others[1]]])
    mixv = nt.nodes.new("ShaderNodeMix")
    mixv.data_type = "VECTOR"
    links.new(vec, mixv.inputs["A"])
    links.new(rot.outputs["Vector"], mixv.inputs["B"])
    links.new(parity, mixv.inputs["Factor"])
    return mixv.outputs["Result"]

def _rotate_90_z(nt, links, vec, parity):
    """A per-cell 90-degree rotation about texture Z (M8): 4 nodes.
    Basket weave only: the face rotation already puts the face on
    texture XY, so Z is the face normal and the brick pattern flips
    between running along X and along Y."""
    return _rotate_90(nt, links, vec, 2, parity)

def _rotate_45(nt, links, vec, axis, parity):
    """A per-cell +-45-degree rotation about a texture axis (M8):
    6 nodes. The 0.7071 factor is skipped: both parities scale
    the same, so the ring spacing stays consistent."""
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    links.new(vec, sep.inputs["Vector"])
    axes = ("X", "Y", "Z")
    others = [a for a in (0, 1, 2) if a != axis]
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    links.new(sep.outputs[axes[others[0]]], add.inputs[0])
    links.new(sep.outputs[axes[others[1]]], add.inputs[1])
    sub = nt.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    links.new(sep.outputs[axes[others[0]]], sub.inputs[0])
    links.new(sep.outputs[axes[others[1]]], sub.inputs[1])
    m0 = nt.nodes.new("ShaderNodeMix")
    m0.data_type = "FLOAT"
    links.new(sub.outputs["Value"], m0.inputs["A"])
    links.new(add.outputs["Value"], m0.inputs["B"])
    links.new(parity, m0.inputs["Factor"])
    m1 = nt.nodes.new("ShaderNodeMix")
    m1.data_type = "FLOAT"
    links.new(add.outputs["Value"], m1.inputs["A"])
    links.new(sub.outputs["Value"], m1.inputs["B"])
    links.new(parity, m1.inputs["Factor"])
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    links.new(sep.outputs[axes[axis]], comb.inputs[axes[axis]])
    links.new(m0.outputs["Result"], comb.inputs[axes[others[0]]])
    links.new(m1.outputs["Result"], comb.inputs[axes[others[1]]])
    return comb.outputs["Vector"]

def _chevron_vec(nt, links, vec, mspec):
    """Fold + 45-degree rotation (M8): 7 nodes. The fold runs in
    cell units and the result is scaled back to mapped units, so
    the wave sees its normal coordinate scale."""
    pre = nt.nodes.new("ShaderNodeVectorMath")
    pre.operation = "MULTIPLY"
    pre.inputs[1].default_value = tuple(mspec["fold_prescale"])
    links.new(vec, pre.inputs[0])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    links.new(pre.outputs["Vector"], sep.inputs["Vector"])
    axes = ("X", "Y", "Z")
    fold_axis = mspec["fold_axis"]
    other_axis = 1 - fold_axis
    fold = nt.nodes.new("ShaderNodeMath")
    fold.operation = "PINGPONG"
    fold.inputs[1].default_value = 1.0
    links.new(sep.outputs[axes[fold_axis]], fold.inputs[0])
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    links.new(fold.outputs["Value"], add.inputs[0])
    links.new(sep.outputs["Z"], add.inputs[1])
    sub = nt.nodes.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    links.new(fold.outputs["Value"], sub.inputs[0])
    links.new(sep.outputs["Z"], sub.inputs[1])
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    links.new(sep.outputs[axes[other_axis]],
              comb.inputs[axes[other_axis]])
    links.new(sub.outputs["Value"], comb.inputs[axes[fold_axis]])
    links.new(add.outputs["Value"], comb.inputs["Z"])
    unscale = nt.nodes.new("ShaderNodeVectorMath")
    unscale.operation = "MULTIPLY"
    unscale.inputs[1].default_value = tuple(mspec["fold_unscale"])
    links.new(comb.outputs["Vector"], unscale.inputs[0])
    return unscale.outputs["Vector"]

def _brick_node(nt, links, vec, width, row_height, bias, mortar):
    """One Brick node (M8): the 4.0+ Brick node has no
    Offset/Frequency inputs (probed on Blender 4.5.9): Brick Width
    and Row Height size the cells directly, and Bias 0 is a stack
    bond, Bias 1 a running bond."""
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Brick Width"].default_value = width
    brick.inputs["Row Height"].default_value = row_height
    brick.inputs["Mortar Size"].default_value = mortar
    brick.inputs["Mortar Smooth"].default_value = 0.0
    brick.inputs["Bias"].default_value = bias
    brick.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    brick.inputs["Color2"].default_value = (0.82, 0.82, 0.82, 1.0)
    brick.inputs["Mortar"].default_value = (0.45, 0.45, 0.45, 1.0)
    links.new(vec, brick.inputs["Vector"])
    return brick

def make_material(part):
    hex_color = part["color"]
    r = int(hex_color[1:3], 16) / 255.0
    g = int(hex_color[3:5], 16) / 255.0
    b = int(hex_color[5:7], 16) / 255.0
    mat = bpy.data.materials.new(part["id"])
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Roughness"].default_value = part["roughness"]
    bsdf.inputs["Metallic"].default_value = part["metallic"]
    mspec = part.get("material_spec")
    if mspec:
        # Parametric recipes (v1.11 M2, v1.12 M7-M9): one branch per
        # family, each producing color_out (the colour socket, or
        # None for glass, whose Base Color is already the tinted
        # style colour) and bump_height (the height socket, or
        # None). The wood recipe's structure follows the NK Wood
        # Grain group in video-to-blender (MIT); every recipe is
        # wired here in plain bpy. Node counts are predicted
        # host-side (materials.py) and the measured count is
        # recorded in the stats.
        links = nt.links
        coord = nt.nodes.new("ShaderNodeTexCoord")
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.inputs["Location"].default_value = tuple(mspec["seed_loc"])
        mapping.inputs["Rotation"].default_value = tuple(mspec["map_rot"])
        cs = mspec["coord_scale"]
        mapping.inputs["Scale"].default_value = (cs, cs, cs)
        links.new(coord.outputs["Object"], mapping.inputs["Vector"])
        vec = mapping.outputs["Vector"]
        fam = mspec["family"]
        layout = mspec["layout"]
        if fam == "wood" and layout == "parquet":
            vec = _rotate_90(nt, links, vec, mspec["layout_rot_axis"],
                             _parity_factor(nt, links, vec,
                                            mspec["parity_prescale"]))
        elif fam == "wood" and layout == "herringbone":
            vec = _rotate_45(nt, links, vec, mspec["layout_rot_axis"],
                             _parity_factor(nt, links, vec,
                                            mspec["parity_prescale"]))
        elif fam == "wood" and layout == "chevron":
            vec = _chevron_vec(nt, links, vec, mspec)
        color_out = None
        bump_height = None
        if fam == "wood":
            # Rings: a saw-profile wave around Z (the grain axis
            # after the mapping rotation), cubed for a sharp
            # latewood edge.
            wave = nt.nodes.new("ShaderNodeTexWave")
            wave.wave_type = "RINGS"
            wave.rings_direction = "Z"
            wave.wave_profile = "SAW"
            wave.inputs["Scale"].default_value = mspec["ring_scale"]
            wave.inputs["Distortion"].default_value = mspec["distort"]
            links.new(vec, wave.inputs["Vector"])
            ring_pow = nt.nodes.new("ShaderNodeMath")
            ring_pow.operation = "POWER"
            ring_pow.inputs[1].default_value = 3.0
            links.new(wave.outputs["Fac"], ring_pow.inputs[0])
            # Fibres: noise stretched along the grain axis.
            stretch = nt.nodes.new("ShaderNodeVectorMath")
            stretch.operation = "MULTIPLY"
            stretch.inputs[1].default_value = (40.0, 40.0, 1.5)
            links.new(vec, stretch.inputs[0])
            fibre = nt.nodes.new("ShaderNodeTexNoise")
            fibre.inputs["Scale"].default_value = 1.0
            fibre.inputs["Detail"].default_value = 4.0
            links.new(stretch.outputs["Vector"], fibre.inputs["Vector"])
            # Blotch: large soft tone variation.
            blotch = nt.nodes.new("ShaderNodeTexNoise")
            blotch.inputs["Scale"].default_value = 2.0
            blotch.inputs["Detail"].default_value = 3.0
            links.new(vec, blotch.inputs["Vector"])
            blotch_range = nt.nodes.new("ShaderNodeMapRange")
            blotch_range.inputs["From Min"].default_value = 0.3
            blotch_range.inputs["From Max"].default_value = 0.7
            blotch_range.inputs["To Min"].default_value = 0.9
            blotch_range.inputs["To Max"].default_value = 1.08
            links.new(blotch.outputs["Fac"], blotch_range.inputs["Value"])
            # Colour: mix light/dark by ring + fibre, toned by blotch.
            ring_term = nt.nodes.new("ShaderNodeMath")
            ring_term.operation = "MULTIPLY"
            ring_term.inputs[1].default_value = 0.85
            links.new(ring_pow.outputs["Value"], ring_term.inputs[0])
            fibre_term = nt.nodes.new("ShaderNodeMath")
            fibre_term.operation = "MULTIPLY"
            fibre_term.inputs[1].default_value = mspec["fibre_k"]
            links.new(fibre.outputs["Fac"], fibre_term.inputs[0])
            tone_t = nt.nodes.new("ShaderNodeMath")
            tone_t.operation = "ADD"
            tone_t.use_clamp = True
            links.new(ring_term.outputs["Value"], tone_t.inputs[0])
            links.new(fibre_term.outputs["Value"], tone_t.inputs[1])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            # The Mix node carries sockets for every data type, but
            # Blender's name lookup returns the enabled (RGBA)
            # sockets for the current data type - verified on
            # Blender 4.5.9.
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(tone_t.outputs["Value"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            if mspec["knots"] > 0:
                # Knots: a hard mask over high-frequency noise,
                # mixed toward the dark colour. The knots value
                # moves the ramp thresholds host-side
                # (materials.resolve_spec).
                knot_noise = nt.nodes.new("ShaderNodeTexNoise")
                knot_noise.inputs["Scale"].default_value = \
                    mspec["knot_scale"]
                knot_noise.inputs["Detail"].default_value = 3.0
                links.new(vec, knot_noise.inputs["Vector"])
                knot_ramp = nt.nodes.new("ShaderNodeValToRGB")
                knot_ramp.color_ramp.interpolation = "CONSTANT"
                knot_ramp.color_ramp.elements[0].position = \
                    mspec["knot_low"]
                knot_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
                knot_ramp.color_ramp.elements[1].position = \
                    mspec["knot_high"]
                knot_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
                links.new(knot_noise.outputs["Fac"], knot_ramp.inputs["Fac"])
                knot_mix = nt.nodes.new("ShaderNodeMix")
                knot_mix.data_type = "RGBA"
                knot_mix.inputs["B"].default_value = \
                    tuple(mspec["dark"]) + (1.0,)
                links.new(color_out, knot_mix.inputs["A"])
                links.new(knot_ramp.outputs["Color"],
                          knot_mix.inputs["Factor"])
                color_out = knot_mix.outputs["Result"]
            tone = nt.nodes.new("ShaderNodeVectorMath")
            tone.operation = "SCALE"
            links.new(color_out, tone.inputs["Vector"])
            links.new(blotch_range.outputs["Result"], tone.inputs["Scale"])
            color_out = tone.outputs["Vector"]
            bump_add = nt.nodes.new("ShaderNodeMath")
            bump_add.operation = "ADD"
            links.new(fibre.outputs["Fac"], bump_add.inputs[0])
            links.new(ring_pow.outputs["Value"], bump_add.inputs[1])
            bump_height = bump_add.outputs["Value"]
        elif fam == "brick":
            # Kiln variation: one noise, remapped by the variation
            # knob, mixing light and dark brick.
            kiln = nt.nodes.new("ShaderNodeTexNoise")
            kiln.inputs["Scale"].default_value = mspec["kiln_scale"]
            kiln.inputs["Detail"].default_value = 3.0
            links.new(vec, kiln.inputs["Vector"])
            kiln_range = nt.nodes.new("ShaderNodeMapRange")
            kiln_range.inputs["From Min"].default_value = 0.3
            kiln_range.inputs["From Max"].default_value = 0.7
            var = mspec["variation"]
            kiln_range.inputs["To Min"].default_value = 0.5 - var * 0.45
            kiln_range.inputs["To Max"].default_value = 0.5 + var * 0.45
            links.new(kiln.outputs["Fac"], kiln_range.inputs["Value"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(kiln_range.outputs["Result"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_height = kiln.outputs["Fac"]
        elif fam == "stone":
            vor = nt.nodes.new("ShaderNodeTexVoronoi")
            vor.inputs["Scale"].default_value = mspec["vein_scale"]
            links.new(vec, vor.inputs["Vector"])
            grain = nt.nodes.new("ShaderNodeTexNoise")
            grain.inputs["Scale"].default_value = mspec["grain_scale"]
            grain.inputs["Detail"].default_value = 3.0
            links.new(vec, grain.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.25
            ramp.color_ramp.elements[1].position = 0.75
            links.new(vor.outputs["Distance"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_add = nt.nodes.new("ShaderNodeMath")
            bump_add.operation = "ADD"
            links.new(vor.outputs["Distance"], bump_add.inputs[0])
            links.new(grain.outputs["Fac"], bump_add.inputs[1])
            bump_height = bump_add.outputs["Value"]
        elif fam == "marble":
            # Veins: noise sampled on coordinates distorted by a
            # second noise, so the veins wander instead of
            # running straight.
            distort = nt.nodes.new("ShaderNodeTexNoise")
            distort.inputs["Scale"].default_value = 2.0
            distort.inputs["Detail"].default_value = 3.0
            links.new(vec, distort.inputs["Vector"])
            dscale = nt.nodes.new("ShaderNodeVectorMath")
            dscale.operation = "MULTIPLY"
            dk = 0.2 + mspec["contrast"] * 0.6
            dscale.inputs[1].default_value = (dk, dk, dk)
            links.new(distort.outputs["Color"], dscale.inputs[0])
            dadd = nt.nodes.new("ShaderNodeVectorMath")
            dadd.operation = "ADD"
            links.new(vec, dadd.inputs[0])
            links.new(dscale.outputs["Vector"], dadd.inputs[1])
            veins = nt.nodes.new("ShaderNodeTexNoise")
            veins.inputs["Scale"].default_value = mspec["vein_scale"]
            veins.inputs["Detail"].default_value = 4.0
            links.new(dadd.outputs["Vector"], veins.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            vw = mspec["vein_width"]
            ramp.color_ramp.elements[0].position = max(0.0, 0.7 - vw * 0.25)
            ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
            ramp.color_ramp.elements[1].position = min(1.0, 0.7 + vw * 0.25)
            ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
            links.new(veins.outputs["Fac"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_height = veins.outputs["Fac"]
        elif fam == "concrete":
            patch = nt.nodes.new("ShaderNodeTexNoise")
            patch.inputs["Scale"].default_value = mspec["patch_scale"]
            patch.inputs["Detail"].default_value = 3.0
            links.new(vec, patch.inputs["Vector"])
            agg = nt.nodes.new("ShaderNodeTexVoronoi")
            agg.inputs["Scale"].default_value = mspec["aggregate_scale"]
            links.new(vec, agg.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.3
            ramp.color_ramp.elements[1].position = 0.7
            links.new(patch.outputs["Fac"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_add = nt.nodes.new("ShaderNodeMath")
            bump_add.operation = "ADD"
            links.new(agg.outputs["Distance"], bump_add.inputs[0])
            links.new(patch.outputs["Fac"], bump_add.inputs[1])
            bump_height = bump_add.outputs["Value"]
        elif fam == "metal":
            # Brushed metal: noise stretched along one axis, so the
            # brush lines run in one direction.
            stretch = nt.nodes.new("ShaderNodeVectorMath")
            stretch.operation = "MULTIPLY"
            stretch.inputs[1].default_value = (
                5.0 + mspec["brushed"] * 40.0, 1.0, 1.0)
            links.new(vec, stretch.inputs[0])
            brush = nt.nodes.new("ShaderNodeTexNoise")
            brush.inputs["Scale"].default_value = 2.0
            brush.inputs["Detail"].default_value = 2.0
            links.new(stretch.outputs["Vector"], brush.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            tarn = mspec["tarnish"]
            ramp.color_ramp.elements[0].position = 0.45 - tarn * 0.35
            ramp.color_ramp.elements[1].position = 0.55 + tarn * 0.35
            links.new(brush.outputs["Fac"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_height = brush.outputs["Fac"]
        elif fam == "fabric":
            # Weave: two perpendicular band waves, added.
            wave_x = nt.nodes.new("ShaderNodeTexWave")
            wave_x.wave_type = "BANDS"
            wave_x.bands_direction = "X"
            wave_x.inputs["Scale"].default_value = mspec["weave_scale"]
            links.new(vec, wave_x.inputs["Vector"])
            wave_y = nt.nodes.new("ShaderNodeTexWave")
            wave_y.wave_type = "BANDS"
            wave_y.bands_direction = "Y"
            wave_y.inputs["Scale"].default_value = mspec["weave_scale"]
            links.new(vec, wave_y.inputs["Vector"])
            wadd = nt.nodes.new("ShaderNodeMath")
            wadd.operation = "ADD"
            wadd.use_clamp = True
            links.new(wave_x.outputs["Fac"], wadd.inputs[0])
            links.new(wave_y.outputs["Fac"], wadd.inputs[1])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.3
            ramp.color_ramp.elements[1].position = 0.7
            links.new(wadd.outputs["Value"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            fuzz = nt.nodes.new("ShaderNodeTexNoise")
            fuzz.inputs["Scale"].default_value = mspec["fuzz_scale"]
            fuzz.inputs["Detail"].default_value = 2.0
            links.new(vec, fuzz.inputs["Vector"])
            bump_height = fuzz.outputs["Fac"]
        elif fam == "leather":
            pores = nt.nodes.new("ShaderNodeTexVoronoi")
            pores.inputs["Scale"].default_value = mspec["pore_scale"]
            links.new(vec, pores.inputs["Vector"])
            crease = nt.nodes.new("ShaderNodeTexNoise")
            crease.inputs["Scale"].default_value = mspec["crease_scale"]
            crease.inputs["Detail"].default_value = 3.0
            links.new(vec, crease.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.25
            ramp.color_ramp.elements[1].position = 0.75
            links.new(pores.outputs["Distance"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_add = nt.nodes.new("ShaderNodeMath")
            bump_add.operation = "ADD"
            links.new(pores.outputs["Distance"], bump_add.inputs[0])
            links.new(crease.outputs["Fac"], bump_add.inputs[1])
            bump_height = bump_add.outputs["Value"]
        elif fam == "ground":
            clump = nt.nodes.new("ShaderNodeTexNoise")
            clump.inputs["Scale"].default_value = mspec["clump_scale"]
            clump.inputs["Detail"].default_value = 3.0
            links.new(vec, clump.inputs["Vector"])
            stones = nt.nodes.new("ShaderNodeTexVoronoi")
            stones.inputs["Scale"].default_value = mspec["stone_scale"]
            links.new(vec, stones.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.3
            ramp.color_ramp.elements[1].position = 0.7
            links.new(clump.outputs["Fac"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            bump_add = nt.nodes.new("ShaderNodeMath")
            bump_add.operation = "ADD"
            links.new(clump.outputs["Fac"], bump_add.inputs[0])
            links.new(stones.outputs["Distance"], bump_add.inputs[1])
            bump_height = bump_add.outputs["Value"]
        elif fam == "paint":
            peel = nt.nodes.new("ShaderNodeTexNoise")
            peel.inputs["Scale"].default_value = mspec["peel_scale"]
            peel.inputs["Detail"].default_value = 2.0
            links.new(vec, peel.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            ramp.color_ramp.elements[0].position = 0.35
            ramp.color_ramp.elements[1].position = 0.65
            links.new(peel.outputs["Fac"], ramp.inputs["Fac"])
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.inputs["A"].default_value = tuple(mspec["light"]) + (1.0,)
            mix.inputs["B"].default_value = tuple(mspec["dark"]) + (1.0,)
            links.new(ramp.outputs["Color"], mix.inputs["Factor"])
            color_out = mix.outputs["Result"]
            if mspec["chip"] > 0:
                # Chips: a hard mask over cell noise, mixed toward
                # the dark substrate colour.
                chip_vor = nt.nodes.new("ShaderNodeTexVoronoi")
                chip_vor.inputs["Scale"].default_value = 12.0
                links.new(vec, chip_vor.inputs["Vector"])
                chip_ramp = nt.nodes.new("ShaderNodeValToRGB")
                chip_ramp.color_ramp.interpolation = "CONSTANT"
                chip_ramp.color_ramp.elements[0].position = \
                    mspec["chip_low"]
                chip_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
                chip_ramp.color_ramp.elements[1].position = \
                    mspec["chip_high"]
                chip_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
                links.new(chip_vor.outputs["Distance"],
                          chip_ramp.inputs["Fac"])
                chip_mix = nt.nodes.new("ShaderNodeMix")
                chip_mix.data_type = "RGBA"
                chip_mix.inputs["B"].default_value = \
                    tuple(mspec["dark"]) + (1.0,)
                links.new(color_out, chip_mix.inputs["A"])
                links.new(chip_ramp.outputs["Color"],
                          chip_mix.inputs["Factor"])
                color_out = chip_mix.outputs["Result"]
            bump_height = peel.outputs["Fac"]
        elif fam == "glass":
            # No pattern nodes: the Base Color is already the
            # tinted style colour, and transmission is set below.
            if mspec["frost"] > 0:
                frost_noise = nt.nodes.new("ShaderNodeTexNoise")
                frost_noise.inputs["Scale"].default_value = \
                    mspec["frost_scale"]
                frost_noise.inputs["Detail"].default_value = 3.0
                links.new(vec, frost_noise.inputs["Vector"])
                bump_height = frost_noise.outputs["Fac"]
        if mspec.get("boards"):
            # Boards: the Brick node on board-scaled coordinates,
            # staggered joints, tone variation per board plus
            # darker mortar grooves in one multiply.
            prescale = nt.nodes.new("ShaderNodeVectorMath")
            prescale.operation = "MULTIPLY"
            prescale.inputs[1].default_value = tuple(mspec["board_prescale"])
            links.new(mapping.outputs["Vector"], prescale.inputs[0])
            brick = nt.nodes.new("ShaderNodeTexBrick")
            brick.inputs["Scale"].default_value = 1.0
            brick.inputs["Brick Width"].default_value = 1.0
            brick.inputs["Row Height"].default_value = 1.0
            brick.inputs["Mortar Size"].default_value = 0.02
            brick.inputs["Mortar Smooth"].default_value = 0.0
            brick.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
            brick.inputs["Color2"].default_value = (0.82, 0.82, 0.82, 1.0)
            brick.inputs["Mortar"].default_value = (0.45, 0.45, 0.45, 1.0)
            links.new(prescale.outputs["Vector"], brick.inputs["Vector"])
            board_tone = nt.nodes.new("ShaderNodeVectorMath")
            board_tone.operation = "MULTIPLY"
            links.new(color_out, board_tone.inputs[0])
            links.new(brick.outputs["Color"], board_tone.inputs[1])
            color_out = board_tone.outputs["Vector"]
        bond = mspec.get("bond")
        if bond:
            # Bond layouts (M8): the Brick node on face-rotated,
            # uniformly-scaled coordinates, multiplying the family
            # colour. The face rotation (host-side) puts the
            # part's largest face on texture XY.
            kind = bond["kind"]
            mortar = mspec.get("mortar", 0.05)
            axes = ("X", "Y", "Z")
            if kind != "basket_weave":
                # Basket weave prescales the rotated vector instead,
                # so the shared prescale is not built for it (a dead
                # node would break the predicted count).
                prescale = nt.nodes.new("ShaderNodeVectorMath")
                prescale.operation = "MULTIPLY"
                prescale.inputs[1].default_value = tuple(
                    mspec["bond_prescale"])
                links.new(mapping.outputs["Vector"], prescale.inputs[0])
            if kind in ("stack", "stretcher"):
                brick = _brick_node(nt, links, prescale.outputs["Vector"],
                                    mspec["brick_width"],
                                    mspec["row_height"], bond["bias"],
                                    mortar)
                brick_color = brick.outputs["Color"]
            elif kind == "english":
                sep = nt.nodes.new("ShaderNodeSeparateXYZ")
                links.new(prescale.outputs["Vector"], sep.inputs["Vector"])
                row_floor = nt.nodes.new("ShaderNodeMath")
                row_floor.operation = "FLOOR"
                links.new(sep.outputs[axes[mspec["bond_row"]]],
                          row_floor.inputs[0])
                row_mod = nt.nodes.new("ShaderNodeMath")
                row_mod.operation = "FLOORED_MODULO"
                row_mod.inputs[1].default_value = 2.0
                links.new(row_floor.outputs["Value"], row_mod.inputs[0])
                brick_s = _brick_node(nt, links,
                                      prescale.outputs["Vector"],
                                      mspec["brick_width"],
                                      mspec["row_height"], 1.0, mortar)
                brick_h = _brick_node(nt, links,
                                      prescale.outputs["Vector"],
                                      mspec["header_width"],
                                      mspec["header_row_height"], 0.0,
                                      mortar)
                bond_mix = nt.nodes.new("ShaderNodeMix")
                bond_mix.data_type = "RGBA"
                links.new(brick_s.outputs["Color"], bond_mix.inputs["A"])
                links.new(brick_h.outputs["Color"], bond_mix.inputs["B"])
                links.new(row_mod.outputs["Value"],
                          bond_mix.inputs["Factor"])
                brick_color = bond_mix.outputs["Result"]
            elif kind == "flemish":
                sep = nt.nodes.new("ShaderNodeSeparateXYZ")
                links.new(prescale.outputs["Vector"], sep.inputs["Vector"])
                divide = nt.nodes.new("ShaderNodeMath")
                divide.operation = "DIVIDE"
                divide.inputs[1].default_value = bond["unit_ratio"]
                links.new(sep.outputs[axes[mspec["bond_unit"]]],
                          divide.inputs[0])
                row_floor = nt.nodes.new("ShaderNodeMath")
                row_floor.operation = "FLOOR"
                links.new(sep.outputs[axes[mspec["bond_row"]]],
                          row_floor.inputs[0])
                row_mod = nt.nodes.new("ShaderNodeMath")
                row_mod.operation = "FLOORED_MODULO"
                row_mod.inputs[1].default_value = 2.0
                links.new(row_floor.outputs["Value"], row_mod.inputs[0])
                offset = nt.nodes.new("ShaderNodeMath")
                offset.operation = "MULTIPLY"
                offset.inputs[1].default_value = 0.5
                links.new(row_mod.outputs["Value"], offset.inputs[0])
                unit_pos = nt.nodes.new("ShaderNodeMath")
                unit_pos.operation = "ADD"
                links.new(divide.outputs["Value"], unit_pos.inputs[0])
                links.new(offset.outputs["Value"], unit_pos.inputs[1])
                fract = nt.nodes.new("ShaderNodeMath")
                fract.operation = "FRACT"
                links.new(unit_pos.outputs["Value"], fract.inputs[0])
                greater = nt.nodes.new("ShaderNodeMath")
                greater.operation = "GREATER_THAN"
                greater.inputs[1].default_value = bond["header_frac"]
                links.new(fract.outputs["Value"], greater.inputs[0])
                brick_s = _brick_node(nt, links,
                                      prescale.outputs["Vector"],
                                      mspec["brick_width"],
                                      mspec["row_height"], 1.0, mortar)
                brick_h = _brick_node(nt, links,
                                      prescale.outputs["Vector"],
                                      mspec["header_width"],
                                      mspec["header_row_height"], 0.0,
                                      mortar)
                bond_mix = nt.nodes.new("ShaderNodeMix")
                bond_mix.data_type = "RGBA"
                links.new(brick_s.outputs["Color"], bond_mix.inputs["A"])
                links.new(brick_h.outputs["Color"], bond_mix.inputs["B"])
                links.new(greater.outputs["Value"],
                          bond_mix.inputs["Factor"])
                brick_color = bond_mix.outputs["Result"]
            else:  # basket_weave
                parity = _parity_factor(nt, links,
                                        mapping.outputs["Vector"],
                                        mspec["basket_prescale"])
                rotated = _rotate_90_z(nt, links,
                                       mapping.outputs["Vector"], parity)
                bprescale = nt.nodes.new("ShaderNodeVectorMath")
                bprescale.operation = "MULTIPLY"
                bprescale.inputs[1].default_value = tuple(
                    mspec["bond_prescale"])
                links.new(rotated, bprescale.inputs[0])
                brick = _brick_node(nt, links, bprescale.outputs["Vector"],
                                    mspec["brick_width"],
                                    mspec["row_height"], 0.0, mortar)
                brick_color = brick.outputs["Color"]
            bond_tone = nt.nodes.new("ShaderNodeVectorMath")
            bond_tone.operation = "MULTIPLY"
            links.new(color_out, bond_tone.inputs[0])
            links.new(brick_color, bond_tone.inputs[1])
            color_out = bond_tone.outputs["Vector"]
        if mspec["wear_any"]:
            # The shared wear group (M9): one group node per worn
            # material. Glass has no colour socket of its own, so
            # its Base Color goes in as the constant style colour.
            grp = _wear_group()
            wnode = nt.nodes.new("ShaderNodeGroup")
            wnode.node_tree = grp
            if color_out is not None:
                links.new(color_out, wnode.inputs["Base Color"])
            else:
                wnode.inputs["Base Color"].default_value = (r, g, b, 1.0)
            wnode.inputs["Base Roughness"].default_value = \
                part["roughness"]
            links.new(mapping.outputs["Vector"], wnode.inputs["Vector"])
            for _name, _value in mspec["wear"].items():
                wnode.inputs[_name].default_value = _value
            color_out = wnode.outputs["Color"]
            links.new(wnode.outputs["Roughness"],
                      bsdf.inputs["Roughness"])
        if color_out is not None:
            links.new(color_out, bsdf.inputs["Base Color"])
        if mspec.get("transmission"):
            bsdf.inputs["Transmission Weight"].default_value = \
                mspec["transmission"]
        if bump_height is not None:
            bump = nt.nodes.new("ShaderNodeBump")
            bump.inputs["Strength"].default_value = mspec["bump"]
            bump.inputs["Distance"].default_value = 0.005
            links.new(bump_height, bump.inputs["Height"])
            links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        return mat
    texture = part.get("texture")
    if texture:
        # Procedural surface from noise, in object coordinates - no image
        # files, no UV dependence, cheap on a small PC.
        coord = nt.nodes.new("ShaderNodeTexCoord")
        noise = nt.nodes.new("ShaderNodeTexNoise")
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        bump = nt.nodes.new("ShaderNodeBump")
        nt.links.new(coord.outputs["Object"], noise.inputs["Vector"])
        ramp.color_ramp.elements[0].position = 0.25
        ramp.color_ramp.elements[1].position = 0.75
        if texture == "wood_grain":
            noise.inputs["Scale"].default_value = 5.0
            noise.inputs["Detail"].default_value = 7.0
            ramp.color_ramp.elements[0].color = _shade(hex_color, 0.55)
            ramp.color_ramp.elements[1].color = _shade(hex_color, 1.25)
            bump.inputs["Strength"].default_value = 0.22
        elif texture == "brushed_metal":
            noise.inputs["Scale"].default_value = 90.0
            noise.inputs["Detail"].default_value = 2.0
            ramp.color_ramp.elements[0].color = _shade(hex_color, 0.8)
            ramp.color_ramp.elements[1].color = _shade(hex_color, 1.12)
            bump.inputs["Strength"].default_value = 0.06
        else:  # worn_leather
            noise.inputs["Scale"].default_value = 9.0
            noise.inputs["Detail"].default_value = 9.0
            ramp.color_ramp.elements[0].color = _shade(hex_color, 0.7)
            ramp.color_ramp.elements[1].color = _shade(hex_color, 1.18)
            bump.inputs["Strength"].default_value = 0.3
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    if part["glass"]:
        bsdf.inputs["Alpha"].default_value = 0.35
        mat.surface_render_method = "DITHERED"
    if part["glow"]:
        bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 2.0
    return mat
'''

_BUILD_TEMPLATE_TAIL = r'''
def look_at(obj, point):
    direction = Vector(point) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

def _finish_mesh(part, verts, faces, uvs):
    mesh = bpy.data.meshes.new(part["id"])
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if uvs and len(uvs) == len(verts):
        layer = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            layer.data[loop.index].uv = uvs[loop.vertex_index]
    obj = bpy.data.objects.new(part["id"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj

def build_extrude(part, mn, sz):
    profile = [(pt[0] / 100.0, pt[1] / 100.0) for pt in part["profile"]]
    n = len(profile)
    z0, z1 = mn[2], mn[2] + sz[2]
    verts = [(mn[0] + x, mn[1] + y, z0) for x, y in profile]
    verts += [(mn[0] + x, mn[1] + y, z1) for x, y in profile]
    uvs = [(x * 0.5, y * 0.5) for x, y in profile] * 2
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    return _finish_mesh(part, verts, faces, uvs)

def build_lathe(part, mn, sz):
    # profile points are [radius_cm, z_cm]; position_cm was the centre of
    # the base, so the footprint min sits one radius out on x/y.
    cx, cy, z0 = mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2]
    profile = [(pt[0] / 100.0, pt[1] / 100.0) for pt in part["profile"]]
    seg = 20
    verts, uvs = [], []
    rings = len(profile)
    for ri, (r, pz) in enumerate(profile):
        for s in range(seg):
            t = 2 * math.pi * s / seg
            verts.append((cx + r * math.cos(t), cy + r * math.sin(t), z0 + pz))
            uvs.append((s / seg, ri / max(1, rings - 1)))
    faces = []
    for ri in range(rings - 1):
        for s in range(seg):
            j = (s + 1) % seg
            a, b = ri * seg + s, ri * seg + j
            c, d = (ri + 1) * seg + j, (ri + 1) * seg + s
            faces.append((a, b, c, d))
    if profile[0][0] > 0.001:
        ci = len(verts); verts.append((cx, cy, z0 + profile[0][1])); uvs.append((0.5, 0.0))
        for s in range(seg):
            faces.append((ci, (s + 1) % seg, s))
    if profile[-1][0] > 0.001:
        ci = len(verts); base = (rings - 1) * seg
        verts.append((cx, cy, z0 + profile[-1][1])); uvs.append((0.5, 1.0))
        for s in range(seg):
            faces.append((ci, base + s, base + (s + 1) % seg))
    return _finish_mesh(part, verts, faces, uvs)

def _vsub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _vcross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _vnorm(a):
    l = math.sqrt(sum(v * v for v in a)) or 1.0
    return (a[0] / l, a[1] / l, a[2] / l)

def build_tube(part):
    path = [tuple(v / 100.0 for v in pt) for pt in part["path"]]
    r = float(part.get("radius") or 2) / 100.0
    seg = 10
    verts, uvs = [], []
    n = len(path)
    for i, p in enumerate(path):
        p0 = path[max(0, i - 1)]; p1 = path[min(n - 1, i + 1)]
        d = _vnorm(_vsub(p1, p0))
        up = (1.0, 0.0, 0.0) if abs(d[2]) > 0.9 else (0.0, 0.0, 1.0)
        side = _vnorm(_vcross(d, up))
        upv = _vcross(side, d)
        for s in range(seg):
            t = 2 * math.pi * s / seg
            off = tuple(r * (math.cos(t) * side[k] + math.sin(t) * upv[k]) for k in range(3))
            verts.append((p[0] + off[0], p[1] + off[1], p[2] + off[2]))
            uvs.append((s / seg, i / max(1, n - 1)))
    faces = []
    for i in range(n - 1):
        for s in range(seg):
            j = (s + 1) % seg
            a, b = i * seg + s, i * seg + j
            c, d2 = (i + 1) * seg + j, (i + 1) * seg + s
            faces.append((a, b, c, d2))
    return _finish_mesh(part, verts, faces, uvs)

MAT_STATS = {}

for part in D["parts"]:
    # A job that carries a real mesh does not rebuild the mesh's
    # stand-in boxes (T21/T30): the mesh itself is imported below and
    # already occupies that volume. Hand-added parts (floor, props)
    # still build.
    if D.get("mesh_file") and part["type"] == "imported":
        continue
    mn = [v / 100.0 for v in part["min"]]
    sz = [v / 100.0 for v in part["size"]]
    center = (mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] / 2)
    shape = part["shape"]
    if shape == "extrude" and part["profile"]:
        obj = build_extrude(part, mn, sz)
    elif shape == "lathe" and part["profile"]:
        obj = build_lathe(part, mn, sz)
    elif shape == "tube" and part["path"]:
        obj = build_tube(part)
    elif shape == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=sz[0] / 2, depth=sz[2], location=center)
        obj = bpy.context.active_object
        obj.dimensions = (max(sz[0], 0.01), max(sz[1], 0.01), max(sz[2], 0.01))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    elif shape == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1.0, location=center)
        obj = bpy.context.active_object
        obj.dimensions = (max(sz[0], 0.01), max(sz[1], 0.01), max(sz[2], 0.01))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bpy.ops.object.shade_smooth()
    else:
        bpy.ops.mesh.primitive_cube_add(location=center)
        obj = bpy.context.active_object
        obj.dimensions = (max(sz[0], 0.01), max(sz[1], 0.01), max(sz[2], 0.01))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        if part["type"] not in ("floor", "ground") and min(sz) > 0.03:
            bevel = obj.modifiers.new("soften", "BEVEL")
            bevel.width = 0.012
            bevel.segments = 2
    if shape in ("box", "extrude") and part.get("bevel") is not None:
        # an explicit bevel_cm replaces the default softening (0 = sharp)
        for mod in list(obj.modifiers):
            if mod.type == "BEVEL":
                obj.modifiers.remove(mod)
        if float(part["bevel"]) > 0:
            bevel = obj.modifiers.new("bevel", "BEVEL")
            bevel.width = float(part["bevel"]) / 100.0
            bevel.segments = 3
    obj.name = part["id"]
    mat = make_material(part)
    obj.data.materials.append(mat)
    if part.get("material_spec"):
        # Measured node counts (v1.11, M2): predicted host-side,
        # recorded here so the build report can prove they match.
        MAT_STATS[part["id"]] = {
            "breadcrumb": part["material_spec"]["breadcrumb"],
            "nodes": len(mat.node_tree.nodes),
        }
    if part["glow"]:
        bpy.ops.object.light_add(type="POINT", location=(mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] + 0.15))
        lamp = bpy.context.active_object
        lamp.name = part["id"] + " light"
        lamp.data.energy = 60
        lamp.data.color = (1.0, 0.75, 0.45)

if D.get("mesh_file"):
    # The real mesh behind the job (T21/T30): imported before stats,
    # LODs and the GLB export, so it counts everywhere the built parts
    # count. .obj/.stl sources are measured as centimetres on ingest
    # but Blender imports one source unit as one metre, so the
    # recorded import scale (0.01 for those formats) is applied here.
    before = set(bpy.data.objects.keys())
    fmt = D.get("mesh_format", "glb")
    if fmt == "glb":
        bpy.ops.import_scene.gltf(filepath=D["mesh_file"])
    elif fmt == "obj":
        # bpy.ops.import_scene.obj was removed in Blender 4.0; the
        # operator moved to bpy.ops.wm.obj_import. The hasattr check
        # keeps the script working on older Blender too.
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=D["mesh_file"])
        else:
            bpy.ops.import_scene.obj(filepath=D["mesh_file"])
    elif fmt == "stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=D["mesh_file"])
        else:
            bpy.ops.import_mesh.stl(filepath=D["mesh_file"])
    elif fmt == "fbx":
        bpy.ops.import_scene.fbx(filepath=D["mesh_file"])
    else:
        raise RuntimeError("unsupported mesh format: " + str(fmt))
    scale = float(D.get("mesh_scale") or 1.0)
    if scale != 1.0:
        for name in list(bpy.data.objects.keys()):
            if name not in before:
                obj = bpy.data.objects[name]
                obj.scale = (scale, scale, scale)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    print("DS_MESH", D["mesh_file"])

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 2.2
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))

# Rendering is the second phase (generate_render_script): the build
# script creates the camera objects and stops. The render script opens
# the saved .blend and renders them, so an engine fallback re-runs
# renders only (P3).
dg = bpy.context.evaluated_depsgraph_get()
stats = {"parts": {}, "total_tris": 0, "materials": MAT_STATS,
         "wear_nodes": len(_WEAR_GROUP.nodes) if _WEAR_GROUP else 0}
for o in scene.objects:
    if o.type != "MESH":
        continue
    eo = o.evaluated_get(dg)
    em = eo.to_mesh()
    tris = 0
    for poly in em.polygons:
        tris += max(0, len(poly.vertices) - 2)
    stats["parts"][o.name] = {"tris": tris, "uv": bool(o.data.uv_layers)}
    stats["total_tris"] += tris
    eo.to_mesh_clear()
with open(os.path.join(OUT, D["slug"] + ".stats.json"), "w") as fh:
    json.dump(stats, fh, indent=2)
print("DS_STATS total_tris", stats["total_tris"])

for cam in D["cameras"]:
    cam_data = bpy.data.cameras.new(cam["name"])
    cam_obj = bpy.data.objects.new(cam["name"], cam_data)
    scene.collection.objects.link(cam_obj)
    cam_obj.location = tuple(v / 100.0 for v in cam["position_cm"])
    look_at(cam_obj, tuple(v / 100.0 for v in cam["look_at_cm"]))

if D["game_ready"]:
    mesh_objs = [o for o in scene.objects if o.type == "MESH"]
    for ratio, suffix in ((0.5, "lod1"), (0.25, "lod2")):
        lod_path = os.path.join(OUT, D["slug"] + "_" + suffix + ".glb")
        # LOD export (v1.10, N53): DECIMATE in the stack, evaluated
        # by the exporter. The exporter special-cases armatures, so
        # skinned meshes keep their skins through export_apply=True
        # (verified against real Blender 4.5.9 on a shape-keyed MPFB
        # human: lod1/lod2 carry skins and fewer triangles than the
        # main GLB). Bevels are hidden for the LOD pass: baking the
        # default "soften" bevel into a LOD inflates it past the
        # main GLB (a 12-tri box became 54 tris at lod1). A hidden
        # modifier is skipped by the exporter; both visibility
        # flags are saved and restored, so the main export and the
        # renders are unaffected.
        added = []
        hidden = []
        for o in mesh_objs:
            for mod in o.modifiers:
                if mod.type == "BEVEL" and (
                        mod.show_viewport or mod.show_render):
                    hidden.append((mod, mod.show_viewport,
                                   mod.show_render))
                    mod.show_viewport = False
                    mod.show_render = False
            mod = o.modifiers.new("lod", "DECIMATE")
            mod.ratio = ratio
            added.append((o, mod))
        bpy.ops.export_scene.gltf(filepath=lod_path, export_format="GLB", export_apply=True)
        for o, mod in added:
            o.modifiers.remove(mod)
        for mod, viewport, render in hidden:
            mod.show_viewport = viewport
            mod.show_render = render
        print("DS_LOD", lod_path)
    ucx_objs = []
    for part in D["parts"]:
        mn = [v / 100.0 for v in part["min"]]
        sz = [max(v / 100.0, 0.01) for v in part["size"]]
        center = (mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] / 2)
        bpy.ops.mesh.primitive_cube_add(location=center)
        o = bpy.context.active_object
        o.dimensions = tuple(sz)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        o.name = "UCX_" + part["id"]
        ucx_objs.append(o)
    if D.get("character"):
        # The human gets a collision box too (v1.7, T26): a character
        # job may have no prop parts at all, and without this its
        # collision export would be empty.
        cb = D["character"]["bounds"]
        mn = [v / 100.0 for v in cb["min"]]
        sz = [max(v / 100.0, 0.01) for v in cb["size"]]
        center = (mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] / 2)
        bpy.ops.mesh.primitive_cube_add(location=center)
        o = bpy.context.active_object
        o.dimensions = tuple(sz)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        o.name = "UCX_character"
        ucx_objs.append(o)
    bpy.ops.object.select_all(action="DESELECT")
    for o in ucx_objs:
        o.select_set(True)
    collision_path = os.path.join(OUT, D["slug"] + "_collision.glb")
    bpy.ops.export_scene.gltf(filepath=collision_path, export_format="GLB", use_selection=True)
    bpy.ops.object.select_all(action="DESELECT")
    print("DS_COLLISION", collision_path)

glb_path = os.path.join(OUT, D["slug"] + ".glb")
bpy.ops.export_scene.gltf(filepath=glb_path, export_format="GLB")
print("DS_GLB", glb_path)
blend_path = os.path.join(OUT, D["slug"] + ".blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print("DS_BLEND", blend_path)
print("DS_DONE")
'''

_BUILD_TEMPLATE = (_BUILD_TEMPLATE_HEAD + _MATERIAL_TEMPLATE
                   + _BUILD_TEMPLATE_TAIL)


# The material preview (v1.11, M3): one cube with the material, one
# camera, one sun, a 128x96 render. Shares _MATERIAL_TEMPLATE with
# the build script, so the preview is the same recipe the build
# uses - not a second implementation that can drift.
_PREVIEW_TEMPLATE_HEAD = r'''
import bpy, math, os
from mathutils import Vector

D = __PAYLOAD__
OUT = D["out"]
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

world = bpy.data.worlds.new("DSPreviewWorld")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.09, 0.09, 0.1, 1.0)
bg.inputs[1].default_value = 0.8
'''

_PREVIEW_TEMPLATE_TAIL = r'''
part = D["parts"][0]
# A flat sample slab, not a cube: 60 x 40 cm shows several 12 cm
# board rows on the top face, where a 20 cm cube shows none.
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0.02))
obj = bpy.context.active_object
obj.dimensions = (0.6, 0.4, 0.04)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
obj.data.materials.append(make_material(part))

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))

cam_data = bpy.data.cameras.new("Preview")
cam_obj = bpy.data.objects.new("Preview", cam_data)
scene.collection.objects.link(cam_obj)
cam_obj.location = (0.35, -0.45, 0.42)
scene.camera = cam_obj
direction = Vector((0, 0, 0.02)) - cam_obj.location
cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

scene.render.engine = os.environ.get("DS_ENGINE", "BLENDER_EEVEE_NEXT")
if scene.render.engine == "CYCLES":
    scene.cycles.samples = 16
scene.render.resolution_x = 128
scene.render.resolution_y = 96
scene.render.filepath = os.path.join(OUT, D["slug"] + ".png")
bpy.ops.render.render(write_still=True)
print("DS_PREVIEW_OK", scene.render.filepath)
'''

_PREVIEW_TEMPLATE = (_PREVIEW_TEMPLATE_HEAD + _MATERIAL_TEMPLATE
                     + _PREVIEW_TEMPLATE_TAIL)


# The render phase (P3): opens the .blend the build script saved and
# renders each camera in it. The engine comes from DS_ENGINE (the
# caller picks it); resolution, samples and denoise from the payload.
_RENDER_TEMPLATE = r'''
import bpy, os

D = __PAYLOAD__
OUT = D["out"]
os.makedirs(OUT, exist_ok=True)

blend_path = os.path.join(OUT, D["slug"] + ".blend")
bpy.ops.wm.open_mainfile(filepath=blend_path)
scene = bpy.context.scene

engine = os.environ.get("DS_ENGINE", "BLENDER_EEVEE_NEXT")
try:
    scene.render.engine = engine
except Exception:
    scene.render.engine = "CYCLES"
if scene.render.engine == "CYCLES":
    scene.cycles.samples = int(D.get("samples", 24))
    if D.get("denoise"):
        scene.cycles.use_denoising = True
scene.render.resolution_x = int(D["res"])
scene.render.resolution_y = int(int(D["res"]) * 0.75)
scene.render.image_settings.file_format = "PNG"

for cam in D["cameras"]:
    cam_obj = scene.objects.get(cam["name"])
    if cam_obj is None:
        raise RuntimeError("camera not found in the built scene: " + str(cam["name"]))
    scene.camera = cam_obj
    scene.render.filepath = os.path.join(OUT, cam["file"])
    bpy.ops.render.render(write_still=True)
    print("DS_RENDER", scene.render.filepath)
print("DS_DONE")
'''


def _expected_outputs(desc: dict, slug: str) -> list[str]:
    data = scene_data(desc)
    names = [f"{slug}.blend", f"{slug}.glb", f"{slug}.stats.json"]
    if desc.get("game_ready"):
        names += [f"{slug}_lod1.glb", f"{slug}_lod2.glb", f"{slug}_collision.glb"]
    for cam in list(data["cameras"]) + [{"name": "Iso (auto)"}]:
        safe = _slug(str(cam["name"]))
        names.append(f"render-{safe}.png")
    return names


def _landed(out_dir: Path, names: list[str]) -> list[str]:
    return [n for n in names
            if (out_dir / n).exists() and (out_dir / n).stat().st_size > 0]


def _run_blender(exe: str, script_path: Path, engine: str, timeout: int = 1200,
                 cancel=None, on_line=None) -> str:
    """Run Blender once. A timeout is a result, not an explosion: EEVEE can
    hang forever on a headless machine with no GL context, and the caller
    needs the chance to fall back to Cycles instead of dying with it.
    `cancel`, when given, is a threading.Event - a set event kills the
    Blender process and raises governor.Cancelled (v1.9, N8). `on_line`,
    when given, is called with each output line as it arrives (v1.9, N9)."""
    from . import subproc

    env = dict(os.environ)
    env["DS_ENGINE"] = engine
    # Blender's embedded Python block-buffers a piped stdout; without
    # this the lines arrive in bursts instead of live.
    env["PYTHONUNBUFFERED"] = "1"
    try:
        _rc, out = subproc.run_cancellable(
            [exe, "--background", "--factory-startup", "--python", str(script_path)],
            timeout=timeout, cancel=cancel, on_line=on_line, env=env)
        return out
    except subprocess.TimeoutExpired as exc:
        out = exc.output if isinstance(exc.output, str) else ""
        return out + f"\nDS_TIMEOUT after {timeout}s ({engine})"


def _build_outputs(desc: dict, slug: str) -> list[str]:
    """The outputs the build phase must land (everything except the
    renders): the .blend, the GLB, stats, and LODs/collision when the
    job is game-ready."""
    names = [f"{slug}.blend", f"{slug}.glb", f"{slug}.stats.json"]
    if desc.get("game_ready"):
        names += [f"{slug}_lod1.glb", f"{slug}_lod2.glb", f"{slug}_collision.glb"]
    return names


def _render_outputs(desc: dict, slug: str) -> list[str]:
    data = scene_data(desc)
    return ["render-" + _slug(str(cam["name"])) + ".png"
            for cam in list(data["cameras"]) + [{"name": "Iso (auto)"}]]


class _BlenderStream:
    """Turns Blender's stdout into Bench progress (v1.9, N9).

    The build phase has no countable milestones, so its lines move
    the text while the fraction sits near the start of the export
    stage. The render phase counts Blender's "Saved:" lines against
    the renders this job expects, so the fraction advances as each
    render lands. Lines are throttled (Blender prints a "Fra:" line
    per tile, far faster than any eye) except "Saved:" lines, which
    always go through - they are the real milestones. The fraction
    never goes backwards, so a Cycles fallback re-run does not rewind
    the bar. The progress callback takes (stage, detail, fraction)
    with fraction relative to the export stage, 0.0 to 1.0.
    """

    def __init__(self, progress, expected_renders: int):
        self._progress = progress
        self._expected = max(1, expected_renders)
        self._saved = 0
        self._best = 0.0
        self._last_sent = 0.0

    def build_line(self, line: str) -> None:
        self._send(line, 0.1)

    def render_line(self, line: str) -> None:
        saved = "Saved:" in line
        if saved:
            self._saved += 1
        frac = 0.25 + 0.75 * (self._saved / self._expected)
        self._send(line, frac, force=saved)

    def _send(self, line: str, fraction: float, force: bool = False) -> None:
        if self._progress is None:
            return
        text = line.strip()
        if not text:
            return
        now = time.monotonic()
        if not force and now - self._last_sent < 0.5:
            return
        self._last_sent = now
        self._best = max(self._best, min(1.0, fraction))
        self._progress("export", text[:120], self._best)


def export_blender_with_build_script(desc: dict, out_dir: Path,
                                     build_script_fn, kind_word: str,
                                     cancel=None, progress=None) -> list[str]:
    """The shared two-phase Blender run (v1.7, T26): the scene exporter
    and the character exporter differ only in the build script they
    run and the word their error messages use - everything else
    (stale clearing, EEVEE probe, Cycles fallback, engine memory,
    build note) is the same run. `build_script_fn` takes
    (desc, out_dir, slug) and returns the build script text;
    `kind_word` is "scene" or "character".

    `cancel`, when given, is a threading.Event - a set event kills
    the Blender process at once and raises governor.Cancelled
    (v1.9, N8). `progress`, when given, is called with
    (stage, detail, fraction) as Blender's output streams in
    (v1.9, N9).

    Raises RuntimeError with a plain message when Blender is missing
    or the run produces nothing - the builder records it as a failed
    job.
    """
    exe = find_blender()
    if exe is None:
        raise RuntimeError(
            f"Blender was not found on this machine, so no 3D {kind_word} was built. "
            "Install Blender (or set DESIGNSHELL_BLENDER to its path) and run the build again.")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(desc["name"])
    build_script = build_script_fn(desc, out_dir, slug)
    build_path = out_dir / f"{slug}.blender.py"
    build_path.write_text(build_script, encoding="utf-8")
    render_script = generate_render_script(desc, out_dir, slug)
    render_path = out_dir / f"{slug}.render.py"
    render_path.write_text(render_script, encoding="utf-8")

    expected = _expected_outputs(desc, slug)
    # Freshness: a previous build's files in this folder must never be
    # mistaken for this run's outputs - clear the expected names first,
    # so "landed" always means "landed in THIS run".
    for name in expected:
        stale = out_dir / name
        if stale.exists():
            stale.unlink()

    stream = _BlenderStream(progress, len(_render_outputs(desc, slug)))

    # Phase one: build. The build script ignores DS_ENGINE, but the
    # runner needs a value; EEVEE is the name it has always carried.
    build_log = _run_blender(exe, build_path, "BLENDER_EEVEE_NEXT", timeout=1200,
                             cancel=cancel, on_line=stream.build_line)
    build_expected = _build_outputs(desc, slug)
    build_landed = _landed(out_dir, build_expected)
    missing_build = [n for n in build_expected if n not in build_landed]
    if missing_build:
        tail = "\n".join(build_log.strip().splitlines()[-8:])
        raise RuntimeError(
            f"Blender ran but the {kind_word} did not build - these outputs "
            f"did not land: {missing_build}. "
            f"Last lines from Blender:\n{tail}")

    # Phase two: render. The engine the job asked for decides whether
    # the probe runs at all (P4): only "auto" probes.
    render = desc.get("render") or {}
    engine_setting = render.get("engine", "auto")
    probe_note = ""
    if engine_setting == "cycles":
        engine_used = "CYCLES"
    elif engine_setting == "eevee":
        engine_used = "BLENDER_EEVEE_NEXT"
    else:
        engine_used = probe_engine(exe, cancel=cancel)
        probe_note = ("EEVEE probe: "
                      + ("EEVEE renders on this machine" if engine_used == "BLENDER_EEVEE_NEXT"
                         else "EEVEE cannot render here, Cycles chosen") + ". ")
    timeout = 600 if engine_used == "BLENDER_EEVEE_NEXT" else 1500
    render_log = _run_blender(exe, render_path, engine_used, timeout=timeout,
                              cancel=cancel, on_line=stream.render_line)
    render_expected = _render_outputs(desc, slug)
    render_landed = _landed(out_dir, render_expected)
    if len(render_landed) < len(render_expected) and engine_used == "BLENDER_EEVEE_NEXT":
        # EEVEE needs a GPU/GL context some headless machines cannot
        # give (or hangs waiting for one). Cycles on CPU always can -
        # slower, but the renders are real. Only the render phase
        # re-runs; the build stands (P3).
        engine_used = "CYCLES"
        render_log = _run_blender(exe, render_path, engine_used, timeout=1500,
                                  cancel=cancel, on_line=stream.render_line)
        render_landed = _landed(out_dir, render_expected)
    landed = _landed(out_dir, expected)
    missing = [n for n in expected if n not in landed]
    if missing:
        tail = "\n".join(render_log.strip().splitlines()[-8:])
        raise RuntimeError(
            f"Blender built the {kind_word} but these renders did not land: {missing}. "
            f"Last lines from Blender:\n{tail}")
    # Remember which engine actually worked (P4): the Settings window
    # shows it, so the user can see what their machine does.
    try:
        from . import settings as settings_mod
        workspace = out_dir.parent.parent.parent
        if (workspace / "jobs").exists():
            settings_mod.record_render_engine(workspace, engine_used)
    except (OSError, ValueError):
        pass
    note = (f"Built by Blender ({engine_used}) from {desc['name']}. "
            f"{probe_note}"
            f"Build script: {build_path.name}. Render script: {render_path.name}. "
            f"Outputs: {', '.join(sorted(landed))}.\n")
    (out_dir / f"{slug}.build-note.txt").write_text(note, encoding="utf-8")
    return sorted(landed + [build_path.name, render_path.name, f"{slug}.build-note.txt"])


def export_blender_scene(desc: dict, out_dir: Path,
                         cancel=None, progress=None) -> list[str]:
    """Exporter entry point: (desc, out_dir) -> output file names.

    Two phases (P3): the build script runs once (geometry, stats,
    LODs, GLB, .blend), then the render script renders the cameras.
    The render engine comes from the description's resolved "render"
    block: "cycles" runs Cycles only; "eevee" tries EEVEE then
    Cycles; "auto" (the default) runs the EEVEE probe (P4) first so
    a headless machine that cannot do EEVEE goes straight to Cycles
    instead of burning the EEVEE timeout. A render fallback re-runs
    renders only - the build is never repeated.

    `cancel` kills the Blender process at once (v1.9, N8);
    `progress` streams Blender's output as (stage, detail, fraction)
    (v1.9, N9).

    Raises RuntimeError with a plain message when Blender is missing
    or the run produces nothing - the builder records it as a failed
    job.
    """
    return export_blender_with_build_script(desc, out_dir, generate_script, "scene",
                                            cancel=cancel, progress=progress)
