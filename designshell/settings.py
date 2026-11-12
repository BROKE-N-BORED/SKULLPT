"""Workspace settings — the user's own switches, in one small file.

Settings live at <workspace>/settings.json. Today there is one switch:

  critic_reference_check (default ON): when a job has a chosen
  reference image on record, the vision critic compares the build's
  renders against that reference, not just against the written rubric.

A description may carry "critic_reference": true/false to override the
workspace switch for that one job ("a toggle in settings, plus a
per-job say-so as well" — Joshua, 2026-11-11).
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = "settings.json"

DEFAULTS = {"critic_reference_check": True}


def settings_path(workspace: str | Path) -> Path:
    return Path(workspace) / SETTINGS_FILE


def load_settings(workspace: str | Path) -> dict:
    path = settings_path(workspace)
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            settings.update(saved)
    return settings


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
