"""The Asset Library — the shell's memory, and its future integration surface.

Every approved job is filed as one folder:

  library/<slug>/
    asset files (every exported variant)
    description.json   the Description File it was made from
    build-report.json  machine checks + critic result + outputs list
    session.jsonl      the job's full session log
    manifest.json      {name, kind, tags, files, recipes, thumbnail}

library/index.json indexes all manifests so anything can be found by
name/tag/kind before generating something new. That manifest is the whole
surface a future Code Forge connector will need.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .governor import stamp


class Library:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def _index(self) -> list[dict]:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return []

    def _save_index(self, rows: list[dict]) -> None:
        self.index_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    def file_job(self, job, report: dict, thumbnail: str | None = None) -> Path:
        desc = job.load_description()
        slug = job.dir.name
        dest = self.root / slug
        n = 2
        while dest.exists():
            dest = self.root / f"{slug}-{n}"
            n += 1
        dest.mkdir(parents=True)
        files: list[str] = []
        for name in report.get("outputs", []):
            src = job.output_dir / name
            if src.exists():
                shutil.copy2(src, dest / name)
                files.append(name)
        for extra in ("description.json", "session.jsonl"):
            src = job.dir / extra
            if src.exists():
                shutil.copy2(src, dest / extra)
        (dest / "build-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "name": desc["name"], "kind": desc["kind"], "slug": dest.name,
            "tags": desc.get("tags", []), "files": files,
            "recipes": report.get("recipes", []),
            "thumbnail": thumbnail, "filed": stamp(),
        }
        (dest / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        rows = self._index()
        rows.append(manifest)
        self._save_index(rows)
        job.transition("filed", library=str(dest))
        return dest

    def search(self, query: str = "", kind: str | None = None) -> list[dict]:
        q = query.lower()
        hits = []
        for row in self._index():
            if kind and row.get("kind") != kind:
                continue
            hay = (row.get("name", "") + " " + " ".join(row.get("tags", []))).lower()
            if q and q not in hay:
                continue
            hits.append(row)
        return hits
