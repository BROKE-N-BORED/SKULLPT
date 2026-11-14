"""SKULLPT v1.4 regression tests: the P0 defects (T1-T7), the five
small weakness fixes, and installer perf (P5).

Covers: governor refusal no longer strands jobs + retry (T1), the
fixed open-conversation path (T2), zero-exporter builds failing loudly
(T3), the unified max_tris budget with shape-based estimates (T4),
description snapshots (T5), resumable + verified installer downloads
with no Blender downgrade (T6), act()'s "job" field (T7), the skull
exclusion, chat persistence, PNG-first thumbnails, never-silent skips,
the atomic governor lock + mid-run RAM re-check, and the -j2 build cap
(P5).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import builder as builder_mod  # noqa: E402
from skullpt import checks as checks_mod  # noqa: E402
from skullpt import cli as cli_mod  # noqa: E402
from skullpt import cloudmesh  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import exporters  # noqa: E402
from skullpt import governor  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt.builder import build_job  # noqa: E402
from skullpt.jobs import GateError, Job  # noqa: E402
from test_cloudmesh import MockGradioCase  # noqa: E402
from test_v13 import FAKE_VENV_PYTHON, STUB_PKEXEC, STUB_PYTHON3  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())
ROOM = json.loads((ROOT / "examples" / "tavern-room.description.json").read_text())
STALL = json.loads((ROOT / "examples" / "craft-stall.description.json").read_text())
INSTALL_SH = (ROOT / "install.sh").read_text()


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _approved_job(ws: Path, desc: dict | None = None) -> Job:
    job = _job(ws, desc)
    job.approve()
    return job


def _high_ram():
    """Mock the RAM floor high so builds are deterministic in the
    contended sandbox (the floor itself is never bypassed — the mock
    just says the machine has RAM to spare)."""
    return mock.patch("skullpt.governor.free_ram_mb", return_value=8192)


# --------------------------------------------------------------------- T1

class GovernorRefusalTests(unittest.TestCase):
    """T1: a governor refusal is not a build failure — the job stays
    approved and can build again, and a failed job can retry."""

    def test_refusal_leaves_job_approved_and_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            with mock.patch("skullpt.governor.free_ram_mb", return_value=100):
                report = build_job(job, ws)
            self.assertFalse(report["ok"])
            self.assertEqual(report["stage"], "governor")
            self.assertEqual(job.state, "approved")  # NOT failed
            self.assertNotIn("failure", job._state)
            # And it builds once RAM is back.
            with _high_ram():
                report2 = build_job(job, ws)
            self.assertTrue(report2["ok"], report2)
            self.assertEqual(job.state, "awaiting_review")

    def test_refusal_is_logged_not_buried(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            with mock.patch("skullpt.governor.free_ram_mb", return_value=100):
                build_job(job, ws)
            events = [json.loads(line)["event"]
                      for line in (job.dir / "session.jsonl").read_text().splitlines()]
            self.assertIn("governor_refused", events)

    def test_retry_returns_failed_job_to_approved(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            job.fail("boom")
            self.assertEqual(job.state, "failed")
            job.retry()
            self.assertEqual(job.state, "approved")
            self.assertNotIn("failure", job._state)
            events = [json.loads(line)["event"]
                      for line in (job.dir / "session.jsonl").read_text().splitlines()]
            self.assertIn("retry", events)

    def test_retry_from_other_states_is_a_gate_error(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)  # awaiting_approval, not failed
            with self.assertRaises(GateError):
                job.retry()

    def test_act_retry(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            job.fail("boom")
            result = bench_mod.act(ws, "retry", {"job": job.dir.name})
            self.assertTrue(result["ok"])
            self.assertEqual(result["job"], job.dir.name)
            self.assertEqual(Job(job.dir).state, "approved")

    def test_cli_retry(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            job.fail("boom")
            rc = cli_mod.main(["retry", str(ws), job.dir.name])
            self.assertEqual(rc, 0)
            self.assertEqual(Job(job.dir).state, "approved")

    def test_chat_retry_verb(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            job.fail("boom")
            result = bench_mod.chat_reply(ws, "retry", selected=job.dir.name)
            self.assertIn("approved again", result["reply"])
            self.assertEqual(Job(job.dir).state, "approved")

    def test_chat_retry_with_nothing_failed_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            _job(ws)
            result = bench_mod.chat_reply(ws, "retry")
            self.assertIn("No job is waiting for retry", result["reply"])


# --------------------------------------------------------------------- T2

class _MockGeminiChat(BaseHTTPRequestHandler):
    """Answers every prompt with fixed text; keeps the prompts it saw."""
    seen_prompts: list[str] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        text = body["contents"][0]["parts"][0]["text"]
        type(self).seen_prompts.append(text)
        if text.startswith("You are the planner seat"):
            payload = json.dumps(SHOP)
        else:
            payload = "mock answer here"
        resp = {"candidates": [{"content": {"parts": [{"text": payload}]}}]}
        raw = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class OpenConversationTests(unittest.TestCase):
    """T2: open conversation works with a key set — the planner seat
    dict (not the whole seats dict), context folded into the prompt."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGeminiChat)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "DESIGNSHELL_GEMINI_URL")}
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["DESIGNSHELL_GEMINI_URL"] = f"http://127.0.0.1:{self.port}/mock"
        _MockGeminiChat.seen_prompts = []

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_open_conversation_answers_with_key(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            result = bench_mod.chat_reply(ws, "hello, what can you do?")
            self.assertEqual(result["reply"], "mock answer here")

    def test_prompt_carries_the_workshop_context(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            bench_mod.chat_reply(ws, "hello there")
            self.assertEqual(len(_MockGeminiChat.seen_prompts), 1)
            self.assertIn("SKULLPT Bench", _MockGeminiChat.seen_prompts[0])
            self.assertIn("hello there", _MockGeminiChat.seen_prompts[0])

    def test_open_conversation_without_key_says_so_plainly(self):
        os.environ.pop("GEMINI_API_KEY")
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            result = bench_mod.chat_reply(ws, "hello, what can you do?")
            self.assertIn("GEMINI_API_KEY", result["reply"])
            self.assertIn("won't fake one", result["reply"])

    def test_planner_ready_follows_the_key(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            self.assertTrue(bench_mod._planner_ready(ws))
            os.environ.pop("GEMINI_API_KEY")
            self.assertFalse(bench_mod._planner_ready(ws))

    def test_plan_action_returns_job(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            result = bench_mod.act(ws, "plan", {"words": "a shop panel", "kind": "ui_component"})
            self.assertTrue(result["ok"])
            self.assertIn("job", result)
            self.assertTrue((ws / "jobs" / result["job"]).is_dir())


# --------------------------------------------------------------------- T3

class ZeroExporterTests(unittest.TestCase):
    """T3: a kind with no exporters fails loudly — never a 'successful'
    build with zero outputs."""

    def _kind_job(self, ws, kind):
        desc = {"name": f"Test {kind}", "kind": kind,
                "parts": [{"id": "body", "type": "body"}]}
        return _approved_job(ws, desc)

    def test_character_build_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._kind_job(ws, "character")
            report = build_job(job, ws)
            self.assertFalse(report["ok"])
            self.assertEqual(report["stage"], "exporters")
            self.assertIn("no exporter", report["problems"][0])
            self.assertEqual(job.state, "failed")
            self.assertFalse((ws / ".heavy.lock").exists())  # lock never taken

    def test_project_build_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._kind_job(ws, "project")
            report = build_job(job, ws)
            self.assertFalse(report["ok"])
            self.assertEqual(report["stage"], "exporters")

    def test_zero_outputs_fails_machine_checks(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            result = checks_mod.run_machine_checks(SHOP, out, [])
            self.assertFalse(result["ok"])
            presence = [c for c in result["checks"] if c["check"] == "outputs_present"]
            self.assertEqual(len(presence), 1)
            self.assertFalse(presence[0]["ok"])

    def test_nonzero_outputs_passes_presence_check(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "panel.html").write_text("<html></html>")
            result = checks_mod.run_machine_checks(SHOP, out, ["panel.html"])
            presence = [c for c in result["checks"] if c["check"] == "outputs_present"]
            self.assertTrue(presence[0]["ok"])


# --------------------------------------------------------------------- T4

class TriangleEstimateTests(unittest.TestCase):
    """T4: the budget key is max_tris, and the pre-build gate estimates
    triangles from the shapes — mirroring the Blender builders, so the
    estimate is a sound lower bound on the real mesh."""

    def test_per_shape_counts(self):
        est = desc_mod.estimate_triangles
        self.assertEqual(est({"shape": "box"}), 12)
        self.assertEqual(est({"shape": "cylinder"}), 92)
        self.assertEqual(est({"shape": "sphere"}), 528)
        # extrude: 4n-4 for n profile points
        self.assertEqual(est({"shape": "extrude",
                              "profile_cm": [[0, 0]] * 8}), 28)
        # lathe: (R-1)*40 + 20 per open end; the craft vase has 6
        # points, first radius 0 (closed), last radius 7 (open)
        vase = {"shape": "lathe", "profile_cm": [[0, 0], [5, 1], [6, 2],
                                                 [6, 4], [5, 5], [7, 6]]}
        self.assertEqual(est(vase), 5 * 40 + 20)
        # lathe with both ends open
        open_lathe = {"shape": "lathe", "profile_cm": [[3, 0], [3, 5]]}
        self.assertEqual(est(open_lathe), 40 + 20 + 20)
        # tube: (n-1)*20 for n path points, no caps
        self.assertEqual(est({"shape": "tube",
                              "path_cm": [[0, 0, 0]] * 5}), 80)
        # shapes the exporter falls back to a cube for
        self.assertEqual(est({"shape": "extrude"}), 12)
        self.assertEqual(est({"shape": "lathe"}), 12)
        self.assertEqual(est({"shape": "tube"}), 12)
        self.assertEqual(est({}), 12)

    def test_example_totals(self):
        self.assertEqual(desc_mod.estimate_description_triangles(STALL), 1356)
        self.assertEqual(desc_mod.estimate_description_triangles(ROOM), 36)

    def test_repeat_and_mirror_multiply(self):
        # The craft stall's picket repeats 8x and its legs mirror —
        # the estimate counts every concrete part, not every template.
        desc = {"name": "t", "kind": "scene", "parts": [
            {"id": "p", "type": "p", "shape": "box",
             "repeat": {"count": 8, "offset_cm": [10, 0, 0]}},
            {"id": "m", "type": "m", "shape": "box",
             "mirror": {"axis": "x", "at_cm": 0}},
        ]}
        self.assertEqual(desc_mod.estimate_description_triangles(desc),
                         8 * 12 + 2 * 12)

    def test_budget_uses_max_tris(self):
        self.assertEqual(governor.check_budget(ROOM), [])
        tight = json.loads(json.dumps(ROOM))
        tight["budget"]["max_tris"] = 10  # the room estimates 36
        problems = governor.check_budget(tight)
        self.assertEqual(len(problems), 1)
        self.assertIn("36", problems[0])
        self.assertIn("10", problems[0])

    def test_legacy_max_triangles_alias(self):
        legacy = json.loads(json.dumps(ROOM))
        legacy["budget"] = {"max_triangles": 10}
        self.assertTrue(governor.check_budget(legacy))
        legacy["budget"] = {"max_triangles": 100000}
        self.assertEqual(governor.check_budget(legacy), [])

    def test_max_tris_wins_when_both_keys_present(self):
        both = json.loads(json.dumps(ROOM))
        both["budget"] = {"max_tris": 10, "max_triangles": 100000}
        self.assertTrue(governor.check_budget(both))

    def test_tavern_example_uses_max_tris(self):
        self.assertIn("max_tris", ROOM["budget"])
        self.assertNotIn("max_triangles", ROOM["budget"])
        self.assertTrue(all("triangles" not in p for p in ROOM["parts"]))


# --------------------------------------------------------------------- T5

class SnapshotTests(unittest.TestCase):
    """T5: the description is snapshotted before anything mutates it."""

    def test_approve_snapshots_the_description(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)
            job.approve()
            snap_path = job.dir / "description.approved.json"
            self.assertTrue(snap_path.exists())
            # The snapshot is the description file as it was at approve
            # time (load_description() adds defaults, so compare files).
            self.assertEqual(json.loads(snap_path.read_text()),
                             json.loads((job.dir / "description.json").read_text()))
            self.assertEqual(json.loads(snap_path.read_text())["name"], SHOP["name"])

    def test_vision_patch_snapshots_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws, ROOM)
            before = job.load_description()

            class _FakeCritic:
                def __init__(self):
                    self.calls = 0

                def critique(self, desc, previews, reference_png=None):
                    self.calls += 1
                    if self.calls == 1:
                        return {"vision": True, "note": "first look",
                                "fixes": [{"part": "floor", "issue": "floor floats",
                                           "fix": "lower it",
                                           "patch": {"position_cm": [0, 0, 5]}}]}
                    return {"vision": True, "note": "second look", "fixes": []}

            fake = _FakeCritic()
            with mock.patch("skullpt.builder.get_critic", return_value=fake), \
                    mock.patch.dict(exporters.EXPORTERS,
                                    {"blender_scene": lambda d, o: []}):
                critique, rounds = builder_mod._run_critic(
                    job, json.loads(json.dumps(ROOM)), [], {}, [])
            self.assertEqual(len(rounds), 1)
            snap_path = job.dir / "description.before-vision-patch.json"
            self.assertTrue(snap_path.exists())
            self.assertEqual(json.loads(snap_path.read_text())["parts"][0]["position_cm"],
                             before["parts"][0]["position_cm"])
            # And the live description carries the patch.
            self.assertEqual(job.load_description()["parts"][0]["position_cm"],
                             [0, 0, 5])


class CloudMeshSnapshotTests(MockGradioCase):
    """T5: cloud-mesh freezes the old description before overwriting."""

    def test_snapshot_before_overwrite(self):
        job = Job.create(self.ws / "jobs", ROOM)
        before = job.load_description()
        result = cloudmesh.cloud_mesh(self.ws, self.photo, job=job.dir.name)
        self.assertTrue(result["ok"])
        snap_path = job.dir / "description.before-cloud-mesh.json"
        self.assertTrue(snap_path.exists())
        self.assertEqual(json.loads(snap_path.read_text())["name"], before["name"])
        self.assertIn("cloud-mesh", job.load_description()["tags"])


# ---------------------------------------------------------------- T6 + P5

class InstallerDownloadTests(unittest.TestCase):
    """T6 + P5: the installer's downloads resume and verify, Blender is
    never downgraded, and the image-engine build is capped on small
    machines. (Text tests — the staged runs below prove the behavior.)"""

    def test_downloads_are_resumable_and_verified(self):
        self.assertIn("curl -fSL -C -", INSTALL_SH)
        self.assertIn("sha256sum", INSTALL_SH)
        self.assertIn("download_verified", INSTALL_SH)
        for name in ("BLENDER_SHA256", "SD_MODEL_SHA256"):
            match = re.search(rf'^{name}="([0-9a-f]{{64}})"$', INSTALL_SH, re.M)
            self.assertIsNotNone(match, f"{name} is not a recorded 64-hex hash")

    def test_no_bare_curl_downloads(self):
        for line in INSTALL_SH.splitlines():
            if "curl -fSL" in line:
                self.assertIn("-C -", line, f"non-resumable curl: {line}")

    def test_no_downgrade_logic_present(self):
        self.assertIn("sort -V", INSTALL_SH)
        self.assertIn("leaving it alone", INSTALL_SH)
        self.assertIn("BLENDER_VERSION", INSTALL_SH)

    def test_build_jobs_capped_on_small_machines(self):
        self.assertIn("BUILD_JOBS=2", INSTALL_SH)
        self.assertIn("8388608", INSTALL_SH)  # 8 GiB in KiB
        self.assertIn('cmake --build "$TMPD/sd/build" -j"$BUILD_JOBS"', INSTALL_SH)

    def test_no_sudo_anywhere(self):
        self.assertNotIn("sudo", INSTALL_SH)

    def test_blender_tarball_goes_to_the_persistent_cache(self):
        self.assertIn("DOWNLOAD_CACHE", INSTALL_SH)
        self.assertIn('"$DOWNLOAD_CACHE/$BLENDER_TARBALL"', INSTALL_SH)


STUB_BLENDER_NEW = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "Blender 5.3.0"; exit 0; fi
echo "stub blender: unexpected args $*" >&2
exit 1
"""

STUB_CURL_FAIL = """#!/bin/sh
echo "curl $*" >> "$CURL_LOG"
exit 1
"""

STUB_CURL_OK = """#!/bin/sh
echo "curl $*" >> "$CURL_LOG"
dest=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then dest="$a"; fi
  prev="$a"
done
[ -n "$dest" ] && echo "fake-download-bytes" > "$dest"
exit 0
"""

STUB_SHA256SUM = """#!/bin/sh
echo "$STUB_SHA256  $1"
exit 0
"""

STUB_CMAKE = """#!/bin/sh
echo "cmake $*" >> "$CMAKE_LOG"
exit 0
"""

STUB_GIT = """#!/bin/sh
echo "git $*" >> "$GIT_LOG"
exit 0
"""

STUB_GXX = """#!/bin/sh
exit 0
"""


class _StagedInstallV14(unittest.TestCase):
    """Runs the real install.sh from a staged copy, with stub commands
    (terminal mode — no dialogs needed for these paths)."""

    def _stage(self, tmp, stubs):
        tmp = Path(tmp)
        stage = tmp / "stage"
        shutil.copytree(ROOT, stage,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (stage / "install.sh").chmod(0o755)
        (stage / "run.sh").chmod(0o755)
        home = tmp / "home"
        home.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        state_dir = tmp / "state"
        state_dir.mkdir()
        scripts = {"python3": STUB_PYTHON3,
                   "fake-venv-python": FAKE_VENV_PYTHON,
                   "pkexec": STUB_PKEXEC, **stubs}
        for name, text in scripts.items():
            path = bin_dir / name
            path.write_text(text)
            path.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        env["HOME"] = str(home)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env["STUB_DIR"] = str(bin_dir)
        env["STUB_STATE"] = str(state_dir)
        env["PKEXEC_LOG"] = str(tmp / "pkexec.log")
        env["CURL_LOG"] = str(tmp / "curl.log")
        env["CMAKE_LOG"] = str(tmp / "cmake.log")
        env["GIT_LOG"] = str(tmp / "git.log")
        return stage, home, env, tmp

    def _run_install(self, stage, env, *args):
        return subprocess.run(["bash", "install.sh", *args], cwd=stage,
                              env=env, capture_output=True, text=True,
                              timeout=120)


class StagedInstallerTests(_StagedInstallV14):
    """T6 + P5, proven by running the real installer against stubs."""

    def test_newer_blender_skips_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(tmp, {
                "blender": STUB_BLENDER_NEW,
                "curl": STUB_CURL_FAIL,
            })
            res = self._run_install(stage, env, "--terminal", "--with-blender")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("Blender 5.3.0 is already installed", res.stdout)
            self.assertIn("leaving it alone", res.stdout)
            # No download was attempted at all.
            curl_log = tmp_path / "curl.log"
            self.assertFalse(curl_log.exists(),
                             "curl ran despite the newer Blender: "
                             + (curl_log.read_text() if curl_log.exists() else ""))
            # And no pkexec Blender install ran.
            pkexec_log = tmp_path / "pkexec.log"
            if pkexec_log.exists():
                self.assertNotIn("/opt/blender", pkexec_log.read_text())

    def test_image_engine_build_uses_j2_and_resumable_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_hash = re.search(
                r'^SD_MODEL_SHA256="([0-9a-f]{64})"$', INSTALL_SH, re.M).group(1)
            stage, home, env, tmp_path = self._stage(tmp, {
                "git": STUB_GIT,
                "cmake": STUB_CMAKE,
                "g++": STUB_GXX,
                "curl": STUB_CURL_OK,
                "sha256sum": STUB_SHA256SUM,
            })
            env["STUB_SHA256"] = model_hash
            res = self._run_install(stage, env, "--terminal", "--with-image-engine")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            cmake_log = (tmp_path / "cmake.log").read_text()
            mem_kb = int(re.search(r"^MemTotal:\s+(\d+) kB",
                                   Path("/proc/meminfo").read_text(), re.M).group(1))
            expected_jobs = 2 if mem_kb <= 8388608 else None
            build_lines = [ln for ln in cmake_log.splitlines() if "--build" in ln]
            self.assertEqual(len(build_lines), 1, cmake_log)
            if expected_jobs is not None:
                self.assertIn("-j2", build_lines[0], build_lines[0])
            else:
                self.assertRegex(build_lines[0], r"-j\d+")
            # The model download resumed (-C -) and verified.
            curl_log = (tmp_path / "curl.log").read_text()
            self.assertIn("-C -", curl_log, curl_log)
            self.assertIn("LCM_Dreamshaper_v7-f16.gguf", curl_log, curl_log)
            self.assertIn("downloaded and verified", res.stdout)
            model = home / "design-shell-models" / "LCM_Dreamshaper_v7-f16.gguf"
            self.assertTrue(model.exists())


# --------------------------------------------------------------------- T7

class ActJobFieldTests(unittest.TestCase):
    """T7: act() returns "job": name as a field — no caller has to
    split the detail string to find which job an action made."""

    def test_approve_and_new_example_return_job(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            made = bench_mod.act(ws, "new-example",
                                 {"example": "workbench.description.json"})
            self.assertEqual(made["job"], "workbench-corner")
            approved = bench_mod.act(ws, "approve", {"job": made["job"]})
            self.assertEqual(approved["job"], "workbench-corner")

    def test_build_returns_job_and_run(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            with _high_ram():
                result = bench_mod.act(ws, "build", {"job": job.dir.name})
                self.assertEqual(result["job"], job.dir.name)
                self.assertIn("run", result)
                # Let the background build finish before the temp dir
                # goes. The RAM mock stays up for the whole wait: the
                # build runs on a background thread, and a real-RAM dip
                # mid-run would refuse it — the governor working, not
                # a test failure.
                for _ in range(100):
                    if Job(job.dir).state in ("awaiting_review", "failed"):
                        break
                    import time
                    time.sleep(0.1)
                self.assertEqual(Job(job.dir).state, "awaiting_review")

    def test_file_returns_job(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _approved_job(ws)
            with _high_ram():
                report = build_job(job, ws)
            self.assertTrue(report["ok"], report)
            result = bench_mod.act(ws, "file", {"job": job.dir.name})
            self.assertEqual(result["job"], job.dir.name)


# ------------------------------------------------------- small fix (a)

class SkullExclusionTests(unittest.TestCase):
    """The skull example is never offered — gated in code, file kept."""

    def test_skull_is_gated_in_code(self):
        self.assertIn("skull.description.json", bench_mod.EXCLUDED_EXAMPLES)

    def test_skull_not_offered_in_workspace_state(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            examples = bench_mod.workspace_state(ws)["examples"]
            self.assertNotIn("skull.description.json", examples)
            self.assertIn("workbench.description.json", examples)

    def test_skull_refused_by_new_example(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            with self.assertRaises(ValueError):
                bench_mod.act(ws, "new-example",
                              {"example": "skull.description.json"})

    def test_skull_file_kept_in_source_tree(self):
        # The file is kept in the source tree (gated, not deleted) —
        # but zip extracts exclude it by design, so skip there.
        skull_path = ROOT / "examples" / "skull.description.json"
        if not skull_path.exists():
            self.skipTest("skull example not in this tree (zip extracts exclude it)")
        self.assertTrue(skull_path.exists())


# ------------------------------------------------------- small fix (b)

class ChatPersistenceTests(unittest.TestCase):
    """The chat transcript survives a Bench restart."""

    def test_transcript_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            bench_mod._CHATS.pop(str(ws), None)
            history = bench_mod.chat_history(ws)
            history.append({"role": "user", "text": "make a stall"})
            history.append({"role": "bot", "text": "drafted it"})
            bench_mod.save_chat_history(ws)
            chat_file = ws / "chat.jsonl"
            self.assertTrue(chat_file.exists())
            # Simulate a restart: drop the in-memory copy and reload.
            bench_mod._CHATS.pop(str(ws), None)
            reloaded = bench_mod.chat_history(ws)
            self.assertEqual([e["text"] for e in reloaded],
                             ["make a stall", "drafted it"])

    def test_chat_file_is_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            bench_mod._CHATS.pop(str(ws), None)
            history = bench_mod.chat_history(ws)
            history.append({"role": "user", "text": "hello"})
            bench_mod.save_chat_history(ws)
            lines = (ws / "chat.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["text"], "hello")

    def test_malformed_lines_skipped_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            (ws / "chat.jsonl").write_text(
                "not json\n" + json.dumps({"role": "user", "text": "kept"}) + "\n")
            bench_mod._CHATS.pop(str(ws), None)
            reloaded = bench_mod.chat_history(ws)
            self.assertEqual([e["text"] for e in reloaded], ["kept"])


# ------------------------------------------------------- small fix (c)

class ThumbnailTests(unittest.TestCase):
    """Library thumbnails use the first render PNG, not the .svg."""

    def _filed_job(self, ws, outputs):
        job = _job(ws)
        job.transition("awaiting_review")
        for name in outputs:
            (job.output_dir / name).write_text("fake")
        report = {"ok": True, "outputs": outputs, "recipes": []}
        (job.dir / "build-report.json").write_text(json.dumps(report))
        return job

    def test_file_prefers_png_over_svg(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._filed_job(ws, ["mock.svg", "render.png", "page.html"])
            bench_mod.act(ws, "file", {"job": job.dir.name})
            manifest = json.loads(
                (ws / "library" / job.dir.name / "manifest.json").read_text())
            self.assertEqual(manifest["thumbnail"], "render.png")

    def test_file_falls_back_to_svg(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._filed_job(ws, ["mock.svg", "page.html"])
            bench_mod.act(ws, "file", {"job": job.dir.name})
            manifest = json.loads(
                (ws / "library" / job.dir.name / "manifest.json").read_text())
            self.assertEqual(manifest["thumbnail"], "mock.svg")

    def test_cli_file_prefers_png(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = self._filed_job(ws, ["render.png", "mock.svg"])
            rc = cli_mod.main(["file", str(ws), job.dir.name])
            self.assertEqual(rc, 0)
            manifest = json.loads(
                (ws / "library" / job.dir.name / "manifest.json").read_text())
            self.assertEqual(manifest["thumbnail"], "render.png")


# ------------------------------------------------------- small fix (d)

class SkippedTests(unittest.TestCase):
    """U32: nothing is silently skipped — unreadable jobs and batches
    are listed with their reasons."""

    def test_unreadable_job_listed_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            good = _job(ws)
            bad_dir = ws / "jobs" / "broken-job"
            bad_dir.mkdir()
            (bad_dir / "job.json").write_text("{not json")
            state = bench_mod.workspace_state(ws)
            self.assertEqual([j["name"] for j in state["jobs"]], [good.dir.name])
            self.assertEqual(len(state["skipped"]), 1)
            entry = state["skipped"][0]
            self.assertEqual(entry["where"], "jobs")
            self.assertEqual(entry["name"], "broken-job")
            self.assertTrue(entry["reason"])

    def test_unreadable_image_batch_listed_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            bad_dir = ws / "images" / "broken-batch"
            bad_dir.mkdir(parents=True)
            state = bench_mod.workspace_state(ws)
            self.assertEqual(len(state["skipped"]), 1)
            self.assertEqual(state["skipped"][0]["where"], "images")
            self.assertEqual(state["skipped"][0]["name"], "broken-batch")


# ------------------------------------------------------- small fix (e)

class GovernorLockTests(unittest.TestCase):
    """The governor lock is atomic, and the RAM floor is re-checked
    mid-run."""

    def test_concurrent_acquire_has_one_winner(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            results: list[str] = []

            def try_acquire():
                lock = governor.HeavyJobLock(lock_path)
                try:
                    with _high_ram():
                        lock.acquire()
                    results.append("won")
                    threading.Event().wait(0.2)
                except governor.GovernorError:
                    results.append("lost")
                finally:
                    lock.release()

            threads = [threading.Thread(target=try_acquire) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(results), ["lost", "won"])
            self.assertFalse(lock_path.exists())

    def test_stale_lock_is_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            # A pid that cannot be alive (far above any real pid).
            lock_path.write_text("999999999")
            lock = governor.HeavyJobLock(lock_path)
            with _high_ram():
                lock.acquire()
            self.assertEqual(lock_path.read_text(), str(os.getpid()))
            lock.release()

    def test_live_lock_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            lock_path.write_text(str(os.getpid()))
            lock = governor.HeavyJobLock(lock_path)
            with _high_ram(), self.assertRaises(governor.GovernorError):
                lock.acquire()

    def test_recheck_enforces_floor(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            lock = governor.HeavyJobLock(lock_path)
            with _high_ram():
                lock.acquire()
                lock.recheck()  # plenty of RAM: passes
                with mock.patch("skullpt.governor.free_ram_mb", return_value=10):
                    with self.assertRaises(governor.GovernorError):
                        lock.recheck()
            lock.release()


# ------------------------------------------------------------------ Qt

@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class QtRetryButtonTests(unittest.TestCase):
    """The Bench window's Retry button follows the failed state, and
    the tree lists what could not be read."""

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "jobs").mkdir()
        (self.ws / "library").mkdir()
        self.job = Job.create(self.ws / "jobs", ROOM)
        self.window = qtbench.BenchWindow(self.ws)
        self.window.show()
        self._app.processEvents()
        self._app.processEvents()
        ctx = self.window._viewport.context()
        if ctx is None or not ctx.isValid():
            self.skipTest("no GL context on this platform (needs Xvfb + xcb)")

    def tearDown(self):
        self.window.close()
        self.tmp.cleanup()

    def test_retry_button_follows_failed_state(self):
        self.assertFalse(self.window._retry_btn.isEnabled())
        self.job.approve()
        self.job.fail("test failure")
        self.window._refresh()
        self.assertTrue(self.window._retry_btn.isEnabled())
        self.assertFalse(self.window._build_btn.isEnabled())

    def test_retry_button_retries_the_job(self):
        self.job.approve()
        self.job.fail("test failure")
        self.window._refresh()
        self.window._do_action("retry")
        self.assertEqual(Job(self.job.dir).state, "approved")
        self.assertFalse(self.window._retry_btn.isEnabled())
        self.assertTrue(self.window._build_btn.isEnabled())

    def test_skipped_section_lists_unreadable_jobs(self):
        bad_dir = self.ws / "jobs" / "broken-job"
        bad_dir.mkdir()
        (bad_dir / "job.json").write_text("{not json")
        self.window._refresh()
        tree = self.window._tree
        heads = [tree.topLevelItem(i).text(0)
                 for i in range(tree.topLevelItemCount())]
        self.assertIn("Couldn't read", heads)
        skip_item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                         if tree.topLevelItem(i).text(0) == "Couldn't read")
        self.assertEqual(skip_item.childCount(), 1)
        self.assertIn("broken-job", skip_item.child(0).text(0))


if __name__ == "__main__":
    unittest.main()
