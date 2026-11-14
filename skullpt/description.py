"""The Description File — the heart of SKULLPT.

A Description File is a JSON document that fully describes one thing to be
made (a UI component, a website, a graphic, a character, a scene) BEFORE any
tool runs. It holds: named parts with parameters, style tokens, machine-
checkable constraints, a resource budget sized for the user's PC, and the
rubric the result will be judged against.

It is what the user approves (not raw code), what every exporter reads
(one description -> many outputs), and what gets filed in the Asset Library
next to the asset so the asset can be re-made or varied later.
"""
from __future__ import annotations

import json
from pathlib import Path

KINDS = ("ui_component", "website", "graphic", "character", "scene", "project")
SHAPES = ("box", "cylinder", "sphere", "extrude", "lathe", "tube")
TEXTURES = ("none", "wood_grain", "brushed_metal", "worn_leather")
# Render engines the Blender exporter knows (v1.6, T25): "auto" probes
# EEVEE and falls back to Cycles when it cannot run; the other two
# pin one engine (Cycles never tries EEVEE at all).
RENDER_ENGINES = ("auto", "eevee", "cycles")
# Mesh formats ingest understands and the Blender build can import
# (v1.6, T21/T30). The description's top-level "mesh" block names one.
MESH_FORMATS = ("glb", "obj", "fbx", "stl")
# Character Studio vocabulary (v1.7, T26): the MPFB2 macro sliders,
# rigs, and skin. The sliders are the MPFB macro set (0.0-1.0 each);
# race is three flat keys here and maps to MPFB's nested race dict in
# the exporter. Rigs are MPFB's built-in rig names — the files
# data/rigs/standard/rig.<name>.json (rigify/rig.<name>.json for the
# rigify.* names) inside the MPFB extension. Skins are the .mhmat file
# names from the MakeHuman system assets pack (23 of them).
CHARACTER_SLIDERS = ("gender", "age", "muscle", "weight", "proportions",
                     "height", "cupsize", "firmness",
                     "race_asian", "race_caucasian", "race_african")
CHARACTER_RIGS = ("game_engine", "game_engine_with_breast", "default",
                  "default_no_toes", "cmu_mb", "mixamo", "mixamo_unity",
                  "openpose", "rigify.human", "rigify.human_toes")
CHARACTER_DEFAULT_RIG = "game_engine"
# Triangles a Character Studio human adds to the pre-build budget
# estimate. Measured from a real MPFB 2.0.18 build in the sandbox:
# body 26,756 + low-poly eyes 172 + teeth 7,120 = 34,048. Shape keys
# move vertices, they do not add faces, so this is the real count for
# the studio's fixed body parts — and the game-ready gate still
# enforces the exact post-build total.
CHARACTER_TRIANGLE_ESTIMATE = 34048
REQUIRED_TOP = ("name", "kind", "parts")
PART_REQUIRED = ("id", "type")


class DescriptionError(ValueError):
    """Raised when a Description File is invalid; message lists every problem."""


def validate(desc: dict) -> list[str]:
    """Return a list of problems with `desc` (empty list = valid)."""
    problems: list[str] = []
    if not isinstance(desc, dict):
        return ["description must be a JSON object"]
    for key in REQUIRED_TOP:
        if key not in desc:
            problems.append(f"missing required field: {key}")
    kind = desc.get("kind")
    if kind is not None and kind not in KINDS:
        problems.append(f"kind must be one of {KINDS}, got {kind!r}")
    parts = desc.get("parts")
    if parts is not None:
        # A character job may have no prop parts at all (v1.7, T26) —
        # the human comes from the character block, not from parts.
        if not isinstance(parts, list) or (not parts and kind != "character"):
            problems.append("parts must be a non-empty list")
        else:
            seen: set[str] = set()
            for i, part in enumerate(parts):
                if not isinstance(part, dict):
                    problems.append(f"parts[{i}] must be an object")
                    continue
                for key in PART_REQUIRED:
                    if key not in part:
                        problems.append(f"parts[{i}] missing required field: {key}")
                pid = part.get("id")
                if pid is not None:
                    if pid in seen:
                        problems.append(f"duplicate part id: {pid!r}")
                    seen.add(pid)
                shape = part.get("shape", "box")
                if shape not in SHAPES:
                    problems.append(f"parts[{i}].shape must be one of {SHAPES}, got {shape!r}")
                if shape == "extrude":
                    profile = part.get("profile_cm")
                    if not (isinstance(profile, list) and len(profile) >= 3 and all(
                            isinstance(pt, list) and len(pt) == 2
                            and all(isinstance(v, (int, float)) for v in pt)
                            for pt in profile)):
                        problems.append(
                            f"parts[{i}] ({pid!r}) shape=extrude needs profile_cm: "
                            "a list of at least 3 [x, y] points")
                if shape == "lathe":
                    profile = part.get("profile_cm")
                    if not (isinstance(profile, list) and len(profile) >= 2 and all(
                            isinstance(pt, list) and len(pt) == 2
                            and all(isinstance(v, (int, float)) for v in pt)
                            and pt[0] >= 0
                            for pt in profile)):
                        problems.append(
                            f"parts[{i}] ({pid!r}) shape=lathe needs profile_cm: "
                            "a list of at least 2 [radius, z] points, radius >= 0")
                if shape == "tube":
                    path = part.get("path_cm")
                    if not (isinstance(path, list) and len(path) >= 2 and all(
                            isinstance(pt, list) and len(pt) == 3
                            and all(isinstance(v, (int, float)) for v in pt)
                            for pt in path)):
                        problems.append(
                            f"parts[{i}] ({pid!r}) shape=tube needs path_cm: "
                            "a list of at least 2 [x, y, z] points")
                    radius = part.get("radius_cm", 2)
                    if not isinstance(radius, (int, float)) or radius <= 0:
                        problems.append(f"parts[{i}] ({pid!r}) radius_cm must be a positive number")
                bevel = part.get("bevel_cm")
                if bevel is not None and (not isinstance(bevel, (int, float)) or bevel < 0):
                    problems.append(f"parts[{i}] ({pid!r}) bevel_cm must be a non-negative number")
                texture = part.get("texture")
                if texture is not None and texture not in TEXTURES:
                    problems.append(
                        f"parts[{i}] ({pid!r}) texture must be one of {TEXTURES}, got {texture!r}")
                repeat = part.get("repeat")
                if repeat is not None:
                    if not isinstance(repeat, dict):
                        problems.append(f"parts[{i}] ({pid!r}) repeat must be an object")
                    else:
                        count = repeat.get("count")
                        if not isinstance(count, int) or count < 1:
                            problems.append(f"parts[{i}] ({pid!r}) repeat.count must be an int >= 1")
                        step = repeat.get("step_cm")
                        if not (isinstance(step, list) and len(step) == 3
                                and all(isinstance(v, (int, float)) for v in step)):
                            problems.append(f"parts[{i}] ({pid!r}) repeat.step_cm must be [x, y, z] numbers")
                mirror = part.get("mirror")
                if mirror is not None:
                    if not isinstance(mirror, dict):
                        problems.append(f"parts[{i}] ({pid!r}) mirror must be an object")
                    else:
                        if mirror.get("axis") not in ("x", "y"):
                            problems.append(f"parts[{i}] ({pid!r}) mirror.axis must be 'x' or 'y'")
                        if not isinstance(mirror.get("at_cm"), (int, float)):
                            problems.append(f"parts[{i}] ({pid!r}) mirror.at_cm must be a number")
                material = part.get("material")
                if material is not None and not isinstance(material, str):
                    problems.append(f"parts[{i}].material must be a preset name or #hex string")
                # A part ingested from a real mesh carries its measured
                # mesh data (v1.6, T30): the triangle count is real, not
                # an estimate, and the budget gate uses it as such.
                mesh = part.get("mesh")
                if mesh is not None:
                    if not isinstance(mesh, dict):
                        problems.append(f"parts[{i}] ({pid!r}) mesh must be an object")
                    else:
                        for num_key in ("triangles", "vertices", "node"):
                            val = mesh.get(num_key)
                            if val is not None and (not isinstance(val, int) or val < 0):
                                problems.append(
                                    f"parts[{i}] ({pid!r}) mesh.{num_key} must be "
                                    "a non-negative int")
                        src = mesh.get("source")
                        if src is not None and src not in MESH_FORMATS:
                            problems.append(
                                f"parts[{i}] ({pid!r}) mesh.source must be one of "
                                f"{MESH_FORMATS}, got {src!r}")
    part_ids = {p.get("id") for p in parts if isinstance(p, dict)} if isinstance(parts, list) else set()
    for i, con in enumerate(desc.get("constraints", []) or []):
        if not isinstance(con, dict) or "type" not in con:
            problems.append(f"constraints[{i}] must be an object with a 'type'")
            continue
        for ref_key in ("part", "on", "inside"):
            ref = con.get(ref_key)
            if ref is not None and ref not in part_ids:
                problems.append(f"constraints[{i}] references unknown part {ref!r}")
    for i, cam in enumerate(desc.get("cameras", []) or []):
        if not isinstance(cam, dict):
            problems.append(f"cameras[{i}] must be an object")
            continue
        if not isinstance(cam.get("name"), str) or not cam.get("name", "").strip():
            problems.append(f"cameras[{i}] needs a non-empty 'name'")
        for key in ("position_cm", "look_at_cm"):
            val = cam.get(key)
            if not (isinstance(val, list) and len(val) == 3
                    and all(isinstance(v, (int, float)) for v in val)):
                problems.append(f"cameras[{i}].{key} must be [x, y, z] numbers")
    budget = desc.get("budget", {}) or {}
    if not isinstance(budget, dict):
        problems.append("budget must be an object")
    else:
        for key, val in budget.items():
            if not isinstance(val, (int, float)) or val < 0:
                problems.append(f"budget.{key} must be a non-negative number")
    rubric = desc.get("rubric", []) or []
    if not isinstance(rubric, list) or not all(isinstance(r, str) for r in rubric):
        problems.append("rubric must be a list of strings")
    # Render settings (v1.6, T25): how the renders are made, not what
    # is built — so changing them never re-opens the plan gate.
    render = desc.get("render")
    if render is not None:
        if not isinstance(render, dict):
            problems.append("render must be an object")
        else:
            engine = render.get("engine")
            if engine is not None and engine not in RENDER_ENGINES:
                problems.append(f"render.engine must be one of {RENDER_ENGINES}, got {engine!r}")
            res = render.get("resolution_px")
            if res is not None and (not isinstance(res, int) or res < 128 or res > 4096):
                problems.append("render.resolution_px must be an int from 128 to 4096")
            samples = render.get("samples")
            if samples is not None and (not isinstance(samples, int) or samples < 1 or samples > 512):
                problems.append("render.samples must be an int from 1 to 512")
            denoise = render.get("denoise")
            if denoise is not None and not isinstance(denoise, bool):
                problems.append("render.denoise must be true or false")
    # Character Studio (v1.7, T26): a character description carries a
    # "character" block — 11 MPFB macro sliders, a skin, a rig. The
    # skin is REQUIRED: without one the human stays grey. Missing
    # sliders default to 0.5 in the exporter (MPFB's own defaults).
    character = desc.get("character")
    if kind == "character":
        if character is None:
            problems.append("kind 'character' needs a character block: "
                            "11 macro sliders, a skin, and a rig")
        elif not isinstance(character, dict):
            problems.append("character must be an object")
        else:
            for slider in CHARACTER_SLIDERS:
                val = character.get(slider)
                if val is not None and (not isinstance(val, (int, float))
                                        or val < 0.0 or val > 1.0):
                    problems.append(
                        f"character.{slider} must be a number from 0.0 to 1.0")
            skin = character.get("skin")
            if not isinstance(skin, str) or not skin.strip():
                problems.append("character.skin is required: the skin .mhmat "
                                "file name, e.g. young_caucasian_male.mhmat")
            rig = character.get("rig")
            if rig is not None and rig not in CHARACTER_RIGS:
                problems.append(
                    f"character.rig must be one of {CHARACTER_RIGS}, got {rig!r}")
    elif character is not None:
        problems.append("character block is only for kind 'character'")
    # The real mesh behind an ingested job (v1.6, T21/T30): the file
    # the Blender build imports instead of rebuilding from boxes.
    mesh = desc.get("mesh")
    if mesh is not None:
        if not isinstance(mesh, dict):
            problems.append("mesh must be an object")
        else:
            if not isinstance(mesh.get("file"), str) or not mesh.get("file", "").strip():
                problems.append("mesh.file must be a non-empty path string")
            fmt = mesh.get("format")
            if fmt is not None and fmt not in MESH_FORMATS:
                problems.append(f"mesh.format must be one of {MESH_FORMATS}, got {fmt!r}")
            scale = mesh.get("import_scale")
            if scale is not None and (not isinstance(scale, (int, float)) or scale <= 0):
                problems.append("mesh.import_scale must be a positive number")
    return problems


# Triangle estimates for the pre-build budget gate (v1.4). These mirror
# the Blender exporter's builders exactly — same segment counts, same
# face counts — so the estimate is what Blender will actually build,
# before bevel modifiers. The counts below are the BASE mesh:
#   box:      a cube, 6 quads = 12 tris
#   cylinder: primitive_cylinder_add(vertices=24): 24 side quads + two
#             24-gon caps = 48 + 2*(24-2) = 92 tris
#   sphere:   primitive_uv_sphere_add(segments=24, ring_count=12):
#             24*(12-2) quads + two 24-tri pole fans = 528 tris
#   extrude:  n-gon profile: two (n-2)-tri caps + n side quads = 4n-4
#   lathe:    R profile points, 20 segments: (R-1)*20 quads + a 20-tri
#             fan cap at each open end (radius > 0.001)
#   tube:     n path points, 10 segments: (n-1)*10 quads, no caps
# A shape the exporter falls back to a cube for (missing profile/path,
# unknown shape) estimates as a cube (12). Bevel modifiers add
# triangles on top, so the estimate is a LOWER bound: the pre-build
# gate refuses only when the build is certainly over budget, and the
# game-ready gate enforces the exact post-build count.
_CYLINDER_VERTICES = 24
_SPHERE_SEGMENTS = 24
_SPHERE_RINGS = 12
_LATHE_SEGMENTS = 20
_TUBE_SEGMENTS = 10


def estimate_triangles(part: dict) -> int:
    """Triangles the Blender exporter will build for one part (base mesh,
    before bevel modifiers — a lower bound on the real count). A part
    ingested from a real mesh carries its measured count (T30) — that
    is what Blender imports, so it is used as-is instead of the box
    estimate of 12, and the budget gate stops under-counting imports."""
    mesh = part.get("mesh") or {}
    measured = mesh.get("triangles")
    if isinstance(measured, int) and measured >= 0:
        return measured
    shape = part.get("shape", "box")
    if shape == "cylinder":
        v = _CYLINDER_VERTICES
        return 2 * v + 2 * (v - 2)
    if shape == "sphere":
        return 2 * _SPHERE_SEGMENTS * (_SPHERE_RINGS - 1)
    if shape == "extrude":
        profile = part.get("profile_cm")
        if isinstance(profile, list) and len(profile) >= 3:
            return 4 * len(profile) - 4
        return 12
    if shape == "lathe":
        profile = part.get("profile_cm")
        if isinstance(profile, list) and len(profile) >= 2:
            total = (_LATHE_SEGMENTS * 2) * (len(profile) - 1)
            if float(profile[0][0]) > 0.001:
                total += _LATHE_SEGMENTS
            if float(profile[-1][0]) > 0.001:
                total += _LATHE_SEGMENTS
            return total
        return 12
    if shape == "tube":
        path = part.get("path_cm")
        if isinstance(path, list) and len(path) >= 2:
            return (_TUBE_SEGMENTS * 2) * (len(path) - 1)
        return 12
    return 12  # box, and anything the exporter falls back to a cube for


def estimate_description_triangles(desc: dict) -> int:
    """Estimated triangles for a whole description, counting every
    concrete part repeat/mirror expansion produces — plus the human
    for a character description (v1.7, T26)."""
    from .expand import expand_parts

    total = sum(estimate_triangles(p) for p in expand_parts(desc))
    if desc.get("kind") == "character":
        total += CHARACTER_TRIANGLE_ESTIMATE
    return total


def load(path: str | Path) -> dict:
    """Load and validate a Description File from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate(data)
    if problems:
        raise DescriptionError("Invalid Description File:\n- " + "\n- ".join(problems))
    data.setdefault("style", {})
    data.setdefault("cameras", [])
    data.setdefault("constraints", [])
    data.setdefault("budget", {})
    data.setdefault("rubric", [])
    data.setdefault("tags", [])
    return data


def dump(desc: dict, path: str | Path) -> None:
    problems = validate(desc)
    if problems:
        raise DescriptionError("Invalid Description File:\n- " + "\n- ".join(problems))
    Path(path).write_text(json.dumps(desc, indent=2) + "\n", encoding="utf-8")


def summarize(desc: dict) -> str:
    """Render a plain-English summary — this is what the user approves."""
    lines = [f"{desc['name']}  [{desc['kind']}]"]
    if desc.get("summary"):
        lines.append(desc["summary"])
    style = desc.get("style") or {}
    if style:
        tokens = ", ".join(f"{k}={v}" for k, v in sorted(style.items()))
        lines.append(f"Style: {tokens}")
    character = desc.get("character")
    if character:
        lines.append(f"Character: skin={character.get('skin')}, "
                     f"rig={character.get('rig', CHARACTER_DEFAULT_RIG)}")
    lines.append(f"Parts ({len(desc['parts'])}):")
    for part in desc["parts"]:
        params = {k: v for k, v in part.items() if k not in ("id", "type")}
        detail = ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
        lines.append(f"  - {part['id']} ({part['type']})" + (f": {detail}" if detail else ""))
    if desc.get("cameras"):
        lines.append("Cameras (his angles, set in the render window):")
        for cam in desc["cameras"]:
            lines.append(f"  - {cam['name']}: at {cam.get('position_cm')} looking at {cam.get('look_at_cm')}")
    if desc.get("constraints"):
        lines.append("Constraints (machine-checked):")
        for con in desc["constraints"]:
            bits = " ".join(f"{k}={v}" for k, v in con.items())
            lines.append(f"  - {bits}")
    if desc.get("budget"):
        lines.append("Budget: " + ", ".join(f"{k}<={v}" for k, v in sorted(desc["budget"].items())))
    if desc.get("rubric"):
        lines.append("Judged on:")
        for item in desc["rubric"]:
            lines.append(f"  - {item}")
    return "\n".join(lines)
