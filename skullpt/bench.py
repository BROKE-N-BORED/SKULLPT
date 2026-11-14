"""The Bench — SKULLPT's window.

One local web page that holds the whole workshop in a single view:

- LEFT: the project folders — jobs, Library, image batches, intake,
  workspace files; clicking a job selects it.
- CENTER: the Live Render Window for the selected job (the same
  viewer as the standalone `view` command, served by the bench, its
  camera saves writing back into the job), with the job's state,
  the plan-gate buttons, its renders and its build verdict.
- RIGHT: a chat panel. Commands ("make …", "picture of …",
  "approve", "build", "file", "status") route to the same actions
  the buttons use; open conversation goes to the planner seat when
  its key is set, and says so plainly when it is not.
- BELOW: new-job drafting, every job's card, the reference-image
  step (generate, keep / reject in place, re-roll), the Library,
  and the settings toggle.

Zero dependencies, same pattern as the render window and the
picker — the shell serves it locally and any browser is the window.
Heavy work (builds, image generation) runs in background threads and
the page polls for state, so a four-minute generation never freezes
the window. Every action calls the same functions the CLI calls —
the bench is a face on the shell, not a second shell.
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
# you to make it", 2026-11-11) — it stays in the tree untouched, but
# the Bench must not offer it as a one-click starting point. The packer
# also excludes it from zips; this gate is the in-code backstop.
EXCLUDED_EXAMPLES = {"skull.description.json"}

_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_RUN_SEQ = [0]


def _start_run(kind: str, label: str, fn, queue: str | None = None) -> str:
    """Run `fn` in a background thread; returns the run id.

    `fn` takes two arguments: a `cancel` threading.Event (set when
    the user cancels the run) and a `report` callable —
    `report(fraction_or_None, text)` — for progress. `fn` may return
    a string (stored as the run's detail) or a dict (stored whole as
    the run's result, with its "detail" or "reply" as the detail).
    A governor.Cancelled from `fn` marks the run "cancelled", not
    "error" — stopping a run is not a failure.

    Queue (v1.6, T24): when `queue` names a queue and another run in
    that queue is already running, the new run is recorded with
    status "queued" and starts when its turn comes — the heavy-job
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
            r.get("_queue") == queue and r["status"] == "running"
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
        except Exception as exc:  # surfaced on the page, never swallowed
            with _RUNS_LOCK:
                run = _RUNS.get(run_id)
                if run is not None:
                    run.update(status="error", detail=str(exc))
        finally:
            if queue is not None:
                _drain_queue(queue)

    threading.Thread(target=target, daemon=True).start()


def _drain_queue(queue: str) -> None:
    """Start the oldest queued run in a queue, if none is running."""
    with _RUNS_LOCK:
        if any(r.get("_queue") == queue and r["status"] == "running"
               for r in _RUNS.values()):
            return
        waiting = [r for r in _RUNS.values()
                   if r.get("_queue") == queue and r["status"] == "queued"]
        if not waiting:
            return
        # Run ids are "run-N" in creation order — the lowest number
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
    running or queued (already finished, or no such run) —
    cancelling a finished run is a no-op, not an error."""
    queue: str | None = None
    with _RUNS_LOCK:
        run = _RUNS.get(run_id)
        if run is None or run["status"] not in ("running", "queued"):
            return False
        if run["status"] == "queued":
            queue = run.get("_queue")
            run.pop("_fn", None)
            run.update(status="cancelled",
                       detail="removed from the queue before it started")
        else:
            event = run.get("_cancel")
            if event is not None:
                event.set()
            return True
    # Outside the lock: dropping a queued run may free the queue for
    # the next waiter (the drain takes the lock itself).
    if queue is not None:
        _drain_queue(queue)
    return True


def _runs() -> list[dict]:
    with _RUNS_LOCK:
        out = []
        for run in list(_RUNS.values())[-8:]:
            # The cancel Event, the queued function and the queue name
            # are not JSON-able — the page never sees them.
            out.append({k: v for k, v in run.items()
                        if k not in ("_cancel", "_fn", "_queue")})
        return out


# --------------------------------------------------------------------- state

def engine_status(workspace: Path) -> dict:
    import os

    from .blender_export import find_blender
    from . import tokenfile

    seats_path = workspace / "seats.json"
    critic_key = None
    try:
        from .seats import load_seats
        seats = load_seats(seats_path if seats_path.exists() else None)
        env_key = seats.get("critic", {}).get("env_key")
        critic_key = bool(env_key and os.environ.get(env_key))
    except Exception:
        critic_key = False
    # Character Studio (v1.7, T26): ready only when MPFB2 AND its
    # system assets pack are both installed for some Blender.
    from .blender_character import find_mpfb
    mpfb = find_mpfb()
    return {
        "blender": find_blender() is not None,
        "image_engine": imagegen.find_engine() is not None,
        "image_model": imagegen.find_model(workspace) is not None,
        "gemini_key": critic_key,
        "hf_token": bool(os.environ.get("HF_TOKEN"))
        or tokenfile.has_token(workspace),
        "character_studio": bool(mpfb["installed"] and mpfb["assets"]),
    }


# ------------------------------------------------------- state cache (P7)

# workspace_state used to re-read every job, batch, and the library
# index on each refresh — fine at 5 jobs, slow at 50. The file-derived
# part is now cached per workspace, keyed by an mtime+size signature
# over the workspace files; anything that writes a file invalidates
# it. Engines and runs are NOT cached (they change without any file
# changing: a new Blender install, a run finishing).
_STATE_CACHE: dict[str, dict] = {}
_STATE_CACHE_LOCK = threading.Lock()
# Milliseconds the last workspace_state call took (P8 — the debug
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
    Normally unnecessary — file writes invalidate it — but tests and
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
    except Exception:
        library = []
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


def act(workspace: Path, action: str, payload: dict) -> dict:
    """Run one bench action; returns a JSON-able result dict."""
    if action == "approve":
        job = _job(workspace, str(payload.get("job", "")))
        job.approve()
        return {"ok": True, "job": job.dir.name,
                "detail": f"approved {job.dir.name}"}
    if action == "retry":
        job = _job(workspace, str(payload.get("job", "")))
        job.retry()
        return {"ok": True, "job": job.dir.name,
                "detail": f"{job.dir.name} is approved again — build when ready"}
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
                "detail": f"duplicated as {copy.dir.name} — waiting at the plan gate"}
    if action == "edit-part":
        job = _job(workspace, str(payload.get("job", "")))
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            raise ValueError("edit-part needs a fields object")
        new_state = job.edit_part(str(payload.get("part", "")), fields)
        detail = f"part {payload.get('part')} updated"
        if new_state == "awaiting_approval":
            detail += " — the description changed, so it needs approval again"
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
            detail += " — the description changed, so it needs approval again"
        return {"ok": True, "job": job.dir.name, "state": new_state,
                "detail": detail}
    if action == "new-character":
        from .blender_character import default_character_description
        name = str(payload.get("name", "")).strip() or "New character"
        job = Job.create(workspace / "jobs",
                         default_character_description(name))
        return {"ok": True, "job": job.dir.name,
                "detail": f"character job created: {job.dir.name} — "
                "shape the human, then approve it to build"}
    if action == "build":
        job = _job(workspace, str(payload.get("job", "")))

        def do_build(cancel, report):
            from .builder import build_job
            stages = {"export": 0.3, "check": 0.65, "critic": 0.85}

            def on_progress(stage, detail):
                report(stages.get(stage), f"{stage}: {detail}")

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
            result["detail"] = (f"{job.dir.name} is queued at position {position} — "
                                "it builds when the run ahead of it finishes")
        return result
    if action == "file":
        job = _job(workspace, str(payload.get("job", "")))
        if job.state != "awaiting_review":
            raise ValueError(f"cannot file from state {job.state!r} (needs awaiting_review)")
        report_path = job.dir / "build-report.json"
        report = (json.loads(report_path.read_text()) if report_path.exists()
                  else {"outputs": sorted(p.name for p in job.output_dir.iterdir())})
        # The thumbnail is the first render PNG when there is one — a
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
        desc = live.plan_from_words(words, seats, kind=str(payload.get("kind", "scene")))
        job = Job.create(workspace / "jobs", desc)
        detail = f"draft job created: {job.dir.name} — approve it to build"
        used = live.last_plan_seat()
        if used.get("seat") == "fallback":
            detail += (" (drafted by the local fallback model — "
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
                "detail": "generating — candidates appear below as they land"}
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
            raise ValueError("give the photo's path — e.g. "
                             "/home/xprnc/Pictures/stool.jpg")
        # A bare name resolves against the workspace first, then as-is.
        candidate = Path(photo).expanduser()
        if not candidate.is_file() and not candidate.is_absolute():
            alt = workspace / photo
            if alt.is_file():
                candidate = alt
        # Fast failures (missing photo, no token, wrong job state) before
        # the background run starts, so the chat answers at once.
        photo_path = cloudmesh.validate_photo(candidate)
        cloudmesh.token_value()
        job_name = payload.get("job") or None
        if job_name:
            job = _job(workspace, job_name)
            if job.state not in ("draft", "awaiting_approval"):
                raise ValueError(
                    f"job {job_name!r} is {job.state!r} — the cloud mesh "
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
                    f"({result['parts']} parts) — waiting at the plan gate")
        run = _start_run("cloud-mesh", photo_path.name, do_mesh)
        return {"ok": True, "run": run,
                "detail": "meshing on Hugging Face's free GPU — a few "
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
                "detail": f"read {Path(path).name} into job {job.dir.name} — "
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
        # not content — it never sends the job back to the plan gate.
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
    report = dict(report)
    report["total_tris"] = total_tris
    report["tris_by_part"] = per_part
    return report


def recover_stale(workspace: Path) -> list[str]:
    """Return jobs stranded in `building`/`checking` by a crash to
    `approved` — unless a build is genuinely running (see
    jobs.recover_stale_jobs). Returns the recovered job names."""
    from .jobs import recover_stale_jobs

    return recover_stale_jobs(workspace)


def debug_report(workspace: Path, job: str | None = None) -> str:
    """One readable report for diagnosing a problem: versions, engine
    status, free RAM, the RAM floor, workspace state timing, the
    selected job's state + session log tail + build report summary,
    and recent runs. Never includes secrets — engine status is
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
                                 f"{'pass' if check.get('ok') else 'FAIL'} — {check.get('detail', '')}")
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
                         + (f" — {run['detail']}" if run.get("detail") else ""))
    text = "\n".join(lines) + "\n"
    # Defensive scrub: a token pasted into a log line must never leave
    # in a report the user sends on.
    text = re.sub(r"(hf_|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{8,}", "[redacted]", text)
    return text



# ------------------------------------------------- chat + per-job render view

_CHATS: dict[str, list[dict]] = {}


def _chat_path(workspace: Path) -> Path:
    return workspace / "chat.jsonl"


def chat_history(workspace: Path) -> list[dict]:
    """The chat transcript for a workspace — loaded from disk on first
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
    JSON entry per line, newest 60). A failed save is swallowed —
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


def _planner_ready(workspace: Path) -> bool:
    """True when the planner seat's key is set in the environment."""
    import os

    try:
        from .seats import load_seats
        seats_path = workspace / "seats.json"
        seats = load_seats(seats_path if seats_path.exists() else None)
        env_key = seats.get("planner", {}).get("env_key")
        return bool(env_key and os.environ.get(env_key))
    except Exception:
        return False


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
              "3D mesh on Hugging Face's free GPU (needs HF_TOKEN) — \"mesh "
              "this photo\" uses the selected job's reference photo; "
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


def chat_reply(workspace: Path, message: str, selected: str | None = None) -> dict:
    """One chat turn: commands route to the same act() the buttons use;
    open conversation goes to the planner seat when its key is set, and
    says so plainly when it is not. Returns {reply, actions}."""
    import re

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

    # "mesh <photo path>" / "mesh this photo" — the cloud mesh step.
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
                return {"reply": ("Give me the photo's path — e.g. "
                                  "\"mesh /home/xprnc/Pictures/stool.jpg\" — "
                                  "or select a job that has a reference photo "
                                  "and say \"mesh this photo\"."),
                        "actions": actions}
        try:
            act(workspace, "cloud-mesh", {"photo": photo_arg, "job": job_arg})
            return {"reply": ("Meshing that photo on Hugging Face's free GPU — "
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
            return {"reply": f"Generating 3 candidates for \"{prompt.strip()}\" — "
                             "they'll appear in the Reference images section as they land.",
                    "actions": actions}
        except Exception as exc:
            return {"reply": str(exc), "actions": actions}

    words = parse_plan_words(text)
    if words:
        try:
            result = act(workspace, "plan", {"words": words, "kind": "scene"})
            job_name = result["job"]
            actions.append(action_button("Approve it", "approve", {"job": job_name}))
            return {"reply": f"Drafted a job from that: {job_name}. It's waiting at the "
                             "plan gate — nothing builds until you approve it. The "
                             "summary is in the Jobs list below.",
                    "actions": actions}
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
        from . import live
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
            seats, context + "\n\nUser: " + text)
        reply = answer.strip() or "(the planner seat answered with nothing)"
        if role == "fallback":
            reply += (" (answered by the local fallback model — "
                      "the Gemini seat could not be reached)")
        return {"reply": reply, "actions": actions}
    except Exception as exc:
        from .live import SeatNotConfigured
        if isinstance(exc, SeatNotConfigured):
            return {"reply": "I take commands here — 'make …', 'picture of …', "
                             "'mesh <photo path>', 'approve', 'build', 'file', 'retry', 'status'. "
                             "For open conversation I need the "
                             "Gemini key set in the environment (GEMINI_API_KEY) — without it "
                             "there's no brain on this seat, and I won't fake one.",
                    "actions": actions}
        return {"reply": str(exc), "actions": actions}


def job_view_page(workspace: Path, name: str) -> str:
    """The Live Render Window for one job, served by the bench itself —
    its scene/camera calls are rewritten to the bench's per-job routes,
    so camera saves land in the job's own description."""
    from . import viewer

    job = _job(workspace, name)
    page = viewer.render_page(job.load_description(), live=True)
    page = page.replace('"/api/scene"', f'"/api/scene-job/{name}"')
    page = page.replace('"/api/cameras"', f'"/api/cameras-job/{name}"')
    return page


# ---------------------------------------------------------------------- page

PAGE_CSS = """
body { margin:0; font-family: system-ui, sans-serif; background:#17130f; color:#f2ede4; }
header { padding:16px 20px 4px; } h1 { font-size:20px; margin:0 0 4px; }
.sub { opacity:.75; font-size:13px; } .chips { margin:8px 0 2px; }
.chip { display:inline-block; padding:3px 10px; border-radius:20px; font-size:12px;
  background:#2c241b; margin-right:6px; } .chip.ok { background:#2e4a22; }
.chip.missing { background:#5c2b23; }
.layout { display:grid; grid-template-columns: 230px 1fr 330px; gap:12px;
  padding:10px 20px; align-items:start; }
@media (max-width: 1050px) { .layout { grid-template-columns: 1fr; } }
section { padding:6px 20px; } h2 { font-size:15px; margin:10px 0 8px; opacity:.9; }
aside h2, .viewpane h2 { margin-top:2px; }
.card { background:#241d16; border-radius:12px; padding:12px 14px; margin-bottom:10px; }
.card h3 { margin:0 0 4px; font-size:15px; } .meta { font-size:12px; opacity:.8; }
.state { font-weight:700; } .st-awaiting_approval { color:#e0a458; }
.st-approved { color:#8ecae6; } .st-awaiting_review { color:#9ed08a; }
.st-filed { color:#9ed08a; } .st-failed { color:#e08a8a; }
pre { white-space:pre-wrap; font-size:12px; background:#0d0a08; border-radius:8px;
  padding:10px; max-height:260px; overflow:auto; }
button { padding:8px 14px; border-radius:8px; border:0; font-size:13px; cursor:pointer;
  background:#3a2f22; color:#f2ede4; margin:4px 6px 2px 0; }
button.primary { background:#b07d2b; color:#17130f; font-weight:700; }
button.keep { background:#3f6128; color:#eef7e6; } button.reject { background:#5c2b23; color:#fbeae6; }
button:disabled { opacity:.4; cursor:default; }
input[type=text], textarea, select { background:#0d0a08; color:#f2ede4; border:1px solid #3a2f22;
  border-radius:8px; padding:8px; font-size:13px; width:100%; box-sizing:border-box; margin:4px 0 8px; }
textarea { min-height:54px; }
.imgs { display:flex; flex-wrap:wrap; gap:10px; } .cand { width:200px; }
.cand img { width:200px; height:200px; object-fit:cover; border-radius:8px; background:#0d0a08; display:block; }
.cand .meta { margin:6px 2px; } .ref { outline:2px solid #b07d2b; border-radius:8px; }
.render { max-width:320px; border-radius:8px; margin:6px 8px 2px 0; }
.run { font-size:12px; padding:4px 0; } .err { color:#e08a8a; } .good { color:#9ed08a; }
details summary { cursor:pointer; font-size:13px; opacity:.85; }
.folder-item { padding:6px 8px; border-radius:8px; cursor:pointer; font-size:13px; }
.folder-item:hover { background:#2c241b; } .folder-item.sel { background:#3a2f22; }
.folder-head { font-size:12px; opacity:.7; margin:10px 0 2px; text-transform:uppercase; letter-spacing:.06em; }
#viewframe { width:100%; height:520px; border:0; border-radius:12px; background:#0d0a08; }
.chatlog { height:430px; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding:4px 2px; }
.msg { padding:8px 10px; border-radius:10px; font-size:13px; white-space:pre-wrap; }
.msg.user { background:#3a2f22; align-self:flex-end; max-width:92%; }
.msg.bot { background:#241d16; align-self:flex-start; max-width:92%; }
.chatrow { display:flex; gap:8px; } .chatrow input { flex:1; margin:0; }
.chatrow button { margin:0; }
"""


PAGE_SCRIPT = """
var S = null; var SEL = null; var VIEWED = null; var CHATLEN = -1;
function esc(s){ var d=document.createElement('div'); d.textContent=String(s==null?'':s); return d.innerHTML; }
async function post(action, payload) {
  var body = Object.assign({action: action}, payload || {});
  var r = await fetch('/api/act', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  var data = await r.json();
  if (!data.ok) alert(data.error || 'failed');
  refresh();
}
function chip(label, ok) { return '<span class="chip ' + (ok ? 'ok' : 'missing') + '">' +
  esc(label) + (ok ? ' \\u2713' : ' missing') + '</span>'; }
function jobButtons(j) {
  var h = '';
  if (j.state === 'awaiting_approval') h += '<button class="primary" onclick="post(\\'approve\\',{job:\\'' + j.name + '\\'})">Approve</button>';
  if (j.state === 'approved') h += '<button class="primary" onclick="post(\\'build\\',{job:\\'' + j.name + '\\'})">Build</button>';
  if (j.state === 'awaiting_review') h += '<button class="primary" onclick="post(\\'file\\',{job:\\'' + j.name + '\\'})">File into Library</button>';
  if (j.state === 'failed') h += '<button class="primary" onclick="post(\\'retry\\',{job:\\'' + j.name + '\\'})">Retry</button>';
  return h;
}
function selectJob(name) { SEL = name; render(); }
function jobCard(j) {
  var h = '<div class="card"><h3>' + esc(j.title) + '</h3><div class="meta">' +
    esc(j.name) + ' \\u00b7 ' + esc(j.kind) + ' \\u00b7 <span class="state st-' + esc(j.state) + '">' +
    esc(j.state) + '</span>' +
    (j.has_reference ? ' \\u00b7 reference: ' + esc(j.reference_source || 'photo') : '') + '</div>';
  if (j.report) h += '<div class="meta">' + (j.report.ok
    ? '<span class="good">last build passed</span>'
    : '<span class="err">last build failed: ' + esc((j.report.problems || []).join('; ')) + '</span>') + '</div>';
  h += '<div>' + jobButtons(j) + '</div>';
  j.renders.forEach(function(rn) {
    h += '<img class="render" src="/files/job/' + encodeURIComponent(j.name) + '/' + encodeURIComponent(rn) + '">';
  });
  if (j.outputs.length) h += '<div class="meta">files: ' + j.outputs.map(esc).join(', ') + '</div>';
  h += '<details><summary>description summary</summary><pre>' + esc(j.summary) + '</pre></details></div>';
  return h;
}
function batchCard(b) {
  var h = '<div class="card"><h3>' + esc(b.prompt) + '</h3><div class="meta">batch ' +
    esc(b.slug) + (b.job ? ' \\u00b7 job ' + esc(b.job) : '') +
    (b.reference_file ? ' \\u00b7 reference: ' + esc(b.reference_file) : '') + '</div><div class="imgs">';
  b.candidates.forEach(function(c) {
    h += '<div class="cand">';
    if (c.status === 'rejected') h += '<div class="meta err">rejected \\u2014 deleted</div>';
    else if (c.status === 'failed') h += '<div class="meta err">generation failed</div>';
    else h += '<img class="' + (b.reference_file === c.file ? 'ref' : '') +
      '" src="/files/batch/' + encodeURIComponent(b.slug) + '/' + encodeURIComponent(c.file) + '">';
    h += '<div class="meta">seed ' + c.seed + ' \\u00b7 ' + esc(c.status) + '</div>';
    if (c.status === 'pending') {
      h += '<button class="keep" onclick="post(\\'decide\\',{batch:\\'' + b.slug + '\\',file:\\'' + c.file + '\\',decision:\\'keep\\'})">Keep</button>' +
           '<button class="reject" onclick="post(\\'decide\\',{batch:\\'' + b.slug + '\\',file:\\'' + c.file + '\\',decision:\\'reject\\'})">Reject</button>';
    }
    h += '</div>';
  });
  h += '</div><button onclick="post(\\'reroll\\',{batch:\\'' + b.slug + '\\'})">Generate another round</button></div>';
  return h;
}
function render() {
  if (!S) return;
  document.getElementById('ws').textContent = S.workspace;
  document.getElementById('chips').innerHTML =
    chip('Blender', S.engines.blender) + chip('Image engine', S.engines.image_engine) +
    chip('Image model', S.engines.image_model) + chip('Gemini key', S.engines.gemini_key) +
    chip('HF token', S.engines.hf_token);
  document.getElementById('runs').innerHTML = S.runs.filter(function(r){return r.status==='running';})
    .map(function(r){ return '<div class="run">\\u23f3 ' + esc(r.kind) + ': ' + esc(r.label) + ' \\u2014 working\\u2026</div>'; }).join('') +
    S.runs.filter(function(r){return r.status==='error';}).slice(-2)
    .map(function(r){ return '<div class="run err">' + esc(r.kind) + ' ' + esc(r.label) + ': ' + esc(r.detail) + '</div>'; }).join('');
  // folders pane
  var f = '<div class="folder-head">Jobs</div>';
  if (!S.jobs.length) f += '<div class="meta">no jobs yet</div>';
  S.jobs.forEach(function(j){
    f += '<div class="folder-item' + (SEL === j.name ? ' sel' : '') + '" onclick="selectJob(\\'' + j.name + '\\')">\\u25b8 ' +
      esc(j.title) + ' <span class="state st-' + esc(j.state) + '">' + esc(j.state) + '</span></div>';
  });
  f += '<div class="folder-head">Library</div>' + (S.folders.library.length
    ? S.folders.library.map(function(n){ return '<div class="folder-item">\\u25b8 ' + esc(n) + '</div>'; }).join('')
    : '<div class="meta">empty</div>');
  f += '<div class="folder-head">Image batches</div>' + (S.folders.images.length
    ? S.folders.images.map(function(n){ return '<div class="folder-item">\\u25b8 ' + esc(n) + '</div>'; }).join('')
    : '<div class="meta">none yet</div>');
  f += '<div class="folder-head">Intake (photos in)</div>' + (S.folders.intake.length
    ? S.folders.intake.map(function(n){ return '<div class="folder-item">\\u25b8 ' + esc(n) + '</div>'; }).join('')
    : '<div class="meta">nothing in intake</div>');
  f += '<div class="folder-head">Workspace files</div>' + (S.folders.root_files.length
    ? S.folders.root_files.map(function(n){ return '<div class="folder-item">\\u25b8 ' + esc(n) + '</div>'; }).join('')
    : '<div class="meta">none</div>');
  if (S.skipped && S.skipped.length) {
    f += '<div class="folder-head">Couldn\\'t read</div>' + S.skipped.map(function(s){
      return '<div class="folder-item err">\\u25b8 ' + esc(s.where) + ' / ' + esc(s.name) +
        ' <span class="meta">' + esc(s.reason) + '</span></div>'; }).join('');
  }
  document.getElementById('folders').innerHTML = f;
  // selection default: first job
  if (!SEL && S.jobs.length) SEL = S.jobs[0].name;
  if (SEL && !S.jobs.some(function(j){ return j.name === SEL; })) SEL = S.jobs.length ? S.jobs[0].name : null;
  var sel = S.jobs.filter(function(j){ return j.name === SEL; })[0];
  // render view pane
  var vp = document.getElementById('viewpane-head');
  if (sel) {
    vp.innerHTML = '<b>' + esc(sel.title) + '</b> <span class="state st-' + esc(sel.state) + '">' + esc(sel.state) + '</span> ' +
      jobButtons(sel) +
      '<button onclick="window.open(\\'/view/' + sel.name + '\\',\\'_blank\\')">Open the render window full-screen</button>';
    if (VIEWED !== SEL) { VIEWED = SEL; document.getElementById('viewframe').src = '/view/' + SEL; }
  } else {
    vp.innerHTML = '<span class="meta">no job selected \\u2014 start one below or ask in the chat</span>';
    if (VIEWED !== null) { VIEWED = null; document.getElementById('viewframe').src = 'about:blank'; }
  }
  document.getElementById('jobdetail').innerHTML = sel ? jobCard(sel) : '';
  document.getElementById('jobs').innerHTML = S.jobs.length ? S.jobs.map(jobCard).join('') : '<div class="meta">no jobs yet</div>';
  var isel = document.getElementById('imgjob');
  var cur = isel.value;
  isel.innerHTML = '<option value="">(no job \\u2014 batch only)</option>' + S.jobs.map(function(j){
    return '<option value="' + j.name + '">' + esc(j.title) + '</option>'; }).join('');
  isel.value = cur || SEL || '';
  document.getElementById('batches').innerHTML = S.batches.length ? S.batches.map(batchCard).join('') : '<div class="meta">no image batches yet</div>';
  document.getElementById('library').innerHTML = S.library.length ? S.library.map(function(a){
    return '<div class="card"><h3>' + esc(a.name) + '</h3><div class="meta">' + esc(a.kind) +
      ' \\u00b7 ' + a.files.length + ' files: ' + a.files.map(esc).join(', ') + '</div></div>'; }).join('')
    : '<div class="meta">library is empty</div>';
  document.getElementById('critref').checked = !!S.settings.critic_reference_check;
  var ex = document.getElementById('example');
  if (!ex.options.length) ex.innerHTML = S.examples.map(function(e){
    return '<option value="' + esc(e) + '">' + esc(e) + '</option>'; }).join('');
  document.getElementById('planbtn').disabled = !S.engines.gemini_key;
  document.getElementById('plannote').textContent = S.engines.gemini_key ? ''
    : 'needs GEMINI_API_KEY in the environment \\u2014 until then, start from an example below';
  document.getElementById('genbtn').disabled = !(S.engines.image_engine && S.engines.image_model);
  document.getElementById('gennote').textContent = (S.engines.image_engine && S.engines.image_model) ? ''
    : 'needs the image engine + model \\u2014 see the README install options';
  // chat transcript (only re-render when it changed, so typing is safe)
  if (S.chat.length !== CHATLEN) {
    CHATLEN = S.chat.length;
    var log = document.getElementById('chatlog');
    log.innerHTML = S.chat.map(function(m){
      var h = '<div class="msg ' + (m.role === 'user' ? 'user' : 'bot') + '">' + esc(m.text);
      (m.actions || []).forEach(function(a){
        h += '<br><button class="primary" onclick="post(\\'' + a.action + '\\',' + JSON.stringify(a.payload).replace(/"/g, '&quot;') + ')">' + esc(a.label) + '</button>';
      });
      return h + '</div>';
    }).join('');
    log.scrollTop = log.scrollHeight;
  }
}
async function sendChat() {
  var input = document.getElementById('chatinput');
  var text = input.value.trim();
  if (!text) return;
  input.value = '';
  var r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({message: text, job: SEL})});
  var data = await r.json();
  if (!data.ok) alert(data.error || 'chat failed');
  CHATLEN = -1;
  refresh();
}
async function refresh() {
  try { var r = await fetch('/api/state'); var d = await r.json(); if (d.ok) { S = d.state; render(); } } catch (e) {}
}
refresh(); setInterval(refresh, 3000);
"""

_PAGE = ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
         "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
         "<title>SKULLPT Bench</title><style>" + PAGE_CSS + "</style></head><body>"
         "<header><h1>SKULLPT \\u2014 Bench</h1>"
         "<div class=\"sub\">workspace: <span id=\"ws\"></span></div>"
         "<div class=\"chips\" id=\"chips\"></div><div id=\"runs\"></div></header>"
         "<div class=\"layout\">"
         "<aside><h2>Project folders</h2><div class=\"card\" id=\"folders\"></div></aside>"
         "<div class=\"viewpane\"><h2>Live render view</h2>"
         "<div class=\"card\"><div id=\"viewpane-head\"></div>"
         "<iframe id=\"viewframe\" title=\"Live render view\"></iframe>"
         "<div class=\"meta\">Drag to orbit \\u00b7 right-drag to pan \\u00b7 scroll to zoom \\u00b7 "
         "click a part to focus it. Cameras you save in there write back into the job.</div></div>"
         "<div id=\"jobdetail\"></div></div>"
         "<aside><h2>Chat</h2><div class=\"card\">"
         "<div class=\"chatlog\" id=\"chatlog\"></div>"
         "<div class=\"chatrow\"><input type=\"text\" id=\"chatinput\" "
         "placeholder=\"make a market stall \\u00b7 picture of a brass lamp \\u00b7 approve \\u00b7 build\""
         " onkeydown=\"if(event.key==='Enter')sendChat()\">"
         "<button class=\"primary\" onclick=\"sendChat()\">Send</button></div>"
         "<div class=\"meta\">Commands work without any key. Open conversation uses the "
         "planner seat (Gemini key) when it's set.</div></div></aside>"
         "</div>"
         "<section><h2>New job</h2><div class=\"card\">"
         "<label>What do you want? (the planner drafts the description \\u2014 you approve before anything builds)</label>"
         "<textarea id=\"words\" placeholder=\"a small market stall with a wood counter\"></textarea>"
         "<button class=\"primary\" id=\"planbtn\" onclick=\"post('plan',{words:document.getElementById('words').value,kind:'scene'})\">Draft from words</button>"
         " <span class=\"meta\" id=\"plannote\"></span>"
         "<div style=\"margin-top:10px\"><label>\\u2026or start from a bundled example</label>"
         "<select id=\"example\"></select>"
         "<button onclick=\"post('new-example',{example:document.getElementById('example').value})\">Start from example</button></div>"
         "</div></section>"
         "<section><h2>All jobs</h2><div id=\"jobs\"></div></section>"
         "<section><h2>Reference images</h2><div class=\"card\">"
         "<label>Prompt for candidate pictures (3 at a time; the keeper becomes the job's reference)</label>"
         "<input type=\"text\" id=\"imgprompt\" placeholder=\"a brass oil lamp, product photo\">"
         "<select id=\"imgjob\"></select>"
         "<button class=\"primary\" id=\"genbtn\" onclick=\"post('images',{prompt:document.getElementById('imgprompt').value,job:document.getElementById('imgjob').value||null})\">Generate 3 candidates</button>"
         " <span class=\"meta\" id=\"gennote\"></span></div>"
         "<div id=\"batches\"></div></section>"
         "<section><h2>Library</h2><div id=\"library\"></div></section>"
         "<section><h2>Settings</h2><div class=\"card\">"
         "<label><input type=\"checkbox\" id=\"critref\" onchange=\"post('settings',{critic_reference:this.checked})\">"
         " Vision critic compares builds against the job's chosen reference picture</label>"
         "<div class=\"meta\">A single job can override this either way with \"critic_reference\": true/false in its description.</div>"
         "</div></section>"
         "<script>\n" + PAGE_SCRIPT + "\n</script></body></html>\n")


def render_page() -> str:
    return _PAGE


# -------------------------------------------------------------------- server

_CTYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".svg": "image/svg+xml", ".html": "text/html", ".css": "text/css",
           ".json": "application/json", ".glb": "model/gltf-binary"}


def make_server(workspace: str | Path, host: str = "127.0.0.1", port: int = 8770):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ws = Path(workspace)
    (ws / "jobs").mkdir(parents=True, exist_ok=True)
    (ws / "library").mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict):
            self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

        def _serve_file(self, base: Path, name: str):
            target = (base / name).resolve()
            if target.parent != base.resolve() and base.resolve() not in target.parents:
                self._json(404, {"ok": False, "error": "not found"})
                return
            if not target.is_file():
                self._json(404, {"ok": False, "error": "not found"})
                return
            ctype = _CTYPES.get(target.suffix.lower(), "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            import urllib.parse
            try:
                if self.path == "/":
                    self._send(200, render_page().encode(), "text/html; charset=utf-8")
                elif self.path == "/api/state":
                    self._json(200, {"ok": True, "state": workspace_state(ws)})
                elif self.path.startswith("/view/"):
                    name = urllib.parse.unquote(self.path[len("/view/"):])
                    self._send(200, job_view_page(ws, name).encode(),
                               "text/html; charset=utf-8")
                elif self.path.startswith("/api/scene-job/"):
                    from . import viewer
                    name = urllib.parse.unquote(self.path[len("/api/scene-job/"):])
                    job = _job(ws, name)
                    self._json(200, viewer.scene_data(job.load_description()))
                elif self.path.startswith("/files/job/"):
                    parts = self.path[len("/files/job/"):].split("/", 1)
                    if len(parts) != 2:
                        self._json(404, {"ok": False, "error": "not found"})
                        return
                    job_name = urllib.parse.unquote(parts[0])
                    fname = urllib.parse.unquote(parts[1])
                    job_dir = ws / "jobs" / job_name
                    if job_dir.parent != ws / "jobs" or not job_dir.is_dir():
                        self._json(404, {"ok": False, "error": "not found"})
                        return
                    if (job_dir / "output" / fname).exists():
                        self._serve_file(job_dir / "output", fname)
                    else:
                        self._serve_file(job_dir, fname)
                elif self.path.startswith("/files/batch/"):
                    parts = self.path[len("/files/batch/"):].split("/", 1)
                    if len(parts) != 2:
                        self._json(404, {"ok": False, "error": "not found"})
                        return
                    slug = urllib.parse.unquote(parts[0])
                    fname = urllib.parse.unquote(parts[1])
                    bdir = ws / "images" / slug
                    if bdir.parent != ws / "images" or not bdir.is_dir():
                        self._json(404, {"ok": False, "error": "not found"})
                        return
                    batch = imagegen.load_batch(bdir)
                    if fname not in {c["file"] for c in batch["candidates"]}:
                        self._json(404, {"ok": False, "error": "not a candidate"})
                        return
                    self._serve_file(bdir, fname)
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})

        def do_POST(self):
            import urllib.parse
            if self.path == "/api/act":
                try:
                    payload = self._read_body()
                    action = str(payload.pop("action", ""))
                    self._json(200, act(ws, action, payload))
                except Exception as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                return
            if self.path == "/api/chat":
                try:
                    payload = self._read_body()
                    message = str(payload.get("message", "")).strip()
                    if not message:
                        self._json(400, {"ok": False, "error": "empty message"})
                        return
                    history = chat_history(ws)
                    history.append({"role": "user", "text": message})
                    result = chat_reply(ws, message,
                                        selected=payload.get("job") or None)
                    history.append({"role": "bot", "text": result["reply"],
                                    "actions": result.get("actions", [])})
                    del history[:-60]
                    save_chat_history(ws)
                    self._json(200, {"ok": True, "reply": result["reply"],
                                     "actions": result.get("actions", [])})
                except Exception as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                return
            if self.path.startswith("/api/cameras-job/"):
                try:
                    from . import viewer
                    name = urllib.parse.unquote(self.path[len("/api/cameras-job/"):])
                    job = _job(ws, name)
                    payload = self._read_body()
                    desc = job.load_description()
                    if "delete" in payload:
                        removed = viewer.delete_camera(desc, str(payload["delete"]))
                        desc_mod.dump(desc, job.desc_path)
                        self._json(200, {"ok": removed,
                                         "cameras": desc.get("cameras", [])})
                    else:
                        entry = viewer.upsert_camera(desc, payload)
                        desc_mod.dump(desc, job.desc_path)
                        self._json(200, {"ok": True, "camera": entry,
                                         "cameras": desc.get("cameras", [])})
                except Exception as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(404, {"ok": False, "error": "not found"})

    return ThreadingHTTPServer((host, port), Handler)


def _open_browser(url: str) -> None:
    """Open the Bench URL in the default browser. Best-effort only:
    a missing browser must never break the server. Set
    DESIGNSHELL_NO_BROWSER=1 to skip the open (headless use)."""
    if os.environ.get("DESIGNSHELL_NO_BROWSER"):
        return
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def serve(workspace: str | Path, host: str = "127.0.0.1", port: int = 8770) -> str:
    """Serve the Bench for a workspace; returns the URL. Blocks."""
    server = make_server(workspace, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"SKULLPT Bench: {url}  (opening in your browser)")
    print(f"Workspace: {workspace}  (Ctrl+C to stop)")
    # The Bench is a web page — a server that only prints a URL never
    # shows the user anything. Open the window for them.
    opener = threading.Timer(0.5, _open_browser, args=(url,))
    opener.daemon = True
    opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url
