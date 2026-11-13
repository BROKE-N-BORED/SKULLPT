"""The Bench — the shell's window (v0.8).

The bench is a face on the same functions the CLI calls; these tests
drive it the way the page does (actions + state + file serving) with
a fake image engine, and check the page script parses.
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
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
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
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

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


class BenchServerTests(BenchCase):
    def setUp(self):
        super().setUp()
        self.server = bench.make_server(self.ws, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                    timeout=5) as resp:
            return resp.status, resp.read()

    def _post(self, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/act",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_page_state_and_action_roundtrip(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Bench", body)
        status, body = self._get("/api/state")
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertIn("engines", data["state"])
        result = self._post({"action": "new-example",
                             "example": "shop-panel.description.json"})
        self.assertTrue(result["ok"])
        status, body = self._get("/api/state")
        self.assertEqual(len(json.loads(body)["state"]["jobs"]), 1)

    def test_file_serving_and_traversal_guard(self):
        bench.act(self.ws, "new-example", {"example": "shop-panel.description.json"})
        job_name = bench.workspace_state(self.ws)["jobs"][0]["name"]
        bench.act(self.ws, "approve", {"job": job_name})
        run = wait_run(bench.act(self.ws, "build", {"job": job_name})["run"])
        self.assertEqual(run["status"], "done", run)
        # a text output serves fine
        status, body = self._get(f"/files/job/{job_name}/tavern-shop-panel.html")
        self.assertEqual(status, 200)
        self.assertIn(b"<", body)
        # traversal is refused
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get(f"/files/job/{job_name}/..%2F..%2Fseats.json")
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/files/batch/nope/cand-01.png")

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_page_script_parses(self):
        page = bench.render_page()
        script = re.search(r"<script>\n(.*)\n</script>", page, re.S).group(1)
        script_file = self.tmp / "bench-script.js"
        script_file.write_text(script)
        proc = subprocess.run(["node", "--check", str(script_file)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
