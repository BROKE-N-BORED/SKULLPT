"""v1.9 regression tests: the audit fixes (N1-N21 + N51 + N55).

One class per item (or per small group), named for the item, so a
failure says which fix regressed. The Qt window tests skip cleanly
without PySide6; run them under xvfb-run with the qt-libs on the
path. The staged installer tests run the real install.sh against
stub commands, like test_v13/test_v14.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from skullpt import bench as bench_mod
from skullpt import builder as builder_mod
from skullpt import cloudmesh
from skullpt import description as desc_mod
from skullpt import exporters
from skullpt import graduate
from skullpt import imagegen
from skullpt import live as live_mod
from skullpt import qtbench
from skullpt import subproc
from skullpt import tokenfile
from skullpt.blender_export import (
    _BlenderStream, _build_outputs, _render_outputs, _resolve_mesh_file,
    export_blender_scene, generate_script)
from skullpt.governor import Cancelled
from skullpt.jobs import Job

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
INSTALL_SH = (ROOT / "install.sh").read_text()
SHOP = json.loads((EXAMPLES / "shop-panel.description.json").read_text())
ROOM = json.loads((EXAMPLES / "tavern-room.description.json").read_text())

# The staged-installer stubs live in test_v13/test_v14 (test_v14
# already imports from test_v13 the same way).
from test_v13 import _StagedInstall  # noqa: E402
from test_v14 import (  # noqa: E402
    STUB_CMAKE, STUB_CURL_FAIL, STUB_CURL_OK, STUB_GIT, STUB_GXX,
    STUB_SHA256SUM)


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir(exist_ok=True)
    (ws / "library").mkdir(exist_ok=True)
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _make_png(width: int, height: int, color=(200, 30, 30)) -> bytes:
    """A real minimal PNG (same builder as test_v16)."""
    def chunk(ctype: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + ctype + payload
                + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(raw))
    out += chunk(b"IEND", b"")
    return out


class _FakeBench:
    """A bench stand-in for window tests: actions run for real."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.refreshed = 0
        self.selected: str | None = None

    def do_action(self, action: str, payload: dict):
        return bench_mod.act(self.workspace, action, payload)

    def refresh(self) -> None:
        self.refreshed += 1

    def select_job(self, name: str | None) -> None:
        self.selected = name


# --------------------------------------------------------------------- N2

class N2CloudMeshPreflightTests(unittest.TestCase):
    """N2: the cloud-mesh preflight uses the Settings token."""

    def _photo(self, tmp: str) -> Path:
        png = Path(tmp) / "photo.png"
        png.write_bytes(_make_png(400, 300))
        return png

    def test_no_token_refuses_before_any_run(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            png = self._photo(td)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HF_TOKEN", None)
                with self.assertRaises(cloudmesh.CloudMeshError) as cm:
                    bench_mod.act(ws, "cloud-mesh", {"photo": str(png)})
            self.assertIn("Settings", str(cm.exception))
            self.assertNotIn("HF_TOKEN", str(cm.exception))

    def test_settings_token_passes_the_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            png = self._photo(td)
            tokenfile.save_token(ws, "hf_testtoken123")
            self.assertEqual(
                cloudmesh.token_value(workspace=ws), "hf_testtoken123")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HF_TOKEN", None)
                with mock.patch(
                        "skullpt.cloudmesh.cloud_mesh",
                        return_value={"job": "photo-job",
                                      "parts": 3}) as meshed:
                    result = bench_mod.act(ws, "cloud-mesh",
                                           {"photo": str(png)})
                    self.assertTrue(result["ok"])
                    deadline = time.monotonic() + 10
                    run = None
                    while time.monotonic() < deadline:
                        run = next(
                            (r for r in bench_mod._runs()
                             if r["id"] == result["run"]), None)
                        if run is not None and run["status"] == "done":
                            break
                        time.sleep(0.05)
                    self.assertIsNotNone(run)
                    self.assertEqual(run["status"], "done")
                    self.assertTrue(meshed.called)


# --------------------------------------------------------------------- N3

class N3ObjImportTests(unittest.TestCase):
    """N3: .obj import uses the Blender 4.0+ operator."""

    def test_generated_script_uses_wm_obj_import(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            out_dir = job.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            (job.dir / "thing.obj").write_text("# fake obj\n")
            desc = job.load_description()
            desc["mesh"] = {"file": "thing.obj", "format": "obj"}
            script = generate_script(desc, out_dir, "shop-panel")
            self.assertIn("bpy.ops.wm.obj_import", script)
            # The legacy operator survives only as the fallback.
            self.assertIn('hasattr(bpy.ops.wm, "obj_import")', script)


# --------------------------------------------------------------------- N5

class N5ExactModelTests(unittest.TestCase):
    """N5: only the exact model file counts as present."""

    def test_guidance_only_model_home_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            model_home = ws / "models"
            model_home.mkdir()
            (model_home / "controlnet.gguf").write_bytes(b"fake")
            (model_home / "ip-adapter.gguf").write_bytes(b"fake")
            with mock.patch.dict(
                    os.environ,
                    {"DESIGNSHELL_SD_MODEL_HOME": str(model_home)}):
                os.environ.pop("DESIGNSHELL_SD_MODEL", None)
                self.assertIsNone(imagegen.find_model(ws))

    def test_exact_model_file_is_found(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            model_home = ws / "models"
            model_home.mkdir()
            exact = model_home / imagegen.MODEL_FILENAME
            exact.write_bytes(b"fake")
            with mock.patch.dict(
                    os.environ,
                    {"DESIGNSHELL_SD_MODEL_HOME": str(model_home)}):
                os.environ.pop("DESIGNSHELL_SD_MODEL", None)
                self.assertEqual(imagegen.find_model(ws), exact)

    def test_installer_find_sd_model_has_no_glob(self):
        fn = re.search(r"^find_sd_model\(\) \{.*?^\}",
                       INSTALL_SH, re.M | re.S).group(0)
        self.assertNotIn("*.gguf", fn)
        self.assertIn("SD_MODEL_FILE", fn)


# --------------------------------------------------------------------- N6

class N6DuplicateTests(unittest.TestCase):
    """N6: duplicating a job copies its job-folder files."""

    def test_duplicate_copies_mesh_and_reference(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            (job.dir / "mesh.glb").write_bytes(b"fake-glb")
            (job.dir / "ref.png").write_bytes(_make_png(64, 64))
            desc = job.load_description()
            desc["mesh"] = {"file": "mesh.glb", "format": "glb"}
            desc["reference"] = {"stored_at": str(job.dir / "ref.png")}
            desc_mod.dump(desc, job.desc_path)
            copy = job.duplicate()
            self.assertTrue((copy.dir / "mesh.glb").exists())
            self.assertTrue((copy.dir / "ref.png").exists())
            copy_desc = copy.load_description()
            self.assertEqual(copy_desc["reference"]["stored_at"],
                             str(copy.dir / "ref.png"))
            self.assertEqual(copy_desc["mesh"]["file"], "mesh.glb")

    def test_duplicate_does_not_copy_shared_files(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            shared = ws / "meshes"
            shared.mkdir()
            (shared / "shared.glb").write_bytes(b"fake-glb")
            job = _job(ws)
            desc = job.load_description()
            desc["mesh"] = {"file": str(shared / "shared.glb"),
                            "format": "glb"}
            desc_mod.dump(desc, job.desc_path)
            copy = job.duplicate()
            self.assertFalse((copy.dir / "shared.glb").exists())
            self.assertEqual(copy.load_description()["mesh"]["file"],
                             str(shared / "shared.glb"))


# --------------------------------------------------------------------- N7

class N7MeshResolutionTests(unittest.TestCase):
    """N7: mesh resolution is job folder first, never the CWD."""

    def test_cwd_shadow_does_not_win(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            out_dir = job.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            (job.dir / "mesh.glb").write_bytes(b"job-mesh")
            shadow_dir = Path(td) / "launch"
            shadow_dir.mkdir()
            (shadow_dir / "mesh.glb").write_bytes(b"shadow-mesh")
            desc = {"mesh": {"file": "mesh.glb", "format": "glb"}}
            old_cwd = os.getcwd()
            try:
                os.chdir(shadow_dir)
                path, fmt, _scale = _resolve_mesh_file(desc, out_dir)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(path, job.dir / "mesh.glb")
            self.assertEqual(path.read_bytes(), b"job-mesh")

    def test_workspace_root_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            out_dir = job.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            (ws / "mesh.glb").write_bytes(b"workspace-mesh")
            desc = {"mesh": {"file": "mesh.glb", "format": "glb"}}
            path, _fmt, _scale = _resolve_mesh_file(desc, out_dir)
            self.assertEqual(path, ws / "mesh.glb")


# ------------------------------------------------------------------ N8/N9

class N8SubprocTests(unittest.TestCase):
    """N8: cancel kills the subprocess; timeout kills it too."""

    def test_cancel_kills_a_sleeping_child_fast(self):
        cancel = threading.Event()

        def setter():
            time.sleep(0.3)
            cancel.set()

        threading.Thread(target=setter, daemon=True).start()
        start = time.monotonic()
        with self.assertRaises(Cancelled):
            subproc.run_cancellable(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                timeout=60, cancel=cancel)
        self.assertLess(time.monotonic() - start, 10)

    def test_timeout_kills_and_keeps_partial_output(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            subproc.run_cancellable(
                [sys.executable, "-u", "-c",
                 "import time; print('before'); time.sleep(60)"],
                timeout=1)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIn("before", cm.exception.output or "")

    def test_cancel_kills_the_process_group(self):
        seen: list[str] = []
        cancel = threading.Event()

        def setter():
            # Only cancel once the grandchild provably exists - the
            # child Python can take a while to start under load.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if any(ln.startswith("GRANDCHILD") for ln in seen):
                    break
                time.sleep(0.05)
            cancel.set()

        threading.Thread(target=setter, daemon=True).start()
        child_code = (
            "import subprocess, time; "
            "p = subprocess.Popen(['sleep', '60']); "
            "print('GRANDCHILD', p.pid, flush=True); "
            "time.sleep(60)")
        with self.assertRaises(Cancelled):
            subproc.run_cancellable(
                [sys.executable, "-u", "-c", child_code],
                timeout=60, cancel=cancel, on_line=seen.append)
        pid_line = [ln for ln in seen if ln.startswith("GRANDCHILD")]
        self.assertTrue(pid_line, seen)
        grandchild = int(pid_line[0].split()[1])
        time.sleep(0.3)
        try:
            os.kill(grandchild, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        state = ""
        try:
            state = Path(f"/proc/{grandchild}/stat").read_text().split()[2]
        except OSError:
            state = "gone"
        self.assertTrue(not alive or state == "Z",
                        f"grandchild survived: state={state}")


class N8CancelRunTests(unittest.TestCase):
    """N8: cancel_run marks a run cancelling, and the queue waits."""

    def _slow_fn(self, started: threading.Event):
        def fn(cancel, report):
            started.set()
            cancel.wait(timeout=10)
            time.sleep(0.3)  # stay cancelling long enough to observe
            raise Cancelled("stopped by test")

        return fn

    def _run(self, run_id: str):
        return next((r for r in bench_mod._runs() if r["id"] == run_id),
                    None)

    def _wait_status(self, run_id: str, status: str, timeout: float = 10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self._run(run_id)
            if run is not None and run["status"] == status:
                return run
            time.sleep(0.05)
        self.fail(f"run {run_id} never reached {status!r}: {self._run(run_id)}")

    def test_cancel_run_marks_cancelling_then_cancelled(self):
        started = threading.Event()
        run_id = bench_mod._start_run("build", "v19-cancel",
                                      self._slow_fn(started))
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(bench_mod.cancel_run(run_id))
        self.assertEqual(self._run(run_id)["status"], "cancelling")
        # Cancelling again is a no-op that still returns True.
        self.assertTrue(bench_mod.cancel_run(run_id))
        self._wait_status(run_id, "cancelled")

    def test_queue_waits_for_a_cancelling_run(self):
        queue = "v19-test-queue"
        started = threading.Event()
        run_a = bench_mod._start_run("build", "v19-a",
                                     self._slow_fn(started), queue=queue)
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(bench_mod.cancel_run(run_a))
        run_b = bench_mod._start_run(
            "build", "v19-b", lambda c, r: "b done", queue=queue)
        self.assertEqual(self._run(run_b)["status"], "queued")
        self._wait_status(run_a, "cancelled")
        self._wait_status(run_b, "done")


class N9StreamTests(unittest.TestCase):
    """N9: Blender's output streams into progress with Saved: counting."""

    def test_saved_lines_advance_the_fraction(self):
        seen: list[tuple] = []
        stream = _BlenderStream(
            lambda s, d, f: seen.append((s, d, f)), expected_renders=2)
        stream.build_line("Blender 4.5.9")
        self.assertEqual(seen[-1][2], 0.1)
        stream.render_line("Saved: 'render-a.png'")
        self.assertAlmostEqual(seen[-1][2], 0.625)
        stream.render_line("Saved: 'render-b.png'")
        self.assertAlmostEqual(seen[-1][2], 1.0)
        # A fallback re-run never rewinds the bar.
        stream.render_line("Saved: 'render-a.png'")
        self.assertAlmostEqual(seen[-1][2], 1.0)

    def test_noisy_lines_are_throttled(self):
        seen: list[tuple] = []
        stream = _BlenderStream(
            lambda s, d, f: seen.append((s, d, f)), expected_renders=2)
        stream.build_line("first line")
        before = len(seen)
        for i in range(10):
            stream.render_line(f"Fra:{i} Mem:10.00M")
        self.assertEqual(len(seen), before)

    def test_fake_blender_streams_and_lands(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            out_dir = job.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            desc = job.load_description()
            desc["render"] = {"engine": "cycles"}
            slug = job.dir.name
            expected = sorted(_build_outputs(desc, slug)
                              + _render_outputs(desc, slug))
            stub = Path(td) / "fake-blender"
            stub.write_text(
                "#!/bin/sh\n"
                "echo \"Blender 4.5.9 (fake)\"\n"
                "echo \"Fra:1 Mem:10.00M\"\n"
                "echo \"Saved: 'render-front.png'\"\n"
                "echo \"Saved: 'render-iso-auto.png'\"\n"
                "for f in $V19_OUTPUTS; do printf 'x' > \"$V19_OUT_DIR/$f\"; done\n"
                "exit 0\n")
            stub.chmod(0o755)
            seen: list[tuple] = []
            env = dict(os.environ)
            env["DESIGNSHELL_BLENDER"] = str(stub)
            env["V19_OUTPUTS"] = " ".join(expected)
            env["V19_OUT_DIR"] = str(out_dir)
            with mock.patch.dict(os.environ, env, clear=True):
                outputs = export_blender_scene(
                    desc, out_dir,
                    progress=lambda s, d, f: seen.append((s, d, f)))
            for name in expected:
                self.assertIn(name, outputs)
            self.assertTrue(any("Saved:" in d for _s, d, _f in seen),
                            seen)
            self.assertGreaterEqual(max(f for _s, _d, f in seen), 0.9)


# ------------------------------------------------------------ N10/N13/N14

class N10WebRemovalTests(unittest.TestCase):
    """N10: the legacy web UI is gone; the picker stays for the bridge."""

    def test_web_commands_are_gone_from_the_cli(self):
        cli_text = (ROOT / "skullpt" / "cli.py").read_text()
        for dead in ("webbench", "render_page", "snapshot"):
            self.assertNotIn(dead, cli_text)
        help_text = subprocess.run(
            [sys.executable, "-m", "skullpt", "--help"],
            cwd=ROOT, capture_output=True, text=True).stdout
        for dead in ("webbench", "snapshot"):
            self.assertNotIn(dead, help_text)

    def test_viewer_has_geometry_not_a_page(self):
        viewer_text = (ROOT / "skullpt" / "viewer.py").read_text()
        self.assertNotIn("render_page", viewer_text)
        self.assertNotIn("Flask", viewer_text)
        for kept in ("part_box", "scene_bounds", "scene_data",
                     "upsert_camera", "delete_camera"):
            self.assertIn(kept, viewer_text)

    def test_picker_kept_for_the_code_forge_bridge(self):
        self.assertTrue((ROOT / "skullpt" / "picker.py").exists())
        help_text = subprocess.run(
            [sys.executable, "-m", "skullpt", "--help"],
            cwd=ROOT, capture_output=True, text=True).stdout
        self.assertIn("pick", help_text)


class N13OpenAssetFilterTests(unittest.TestCase):
    """N13: the open-asset filter lists .obj/.stl/.fbx."""

    def test_filter_line(self):
        qtbench_text = (ROOT / "skullpt" / "qtbench.py").read_text()
        self.assertIn('"Assets (*.glb *.blend *.obj *.stl *.fbx)"',
                      qtbench_text)


class N14PlannerTests(unittest.TestCase):
    """N14: the planner knows character vocabulary and refuses projects."""

    def test_format_spec_covers_characters(self):
        spec = live_mod.FORMAT_SPEC
        for word in ("gender", "age", "skin", "game_engine",
                     "race_asian", "race_caucasian", "race_african"):
            self.assertIn(word, spec)

    def test_plan_project_refuses_plainly(self):
        with self.assertRaises(ValueError) as cm:
            live_mod.plan_from_words("make a game", {}, kind="project")
        self.assertIn("no builder", str(cm.exception))

    def test_plan_unknown_kind_refuses_plainly(self):
        with self.assertRaises(ValueError) as cm:
            live_mod.plan_from_words("make a thing", {}, kind="nope")
        self.assertIn("unknown kind", str(cm.exception))


class N15VisionNoteTests(unittest.TestCase):
    """N15: character builds say plainly the patch loop is scene-only."""

    def test_character_critique_note_says_scenes_only(self):
        class FakeCritic:
            def critique(self, context, previews=None, reference_png=None,
                         cancel=None):
                return {"vision": True, "score": 3,
                        "note": "the pose looks stiff",
                        "fixes": [{"part": "torso", "move": [0, 0, 0.1]}]}

        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            desc = {
                "name": "v19 hero", "kind": "character", "parts": [],
                "character": {"skin": "young_caucasian_male.mhmat",
                              "rig": "game_engine"},
                "summary": "a hero", "budget": {"meshes": 1},
                "rubric": ["the character reads as a hero"],
                "style": {"surface": "#8a7a5a"},
                "version": 1}
            job = Job.create(ws / "jobs", desc)
            with mock.patch.object(builder_mod, "get_critic",
                                   return_value=FakeCritic()):
                critique, rounds = builder_mod._run_critic(
                    job, desc, ["hero.glb"], {}, None)
            self.assertIn("scenes only", critique["note"])
            self.assertEqual(rounds, [])


# --------------------------------------------------------- N16/N17/N18

class N16VersionTests(unittest.TestCase):
    """N16: version strings come from __version__."""

    def test_version_is_1_10(self):
        from skullpt import __version__
        self.assertEqual(__version__, "1.10")

    def test_about_and_help_read_version(self):
        qtbench_text = (ROOT / "skullpt" / "qtbench.py").read_text()
        self.assertIn("__version__", qtbench_text)
        qtwindows_text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        self.assertIn("__SKULLPT_VERSION__", qtwindows_text)
        self.assertIn("__version__", qtwindows_text)


class N17BlenderVersionTests(unittest.TestCase):
    """N17: Blender is pinned at 4.5.9; Help says 4.5.9 or newer."""

    def test_installer_pin_and_help_line(self):
        self.assertIn('BLENDER_VERSION="4.5.9"', INSTALL_SH)
        help_text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        self.assertIn("Blender 4.5.9 or newer", help_text)


class N18ScrubTests(unittest.TestCase):
    """N18: the debug report scrubs Groq and Gemini keys too."""

    def test_report_scrubs_gsk_and_aiza(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            groq = "gsk_" + "g" * 20
            gemini = "AIza" + "g" * 20
            hf = "hf_" + "h" * 20
            with open(job.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"groq key {groq}\n")
                fh.write(f"gemini key {gemini}\n")
                fh.write(f"hf key {hf}\n")
            report = bench_mod.debug_report(ws, job.dir.name)
            for key in (groq, gemini, hf):
                self.assertNotIn(key, report)
            self.assertIn("[redacted]", report)


# ------------------------------------------------------------- N20/N21

class N20NoEmDashTests(unittest.TestCase):
    """N20: no em dashes in visible UI text."""

    def test_no_em_dash_in_python_files(self):
        for path in sorted((ROOT / "skullpt").glob("*.py")):
            self.assertNotIn("\u2014", path.read_text(), path.name)

    def test_no_em_dash_in_installer_ui(self):
        for line in INSTALL_SH.splitlines():
            if line.lstrip().startswith("#"):
                continue
            self.assertNotIn("\u2014", line, line)
        desktop = (ROOT / "Install-SKULLPT.desktop").read_text()
        self.assertNotIn("\u2014", desktop)

    def test_no_em_dash_in_runtime_strings(self):
        self.assertNotIn("\u2014", bench_mod._CHAT_HELP)
        from skullpt import intake
        self.assertNotIn("\u2014", intake.ROUTES)
        from skullpt import cli
        self.assertNotIn("\u2014", cli.__doc__)
        help_text = subprocess.run(
            [sys.executable, "-m", "skullpt", "--help"],
            cwd=ROOT, capture_output=True, text=True).stdout
        self.assertNotIn("\u2014", help_text)
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            self.assertNotIn("\u2014",
                             bench_mod.act(ws, "approve",
                                           {"job": job.dir.name})["detail"])
            self.assertNotIn("\u2014",
                             bench_mod.act(ws, "duplicate",
                                           {"job": job.dir.name})["detail"])
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                reply = bench_mod.chat_reply(ws, "hello")["reply"]
            self.assertNotIn("\u2014", reply)


class N21NoEnvFramingTests(unittest.TestCase):
    """N21: the UI points at Settings, not at environment variables."""

    def test_user_facing_strings_point_at_settings(self):
        self.assertNotIn("HF_TOKEN", bench_mod._CHAT_HELP)
        self.assertIn("Settings", bench_mod._CHAT_HELP)
        from skullpt import intake
        self.assertNotIn("HF_TOKEN", intake.ROUTES)
        self.assertIn("Settings", intake.ROUTES)
        from skullpt import cli
        self.assertNotIn("HF_TOKEN", cli.__doc__)
        self.assertNotIn("GEMINI_API_KEY", cli.__doc__)
        self.assertNotIn("HF_TOKEN", cloudmesh.TOKEN_FIX)
        self.assertIn("Settings", cloudmesh.TOKEN_FIX)
        help_text = subprocess.run(
            [sys.executable, "-m", "skullpt", "--help"],
            cwd=ROOT, capture_output=True, text=True).stdout
        self.assertNotIn("HF_TOKEN", help_text)
        self.assertNotIn("GEMINI_API_KEY", help_text)

    def test_no_key_chat_reply_points_at_settings(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                reply = bench_mod.chat_reply(ws, "hello")["reply"]
            self.assertNotIn("GEMINI_API_KEY", reply)
            self.assertIn("Settings", reply)

    def test_planner_ready_is_gone(self):
        self.assertFalse(hasattr(bench_mod, "_planner_ready"))


# ------------------------------------------------------------------- N51

class N51LicenseTests(unittest.TestCase):
    """N51: the repo ships an MIT license."""

    def test_license_file(self):
        lic = ROOT / "LICENSE"
        self.assertTrue(lic.exists())
        text = lic.read_text()
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 Broke n' Bored Studio", text)


# ------------------------------------------------------------ N1/N4/N19

@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class N1ImagesWindowTests(unittest.TestCase):
    """N1: the Images window reads batch slugs, not a missing 'name'."""

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def test_opening_with_batches_lists_slugs(self):
        from skullpt import qtwindows
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            batch, batch_dir = imagegen.new_batch(ws, "a red chair")
            (batch_dir / "candidate-a.png").write_bytes(
                _make_png(64, 64))
            (batch_dir / "candidate-b.png").write_bytes(
                _make_png(64, 64))
            record = json.loads((batch_dir / "batch.json").read_text())
            record["candidates"] = [
                {"file": "candidate-a.png", "status": "kept"},
                {"file": "candidate-b.png", "status": "pending"}]
            (batch_dir / "batch.json").write_text(
                json.dumps(record, indent=2) + "\n")
            fake = _FakeBench(ws)
            window = qtwindows.ImagesWindow(fake, ws)
            try:
                items = [window._batch_list.item(i).text()
                         for i in range(window._batch_list.count())]
            finally:
                window.close()
            self.assertIn(f"{batch['slug']} (1 kept, 1 waiting)", items)


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class N4PartEditorTests(unittest.TestCase):
    """N4: the part editor leaves hex/unknown materials alone."""

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def _job_with_hex_material(self, ws: Path) -> Job:
        desc = json.loads(json.dumps(ROOM))
        desc["name"] = "hex job"
        part = desc["parts"][1]  # the counter
        part["material"] = "#ff0000"
        part.pop("bevel_cm", None)
        part.pop("radius_cm", None)
        return Job.create(ws / "jobs", desc)

    def test_hex_material_is_shown_not_forced(self):
        from skullpt import qtwindows
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._job_with_hex_material(ws)
            fake = _FakeBench(ws)
            window = qtwindows.RenderWindow(fake, ws)
            try:
                window.set_job(job.dir.name)
                window._fill_editor("counter")
                self.assertEqual(window._material_combo.currentIndex(), -1)
                self.assertIn("custom", window._editor_note.text())
            finally:
                window.close()

    def test_saving_keeps_the_hex_material_and_no_bevel(self):
        from skullpt import qtwindows
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._job_with_hex_material(ws)
            fake = _FakeBench(ws)
            window = qtwindows.RenderWindow(fake, ws)
            try:
                window.set_job(job.dir.name)
                window._fill_editor("counter")
                window._save_part()
            finally:
                window.close()
            part = job.load_description()["parts"][1]
            self.assertEqual(part["material"], "#ff0000")
            self.assertNotIn("bevel_cm", part)
            self.assertNotIn("radius_cm", part)


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class N19RecipesWindowTests(unittest.TestCase):
    """N19: the Bench has a Recipes window that starts jobs."""

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def test_recipe_starts_a_job(self):
        from skullpt import qtwindows
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            graduate.graduate_recipe(ws, job, {"outputs": []})
            self.assertEqual(len(graduate.list_recipes(ws)), 1)
            fake = _FakeBench(ws)
            window = qtwindows.RecipesWindow(fake, ws)
            try:
                items = [window._list.item(i).text()
                         for i in range(window._list.count())]
                self.assertIn(f"{SHOP['name']} recipe  [ui_component]",
                              items)
                # A click is what selects a recipe (itemClicked, like
                # the other list windows); setCurrentRow alone does not.
                window._on_recipe(window._list.item(0))
                window._start_job()
            finally:
                window.close()
            self.assertGreater(fake.refreshed, 0)
            self.assertIsNotNone(fake.selected)
            self.assertEqual(Job(ws / "jobs" / fake.selected).state,
                             "awaiting_approval")


# ------------------------------------------------------- N11/N12/N55

class N11N12N55InstallerStaticTests(unittest.TestCase):
    """N11/N12/N55: the installer fails loud, pins sd.cpp, traps aborts."""

    def test_abort_trap_is_installed(self):
        self.assertIn("trap _worker_abort_trap EXIT", INSTALL_SH)

    def test_sd_cpp_is_pinned_and_never_cloned(self):
        match = re.search(r'SD_COMMIT="([0-9a-f]{40})"', INSTALL_SH)
        self.assertIsNotNone(match, "SD_COMMIT is not a full 40-hex SHA")
        self.assertIn('fetch --depth 1 origin "$SD_COMMIT"', INSTALL_SH)
        self.assertNotIn("git clone", INSTALL_SH)

    def test_fatal_failures_are_python_and_blender_only(self):
        calls = [line for line in INSTALL_SH.splitlines()
                 if "gui_fail " in line
                 and not line.lstrip().startswith("#")]
        # 5 in v1.9; v1.10 (F48) adds 2 Blender ones - the new tree
        # failing to run, and the swap failing after the old tree
        # was moved aside.
        self.assertEqual(len(calls), 7, calls)
        for line in calls:
            self.assertTrue("Python" in line or "Blender" in line, line)

    def test_warnings_only_accumulate_through_add_warning(self):
        lines = INSTALL_SH.splitlines()
        add_start = next(i for i, ln in enumerate(lines)
                         if ln.startswith("add_warning()"))
        add_end = next(i for i, ln in enumerate(lines)
                       if i > add_start and ln == "}")
        for i, line in enumerate(lines):
            if "WARNING=" in line:
                self.assertTrue(
                    line.strip() == 'WARNING=""'
                    or add_start <= i <= add_end,
                    f"direct WARNING assignment at line {i + 1}: {line}")


STUB_CMAKE_FAIL_BUILD = """\
#!/bin/sh
echo "cmake $*" >> "$CMAKE_LOG"
case "$*" in
  *--build*) exit 1 ;;
esac
exit 0
"""


class N11N55StagedInstallerTests(_StagedInstall):
    """N11/N55: optional components degrade to a warning, never a fail.

    Built on test_v13's GUI staging (stub dialogs, stub Blender,
    seeded MPFB + model) with one stub swapped per test.
    """

    def _write_stub(self, bin_dir: Path, name: str, text: str) -> None:
        stub = bin_dir / name
        stub.write_text(text)
        stub.chmod(0o755)

    def _warning_dialogs(self, paths):
        return [c for c in self._dialog_calls(paths)
                if "--warning" in c]

    def test_failed_engine_build_warns_and_completes(self):
        with tempfile.TemporaryDirectory() as td:
            stage, home, env, paths = self._stage(td)
            bin_dir = Path(env["STUB_DIR"])
            (bin_dir / "sd-cli").unlink()  # force the engine build
            self._write_stub(bin_dir, "git", STUB_GIT)
            self._write_stub(bin_dir, "cmake", STUB_CMAKE_FAIL_BUILD)
            self._write_stub(bin_dir, "g++", STUB_GXX)
            env["CMAKE_LOG"] = str(Path(td) / "cmake.log")
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("--build",
                          Path(env["CMAKE_LOG"]).read_text())
            warnings = self._warning_dialogs(paths)
            self.assertTrue(any("image engine" in w for w in warnings),
                            f"no image-engine warning in {warnings}")
            self.assertFalse(any("--error" in c
                                 for c in self._dialog_calls(paths)))
            self.assertIn("image engine",
                          paths["install_log"].read_text())
            self._assert_installed(paths)

    def test_failed_model_download_warns_and_completes(self):
        with tempfile.TemporaryDirectory() as td:
            stage, home, env, paths = self._stage(td)
            bin_dir = Path(env["STUB_DIR"])
            self._write_stub(bin_dir, "curl", STUB_CURL_FAIL)
            env.pop("DESIGNSHELL_SD_MODEL", None)
            (home / "design-shell-models" / "other-model.gguf").unlink()
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            warnings = self._warning_dialogs(paths)
            self.assertTrue(any("image model" in w for w in warnings),
                            f"no image-model warning in {warnings}")
            self.assertFalse(any("--error" in c
                                 for c in self._dialog_calls(paths)))
            self._assert_installed(paths)

    def test_failed_mpfb_download_warns_and_completes(self):
        with tempfile.TemporaryDirectory() as td:
            stage, home, env, paths = self._stage(td)
            bin_dir = Path(env["STUB_DIR"])
            self._write_stub(bin_dir, "curl", STUB_CURL_FAIL)
            mpfb = (home / ".config" / "blender" / "4.5" / "extensions"
                    / "user_default" / "mpfb")
            shutil.rmtree(mpfb)
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            warnings = self._warning_dialogs(paths)
            self.assertTrue(any("MPFB" in w for w in warnings),
                            f"no MPFB warning in {warnings}")
            self.assertFalse(any("--error" in c
                                 for c in self._dialog_calls(paths)))
            self._assert_installed(paths)

    def test_guidance_only_model_home_still_downloads(self):
        with tempfile.TemporaryDirectory() as td:
            stage, home, env, paths = self._stage(td)
            bin_dir = Path(env["STUB_DIR"])
            model_home = home / "design-shell-models"
            (model_home / "other-model.gguf").unlink()
            (model_home / "controlnet.gguf").write_bytes(b"fake")
            env.pop("DESIGNSHELL_SD_MODEL", None)
            self._write_stub(bin_dir, "curl", STUB_CURL_OK)
            self._write_stub(bin_dir, "sha256sum", STUB_SHA256SUM)
            env["CURL_LOG"] = str(Path(td) / "curl.log")
            env["STUB_SHA256"] = re.search(
                r'SD_MODEL_SHA256="([0-9a-f]{64})"', INSTALL_SH).group(1)
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("LCM_Dreamshaper",
                          Path(env["CURL_LOG"]).read_text())
            self.assertFalse(any("--error" in c
                                 for c in self._dialog_calls(paths)))
            self._assert_installed(paths)

    def test_abort_trap_names_the_step(self):
        with tempfile.TemporaryDirectory() as td:
            stage, home, env, paths = self._stage(td)
            script = stage / "install.sh"
            text = script.read_text()
            anchor = '  step 5 "Checking Python"'
            self.assertIn(anchor, text)
            script.write_text(text.replace(
                anchor,
                anchor + '\n  echo "$V19_TRAP_PROBE_UNSET_VAR"',
                1))
            res = self._run_install(stage, env, "--gui")
            self.assertNotEqual(res.returncode, 0)
            log = paths["install_log"].read_text()
            self.assertIn("INSTALL-FAILED:", log)
            self.assertIn("Checking Python", log)
            calls = self._dialog_calls(paths)
            self.assertTrue(any("--error" in c for c in calls), calls)


if __name__ == "__main__":
    unittest.main()
