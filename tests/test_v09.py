"""The Bench action engine (v0.9): workspace state and the chat panel
(commands route to the real actions; open chat needs the planner seat
and says so). Fake image engine, no keys.
(The web page and per-job routes these tests once drove were removed
in v1.9; camera saves now go through the Qt viewport.)
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


class ChatTests(BenchV9Case):
    def test_status_and_help(self):
        reply = bench.chat_reply(self.ws, "status")
        self.assertIn("Blender", reply["reply"])
        reply = bench.chat_reply(self.ws, "help")
        self.assertIn("make", reply["reply"])

    def test_make_without_key_refuses_plainly(self):
        reply = bench.chat_reply(self.ws, "make a market stall")
        # v1.8: the fix is in-app (paste the key in Settings), not an
        # environment variable — the refusal says so.
        self.assertIn("has no key", reply["reply"])
        self.assertIn("Bench Settings window", reply["reply"])

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


if __name__ == "__main__":
    unittest.main()
