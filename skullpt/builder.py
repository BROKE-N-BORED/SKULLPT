"""The Builder — runs one approved job through the loop.

Order of operations (the blueprint, in code):
  plan gate -> governor budget gate -> heavy-job lock -> exporters
  -> machine checks -> critic -> build report.
Repairs are patches and re-runs by the caller; this skeleton runs the
loop once and reports honestly, it does not loop silently.
"""
from __future__ import annotations

from pathlib import Path

from . import checks as checks_mod
from . import description as desc_mod
from . import exporters, governor
from .critic import CriticNotConfigured, get_critic
from .jobs import Job
from .seats import load_seats

MAX_VISION_ROUNDS = 2


def _run_critic(job: Job, desc: dict, outputs: list[str], seats: dict,
                preview_paths: list[str], workspace: Path | None = None,
                cancel=None, progress=None):
    """Critic pass, with the vision patch loop when a live critic is seated.

    The Gemini critic runs only when its key is in the environment;
    otherwise the offline critic answers and says so. When the vision
    critic returns machine-appliable patches, they are applied as
    parameter changes, the Blender exporter re-runs, and the critic
    looks again — at most MAX_VISION_ROUNDS rounds (blueprint cap).
    Returns (critique, rounds) where rounds records every patch applied.
    `cancel` is a threading.Event checked before each patch round;
    a set event raises governor.Cancelled.
    """
    import os

    critic_seat = seats.get("critic", {})
    env_key = critic_seat.get("env_key")
    prefer = "gemini" if env_key and os.environ.get(env_key) else "offline"
    critic = get_critic(seats, prefer=prefer)

    # Reference comparison (Joshua's toggle): when the workspace switch
    # (or the job's own say-so) allows it and the job has a chosen
    # reference image on record, the vision critic judges the render
    # against that picture too. Resolution lives in settings.py.
    reference_png = None
    if workspace is not None:
        from . import settings as settings_mod
        if settings_mod.critic_reference_enabled(workspace, desc):
            ref_path = settings_mod.reference_image_path(desc)
            if ref_path is not None:
                reference_png = ref_path.read_bytes()

    try:
        critique = critic.critique(desc, preview_paths, reference_png=reference_png)
    except CriticNotConfigured as exc:
        critique = get_critic(seats).critique(desc, preview_paths)
        critique["note"] += f" (vision critic unavailable: {exc})"
        return critique, []
    if reference_png is not None and not critique.get("vision"):
        critique["note"] += " (reference comparison is on, but the offline critic cannot see pictures)"

    rounds: list[dict] = []
    if not critique.get("vision") or desc.get("kind") != "scene":
        return critique, rounds
    from . import live
    snapshot_saved = False
    for round_no in range(1, MAX_VISION_ROUNDS + 1):
        if cancel is not None and cancel.is_set():
            raise governor.Cancelled("build cancelled during the critic loop")
        patches = live.extract_patches(critique.get("fixes", []))
        if not patches:
            break
        applied = live.apply_patches(desc, patches)
        if not applied:
            break
        if not snapshot_saved:
            # The patches rewrite the approved description in place —
            # freeze what was there first, so it can be recovered.
            job.snapshot_description("description.before-vision-patch.json")
            snapshot_saved = True
        desc_mod.dump(desc, job.desc_path)
        job.log("vision_patch", round=round_no, applied=applied)
        rounds.append({"round": round_no, "applied": applied})
        if progress is not None:
            progress("critic", f"vision patch round {round_no} — rebuilding")
        fn = exporters.EXPORTERS["blender_scene"]
        refreshed = fn(desc, job.output_dir)
        for name in refreshed:
            if name not in outputs:
                outputs.append(name)
        preview_paths = [str(job.output_dir / o) for o in outputs
                         if o.endswith((".svg", ".html", ".png"))]
        try:
            critique = critic.critique(desc, preview_paths, reference_png=reference_png)
        except CriticNotConfigured:
            break
    return critique, rounds


def build_job(job: Job, workspace: Path, seats_path=None,
              cancel=None, progress=None) -> dict:
    """Run one approved job through the loop.

    `cancel`, when given, is a threading.Event checked between
    exporters and before the critic pass; a set event raises
    governor.Cancelled and the job goes back to `approved` (the
    description is still approved — the build just did not finish).
    `progress`, when given, is called with (stage, detail) as the
    build moves through export, check, and critic.
    """
    job.require_approved()  # the Plan Gate — nothing builds unapproved
    desc = job.load_description()

    # Render settings (T25): the job's own block wins, then the
    # workspace defaults, then the code defaults. Stamped onto the
    # in-memory description only — the file keeps the user's own
    # partial block, and the resolved values ride into the build
    # report so the user can see what was actually used.
    from . import settings as settings_mod
    render = settings_mod.resolve_render_settings(workspace, desc)
    desc["render"] = render

    budget_problems = governor.check_budget(desc)
    if budget_problems:
        job.fail("budget: " + "; ".join(budget_problems))
        return {"ok": False, "stage": "budget", "problems": budget_problems}

    # A kind with no exporters (project — no studio yet; character got
    # its studio in v1.7) used to "build successfully" with zero
    # outputs. Fail loudly instead, before the lock is even taken.
    kind_exporters = exporters.DEFAULT_EXPORTERS.get(desc["kind"], ())
    if not kind_exporters:
        reason = (f"no exporter for kind {desc['kind']!r} yet — "
                  "nothing was built, so there is nothing to check")
        job.fail(reason)
        return {"ok": False, "stage": "exporters", "problems": [reason]}

    lock = governor.HeavyJobLock(workspace / ".heavy.lock")
    job.transition("building")
    outputs: list[str] = []
    used_recipes: list[str] = []
    try:
        with lock:
            for name in kind_exporters:
                if cancel is not None and cancel.is_set():
                    raise governor.Cancelled("build cancelled")
                if progress is not None:
                    progress("export", f"running the {name} exporter")
                fn = exporters.EXPORTERS[name]
                outputs.extend(fn(desc, job.output_dir))
                job.log("export", exporter=name)
                # The RAM floor is checked at acquire time; re-check
                # between exporters so a build that grows past it is
                # stopped rather than allowed to thrash the machine.
                lock.recheck()
            used_recipes = sorted({p["type"] for p in desc["parts"]})
    except governor.Cancelled:
        # The user stopped the run — like a governor refusal, the job
        # stays approved and can be built again when wanted.
        job.log("cancelled")
        job.transition("approved")
        raise
    except governor.GovernorError as exc:
        # A governor REFUSAL is not a build failure — nothing ran (or
        # nothing finished), so the job stays approved and can be
        # retried when the machine is free. (Marking it failed here
        # used to strand approved jobs in a dead state no approval
        # could rescue.)
        job.log("governor_refused", reason=str(exc))
        job.transition("approved")
        return {"ok": False, "stage": "governor", "problems": [str(exc)]}
    except Exception as exc:  # exporter blew up — record it, don't hide it
        job.fail(f"export: {type(exc).__name__}: {exc}")
        return {"ok": False, "stage": "export", "problems": [str(exc)]}

    if cancel is not None and cancel.is_set():
        job.log("cancelled")
        job.transition("approved")
        raise governor.Cancelled("build cancelled")

    job.transition("checking")
    if progress is not None:
        progress("check", "running the machine checks")
    try:
        report_checks = checks_mod.run_machine_checks(desc, job.output_dir, outputs)

        gate = None
        if desc.get("game_ready") and any(o.endswith(".glb") for o in outputs):
            from . import gameready
            gate = gameready.run_gate(desc, job.output_dir, outputs)
            job.log("game_ready_gate", ok=gate["ok"], total_tris=gate.get("total_tris"))

        seats = load_seats(seats_path)
        preview_paths = [str(job.output_dir / o) for o in outputs
                         if o.endswith((".svg", ".html", ".png"))]
        if progress is not None:
            progress("critic", "the critic is looking at the renders")
        critique, vision_rounds = _run_critic(job, desc, outputs, seats, preview_paths,
                                              workspace=workspace,
                                              cancel=cancel, progress=progress)
    except governor.Cancelled:
        # Cancelled mid-check or mid-critic: the job goes back to
        # approved, like a cancelled export — never stranded in
        # "checking" with no way back.
        job.log("cancelled")
        job.transition("approved")
        raise
    if vision_rounds:
        # patches changed the description -> facts must be re-checked
        report_checks = checks_mod.run_machine_checks(desc, job.output_dir, outputs)
        if desc.get("game_ready") and any(o.endswith(".glb") for o in outputs):
            from . import gameready
            gate = gameready.run_gate(desc, job.output_dir, outputs)
    overall_ok = report_checks["ok"] and (gate is None or gate["ok"])
    report = {
        "ok": overall_ok,
        "outputs": outputs,
        "recipes": used_recipes,
        "render": render,
        "machine_checks": report_checks,
        "critique": critique,
    }
    if gate is not None:
        report["game_ready"] = gate
    if vision_rounds:
        report["vision_loop"] = vision_rounds
    job.log("checked", ok=overall_ok)
    if overall_ok:
        job.transition("awaiting_review")
    elif not report_checks["ok"]:
        job.fail("machine checks failed")
    else:
        job.fail("game-ready gate failed")
    return report
