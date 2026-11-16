"""Scene geometry for the Live Render Window.

Joshua's standing requirement (2026-11-10): a live render window where he
can EXAMINE anything built or being built while work is in progress - a
free inspection camera (tilt, rotate, spin on every axis, zoom, pan) plus
scene cameras HE can reposition, so the renders that get filed come from
angles he chose.

The window itself is the Qt OpenGL viewport in qtbench.py; this module
keeps the geometry it draws on - part boxes, scene bounds, the scene
data dict - and the camera helpers that save named cameras back into
the Description File, so they travel with the asset into the Library.

The old self-contained HTML page and its local server were removed in
v1.9 (SKULLPT is a native Linux desktop app - no web UI, by standing
rule). The camera contract (inspection view + named scene cameras
stored in the description) does not change.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import description as desc_mod
from .expand import expand_parts


# ---------------------------------------------------------------- geometry

def part_box(part: dict, index: int) -> dict:
    """Return {id, type, min:[x,y,z], size:[w,d,h], auto:bool, ...} for a part.

    Parts without position/size (e.g. flat UI descriptions) are auto-placed
    in a row so they still show up; `auto` flags that honestly. Lathe parts
    derive their footprint from the profile (position_cm is the centre of
    the base); tube parts derive theirs from the path's bounding box.
    """
    shape = part.get("shape", "box")
    pos = part.get("position_cm")
    size = part.get("size_cm")
    auto = False
    if shape == "lathe" and isinstance(part.get("profile_cm"), list) and part["profile_cm"]:
        radii = [float(pt[0]) for pt in part["profile_cm"]]
        heights = [float(pt[1]) for pt in part["profile_cm"]]
        r_max, h_max = max(radii), max(heights)
        if isinstance(pos, list) and len(pos) == 3:
            pos = [pos[0] - r_max, pos[1] - r_max, pos[2]]
        size = [2 * r_max, 2 * r_max, h_max]
    elif shape == "tube" and isinstance(part.get("path_cm"), list) and part["path_cm"]:
        radius = float(part.get("radius_cm", 2))
        path = part["path_cm"]
        pos = [min(float(pt[i]) for pt in path) - radius for i in range(3)]
        size = [max(float(pt[i]) for pt in path) - min(float(pt[i]) for pt in path) + 2 * radius
                for i in range(3)]
    if not (isinstance(pos, list) and len(pos) == 3):
        pos = [index * 130, 0, 0]
        auto = True
    if not (isinstance(size, list) and len(size) == 3):
        size = [100, 60, 100]
        auto = True
    return {
        "id": part.get("id", f"part{index}"),
        "type": part.get("type", "part"),
        "min": [float(v) for v in pos],
        "size": [float(v) for v in size],
        "auto": auto,
        "shape": shape,
        "profile": part.get("profile_cm"),
        "path": part.get("path_cm"),
        "radius": part.get("radius_cm"),
    }


def scene_bounds(boxes: list[dict]) -> dict:
    """Bounding box + center of the whole scene, in cm."""
    if not boxes:
        return {"min": [0, 0, 0], "max": [100, 100, 100], "center": [50, 50, 50], "span": 100.0}
    lo = [min(b["min"][i] for b in boxes) for i in range(3)]
    hi = [max(b["min"][i] + b["size"][i] for b in boxes) for i in range(3)]
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3)) or 100.0
    return {"min": lo, "max": hi, "center": center, "span": float(span)}


def scene_data(desc: dict) -> dict:
    """Everything the viewer page needs, as one JSON-able dict."""
    boxes = [part_box(p, i) for i, p in enumerate(expand_parts(desc))]
    cameras = []
    for cam in desc.get("cameras", []) or []:
        if isinstance(cam, dict) and "name" in cam:
            cameras.append({
                "name": str(cam["name"]),
                "position_cm": [float(v) for v in cam.get("position_cm", [0, 0, 0])],
                "look_at_cm": [float(v) for v in cam.get("look_at_cm", [0, 0, 0])],
            })
    return {
        "name": desc.get("name", "Untitled"),
        "kind": desc.get("kind", ""),
        "style": desc.get("style", {}) or {},
        "boxes": boxes,
        "bounds": scene_bounds(boxes),
        "cameras": cameras,
    }


# ----------------------------------------------------------------- cameras

def _vec3(value, field: str) -> list[float]:
    if not (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) for v in value)):
        raise desc_mod.DescriptionError(f"camera {field} must be [x, y, z] numbers")
    return [float(v) for v in value]


def upsert_camera(desc: dict, camera: dict) -> dict:
    """Insert or replace a named camera in a description (in place)."""
    name = camera.get("name")
    if not isinstance(name, str) or not name.strip():
        raise desc_mod.DescriptionError("camera needs a non-empty name")
    entry = {
        "name": name.strip(),
        "position_cm": _vec3(camera.get("position_cm"), "position_cm"),
        "look_at_cm": _vec3(camera.get("look_at_cm"), "look_at_cm"),
    }
    cameras = desc.setdefault("cameras", [])
    for i, cam in enumerate(cameras):
        if isinstance(cam, dict) and cam.get("name") == entry["name"]:
            cameras[i] = entry
            return entry
    cameras.append(entry)
    return entry


def delete_camera(desc: dict, name: str) -> bool:
    cameras = desc.get("cameras", []) or []
    kept = [c for c in cameras if not (isinstance(c, dict) and c.get("name") == name)]
    desc["cameras"] = kept
    return len(kept) != len(cameras)


