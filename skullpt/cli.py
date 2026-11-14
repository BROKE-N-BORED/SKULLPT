"""Command-line entry point.

Workspace layout (created by `init`):
  <workspace>/jobs/       one folder per job
  <workspace>/library/    the Asset Library
  <workspace>/seats.json  optional seat overrides (no keys, ever)

Commands:
  init <workspace>
  new <workspace> <description.json>     create a job (draft -> awaiting approval)
  show <workspace> <job>                 print the plain-English summary to approve
  approve <workspace> <job>              the user's gate
  retry <workspace> <job>                send a failed job back to approved
  delete <workspace> <job>               remove a job's folder entirely
  rename <workspace> <job> <name>        rename a job (folder + title)
  duplicate <workspace> <job>            a fresh job with the same description
  recover <workspace>                    return crash-stranded jobs to approved
  build <workspace> <job>                run the loop (refuses unless approved)
  file <workspace> <job>                 file an awaiting-review job into the Library
  search <workspace> <query> [--kind K]  search the Library before making new
  demo <workspace>                       run the bundled example end to end
  view <description> [--port N]          serve the Live Render Window for a description
  snapshot <description> <out.html>      write a standalone (non-live) viewer page
  plan <workspace> <words...> [--kind K] draft a description from plain words (planner seat)
  critique <workspace> <job>             run the live vision critic on a job's renders
  ingest <file.glb|.obj|.stl|.fbx|.blend> <out.json>
                                         derive a description FROM an existing asset
                                         (the source mesh is kept; the build imports it)
  intake <workspace> <photo>             file a photo as a reference + draft job
  cloud-mesh <workspace> <photo> [--job J] [--seed N]
                                         photo -> textured 3D mesh on Hugging
                                         Face's free GPU, filed in a job
                                         (needs HF_TOKEN in the environment)
  recipes <workspace>                    list graduated recipes
  apply-recipe <workspace> <file> [out]  start a description from a graduated recipe
  unreal-package <workspace> <job>       assemble the Unreal import package for a built job
  images <workspace> <prompt...> [--job J] [--count 3]
                                         generate a batch of candidate reference images
  pick <batch-dir> [--port N]            serve the Image Picker (keep / reject)
  decide <batch-dir> <file> keep|reject  decide a candidate from the terminal
  reroll <batch-dir>                     generate another round into the same batch
  vary <batch-dir> <file> [--prompt ...] [--strength 0.6] [--count 3]
                                         img2img variations of a kept candidate
                                         into a new batch
  settings <workspace> [--critic-reference on|off]
                       [--render-engine auto|eevee|cycles]
                       [--render-resolution N] [--render-samples N]
                       [--render-denoise on|off]
                                         show or set workspace switches
  backup <workspace> [dest]              zip the whole workspace (the Hugging
                                         Face token is never in a backup)
  update-check                           is there a newer SKULLPT release?
                                         (read-only — never downloads)
  bench [workspace]                      open the Bench — the shell's window,
                                         a native Linux desktop window
                                         (Jobs home, Render, Images, Library,
                                         Settings); no workspace means the
                                         folder chosen in Settings, else
                                         ~/design-shell-workspace
  webbench <workspace> [--port N]        serve the legacy web Bench instead
                                         (same jobs/images/library, in a browser)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import description as desc_mod
from .builder import build_job
from .jobs import GateError, Job
from .library import Library

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "shop-panel.description.json"


def _ws(path: str) -> Path:
    ws = Path(path)
    (ws / "jobs").mkdir(parents=True, exist_ok=True)
    (ws / "library").mkdir(parents=True, exist_ok=True)
    return ws


def _job(ws: Path, name: str) -> Job:
    return Job(ws / "jobs" / name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skullpt", description="SKULLPT — standalone design & asset workshop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init",):
        p = sub.add_parser(name); p.add_argument("workspace")
    p = sub.add_parser("new"); p.add_argument("workspace"); p.add_argument("description")
    for name in ("show", "approve", "retry", "build", "file", "delete", "duplicate"):
        p = sub.add_parser(name); p.add_argument("workspace"); p.add_argument("job")
    p = sub.add_parser("rename"); p.add_argument("workspace"); p.add_argument("job")
    p.add_argument("name")
    p = sub.add_parser("recover"); p.add_argument("workspace")
    p = sub.add_parser("search"); p.add_argument("workspace"); p.add_argument("query")
    p.add_argument("--kind", default=None)
    p = sub.add_parser("demo"); p.add_argument("workspace")
    p = sub.add_parser("view"); p.add_argument("description")
    p.add_argument("--port", type=int, default=8760)
    p = sub.add_parser("snapshot"); p.add_argument("description"); p.add_argument("out")
    p = sub.add_parser("plan"); p.add_argument("workspace")
    p.add_argument("words", nargs="+"); p.add_argument("--kind", default="scene")
    p = sub.add_parser("critique"); p.add_argument("workspace"); p.add_argument("job")
    p = sub.add_parser("ingest"); p.add_argument("file"); p.add_argument("out")
    p = sub.add_parser("intake"); p.add_argument("workspace"); p.add_argument("photo")
    p = sub.add_parser("cloud-mesh"); p.add_argument("workspace"); p.add_argument("photo")
    p.add_argument("--job", default=None)
    p.add_argument("--seed", type=int, default=None)
    p = sub.add_parser("recipes"); p.add_argument("workspace")
    p = sub.add_parser("apply-recipe"); p.add_argument("workspace"); p.add_argument("recipe")
    p.add_argument("out", nargs="?", default=None)
    p = sub.add_parser("unreal-package"); p.add_argument("workspace"); p.add_argument("job")
    p.add_argument("--dest", default=None)
    p = sub.add_parser("images"); p.add_argument("workspace")
    p.add_argument("prompt", nargs="+"); p.add_argument("--job", default=None)
    p.add_argument("--count", type=int, default=3); p.add_argument("--size", type=int, default=512)
    p = sub.add_parser("pick"); p.add_argument("batch_dir")
    p.add_argument("--port", type=int, default=8762)
    p = sub.add_parser("decide"); p.add_argument("batch_dir"); p.add_argument("file")
    p.add_argument("decision", choices=("keep", "reject"))
    p = sub.add_parser("reroll"); p.add_argument("batch_dir")
    p.add_argument("--count", type=int, default=None)
    p = sub.add_parser("vary"); p.add_argument("batch_dir"); p.add_argument("file")
    p.add_argument("--prompt", nargs="+", default=None)
    p.add_argument("--strength", type=float, default=0.6)
    p.add_argument("--count", type=int, default=3)
    p = sub.add_parser("settings"); p.add_argument("workspace")
    p.add_argument("--critic-reference", choices=("on", "off"), default=None)
    p.add_argument("--render-engine", choices=("auto", "eevee", "cycles"), default=None)
    p.add_argument("--render-resolution", type=int, default=None)
    p.add_argument("--render-samples", type=int, default=None)
    p.add_argument("--render-denoise", choices=("on", "off"), default=None)
    p = sub.add_parser("backup"); p.add_argument("workspace")
    p.add_argument("dest", nargs="?", default=None)
    p = sub.add_parser("update-check")
    p = sub.add_parser("bench"); p.add_argument("workspace", nargs="?", default=None)
    p = sub.add_parser("webbench"); p.add_argument("workspace")
    p.add_argument("--port", type=int, default=8770)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "init":
            _ws(args.workspace)
            print(f"workspace ready: {args.workspace}")
        elif args.cmd == "new":
            ws = _ws(args.workspace)
            desc = desc_mod.load(args.description)
            job = Job.create(ws / "jobs", desc)
            print(f"job created: {job.dir.name} (state: {job.state})")
            print(desc_mod.summarize(desc))
        elif args.cmd == "show":
            job = _job(_ws(args.workspace), args.job)
            print(desc_mod.summarize(job.load_description()))
            print(f"\nstate: {job.state}")
        elif args.cmd == "approve":
            job = _job(_ws(args.workspace), args.job)
            job.approve()
            print(f"approved: {job.dir.name}")
        elif args.cmd == "retry":
            job = _job(_ws(args.workspace), args.job)
            job.retry()
            print(f"retried: {job.dir.name} is approved again — build when ready")
        elif args.cmd == "delete":
            job = _job(_ws(args.workspace), args.job)
            name = job.dir.name
            job.delete()
            print(f"deleted job {name}")
        elif args.cmd == "rename":
            job = _job(_ws(args.workspace), args.job)
            renamed = job.rename(args.name)
            print(f"renamed to {renamed.dir.name}")
        elif args.cmd == "duplicate":
            job = _job(_ws(args.workspace), args.job)
            copy = job.duplicate()
            print(f"duplicated as {copy.dir.name} — waiting at the plan gate")
        elif args.cmd == "recover":
            from .jobs import recover_stale_jobs
            ws = _ws(args.workspace)
            recovered = recover_stale_jobs(ws)
            if recovered:
                for name in recovered:
                    print(f"recovered: {name} is approved again")
            else:
                print("no stranded jobs — nothing to recover")
        elif args.cmd == "build":
            ws = _ws(args.workspace)
            job = _job(ws, args.job)
            report = build_job(job, ws)
            (job.dir / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 0 if report.get("ok") else 1
        elif args.cmd == "file":
            ws = _ws(args.workspace)
            job = _job(ws, args.job)
            if job.state != "awaiting_review":
                print(f"cannot file from state {job.state!r} (needs awaiting_review)", file=sys.stderr)
                return 1
            build_report_path = job.dir / "build-report.json"
            report_data = json.loads(build_report_path.read_text()) if build_report_path.exists() else {"outputs": sorted(p.name for p in job.output_dir.iterdir()), "recipes": []}
            thumb = next((o for o in report_data.get("outputs", []) if o.endswith(".png")), None)
            if thumb is None:
                thumb = next((o for o in report_data.get("outputs", []) if o.endswith(".svg")), None)
            dest = Library(ws / "library").file_job(job, report_data, thumbnail=thumb)
            print(f"filed: {dest}")
            from .graduate import graduate_recipe
            recipe_path = graduate_recipe(ws, job, report_data)
            print(f"recipe graduated: {recipe_path}")
        elif args.cmd == "search":
            ws = _ws(args.workspace)
            hits = Library(ws / "library").search(args.query, kind=args.kind)
            if not hits:
                print("no library hits")
            for hit in hits:
                print(f"{hit['slug']}  [{hit['kind']}]  {hit['name']}  files: {', '.join(hit['files'])}")
        elif args.cmd == "demo":
            ws = _ws(args.workspace)
            desc = desc_mod.load(EXAMPLE)
            job = Job.create(ws / "jobs", desc)
            print(desc_mod.summarize(desc))
            job.approve()
            report = build_job(job, ws)
            (job.dir / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            if not report.get("ok"):
                return 1
            thumb = next((o for o in report["outputs"] if o.endswith(".png")), None)
            if thumb is None:
                thumb = next((o for o in report["outputs"] if o.endswith(".svg")), None)
            dest = Library(ws / "library").file_job(job, report, thumbnail=thumb)
            print(f"demo filed in library: {dest}")
        elif args.cmd == "plan":
            from . import live
            from .seats import load_seats
            ws = _ws(args.workspace)
            seats_path = ws / "seats.json"
            seats = load_seats(seats_path if seats_path.exists() else None)
            try:
                desc = live.plan_from_words(" ".join(args.words), seats, kind=args.kind)
            except (live.SeatNotConfigured, live.SeatCallFailed) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            job = Job.create(ws / "jobs", desc)
            print(f"job created: {job.dir.name} (state: {job.state})")
            print(desc_mod.summarize(desc))
            print("\nDraft only — approve it with the approve command before anything builds.")
        elif args.cmd == "critique":
            from .critic import GeminiCritic
            from .seats import load_seats
            ws = _ws(args.workspace)
            job = _job(ws, args.job)
            seats_path = ws / "seats.json"
            seats = load_seats(seats_path if seats_path.exists() else None)
            previews = [str(p) for p in sorted(job.output_dir.glob("*.png"))]
            critic = GeminiCritic(seats.get("critic", {}))
            desc = job.load_description()
            reference_png = None
            from . import settings as settings_mod
            if settings_mod.critic_reference_enabled(ws, desc):
                ref_path = settings_mod.reference_image_path(desc)
                if ref_path is not None:
                    reference_png = ref_path.read_bytes()
            try:
                result = critic.critique(desc, previews, reference_png=reference_png)
            except Exception as exc:  # seat not configured / call failed — say so plainly
                print(f"vision critique unavailable: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(result, indent=2))
        elif args.cmd == "ingest":
            from .ingest import ingest
            try:
                desc = ingest(args.file)
            except (ValueError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            desc_mod.dump(desc, args.out)
            print(f"description written: {args.out}")
            print(desc_mod.summarize(desc))
        elif args.cmd == "intake":
            from .intake import ROUTES, intake_photo
            ws = _ws(args.workspace)
            try:
                desc, dest_dir = intake_photo(ws, args.photo)
            except FileNotFoundError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            job = Job.create(ws / "jobs", desc)
            print(f"photo filed: {dest_dir}")
            print(f"job created: {job.dir.name} (state: {job.state})")
            print(ROUTES)
        elif args.cmd == "cloud-mesh":
            from . import cloudmesh
            ws = _ws(args.workspace)
            # The disclosure goes first, before anything is sent.
            print(cloudmesh.DISCLOSURE)
            print("A mesh takes a few minutes on the free queue — stay put.")
            try:
                result = cloudmesh.cloud_mesh(
                    ws, args.photo, job=args.job, seed=args.seed,
                    progress=lambda stage, detail: print(f"  {stage}: {detail}"))
            except cloudmesh.CloudMeshError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"mesh filed: {result['glb']}")
            print(f"job: {result['job']} (state: {result['state']}, "
                  f"{result['parts']} parts measured)")
            print(f"seed: {result['seed']}  space: {result['space']}")
            print("\nThe job is waiting at the plan gate — approve it with "
                  "the approve command before anything builds.")
        elif args.cmd == "recipes":
            from .graduate import list_recipes
            ws = _ws(args.workspace)
            rows = list_recipes(ws)
            if not rows:
                print("no graduated recipes yet — file an approved job and one graduates from it")
            for row in rows:
                print(f"{row['file']}  [{row['kind']}]  {row['name']}  (from job {row['graduated_from']})")
        elif args.cmd == "apply-recipe":
            from .graduate import apply_recipe
            ws = _ws(args.workspace)
            try:
                desc = apply_recipe(ws, args.recipe)
            except (ValueError, desc_mod.DescriptionError, FileNotFoundError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            out = args.out or str(ws / f"{desc['name'].lower().replace(' ', '-')}.description.json")
            desc_mod.dump(desc, out)
            print(f"description written: {out}")
            print(desc_mod.summarize(desc))
        elif args.cmd == "unreal-package":
            from .unreal import package_unreal
            ws = _ws(args.workspace)
            job = _job(ws, args.job)
            dest_root = Path(args.dest) if args.dest else job.dir
            try:
                dest = package_unreal(job, dest_root)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"unreal package: {dest}")
        elif args.cmd == "images":
            from . import imagegen
            ws = _ws(args.workspace)
            prompt = " ".join(args.prompt)
            try:
                batch, bdir = imagegen.new_batch(ws, prompt, job=args.job,
                                                 count=args.count, size=args.size)
                batch = imagegen.generate(ws, bdir)
            except imagegen.ImageGenError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            except Exception as exc:  # governor lock etc. — say it plainly
                print(f"image generation stopped: {exc}", file=sys.stderr)
                return 1
            landed = [c for c in batch["candidates"] if c["status"] == "pending"]
            failed = [c for c in batch["candidates"] if c["status"] == "failed"]
            print(f"batch: {bdir}")
            for c in batch["candidates"]:
                print(f"  {c['file']}  seed {c['seed']}  {c['status']}")
            if failed:
                print(f"{len(failed)} candidate(s) failed — see batch.json for the engine's words")
            if landed:
                print(f"\nPick the keeper:  python3 -m skullpt pick {bdir}")
                print(f"Or from the terminal:  python3 -m skullpt decide {bdir} cand-01.png keep")
        elif args.cmd == "pick":
            from .picker import serve
            serve(args.batch_dir, port=args.port)
        elif args.cmd == "decide":
            from . import imagegen
            bdir = Path(args.batch_dir)
            ws = bdir.parent.parent if bdir.parent.name == "images" else bdir.parent
            try:
                batch = imagegen.decide(ws, bdir, args.file, args.decision)
            except imagegen.ImageGenError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            record = next(c for c in batch["candidates"] if c["file"] == args.file)
            print(f"{args.file}: {record['status']}")
            if batch.get("reference_file"):
                print(f"job reference: {batch['reference_file']}")
        elif args.cmd == "reroll":
            from . import imagegen
            bdir = Path(args.batch_dir)
            ws = bdir.parent.parent if bdir.parent.name == "images" else bdir.parent
            try:
                batch = imagegen.generate(ws, bdir, count=args.count)
            except imagegen.ImageGenError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"image generation stopped: {exc}", file=sys.stderr)
                return 1
            for c in batch["candidates"]:
                print(f"  {c['file']}  seed {c['seed']}  {c['status']}")
        elif args.cmd == "vary":
            from . import imagegen
            bdir = Path(args.batch_dir)
            ws = bdir.parent.parent if bdir.parent.name == "images" else bdir.parent
            prompt = " ".join(args.prompt) if args.prompt else None
            try:
                batch = imagegen.vary(ws, bdir, args.file, prompt=prompt,
                                      strength=args.strength, count=args.count)
            except imagegen.ImageGenError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"image generation stopped: {exc}", file=sys.stderr)
                return 1
            print(f"variation batch: {imagegen.batch_path(ws, batch['slug'])}")
            for c in batch["candidates"]:
                print(f"  {c['file']}  seed {c['seed']}  {c['status']}")
        elif args.cmd == "settings":
            from . import settings as settings_mod
            ws = _ws(args.workspace)
            current = settings_mod.load_settings(ws)
            if args.critic_reference is not None:
                current["critic_reference_check"] = args.critic_reference == "on"
                settings_mod.save_settings(ws, current)
                print(f"critic_reference_check = {current['critic_reference_check']} (saved)")
            render_updates = {}
            if args.render_engine is not None:
                render_updates["engine"] = args.render_engine
            if args.render_resolution is not None:
                render_updates["resolution_px"] = args.render_resolution
            if args.render_samples is not None:
                render_updates["samples"] = args.render_samples
            if args.render_denoise is not None:
                render_updates["denoise"] = args.render_denoise == "on"
            if render_updates:
                current = settings_mod.save_render_settings(ws, render_updates)
                print("render defaults saved: "
                      f"engine={current['render_engine']}, "
                      f"resolution={current['render_resolution_px']}px, "
                      f"samples={current['render_samples']}, "
                      f"denoise={'on' if current['render_denoise'] else 'off'}")
            print(f"critic reference comparison: "
                  f"{'on' if current.get('critic_reference_check', True) else 'off'}"
                  f"  (a job can override this with \"critic_reference\": true/false "
                  f"in its description)")
            print(f"render defaults: engine={current.get('render_engine', 'auto')}, "
                  f"resolution={current.get('render_resolution_px', 640)}px, "
                  f"samples={current.get('render_samples', 24)}, "
                  f"denoise={'on' if current.get('render_denoise') else 'off'}"
                  f"  (a job can override these in its render settings)")
            last = settings_mod.last_render_engine(ws)
            if last.get("engine"):
                print(f"last render engine that worked here: {last['engine']}"
                      + (f" ({last['at']})" if last.get("at") else ""))
        elif args.cmd == "backup":
            from .backup import backup_workspace
            ws = _ws(args.workspace)
            try:
                result = backup_workspace(ws, args.dest)
            except (OSError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"backup: {result['path']} ({result['files']} files, "
                  f"{result['bytes']} bytes)")
            print(result["note"])
        elif args.cmd == "update-check":
            from . import __version__, update as update_mod
            result = update_mod.check_for_update(__version__)
            if result["error"]:
                print(result["error"], file=sys.stderr)
                return 1
            if result["update_available"]:
                print(f"a newer SKULLPT is out: v{result['latest']} "
                      f"(you are on v{result['current']})")
                print(f"get it at: {result['url']}")
            else:
                print(f"you are on the latest release (v{result['current']})")
        elif args.cmd == "bench":
            from .qtbench import default_workspace, main as qt_main
            ws = _ws(args.workspace) if args.workspace else _ws(default_workspace())
            return qt_main(ws)
        elif args.cmd == "webbench":
            from .bench import serve
            ws = _ws(args.workspace)
            serve(ws, port=args.port)
        elif args.cmd == "view":
            from .viewer import serve
            serve(args.description, port=args.port)
        elif args.cmd == "snapshot":
            from .viewer import render_page
            desc = desc_mod.load(args.description)
            Path(args.out).write_text(render_page(desc, live=False), encoding="utf-8")
            print(f"snapshot written: {args.out}")
        return 0
    except (GateError, desc_mod.DescriptionError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
