"""SKULLPT v1.5 tests: the redesign (R1-R9), the P1 tasks (T8-T20),
and the perf items (P1, P2, P6, P7, P8).

Backend tests run everywhere; Qt tests skip without PySide6.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import cli as cli_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import governor  # noqa: E402
from skullpt import live  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt import seats as seats_mod  # noqa: E402
from skullpt import tokenfile  # noqa: E402
from skullpt.jobs import GateError, Job  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())
ROOM = json.loads((ROOT / "examples" / "tavern-room.description.json").read_text())


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _set_state(job: Job, state: str) -> None:
    """Force a job's state the way a crash would leave it."""
    job.transition(state)


# ---------------------------------------------------------------- P6 + T12

class GovernorAdaptiveFloorTests(unittest.TestCase):
    """P6: the RAM floor adapts to the machine instead of a fixed
    1024 MB — 15% of total RAM, never below 1024 MB."""

    def test_adaptive_floor_is_15_percent_or_1024(self):
        total = governor.total_ram_mb()
        self.assertGreater(total, 0)
        expected = max(1024, int(total * governor.ADAPTIVE_FLOOR_FRACTION))
        self.assertEqual(governor.adaptive_min_free_mb(), expected)

    def test_cancelled_is_a_runtime_error(self):
        self.assertTrue(issubclass(governor.Cancelled, RuntimeError))

    def test_lock_defaults_to_adaptive_floor(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("skullpt.governor.free_ram_mb", return_value=8192):
            lock = governor.HeavyJobLock(Path(td) / "heavy.lock")
            lock.acquire()  # no error with plenty of RAM
            self.assertTrue(lock.lock_path.exists())
            lock.release()

    def test_lock_refuses_below_adaptive_floor(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch("skullpt.governor.free_ram_mb", return_value=100):
            lock = governor.HeavyJobLock(Path(td) / "heavy.lock")
            with self.assertRaises(governor.GovernorError):
                lock.acquire()
            lock.release()


# --------------------------------------------------------------------- T10

class JobManagementTests(unittest.TestCase):
    """T10: delete / rename / duplicate / stale-state recovery."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_delete_removes_the_job_folder(self):
        job = _job(self.ws)
        job.delete()
        self.assertFalse(job.dir.exists())

    def test_delete_refuses_a_building_job(self):
        job = _job(self.ws)
        _set_state(job, "building")
        with self.assertRaises(GateError):
            job.delete()
        self.assertTrue(job.dir.exists())

    def test_rename_moves_folder_and_title(self):
        job = _job(self.ws)
        old_dir = job.dir
        renamed = job.rename("My New Panel")
        self.assertFalse(old_dir.exists())
        self.assertTrue(renamed.dir.exists())
        self.assertEqual(renamed.dir.name, "my-new-panel")
        desc = json.loads(renamed.desc_path.read_text())
        self.assertEqual(desc["name"], "My New Panel")

    def test_rename_refuses_a_collision(self):
        _job(self.ws, SHOP)
        other = _job(self.ws, ROOM)
        with self.assertRaises(ValueError):
            other.rename(SHOP["name"])

    def test_rename_refuses_a_building_job(self):
        job = _job(self.ws)
        _set_state(job, "building")
        with self.assertRaises(GateError):
            job.rename("New Name")

    def test_duplicate_is_a_fresh_job_with_the_same_description(self):
        job = _job(self.ws)
        job.approve()
        copy = job.duplicate()
        self.assertNotEqual(copy.dir, job.dir)
        self.assertEqual(copy.state, "awaiting_approval")
        self.assertTrue(copy.dir.name.endswith("-copy"))
        orig = json.loads(job.desc_path.read_text())
        dup = json.loads(copy.desc_path.read_text())
        self.assertEqual(orig["parts"], dup["parts"])
        self.assertFalse(any(copy.output_dir.iterdir()))

    def test_recover_stale_jobs_returns_approved(self):
        job = _job(self.ws)
        _set_state(job, "building")
        recovered = bench_mod.recover_stale(self.ws)
        self.assertEqual(recovered, [job.dir.name])
        self.assertEqual(Job(job.dir).state, "approved")

    def test_recover_leaves_a_live_build_alone(self):
        job = _job(self.ws)
        _set_state(job, "building")
        lock = governor.HeavyJobLock(self.ws / ".heavy.lock")
        with mock.patch("skullpt.governor.free_ram_mb", return_value=8192):
            lock.acquire()
            try:
                self.assertEqual(bench_mod.recover_stale(self.ws), [])
                self.assertEqual(Job(job.dir).state, "building")
            finally:
                lock.release()


# --------------------------------------------------------------------- T11

class PartEditTests(unittest.TestCase):
    """T11 (backend): editing a part validates, snapshots the old
    description, and sends the job back to the plan gate."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_edit_part_changes_the_field(self):
        job = _job(self.ws)
        part_id = SHOP["parts"][0]["id"]
        job.edit_part(part_id, {"material": "oak"})
        desc = json.loads(job.desc_path.read_text())
        part = next(p for p in desc["parts"] if p["id"] == part_id)
        self.assertEqual(part["material"], "oak")

    def test_edit_part_snapshots_the_old_description(self):
        job = _job(self.ws)
        job.edit_part(SHOP["parts"][0]["id"], {"material": "oak"})
        snapshot = job.dir / "description.before-part-edit.json"
        self.assertTrue(snapshot.exists())
        old = json.loads(snapshot.read_text())
        self.assertNotEqual(old["parts"][0].get("material"), "oak")

    def test_edit_part_re_gates_an_approved_job(self):
        job = _job(self.ws)
        job.approve()
        job.edit_part(SHOP["parts"][0]["id"], {"material": "oak"})
        self.assertEqual(Job(job.dir).state, "awaiting_approval")

    def test_edit_part_refuses_an_unknown_field(self):
        job = _job(self.ws)
        with self.assertRaises(ValueError):
            job.edit_part(SHOP["parts"][0]["id"], {"nope": 1})

    def test_edit_part_refuses_an_unknown_part(self):
        job = _job(self.ws)
        with self.assertRaises(ValueError):
            job.edit_part("not-a-part", {"material": "oak"})

    def test_edit_part_refuses_a_building_job(self):
        job = _job(self.ws)
        _set_state(job, "building")
        with self.assertRaises(GateError):
            job.edit_part(SHOP["parts"][0]["id"], {"material": "oak"})


# ---------------------------------------------------------------------- T9

class TokenFileTests(unittest.TestCase):
    """T9: the HF token lives in a gitignored local file, mode 0600,
    and is never in code or in the repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_load_has_clear_round_trip(self):
        self.assertFalse(tokenfile.has_token(self.ws))
        tokenfile.save_token(self.ws, "hf_testtoken123")
        self.assertTrue(tokenfile.has_token(self.ws))
        self.assertEqual(tokenfile.load_token(self.ws), "hf_testtoken123")
        tokenfile.clear_token(self.ws)
        self.assertFalse(tokenfile.has_token(self.ws))
        self.assertIsNone(tokenfile.load_token(self.ws))

    def test_token_file_is_mode_0600(self):
        tokenfile.save_token(self.ws, "hf_testtoken123")
        mode = stat.S_IMODE((self.ws / "hf_token.key").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_token_file_is_gitignored(self):
        tokenfile.save_token(self.ws, "hf_testtoken123")
        gitignore = (self.ws / ".gitignore").read_text()
        self.assertIn("hf_token.key", gitignore)
        tokenfile.save_token(self.ws, "hf_secondtoken")
        self.assertEqual((self.ws / ".gitignore").read_text().count("hf_token.key"), 1)

    def test_token_value_prefers_env_then_file(self):
        from skullpt import cloudmesh

        tokenfile.save_token(self.ws, "hf_fromfile")
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_fromenv"}):
            self.assertEqual(cloudmesh.token_value(workspace=self.ws), "hf_fromenv")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cloudmesh.token_value(workspace=self.ws), "hf_fromfile")
        self.assertEqual(cloudmesh.token_value(token="hf_explicit", workspace=self.ws),
                         "hf_explicit")


# --------------------------------------------------------------------- T20

class SeatsTests(unittest.TestCase):
    """T20: the default seats are only the wired ones; a user can
    still define extra roles in seats.json."""

    def test_default_seats_are_planner_critic_fallback(self):
        # v1.6 (T22): the repairer seat is back — the planner repair
        # loop uses it. drafter and heavy_coder are still out.
        self.assertEqual(set(seats_mod.DEFAULT_SEATS),
                         {"planner", "critic", "fallback", "repairer"})
        self.assertNotIn("drafter", seats_mod.DEFAULT_SEATS)
        self.assertNotIn("heavy_coder", seats_mod.DEFAULT_SEATS)

    def test_user_defined_roles_still_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seats.json"
            path.write_text(json.dumps({
                "my_role": {"provider": "ollama", "model": "x"}}))
            seats = seats_mod.load_seats(path)
            self.assertIn("my_role", seats)
            self.assertIn("planner", seats)


class LiveFallbackTests(unittest.TestCase):
    """T20: the planner seat is tried first, the local fallback
    second; when both fail the PRIMARY error is the one raised."""

    def _seats(self):
        return {"planner": {"provider": "gemini", "model": "mock",
                            "env_key": "GEMINI_API_KEY"},
                "fallback": {"provider": "ollama", "model": "mock"}}

    def test_planner_answer_wins(self):
        with mock.patch("skullpt.live.gemini_text", return_value="from gemini"), \
             mock.patch("skullpt.live.ollama_text") as ollama:
            text, role, err = live.text_with_fallback(self._seats(), "hi")
            self.assertEqual((text, role, err), ("from gemini", "planner", None))
            ollama.assert_not_called()

    def test_fallback_answers_when_planner_fails(self):
        with mock.patch("skullpt.live.gemini_text",
                        side_effect=live.SeatCallFailed("gemini down")), \
             mock.patch("skullpt.live.ollama_text", return_value="from ollama"):
            text, role, err = live.text_with_fallback(self._seats(), "hi")
            self.assertEqual(text, "from ollama")
            self.assertEqual(role, "fallback")
            self.assertIn("gemini down", err)

    def test_both_failing_raises_the_primary_error(self):
        with mock.patch("skullpt.live.gemini_text",
                        side_effect=live.SeatCallFailed("gemini down")), \
             mock.patch("skullpt.live.ollama_text",
                        side_effect=live.SeatCallFailed("ollama down")):
            with self.assertRaises(live.SeatCallFailed) as ctx:
                live.text_with_fallback(self._seats(), "hi")
            self.assertIn("gemini down", str(ctx.exception))

    def test_unconfigured_planner_is_not_masked(self):
        with mock.patch("skullpt.live.gemini_text",
                        side_effect=live.SeatNotConfigured("no key")), \
             mock.patch("skullpt.live.ollama_text",
                        side_effect=live.SeatCallFailed("ollama down")):
            with self.assertRaises(live.SeatNotConfigured):
                live.text_with_fallback(self._seats(), "hi")

    def test_plan_from_words_falls_back_to_ollama(self):
        desc_json = json.dumps(SHOP)
        with mock.patch("skullpt.live.gemini_text",
                        side_effect=live.SeatCallFailed("gemini down")), \
             mock.patch("skullpt.live.ollama_text", return_value=desc_json):
            desc = live.plan_from_words("a shop panel", self._seats())
            self.assertEqual(desc["name"], SHOP["name"])
            self.assertEqual(live.last_plan_seat()["seat"], "fallback")


# ------------------------------------------------------- T18 prep + runs

class ParsePlanWordsTests(unittest.TestCase):
    """T18 prep: the chat router and the Bench agree on what a
    drafting command is."""

    def test_make_commands(self):
        self.assertEqual(bench_mod.parse_plan_words("make a chair"), "a chair")
        self.assertEqual(bench_mod.parse_plan_words("build me a table"), "a table")
        self.assertEqual(bench_mod.parse_plan_words("create a lamp"), "a lamp")
        self.assertEqual(bench_mod.parse_plan_words("design a shelf"), "a shelf")
        self.assertEqual(bench_mod.parse_plan_words("plan a room"), "a room")

    def test_non_commands_are_none(self):
        self.assertIsNone(bench_mod.parse_plan_words("build"))
        self.assertIsNone(bench_mod.parse_plan_words("hello there"))
        self.assertIsNone(bench_mod.parse_plan_words("status"))


class BenchRunTests(unittest.TestCase):
    """T12: runs report progress, store their result, and cancel
    cleanly instead of erroring."""

    def _run(self, run_id: str) -> dict:
        return next(r for r in bench_mod._runs() if r["id"] == run_id)

    def _wait_status(self, run_id: str, status: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self._run(run_id)
            if run["status"] == status:
                return run
            time.sleep(0.02)
        return self._run(run_id)

    def test_progress_and_result_are_visible(self):
        def fn(cancel, report):
            report(0.5, "halfway")
            return {"detail": "all done", "job": "some-job"}

        run_id = bench_mod._start_run("plan", "test", fn)
        run = self._wait_status(run_id, "done")
        self.assertEqual(run["detail"], "all done")
        self.assertEqual(run["result"]["job"], "some-job")
        self.assertEqual(run["progress"], 1.0)

    def test_cancel_marks_cancelled_not_error(self):
        started = threading.Event()

        def fn(cancel, report):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise governor.Cancelled("stopped by the user")

        run_id = bench_mod._start_run("build", "test", fn)
        self.assertTrue(started.wait(timeout=5))
        self.assertTrue(bench_mod.cancel_run(run_id))
        run = self._wait_status(run_id, "cancelled")
        self.assertEqual(run["status"], "cancelled")
        self.assertIn("stopped", run["detail"])

    def test_cancel_of_a_finished_run_is_a_noop(self):
        run_id = bench_mod._start_run("plan", "test", lambda c, r: "done")
        self._wait_status(run_id, "done")
        self.assertFalse(bench_mod.cancel_run(run_id))
        self.assertFalse(bench_mod.cancel_run("run-does-not-exist"))


# ---------------------------------------------------------------- P7 + P8

class BenchCacheTests(unittest.TestCase):
    """P7: workspace_state caches the file-derived part and only
    re-reads when the workspace changes. P8: the call time is recorded."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        bench_mod.invalidate_workspace_cache()

    def test_state_reflects_a_new_job(self):
        first = bench_mod.workspace_state(self.ws)
        self.assertEqual(first["jobs"], [])
        _job(self.ws)
        second = bench_mod.workspace_state(self.ws)
        self.assertEqual(len(second["jobs"]), 1)

    def test_last_state_ms_is_recorded(self):
        bench_mod.workspace_state(self.ws)
        self.assertIsNotNone(bench_mod.LAST_STATE_MS)
        self.assertGreaterEqual(bench_mod.LAST_STATE_MS, 0.0)

    def test_invalidate_forces_a_re_read(self):
        _job(self.ws)
        bench_mod.workspace_state(self.ws)
        bench_mod.invalidate_workspace_cache(self.ws)
        state = bench_mod.workspace_state(self.ws)
        self.assertEqual(len(state["jobs"]), 1)


# ------------------------------------------------- act() actions (T10/T11)

class BenchActionTests(unittest.TestCase):
    """The new act() actions: delete, rename, duplicate, edit-part,
    intake, and the plan fallback note."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        bench_mod.invalidate_workspace_cache()

    def test_act_delete(self):
        job = _job(self.ws)
        result = bench_mod.act(self.ws, "delete", {"job": job.dir.name})
        self.assertIn("deleted", result["detail"])
        self.assertFalse(job.dir.exists())

    def test_act_rename(self):
        job = _job(self.ws)
        result = bench_mod.act(self.ws, "rename",
                               {"job": job.dir.name, "name": "Renamed Panel"})
        self.assertEqual(result["job"], "renamed-panel")
        self.assertTrue((self.ws / "jobs" / "renamed-panel").exists())

    def test_act_duplicate(self):
        job = _job(self.ws)
        result = bench_mod.act(self.ws, "duplicate", {"job": job.dir.name})
        self.assertTrue(result["job"].endswith("-copy"))
        self.assertIn("plan gate", result["detail"])

    def test_act_edit_part_re_gates(self):
        job = _job(self.ws)
        job.approve()
        part_id = SHOP["parts"][0]["id"]
        result = bench_mod.act(self.ws, "edit-part",
                               {"job": job.dir.name, "part": part_id,
                                "fields": {"material": "oak"}})
        self.assertIn("approval again", result["detail"])
        self.assertEqual(Job(job.dir).state, "awaiting_approval")

    def test_act_intake_creates_a_job(self):
        photo = Path(self._tmp.name) / "ref.png"
        photo.write_bytes(b"fake png bytes")
        result = bench_mod.act(self.ws, "intake", {"photo": str(photo)})
        self.assertIn("job", result)
        job_dir = self.ws / "jobs" / result["job"]
        self.assertTrue((job_dir / "description.json").exists())

    def test_act_plan_notes_the_fallback_seat(self):
        desc_json = json.dumps(SHOP)
        with mock.patch("skullpt.live.gemini_text",
                        side_effect=live.SeatCallFailed("gemini down")), \
             mock.patch("skullpt.live.ollama_text", return_value=desc_json):
            result = bench_mod.act(self.ws, "plan", {"words": "a shop panel"})
            self.assertIn("local fallback model", result["detail"])

    def test_act_ingest_creates_a_job(self):
        from test_v06 import _make_glb

        glb_path = Path(self._tmp.name) / "test-box.glb"
        glb_path.write_bytes(_make_glb())
        result = bench_mod.act(self.ws, "ingest", {"file": str(glb_path)})
        self.assertIn("job", result)
        self.assertIn("plan gate", result["detail"])
        job_dir = self.ws / "jobs" / result["job"]
        self.assertTrue((job_dir / "description.json").exists())

    def test_act_unreal_package_needs_a_built_job(self):
        job = _job(self.ws)
        with self.assertRaises(RuntimeError):
            bench_mod.act(self.ws, "unreal-package", {"job": job.dir.name})


# ---------------------------------------------------------------- T15 + T19

class ReportTests(unittest.TestCase):
    """T15: the debug report diagnoses without leaking secrets.
    T19: the build report surfaces the measured triangle count."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        bench_mod.invalidate_workspace_cache()

    def test_build_report_is_none_before_a_build(self):
        job = _job(self.ws)
        self.assertIsNone(bench_mod.build_report(self.ws, job.dir.name))

    def test_build_report_adds_triangle_counts(self):
        job = _job(self.ws)
        report = {"ok": True, "outputs": ["panel.blend"],
                  "machine_checks": {"ok": True, "checks": []}}
        (job.dir / "build-report.json").write_text(json.dumps(report))
        job.output_dir.mkdir(exist_ok=True)
        slug = job.dir.name
        (job.output_dir / f"{slug}.stats.json").write_text(json.dumps({
            "total_tris": 1234, "parts": {"panel": {"tris": 1234, "uv": True}}}))
        out = bench_mod.build_report(self.ws, job.dir.name)
        self.assertEqual(out["total_tris"], 1234)
        self.assertEqual(out["tris_by_part"]["panel"]["tris"], 1234)

    def test_debug_report_covers_the_essentials(self):
        job = _job(self.ws)
        text = bench_mod.debug_report(self.ws, job.dir.name)
        self.assertIn("SKULLPT debug report", text)
        self.assertIn("engines:", text)
        self.assertIn("free RAM:", text)
        self.assertIn(job.dir.name, text)

    def test_debug_report_scrubs_token_shaped_strings(self):
        job = _job(self.ws)
        job.log("tried token hf_abcdefghij1234567890 and key sk-abcdefghij123456")
        text = bench_mod.debug_report(self.ws, job.dir.name)
        self.assertNotIn("hf_abcdefghij1234567890", text)
        self.assertNotIn("sk-abcdefghij123456", text)
        self.assertIn("[redacted]", text)

    def test_debug_report_has_no_chat_transcript(self):
        (self.ws / "chat.jsonl").write_text(
            json.dumps({"role": "user", "text": "my secret words here"}) + "\n")
        text = bench_mod.debug_report(self.ws)
        self.assertNotIn("my secret words here", text)


# ---------------------------------------------------------------- T10 CLI

class CliManagementTests(unittest.TestCase):
    """The new CLI commands mirror the job-management actions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_delete_rename_duplicate_recover(self):
        job = _job(self.ws)
        cli_mod.main(["duplicate", str(self.ws), job.dir.name])
        self.assertTrue((self.ws / "jobs" / f"{job.dir.name}-copy").exists())
        cli_mod.main(["rename", str(self.ws), job.dir.name, "Cli Renamed"])
        self.assertTrue((self.ws / "jobs" / "cli-renamed").exists())
        cli_mod.main(["delete", str(self.ws), "cli-renamed"])
        self.assertFalse((self.ws / "jobs" / "cli-renamed").exists())
        cli_mod.main(["recover", str(self.ws)])  # no stranded jobs: prints, no crash


# ------------------------------------------------------- P1 + P2 + T11 ray

class ViewportGeometryTests(unittest.TestCase):
    """P1: normals are precomputed at tessellation time. P2: scenes
    are cached by description hash. T11: the ray picker hits the
    part under the ray and nothing else."""

    def test_meshes_carry_precomputed_normals(self):
        scene = qtbench.scene_meshes(SHOP)
        self.assertTrue(scene["meshes"])
        for mesh in scene["meshes"]:
            self.assertEqual(len(mesh["normals"]), len(mesh["tris"]))
            for normal in mesh["normals"]:
                length = sum(v * v for v in normal) ** 0.5
                self.assertAlmostEqual(length, 1.0, places=5)

    def test_cached_scene_meshes_returns_the_same_scene(self):
        qtbench.clear_mesh_cache()
        first = qtbench.cached_scene_meshes(SHOP)
        second = qtbench.cached_scene_meshes(SHOP)
        self.assertIs(first, second)
        changed = json.loads(json.dumps(SHOP))
        changed["parts"][0]["size_cm"] = [99, 99, 99]
        third = qtbench.cached_scene_meshes(changed)
        self.assertIsNot(first, third)
        qtbench.clear_mesh_cache()
        self.assertIsNot(qtbench.cached_scene_meshes(SHOP), first)

    def test_pick_mesh_hits_the_part_under_the_ray(self):
        scene = qtbench.scene_meshes(SHOP)
        mesh = scene["meshes"][0]
        mn, mx = mesh["bounds"]["min"], mesh["bounds"]["max"]
        cx, cy = (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2
        hit = qtbench.pick_mesh(scene["meshes"],
                                (cx, cy, mx[2] + 100.0), (0.0, 0.0, -1.0))
        self.assertEqual(hit, mesh["id"])
        miss = qtbench.pick_mesh(scene["meshes"],
                                 (cx + 10000.0, cy, mx[2] + 100.0),
                                 (0.0, 0.0, -1.0))
        self.assertIsNone(miss)

    def test_ray_triangle_miss_and_back_face(self):
        tri = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0))
        self.assertIsNotNone(qtbench._ray_triangle((2.0, 2.0, 5.0),
                                                   (0.0, 0.0, -1.0), *tri))
        self.assertIsNotNone(qtbench._ray_triangle((2.0, 2.0, -5.0),
                                                   (0.0, 0.0, 1.0), *tri))
        self.assertIsNone(qtbench._ray_triangle((20.0, 20.0, 5.0),
                                                (0.0, 0.0, -1.0), *tri))


# ------------------------------------------------------- R3 + R4 (no Qt)

class DesignTokenTests(unittest.TestCase):
    """R3/R4: the amber comes from the icon, every text pair passes
    WCAG AA, and the Qt files carry no em dashes (U33)."""

    def test_amber_is_sampled_from_the_icon(self):
        from skullpt import qtdesign

        self.assertEqual(qtdesign.sample_amber(), "#f5a623")
        icon_text = (ROOT / "skullpt-icon.svg").read_text()
        self.assertIn("#f5a623", icon_text)

    def test_every_text_pair_passes_wcag_aa(self):
        from skullpt import qtdesign

        for label, _fg, _bg, ratio, ok in qtdesign.contrast_table():
            self.assertTrue(ok, f"{label}: {ratio:.2f} (needs 4.5)")

    def test_stylesheet_uses_the_tokens(self):
        from skullpt import qtdesign

        sheet = qtdesign.build_stylesheet()
        self.assertIn(qtdesign.CANVAS, sheet)
        self.assertIn(qtdesign.BONE, sheet)
        self.assertIn(qtdesign.AMBER(), sheet)

    def test_no_em_dash_in_qt_files(self):
        for name in ("qtbench.py", "qtwindows.py", "qtdesign.py"):
            text = (ROOT / "skullpt" / name).read_text()
            self.assertNotIn("\u2014", text, f"{name} has an em dash")

    def test_lucide_icons_are_vendored(self):
        from skullpt import qtdesign

        icons_dir = ROOT / "skullpt" / "icons"
        self.assertTrue((icons_dir / "LICENSE").exists(),
                        "the vendored icon license is missing")
        for name in qtdesign.ICONS:
            path = icons_dir / f"{name}.svg"
            self.assertTrue(path.exists(), f"missing icon {name}.svg")
            self.assertTrue(path.read_text().lstrip().startswith("<svg"),
                            f"{name}.svg is not an SVG")

    def test_fonts_are_bundled(self):
        fonts_dir = ROOT / "skullpt" / "fonts"
        for name in ("Inter.ttf", "JetBrainsMono.ttf",
                     "OFL-Inter.txt", "OFL-JetBrainsMono.txt"):
            self.assertTrue((fonts_dir / name).exists(), f"missing {name}")
        self.assertGreater((fonts_dir / "Inter.ttf").stat().st_size, 100000)


# ------------------------------------------------------------- Qt tests (R5+)

try:
    from PySide6 import QtCore, QtTest, QtWidgets

    _QT_TESTS = True
except Exception:
    _QT_TESTS = False


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)
        self._windows: list[QtWidgets.QWidget] = []

    def tearDown(self):
        for window in self._windows:
            window.close()
        self.tmp.cleanup()

    def _bench(self):
        window = qtbench.BenchWindow(self.ws)
        window.show()
        self._windows.append(window)
        self._app.processEvents()
        return window

    def _pump(self, ms: int = 100):
        QtTest.QTest.qWait(ms)


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtWindowSplitTests(_QtCase):
    """R5/T13/T14: the Jobs home window, the split windows, the
    size cap, no auto-select, and the first-run panel."""

    def test_jobs_window_fits_the_screen(self):
        window = self._bench()
        self.assertLessEqual(window.width(), 1280)
        self.assertLessEqual(window.height(), 720)

    def test_nothing_is_selected_by_default(self):
        job = _job(self.ws)
        window = self._bench()
        self.assertIsNone(window._selected)
        self.assertFalse(window._approve_btn.isEnabled())
        self.assertFalse(window._build_btn.isEnabled())
        window.select_job(job.dir.name)
        self.assertEqual(window._selected, job.dir.name)
        self.assertTrue(window._approve_btn.isEnabled())

    def test_first_run_panel_shows_with_no_jobs(self):
        window = self._bench()
        self.assertTrue(window._first_run.isVisible())
        self.assertFalse(window._scroll.isVisible())
        _job(self.ws)
        window._refresh()
        self.assertFalse(window._first_run.isVisible())
        self.assertTrue(window._scroll.isVisible())

    def test_each_window_opens(self):
        from skullpt import qtwindows

        window = self._bench()
        _job(self.ws)
        window._refresh()
        for name, cls in (("render", qtwindows.RenderWindow),
                          ("character", qtwindows.CharacterWindow),
                          ("images", qtwindows.ImagesWindow),
                          ("library", qtwindows.LibraryWindow),
                          ("settings", qtwindows.SettingsWindow),
                          ("help", qtwindows.HelpWindow)):
            window._open_window(name)
            self._pump()
            opened = window._child_windows[name]
            self.assertIsInstance(opened, cls)
            self.assertTrue(opened.isVisible(), f"{name} did not show")
            self._windows.append(opened)

    def test_menu_bar_has_the_windows(self):
        window = self._bench()
        labels = [a.text() for a in window.menuBar().actions()]
        for expected in ("File", "Jobs", "Render", "Character", "Images",
                         "Library", "Settings", "Help"):
            self.assertIn(expected, labels)


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtJobCardTests(_QtCase):
    """R6/R7/T17: stage pills, bento cards, and incremental refresh
    that keeps widget identity."""

    def test_card_sync_keeps_widget_identity(self):
        job = _job(self.ws)
        window = self._bench()
        card = window._cards[job.dir.name]
        window._refresh()
        self.assertIs(window._cards[job.dir.name], card)
        job.approve()
        window._refresh()
        self.assertIs(window._cards[job.dir.name], card)
        self.assertEqual(card.pill.text(), "APPROVED")
        self.assertEqual(card._gate_btn.text(), "Build")

    def test_clicking_a_card_selects_the_job(self):
        job = _job(self.ws)
        window = self._bench()
        card = window._cards[job.dir.name]
        QtTest.QTest.mouseClick(card, QtCore.Qt.LeftButton)
        self.assertEqual(window._selected, job.dir.name)
        self.assertEqual(card.objectName(), "cardSelected")

    def test_pill_text_follows_state(self):
        from skullpt.qtwindows import StagePill

        pill = StagePill("draft")
        pill.set_state("awaiting_approval")
        self.assertEqual(pill.text(), "DRAFT")
        pill.set_state("approved")
        self.assertEqual(pill.text(), "APPROVED")
        pill.set_state("failed")
        self.assertEqual(pill.text(), "FAILED")
        pill.set_stage("build")
        self.assertEqual(pill.text(), "BUILD")

    def test_toast_shows_and_goes_away(self):
        from skullpt.qtwindows import Toast

        window = self._bench()
        toast = Toast.show_toast(window, "Test note.")
        self.assertTrue(toast.isVisible())
        self.assertIn("Test note.", toast.findChild(QtWidgets.QLabel).text())
        toast.close()


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtRenderWindowTests(_QtCase):
    """R9/T11: the Render window's legend, triangle count, parts
    list, and part editor."""

    def _render(self, job=None):
        window = self._bench()
        if job is not None:
            window.select_job(job.dir.name)
        render = window._render_window
        render.show()
        self._windows.append(render)
        self._pump()
        return window, render

    def test_legend_shows_the_true_grid_step(self):
        job = _job(self.ws)
        _window, render = self._render(job)
        step = render.viewport.grid_step()
        self.assertIn(f"{step:.1f} cm", render._legend.text())
        self.assertIn("X red", render._legend.text())

    def test_tri_count_is_an_estimate_before_a_build(self):
        job = _job(self.ws)
        _window, render = self._render(job)
        self.assertIn("estimate", render._tri_label.text())
        self.assertIn("Triangles:", render._tri_label.text())

    def test_parts_list_and_editor(self):
        job = _job(self.ws)
        _window, render = self._render(job)
        self.assertGreater(render._parts_list.count(), 0)
        part_id = SHOP["parts"][0]["id"]
        render._select_part(part_id)
        self.assertTrue(render._save_part_btn.isEnabled())
        self.assertIn(part_id, render._editor_note.text())

    def test_saving_a_part_edits_the_description(self):
        job = _job(self.ws)
        _window, render = self._render(job)
        part_id = SHOP["parts"][0]["id"]
        render._select_part(part_id)
        render._material_combo.setCurrentText("maple")
        render._save_part()
        desc = job.load_description()
        part = next(p for p in desc["parts"] if p["id"] == part_id)
        self.assertEqual(part["material"], "maple")

    def test_pick_hits_the_part_at_the_center(self):
        job = _job(self.ws)
        _window, render = self._render(job)
        viewport = render.viewport
        ctx = viewport.context()
        if ctx is None or not ctx.isValid():
            self.skipTest("no GL context on this platform (needs Xvfb + xcb)")
        self._pump(200)
        hit = viewport.pick(viewport.width() // 2, viewport.height() // 2)
        self.assertIsNotNone(hit, "the center pixel hit no part")
        from skullpt.expand import expand_parts

        self.assertIn(hit, {p["id"] for p in expand_parts(SHOP)})


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtSettingsTokenTests(_QtCase):
    """T9: the token field saves to the gitignored file and clears."""

    def test_token_save_and_clear(self):
        from skullpt import qtwindows, tokenfile

        class _BenchStub:
            def refresh(self):
                pass

        window = qtwindows.SettingsWindow(_BenchStub(), self.ws)
        window.show()
        self._windows.append(window)
        self._pump()
        self.assertIn("No token saved", window._token_status.text())
        window._token_field.setText("hf_faketoken123")
        for button in window.findChildren(QtWidgets.QPushButton):
            if button.text() == "Save token":
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                break
        self._pump()
        self.assertTrue(tokenfile.has_token(self.ws))
        self.assertEqual(tokenfile.load_token(self.ws), "hf_faketoken123")
        self.assertIn("Token saved", window._token_status.text())
        for button in window.findChildren(QtWidgets.QPushButton):
            if button.text() == "Clear token":
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                break
        self._pump()
        self.assertFalse(tokenfile.has_token(self.ws))


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtDebugReportTests(_QtCase):
    """T15: the debug report dialog shows the report."""

    def test_debug_report_dialog(self):
        from skullpt import qtwindows

        job = _job(self.ws)
        dialog = qtwindows.DebugReportDialog(self.ws, job.dir.name)
        self._windows.append(dialog)
        dialog.show()
        self._pump()
        text = dialog._text.toPlainText()
        self.assertIn("SKULLPT debug report", text)
        self.assertIn(job.dir.name, text)
        self.assertIn("free RAM", text)

    def test_build_report_dialog_before_a_build(self):
        from skullpt import qtwindows

        job = _job(self.ws)
        dialog = qtwindows.BuildReportDialog(self.ws, job.dir.name)
        self._windows.append(dialog)
        dialog.show()
        self._pump()
        text = dialog.findChild(QtWidgets.QTextEdit).toPlainText()
        self.assertIn("has not built yet", text)


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtGeometryTests(_QtCase):
    """T13: a window's geometry round-trips through QSettings."""

    def test_geometry_round_trip(self):
        from skullpt import qtwindows

        window = qtwindows.HelpWindow()
        window.resize(700, 500)
        window.move(40, 30)
        qtwindows.save_geometry(window, "HelpWindow")
        window.close()
        again = qtwindows.HelpWindow()
        self._windows.append(again)
        qtwindows.restore_geometry(again, "HelpWindow")
        self.assertEqual(again.width(), 700)
        self.assertEqual(again.height(), 500)


if __name__ == "__main__":
    unittest.main()

