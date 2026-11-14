"""Workspace settings — the user's own switches, in one small file.

Settings live at <workspace>/settings.json. Today there is one switch:

  critic_reference_check (default ON): when a job has a chosen
  reference image on record, the vision critic compares the build's
  renders against that reference, not just against the written rubric.

A description may carry "critic_reference": true/false to override the
workspace switch for that one job ("a toggle in settings, plus a
per-job say-so as well" — Joshua, 2026-11-11).

Render settings (v1.6, T25) follow the same pattern: workspace
defaults here, per-job overrides in the description's "render" block.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SETTINGS_FILE = "settings.json"

DEFAULTS = {
    "critic_reference_check": True,
    # Render defaults (T25): how renders are made when a job does not
    # say otherwise. "auto" probes EEVEE and falls back to Cycles.
    "render_engine": "auto",
    "render_resolution_px": 640,
    "render_samples": 24,
    "render_denoise": False,
}

# The render engines the exporter knows (kept in sync with
# description.RENDER_ENGINES — the settings layer validates what the
# Settings window writes, the description layer what jobs carry).
RENDER_ENGINES = ("auto", "eevee", "cycles")


def settings_path(workspace: str | Path) -> Path:
    return Path(workspace) / SETTINGS_FILE


def load_settings(workspace: str | Path) -> dict:
    """The workspace settings, with defaults for anything unset."""
    settings = dict(DEFAULTS)
    settings.update(_raw_settings(workspace))
    return settings


def _raw_settings(workspace: str | Path) -> dict:
    """The settings file's own contents, without defaults — so the
    resolver can tell a value the user actually set from a default."""
    path = settings_path(workspace)
    if not path.exists():
        return {}
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def save_settings(workspace: str | Path, settings: dict) -> Path:
    path = settings_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def critic_reference_enabled(workspace: str | Path, desc: dict) -> bool:
    """Resolution order: the job's own say-so, then the workspace toggle."""
    own = desc.get("critic_reference")
    if own is True or own is False:
        return own
    return bool(load_settings(workspace).get("critic_reference_check", True))


def reference_image_path(desc: dict) -> Path | None:
    """The job's reference image on disk, if the record points at a file."""
    ref = desc.get("reference") or {}
    stored = ref.get("stored_at")
    if stored and Path(stored).exists():
        return Path(stored)
    return None


# ------------------------------------------------- render settings (T25)

def resolve_render_settings(workspace: str | Path, desc: dict) -> dict:
    """The render settings for one build: the job's own "render" block
    wins where it speaks, then the workspace defaults the user
    actually set, then the code defaults. A description's
    budget.preview_px still sets the resolution when neither the job
    nor the workspace names one (the pre-v1.6 path — older
    descriptions keep rendering as they did). The workspace read here
    is the raw file, not the merged defaults: a default of 640 must
    not shadow a description's own preview size."""
    raw = _raw_settings(workspace)
    job = desc.get("render") or {}
    engine = job.get("engine") or raw.get("render_engine") or "auto"
    if engine not in RENDER_ENGINES:
        engine = "auto"
    resolution = job.get("resolution_px") or raw.get("render_resolution_px")
    if not isinstance(resolution, int) or resolution < 128 or resolution > 4096:
        budget_px = (desc.get("budget") or {}).get("preview_px")
        resolution = (budget_px if isinstance(budget_px, int)
                      and 128 <= budget_px <= 4096 else 640)
    samples = job.get("samples") or raw.get("render_samples")
    if not isinstance(samples, int) or samples < 1 or samples > 512:
        samples = 24
    denoise = job.get("denoise")
    if not isinstance(denoise, bool):
        denoise = bool(raw.get("render_denoise", False))
    return {"engine": engine, "resolution_px": resolution,
            "samples": samples, "denoise": denoise}


def save_render_settings(workspace: str | Path, render: dict) -> dict:
    """Store workspace render defaults (the Settings window's save).
    Only known keys, only valid values — anything else is ignored
    rather than written, so a bad save can never poison the file."""
    current = load_settings(workspace)
    engine = render.get("engine")
    if engine in RENDER_ENGINES:
        current["render_engine"] = engine
    res = render.get("resolution_px")
    if isinstance(res, int) and 128 <= res <= 4096:
        current["render_resolution_px"] = res
    samples = render.get("samples")
    if isinstance(samples, int) and 1 <= samples <= 512:
        current["render_samples"] = samples
    if isinstance(render.get("denoise"), bool):
        current["render_denoise"] = render["denoise"]
    save_settings(workspace, current)
    return current


# --------------------------------------- last working render engine (P4)

def record_render_engine(workspace: str | Path, engine: str) -> None:
    """Remember which render engine actually worked on this machine
    (the EEVEE probe's verdict, or the engine a build fell back to).
    Best effort — a settings file that cannot be written is not a
    build failure. The Settings window shows it; the debug report
    carries it."""
    if engine not in ("BLENDER_EEVEE_NEXT", "CYCLES"):
        return
    try:
        current = load_settings(workspace)
        current["last_render_engine"] = engine
        current["last_render_engine_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save_settings(workspace, current)
    except OSError:
        pass


def last_render_engine(workspace: str | Path) -> dict:
    """{"engine": "BLENDER_EEVEE_NEXT"|"CYCLES"|None, "at": str|None}."""
    saved = load_settings(workspace)
    return {"engine": saved.get("last_render_engine"),
            "at": saved.get("last_render_engine_at")}
