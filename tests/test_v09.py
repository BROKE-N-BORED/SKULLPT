"""The Bench as ONE window (v0.9): project folders, the Live Render
Window inside the bench (per-job routes, camera saves into the job),
and the chat panel (commands route to the real actions; open chat
needs the planner seat and says so). Fake image engine, no keys.
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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import bench, imagegen  # noqa: E402
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


def wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class BenchV9Case(unittest.TestCase):
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
        bench.act(self.ws, "new-example",
                  {"example": "tavern-room.description.json"})
        self.job_name = bench.workspace_state(self.ws)["jobs"][0]["name"]

    def tearDown(self):
        self._ram.stop()
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class StateTests(BenchV9Case):
    def test_state_has_folders_and_chat(self):
        state = bench.workspace_state(self.ws)
        self.assertIn("folders", state)
        self.assertIn("library", state["folders"])
        self.assertIn("intake", state["folders"])
        self.assertEqual(state["chat"], [])


class RenderWindowInBenchTests(BenchV9Case):
    def test_view_page_endpoints_rewritten_to_the_job(self):
        page = bench.job_view_page(self.ws, self.job_name)
        self.assertIn(f"/api/scene-job/{self.job_name}", page)
        self.assertIn(f"/api/cameras-job/{self.job_name}", page)
        self.assertNotIn('fetch("/api/scene")', page)

    def test_view_page_unknown_job_raises(self):
        with self.assertRaises(ValueError):
            bench.job_view_page(self.ws, "../nope")


class ChatTests(BenchV9Case):
    def test_status_and_help(self):
        reply = bench.chat_reply(self.ws, "status")
        self.assertIn("Blender", reply["reply"])
        reply = bench.chat_reply(self.ws, "help")
        self.assertIn("make", reply["reply"])

    def test_make_without_key_refuses_plainly(self):
        reply = bench.chat_reply(self.ws, "make a market stall")
        self.assertIn("GEMINI_API_KEY", reply["reply"])

    def test_open_chat_without_key_is_honest(self):
        reply = bench.chat_reply(self.ws, "what do you think of elm wood?")
        self.assertIn("I take commands", reply["reply"])
        self.assertIn("won't fake", reply["reply"])

    def test_approve_then_actions_offer_build(self):
        reply = bench.chat_reply(self.ws, "approve", selected=self.job_name)
        self.assertIn("approved", reply["reply"])
        self.assertEqual(Job(self.ws / "jobs" / self.job_name).state, "approved")
        self.assertTrue(any(a["action"] == "build" for a in reply["actions"]))

    def test_approve_with_nothing_waiting(self):
        bench.act(self.ws, "approve", {"job": self.job_name})
        reply = bench.chat_reply(self.ws, "approve", selected=self.job_name)
        self.assertIn("No job is waiting", reply["reply"])

    def test_picture_command_generates_a_batch(self):
        reply = bench.chat_reply(self.ws, "picture of a brass lamp",
                                 selected=self.job_name)
        self.assertIn("Generating 3 candidates", reply["reply"])
        landed = wait_for(lambda: len(bench.workspace_state(self.ws)["batches"]) == 1
                          and len(bench.workspace_state(self.ws)["batches"][0]["candidates"]) == 3)
        self.assertTrue(landed)


class BenchV9ServerTests(BenchV9Case):
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

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def test_render_window_routes(self):
        status, body = self._get(f"/view/{self.job_name}")
        self.assertEqual(status, 200)
        self.assertIn(f"/api/scene-job/{self.job_name}".encode(), body)
        status, body = self._get(f"/api/scene-job/{self.job_name}")
        scene = json.loads(body)
        self.assertIn("boxes", scene)
        self.assertGreaterEqual(len(scene["boxes"]), 1)

    def test_camera_save_lands_in_the_job(self):
        result = self._post(f"/api/cameras-job/{self.job_name}",
                            {"name": "hero", "position_cm": [300, 200, 300],
                             "look_at_cm": [0, 50, 0]})
        self.assertTrue(result["ok"])
        desc = Job(self.ws / "jobs" / self.job_name).load_description()
        self.assertIn("hero", [c["name"] for c in desc.get("cameras", [])])
        result = self._post(f"/api/cameras-job/{self.job_name}",
                            {"delete": "hero"})
        self.assertTrue(result["ok"])
        desc = Job(self.ws / "jobs" / self.job_name).load_description()
        self.assertNotIn("hero", [c["name"] for c in desc.get("cameras", [])])

    def test_chat_endpoint_records_history(self):
        result = self._post("/api/chat", {"message": "status", "job": self.job_name})
        self.assertTrue(result["ok"])
        self.assertIn("Blender", result["reply"])
        state = bench.workspace_state(self.ws)
        self.assertEqual(len(state["chat"]), 2)
        self.assertEqual(state["chat"][0]["role"], "user")

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_page_script_parses(self):
        page = bench.render_page()
        script = re.search(r"<script>\n(.*)\n</script>", page, re.S).group(1)
        script_file = self.tmp / "bench9-script.js"
        script_file.write_text(script)
        proc = subprocess.run(["node", "--check", str(script_file)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class BenchAutoOpenTests(unittest.TestCase):
    """v0.9.1: starting the Bench opens the window for the user — a
    server that only prints a URL never shows anyone anything."""

    def test_open_browser_calls_webbrowser(self):
        calls = []
        fake = unittest.mock.Mock()
        fake.open.side_effect = lambda url: calls.append(url)
        with unittest.mock.patch.dict(sys.modules, {"webbrowser": fake}):
            with unittest.mock.patch.dict(os.environ, {}, clear=True):
                bench._open_browser("http://127.0.0.1:8770/")
        self.assertEqual(calls, ["http://127.0.0.1:8770/"])

    def test_open_browser_respects_no_browser_env(self):
        fake = unittest.mock.Mock()
        with unittest.mock.patch.dict(sys.modules, {"webbrowser": fake}):
            with unittest.mock.patch.dict(
                    os.environ, {"DESIGNSHELL_NO_BROWSER": "1"}):
                bench._open_browser("http://127.0.0.1:8770/")
        fake.open.assert_not_called()

    def test_open_browser_failure_never_raises(self):
        fake = unittest.mock.Mock()
        fake.open.side_effect = RuntimeError("no browser here")
        with unittest.mock.patch.dict(sys.modules, {"webbrowser": fake}):
            with unittest.mock.patch.dict(os.environ, {}, clear=True):
                bench._open_browser("http://127.0.0.1:8770/")  # must not raise


if __name__ == "__main__":
    unittest.main()
