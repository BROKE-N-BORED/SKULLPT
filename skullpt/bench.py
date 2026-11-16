"""The Bench's action engine - the shared face behind the Qt windows.

Everything the native desktop Bench does runs through here: the job
actions (plan, approve, build, file, retry, ...), the workspace state
the windows read, the chat commands, and the background-run machinery
(builds, image generation, cloud mesh) with progress, cancel, and the
build queue. Heavy work runs in background threads so a four-minute
generation never freezes the window. Every action calls the same
functions the CLI calls - the Bench is a face on the shell, not a
second shell.

The old web Bench that used to live in this file was removed in v1.9
(SKULLPT is a native Linux desktop app - no web UI, by standing rule).
The image picker (`picker.py`) is the one page left, kept because the
Code Forge bridge drives it on the owner's phone browser.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import description as desc_mod
from . import imagegen
from . import settings as settings_mod
from .jobs import Job
from .library import Library

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

# Examples that must never be offered, even though the file exists in
# the tree. The skull was never authorized work (Joshua: "I didnt tell
# you to make it", 2026-11-11) - it stays in the tree untouched, but
# the Bench must not offer it as a one-click starting point. The packer
# also excludes it from zips; this gate is the in-code backstop.
EXCLUDED_EXAMPLES = {"skull.description.json"}

_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_RUN_SEQ = [0]

# Statuses that hold a queue slot: a run being cancelled still owns
# its heavy-job lock until its thread exits, so the next waiter must
# not start while it is dying (v1.9, N8).
_BUSY_STATUSES = ("running", "cancelling")


def _start_run(kind: str, label: str, fn, queue: str | None = None) -> str:
    """Run `fn` in a background thread; returns the run id.

    `fn` takes two arguments: a `cancel` threading.Event (set when
    the user cancels the run) and a `report` callable -
    `report(fraction_or_None, text)` - for progress. `fn` may return
    a string (stored as the run's detail) or a dict (stored whole as
    the run's result, with its "detail" or "reply" as the detail).
    A governor.Cancelled from `fn` marks the run "cancelled", not
    "error" - stopping a run is not a failure.

    Queue (v1.6, T24): when `queue` names a queue and another run in
    that queue is already running, the new run is recorded with
    status "queued" and starts when its turn comes - the heavy-job
    lock is one-at-a-time, so builds and image rounds wait in line
    instead of colliding on it. The queue drains itself: every run
    starts the next queued run in its queue when it finishes.
    """
    from .governor import Cancelled

    with _RUNS_LOCK:
        _RUN_SEQ[0] += 1
        run_id = f"run-{_RUN_SEQ[0]}"
        cancel = threading.Event()
        busy = queue is not None and any(
            r.get("_queue") == queue and r["status"] in _BUSY_STATUSES
            for r in _RUNS.values())
        _RUNS[run_id] = {"id": run_id, "kind": kind, "label": label,
                         "status": "queued" if busy else "running",
                         "detail": "waiting its turn in the queue" if busy else "",
                         "progress": None, "progress_text": "",
                         "result": None, "_cancel": cancel,
                         "_queue": queue}
        if busy:
            _RUNS[run_id]["_fn"] = fn
            return run_id

    _launch_run(run_id, fn, cancel, queue)
    return run_id


def _launch_run(run_id: str, fn, cancel: threading.Event, queue: str | None) -> None:
    """Start a run's thread (shared by _start_run and the queue drain)."""
    from .governor import Cancelled
    from .live import SeatCancelled

    def report(fraction, text: str = "") -> None:
        with _RUNS_LOCK:
            run = _RUNS.get(run_id)
            if run is not None:
                run["progress"] = fraction
                run["progress_text"] = str(text or "")

    def target():
        try:
            value = fn(cancel, report)
            with _RUNS_LOCK:
                run = _RUNS.get(run_id)
                if run is not None:
                    if isinstance(value, dict):
                        detail = str(value.get("detail") or value.get("reply") or "")
                        run.update(status="done", detail=detail,
                                   result=value, progress=1.0)
                    else:
                        run.update(status="done", detail=str(value or ""),
                                   progress=1.0)
        except Cancelled as exc:
            with _RUNS_LOCK:
                run = _RUNS.get(run_id)
                if run is not None:
                    run.update(status="cancelled", detail=str(exc))
        except SeatCancelled as exc:
            # A cancelled seat call (plan/chat, v1.10 N50) is a
            # cancellation, not an error - same as governor.Cancelled.
            with _RUNS_LOCK:
                run = _RUNS.get(run_id)
                if run is not None:
                    run.update(status="cancelled", detail=str(exc))
        except Exception as exc:  # surfaced in the Bench chat, never swallowed
            label = ""
            with _RUNS_LOCK:
                run = _RUNS.get(run_id)
                if run is not None:
                    label = f"{run.get('kind')} {run.get('label')}"
                    run.update(status="error", detail=str(exc))
            from . import applog

            applog.get_logger().error("run %s (%s) failed: %s",
                                      run_id, label, exc)
        finally:
            if queue is not None:
                _drain_queue(queue)

    threading.Thread(target=target, daemon=True).start()


def _drain_queue(queue: str) -> None:
    """Start the oldest queued run in a queue, if none is running."""
    with _RUNS_LOCK:
        if any(r.get("_queue") == queue and r["status"] in _BUSY_STATUSES
               for r in _RUNS.values()):
            return
        waiting = [r for r in _RUNS.values()
                   if r.get("_queue") == queue and r["status"] == "queued"]
        if not waiting:
            return
        # Run ids are "run-N" in creation order - the lowest number
        # waited longest.
        nxt = min(waiting, key=lambda r: int(str(r["id"]).split("-")[1]))
        fn = nxt.pop("_fn", None)
        cancel = nxt["_cancel"]
        run_id = nxt["id"]
        nxt.update(status="running", detail="", progress=None, progress_text="")
    if fn is not None:
        _launch_run(run_id, fn, cancel, queue)


def queue_position(queue: str, run_id: str) -> int:
    """A queued run's position in its queue (1 = next to run), or 0
    when the run is not queued."""
    with _RUNS_LOCK:
        waiting = sorted(
            (r for r in _RUNS.values()
             if r.get("_queue") == queue and r["status"] == "queued"),
            key=lambda r: int(str(r["id"]).split("-")[1]))
        for pos, run in enumerate(waiting, start=1):
            if run["id"] == run_id:
                return pos
    return 0


def cancel_run(run_id: str) -> bool:
    """Ask a running run to stop at its next safe checkpoint, or drop
    a queued run from its queue. Returns False when the run is not
    running or queued (already finished, or no such run) -
    cancelling a finished run is a no-op, not an error. A running
    run moves to status "cancelling" at once (v1.9, N8) so the Bench
    can say so while the subprocess is being killed; its thread
    records "cancelled" when the kill lands. Cancelling a run that
    is already cancelling is a no-op that returns True."""
    queue: str | None = None
    with _RUNS_LOCK:
        run = _RUNS.get(run_id)
        if run is None or run["status"] not in ("running", "queued", "cancelling"):
            return False
        if run["status"] == "queued":
            queue = run.get("_queue")
            run.pop("_fn", None)
            run.update(status="cancelled",
                       detail="removed from the queue before it started")
        elif run["status"] == "cancelling":
            return True
        else:
            event = run.get("_cancel")
            if event is not None:
                event.set()
            run.update(status="cancelling", detail="Cancelling...")
            return True
    # Outside the lock: dropping a queued run may free the queue for
    # the next waiter (the drain takes the lock itself).
    if queue is not None:
        _drain_queue(queue)
    return True


def _runs() -> list[dict]:
    """Runs for the windows: every run still going (running,
    cancelling, queued) plus the last 8 finished ones (v1.10, N29).
    The old last-8 truncation could orphan a still-pending run over
    a long session, so the poller showed Idle while it ran and never
    reported its completion. Order stays chronological."""
    with _RUNS_LOCK:
        runs = list(_RUNS.values())
        active = {"running", "cancelling", "queued"}
        finished = [r for r in runs if r["status"] not in active]
        keep = {id(r) for r in finished[-8:]}
        out = []
        for run in runs:
            if run["status"] in active or id(run) in keep:
                # The cancel Event, the queued function and the queue name
                # are not JSON-able - the windows never see them.
                out.append({k: v for k, v in run.items()
                            if k not in ("_cancel", "_fn", "_queue")})
        return out


# --------------------------------------------------------------------- state

def engine_status(workspace: Path) -> dict:
    import os

    from .blender_export import find_blender
    from . import live, ollama as ollama_mod, tokenfile

    seats_path = workspace / "seats.json"
    gemini_key = False
    groq_key = False
    try:
        from .seats import load_seats
        seats = load_seats(seats_path if seats_path.exists() else None)
        # A key counts when it is set in the environment OR saved in
        # the workspace's key file (v1.8) - never the value itself.
        gemini_key = live.key_available(seats.get("critic", {}), workspace)
        groq_key = live.key_available(seats.get("repairer", {}), workspace)
    except Exception:
        gemini_key = False
        groq_key = False
    # Character Studio (v1.7, T26): ready only when MPFB2 AND its
    # system assets pack are both installed for some Blender.
    from .blender_character import find_mpfb
    mpfb = find_mpfb()
    # Ollama (v1.8, S5): the local model counts as ready only when
    # the server is up AND the model is pulled. The check is cached
    # briefly inside the ollama module, so a down server never
    # stalls a refresh.
    ollama = ollama_mod.status()
    return {
        "blender": find_blender() is not None,
        "image_engine": imagegen.find_engine() is not None,
        "image_model": imagegen.find_model(workspace) is not None,
        "gemini_key": gemini_key,
        "groq_key": groq_key,
        "ollama": bool(ollama["server"] and ollama["model"]),
        "ollama_server": ollama["server"],
        "ollama_model": ollama["model"],
        "hf_token": bool(os.environ.get("HF_TOKEN"))
        or tokenfile.has_token(workspace),
        "character_studio": bool(mpfb["installed"] and mpfb["assets"]),
    }


def missing_setup_items(workspace: Path) -> list[dict]:
    """Everything the setup check lists (v1.8, S4): one item per
    missing engine or key, with the fix the Setup window offers.
    "installer" runs the SKULLPT installer GUI, "pull_model" pulls
    the local model, "settings" opens the Settings window's key
    sections. Empty when everything is ready."""
    status = engine_status(workspace)
    items = []
    if not status["blender"]:
        items.append({"key": "blender", "title": "Blender",
                      "detail": "Blender builds the 3D files and the "
                                "renders. It is not installed.",
                      "fix": "installer"})
    if not status["image_engine"]:
        items.append({"key": "image_engine", "title": "Image engine",
                      "detail": "The image engine draws reference "
                                "pictures. It is not installed.",
                      "fix": "installer"})
    if not status["image_model"]:
        items.append({"key": "image_model", "title": "Image model",
                      "detail": "The picture model file (about 2.1 GB) "
                                "is not downloaded.",
                      "fix": "installer"})
    if not status["character_studio"]:
        items.append({"key": "character_studio", "title": "Character Studio",
                      "detail": "MPFB2 and its skins are not installed, "
                                "so character jobs cannot build.",
                      "fix": "installer"})
    if not status["ollama"]:
        if not status["ollama_server"]:
            items.append({"key": "ollama", "title": "Ollama (local model)",
                          "detail": "The local model server is not "
                                    "running. The installer sets it up.",
                          "fix": "installer"})
        else:
            items.append({"key": "ollama", "title": "Ollama model",
                          "detail": "The server is running, but the "
                                    "local model is not pulled yet.",
                          "fix": "pull_model"})
    if not status["gemini_key"]:
        items.append({"key": "gemini_key", "title": "Gemini key",
                      "detail": "The planner and critic seats need your "
                                "Gemini key. Paste it in Settings.",
                      "fix": "settings"})
    if not status["groq_key"]:
        items.append({"key": "groq_key", "title": "Groq key",
                      "detail": "The repair seat needs your Groq key. "
                                "Paste it in Settings.",
                      "fix": "settings"})
    if not status["hf_token"]:
        items.append({"key": "hf_token", "title": "Hugging Face token",
                      "detail": "Turning a photo into a 3D mesh needs a "
                                "Hugging Face token. Save it in Settings.",
                      "fix": "settings"})
    return items


# ------------------------------------------------------- state cache (P7)

# workspace_state used to re-read every job, batch, and the library
# index on each refresh - fine at 5 jobs, slow at 50. The file-derived
# part is now cached per workspace, keyed by an mtime+size signature
# over the workspace files; anything that writes a file invalidates
# it. Engines and runs are NOT cached (they change without any file
# changing: a new Blender install, a run finishing).
_STATE_CACHE: dict[str, dict] = {}
_STATE_CACHE_LOCK = threading.Lock()
# Milliseconds the last workspace_state call took (P8 - the debug
# report shows it, so the next audit has numbers, not guesses).
LAST_STATE_MS: float | None = None


def _workspace_signature(workspace: Path) -> list:
    """(path, mtime_ns, size) for every file the state is read from."""
    sig: list = []
    for root in ("jobs", "images", "library", "intake"):
        base = workspace / root
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for name in sorted(filenames):
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                    sig.append((str(p.relative_to(workspace)),
                                st.st_mtime_ns, st.st_size))
                except OSError:
                    sig.append((str(p.relative_to(workspace)), None, None))
    for name in ("settings.json", "chat.jsonl", "seats.json"):
        p = workspace / name
        if p.exists():
            try:
                st = p.stat()
                sig.append((name, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
    return sig


def invalidate_workspace_cache(workspace: str | Path | None = None) -> None:
    """Drop the cached state for one workspace (or all of them).
    Normally unnecessary - file writes invalidate it - but tests and
    anything that changes state without touching a file can force it."""
    with _STATE_CACHE_LOCK:
        if workspace is None:
            _STATE_CACHE.clear()
        else:
            _STATE_CACHE.pop(str(workspace), None)


def _workspace_state_files(workspace: Path) -> dict:
    """The file-derived part of workspace_state: jobs, batches,
    library, examples, settings, skipped, folders, chat."""
    jobs = []
    skipped: list[dict] = []
    jobs_root = workspace / "jobs"
    if jobs_root.exists():
        for job_dir in sorted(p for p in jobs_root.iterdir() if p.is_dir()):
            try:
                job = Job(job_dir)
                desc = job.load_description()
            except Exception as exc:
                # Never silently skip (U32): a job the Bench cannot
                # read is listed with its reason, not vanished.
                skipped.append({"where": "jobs", "name": job_dir.name,
                                "reason": str(exc)[:200]})
                continue
            outputs = []
            if job.output_dir.exists():
                outputs = sorted(p.name for p in job.output_dir.iterdir() if p.is_file())
            report = None
            report_path = job_dir / "build-report.json"
            if report_path.exists():
                try:
                    raw = json.loads(report_path.read_text())
                    report = {"ok": raw.get("ok"), "stage": raw.get("stage"),
                              "problems": raw.get("problems", [])}
                except (OSError, ValueError):
                    report = None
            ref = desc.get("reference") or {}
            jobs.append({
                "name": job_dir.name,
                "title": desc.get("name", job_dir.name),
                "kind": desc.get("kind", ""),
                "state": job.state,
                "summary": desc_mod.summarize(desc),
                "outputs": outputs,
                "renders": [o for o in outputs if o.endswith(".png")],
                "has_reference": bool(ref.get("stored_at")
                                      and Path(ref["stored_at"]).exists()),
                "reference_source": ref.get("source"),
                "report": report,
            })
    batches = []
    images_root = workspace / "images"
    if images_root.exists():
        for bdir in sorted((p for p in images_root.iterdir() if p.is_dir()),
                           reverse=True):
            try:
                batch = imagegen.load_batch(bdir)
            except Exception as exc:
                # Never silently skip (U32): an unreadable batch is
                # listed with its reason, not vanished.
                skipped.append({"where": "images", "name": bdir.name,
                                "reason": str(exc)[:200]})
                continue
            batches.append({
                "slug": batch["slug"], "prompt": batch["prompt"],
                "job": batch.get("job"), "model": batch.get("model"),
                "reference_file": batch.get("reference_file"),
                "candidates": batch["candidates"],
            })
    library = []
    try:
        library = Library(workspace / "library").search("")
    except Exception as exc:
        # Never silently skip (U32): a library that cannot be read at
        # all (the index rebuild in library.py already tried) is
        # listed with its reason, not vanished (v1.10, N24).
        skipped.append({"where": "library", "name": "index.json",
                        "reason": str(exc)[:200]})
    return {
        "workspace": str(workspace),
        "jobs": jobs,
        "batches": batches,
        "library": [{"slug": h["slug"], "name": h["name"], "kind": h["kind"],
                     "files": h["files"]} for h in library],
        "examples": sorted(p.name for p in EXAMPLES_DIR.glob("*.description.json")
                           if p.name not in EXCLUDED_EXAMPLES),
        "settings": settings_mod.load_settings(workspace),
        "skipped": skipped,
        "folders": {
            "library": [h["slug"] for h in library],
            "images": [b["slug"] for b in batches],
            "intake": sorted(p.name for p in (workspace / "intake").iterdir()
                             if p.is_dir()) if (workspace / "intake").exists() else [],
            "root_files": sorted(p.name for p in workspace.iterdir()
                                 if p.is_file())[:30],
        },
        "chat": chat_history(workspace)[-40:],
    }


def workspace_state(workspace: Path) -> dict:
    """The whole workshop in one dict: the cached file-derived state
    plus freshly computed engines and runs."""
    global LAST_STATE_MS
    started = time.monotonic()
    ws = Path(workspace)
    signature = _workspace_signature(ws)
    key = str(ws)
    with _STATE_CACHE_LOCK:
        entry = _STATE_CACHE.get(key)
        if entry is not None and entry["signature"] == signature:
            files_state = entry["state"]
        else:
            files_state = _workspace_state_files(ws)
            _STATE_CACHE[key] = {"signature": signature, "state": files_state}
    state = dict(files_state)
    state["engines"] = engine_status(ws)
    state["runs"] = _runs()
    LAST_STATE_MS = (time.monotonic() - started) * 1000.0
    return state


# ------------------------------------------------------------------- actions

def _job(workspace: Path, name: str) -> Job:
    job_dir = workspace / "jobs" / name
    if not job_dir.is_dir() or job_dir.parent != workspace / "jobs":
        raise ValueError(f"no job named {name!r}")
    return Job(job_dir)


def act(workspace: Path, action: str, payload: dict, cancel=None) -> dict:
    """Run one bench action; returns a JSON-able result dict. `cancel`,
    when given, is a threading.Event the plan action passes to the
    seat call, so cancelling a draft actually stops it (v1.10, N50)."""
    if action == "approve":
        job = _job(workspace, str(payload.get("job", "")))
        job.approve()
        return {"ok": True, "job": job.dir.name,
                "detail": f"approved {job.dir.name}"}
    if action == "retry":
        job = _job(workspace, str(payload.get("job", "")))
        job.retry()
        return {"ok": True, "job": job.dir.name,
                "detail": f"{job.dir.name} is approved again - build when ready"}
    if action == "delete":
        job = _job(workspace, str(payload.get("job", "")))
        name = job.dir.name
        job.delete()
        return {"ok": True, "detail": f"deleted job {name}"}
    if action == "rename":
        job = _job(workspace, str(payload.get("job", "")))
        renamed = job.rename(str(payload.get("name", "")))
        return {"ok": True, "job": renamed.dir.name,
                "detail": f"renamed to {renamed.dir.name}"}
    if action == "duplicate":
        job = _job(workspace, str(payload.get("job", "")))
        copy = job.duplicate()
        return {"ok": True, "job": copy.dir.name,
                "detail": f"duplicated as {copy.dir.name} - waiting at the plan gate"}
    if action == "edit-part":
        job = _job(workspace, str(payload.get("job", "")))
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("edit-part needs a fields object")
        new_state = job.edit_part(str(payload.get("part", "")), fields)
        detail = f"part {payload.get('part')} updated"
        if new_state == "awaiting_approval":
            detail += " - the description changed, so it needs approval again"
        return {"ok": True, "job": job.dir.name, "state": new_state,
                "detail": detail}
    if action == "copy-material":
        # The eyedropper (v1.11, M6): copy one part's material onto
        # another. A snapshot, not a link - editing the source later
        # never changes the target. Goes through edit_part, so the
        # snapshot + plan-gate rules apply.
        job = _job(workspace, str(payload.get("job", "")))
        desc = job.load_description()
        by_id = {p["id"]: p for p in desc.get("parts", [])}
        from_part = by_id.get(str(payload.get("from_part", "")))
        to_part = by_id.get(str(payload.get("to_part", "")))
        if from_part is None:
            raise ValueError(f"no part {payload.get('from_part')!r}")
        if to_part is None:
            raise ValueError(f"no part {payload.get('to_part')!r}")
        material = from_part.get("material")
        if material is None:
            raise ValueError(
                f"part {from_part['id']} has no material to copy")
        new_state = job.edit_part(
            to_part["id"],
            # A JSON round-trip deep-copies the material (a local
            # named `copy` in this function shadows the copy module).
            {"material": json.loads(json.dumps(material))})
        if isinstance(material, dict):
            from . import materials as materials_mod
            label = materials_mod.breadcrumb(material)
        else:
            label = str(material)
        detail = (f"copied {label} from {from_part['id']} "
                  f"to {to_part['id']}")
        if new_state == "awaiting_approval":
            detail += " - the description changed, so it needs approval again"
        return {"ok": True, "job": job.dir.name, "state": new_state,
                "detail": detail}
    if action == "edit-character":
        job = _job(workspace, str(payload.get("job", "")))
        character = payload.get("character")
        if not isinstance(character, dict):
            raise ValueError("edit-character needs a character object")
        new_state = job.edit_character(character)
        detail = f"character {job.dir.name} updated"
        if new_state == "awaiting_approval":
            detail += " - the description changed, so it needs approval again"
        return {"ok": True, "job": job.dir.name, "state": new_state,
                "detail": detail}
    if action == "new-character":
        from .blender_character import default_character_description
        name = str(payload.get("name", "")).strip() or "New character"
        job = Job.create(workspace / "jobs",
                         default_character_description(name))
        return {"ok": True, "job": job.dir.name,
                "detail": f"character job created: {job.dir.name} - "
                "shape the human, then approve it to build"}
    if action == "build":
        job = _job(workspace, str(payload.get("job", "")))

        def do_build(cancel, report):
            from .builder import build_job
            stages = {"export": 0.3, "check": 0.65, "critic": 0.85}
            # Where each stage starts (v1.9, N9): a fraction from the
            # exporter is relative to its stage, so it maps onto the
            # band between the previous stage's point and this one.
            starts = {"export": 0.05, "check": 0.3, "critic": 0.65}

            def on_progress(stage, detail, fraction=None):
                point = stages.get(stage)
                if fraction is not None and point is not None:
                    base = starts.get(stage, 0.0)
                    clamped = max(0.0, min(1.0, fraction))
                    report(base + clamped * (point - base),
                           f"{stage}: {detail}")
                else:
                    report(point, f"{stage}: {detail}")

            report(0.05, "starting the build")
            report_data = build_job(job, workspace, cancel=cancel,
                                    progress=on_progress)
            (job.dir / "build-report.json").write_text(
                json.dumps(report_data, indent=2) + "\n")
            if not report_data.get("ok"):
                raise RuntimeError("build did not pass: "
                                   + "; ".join(report_data.get("problems", [])[:3]))
            return f"built {job.dir.name}"
        # One heavy job at a time per workspace (T24): builds and image
        # rounds share the heavy-job lock, so they share one queue.
        queue = f"heavy:{workspace}"
        run_id = _start_run("build", job.dir.name, do_build, queue=queue)
        position = queue_position(queue, run_id)
        result: dict = {"ok": True, "job": job.dir.name, "run": run_id}
        if position:
            result["queued"] = True
            result["detail"] = (f"{job.dir.name} is queued at position {position} - "
                                "it builds when the run ahead of it finishes")
        return result
    if action == "file":
        job = _job(workspace, str(payload.get("job", "")))
        if job.state != "awaiting_review":
            raise ValueError(f"cannot file from state {job.state!r} (needs awaiting_review)")
        report_path = job.dir / "build-report.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text())
            except json.JSONDecodeError:
                # A damaged build report must not block filing (v1.10,
                # N22): fall back to the output listing, the same as
                # when no report exists.
                from . import applog

                applog.get_logger().warning(
                    "build-report.json for %s is damaged - filing from "
                    "the output files instead", job.dir.name)
                report = {"outputs": sorted(p.name for p in job.output_dir.iterdir())}
        else:
            report = {"outputs": sorted(p.name for p in job.output_dir.iterdir())}
        # The thumbnail is the first render PNG when there is one - a
        # picture of the thing, not the SVG mock. (v1.3 took the .svg.)
        thumb = next((o for o in report.get("outputs", []) if o.endswith(".png")), None)
        if thumb is None:
            thumb = next((o for o in report.get("outputs", []) if o.endswith(".svg")), None)
        dest = Library(workspace / "library").file_job(job, report, thumbnail=thumb)
        from .graduate import graduate_recipe
        graduate_recipe(workspace, job, report)
        return {"ok": True, "job": job.dir.name,
                "detail": f"filed into library: {dest.name}"}
    if action == "plan":
        from . import live
        from .seats import load_seats
        seats_path = workspace / "seats.json"
        seats = load_seats(seats_path if seats_path.exists() else None)
        words = str(payload.get("words", "")).strip()
        if not words:
            raise ValueError("type what you want first")
        desc = live.plan_from_words(words, seats, kind=str(payload.get("kind", "scene")),
                                  workspace=workspace, cancel=cancel)
        job = Job.create(workspace / "jobs", desc)
        detail = f"draft job created: {job.dir.name} - approve it to build"
        used = live.last_plan_seat()
        if used.get("seat") == "fallback":
            detail += (" (drafted by the local fallback model - "
                       "the Gemini seat could not be reached)")
        if used.get("repaired"):
            detail += (f" (the {used.get('repair_seat')} seat repaired "
                       "the draft before it was filed)")
        return {"ok": True, "job": job.dir.name, "detail": detail}
    if action == "new-example":
        name = str(payload.get("example", ""))
        if name in EXCLUDED_EXAMPLES:
            raise ValueError(f"example {name!r} is not offered")
        path = EXAMPLES_DIR / name
        if path.parent != EXAMPLES_DIR or not path.exists():
            raise ValueError(f"no bundled example {name!r}")
        job = Job.create(workspace / "jobs", desc_mod.load(path))
        return {"ok": True, "job": job.dir.name,
                "detail": f"job created from example: {job.dir.name}"}
    if action == "images":
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("type a prompt first")
        job_name = payload.get("job") or None
        batch, bdir = imagegen.new_batch(workspace, prompt, job=job_name)

        def do_images(cancel, report):
            def on_progress(done, total, text):
                report(done / total if total else None, text)
            return imagegen.generate(workspace, bdir, cancel=cancel,
                                     progress=on_progress)

        run = _start_run("images", batch["slug"], do_images,
                         queue=f"heavy:{workspace}")
        return {"ok": True, "run": run, "batch": batch["slug"],
                "detail": "generating - candidates appear below as they land"}
    if action == "decide":
        slug = str(payload.get("batch", ""))
        bdir = workspace / "images" / slug
        if bdir.parent != workspace / "images" or not bdir.is_dir():
            raise ValueError(f"no batch {slug!r}")
        batch = imagegen.decide(workspace, bdir, str(payload.get("file", "")),
                                str(payload.get("decision", "")))
        return {"ok": True, "detail": f"{payload.get('file')}: "
                + next(c["status"] for c in batch["candidates"]
                       if c["file"] == payload.get("file"))}
    if action == "reroll":
        slug = str(payload.get("batch", ""))
        bdir = workspace / "images" / slug
        if bdir.parent != workspace / "images" or not bdir.is_dir():
            raise ValueError(f"no batch {slug!r}")

        def do_reroll(cancel, report):
            def on_progress(done, total, text):
                report(done / total if total else None, text)
            return imagegen.generate(workspace, bdir, cancel=cancel,
                                     progress=on_progress)

        run = _start_run("images", slug, do_reroll, queue=f"heavy:{workspace}")
        return {"ok": True, "run": run, "detail": "generating another round"}
    if action == "vary":
        slug = str(payload.get("batch", ""))
        bdir = workspace / "images" / slug
        if bdir.parent != workspace / "images" or not bdir.is_dir():
            raise ValueError(f"no batch {slug!r}")
        filename = str(payload.get("file", ""))
        prompt = str(payload.get("prompt") or "").strip() or None
        try:
            strength = float(payload.get("strength", imagegen.DEFAULT_VARY_STRENGTH))
        except (TypeError, ValueError):
            strength = imagegen.DEFAULT_VARY_STRENGTH

        def do_vary(cancel, report):
            def on_progress(done, total, text):
                report(done / total if total else None, text)
            return imagegen.vary(workspace, bdir, filename, prompt=prompt,
                                 strength=strength, cancel=cancel,
                                 progress=on_progress)

        run = _start_run("images", slug, do_vary, queue=f"heavy:{workspace}")
        return {"ok": True, "run": run,
                "detail": f"making variations of {filename}"}
    if action == "cloud-mesh":
        from . import cloudmesh
        photo = str(payload.get("photo", "")).strip()
        if not photo:
            raise ValueError("give the photo's path - e.g. "
                             "~/Pictures/stool.jpg")
        # A bare name resolves against the workspace first, then as-is.
        candidate = Path(photo).expanduser()
        if not candidate.is_file() and not candidate.is_absolute():
            alt = workspace / photo
            if alt.is_file():
                candidate = alt
        # Fast failures (missing photo, no token, wrong job state) before
        # the background run starts, so the chat answers at once.
        photo_path = cloudmesh.validate_photo(candidate)
        cloudmesh.token_value(workspace=workspace)
        job_name = payload.get("job") or None
        if job_name:
            job = _job(workspace, job_name)
            if job.state not in ("draft", "awaiting_approval"):
                raise ValueError(
                    f"job {job_name!r} is {job.state!r} - the cloud mesh "
                    "step only lands in a job still waiting at the plan gate")

        def do_mesh(cancel, report):
            stages = {"upload": 0.1, "preprocess": 0.25, "generate": 0.55,
                      "extract": 0.85, "done": 1.0}

            def on_progress(stage, detail):
                report(stages.get(stage), f"{stage}: {detail}")

            result = cloudmesh.cloud_mesh(
                workspace, photo_path, job=job_name,
                seed=payload.get("seed"), cancel=cancel, progress=on_progress)
            return (f"meshed {photo_path.name} into job {result['job']} "
                    f"({result['parts']} parts) - waiting at the plan gate")
        run = _start_run("cloud-mesh", photo_path.name, do_mesh)
        return {"ok": True, "run": run,
                "detail": "meshing on Hugging Face's free GPU - a few "
                          "minutes; the photo leaves this machine"}
    if action == "intake":
        from .intake import intake_photo
        photo = str(payload.get("photo", "")).strip()
        if not photo:
            raise ValueError("pick a photo first")
        desc, dest_dir = intake_photo(workspace, photo)
        job = Job.create(workspace / "jobs", desc)
        return {"ok": True, "job": job.dir.name,
                "detail": f"photo filed as {dest_dir.name}; "
                          f"job {job.dir.name} is waiting at the plan gate"}
    if action == "ingest":
        from .ingest import ingest

        path = str(payload.get("file", "")).strip()
        if not path:
            raise ValueError("pick a file first")
        desc = ingest(path, workspace)
        job = Job.create(workspace / "jobs", desc)
        mesh_note = (" the source mesh is kept in the workspace and the "
                     "build will import it" if desc.get("mesh") else "")
        return {"ok": True, "job": job.dir.name,
                "detail": f"read {Path(path).name} into job {job.dir.name} - "
                          "waiting at the plan gate; the original file "
                          f"was not changed.{mesh_note}"}
    if action == "unreal-package":
        from .unreal import package_unreal

        job = _job(workspace, payload.get("job", ""))
        dest_root = Path(payload.get("dest")) if payload.get("dest") else job.dir
        dest = package_unreal(job, dest_root)
        return {"ok": True, "detail": f"Unreal package written to {dest}"}
    if action == "settings":
        current = settings_mod.load_settings(workspace)
        if "critic_reference" in payload:
            current["critic_reference_check"] = bool(payload["critic_reference"])
            settings_mod.save_settings(workspace, current)
        return {"ok": True, "detail": "settings saved"}
    if action == "render-settings":
        # Per-job render override (T25): stored in the description's
        # "render" block. Changing how a job renders is presentation,
        # not content - it never sends the job back to the plan gate.
        job = _job(workspace, str(payload.get("job", "")))
        desc = job.load_description()
        render = dict(desc.get("render") or {})
        for key in ("engine", "resolution_px", "samples", "denoise"):
            if key in payload:
                render[key] = payload[key]
        problems = desc_mod.validate({**desc, "render": render})
        render_problems = [p for p in problems if p.startswith("render.")]
        if render_problems:
            raise ValueError("; ".join(render_problems))
        desc["render"] = render
        desc_mod.dump(desc, job.desc_path)
        job.log("render_settings", **render)
        return {"ok": True, "job": job.dir.name, "render": render,
                "detail": f"render settings saved for {job.dir.name}"}
    if action == "backup":
        from .backup import backup_workspace

        dest = payload.get("dest") or None
        result = backup_workspace(workspace, dest)
        return {"ok": True, "detail": result["note"], "path": result["path"],
                "files": result["files"]}
    if action == "update-check":
        from . import update as update_mod
        from . import __version__

        result = update_mod.check_for_update(__version__)
        if result["error"]:
            return {"ok": False, "detail": result["error"], "result": result}
        if result["update_available"]:
            detail = (f"a newer SKULLPT is out: v{result['latest']} "
                      f"(you are on v{result['current']})")
        else:
            detail = f"you are on the latest release (v{result['current']})"
        return {"ok": True, "detail": detail, "result": result}
    raise ValueError(f"unknown action {action!r}")


# ------------------------------------------------- reports + recovery (v1.5)

def build_report(workspace: Path, name: str) -> dict | None:
    """The full build report for a job: the build-report.json record
    plus the measured triangle count from the exporter's stats file
    (when one landed). None when the job has never built."""
    job = _job(workspace, name)
    report_path = job.dir / "build-report.json"
    if not report_path.exists():
        return None
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        return None
    total_tris = None
    per_part: dict = {}
    measured_materials: dict = {}
    if job.output_dir.exists():
        for stats_path in sorted(job.output_dir.glob("*.stats.json")):
            try:
                stats = json.loads(stats_path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(stats.get("total_tris"), int):
                total_tris = stats["total_tris"]
            if isinstance(stats.get("parts"), dict):
                per_part.update(stats["parts"])
            if isinstance(stats.get("materials"), dict):
                measured_materials.update(stats["materials"])
    report = dict(report)
    report["total_tris"] = total_tris
    report["tris_by_part"] = per_part
    if measured_materials:
        report["measured_materials"] = measured_materials
    return report


def recover_stale(workspace: Path) -> list[str]:
    """Return jobs stranded in `building`/`checking` by a crash to
    `approved` - unless a build is genuinely running (see
    jobs.recover_stale_jobs). Returns the recovered job names."""
    from .jobs import recover_stale_jobs

    return recover_stale_jobs(workspace)


def debug_report(workspace: Path, job: str | None = None) -> str:
    """One readable report for diagnosing a problem: versions, engine
    status, free RAM, the RAM floor, workspace state timing, the
    selected job's state + session log tail + build report summary,
    and recent runs. Never includes secrets - engine status is
    booleans only, and token-shaped strings are scrubbed."""
    import platform
    import re
    import sys

    from . import governor

    lines = ["SKULLPT debug report",
             f"workspace: {workspace}",
             f"python: {sys.version.split()[0]}  platform: {platform.system()} "
             f"{platform.release()}"]
    try:
        import PySide6
        lines.append(f"PySide6: {PySide6.__version__}")
    except Exception:
        lines.append("PySide6: not installed")
    engines = engine_status(workspace)
    lines.append("engines: " + ", ".join(
        f"{k}={'ready' if v else 'missing'}" for k, v in engines.items()))
    free = governor.free_ram_mb()
    lines.append(f"free RAM: {free} MB (floor is "
                 f"{governor.adaptive_min_free_mb()} MB)")
    if LAST_STATE_MS is not None:
        lines.append(f"last workspace_state: {LAST_STATE_MS:.1f} ms")
    try:
        from . import qtbench
        timings = qtbench.startup_timings()
        if timings:
            lines.append("startup timings: " + "; ".join(
                f"{stage} {ms:.0f} ms" for stage, ms in timings))
    except Exception:
        pass
    state = workspace_state(workspace)
    lines.append(f"jobs: {len(state['jobs'])}  batches: "
                 f"{len(state['batches'])}  library: {len(state['library'])}")
    if state.get("skipped"):
        lines.append("could not read: " + "; ".join(
            f"{s['where']}/{s['name']}: {s['reason']}" for s in state["skipped"]))
    if job:
        try:
            target = _job(workspace, job)
            lines.append(f"job {job}: state {target.state}")
            report = build_report(workspace, job)
            if report is not None:
                lines.append(f"  last build: {'passed' if report.get('ok') else 'failed'}"
                             + (f" (stage {report.get('stage')})" if report.get("stage") else ""))
                for check in (report.get("machine_checks") or {}).get("checks", []):
                    lines.append(f"  check {check.get('check')}: "
                                 f"{'pass' if check.get('ok') else 'FAIL'} - {check.get('detail', '')}")
                if report.get("total_tris") is not None:
                    lines.append(f"  triangles: {report['total_tris']}")
                problems = report.get("problems") or []
                if problems:
                    lines.append("  problems: " + "; ".join(problems[:5]))
            log_path = target.log_path
            if log_path.exists():
                tail = log_path.read_text(errors="replace").splitlines()[-15:]
                lines.append("  session log (last 15 lines):")
                lines.extend(f"    {line}" for line in tail)
        except Exception as exc:
            lines.append(f"job {job}: could not read ({exc})")
    runs = _runs()
    if runs:
        lines.append("recent runs:")
        for run in runs[-5:]:
            lines.append(f"  {run['kind']} {run['label']}: {run['status']}"
                         + (f" - {run['detail']}" if run.get("detail") else ""))
    # The app log's tail (v1.10, N39): what the app did most recently,
    # so a problem can be diagnosed from the report alone. Scrubbed
    # with the rest of the report below.
    from . import applog

    recent = applog.tail(30)
    if recent:
        lines.append("recent log (last 30 lines):")
        lines.extend(f"  {line}" for line in recent)
    text = "\n".join(lines) + "\n"
    # Defensive scrub: a token pasted into a log line must never leave
    # in a report the user sends on.
    text = re.sub(r"(hf_|sk-|gsk_|AIza|ghp_|github_pat_)[A-Za-z0-9_\-]{8,}", "[redacted]", text)
    return text



# ------------------------------------------------- chat + per-job render view

_CHATS: dict[str, list[dict]] = {}


def _chat_path(workspace: Path) -> Path:
    return workspace / "chat.jsonl"


def chat_history(workspace: Path) -> list[dict]:
    """The chat transcript for a workspace - loaded from disk on first
    use, so it survives a Bench restart (it was memory-only before
    v1.4). Call save_chat_history() after appending."""
    key = str(workspace)
    if key not in _CHATS:
        history: list[dict] = []
        path = _chat_path(workspace)
        if path.exists():
            for line in path.read_text(errors="replace").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and "text" in entry:
                    history.append(entry)
        _CHATS[key] = history[-60:]
    return _CHATS[key]


def save_chat_history(workspace: Path) -> None:
    """Write the in-memory transcript to workspace/chat.jsonl (one
    JSON entry per line, newest 60). A failed save is swallowed -
    chat persistence must never break a chat turn."""
    history = _CHATS.get(str(workspace))
    if history is None:
        return
    try:
        path = _chat_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in history[-60:]))
        tmp.replace(path)
    except OSError:
        pass


def _pick_job(workspace: Path, text: str, selected: str | None,
              want_state: str | None = None) -> str | None:
    jobs_root = workspace / "jobs"
    if not jobs_root.exists():
        return None
    names = [p.name for p in jobs_root.iterdir() if p.is_dir()]
    for name in names:
        if name in text:
            return name
    candidates = []
    for name in names:
        try:
            state = Job(jobs_root / name).state
        except Exception:
            continue
        if want_state and state != want_state:
            continue
        candidates.append((name, (jobs_root / name).stat().st_mtime))
    if selected and (not want_state or selected in [c[0] for c in candidates]):
        if selected in names:
            return selected
    if candidates:
        return max(candidates, key=lambda c: c[1])[0]
    if want_state:
        return None  # nothing is in the state this verb needs
    return selected if selected in names else None


_CHAT_HELP = ("Commands I take right here: \"make <what you want>\" drafts a job "
              "for your approval; \"picture of <thing>\" generates 3 reference "
              "candidates; \"mesh <photo path>\" turns a photo into a textured "
              "3D mesh on Hugging Face's free GPU - save your Hugging Face "
              "token in Settings first. \"mesh this photo\" uses the "
              "selected job's reference photo; "
              "\"approve\" / \"build\" / \"file\" act on the selected "
              "job; \"retry\" sends a failed job back to approved so it can "
              "build again; \"status\" reports the workshop.")


def parse_plan_words(message: str) -> str | None:
    """The words after a make/create/design/plan command, or None when
    the message is not a drafting command. The chat router and the Qt
    Bench share this so both agree on what "make ..." means."""
    import re

    low = message.strip().lower()
    if low in ("build",):
        return None
    match = re.match(r"^(make|build me|create|design|plan)\s+(me\s+)?(.+)$", low)
    if not match:
        return None
    return match.group(3).strip() or None


def chat_reply(workspace: Path, message: str, selected: str | None = None,
               cancel=None) -> dict:
    """One chat turn: commands route to the same act() the buttons use;
    open conversation goes to the planner seat when its key is set, and
    says so plainly when it is not. Returns {reply, actions}. `cancel`,
    when given, is a threading.Event passed to the seat calls, so
    cancelling a chat turn actually stops it (v1.10, N50) - a
    cancellation raises SeatCancelled instead of becoming a reply."""
    import re

    from . import live

    text = message.strip()
    low = text.lower()
    actions: list[dict] = []

    def action_button(label, action, payload):
        return {"label": label, "action": action, "payload": payload}

    if low in ("status", "status?", "what's running", "whats running"):
        st = workspace_state(workspace)
        e = st["engines"]
        jobs_line = "; ".join(f"{j['name']} ({j['state']})" for j in st["jobs"]) or "no jobs yet"
        return {"reply": (f"Blender {'ready' if e['blender'] else 'MISSING'}, image engine "
                          f"{'ready' if e['image_engine'] else 'MISSING'}, image model "
                          f"{'ready' if e['image_model'] else 'MISSING'}, Gemini key "
                          f"{'set' if e['gemini_key'] else 'not set'}, HF token "
                          f"{'set' if e.get('hf_token') else 'not set'}. Jobs: {jobs_line}."),
                "actions": actions}
    if low in ("help", "help?", "commands"):
        return {"reply": _CHAT_HELP, "actions": actions}

    # "mesh <photo path>" / "mesh this photo" - the cloud mesh step.
    mesh_match = re.match(r"^(mesh|cloud[ -]?mesh)\b\s*(.*)$", low)
    if mesh_match:
        rest = mesh_match.group(2).strip().strip("\"'")
        for prefix in ("this photo", "the photo", "it"):
            if rest == prefix:
                rest = ""
                break
            if rest.startswith(prefix + " "):
                rest = rest[len(prefix):].strip()
                break
        photo_arg = rest
        job_arg = selected if selected else None
        if not photo_arg:
            # No path given: mesh the selected job's reference photo.
            if job_arg:
                try:
                    ref = _job(workspace, job_arg).load_description().get("reference") or {}
                    stored = ref.get("stored_at")
                    if stored and Path(stored).exists():
                        photo_arg = stored
                except Exception:
                    photo_arg = ""
            if not photo_arg:
                return {"reply": ("Give me the photo's path - e.g. "
                                  "\"mesh ~/Pictures/stool.jpg\" - "
                                  "or select a job that has a reference photo "
                                  "and say \"mesh this photo\"."),
                        "actions": actions}
        try:
            act(workspace, "cloud-mesh", {"photo": photo_arg, "job": job_arg})
            return {"reply": ("Meshing that photo on Hugging Face's free GPU - "
                              "a few minutes. The photo leaves this machine; "
                              "the textured mesh comes back into a job waiting "
                              "at the plan gate."),
                    "actions": actions}
        except Exception as exc:
            return {"reply": str(exc), "actions": actions}

    image_match = re.match(
        r"^(image|picture|photo|draw|img)\s+(of\s+|for\s+)?(.+)$", low)
    gen_match = re.match(
        r"^generate\s+(an?\s+)?(image|picture|photo)\s+(of\s+|for\s+)?(.+)$", low)
    prompt = None
    if gen_match:
        prompt = gen_match.group(4)
    elif image_match:
        prompt = image_match.group(3)
    if prompt:
        try:
            result = act(workspace, "images",
                         {"prompt": prompt.strip(), "job": selected})
            return {"reply": f"Generating 3 candidates for \"{prompt.strip()}\" - "
                             "they'll appear in the Reference images section as they land.",
                    "actions": actions}
        except Exception as exc:
            return {"reply": str(exc), "actions": actions}

    words = parse_plan_words(text)
    if words:
        try:
            result = act(workspace, "plan", {"words": words, "kind": "scene"},
                         cancel=cancel)
            job_name = result["job"]
            actions.append(action_button("Approve it", "approve", {"job": job_name}))
            return {"reply": f"Drafted a job from that: {job_name}. It's waiting at the "
                             "plan gate - nothing builds until you approve it. The "
                             "summary is in the Jobs list below.",
                    "actions": actions}
        except live.SeatCancelled:
            raise
        except Exception as exc:
            return {"reply": str(exc), "actions": actions}

    for verb, want_state, next_label, next_action in (
            ("approve", "awaiting_approval", "Build it", "build"),
            ("build", "approved", None, None),
            ("file", "awaiting_review", None, None),
            ("retry", "failed", "Build it", "build")):
        if low == verb or low.startswith(verb + " "):
            job_name = _pick_job(workspace, low, selected, want_state)
            if not job_name:
                return {"reply": f"No job is waiting for {verb} right now.",
                        "actions": actions}
            try:
                result = act(workspace, verb, {"job": job_name})
            except Exception as exc:
                return {"reply": str(exc), "actions": actions}
            if next_label:
                actions.append(action_button(next_label, next_action, {"job": job_name}))
            detail = result.get("detail") or f"{verb} started for {job_name}"
            return {"reply": f"{detail}.", "actions": actions}

    # Open conversation: the planner seat first, the local fallback
    # seat second (T20). When neither brain can answer, the reply
    # says so plainly instead of faking one.
    try:
        from .seats import load_seats
        seats_path = workspace / "seats.json"
        seats = load_seats(seats_path if seats_path.exists() else None)
        st = workspace_state(workspace)
        context = ("You are the assistant inside the SKULLPT Bench, a local "
                   "workshop that turns descriptions into assets. Jobs: "
                   + ("; ".join(f"{j['name']} ({j['state']})" for j in st["jobs"])
                      or "none")
                   + f". Selected job: {selected or 'none'}. Answer briefly and "
                   "plainly. If the user wants something made, tell them to start "
                   "their message with 'make'.")
        answer, role, _err = live.text_with_fallback(
            seats, context + "\n\nUser: " + text, workspace=workspace,
            cancel=cancel)
        reply = answer.strip() or "(the planner seat answered with nothing)"
        if role == "fallback":
            reply += (" (answered by the local fallback model - "
                      "the Gemini seat could not be reached)")
        return {"reply": reply, "actions": actions}
    except live.SeatCancelled:
        raise
    except Exception as exc:
        from .live import SeatNotConfigured
        if isinstance(exc, SeatNotConfigured):
            return {"reply": "I take commands here - 'make …', 'picture of …', "
                             "'mesh <photo path>', 'approve', 'build', 'file', 'retry', 'status'. "
                             "For open conversation I need your Gemini key "
                             "saved in Settings - without it there's no brain "
                             "on this seat, and I won't fake one.",
                    "actions": actions}
        return {"reply": str(exc), "actions": actions}


