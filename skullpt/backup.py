"""Workspace backup — the whole workshop into one zip (v1.6, T29).

`backup_workspace` zips a workspace's jobs, library, images, intake,
meshes, settings and chat transcript into a single file, with a
manifest saying what is inside, when it was made, and which workspace
it came from. Restoring is an unzip — the backup IS a workspace, so
an unzipped backup opens as one.

The HF token file is NEVER in a backup: it is a secret, and a backup
is a file that gets copied around. The backup says so plainly in its
manifest and in the result. The heavy-job lock and caches are
excluded too (they are machine state, not work).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .governor import stamp

# Directories that hold the user's work, relative to the workspace.
BACKED_UP_DIRS = ("jobs", "library", "images", "intake", "meshes")
# Loose files at the workspace root that hold the user's work.
BACKED_UP_FILES = ("settings.json", "chat.jsonl", "seats.json")
# Never in a backup: the HF token is a secret; the lock and caches
# are machine state, not work.
EXCLUDED_NAMES = {"hf_token.key", ".heavy.lock"}
EXCLUDED_SUFFIXES = (".pyc",)
EXCLUDED_DIRS = {"__pycache__"}


def backup_workspace(workspace: str | Path, dest: str | Path | None = None) -> dict:
    """Zip a workspace. `dest` names the zip file; without one the
    backup lands next to the workspace as <name>-backup-<stamp>.zip.
    Returns {"ok", "path", "files", "bytes", "note"}."""
    ws = Path(workspace)
    if not ws.is_dir():
        raise FileNotFoundError(f"no workspace at {workspace}")
    if dest is None:
        dest = ws.parent / f"{ws.name}-backup-{stamp().replace(':', '-')}.zip"
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirname in BACKED_UP_DIRS:
            folder = ws / dirname
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if path.is_dir() or path.name in EXCLUDED_NAMES:
                    continue
                if path.suffix in EXCLUDED_SUFFIXES:
                    continue
                if any(part in EXCLUDED_DIRS for part in path.parts):
                    continue
                arc = path.relative_to(ws).as_posix()
                zf.write(path, arc)
                files.append(arc)
        for name in BACKED_UP_FILES:
            path = ws / name
            if path.is_file() and path.name not in EXCLUDED_NAMES:
                zf.write(path, name)
                files.append(name)
        manifest = {
            "what": "SKULLPT workspace backup",
            "workspace": ws.name,
            "made": stamp(),
            "files": sorted(files),
            "note": ("The Hugging Face token file (hf_token.key) is "
                     "never in a backup — it is a secret. Re-save the "
                     "token in Settings after restoring."),
        }
        zf.writestr("backup-manifest.json", json.dumps(manifest, indent=2) + "\n")
    size = dest.stat().st_size
    return {"ok": True, "path": str(dest), "files": len(files), "bytes": size,
            "note": ("Backed up " + str(len(files)) + " files. The Hugging "
                     "Face token is not in the backup — re-save it in "
                     "Settings after restoring.")}
