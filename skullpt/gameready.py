"""The game-ready gate — could this asset actually go into a game?

Runs when a description sets "game_ready": true. The Blender exporter
writes a stats file (real triangle counts + UV presence, measured on the
evaluated meshes) plus LOD and collision exports; this gate judges them:

  - total triangles within the description's budget.max_tris (if set)
  - every part carries a UV map
  - LOD1 + LOD2 exports landed
  - a collision export landed (UCX_ boxes, Unreal's naming convention)
  - every part id is a clean engine-safe name (lowercase, digits, _)

The gate is a report, not a vibe: each line is a fact read from the files
the build actually produced.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .blender_export import _slug
from .expand import expand_parts

NAME_OK = re.compile(r"^[a-z][a-z0-9_]*$")


def run_gate(desc: dict, out_dir: str | Path, outputs: list[str]) -> dict:
    out = Path(out_dir)
    slug = _slug(desc["name"])
    checks: list[dict] = []

    stats = None
    stats_path = out / f"{slug}.stats.json"
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats = None
    checks.append({"check": "stats_present", "ok": stats is not None,
                   "detail": f"{slug}.stats.json readable" if stats else "no stats file from the exporter"})

    max_tris = (desc.get("budget") or {}).get("max_tris")
    if max_tris is not None:
        total = (stats or {}).get("total_tris")
        ok = total is not None and total <= max_tris
        checks.append({"check": "triangle_budget", "ok": ok,
                       "detail": f"{total} tris vs budget {max_tris}" if total is not None
                                 else "no triangle count to judge"})
    else:
        checks.append({"check": "triangle_budget", "ok": True,
                       "detail": "no max_tris budget set — count not judged"})

    if stats:
        no_uv = [name for name, s in stats.get("parts", {}).items() if not s.get("uv")]
        checks.append({"check": "uv_maps_present", "ok": not no_uv,
                       "detail": f"parts without UVs: {no_uv}" if no_uv else "every part has a UV map"})
    else:
        checks.append({"check": "uv_maps_present", "ok": False, "detail": "no stats to check UVs against"})

    for suffix in ("lod1", "lod2"):
        name = f"{slug}_{suffix}.glb"
        landed = (out / name).exists() and (out / name).stat().st_size > 0
        checks.append({"check": f"{suffix}_export", "ok": landed,
                       "detail": name if landed else f"{name} missing/empty"})

    collision = f"{slug}_collision.glb"
    landed = (out / collision).exists() and (out / collision).stat().st_size > 0
    checks.append({"check": "collision_export", "ok": landed,
                   "detail": collision if landed else f"{collision} missing/empty"})

    bad_names = [p["id"] for p in expand_parts(desc) if not NAME_OK.match(str(p["id"]))]
    checks.append({"check": "engine_safe_names", "ok": not bad_names,
                   "detail": f"ids that are not lowercase_engine_safe: {bad_names}" if bad_names
                             else "all part ids are engine-safe"})

    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "total_tris": (stats or {}).get("total_tris")}
