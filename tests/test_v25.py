"""SKULLPT v1.15 tests: the audit finish (A20-A35) + ask-first updates.

Backend tests run everywhere; Qt tests skip without PySide6. The UI
fixes get pixel/geometry checks under Xvfb at 1366x768, not just
string asserts - and, per the v1.14 A13 lesson, width/fit assertions
measure with the real stylesheet font (Inter 10), never the test
default font.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import imagegen  # noqa: E402
from skullpt import live as live_mod  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt import update as update_mod  # noqa: E402
from skullpt import updater as updater_mod  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

CRAFT = json.loads(
    (ROOT / "examples" / "craft-stall.description.json").read_text())
SHOP = json.loads(
    (ROOT / "examples" / "shop-panel.description.json").read_text())

WOOD_SPEC = {"family": "wood", "style": "walnut", "layout": "boards",
             "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or CRAFT)))


def _mat_desc(kind: str = "scene") -> dict:
    """One object-material part and one string-material part."""
    return {
        "name": "material test", "kind": kind,
        "summary": "two parts for the material combo",
        "parts": [
            {"id": "plank", "type": "prop", "shape": "box",
             "position_cm": [0, 0, 0], "size_cm": [120, 12, 2],
             "material": json.loads(json.dumps(WOOD_SPEC))},
            {"id": "block", "type": "prop", "shape": "box",
             "position_cm": [50, 10, 0], "size_cm": [20, 20, 20],
             "material": "metal"},
        ],
        "constraints": [], "rubric": [],
        "budget": {"max_tris": 40000, "preview_px": 640},
    }


class _FakeBench:
    """A bench stand-in for window tests: actions run for real."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.refreshed = 0
        self.selected: str | None = None
        self.logged: list[str] = []

    def do_action(self, action: str, payload: dict):
        return bench_mod.act(self.workspace, action, payload)

    def refresh(self) -> None:
        self.refreshed += 1

    def select_job(self, name: str | None) -> None:
        self.selected = name

    def _log(self, who: str, text: str) -> None:
        self.logged.append(text)


# -------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.16 everywhere."""

    def test_version_is_115(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.17")
        self.assertIn("v1.17", (ROOT / "README.md").read_text())
        self.assertIn("## v1.17", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.17", (ROOT / "DESIGN.md").read_text())


# ----------------------------------------------------------------- A20


class A20EngineRunsTests(unittest.TestCase):
    """A20: the engine check runs the image engine, not just looks
    for the file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._cache = dict(imagegen._ENGINE_RUN_CACHE)
        imagegen._ENGINE_RUN_CACHE.update(
            {"path": None, "ok": False, "at": 0.0})

    def tearDown(self):
        imagegen._ENGINE_RUN_CACHE.update(self._cache)
        self.tmp.cleanup()

    def _fake_engine(self, name: str, help_exit: int) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text("#!/bin/sh\n"
                        f"if [ \"$1\" = \"--help\" ]; then exit {help_exit}; fi\n"
                        "exit 1\n")
        path.chmod(0o755)
        return path

    def test_engine_that_runs_is_ready(self):
        good = self._fake_engine("sd-cli-good", 0)
        self.assertTrue(imagegen.engine_runs(good))

    def test_engine_that_cannot_run_is_not_ready(self):
        broken = self._fake_engine("sd-cli-broken", 1)
        self.assertFalse(imagegen.engine_runs(broken))

    def test_no_engine_is_not_ready(self):
        with mock.patch.object(imagegen, "find_engine", return_value=None):
            self.assertFalse(imagegen.engine_runs())

    def test_status_reports_a_broken_engine_as_missing(self):
        broken = self._fake_engine("sd-cli-broken", 1)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            with mock.patch.object(imagegen, "find_engine",
                                   return_value=broken):
                status = bench_mod.engine_status(ws)
        self.assertFalse(status["image_engine"])

    def test_status_reports_a_running_engine_as_ready(self):
        good = self._fake_engine("sd-cli-good", 0)
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            with mock.patch.object(imagegen, "find_engine",
                                   return_value=good):
                status = bench_mod.engine_status(ws)
        self.assertTrue(status["image_engine"])

    def test_run_check_is_cached_briefly(self):
        good = self._fake_engine("sd-cli-good", 0)
        self.assertTrue(imagegen.engine_runs(good))
        cache = imagegen._ENGINE_RUN_CACHE
        self.assertEqual(cache["path"], str(good))
        self.assertTrue(cache["ok"])
        # A second call reuses the cache: removing the binary does
        # not change the answer until the cache expires.
        good.unlink()
        self.assertTrue(imagegen.engine_runs(good))


# ----------------------------------------------------------------- A21


class A21NoEnvVarTests(unittest.TestCase):
    """A21: the Blender-missing refusal never mentions an
    environment variable; it points at the installer."""

    def _assert_installer_framing(self, message: str) -> None:
        self.assertIn("Blender was not found", message)
        self.assertNotIn("DESIGNSHELL_BLENDER", message)
        self.assertNotIn("environment variable", message.lower())
        self.assertIn("installer", message.lower())

    def test_scene_export_refusal(self):
        from skullpt import blender_export

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(blender_export, "find_blender",
                                   return_value=None), \
                    self.assertRaises(RuntimeError) as ctx:
                blender_export.export_blender_with_build_script(
                    SHOP, Path(tmp) / "out", lambda *a: "", "scene")
        self._assert_installer_framing(str(ctx.exception))

    def test_character_export_refusal(self):
        from skullpt import blender_character, blender_export

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(blender_export, "find_blender",
                                   return_value=None), \
                    self.assertRaises(RuntimeError) as ctx:
                blender_character.export_blender_character(
                    SHOP, Path(tmp) / "out")
        self._assert_installer_framing(str(ctx.exception))

    def test_blend_measure_refusal(self):
        from skullpt import ingest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ingest, "find_blender",
                                   return_value=None), \
                    self.assertRaises(RuntimeError) as ctx:
                ingest.ingest_blend(Path(tmp) / "nope.blend", Path(tmp))
        self._assert_installer_framing(str(ctx.exception))

    def test_fbx_measure_refusal(self):
        from skullpt import ingest

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ingest, "find_blender",
                                   return_value=None), \
                    self.assertRaises(RuntimeError) as ctx:
                ingest.ingest_fbx(Path(tmp) / "nope.fbx", Path(tmp))
        self._assert_installer_framing(str(ctx.exception))


# ----------------------------------------------------------------- A26


class A26ProviderNameTests(unittest.TestCase):
    """A26: the no-key refusal names the provider plainly."""

    def test_gemini_refusal(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                self.assertRaises(live_mod.SeatNotConfigured) as ctx:
            live_mod._key_for(
                {"provider": "gemini", "model": "gemini-flash-latest",
                 "env_key": "GEMINI_API_KEY"}, None)
        self.assertEqual(str(ctx.exception),
                         "Gemini has no key - paste one in the "
                         "Bench Settings window")
        self.assertNotIn("seat gemini/", str(ctx.exception))

    def test_groq_refusal(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                self.assertRaises(live_mod.SeatNotConfigured) as ctx:
            live_mod._key_for(
                {"provider": "groq", "model": "llama-3.3-70b-versatile",
                 "env_key": "GROQ_API_KEY"}, None)
        self.assertIn("Groq has no key", str(ctx.exception))
        self.assertNotIn("seat groq/", str(ctx.exception))

    def test_plan_action_refusal_names_gemini(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            with mock.patch.dict(os.environ, {}, clear=True), \
                    self.assertRaises(Exception) as ctx:
                bench_mod.act(ws, "plan",
                              {"words": "a stall", "kind": "scene"})
        self.assertIn("Gemini has no key", str(ctx.exception))
        self.assertNotIn("seat gemini/", str(ctx.exception))


# ----------------------------------------------------------------- A27


class A27ImagesWindowWordingTests(unittest.TestCase):
    """A27: the image reply points at the Images window, not the
    removed web UI's 'Reference images section'."""

    def test_chat_reply_names_the_images_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            reply = bench_mod.chat_reply(
                ws, "generate an image of a wooden stool")
        self.assertIn("Images window", reply["reply"])
        self.assertNotIn("Reference images", reply["reply"])

    def test_no_reference_images_section_anywhere(self):
        for path in (ROOT / "skullpt").glob("*.py"):
            self.assertNotIn("Reference images", path.read_text(),
                             f"{path.name} still mentions it")


# ----------------------------------------------------------------- A35


class A35DemoActionTests(unittest.TestCase):
    """A35: the demo as a Bench action - the shop panel, approved,
    built on the heavy queue, filed into the Library."""

    def _wait_terminal(self, run_id: str, timeout: float = 60.0) -> None:
        # A demo run is a background Blender build: the temp dir
        # must not be deleted while it is still writing into it
        # (v1.15 verification: rmtree failed with "Directory not
        # empty: 'output'" under full-suite load). Cancel it and
        # wait for a terminal status before the temp dir exits.
        deadline = time.monotonic() + timeout
        bench_mod.cancel_run(run_id)
        while time.monotonic() < deadline:
            run = next((r for r in bench_mod._runs()
                        if r["id"] == run_id), None)
            if run is not None and run["status"] in (
                    "done", "cancelled", "error"):
                return
            time.sleep(0.05)
        self.fail(f"run {run_id} never finished: "
                  f"{next((r for r in bench_mod._runs() if r['id'] == run_id), None)}")

    def test_demo_creates_an_approved_job_and_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            result = bench_mod.act(ws, "demo", {})
            self.assertTrue(result["ok"])
            self.assertIn("run", result)
            job = Job(ws / "jobs" / result["job"])
            # Approved, checking, or already building: the
            # background run may flip the state before this
            # assertion reads it (a race the full suite hits under
            # load - 'checking' is the governor check on the way to
            # 'building'). Either way the job passed the plan gate
            # - an unapproved job would still read
            # awaiting_approval.
            self.assertIn(job.state, ("approved", "checking", "building"))
            self.assertIn("shop panel",
                          job.load_description()["name"].lower())
            self._wait_terminal(result["run"])

    def test_demo_action_is_in_the_bench(self):
        # The action exists alongside build/bake on the heavy queue:
        # a second demo while one runs queues instead of colliding.
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            first = bench_mod.act(ws, "demo", {})
            second = bench_mod.act(ws, "demo", {})
            self.assertNotEqual(first["job"], second["job"])
            self.assertIn("run", second)
            self._wait_terminal(first["run"])
            self._wait_terminal(second["run"])


# ------------------------------------------------- updater: the check


class UpdaterCheckTests(unittest.TestCase):
    """The check returns the release asset URLs the pull needs."""

    def _fake_response(self, payload):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return Resp()

    def test_assets_come_back_with_the_check(self):
        import urllib.request

        payload = {
            "tag_name": "v1.16",
            "html_url": "https://github.com/x/y/releases/tag/v1.16",
            "assets": [
                {"name": "skullpt-v1.16.zip",
                 "browser_download_url": "https://example.invalid/z.zip"},
                {"name": "skullpt-v1.16.zip.sha256",
                 "browser_download_url": "https://example.invalid/z.sha256"},
                {"name": "notes.txt",
                 "browser_download_url": "https://example.invalid/n.txt"},
            ]}
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=self._fake_response(payload)):
            result = update_mod.check_for_update("1.15")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["zip_url"], "https://example.invalid/z.zip")
        self.assertEqual(result["sha256_url"],
                         "https://example.invalid/z.sha256")

    def test_missing_assets_come_back_none(self):
        import urllib.request

        payload = {"tag_name": "v1.16",
                   "html_url": "https://github.com/x/y/releases/tag/v1.16",
                   "assets": []}
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=self._fake_response(payload)):
            result = update_mod.check_for_update("1.15")
        self.assertTrue(result["update_available"])
        self.assertIsNone(result["zip_url"])
        self.assertIsNone(result["sha256_url"])

    def test_error_result_has_asset_keys(self):
        import urllib.error
        import urllib.request

        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no net")):
            result = update_mod.check_for_update("1.15")
        self.assertIsNone(result["zip_url"])
        self.assertIsNone(result["sha256_url"])


class ParseSidecarTests(unittest.TestCase):
    """The .sha256 sidecar parses in both sha256sum forms."""

    def test_plain_form(self):
        digest = "a" * 64
        self.assertEqual(updater_mod.parse_sidecar(
            f"{digest}  skullpt-v9.9.zip\n"), digest)

    def test_binary_form(self):
        digest = "b" * 64
        self.assertEqual(updater_mod.parse_sidecar(
            f"{digest} *skullpt-v9.9.zip\n"), digest)

    def test_garbage_is_none(self):
        self.assertIsNone(updater_mod.parse_sidecar("not a checksum\n"))
        self.assertIsNone(updater_mod.parse_sidecar(""))
        self.assertIsNone(updater_mod.parse_sidecar("xyz  file.zip\n"))


# ------------------------------------------- updater: fake release server


def _dead_pid() -> int:
    """A pid that no longer exists (a reaped short-lived child)."""
    proc = subprocess.Popen(["true"])
    pid = proc.pid
    proc.wait()  # reaps it, so the pid is free again
    return pid


def _make_release_zip(version: str, extra: dict[str, str] | None = None) -> bytes:
    """A fake release zip: the install dir packed as skullpt/, the
    way build_zip.py packs it."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("skullpt/skullpt/__init__.py",
                    f'"""Fake SKULLPT."""\n\n__version__ = "{version}"\n')
        zf.writestr("skullpt/skullpt/bench.py", "# new bench\n")
        zf.writestr("skullpt/run.sh",
                    "#!/bin/sh\ntouch relaunched.marker\n")
        zf.writestr("skullpt/install.sh", "#!/bin/sh\nexit 0\n")
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_fake_install(root: Path, version: str = "1.0") -> Path:
    """A fake install dir: old code, a .venv, and scripts."""
    install = root / "install"
    (install / "skullpt").mkdir(parents=True)
    (install / ".venv").mkdir()
    (install / "skullpt" / "__init__.py").write_text(
        f'"""Fake SKULLPT."""\n\n__version__ = "{version}"\n')
    (install / "skullpt" / "bench.py").write_text("# old bench\n")
    (install / "skullpt" / "keep.py").write_text("# not in the zip\n")
    (install / "run.sh").write_text("#!/bin/sh\nexit 0\n")
    (install / "run.sh").chmod(0o755)
    (install / "install.sh").write_text("#!/bin/sh\nexit 0\n")
    (install / ".venv" / "marker.txt").write_text("venv stays\n")
    return install


class _FakeReleaseServer:
    """A local HTTP server standing in for GitHub: serves the
    release zip and its sidecar."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = server.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _release_result(server: _FakeReleaseServer, version: str,
                    with_sidecar: bool = True) -> dict:
    result = {"latest": version,
              "zip_url": f"{server.url}/skullpt-v{version}.zip",
              "sha256_url": None}
    if with_sidecar:
        result["sha256_url"] = (
            f"{server.url}/skullpt-v{version}.zip.sha256")
    return result


class UpdaterDownloadTests(unittest.TestCase):
    """Download + verify against a local fake release server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = _make_fake_install(self.root)
        self.zip_bytes = _make_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def test_download_and_verify_round_trip(self):
        result = updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install)
        self.assertEqual(result["version"], "9.9")
        pending = self.install / ".pending-update"
        self.assertTrue((pending / "skullpt-v9.9.zip").exists())
        self.assertTrue((pending / "skullpt-v9.9.zip.sha256").exists())
        record = json.loads((pending / "pending.json").read_text())
        self.assertEqual(record["version"], "9.9")
        self.assertEqual(
            record["zip_sha256"],
            hashlib.sha256(self.zip_bytes).hexdigest())
        self.assertTrue(updater_mod.stage_is_pending(self.install))
        # The staged update re-verifies: the zip on disk still
        # matches the sidecar.
        self.assertEqual(updater_mod.verify_staged(self.install)["version"],
                         "9.9")

    def test_checksum_mismatch_refuses_and_installs_nothing(self):
        bad = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{'0' * 64}  skullpt-v9.9.zip\n".encode()})
        try:
            with self.assertRaises(updater_mod.UpdateError) as ctx:
                updater_mod.download_and_stage(
                    _release_result(bad, "9.9"), self.install)
        finally:
            bad.close()
        self.assertIn("checksum does not match", str(ctx.exception))
        pending = self.install / ".pending-update"
        self.assertFalse((pending / "skullpt-v9.9.zip").exists())
        self.assertFalse(updater_mod.stage_is_pending(self.install))
        # The old install is untouched.
        self.assertIn('__version__ = "1.0"',
                      (self.install / "skullpt" / "__init__.py").read_text())

    def test_missing_sidecar_refuses(self):
        with self.assertRaises(updater_mod.UpdateError) as ctx:
            updater_mod.download_and_stage(
                _release_result(self.server, "9.9", with_sidecar=False),
                self.install)
        self.assertIn("no checksum to verify against", str(ctx.exception))
        self.assertFalse(updater_mod.stage_is_pending(self.install))

    def test_missing_zip_asset_refuses(self):
        result = {"latest": "9.9", "zip_url": None,
                  "sha256_url": f"{self.server.url}/x.sha256"}
        with self.assertRaises(updater_mod.UpdateError) as ctx:
            updater_mod.download_and_stage(result, self.install)
        self.assertIn("no download attached", str(ctx.exception))


# ------------------------------------------------ updater: the swap


class UpdaterSwapTests(unittest.TestCase):
    """The swap replaces code only and leaves the rest alone."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = _make_fake_install(self.root)
        self.zip_bytes = _make_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})
        # A workspace of its own, with a key file and a job: the
        # updater must never touch it.
        (self.root / "ws").mkdir()
        self.ws = _workspace(str(self.root / "ws"))
        (self.ws / "hf_token.key").write_text("secret-key\n")
        _job(self.ws)

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def _stage(self) -> None:
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install)

    def test_swap_replaces_code_and_relaunches(self):
        self._stage()
        self.assertEqual(updater_mod.run_update(self.install, _dead_pid()), 0)
        # New code is in place.
        self.assertIn('__version__ = "9.9"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        self.assertEqual((self.install / "skullpt" / "bench.py").read_text(),
                         "# new bench\n")
        # A file the zip does not contain survives the swap.
        self.assertEqual(
            (self.install / "skullpt" / "keep.py").read_text(),
            "# not in the zip\n")
        # .venv is never touched.
        self.assertEqual(
            (self.install / ".venv" / "marker.txt").read_text(),
            "venv stays\n")
        # The scripts stay executable.
        self.assertTrue(os.access(self.install / "run.sh", os.X_OK))
        # The relaunch ran: the zip's run.sh touches its marker.
        # The relaunch is a detached process, so wait for it.
        marker = self.install / "relaunched.marker"
        for _ in range(20):
            if marker.exists():
                break
            import time as _time

            _time.sleep(0.1)
        self.assertTrue(marker.exists())
        # The workspace and its key file survived.
        self.assertEqual((self.ws / "hf_token.key").read_text(),
                         "secret-key\n")
        self.assertEqual(len(list((self.ws / "jobs").iterdir())), 1)
        # The updated marker is there, and consuming it reports the
        # new version and cleans up the backup + staging.
        updated, failed = updater_mod.consume_update_marker(self.install)
        self.assertEqual(updated, "9.9")
        self.assertIsNone(failed)
        self.assertFalse((self.install / ".backup-1.0").exists())
        self.assertFalse(updater_mod.stage_is_pending(self.install))

    def test_tampered_stage_is_refused_at_apply_time(self):
        self._stage()
        zip_path = self.install / ".pending-update" / "skullpt-v9.9.zip"
        with open(zip_path, "ab") as fh:
            fh.write(b"tampered")
        self.assertEqual(updater_mod.run_update(self.install, _dead_pid()), 1)
        # The old install is untouched, the pending update dropped,
        # and the failure is recorded for the next launch.
        self.assertIn('__version__ = "1.0"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        self.assertFalse(updater_mod.stage_is_pending(self.install))
        self.assertTrue((self.install / ".update-failed").exists())
        updated, failed = updater_mod.consume_update_marker(self.install)
        self.assertIsNone(updated)
        self.assertIn("checksum", failed)

    def test_failed_swap_restores_the_old_install(self):
        # The zip carries skullpt/sub/file.py, but the install has
        # a plain FILE at skullpt/sub: the copy cannot make the
        # directory, so the swap fails midway.
        (self.install / "skullpt" / "sub").write_text("a file, not a dir\n")
        zip_bytes = _make_release_zip(
            "9.9", {"skullpt/skullpt/sub/file.py": "# nested\n"})
        digest = hashlib.sha256(zip_bytes).hexdigest()
        server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})
        try:
            updater_mod.download_and_stage(
                _release_result(server, "9.9"), self.install)
            self.assertEqual(
                updater_mod.run_update(self.install, _dead_pid()), 1)
        finally:
            server.close()
        # The old code is back in place, exactly.
        self.assertIn('__version__ = "1.0"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        self.assertEqual((self.install / "skullpt" / "bench.py").read_text(),
                         "# old bench\n")
        self.assertFalse(
            (self.install / ".updated-to-9.9").exists())
        self.assertTrue((self.install / ".update-failed").exists())
        updated, failed = updater_mod.consume_update_marker(self.install)
        self.assertIsNone(updated)
        self.assertIn("could not be applied", failed)


class UpdaterPendingTests(unittest.TestCase):
    """The pending update at startup: apply, or drop plainly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = _make_fake_install(self.root)
        self.zip_bytes = _make_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def test_nothing_pending_is_silent(self):
        self.assertIsNone(
            updater_mod.handle_pending_at_startup(self.install))

    def test_pending_update_restarts(self):
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install)
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            result = updater_mod.handle_pending_at_startup(self.install)
        self.assertEqual(result, "restarting")
        spawn.assert_called_once_with(self.install, os.getpid())

    def test_tampered_pending_update_is_dropped_with_a_reason(self):
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install)
        zip_path = self.install / ".pending-update" / "skullpt-v9.9.zip"
        with open(zip_path, "ab") as fh:
            fh.write(b"tampered")
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            result = updater_mod.handle_pending_at_startup(self.install)
        self.assertIn("checksum", result)
        spawn.assert_not_called()
        self.assertFalse(updater_mod.stage_is_pending(self.install))


# ----------------------------------------------------------------- Qt


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets

        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)
        self._windows = []

    def tearDown(self):
        for window in self._windows:
            window.close()
        self.tmp.cleanup()

    def _pump(self, ms: int = 100):
        from PySide6 import QtCore

        self._app.processEvents()
        QtCore.QThread.msleep(ms)
        self._app.processEvents()

    def _track(self, window):
        self._windows.append(window)
        return window

    def _bench(self):
        window = self._track(qtbench.BenchWindow(self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(300)
        return window


# ------------------------------------------------------------ A22/A23


class A22A23StatusBarTests(_QtCase):
    """A22: the status bar shows Ollama and Groq. A23: it elides
    cleanly when the text outgrows the bar."""

    def test_status_bar_names_ollama_and_groq(self):
        window = self._bench()
        text = window._engines_label.text()
        for key in ("Blender:", "Image engine:", "Image model:",
                    "Gemini key:", "Groq key:", "Ollama:", "HF token:",
                    "Character Studio:"):
            self.assertIn(key, text)
        # Settings order: Gemini, Groq, Ollama, HF.
        self.assertLess(text.index("Gemini key:"), text.index("Groq key:"))
        self.assertLess(text.index("Groq key:"), text.index("Ollama:"))
        self.assertLess(text.index("Ollama:"), text.index("HF token:"))

    def test_status_bar_elides_instead_of_cutting(self):
        from PySide6 import QtGui, QtWidgets

        from skullpt import qtdesign

        window = self._bench()
        label = window._engines_label
        window.resize(700, 500)
        self._pump(300)
        # The real app font (Inter 10), per the v1.14 A13 lesson.
        qtdesign.load_fonts()
        label.setFont(QtGui.QFont("Inter", 10))
        label.setText(label.text())  # re-elide under the real font
        self._pump(100)
        # Re-elide at the settled width: the layout can shift by a
        # pixel after the font change, and the measurement must see
        # the elision for the width it measures against.
        label._apply_elided()
        full = label.text()
        shown = QtWidgets.QLabel.text(label)  # what is actually painted
        self.assertIn("Character Studio:", full)
        self.assertTrue(shown.endswith("\u2026"),
                        f"expected an ellipsis, got: {shown!r}")
        self.assertLess(len(shown), len(full))
        # What shows fits the label (contentsRect already excludes
        # the frame, so no extra margin is due here).
        self.assertLessEqual(
            label.fontMetrics().horizontalAdvance(shown),
            label.contentsRect().width())

    def test_elided_label_keeps_the_full_text(self):
        from PySide6 import QtWidgets

        from skullpt.qtbench import _ElidedLabel

        label = self._track(_ElidedLabel("a very long status line"))
        label.resize(60, 20)
        label.show()
        self._pump(100)
        self.assertEqual(label.text(), "a very long status line")
        self.assertTrue(
            QtWidgets.QLabel.text(label).endswith("\u2026"))


# ----------------------------------------------------------------- A24


class A24ExampleDefaultTests(_QtCase):
    """A24: the example list starts with nothing selected."""

    def test_nothing_selected_on_launch(self):
        window = self._bench()
        self.assertEqual(window._example_combo.currentIndex(), -1)
        self.assertEqual(window._example_combo.currentText(), "")

    def test_from_example_with_nothing_picked_says_so(self):
        window = self._bench()
        before = len(bench_mod.chat_history(self.ws))
        window._job_from_example()
        history = bench_mod.chat_history(self.ws)
        self.assertEqual(len(history), before + 1)
        self.assertIn("Pick an example from the list first",
                      history[-1]["text"])
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])

    def test_from_example_with_a_pick_still_works(self):
        window = self._bench()
        window._example_combo.setCurrentIndex(0)
        window._job_from_example()
        self.assertEqual(len(list((self.ws / "jobs").iterdir())), 1)


# ----------------------------------------------------------------- A25


class A25SetupHeaderTests(_QtCase):
    """A25: the Setup header names the buttons that are there."""

    def test_header_names_the_real_buttons(self):
        from PySide6 import QtWidgets
        from skullpt import qtwindows

        window = self._track(qtwindows.SetupWindow(
            _FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        intros = [label.text() for label in
                  window.findChildren(QtWidgets.QLabel)
                  if label.text().startswith("Everything SKULLPT needs")]
        self.assertEqual(len(intros), 1)
        for button in ("Install", "Pull the model", "Add in Settings"):
            self.assertIn(button, intros[0])
        self.assertNotIn("Press Fix", intros[0])


# ----------------------------------------------------------------- A28


class A28LibraryDetailTests(_QtCase):
    """A28: the Library detail shows a plain date and only the
    files a user cares about."""

    def test_detail_shows_plain_date_and_user_files(self):
        from skullpt import qtwindows

        entry = self.ws / "library" / "panel"
        entry.mkdir()
        (entry / "manifest.json").write_text(json.dumps({
            "slug": "panel", "name": "Shop Panel", "kind": "ui_component",
            "filed": "2026-11-17T00:19:08+0000",
            "files": ["panel.glb", "panel.blend", "render.png",
                      "panel.blender.py", "panel.render.py",
                      "panel.stats.json", "panel.build-note.txt"],
            "recipes": []}))
        (entry / "description.json").write_text(json.dumps(SHOP))
        window = self._track(qtwindows.LibraryWindow(
            _FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window._on_entry(window._list.item(0))
        detail = window._detail.text()
        self.assertIn("17 Nov 2026", detail)
        self.assertNotIn("2026-11-17T00:19:08", detail)
        for visible in ("panel.glb", "panel.blend", "render.png"):
            self.assertIn(visible, detail)
        for hidden in (".blender.py", ".render.py", ".stats.json",
                       ".build-note.txt"):
            self.assertNotIn(hidden, detail)


# ----------------------------------------------------------------- A29


class A29RecipeSummaryTests(_QtCase):
    """A29: recipe summaries read in full."""

    def test_summary_is_not_truncated(self):
        from skullpt import qtwindows

        summary = ("A long summary that goes on and on about the "
                   "craft stall, its shelves, its awning, its "
                   "wooden counter, and every last detail of the "
                   "build, right down to the hinges on the little "
                   "doors and the very end.")
        self.assertGreater(len(summary), 200)
        recipes = self.ws / "recipes"
        recipes.mkdir()
        (recipes / "stall.recipe.json").write_text(json.dumps({
            "name": "Craft Stall recipe", "kind": "scene",
            "graduated_from": "craft-stall",
            "template": {"summary": summary, "parts": []}}))
        window = self._track(qtwindows.RecipesWindow(
            _FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window._on_recipe(window._list.item(0))
        detail = window._detail.text()
        self.assertIn(summary, detail)
        self.assertIn("and the very end.", detail)


# ----------------------------------------------------------------- A30


class A30ChatLogTests(_QtCase):
    """A30: recipe starts and backups log in the chat, not just a
    Toast."""

    def test_recipe_start_logs_in_the_chat(self):
        from skullpt import graduate, qtwindows

        job = _job(self.ws)
        graduate.graduate_recipe(self.ws, job, {"outputs": []})
        fake = _FakeBench(self.ws)
        window = self._track(qtwindows.RecipesWindow(fake, self.ws))
        window.show()
        self._pump(200)
        window._on_recipe(window._list.item(0))
        window._start_job()
        self.assertIsNotNone(fake.selected)
        self.assertTrue(any("Started " in line and "from the recipe" in line
                            for line in fake.logged))

    def test_backup_done_logs_in_the_chat(self):
        from skullpt import qtwindows

        fake = _FakeBench(self.ws)
        window = self._track(qtwindows.SettingsWindow(fake, self.ws))
        window.show()
        self._pump(200)
        window._on_backup_done(
            {"note": "Backed up 3 files. Your keys were not included."})
        self.assertIn("Backed up 3 files. Your keys were not included.",
                      fake.logged)

    def test_log_guard_survives_a_bench_without_log(self):
        # A bench double with no _log at all (the old _FakeBench
        # shape) must not break the recipe start.
        from skullpt import graduate, qtwindows

        class NoLogBench:
            def __init__(self, workspace):
                self.workspace = workspace
                self.refreshed = 0
                self.selected = None

            def do_action(self, action, payload):
                return bench_mod.act(self.workspace, action, payload)

            def refresh(self):
                self.refreshed += 1

            def select_job(self, name):
                self.selected = name

        job = _job(self.ws)
        graduate.graduate_recipe(self.ws, job, {"outputs": []})
        fake = NoLogBench(self.ws)
        window = self._track(qtwindows.RecipesWindow(fake, self.ws))
        window.show()
        self._pump(200)
        window._on_recipe(window._list.item(0))
        window._start_job()  # must not raise
        self.assertIsNotNone(fake.selected)


# ----------------------------------------------------------------- A31


class A31MaterialComboTests(_QtCase):
    """A31: the part editor shows a material-spec part's material
    in the Material box."""

    def _render(self, job_name):
        from skullpt import qtwindows

        window = self._track(qtwindows.RenderWindow(
            _FakeBench(self.ws), self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(200)
        window.set_job(job_name)
        self._pump(200)
        return window

    def test_material_spec_part_shows_its_breadcrumb(self):
        from skullpt import materials as materials_mod

        job = _job(self.ws, _mat_desc())
        window = self._render(job.dir.name)
        window._select_part("plank")
        self._pump(100)
        self.assertEqual(window._material_combo.currentText(),
                         materials_mod.breadcrumb(WOOD_SPEC))
        self.assertNotEqual(window._material_combo.currentText(), "")
        # The index stays unselected: saving must not write the
        # breadcrumb as a preset.
        self.assertEqual(window._material_combo.currentIndex(), -1)

    def test_saving_keeps_the_material_spec(self):
        job = _job(self.ws, _mat_desc())
        window = self._render(job.dir.name)
        window._select_part("plank")
        self._pump(100)
        window._save_part()
        desc = Job(self.ws / "jobs" / job.dir.name).load_description()
        plank = next(p for p in desc["parts"] if p["id"] == "plank")
        self.assertIsInstance(plank["material"], dict)
        self.assertEqual(plank["material"]["family"], "wood")

    def test_string_material_still_selects_its_preset(self):
        job = _job(self.ws, _mat_desc())
        window = self._render(job.dir.name)
        window._select_part("block")
        self._pump(100)
        self.assertEqual(window._material_combo.currentText(), "metal")
        self.assertGreaterEqual(window._material_combo.currentIndex(), 0)

    def test_typing_a_preset_name_saves_it(self):
        # The editable combo (A31) shows a typed preset name but
        # leaves the index at -1: saving must still write it, or a
        # typed preset would silently not save. The plank carries
        # a material spec, so its combo starts unselected with the
        # breadcrumb as its text - the user types over it.
        job = _job(self.ws, _mat_desc())
        window = self._render(job.dir.name)
        window._select_part("plank")
        self._pump(100)
        self.assertEqual(window._material_combo.currentIndex(), -1)
        window._material_combo.setCurrentText("maple")
        self.assertEqual(window._material_combo.currentIndex(), -1)
        self.assertEqual(window._material_combo.currentText(), "maple")
        window._save_part()
        desc = Job(self.ws / "jobs" / job.dir.name).load_description()
        plank = next(p for p in desc["parts"] if p["id"] == "plank")
        self.assertEqual(plank["material"], "maple")


# ----------------------------------------------------------------- A32


class A32DialPageTests(_QtCase):
    """A32: the dial clears its knob when you change pages."""

    def test_page_change_clears_the_knob(self):
        from skullpt.qtwindows import DialFace

        face = self._track(DialFace(WOOD_SPEC))
        face.show()
        self._pump(100)
        face._active_knob = "knots"
        face.set_page(0)  # Style (1) -> another page
        self.assertIsNone(face._active_knob)

    def test_same_page_keeps_the_knob(self):
        from skullpt.qtwindows import DialFace

        face = self._track(DialFace(WOOD_SPEC))
        face.show()
        self._pump(100)
        face._active_knob = "knots"
        face.set_page(face.page)
        self.assertEqual(face._active_knob, "knots")


# ----------------------------------------------------------------- A33


class A33CardHeightTests(_QtCase):
    """A33: image cards keep one height, and the grid pins its
    rows to the top."""

    def _batch_with(self, candidates):
        from PySide6 import QtGui

        _batch, batch_dir = imagegen.new_batch(self.ws, "a wooden stool")
        for candidate in candidates:
            if candidate.get("status") != "failed":
                pix = QtGui.QPixmap(120, 120)
                pix.fill(QtGui.QColor("red"))
                pix.save(str(batch_dir / candidate["file"]))
        record = json.loads((batch_dir / "batch.json").read_text())
        record["candidates"] = candidates
        (batch_dir / "batch.json").write_text(json.dumps(record))
        return batch_dir

    def test_failed_and_pending_cards_share_a_height(self):
        from skullpt import qtwindows

        batch_dir = self._batch_with([
            {"file": "cand-01.png", "status": "pending"},
            {"file": "cand-02.png", "status": "failed",
             "error": "x" * 300}])
        failed = self._track(qtwindows._CandidateCard(
            batch_dir, {"file": "cand-02.png", "status": "failed",
                        "error": "x" * 300}))
        pending = self._track(qtwindows._CandidateCard(
            batch_dir, {"file": "cand-01.png", "status": "pending"}))
        failed.show()
        pending.show()
        self._pump(200)
        self.assertEqual(failed.height(), 400)
        self.assertEqual(pending.height(), 400)
        # The reason is capped: the card cannot overflow.
        from PySide6 import QtWidgets
        reason_labels = [label.text() for label in
                         failed.findChildren(QtWidgets.QLabel)
                         if label.text().startswith("Why: ")]
        self.assertEqual(len(reason_labels), 1)
        self.assertLessEqual(len(reason_labels[0]), len("Why: ") + 100)
        self.assertTrue(reason_labels[0].endswith("..."))

    def test_grid_pins_rows_to_the_top(self):
        from skullpt import qtwindows

        batch_dir = self._batch_with([
            {"file": f"cand-0{i}.png", "status": "pending"}
            for i in range(1, 5)])
        window = self._track(qtwindows.ImagesWindow(
            _FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window._show_batch(batch_dir.name)
        self._pump(200)
        cards = window.findChildren(qtwindows._CandidateCard)
        self.assertEqual(len(cards), 4)
        self.assertTrue(all(card.height() == 400 for card in cards))
        # Four cards fill rows 0-1 (three across); row 2 stretches.
        self.assertEqual(window._grid.rowStretch(2), 1)


# ----------------------------------------------------------------- A34


class A34MenuBarTests(_QtCase):
    """A34: the menu bar uses real menus."""

    def test_menu_bar_is_four_menus(self):
        window = self._bench()
        actions = window.menuBar().actions()
        titles = [action.text() for action in actions]
        self.assertEqual(titles, ["File", "Jobs", "Windows", "Help"])
        for action in actions:
            self.assertIsNotNone(action.menu(),
                                 f"{action.text()} is not a menu")

    def test_windows_menu_lists_every_window(self):
        window = self._bench()
        menus = {action.text(): action.menu()
                 for action in window.menuBar().actions()}
        entries = [action.text() for action in
                   menus["Windows"].actions()]
        self.assertEqual(entries, ["Jobs", "Render", "Character Studio",
                                   "Images", "Library", "Recipes",
                                   "Settings"])


# ----------------------------------------------------------------- A35


class A35DemoMenuTests(_QtCase):
    """A35: the Bench gains a Demo action in the Jobs menu."""

    def _jobs_menu(self, window):
        for action in window.menuBar().actions():
            if action.text() == "Jobs":
                return action.menu()
        raise AssertionError("no Jobs menu")

    def test_jobs_menu_has_run_the_demo(self):
        window = self._bench()
        entries = [action.text() for action in
                   self._jobs_menu(window).actions()]
        self.assertIn("Run the demo", entries)

    def test_demo_action_runs_the_demo_and_logs_it(self):
        window = self._bench()
        demo = next(action for action in
                    self._jobs_menu(window).actions()
                    if action.text() == "Run the demo")
        with mock.patch.object(window, "do_action") as do:
            demo.trigger()
        do.assert_called_once_with("demo", {})
        history = bench_mod.chat_history(self.ws)
        self.assertTrue(any("Running the demo" in entry["text"]
                            for entry in history))


# ------------------------------------------------- updater: docs + help


class UpdaterDocsTests(unittest.TestCase):
    """The updater is documented in the CHANGELOG and the Help."""

    def test_changelog_documents_the_updater(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        section = text.split("## v1.15", 1)[1].split("## v1.14", 1)[0]
        for phrase in ("silent check", "ask-first", "checksum",
                       ".sha256 sidecar", "restart now"):
            self.assertIn(phrase, section)

    def test_help_documents_updates(self):
        text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        self.assertIn("<h3>Updates</h3>", text)
        self.assertIn("Update now?", text)
        self.assertNotIn("it never downloads\nanything by itself", text)
        self.assertNotIn("with a Fix button", text)


# --------------------------------------------- updater: the bench flow


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class UpdaterBenchTests(_QtCase):
    """The updater through the Bench: silent when up to date,
    ask-first when an update exists, ask-first restart."""

    def _check_result(self, available: bool, error=None) -> dict:
        return {"current": "1.15", "latest": "9.9" if available else "1.15",
                "update_available": available,
                "url": "https://example.invalid/release",
                "error": error, "zip_url": None, "sha256_url": None}

    def _message_clicker(self, text):
        """Patch QMessageBox.exec to click the named button at once,
        so a modal popup never blocks the test. Returns the patcher
        and the list of boxes shown."""
        from PySide6 import QtWidgets

        shown = []

        def fake_exec(box):
            shown.append(box)
            for btn in box.buttons():
                if btn.text() == text:
                    btn.click()
                    return 1
            return 0

        patcher = mock.patch.object(QtWidgets.QMessageBox, "exec", fake_exec)
        return patcher, shown

    def _wait_until(self, fn, timeout_s: float = 5.0) -> bool:
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if fn():
                return True
            self._pump(100)
        return fn()

    def test_silent_check_is_silent_when_up_to_date(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._check_result(False)), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "exec") as exec_mock, \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info, \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "warning") as warn:
            window._maybe_check_update_silent()
            self._wait_until(lambda: getattr(
                window, "_update_check_done", False))
            self._pump(300)
        exec_mock.assert_not_called()
        info.assert_not_called()
        warn.assert_not_called()

    def test_silent_check_is_silent_on_network_failure(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._check_result(
                                   False, error="could not reach GitHub")), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "exec") as exec_mock, \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info, \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "warning") as warn:
            window._maybe_check_update_silent()
            self._pump(500)
        exec_mock.assert_not_called()
        info.assert_not_called()
        warn.assert_not_called()

    def test_silent_check_is_silent_when_the_check_raises(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(update_mod, "check_for_update",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "exec") as exec_mock, \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "warning") as warn:
            window._maybe_check_update_silent()
            self._pump(500)
        exec_mock.assert_not_called()
        warn.assert_not_called()

    def test_silent_check_pops_up_when_an_update_exists(self):
        window = self._bench()
        clicker, shown = self._message_clicker("Not now")
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._check_result(True)), \
                clicker:
            window._maybe_check_update_silent()
            self.assertTrue(self._wait_until(lambda: len(shown) == 1))
        self.assertIn("SKULLPT v9.9 is out", shown[0].text())
        self.assertIn("You are on v1.15", shown[0].text())
        # "Not now" downloads nothing: no pending update anywhere.
        self.assertFalse(updater_mod.stage_is_pending(
            updater_mod.install_dir()))

    def test_manual_check_reports_up_to_date(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._check_result(False)), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info:
            window._check_for_updates()
            self.assertTrue(self._wait_until(
                lambda: info.call_count == 1))
        self.assertIn("latest release", info.call_args[0][2])

    def test_manual_check_offers_the_pull(self):
        window = self._bench()
        clicker, shown = self._message_clicker("Not now")
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._check_result(True)), \
                clicker:
            window._check_for_updates()
            self.assertTrue(self._wait_until(lambda: len(shown) == 1))
        buttons = [btn.text() for btn in shown[0].buttons()]
        self.assertIn("Update now", buttons)
        self.assertIn("Open release page", buttons)
        self.assertIn("Not now", buttons)

    def test_yes_restarts_with_the_update(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.Yes), \
                mock.patch.object(updater_mod,
                                  "spawn_updater") as spawn, \
                mock.patch.object(QtWidgets.QApplication,
                                  "quit") as quit_mock:
            window._ask_restart("9.9")
        # The workspace rides along (v1.17, E1): the relaunch
        # reopens the workspace the Bench was running.
        spawn.assert_called_once_with(updater_mod.install_dir(),
                                      os.getpid(), window.workspace)
        quit_mock.assert_called_once()

    def test_no_leaves_the_update_waiting(self):
        from PySide6 import QtWidgets

        window = self._bench()
        with mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.No), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info, \
                mock.patch.object(updater_mod,
                                  "spawn_updater") as spawn:
            window._ask_restart("9.9")
        spawn.assert_not_called()
        info.assert_called_once()
        self.assertIn("next time SKULLPT starts", info.call_args[0][2])
        history = bench_mod.chat_history(self.ws)
        self.assertTrue(any("waiting" in entry["text"]
                            for entry in history))


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class UpdaterRoundTripTests(_QtCase):
    """The full round-trip the way the user will use it: a fake
    install dir + a fake release server, drive the Bench, accept
    the popup, confirm the new version runs and the workspace and
    keys survived."""

    def setUp(self):
        super().setUp()
        self.install_root = Path(self.tmp.name) / "install-root"
        self.install_root.mkdir()
        self.install = _make_fake_install(self.install_root)
        self.zip_bytes = _make_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})
        # The Bench's own workspace, with a key file and a job.
        (self.ws / "hf_token.key").write_text("secret-key\n")
        _job(self.ws)

    def tearDown(self):
        self.server.close()
        super().tearDown()

    def _fake_result(self) -> dict:
        result = _release_result(self.server, "9.9")
        result.update({"current": "1.15", "update_available": True,
                       "url": "https://example.invalid/release",
                       "error": None})
        return result

    def _message_clicker(self, text):
        from PySide6 import QtWidgets

        shown = []

        def fake_exec(box):
            shown.append(box)
            for btn in box.buttons():
                if btn.text() == text:
                    btn.click()
                    return 1
            return 0

        patcher = mock.patch.object(QtWidgets.QMessageBox, "exec", fake_exec)
        return patcher, shown

    def _wait_until(self, fn, timeout_s: float = 8.0) -> bool:
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if fn():
                return True
            self._pump(100)
        return fn()

    def test_accept_pull_then_yes_restarts_on_the_new_version(self):
        from PySide6 import QtWidgets

        window = self._bench()
        clicker, shown = self._message_clicker("Update now")
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._fake_result()), \
                mock.patch.object(updater_mod, "install_dir",
                                  return_value=self.install), \
                mock.patch.object(QtWidgets.QMessageBox, "question",
                                  return_value=QtWidgets.QMessageBox.Yes), \
                mock.patch.object(updater_mod,
                                  "spawn_updater") as spawn, \
                mock.patch.object(QtWidgets.QApplication,
                                  "quit") as quit_mock, \
                clicker:
            window._maybe_check_update_silent()
            self.assertTrue(self._wait_until(
                lambda: spawn.call_count == 1))
        spawn.assert_called_once_with(self.install, os.getpid(),
                                      window.workspace)
        quit_mock.assert_called_once()
        # The staged update is verified and waiting.
        self.assertTrue(updater_mod.stage_is_pending(self.install))
        # Simulate the restart: the updater swaps the staged code
        # in once the Bench has exited.
        self.assertEqual(updater_mod.run_update(self.install, _dead_pid()), 0)
        self.assertIn('__version__ = "9.9"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        # The new version reports the update and cleans up.
        updated, failed = updater_mod.consume_update_marker(self.install)
        self.assertEqual(updated, "9.9")
        self.assertIsNone(failed)
        # The workspace, its key, and the .venv survived.
        self.assertEqual((self.ws / "hf_token.key").read_text(),
                         "secret-key\n")
        self.assertEqual(len(list((self.ws / "jobs").iterdir())), 1)
        self.assertEqual(
            (self.install / ".venv" / "marker.txt").read_text(),
            "venv stays\n")

    def test_no_defers_then_a_fresh_launch_applies(self):
        from PySide6 import QtWidgets

        window = self._bench()
        clicker, shown = self._message_clicker("Update now")
        with mock.patch.object(update_mod, "check_for_update",
                               return_value=self._fake_result()), \
                mock.patch.object(updater_mod, "install_dir",
                                  return_value=self.install), \
                mock.patch.object(QtWidgets.QMessageBox, "question",
                                  return_value=QtWidgets.QMessageBox.No), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info, \
                mock.patch.object(updater_mod,
                                  "spawn_updater") as spawn, \
                clicker:
            window._maybe_check_update_silent()
            self.assertTrue(self._wait_until(
                lambda: info.call_count == 1))
        # No restart happened, and the old version is still running.
        spawn.assert_not_called()
        self.assertIn('__version__ = "1.0"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        self.assertTrue(updater_mod.stage_is_pending(self.install))
        # A fresh launch applies the waiting update.
        with mock.patch.object(updater_mod,
                               "spawn_updater") as spawn2:
            result = updater_mod.handle_pending_at_startup(self.install)
        self.assertEqual(result, "restarting")
        # The staged record carries the workspace from the pull
        # (v1.17, E1), so the deferred hand-off passes it on as
        # the string the record holds.
        spawn2.assert_called_once_with(self.install, os.getpid(),
                                       str(window.workspace))
        # Simulate the updater after the Bench exits.
        self.assertEqual(updater_mod.run_update(self.install, _dead_pid()), 0)
        self.assertIn('__version__ = "9.9"',
                      (self.install / "skullpt" / "__init__.py").read_text())
        updated, _failed = updater_mod.consume_update_marker(self.install)
        self.assertEqual(updated, "9.9")
        self.assertEqual((self.ws / "hf_token.key").read_text(),
                         "secret-key\n")

    def test_second_launch_never_triggers_a_pending_swap(self):
        """A second launch while the Bench is running hands off to
        it and never triggers the pending update: the updater only
        waits for the process that spawned it, so a swap here would
        replace the code under the running Bench. The pending update
        stays waiting for the next real launch."""
        from PySide6 import QtNetwork, QtWidgets

        updater_mod.download_and_stage(self._fake_result(), self.install)
        self.assertTrue(updater_mod.stage_is_pending(self.install))
        server_name = ("SKULLPT-" + hashlib.sha256(
            str(self.ws.resolve()).encode()).hexdigest()[:16])
        server = QtNetwork.QLocalServer()
        self.assertTrue(server.listen(server_name))
        try:
            with mock.patch.object(updater_mod, "install_dir",
                                  return_value=self.install), \
                    mock.patch.object(updater_mod,
                                      "spawn_updater") as spawn, \
                    mock.patch.object(QtWidgets, "QApplication",
                                      return_value=self._app):
                result = qtbench.main(self.ws)
            self.assertEqual(result, 0)
            spawn.assert_not_called()
            self.assertTrue(updater_mod.stage_is_pending(self.install))
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
