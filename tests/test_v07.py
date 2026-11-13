"""Item: the image step — prompt -> candidates -> keep/reject -> reference.

A fake sd-cli (a tiny script that writes a PNG where it is told to)
proves the whole flow without the 2.1 GB model; the real engine runs
in RealEngineTests when one is installed, and in the sandbox proof.
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
import unittest.mock
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import cli, imagegen, picker, settings as settings_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt.critic import GeminiCritic  # noqa: E402
from skullpt.governor import GovernorError  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000000020001e221bc330000"
    "000049454e44ae426082")

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

SCENE_DESC = {
    "name": "Ref Job", "kind": "scene",
    "parts": [{"id": "floor", "type": "floor", "shape": "box",
               "position_cm": [0, 0, 0], "size_cm": [200, 200, 10]}],
    "constraints": [], "budget": {}, "rubric": ["looks like the reference"],
    "style": {},
}


class _FakeEnv(unittest.TestCase):
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
        # The sandbox VM's free RAM swings with other work; the fake
        # engine uses none, so pin the governor's reading. The lock-file
        # and floor behaviours are still exercised (see below).
        self._ram = unittest.mock.patch.object(
            imagegen.governor, "free_ram_mb", return_value=8192)
        self._ram.start()

    def tearDown(self):
        self._ram.stop()
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class MissingPiecesTests(_FakeEnv):
    def test_missing_engine_fails_loud(self):
        os.environ.pop("DESIGNSHELL_SD_CLI")
        with unittest.mock.patch.object(imagegen, "find_engine", return_value=None):
            batch, bdir = imagegen.new_batch(self.ws, "a red mug")
            with self.assertRaises(imagegen.ImageGenError) as ctx:
                imagegen.generate(self.ws, bdir)
            self.assertIn("not installed", str(ctx.exception))
            # v1.3: the fix names the installer's image-engine option,
            # never a terminal command.
            self.assertIn("image-engine option", str(ctx.exception))
            self.assertIn("README", str(ctx.exception))
            self.assertNotIn("./install.sh", str(ctx.exception))

    def test_missing_model_fails_loud(self):
        os.environ.pop("DESIGNSHELL_SD_MODEL")
        with unittest.mock.patch.object(imagegen, "find_model", return_value=None):
            batch, bdir = imagegen.new_batch(self.ws, "a red mug")
            with self.assertRaises(imagegen.ImageGenError) as ctx:
                imagegen.generate(self.ws, bdir)
            self.assertIn(imagegen.MODEL_FILENAME, str(ctx.exception))


class BatchFlowTests(_FakeEnv):
    def _job(self) -> Job:
        return Job.create(self.ws / "jobs", dict(SCENE_DESC))

    def test_generate_three_pending_distinct_seeds(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=3)
        batch = imagegen.generate(self.ws, bdir)
        self.assertEqual(len(batch["candidates"]), 3)
        self.assertTrue(all(c["status"] == "pending" for c in batch["candidates"]))
        self.assertEqual(len({c["seed"] for c in batch["candidates"]}), 3)
        for c in batch["candidates"]:
            self.assertTrue((bdir / c["file"]).exists())

    def test_reject_deletes_file_and_records_seed(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool")
        batch = imagegen.generate(self.ws, bdir)
        victim = batch["candidates"][1]
        batch = imagegen.decide(self.ws, bdir, victim["file"], "reject")
        self.assertFalse((bdir / victim["file"]).exists())
        self.assertIn(victim["seed"], batch["rejected_seeds"])
        record = next(c for c in batch["candidates"] if c["file"] == victim["file"])
        self.assertEqual(record["status"], "rejected")

    def test_keep_promotes_to_job_reference(self):
        job = self._job()
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", job=job.dir.name)
        batch = imagegen.generate(self.ws, bdir)
        keeper = batch["candidates"][0]
        batch = imagegen.decide(self.ws, bdir, keeper["file"], "keep")
        self.assertEqual(batch["reference_file"], keeper["file"])
        desc = job.load_description()
        ref = desc["reference"]
        self.assertEqual(ref["source"], "generated")
        self.assertEqual(ref["seed"], keeper["seed"])
        self.assertEqual(ref["prompt"], "a wooden stool")
        self.assertTrue(Path(ref["stored_at"]).exists())
        self.assertEqual(Path(ref["stored_at"]).parent, job.dir)

    def test_reroll_never_repeats_rejected_seed(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool")
        batch = imagegen.generate(self.ws, bdir)
        rejected_seed = batch["candidates"][0]["seed"]
        imagegen.decide(self.ws, bdir, batch["candidates"][0]["file"], "reject")
        batch = imagegen.generate(self.ws, bdir)  # reroll round
        new_seeds = [c["seed"] for c in batch["candidates"]]
        self.assertEqual(len(new_seeds), 6)
        self.assertNotIn(rejected_seed, new_seeds[3:])
        self.assertEqual(len(set(new_seeds)), 6)

    def test_governor_lock_blocks_generation(self):
        (self.ws / ".heavy.lock").write_text(str(os.getpid()))
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool")
        with self.assertRaises(GovernorError):
            imagegen.generate(self.ws, bdir)

    def test_governor_ram_floor_blocks_generation(self):
        self._ram.stop()
        low = unittest.mock.patch.object(
            imagegen.governor, "free_ram_mb", return_value=10)
        low.start()
        try:
            batch, bdir = imagegen.new_batch(self.ws, "a wooden stool")
            with self.assertRaises(GovernorError) as ctx:
                imagegen.generate(self.ws, bdir)
            self.assertIn("RAM free", str(ctx.exception))
        finally:
            low.stop()
            self._ram.start()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_on(self):
        self.assertTrue(settings_mod.critic_reference_enabled(self.ws, {}))

    def test_workspace_toggle_off(self):
        settings = settings_mod.load_settings(self.ws)
        settings["critic_reference_check"] = False
        settings_mod.save_settings(self.ws, settings)
        self.assertFalse(settings_mod.critic_reference_enabled(self.ws, {}))

    def test_job_override_beats_workspace(self):
        settings = settings_mod.load_settings(self.ws)
        settings["critic_reference_check"] = False
        settings_mod.save_settings(self.ws, settings)
        self.assertTrue(settings_mod.critic_reference_enabled(
            self.ws, {"critic_reference": True}))
        settings["critic_reference_check"] = True
        settings_mod.save_settings(self.ws, settings)
        self.assertFalse(settings_mod.critic_reference_enabled(
            self.ws, {"critic_reference": False}))

    def test_reference_path_resolution(self):
        target = self.tmp / "ref.png"
        target.write_bytes(TINY_PNG)
        desc = {"reference": {"stored_at": str(target)}}
        self.assertEqual(settings_mod.reference_image_path(desc), target)
        self.assertIsNone(settings_mod.reference_image_path({}))
        self.assertIsNone(settings_mod.reference_image_path(
            {"reference": {"stored_at": str(self.tmp / "nope.png")}}))


class PickerTests(_FakeEnv):
    def setUp(self):
        super().setUp()
        batch, self.bdir = imagegen.new_batch(self.ws, "a brass lamp")
        imagegen.generate(self.ws, self.bdir)
        self.server = picker.make_server(self.bdir, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status, resp.read()

    def test_page_and_image_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Image Picker", body)
        status, body = self._get("/img/cand-01.png")
        self.assertEqual(status, 200)
        self.assertEqual(body[:4], b"\x89PNG")

    def test_img_guard_rejects_non_candidates(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/img/batch.json")
        self.assertEqual(ctx.exception.code, 404)

    def test_decide_over_http(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/decide",
            data=json.dumps({"file": "cand-02.png", "decision": "reject"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        self.assertTrue(data["ok"])
        batch = imagegen.load_batch(self.bdir)
        record = next(c for c in batch["candidates"] if c["file"] == "cand-02.png")
        self.assertEqual(record["status"], "rejected")
        self.assertFalse((self.bdir / "cand-02.png").exists())

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_page_script_parses(self):
        batch = imagegen.load_batch(self.bdir)
        page = picker.render_page(batch, live=True)
        script = re.search(r"<script>\n(.*)\n</script>", page, re.S).group(1)
        script_file = self.tmp / "picker-script.js"
        script_file.write_text(script)
        proc = subprocess.run(["node", "--check", str(script_file)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class _CaptureGemini(BaseHTTPRequestHandler):
    bodies: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).bodies.append(body)
        reply = {"fixes": []}
        raw = json.dumps({"candidates": [{"content": {"parts": [
            {"text": json.dumps(reply)}]}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class CriticReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.render = self.tmp / "render.png"
        self.render.write_bytes(TINY_PNG)
        _CaptureGemini.bodies = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureGemini)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._env = dict(os.environ)
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["DESIGNSHELL_GEMINI_URL"] = (
            f"http://127.0.0.1:{self.server.server_address[1]}/mock")

    def tearDown(self):
        self.server.shutdown()
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parts(self):
        return _CaptureGemini.bodies[-1]["contents"][0]["parts"]

    def test_reference_sends_two_images_and_names_order(self):
        critic = GeminiCritic({"env_key": "GEMINI_API_KEY", "model": "mock"})
        result = critic.critique(dict(SCENE_DESC), [str(self.render)],
                                 reference_png=TINY_PNG)
        parts = self._parts()
        images = [p for p in parts if "inline_data" in p]
        self.assertEqual(len(images), 2)
        self.assertIn("FIRST is the reference", parts[0]["text"])
        self.assertTrue(result["reference_compared"])

    def test_no_reference_sends_one_image(self):
        critic = GeminiCritic({"env_key": "GEMINI_API_KEY", "model": "mock"})
        result = critic.critique(dict(SCENE_DESC), [str(self.render)])
        images = [p for p in self._parts() if "inline_data" in p]
        self.assertEqual(len(images), 1)
        self.assertFalse(result["reference_compared"])


class CliTests(_FakeEnv):
    def test_settings_command_roundtrip(self):
        self.assertEqual(cli.main(["settings", str(self.ws)]), 0)
        self.assertEqual(cli.main(["settings", str(self.ws),
                                   "--critic-reference", "off"]), 0)
        self.assertFalse(settings_mod.load_settings(self.ws)["critic_reference_check"])
        self.assertEqual(cli.main(["settings", str(self.ws),
                                   "--critic-reference", "on"]), 0)
        self.assertTrue(settings_mod.load_settings(self.ws)["critic_reference_check"])


@unittest.skipIf(os.environ.get("DESIGNSHELL_SKIP_E2E"),
                 "DESIGNSHELL_SKIP_E2E set")
class RealEngineTests(unittest.TestCase):
    def test_real_generation_lands_a_png(self):
        engine = imagegen.find_engine()
        model = imagegen.find_model()
        if engine is None or model is None:
            self.skipTest("real sd engine/model not installed here")
        tmp = Path(tempfile.mkdtemp())
        try:
            ws = tmp / "ws"
            (ws / "jobs").mkdir(parents=True)
            batch, bdir = imagegen.new_batch(ws, "a wooden stool", count=1, size=256)
            batch = imagegen.generate(ws, bdir, count=1, timeout_s=1500)
            landed = [c for c in batch["candidates"] if c["status"] == "pending"]
            self.assertEqual(len(landed), 1, batch["candidates"])
            width, height, fmt = imagegen.image_size(bdir / landed[0]["file"])
            self.assertEqual((width, height, fmt), (256, 256, "png"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
