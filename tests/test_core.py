"""Skeleton test suite. Run:  python3 -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import constraints as con_mod
from skullpt import description as desc_mod
from skullpt import governor
from skullpt.builder import build_job
from skullpt.critic import CriticNotConfigured, GeminiCritic, OfflineCritic
from skullpt.jobs import GateError, Job
from skullpt.library import Library

ROOT = Path(__file__).resolve().parent.parent
SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())
ROOM = json.loads((ROOT / "examples" / "tavern-room.description.json").read_text())


class DescriptionTests(unittest.TestCase):
    def test_examples_are_valid(self):
        self.assertEqual(desc_mod.validate(SHOP), [])
        self.assertEqual(desc_mod.validate(ROOM), [])

    def test_missing_fields_reported(self):
        problems = desc_mod.validate({"kind": "scene"})
        self.assertTrue(any("name" in p for p in problems))
        self.assertTrue(any("parts" in p for p in problems))

    def test_duplicate_part_ids_rejected(self):
        bad = json.loads(json.dumps(SHOP))
        bad["parts"].append(dict(bad["parts"][1]))
        self.assertTrue(any("duplicate" in p for p in desc_mod.validate(bad)))

    def test_constraint_referencing_unknown_part_rejected(self):
        bad = json.loads(json.dumps(SHOP))
        bad["constraints"] = [{"type": "text_fits", "part": "nope"}]
        self.assertTrue(any("unknown part" in p for p in desc_mod.validate(bad)))

    def test_summary_is_plain_english(self):
        text = desc_mod.summarize(SHOP)
        self.assertIn("Tavern Shop Panel", text)
        self.assertIn("buy (button)", text)


class ConstraintTests(unittest.TestCase):
    def test_room_constraints_pass(self):
        results = con_mod.check_constraints(ROOM)
        self.assertTrue(con_mod.all_ok(results), results)

    def test_floating_part_fails(self):
        bad = json.loads(json.dumps(ROOM))
        for part in bad["parts"]:
            if part["id"] == "lamp":
                part["position_cm"][2] = 150  # hovering 30 cm over the counter
        results = con_mod.check_constraints(bad)
        self.assertFalse(con_mod.all_ok(results))

    def test_text_overflow_fails(self):
        bad = json.loads(json.dumps(SHOP))
        for part in bad["parts"]:
            if part["id"] == "buy":
                part["text"] = "Buy one get one free today only"
        results = con_mod.check_constraints(bad)
        self.assertFalse(con_mod.all_ok(results))


class GovernorTests(unittest.TestCase):
    def test_budget_pass_and_fail(self):
        self.assertEqual(governor.check_budget(ROOM), [])
        bad = json.loads(json.dumps(ROOM))
        bad["budget"]["max_tris"] = 10  # the room estimates 36 triangles
        self.assertTrue(governor.check_budget(bad))

    def test_heavy_lock_blocks_second_holder(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            first = governor.HeavyJobLock(lock_path)
            first.acquire(min_free_mb=0)
            try:
                second = governor.HeavyJobLock(lock_path)
                with self.assertRaises(governor.GovernorError):
                    second.acquire(min_free_mb=0)
            finally:
                first.release()
            third = governor.HeavyJobLock(lock_path)  # free again after release
            third.acquire(min_free_mb=0)
            third.release()


class GateAndBuildTests(unittest.TestCase):
    def test_build_refuses_before_approval(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "jobs").mkdir()
            job = Job.create(ws / "jobs", json.loads(json.dumps(SHOP)))
            with self.assertRaises(GateError):
                build_job(job, ws)

    def test_full_loop_files_into_library(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "jobs").mkdir()
            (ws / "library").mkdir()
            job = Job.create(ws / "jobs", json.loads(json.dumps(SHOP)))
            job.approve()
            report = build_job(job, ws)
            self.assertTrue(report["ok"], report)
            names = set(report["outputs"])
            self.assertTrue(any(n.endswith(".html") for n in names))
            self.assertTrue(any(n.endswith(".svg") for n in names))  # one description, many outputs
            html_text = (job.output_dir / next(n for n in names if n.endswith(".html"))).read_text()
            for part in SHOP["parts"]:
                self.assertIn(f'id="{part["id"]}"', html_text)
            self.assertEqual(job.state, "awaiting_review")
            lib = Library(ws / "library")
            dest = lib.file_job(job, report, thumbnail=next(n for n in names if n.endswith(".svg")))
            self.assertTrue((dest / "manifest.json").exists())
            self.assertTrue((dest / "description.json").exists())
            self.assertEqual(job.state, "filed")
            hits = lib.search("tavern")
            self.assertEqual(len(hits), 1)
            self.assertEqual(lib.search("spaceship"), [])

    def test_over_budget_job_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "jobs").mkdir()
            bad = json.loads(json.dumps(ROOM))
            bad["budget"] = {"max_tris": 10}
            bad["kind"] = "ui_component"  # exporters irrelevant; budget gate fires first
            job = Job.create(ws / "jobs", bad)
            job.approve()
            report = build_job(job, ws)
            self.assertFalse(report["ok"])
            self.assertEqual(report["stage"], "budget")


class CriticTests(unittest.TestCase):
    def test_offline_critic_never_claims_vision(self):
        result = OfflineCritic().critique(SHOP, ["x.svg"])
        self.assertFalse(result["vision"])

    def test_gemini_critic_refuses_without_key(self):
        critic = GeminiCritic({"env_key": "DEFINITELY_NOT_SET_XYZ"})
        with self.assertRaises(CriticNotConfigured):
            critic.critique(SHOP, ["x.svg"])


if __name__ == "__main__":
    unittest.main()
