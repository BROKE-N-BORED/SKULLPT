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
        if not isinstance(parts, list) or not parts:
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
    before bevel modifiers — a lower bound on the real count)."""
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
    concrete part repeat/mirror expansion produces."""
    from .expand import expand_parts

    return sum(estimate_triangles(p) for p in expand_parts(desc))


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
