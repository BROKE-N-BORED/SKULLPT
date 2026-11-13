"""v1.1 tests: the cloud mesh step (photo -> textured GLB on the
TRELLIS.2 Hugging Face Space, then ingest into a job).

Everything runs against a LOCAL MOCK Gradio server implementing the
same HTTP shapes the real Space exposes (upload, /gradio_api/call/v2,
SSE event stream, file download) — no real Hugging Face call happens
in the sandbox. The first real mesh is a real-PC verdict.
"""
import base64
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from designshell import bench, cli, cloudmesh, description as desc_mod
from designshell import intake
from designshell.jobs import Job

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _make_glb() -> bytes:
    """A small valid GLB: one box node, like test_v06's fixture."""
    gltf = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "Mesh", "mesh": 0, "translation": [0, 0, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"min": [-0.5, -0.5, 0], "max": [0.5, 0.5, 1.0]}],
    }
    raw = json.dumps(gltf).encode()
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total = 12 + 8 + len(raw)
    return struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(raw), b"JSON") + raw


GLB_BYTES = _make_glb()
MASKED_IMAGE = {"path": "/tmp/gradio/mock/masked.png",
                "url": "/gradio_api/file=/tmp/gradio/mock/masked.png",
                "orig_name": "masked.png", "size": 100,
                "mime_type": "image/png", "meta": {"_type": "gradio.FileData"}}
GLB_FILE = {"path": "/tmp/gradio/mock/mesh.glb",
            "url": "/gradio_api/file=/tmp/gradio/mock/mesh.glb",
            "orig_name": "mesh.glb", "size": len(GLB_BYTES),
            "mime_type": "model/gltf-binary",
            "meta": {"_type": "gradio.FileData"}}


class _MockGradio(BaseHTTPRequestHandler):
    """A Gradio Space in miniature: upload, call, SSE stream, download.

    Class knobs (reset per test): mode selects the failure to stage.
    """
    mode = "ok"            # ok | quota | quota-http | timeout | bad-glb | extract-flaky
    requests = 0
    calls: list = []       # (fn, data, session_hash)
    uploads: list = []     # session_hashes seen on /upload
    seen_auth: list = []
    events: dict = {}
    extract_calls = 0
    _seq = [0]

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, frames):
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n"
                       for name, data in frames).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        type(self).requests += 1
        type(self).seen_auth.append(self.headers.get("Authorization"))
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/gradio_api/upload":
            # The multipart body carries session_hash as a form field.
            text = body.decode("utf-8", "replace")
            marker = 'name="session_hash"\r\n\r\n'
            if marker in text:
                type(self).uploads.append(
                    text.split(marker, 1)[1].split("\r\n", 1)[0])
            self._json(200, ["/tmp/gradio/mock/photo.png"])
            return
        fn = None
        for prefix in ("/gradio_api/call/v2", "/gradio_api/call"):
            if self.path.startswith(prefix):
                fn = self.path[len(prefix):]
                break
        if fn is None:
            self._json(404, {"detail": "no such route"})
            return
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            payload = {}
        type(self).calls.append((fn, payload.get("data", []),
                                 payload.get("session_hash")))
        if type(self).mode == "quota-http":
            self._json(400, {"detail": "You have exceeded your ZeroGPU quota "
                                       "for today"})
            return
        type(self)._seq[0] += 1
        event_id = f"evt-{type(self)._seq[0]}"
        type(self).events[event_id] = fn
        self._json(200, {"event_id": event_id})

    def do_GET(self):
        type(self).requests += 1
        type(self).seen_auth.append(self.headers.get("Authorization"))
        if self.path.startswith("/gradio_api/file="):
            if type(self).mode == "bad-glb":
                raw = b"this is not a glb file, just text" * 5
            else:
                raw = GLB_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf-binary")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path.startswith("/gradio_api/call/"):
            event_id = self.path[len("/gradio_api/call/"):]
            fn = type(self).events.get(event_id)
            if fn is None:
                self._json(404, {"detail": "unknown event"})
                return
            if type(self).mode == "timeout":
                time.sleep(10)  # the client must give up first
                self._sse([])
                return
            gen = ("generating", {"output": {"data": []}, "success": True})
            if fn == "/preprocess_image":
                self._sse([gen, ("process_completed",
                                 {"output": {"data": [MASKED_IMAGE]},
                                  "success": True})])
            elif fn == "/image_to_3d":
                if type(self).mode == "quota":
                    self._sse([("error", {"error": "You have exceeded your "
                                               "ZeroGPU quota for today"})])
                else:
                    self._sse([gen, ("process_completed",
                                     {"output": {"data": ["<div>preview</div>"]},
                                      "success": True})])
            elif fn == "/extract_glb":
                type(self).extract_calls += 1
                if (type(self).mode == "extract-flaky"
                        and type(self).extract_calls == 1):
                    self._sse([("error", {"error": "extraction timed out"})])
                else:
                    self._sse([gen, ("process_completed",
                                     {"output": {"data": [GLB_FILE, GLB_FILE]},
                                      "success": True})])
            else:
                self._sse([("error", {"error": f"no such endpoint {fn}"})])
            return
        self._json(404, {"detail": "no such route"})


class MockGradioCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGradio)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _MockGradio.mode = "ok"
        _MockGradio.requests = 0
        _MockGradio.calls = []
        _MockGradio.uploads = []
        _MockGradio.seen_auth = []
        _MockGradio.events = {}
        _MockGradio.extract_calls = 0
        self._saved = {k: os.environ.get(k) for k in
                       ("HF_TOKEN", "DESIGNSHELL_CLOUDMESH_URL")}
        os.environ["HF_TOKEN"] = "test-token"
        os.environ["DESIGNSHELL_CLOUDMESH_URL"] = (
            f"http://127.0.0.1:{self.port}")
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        (self.ws / "jobs").mkdir()
        self.photo = Path(self.tmp.name) / "stool.png"
        self.photo.write_bytes(TINY_PNG)

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.tmp.cleanup()


class FlowTests(MockGradioCase):
    def test_success_round_trip(self):
        result = cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertTrue(result["ok"])
        self.assertEqual(result["space"], "microsoft/TRELLIS.2")
        job = Job(self.ws / "jobs" / result["job"])
        self.assertEqual(job.state, "awaiting_approval")
        glb = Path(result["glb"])
        self.assertTrue(glb.exists())
        self.assertEqual(glb.read_bytes()[:4], b"glTF")
        self.assertEqual(glb.name, "stool-mesh.glb")
        desc = job.load_description()
        self.assertEqual(desc["name"], "Stool Mesh")
        self.assertEqual(len(desc["parts"]), 1)
        self.assertEqual(desc["parts"][0]["size_cm"], [100.0, 100.0, 100.0])
        self.assertIn("cloud-mesh", desc["tags"])
        self.assertEqual(desc["cloud_mesh"]["space"], "microsoft/TRELLIS.2")
        self.assertEqual(desc["cloud_mesh"]["photo"], "stool.png")
        self.assertEqual(desc["cloud_mesh"]["seed"], result["seed"])
        self.assertEqual(desc_mod.validate(desc), [])
        # The three calls ran in order, in one session, with the token.
        fns = [c[0] for c in _MockGradio.calls]
        self.assertEqual(fns, ["/preprocess_image", "/image_to_3d",
                               "/extract_glb"])
        sessions = {c[2] for c in _MockGradio.calls}
        self.assertEqual(len(sessions), 1)
        self.assertEqual(_MockGradio.uploads, list(sessions))
        self.assertIn("Bearer test-token", _MockGradio.seen_auth)
        # The generation args match the Space's documented shape.
        gen_data = _MockGradio.calls[1][1]
        self.assertEqual(gen_data[1], result["seed"])
        self.assertEqual(gen_data[2], "512")
        self.assertEqual(len(gen_data), 15)
        self.assertEqual(_MockGradio.calls[2][1], [300000, 2048])

    def test_into_existing_intake_job(self):
        desc, _ = intake.intake_photo(self.ws, self.photo)
        job = Job.create(self.ws / "jobs", desc)
        result = cloudmesh.cloud_mesh(self.ws, self.photo, job=job.dir.name)
        self.assertEqual(result["job"], job.dir.name)
        job2 = Job(self.ws / "jobs" / job.dir.name)
        self.assertEqual(job2.state, "awaiting_approval")
        new_desc = job2.load_description()
        # The reference photo survives the description swap.
        self.assertEqual(new_desc["reference"]["file"], "stool.png")
        self.assertIn("photo-reference", new_desc["tags"])
        self.assertIn("cloud-mesh", new_desc["tags"])
        self.assertTrue((job2.dir / "stool-mesh.glb").exists())
        log = (job2.dir / "session.jsonl").read_text()
        self.assertIn("cloud_mesh", log)

    def test_no_token_refuses_before_network(self):
        os.environ.pop("HF_TOKEN")
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertIn("huggingface.co", str(cm.exception))
        self.assertIn("HF_TOKEN", str(cm.exception))
        self.assertEqual(_MockGradio.requests, 0)
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])

    def test_quota_sse_error_is_plain_and_job_intact(self):
        _MockGradio.mode = "quota"
        desc, _ = intake.intake_photo(self.ws, self.photo)
        job = Job.create(self.ws / "jobs", desc)
        before = job.load_description()
        with self.assertRaises(cloudmesh.QuotaExhaustedError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo, job=job.dir.name)
        self.assertIn("daily free GPU time", str(cm.exception))
        self.assertIn("tomorrow", str(cm.exception))
        job2 = Job(self.ws / "jobs" / job.dir.name)
        self.assertEqual(job2.state, "awaiting_approval")
        self.assertEqual(job2.load_description(), before)
        self.assertFalse((job2.dir / "stool-mesh.glb").exists())

    def test_quota_http_error_is_plain(self):
        _MockGradio.mode = "quota-http"
        with self.assertRaises(cloudmesh.QuotaExhaustedError):
            cloudmesh.cloud_mesh(self.ws, self.photo)

    def test_space_down_is_plain(self):
        os.environ["DESIGNSHELL_CLOUDMESH_URL"] = "http://127.0.0.1:9"
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertIn("could not reach", str(cm.exception))
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])

    def test_bad_image_refuses(self):
        bad = Path(self.tmp.name) / "notes.txt"
        bad.write_text("not an image at all")
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, bad)
        self.assertIn("PNG or JPEG", str(cm.exception))
        self.assertEqual(_MockGradio.requests, 0)
        with self.assertRaises(cloudmesh.CloudMeshError):
            cloudmesh.cloud_mesh(self.ws, Path(self.tmp.name) / "missing.png")

    def test_timeout_is_plain(self):
        _MockGradio.mode = "timeout"
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo, timeout=2)
        self.assertIn("timed out", str(cm.exception))
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])

    def test_extract_retries_once_in_the_same_session(self):
        _MockGradio.mode = "extract-flaky"
        result = cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertTrue(result["ok"])
        self.assertEqual(_MockGradio.extract_calls, 2)
        sessions = {c[2] for c in _MockGradio.calls}
        self.assertEqual(len(sessions), 1)

    def test_missing_job_is_plain(self):
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo, job="no-such-job")
        self.assertIn("no job named", str(cm.exception))
        self.assertEqual(_MockGradio.requests, 0)

    def test_refuses_approved_job(self):
        desc, _ = intake.intake_photo(self.ws, self.photo)
        job = Job.create(self.ws / "jobs", desc)
        job.approve()
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo, job=job.dir.name)
        self.assertIn("plan gate", str(cm.exception))
        self.assertEqual(_MockGradio.requests, 0)

    def test_bad_glb_rejected(self):
        _MockGradio.mode = "bad-glb"
        with self.assertRaises(cloudmesh.CloudMeshError) as cm:
            cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertIn("not a GLB", str(cm.exception))
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])

    def test_no_governor_lock_needed(self):
        # A heavy job is "running" (lock held by a live pid) and the
        # cloud step still goes through — it loads no local model.
        lock = self.ws / ".heavy.lock"
        lock.write_text(str(os.getpid()))
        result = cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertTrue(result["ok"])
        # And it never creates the lock itself.
        lock.unlink()
        result = cloudmesh.cloud_mesh(self.ws, self.photo)
        self.assertTrue(result["ok"])
        self.assertFalse(lock.exists())


class CliTests(MockGradioCase):
    def test_cli_cloud_mesh(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["cloud-mesh", str(self.ws), str(self.photo)])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("leaves this machine", text)
        self.assertIn("mesh filed", text)
        jobs = list((self.ws / "jobs").iterdir())
        self.assertEqual(len(jobs), 1)

    def test_cli_no_token(self):
        os.environ.pop("HF_TOKEN")
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["cloud-mesh", str(self.ws), str(self.photo)])
        self.assertEqual(code, 1)


class BenchTests(MockGradioCase):
    def _wait_run(self, run_id: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for run in bench._runs():
                if run["id"] == run_id and run["status"] != "running":
                    return run
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} never finished")

    def test_act_cloud_mesh(self):
        result = bench.act(self.ws, "cloud-mesh",
                           {"photo": str(self.photo)})
        self.assertTrue(result["ok"])
        run = self._wait_run(result["run"])
        self.assertEqual(run["status"], "done", run.get("detail"))
        jobs = list((self.ws / "jobs").iterdir())
        self.assertEqual(len(jobs), 1)
        self.assertIn("meshed stool.png", run["detail"])

    def test_act_fast_failures(self):
        with self.assertRaises(ValueError):
            bench.act(self.ws, "cloud-mesh", {"photo": ""})
        os.environ.pop("HF_TOKEN")
        with self.assertRaises(cloudmesh.CloudMeshError):
            bench.act(self.ws, "cloud-mesh", {"photo": str(self.photo)})

    def test_chat_mesh_with_path(self):
        reply = bench.chat_reply(self.ws, f"mesh {self.photo}")
        self.assertIn("free GPU", reply["reply"])
        runs = bench._runs()
        self.assertTrue(runs)
        run = self._wait_run(runs[-1]["id"])
        self.assertEqual(run["status"], "done", run.get("detail"))
        self.assertEqual(len(list((self.ws / "jobs").iterdir())), 1)

    def test_chat_mesh_this_photo_uses_selected_job_reference(self):
        desc, _ = intake.intake_photo(self.ws, self.photo)
        job = Job.create(self.ws / "jobs", desc)
        reply = bench.chat_reply(self.ws, "mesh this photo",
                                 selected=job.dir.name)
        self.assertIn("free GPU", reply["reply"])
        runs = bench._runs()
        run = self._wait_run(runs[-1]["id"])
        self.assertEqual(run["status"], "done", run.get("detail"))
        job2 = Job(self.ws / "jobs" / job.dir.name)
        self.assertTrue((job2.dir / "stool-mesh.glb").exists())

    def test_chat_mesh_without_photo_asks(self):
        reply = bench.chat_reply(self.ws, "mesh")
        self.assertIn("Give me the photo's path", reply["reply"])

    def test_status_reports_hf_token(self):
        reply = bench.chat_reply(self.ws, "status")
        self.assertIn("HF token set", reply["reply"])
        os.environ.pop("HF_TOKEN")
        reply = bench.chat_reply(self.ws, "status")
        self.assertIn("HF token not set", reply["reply"])


if __name__ == "__main__":
    unittest.main()
