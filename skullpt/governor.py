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
# The adaptive floor is the larger of the absolute floor above and this
# share of the machine's total RAM — on a bigger machine the floor
# rises so the desktop keeps breathing room; on a small one the
# absolute floor is what counts. (v1.5, P6.)
ADAPTIVE_FLOOR_FRACTION = 0.15


class GovernorError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """A long-running step stopped because the user cancelled it.

    Raised at the next safe checkpoint (between exporters, between
    image candidates, between cloud-mesh stages) — never mid-write.
    Lives here because every long-running module already imports the
    governor, and the run machinery needs one shared type to tell
    "cancelled" apart from "failed".
    """


def check_budget(desc: dict) -> list[str]:
    """Return budget violations (empty = within budget).

    The triangle key is `max_tris` (what the planner writes, what the
    game-ready gate reads, what the examples use). `max_triangles` is
    still honored when `max_tris` is absent, for descriptions written
    before v1.4 — it is a legacy alias, not a second key. The total is
    estimated from the shapes (description.estimate_triangles, over
    the repeat/mirror expansion), so the pre-build gate means
    something instead of summing zeros.
    """
    budget = desc.get("budget", {}) or {}
    problems: list[str] = []
    max_tris = budget.get("max_tris", budget.get("max_triangles"))
    if max_tris is not None:
        from . import description as desc_mod

        total = desc_mod.estimate_description_triangles(desc)
        if total > max_tris:
            problems.append(
                f"triangles: parts total ~{total} (estimated) > budget {max_tris}"
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


def total_ram_mb() -> int | None:
    """Total RAM in MB on Linux; None where it cannot be read."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def adaptive_min_free_mb() -> int:
    """The RAM floor for heavy work on this machine: the larger of the
    absolute 1024 MB floor and 15% of total RAM. On Joshua's 8 GB PC
    that is ~1200 MB; on a 4 GB machine it stays 1024 MB. Falls back
    to the absolute floor when total RAM cannot be read."""
    total = total_ram_mb()
    if total is None:
        return DEFAULT_MIN_FREE_MB
    return max(DEFAULT_MIN_FREE_MB, int(total * ADAPTIVE_FLOOR_FRACTION))


class HeavyJobLock:
    """One-heavy-job-at-a-time lock, as a lock file with the holder's pid."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._held = False
        self._min_free_mb = DEFAULT_MIN_FREE_MB

    def acquire(self, min_free_mb: int | None = None) -> None:
        """Take the lock. min_free_mb=None means the adaptive floor
        (max(1024 MB, 15% of total RAM)); pass an explicit number to
        override it (tests use 0 to mean "no floor")."""
        if min_free_mb is None:
            min_free_mb = adaptive_min_free_mb()
        self._check_ram(min_free_mb)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic creation: two processes starting at once cannot both
        # win — O_EXCL makes exactly one of them the holder, and the
        # loser sees the file and reads a live pid. A stale lock (dead
        # holder) is removed and the create retried once. An empty lock
        # file is NOT stale: it is a contender that just created the
        # file and has not written its pid yet (the write follows the
        # create by two syscalls). Stealing it lets two heavy jobs run
        # at once — the old code did exactly that under load, and both
        # contenders reported "won". (v1.7 verification fix.)
        for _attempt in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._read_holder()
                if holder == 0:
                    # Empty or unreadable: wait briefly and re-read
                    # twice before deciding it is orphaned. No writer
                    # takes 150 ms between create and write; a file
                    # still empty after that is genuinely abandoned.
                    for _wait in (0.05, 0.1):
                        time.sleep(_wait)
                        holder = self._read_holder()
                        if holder != 0:
                            break
                alive = holder > 0 and Path(f"/proc/{holder}").exists()
                if alive:
                    raise GovernorError(
                        f"another heavy job is already running (pid {holder})")
                self.lock_path.unlink(missing_ok=True)  # stale lock from a dead job
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self._held = True
            self._min_free_mb = min_free_mb
            return
        raise GovernorError("could not take the heavy-job lock — try again")

    def _read_holder(self) -> int:
        """The pid recorded in the lock file, or 0 when the file is
        empty, missing, or holds no number."""
        try:
            return int(self.lock_path.read_text().strip() or "0")
        except (ValueError, OSError):
            return 0

    def _check_ram(self, min_free_mb: int) -> None:
        free = free_ram_mb()
        if free is not None and free < min_free_mb:
            raise GovernorError(
                f"only {free} MB RAM free (floor is {min_free_mb} MB) — wait, don't thrash"
            )

    def recheck(self) -> None:
        """Re-check the RAM floor mid-run (the floor is checked at
        acquire time too; a build that grows past it is stopped
        rather than allowed to thrash the machine)."""
        if self._held:
            self._check_ram(getattr(self, "_min_free_mb", DEFAULT_MIN_FREE_MB))

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
