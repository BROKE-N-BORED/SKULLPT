"""The Bench action engine (v0.8).

The bench is a face on the same functions the CLI calls; these tests
drive it through actions + state with a fake image engine.
(The web page these tests once drove was removed in v1.9.)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import bench, imagegen  # noqa: E402
from skullpt import settings as settings_mod  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

FAKE_SD = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
png = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000000020001e221bc330000"
    "000049454e44ae426082")
open(out, "wb").write(png)
"""


def wait_run(run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = bench._RUNS.get(run_id)
        if run and run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


class BenchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        (self.ws / "library").mkdir(parents=True)
        self.fake = self.tmp / "sd-cli"
        self.fake.write_text(FAKE_SD)
        self.fake.chmod(0o755)
        self.model = self.tmp / "model.gguf"
        self.model.write_bytes(b"GGUF-fake")
        self._env = dict(os.environ)
        os.environ["DESIGNSHELL_SD_CLI"] = str(self.fake)
        os.environ["DESIGNSHELL_SD_MODEL"] = str(self.model)
        os.environ.pop("GEMINI_API_KEY", None)
        self._ram = unittest.mock.patch.object(
            imagegen.governor, "free_ram_mb", return_value=8192)
        self._ram.start()

    def tearDown(self):
        self._ram.stop()
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class BenchFlowTests(BenchCase):
    def test_example_job_full_loop_through_bench(self):
        result = bench.act(self.ws, "new-example",
                           {"example": "shop-panel.description.json"})
        self.assertTrue(result["ok"])
        state = bench.workspace_state(self.ws)
        self.assertEqual(len(state["jobs"]), 1)
        job_name = state["jobs"][0]["name"]
        self.assertEqual(state["jobs"][0]["state"], "awaiting_approval")

        bench.act(self.ws, "approve", {"job": job_name})
        result = bench.act(self.ws, "build", {"job": job_name})
        run = wait_run(result["run"])
        self.assertEqual(run["status"], "done", run)

        state = bench.workspace_state(self.ws)
        job = state["jobs"][0]
        self.assertEqual(job["state"], "awaiting_review")
        self.assertIn("tavern-shop-panel.html", job["outputs"])
        self.assertTrue(job["report"]["ok"])

        result = bench.act(self.ws, "file", {"job": job_name})
        self.assertTrue(result["ok"])
        state = bench.workspace_state(self.ws)
        self.assertEqual(len(state["library"]), 1)
        self.assertEqual(state["jobs"][0]["state"], "filed")

    def test_plan_without_key_refuses_plainly(self):
        with self.assertRaises(Exception) as ctx:
            bench.act(self.ws, "plan", {"words": "a stall", "kind": "scene"})
        # v1.8: the fix is in-app (paste the key in Settings), not an
        # environment variable — the refusal says so.
        self.assertIn("has no key", str(ctx.exception))
        self.assertIn("Bench Settings window", str(ctx.exception))

    def test_images_and_decide_through_bench(self):
        bench.act(self.ws, "new-example", {"example": "shop-panel.description.json"})
        job_name = bench.workspace_state(self.ws)["jobs"][0]["name"]
        result = bench.act(self.ws, "images",
                           {"prompt": "a brass lamp", "job": job_name})
        run = wait_run(result["run"])
        self.assertEqual(run["status"], "done", run)
        state = bench.workspace_state(self.ws)
        self.assertEqual(len(state["batches"]), 1)
        batch = state["batches"][0]
        self.assertEqual(len(batch["candidates"]), 3)
        bench.act(self.ws, "decide", {"batch": batch["slug"],
                                      "file": "cand-01.png", "decision": "keep"})
        job = Job(self.ws / "jobs" / job_name)
        desc = job.load_description()
        self.assertEqual(desc["reference"]["source"], "generated")
        state = bench.workspace_state(self.ws)
        self.assertTrue(state["jobs"][0]["has_reference"])

    def test_settings_toggle_through_bench(self):
        bench.act(self.ws, "settings", {"critic_reference": False})
        self.assertFalse(settings_mod.load_settings(self.ws)["critic_reference_check"])
        bench.act(self.ws, "settings", {"critic_reference": True})
        self.assertTrue(settings_mod.load_settings(self.ws)["critic_reference_check"])

    def test_unknown_job_and_batch_rejected(self):
        with self.assertRaises(ValueError):
            bench.act(self.ws, "approve", {"job": "../nope"})
        with self.assertRaises(ValueError):
            bench.act(self.ws, "decide", {"batch": "../nope", "file": "x.png",
                                          "decision": "keep"})


if __name__ == "__main__":
    unittest.main()
