"""The making engine, step one: Description File -> real 3D scene.

Generates a Blender Python script from the description (parts become real
meshes at true scale, cameras come from the description's own `cameras`,
lights from the style), runs Blender headless, and collects the outputs:

    <slug>.blend            the scene, openable in Blender
    <slug>.glb              the model, for Unreal / the web / anywhere
    render-<camera>.png     one render per saved camera + an auto iso
    <slug>.blender.py       the exact script that built it (kept, auditable)

Fail-loud contract: if Blender is not installed, this raises with a plain
message and the job fails — it never pretends a scene was built.
Rendering tries EEVEE first (fast) and falls back to Cycles CPU when the
EEVEE renders do not land (headless machines without a GPU context),
which is recorded in the outputs note file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

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
    if isinstance(name, str) and name.startswith("#") and len(name) == 7:
        return name, 0.0, 0.8
    if isinstance(name, str) and name in MATERIALS:
        return MATERIALS[name]
    return _color_for(part.get("id", ""), part.get("type", "")), 0.0, 0.85


# Procedural surface textures, generated inside Blender from noise — no
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
    """Procedural texture name for a part, or None for a flat material."""
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


def generate_script(desc: dict, out_dir: Path, slug: str) -> str:
    """Build the Blender Python script for a description. Pure function."""
    data = scene_data(desc)
    style = desc.get("style", {}) or {}
    accent = str(style.get("accent", "#e0a458"))
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
        "accent": accent, "surface": surface,
        "out": str(out_dir), "slug": slug,
        "game_ready": bool(desc.get("game_ready")),
        "res": int((desc.get("budget", {}) or {}).get("preview_px", 640)),
    }
    script = _SCRIPT_TEMPLATE.replace("__PAYLOAD__", repr(payload))
    return script.replace("__SURFACE__", repr(_hex_rgb(surface)))


_SCRIPT_TEMPLATE = r'''
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

def _shade(hex_color, k):
    r = int(hex_color[1:3], 16) / 255.0 * k
    g = int(hex_color[3:5], 16) / 255.0 * k
    b = int(hex_color[5:7], 16) / 255.0 * k
    return (max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b)), 1.0)

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
    texture = part.get("texture")
    if texture:
        # Procedural surface from noise, in object coordinates — no image
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

for part in D["parts"]:
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
    obj.data.materials.append(make_material(part))
    if part["glow"]:
        bpy.ops.object.light_add(type="POINT", location=(mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] + 0.15))
        lamp = bpy.context.active_object
        lamp.name = part["id"] + " light"
        lamp.data.energy = 60
        lamp.data.color = (1.0, 0.75, 0.45)

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = 2.2
sun = bpy.data.objects.new("Sun", sun_data)
scene.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(-35))

engine = os.environ.get("DS_ENGINE", "BLENDER_EEVEE_NEXT")
try:
    scene.render.engine = engine
except Exception:
    scene.render.engine = "CYCLES"
if scene.render.engine == "CYCLES":
    scene.cycles.samples = 24
scene.render.resolution_x = D["res"]
scene.render.resolution_y = int(D["res"] * 0.75)
scene.render.image_settings.file_format = "PNG"

dg = bpy.context.evaluated_depsgraph_get()
stats = {"parts": {}, "total_tris": 0}
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
    scene.camera = cam_obj
    scene.render.filepath = os.path.join(OUT, cam["file"])
    bpy.ops.render.render(write_still=True)
    print("DS_RENDER", scene.render.filepath)

if D["game_ready"]:
    mesh_objs = [o for o in scene.objects if o.type == "MESH"]
    for ratio, suffix in ((0.5, "lod1"), (0.25, "lod2")):
        added = []
        for o in mesh_objs:
            mod = o.modifiers.new("lod", "DECIMATE")
            mod.ratio = ratio
            added.append((o, mod))
        lod_path = os.path.join(OUT, D["slug"] + "_" + suffix + ".glb")
        bpy.ops.export_scene.gltf(filepath=lod_path, export_format="GLB", export_apply=True)
        for o, mod in added:
            o.modifiers.remove(mod)
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


def _run_blender(exe: str, script_path: Path, engine: str, timeout: int = 1200) -> str:
    """Run Blender once. A timeout is a result, not an explosion: EEVEE can
    hang forever on a headless machine with no GL context, and the caller
    needs the chance to fall back to Cycles instead of dying with it."""
    env = dict(os.environ)
    env["DS_ENGINE"] = engine
    try:
        proc = subprocess.run(
            [exe, "--background", "--factory-startup", "--python", str(script_path)],
            capture_output=True, text=True, timeout=timeout, env=env)
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return out + "\n" + err + f"\nDS_TIMEOUT after {timeout}s ({engine})"


def export_blender_scene(desc: dict, out_dir: Path) -> list[str]:
    """Exporter entry point: (desc, out_dir) -> output file names.

    Raises RuntimeError with a plain message when Blender is missing or
    the run produces nothing — the builder records it as a failed job.
    """
    exe = find_blender()
    if exe is None:
        raise RuntimeError(
            "Blender was not found on this machine, so no 3D scene was built. "
            "Install Blender (or set DESIGNSHELL_BLENDER to its path) and run the build again.")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(desc["name"])
    script = generate_script(desc, out_dir, slug)
    script_path = out_dir / f"{slug}.blender.py"
    script_path.write_text(script, encoding="utf-8")

    expected = _expected_outputs(desc, slug)
    # Freshness: a previous build's files in this folder must never be
    # mistaken for this run's outputs — clear the expected names first,
    # so "landed" always means "landed in THIS run".
    for name in expected:
        stale = out_dir / name
        if stale.exists():
            stale.unlink()
    engine_used = "BLENDER_EEVEE_NEXT"
    log = _run_blender(exe, script_path, engine_used, timeout=600)
    landed = _landed(out_dir, expected)
    renders = [n for n in landed if n.startswith("render-")]
    if len(renders) < len([n for n in expected if n.startswith("render-")]):
        # EEVEE needs a GPU/GL context some headless machines cannot give
        # (or hangs waiting for one). Cycles on CPU always can — slower,
        # but the renders are real.
        engine_used = "CYCLES"
        log = _run_blender(exe, script_path, engine_used, timeout=1500)
        landed = _landed(out_dir, expected)
    missing = [n for n in expected if n not in landed]
    if missing:
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise RuntimeError(
            f"Blender ran but these outputs did not land: {missing}. "
            f"Last lines from Blender:\n{tail}")
    note = (f"Built by Blender ({engine_used}) from {desc['name']}. "
            f"Script: {script_path.name}. Outputs: {', '.join(sorted(landed))}.\n")
    (out_dir / f"{slug}.build-note.txt").write_text(note, encoding="utf-8")
    return sorted(landed + [script_path.name, f"{slug}.build-note.txt"])
