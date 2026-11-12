"""Repeat + mirror expansion — one written part, many placed parts.

A part may carry:
  "repeat": {"count": N, "step_cm": [dx, dy, dz]}
      -> N copies in a row; the first keeps the part's id and position,
         copies are `<id>_2` .. `<id>_N`, each offset by step * (n-1).
  "mirror": {"axis": "x"|"y", "at_cm": A}
      -> one extra copy `<id>_mirrored`, reflected across the plane
         axis = A (the part's own span is accounted for, so a part ending
         exactly at the plane mirrors flush against itself).

Expansion happens everywhere geometry is consumed (render window, Blender
exporter, game-ready gate) so what Joshua sees is what gets built. The
Description File on disk keeps the compact written form; constraints keep
referring to the written (base) part ids.
"""
from __future__ import annotations

import copy


def _repeat_copies(part: dict) -> list[dict]:
    repeat = part.get("repeat")
    base = {k: v for k, v in part.items() if k != "repeat"}
    if not isinstance(repeat, dict):
        return [base]
    count = int(repeat.get("count", 1))
    step = repeat.get("step_cm", [0, 0, 0])
    out = [base]
    for n in range(2, count + 1):
        clone = copy.deepcopy(base)
        clone["id"] = f"{part['id']}_{n}"
        pos = clone.get("position_cm")
        if isinstance(pos, list) and len(pos) == 3:
            clone["position_cm"] = [pos[i] + step[i] * (n - 1) for i in range(3)]
        path = clone.get("path_cm")
        if isinstance(path, list):
            clone["path_cm"] = [[pt[i] + step[i] * (n - 1) for i in range(3)] for pt in path]
        out.append(clone)
    return out


def _mirror_copy(part: dict) -> dict | None:
    mirror = part.get("mirror")
    if not isinstance(mirror, dict):
        return None
    axis = 0 if mirror.get("axis") == "x" else 1
    at = float(mirror.get("at_cm", 0))
    clone = copy.deepcopy({k: v for k, v in part.items() if k != "mirror"})
    clone["id"] = f"{part['id']}_mirrored"
    pos = clone.get("position_cm")
    size = clone.get("size_cm")
    if isinstance(pos, list) and len(pos) == 3:
        span = float(size[axis]) if isinstance(size, list) and len(size) == 3 else 0.0
        new_pos = list(pos)
        new_pos[axis] = 2 * at - (float(pos[axis]) + span)
        clone["position_cm"] = new_pos
    path = clone.get("path_cm")
    if isinstance(path, list):
        clone["path_cm"] = [
            [2 * at - pt[axis] if i == axis else pt[i] for i in range(3)] for pt in path]
    profile = clone.get("profile_cm")
    if clone.get("shape") == "extrude" and isinstance(profile, list) \
            and isinstance(size, list) and len(size) == 3:
        # profile points are relative to the part's min corner; mirroring
        # flips them inside the part's own span so the shape mirrors too
        span = float(size[axis])
        clone["profile_cm"] = [
            [span - pt[axis] if i == axis else pt[i] for i in range(2)] for pt in profile]
    return clone


def expand_part(part: dict) -> list[dict]:
    """All concrete parts one written part produces (repeat, then mirror)."""
    out: list[dict] = []
    for clone in _repeat_copies(part):
        plain = {k: v for k, v in clone.items() if k != "mirror"}
        out.append(plain)
        mirrored = _mirror_copy(clone)
        if mirrored is not None:
            out.append(mirrored)
    return out


def expand_parts(desc: dict) -> list[dict]:
    out: list[dict] = []
    for part in desc.get("parts", []):
        out.extend(expand_part(part))
    return out


def expanded(desc: dict) -> dict:
    """A copy of the description with parts replaced by their expansion."""
    clone = copy.deepcopy(desc)
    clone["parts"] = expand_parts(desc)
    return clone
