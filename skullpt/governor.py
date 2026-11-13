"""Resource Governor — keeps heavy jobs from thrashing a small PC.

Two duties:
1. Budget gate: a Description File's budget (triangles, texture size,
   preview resolution) is checked against the summed estimates of its parts
   BEFORE anything runs. Over budget = the job does not start.
2. Machine gate: one heavy job at a time (lock file), and a free-RAM floor
   read from /proc/meminfo on Linux. If the machine is already loaded, the
   job is told to wait instead of swapping the HDD to death.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_MIN_FREE_MB = 1024  # refuse heavy work under ~1 GB free


class GovernorError(RuntimeError):
    pass


def check_budget(desc: dict) -> list[str]:
    """Return budget violations (empty = within budget)."""
    budget = desc.get("budget", {}) or {}
    problems: list[str] = []
    if "max_triangles" in budget:
        total = sum(int(p.get("triangles", 0) or 0) for p in desc.get("parts", []))
        if total > budget["max_triangles"]:
            problems.append(
                f"triangles: parts total {total} > budget {budget['max_triangles']}"
            )
    if "max_texture_px" in budget:
        for part in desc.get("parts", []):
            tex = part.get("texture_px")
            if tex and tex > budget["max_texture_px"]:
                problems.append(
                    f"texture: part {part.get('id')!r} wants {tex}px > budget {budget['max_texture_px']}px"
                )
    if "max_preview_px" in budget:
        prev = (desc.get("preview") or {}).get("width_px")
        if prev and prev > budget["max_preview_px"]:
            problems.append(
                f"preview: width {prev}px > budget {budget['max_preview_px']}px"
            )
    return problems


def free_ram_mb() -> int | None:
    """Available RAM in MB on Linux; None where it cannot be read."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class HeavyJobLock:
    """One-heavy-job-at-a-time lock, as a lock file with the holder's pid."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._held = False

    def acquire(self, min_free_mb: int = DEFAULT_MIN_FREE_MB) -> None:
        free = free_ram_mb()
        if free is not None and free < min_free_mb:
            raise GovernorError(
                f"only {free} MB RAM free (floor is {min_free_mb} MB) — wait, don't thrash"
            )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                holder = int(self.lock_path.read_text().strip() or "0")
            except ValueError:
                holder = 0
            alive = holder > 0 and Path(f"/proc/{holder}").exists()
            if alive:
                raise GovernorError(f"another heavy job is already running (pid {holder})")
            self.lock_path.unlink(missing_ok=True)  # stale lock from a dead job
        self.lock_path.write_text(str(os.getpid()))
        self._held = True

    def release(self) -> None:
        if self._held:
            self.lock_path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
