"""Machine fact-checks: constraints from the Description File, verified for free.

These are the checks a vision model should never be trusted with alone —
facts, not vibes: is the part on the ground, does it sit ON the thing it
claims to sit on, is it inside its container, does its label fit its box,
is the triangle budget respected. Floating/disconnected parts are the #1
geometric failure of generated 3D, so `on_ground` / `sits_on` exist here
from day one even though the first exporters are 2D — scene exporters will
feed real part geometry (position_cm / size_cm) into exactly these checks.

Part geometry convention (all in cm, z-up):
  part["position_cm"] = [x, y, z_of_base]
  part["size_cm"]     = [width, depth, height]
"""
from __future__ import annotations

TOL_CM = 0.5  # half a centimetre of slack on contact checks


def _part_map(desc: dict) -> dict[str, dict]:
    return {p["id"]: p for p in desc.get("parts", [])}


def _base_z(part: dict) -> float | None:
    pos = part.get("position_cm")
    return float(pos[2]) if pos and len(pos) == 3 else None


def _top_z(part: dict) -> float | None:
    base = _base_z(part)
    size = part.get("size_cm")
    if base is None or not size or len(size) != 3:
        return None
    return base + float(size[2])


def check_constraints(desc: dict) -> list[dict]:
    """Evaluate every constraint; return [{constraint, ok, detail}, ...]."""
    parts = _part_map(desc)
    results: list[dict] = []
    for con in desc.get("constraints", []) or []:
        ctype = con.get("type")
        ok, detail = True, ""
        if ctype == "on_ground":
            part = parts.get(con.get("part"), {})
            z = _base_z(part)
            if z is None:
                ok, detail = False, "part has no position_cm to check"
            elif abs(z) > TOL_CM:
                ok, detail = False, f"base is {z} cm above the ground (floating)"
        elif ctype == "sits_on":
            part = parts.get(con.get("part"), {})
            support = parts.get(con.get("on"), {})
            z, top = _base_z(part), _top_z(support)
            if z is None or top is None:
                ok, detail = False, "missing position_cm/size_cm on part or support"
            elif abs(z - top) > TOL_CM:
                ok, detail = False, f"base at {z} cm but support top is {top} cm"
        elif ctype == "text_fits":
            part = parts.get(con.get("part"), {})
            text = str(part.get("text", ""))
            width = part.get("width_chars")
            if width is None:
                ok, detail = False, "part has no width_chars to check against"
            elif len(text) > int(width):
                ok, detail = False, f"text {text!r} is {len(text)} chars > width {width}"
        elif ctype == "max_count":
            count = sum(1 for p in desc.get("parts", []) if p.get("type") == con.get("part_type"))
            limit = con.get("limit")
            if limit is not None and count > int(limit):
                ok, detail = False, f"{count} parts of type {con.get('part_type')} > limit {limit}"
        else:
            ok, detail = False, f"unknown constraint type {ctype!r}"
        results.append({"constraint": con, "ok": ok, "detail": detail})
    return results


def all_ok(results: list[dict]) -> bool:
    return all(r["ok"] for r in results)
