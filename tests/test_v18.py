"""SKULLPT v1.8 tests: the seamless install (S1-S6).

Full install by default (S1), Ollama install + model pull (S2),
in-app key setup for Gemini + Groq (S3), the first-run Setup check
(S4/S5), windows open maximized with nothing clipped (S6).

No real API keys anywhere in this file — every key value is a
clearly fake placeholder, and every validation call goes to a local
mock server.
"""
import json
import os
import shutil
import socket
import stat
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

from skullpt import backup, bench, keycheck, live, ollama as ollama_mod, seats, tokenfile  # noqa: E402

FAKE_GEMINI_KEY = "AIza-fake-test-key-not-real"
FAKE_GROQ_KEY = "gsk_fake-test-key-not-real"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MockKeyServer:
    """A tiny HTTP server that answers like the Gemini/Groq
    models-list endpoints: 200 for the good key, 401 otherwise."""

    def __init__(self, good_key):
        self.good_key = good_key
        self.seen = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._answer()

            def do_POST(self):
                # The generation call is a POST; the key is still in
                # the query string, so the same check applies.
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self._answer()

            def _answer(self):
                outer.seen.append((self.path, self.headers.get("Authorization")))
                key = None
                if "key=" in self.path:
                    key = self.path.split("key=", 1)[1].split("&")[0]
                auth = self.headers.get("Authorization") or ""
                if auth.startswith("Bearer "):
                    key = auth[len("Bearer "):]
                if key == outer.good_key:
                    body = b'{"models": []}'
                    self.send_response(200)
                else:
                    body = b'{"error": "bad key"}'
                    self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class _MockOllamaServer:
    """Answers /api/version and /api/tags like a real Ollama."""

    def __init__(self, models):
        self.models = list(models)
        self.hits = []
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path == "/api/version":
                    body = json.dumps({"version": "0.0.0-test"}).encode()
                elif self.path == "/api/tags":
                    body = json.dumps(
                        {"models": [{"name": m} for m in outer.models]}).encode()
                else:
                    body = b'{"error": "not found"}'
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class TokenfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_clear_each_kind(self):
        for kind, key in (("gemini", FAKE_GEMINI_KEY),
                          ("groq", FAKE_GROQ_KEY),
                          ("hf", "hf_fake-test-token")):
            self.assertFalse(tokenfile.has_key(kind, self.ws))
            tokenfile.save_key(kind, self.ws, key)
            self.assertTrue(tokenfile.has_key(kind, self.ws))
            self.assertEqual(tokenfile.load_key(kind, self.ws), key)
            tokenfile.clear_key(kind, self.ws)
            self.assertFalse(tokenfile.has_key(kind, self.ws))
            self.assertIsNone(tokenfile.load_key(kind, self.ws))

    def test_key_files_are_owner_only(self):
        tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
        mode = stat.S_IMODE(os.stat(self.ws / "gemini_key.key").st_mode)
        self.assertEqual(mode, 0o600)

    def test_key_files_are_gitignored(self):
        tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
        tokenfile.save_key("groq", self.ws, FAKE_GROQ_KEY)
        ignored = (self.ws / ".gitignore").read_text()
        for name in ("hf_token.key", "gemini_key.key", "groq_key.key"):
            self.assertIn(name, ignored)

    def test_repo_gitignore_covers_key_files(self):
        ignored = (ROOT / ".gitignore").read_text()
        self.assertIn("*.key", ignored)

    def test_unknown_kind_raises(self):
        with self.assertRaises(KeyError):
            tokenfile.save_key("nope", self.ws, "x")

    def test_hf_wrappers_still_work(self):
        tokenfile.save_token(self.ws, "hf_fake-test-token")
        self.assertTrue(tokenfile.has_token(self.ws))
        self.assertEqual(tokenfile.load_token(self.ws), "hf_fake-test-token")
        tokenfile.clear_token(self.ws)
        self.assertFalse(tokenfile.has_token(self.ws))

    def test_env_key_lookup(self):
        tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
        self.assertEqual(
            tokenfile.load_key_for_env(self.ws, "GEMINI_API_KEY"),
            FAKE_GEMINI_KEY)
        self.assertIsNone(tokenfile.load_key_for_env(self.ws, "GROQ_API_KEY"))
        self.assertIsNone(tokenfile.load_key_for_env(self.ws, "NOPE_KEY"))


class KeycheckTests(unittest.TestCase):
    def test_gemini_valid_key(self):
        with _MockKeyServer(FAKE_GEMINI_KEY) as server:
            url = f"http://127.0.0.1:{server.port}/v1beta/models"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_GEMINI_VALIDATE_URL": url}):
                ok, msg = keycheck.validate_gemini_key(FAKE_GEMINI_KEY)
        self.assertTrue(ok, msg)
        self.assertNotIn(FAKE_GEMINI_KEY, msg)

    def test_gemini_invalid_key_is_plain_and_keyless(self):
        with _MockKeyServer(FAKE_GEMINI_KEY) as server:
            url = f"http://127.0.0.1:{server.port}/v1beta/models"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_GEMINI_VALIDATE_URL": url}):
                ok, msg = keycheck.validate_gemini_key("AIza-wrong-key")
        self.assertFalse(ok)
        self.assertIn("did not work", msg)
        self.assertNotIn("AIza-wrong-key", msg)

    def test_groq_valid_and_invalid(self):
        with _MockKeyServer(FAKE_GROQ_KEY) as server:
            url = f"http://127.0.0.1:{server.port}/openai/v1/models"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_GROQ_VALIDATE_URL": url}):
                ok, _ = keycheck.validate_groq_key(FAKE_GROQ_KEY)
                self.assertTrue(ok)
                ok, msg = keycheck.validate_groq_key("gsk_wrong-key")
                self.assertFalse(ok)
                self.assertIn("did not work", msg)
                self.assertNotIn("gsk_wrong-key", msg)

    def test_unreachable_server_is_a_plain_network_message(self):
        port = _free_port()
        url = f"http://127.0.0.1:{port}/v1beta/models"
        with mock.patch.dict(os.environ,
                             {"DESIGNSHELL_GEMINI_VALIDATE_URL": url}):
            ok, msg = keycheck.validate_gemini_key(FAKE_GEMINI_KEY,
                                                   timeout=2.0)
        self.assertFalse(ok)
        self.assertIn("Could not reach", msg)

    def test_key_page_urls_are_the_real_ones(self):
        self.assertEqual(keycheck.GEMINI_KEY_PAGE,
                         "https://aistudio.google.com/apikey")
        self.assertEqual(keycheck.GROQ_KEY_PAGE,
                         "https://console.groq.com/keys")


class OllamaModuleTests(unittest.TestCase):
    def setUp(self):
        ollama_mod._cache.update(at=0.0, status=None)

    def tearDown(self):
        ollama_mod._cache.update(at=0.0, status=None)

    def test_server_up_and_model_present(self):
        with _MockOllamaServer(["qwen3:4b"]) as server:
            with mock.patch.dict(os.environ, {"DESIGNSHELL_OLLAMA_BASE":
                                              f"http://127.0.0.1:{server.port}"}):
                self.assertTrue(ollama_mod.server_up())
                self.assertTrue(ollama_mod.model_present())
                status = ollama_mod.status()
        self.assertTrue(status["server"])
        self.assertTrue(status["model"])
        self.assertEqual(status["model_name"], "qwen3:4b")

    def test_server_down(self):
        port = _free_port()
        with mock.patch.dict(os.environ, {"DESIGNSHELL_OLLAMA_BASE":
                                          f"http://127.0.0.1:{port}"}):
            self.assertFalse(ollama_mod.server_up(timeout=1.0))
            self.assertFalse(ollama_mod.model_present(timeout=1.0))
            status = ollama_mod.status()
        self.assertFalse(status["server"])
        self.assertFalse(status["model"])

    def test_qwen3_latest_is_not_qwen3_4b(self):
        # The tag matters: "qwen3:latest" is a different model from
        # "qwen3:4b", and must not count as present.
        with _MockOllamaServer(["qwen3:latest"]) as server:
            with mock.patch.dict(os.environ, {"DESIGNSHELL_OLLAMA_BASE":
                                              f"http://127.0.0.1:{server.port}"}):
                self.assertTrue(ollama_mod.server_up())
                self.assertFalse(ollama_mod.model_present())

    def test_status_is_cached(self):
        with _MockOllamaServer(["qwen3:4b"]) as server:
            with mock.patch.dict(os.environ, {"DESIGNSHELL_OLLAMA_BASE":
                                              f"http://127.0.0.1:{server.port}"}):
                ollama_mod.status()
                hits_after_first = len(server.hits)
                ollama_mod.status()
                self.assertEqual(len(server.hits), hits_after_first,
                                 "the second status() hit the server again")
                ollama_mod.status(force=True)
                self.assertGreater(len(server.hits), hits_after_first)

    def test_pull_model_with_stub_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            script = bin_dir / "ollama"
            script.write_text("#!/bin/sh\necho \"ollama $*\" >> \"$OLLAMA_LOG\"\n"
                              "exit \"${STUB_PULL_EXIT:-0}\"\n")
            script.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                       OLLAMA_LOG=str(Path(tmp) / "ollama.log"))
            with mock.patch.dict(os.environ, env, clear=True):
                ok, msg = ollama_mod.pull_model()
                self.assertTrue(ok, msg)
                self.assertIn("qwen3:4b", msg)
                log = (Path(tmp) / "ollama.log").read_text()
                self.assertIn("pull qwen3:4b", log)

    def test_pull_model_failure_is_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            script = bin_dir / "ollama"
            script.write_text("#!/bin/sh\nexit 1\n")
            script.chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
            with mock.patch.dict(os.environ, env, clear=True):
                ok, msg = ollama_mod.pull_model()
                self.assertFalse(ok)
                self.assertIn("pull", msg.lower())

    def test_pull_without_a_binary_is_plain(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PATH=str(Path(tmp)))
            with mock.patch.dict(os.environ, env, clear=True):
                ok, msg = ollama_mod.pull_model()
                self.assertFalse(ok)
                self.assertIn("not installed", msg)


class LiveKeyPrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_key_available_from_env_or_file(self):
        seat = dict(seats.DEFAULT_SEATS["planner"])
        env = {k: v for k, v in os.environ.items()
               if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(live.key_available(seat, self.ws))
            tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
            self.assertTrue(live.key_available(seat, self.ws))

    def test_env_key_wins_over_key_file(self):
        # The precedence lives in _key_for: env first, then the key
        # file. (The DESIGNSHELL_GEMINI_URL override is used verbatim
        # — the key is never appended to it — so the precedence is
        # checked here, not on the wire.)
        tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
        seat = {"provider": "gemini", "model": "m",
                "env_key": "GEMINI_API_KEY"}
        env = dict(os.environ, GEMINI_API_KEY="AIza-env-key")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(live._key_for(seat, self.ws), "AIza-env-key")
        env = {k: v for k, v in os.environ.items()
               if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(live._key_for(seat, self.ws), FAKE_GEMINI_KEY)

    def test_key_file_used_when_env_is_absent(self):
        # End to end: with only the key file set, a real call goes
        # out and comes back; with no key at all, nothing is sent.
        class GenerateHandler(BaseHTTPRequestHandler):
            seen = []

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                GenerateHandler.seen.append(self.path)
                body = json.dumps({"candidates": [{"content": {"parts": [
                    {"text": "hello from the mock"}]}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), GenerateHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = (f"http://127.0.0.1:{server.server_address[1]}"
                   "/v1beta/models/m:generateContent")
            tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
            seat = {"provider": "gemini", "model": "m",
                    "env_key": "GEMINI_API_KEY"}
            env = {k: v for k, v in os.environ.items()
                   if k != "GEMINI_API_KEY"}
            env["DESIGNSHELL_GEMINI_URL"] = url
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(live.gemini_text(seat, "hi",
                                                  workspace=self.ws),
                                 "hello from the mock")
            self.assertEqual(len(GenerateHandler.seen), 1)
            # No key anywhere: the refusal happens before any request.
            tokenfile.clear_key("gemini", self.ws)
            GenerateHandler.seen.clear()
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(live.SeatNotConfigured):
                    live.gemini_text(seat, "hi", workspace=self.ws)
            self.assertEqual(GenerateHandler.seen, [])
        finally:
            server.shutdown()
            server.server_close()

    def test_no_key_anywhere_refuses_plainly(self):
        seat = dict(seats.DEFAULT_SEATS["planner"])
        env = {k: v for k, v in os.environ.items()
               if k != "GEMINI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(live.SeatNotConfigured) as ctx:
                live.gemini_text(seat, "hi", workspace=self.ws)
            self.assertIn("has no key", str(ctx.exception))
            self.assertIn("Bench Settings window", str(ctx.exception))
            self.assertNotIn("GEMINI_API_KEY", str(ctx.exception))


class EngineStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        ollama_mod._cache.update(at=0.0, status=None)

    def tearDown(self):
        self.tmp.cleanup()
        ollama_mod._cache.update(at=0.0, status=None)

    def test_status_has_the_new_keys(self):
        status = bench.engine_status(self.ws)
        for key in ("ollama", "ollama_server", "ollama_model", "groq_key"):
            self.assertIn(key, status)

    def test_gemini_key_true_from_key_file(self):
        tokenfile.save_key("gemini", self.ws, FAKE_GEMINI_KEY)
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN")}
        with mock.patch.dict(os.environ, env, clear=True):
            status = bench.engine_status(self.ws)
        self.assertTrue(status["gemini_key"])
        self.assertFalse(status["groq_key"])
        self.assertFalse(status["hf_token"])

    def test_groq_key_true_from_key_file(self):
        tokenfile.save_key("groq", self.ws, FAKE_GROQ_KEY)
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_API_KEY", "GROQ_API_KEY", "HF_TOKEN")}
        with mock.patch.dict(os.environ, env, clear=True):
            status = bench.engine_status(self.ws)
        self.assertTrue(status["groq_key"])

    def test_ollama_ready_when_server_up_and_model_present(self):
        with _MockOllamaServer(["qwen3:4b"]) as server:
            with mock.patch.dict(os.environ, {"DESIGNSHELL_OLLAMA_BASE":
                                              f"http://127.0.0.1:{server.port}"}):
                status = bench.engine_status(self.ws)
        self.assertTrue(status["ollama"])
        self.assertTrue(status["ollama_server"])
        self.assertTrue(status["ollama_model"])


class MissingSetupItemsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _items(self, status):
        with mock.patch.object(bench, "engine_status", return_value=status):
            return bench.missing_setup_items(self.ws)

    def _all_ready(self):
        return {"blender": True, "image_engine": True, "image_model": True,
                "gemini_key": True, "groq_key": True, "hf_token": True,
                "character_studio": True, "ollama": True,
                "ollama_server": True, "ollama_model": True}

    def test_all_ready_is_empty(self):
        self.assertEqual(self._items(self._all_ready()), [])

    def test_all_missing_lists_everything_with_fixes(self):
        status = self._all_ready()
        for key in status:
            status[key] = False
        items = self._items(status)
        keys = [item["key"] for item in items]
        for key in ("blender", "image_engine", "image_model",
                    "character_studio", "ollama", "gemini_key",
                    "groq_key", "hf_token"):
            self.assertIn(key, keys)
        # Engines first, keys last.
        self.assertLess(keys.index("blender"), keys.index("gemini_key"))
        fixes = {item["key"]: item["fix"] for item in items}
        self.assertEqual(fixes["blender"], "installer")
        self.assertEqual(fixes["image_engine"], "installer")
        self.assertEqual(fixes["character_studio"], "installer")
        self.assertEqual(fixes["ollama"], "installer")  # server down
        self.assertEqual(fixes["gemini_key"], "settings")
        self.assertEqual(fixes["groq_key"], "settings")
        self.assertEqual(fixes["hf_token"], "settings")
        for item in items:
            self.assertTrue(item["title"])
            self.assertTrue(item["detail"])
            # No fix text points at a terminal or an env var.
            for forbidden in ("terminal", "GEMINI_API_KEY", "GROQ_API_KEY",
                              "HF_TOKEN", "environment variable"):
                self.assertNotIn(forbidden, item["detail"])

    def test_ollama_server_up_but_model_missing_is_a_pull(self):
        status = self._all_ready()
        status["ollama"] = False
        status["ollama_model"] = False
        items = self._items(status)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["key"], "ollama")
        self.assertEqual(items[0]["fix"], "pull_model")
        self.assertIn("model", items[0]["detail"].lower())

    def test_ollama_server_down_is_an_install(self):
        status = self._all_ready()
        status["ollama"] = False
        status["ollama_server"] = False
        status["ollama_model"] = False
        items = self._items(status)
        self.assertEqual(items[0]["fix"], "installer")
        self.assertIn("server", items[0]["detail"].lower())


class BackupExcludesKeysTests(unittest.TestCase):
    def test_new_key_files_are_never_in_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / "gemini_key.key").write_text(FAKE_GEMINI_KEY)
            (ws / "groq_key.key").write_text(FAKE_GROQ_KEY)
            (ws / "hf_token.key").write_text("hf_fake-test-token")
            # A key file inside a backed-up dir is the real test of
            # the exclusion filter (root files are only backed up
            # when named in BACKED_UP_FILES).
            (ws / "jobs").mkdir()
            (ws / "jobs" / "gemini_key.key").write_text(FAKE_GEMINI_KEY)
            (ws / "jobs" / "note.txt").write_text("keep me")
            dest = Path(tmp) / "backup.zip"
            result = backup.backup_workspace(ws, dest)
            self.assertTrue(result["ok"], result)
            import zipfile
            with zipfile.ZipFile(dest) as zf:
                names = zf.namelist()
                manifest = json.loads(zf.read("backup-manifest.json"))
            for name in ("gemini_key.key", "groq_key.key", "hf_token.key",
                         "jobs/gemini_key.key"):
                self.assertNotIn(name, names)
            self.assertIn("jobs/note.txt", names)
            self.assertIn("gemini_key.key", manifest.get("note", ""))


class InstallerTextTests(unittest.TestCase):
    def setUp(self):
        self.install = (ROOT / "install.sh").read_text()

    def test_full_install_is_the_default(self):
        self.assertIn("WITH_BLENDER=1", self.install)
        self.assertIn("WITH_IMAGE=1", self.install)
        self.assertIn("WITH_CHARACTER=1", self.install)
        self.assertIn("WITH_OLLAMA=1", self.install)
        self.assertIn("--with-ollama) WITH_OLLAMA=1 ;;", self.install)
        self.assertIn("PASSTHROUGH+=(--with-ollama)", self.install)

    def test_disclosure_names_the_total(self):
        self.assertIn("up to about 7.7 GB", self.install)
        self.assertIn("Installing everything SKULLPT needs", self.install)
        # The progress window shows the disclosure before the worker
        # starts (zero extra clicks).
        self.assertIn('--text="Installing everything SKULLPT needs',
                      self.install)

    def test_progress_steps_cover_the_full_install(self):
        for label in ('step 5 "Checking Python"',
                      'step 15 "Setting up the Bench window"',
                      'step 20 "Checking Blender"',
                      'step 32 "Checking the image engine"',
                      'step 50 "Checking Character Studio"',
                      'step 60 "Checking Ollama"',
                      'step 66 "Pulling the local model"',
                      'step 70 "Running the test suite"',
                      'step 85 "Finishing up"',
                      'step 100 "Done"'):
            self.assertIn(label, self.install, label)

    def test_pinned_blender_is_not_reinstalled(self):
        self.assertIn("Blender $BLENDER_VERSION is already installed",
                      self.install)
        # The old "not equal" condition that reinstalled the pinned
        # version is gone.
        self.assertNotIn('FOUND_VER" != "$BLENDER_VERSION"', self.install)

    def test_character_studio_skips_when_installed(self):
        self.assertIn("Character Studio is already installed",
                      self.install)

    def test_ollama_section_shape(self):
        self.assertIn('OLLAMA_INSTALL_URL="https://ollama.com/install.sh"',
                      self.install)
        self.assertIn('OLLAMA_MODEL="qwen3:4b"', self.install)
        self.assertIn("ollama_server_up()", self.install)
        self.assertIn("ollama_model_present()", self.install)
        self.assertIn("pkexec sh \"$DOWNLOAD_CACHE/ollama-install.sh\"",
                      self.install)
        self.assertIn('"$OLLAMA_BIN" pull "$OLLAMA_MODEL"', self.install)
        self.assertIn("pkexec systemctl enable --now ollama", self.install)
        # A failed pull is a warning, not a failure.
        self.assertIn("The local model (Ollama) is missing",
                      self.install)

    def test_no_manual_fix_text_remains(self):
        for stale in ("README's install options",
                      "Set the GEMINI_API_KEY environment variable",
                      "see the README's install options"):
            self.assertNotIn(stale, self.install)

    def test_no_sudo(self):
        self.assertNotIn("sudo", self.install)


# --- Installer sims (staged runs, stubbed downloads) -------------------------
# The sandbox cannot pull ~7 GB, so these sims stub the engines and the
# network and verify the real code paths: flag defaults, progress
# steps, disclosure, the Ollama install command shape, and the model
# pull. The real-download success path is NOT faked here.

STUB_PYTHON3 = """#!/bin/sh
# Fakes the venv path: python3 -m venv creates .venv/bin/python from
# the fake venv python; python3 -m unittest is faked too (the staged
# installer must NOT run the real suite — the real suite contains
# installer sims, which would stage another installer and recurse).
# Anything else is passed to the real python3.
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$(dirname "$0")/fake-venv-python" "$3/bin/python"
  chmod +x "$3/bin/python"
  echo "fake venv created" >&2
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "unittest" ]; then
  echo "Ran 1 test in 0.0s"
  echo ""
  echo "OK"
  exit 0
fi
exec /usr/bin/python3 "$@"
"""

FAKE_VENV_PYTHON = """#!/bin/sh
# The staged installer runs the suite with the venv python; fake it.
if [ "$1" = "-m" ] && [ "$2" = "unittest" ]; then
  echo "Ran 1 test in 0.001s"
  echo "OK"
  exit 0
fi
if [ "$1" = "-c" ]; then exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi
exit 0
"""

STUB_PKEXEC = """#!/bin/sh
echo "pkexec $*" >> "$PKEXEC_LOG"
exit "${PKEXEC_EXIT:-0}"
"""

STUB_BLENDER_PINNED = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "Blender 4.5.9"; exit 0; fi
exit 0
"""

STUB_SD_CLI = """#!/bin/sh
exit 0
"""

STUB_OLLAMA = """#!/bin/sh
echo "ollama $*" >> "$OLLAMA_LOG"
case "$1" in
  --version) echo "ollama version 0.40.16-stub" ;;
  pull) exit "${STUB_OLLAMA_PULL_EXIT:-0}" ;;
esac
exit 0
"""

STUB_CURL_FAIL = """#!/bin/sh
echo "curl $*" >> "$CURL_LOG"
exit 1
"""

# Stub curl that "downloads" successfully: writes fake bytes to the
# -o target and exits 0. Used where the sim must reach the pkexec
# step without touching the real network.
STUB_CURL_OK = """#!/bin/sh
echo "curl $*" >> "$CURL_LOG"
prev=""
for arg in "$@"; do
  if [ "$prev" = "-o" ]; then echo "fake-download-bytes" > "$arg"; fi
  prev="$arg"
done
exit 0
"""


class StagedInstallerV18Tests(unittest.TestCase):
    def _stage(self, tmp, stubs, seed_mpfb=True, seed_model=True):
        tmp = Path(tmp)
        stage = tmp / "stage"
        shutil.copytree(ROOT, stage,
                        ignore=shutil.ignore_patterns("__pycache__",
                                                      ".venv"))
        (stage / "install.sh").chmod(0o755)
        (stage / "run.sh").chmod(0o755)
        home = tmp / "home"
        home.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        scripts = {"python3": STUB_PYTHON3,
                   "fake-venv-python": FAKE_VENV_PYTHON,
                   "pkexec": STUB_PKEXEC, **stubs}
        for name, text in scripts.items():
            path = bin_dir / name
            path.write_text(text)
            path.chmod(0o755)
        if seed_mpfb:
            mpfb_dir = (home / ".config" / "blender" / "4.5" / "extensions"
                        / "user_default" / "mpfb")
            (mpfb_dir / "data" / "skins").mkdir(parents=True)
            (mpfb_dir / "__init__.py").write_text("# stub MPFB\n")
        if seed_model:
            model_home = home / "design-shell-models"
            model_home.mkdir()
            (model_home / "other-model.gguf").write_bytes(b"fake-model")
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        env["PKEXEC_LOG"] = str(tmp / "pkexec.log")
        env["OLLAMA_LOG"] = str(tmp / "ollama.log")
        env["CURL_LOG"] = str(tmp / "curl.log")
        env["DESIGNSHELL_SKIP_E2E"] = "1"
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env.pop("DESIGNSHELL_BLENDER", None)
        env.pop("DESIGNSHELL_SD_CLI", None)
        if seed_model:
            # v1.9 (N5): only the exact model file counts as present —
            # point the installer at the stand-in with the override.
            env["DESIGNSHELL_SD_MODEL"] = str(
                home / "design-shell-models" / "other-model.gguf")
        else:
            env.pop("DESIGNSHELL_SD_MODEL", None)
        return stage, home, env, tmp

    def _run_install(self, stage, env, *args):
        return subprocess.run([str(stage / "install.sh"), *args],
                              capture_output=True, text=True, env=env,
                              timeout=120)

    def _all_engines(self):
        return {"blender": STUB_BLENDER_PINNED, "sd-cli": STUB_SD_CLI,
                "ollama": STUB_OLLAMA}

    def test_default_install_reaches_every_engine_and_skips(self):
        # The default run (no flags at all) reaches every engine
        # section and skips each one as already installed.
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, self._all_engines())
            with _MockOllamaServer(["qwen3:4b"]) as server:
                env["DESIGNSHELL_OLLAMA_BASE"] = (
                    f"http://127.0.0.1:{server.port}")
                res = self._run_install(stage, env, "--terminal")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("up to about 7.7 GB", res.stdout)
            self.assertIn("Blender 4.5.9 is already installed", res.stdout)
            self.assertIn("Character Studio is already installed",
                          res.stdout)
            self.assertIn("the local model (qwen3:4b) is ready",
                          res.stdout)
            # No pull was attempted — the model was already there.
            ollama_log = tmp_path / "ollama.log"
            if ollama_log.exists():
                self.assertNotIn("pull", ollama_log.read_text())
            # No pkexec ran at all — nothing needed installing.
            self.assertFalse((tmp_path / "pkexec.log").exists())

    def test_pinned_blender_is_not_reinstalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, {**self._all_engines(), "curl": STUB_CURL_FAIL})
            res = self._run_install(stage, env, "--terminal")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("Blender 4.5.9 is already installed", res.stdout)
            curl_log = tmp_path / "curl.log"
            if curl_log.exists():
                log_text = curl_log.read_text()
                self.assertNotIn("blender-4.5.9", log_text)
                self.assertNotIn("download.blender.org", log_text)

    def test_blender_missing_and_download_fails_is_fatal(self):
        # No Blender stub, curl fails: the install refuses plainly.
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, {"sd-cli": STUB_SD_CLI, "ollama": STUB_OLLAMA,
                      "curl": STUB_CURL_FAIL}, seed_mpfb=False)
            res = self._run_install(stage, env, "--terminal")
            self.assertNotEqual(res.returncode, 0)
            # In terminal mode gui_fail prints nothing (it is a
            # GUI-mode marker) — the visible refusal is the
            # download helper's own error.
            self.assertIn("could not download Blender 4.5.9",
                          res.stdout + res.stderr)

    def test_ollama_install_declined_warns_and_continues(self):
        # No ollama binary; the pkexec prompt is declined. The
        # install still completes (exit 0) and says the local model
        # is missing and the Setup check can fetch it.
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, {"blender": STUB_BLENDER_PINNED,
                      "sd-cli": STUB_SD_CLI,
                      "curl": STUB_CURL_OK})
            env["PKEXEC_EXIT"] = "1"
            res = self._run_install(stage, env, "--terminal")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("Ollama", res.stdout)
            pkexec_log = (tmp_path / "pkexec.log").read_text()
            self.assertIn("ollama-install.sh", pkexec_log)

    def test_ollama_model_pull_failure_warns_and_continues(self):
        # Server up, model missing, pull fails: exit 0, and the log
        # names the Setup check as the fix.
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, self._all_engines())
            env["STUB_OLLAMA_PULL_EXIT"] = "1"
            with _MockOllamaServer([]) as server:
                env["DESIGNSHELL_OLLAMA_BASE"] = (
                    f"http://127.0.0.1:{server.port}")
                res = self._run_install(stage, env, "--terminal")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("could not be pulled", res.stdout)
            self.assertIn("Setup check", res.stdout)
            ollama_log = (tmp_path / "ollama.log").read_text()
            self.assertIn("pull qwen3:4b", ollama_log)

    def test_with_flags_still_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, tmp_path = self._stage(
                tmp, self._all_engines())
            with _MockOllamaServer(["qwen3:4b"]) as server:
                env["DESIGNSHELL_OLLAMA_BASE"] = (
                    f"http://127.0.0.1:{server.port}")
                res = self._run_install(
                    stage, env, "--terminal", "--with-blender",
                    "--with-image-engine", "--with-character-studio",
                    "--with-ollama")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


# --- Qt tests (skipped without PySide6) --------------------------------------
try:
    from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
    from skullpt import qtbench, qtdesign, qtwindows  # noqa: E402
    HAS_QT = True
except ImportError:
    HAS_QT = False


class _BenchStub:
    def __init__(self, workspace):
        self.workspace = workspace
        self._setup_dismissed = False
        self._child_windows = {}
        self.refreshed = 0
        self.opened = []

    def refresh(self):
        self.refreshed += 1

    def _open_window(self, name):
        self.opened.append(name)


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtKeySectionTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.bench_stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_for(self, condition, timeout_ms=5000):
        deadline = QtCore.QDeadlineTimer(timeout_ms)
        while not condition():
            if deadline.hasExpired():
                return False
            QtTest.QTest.qWait(50)
        return True

    def test_save_validates_then_saves(self):
        window = qtwindows.SettingsWindow(self.bench_stub, self.ws)
        with _MockKeyServer(FAKE_GEMINI_KEY) as server:
            url = f"http://127.0.0.1:{server.port}/v1beta/models"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_GEMINI_VALIDATE_URL": url}):
                field = window._key_fields["gemini"]
                field.setText(FAKE_GEMINI_KEY)
                window._save_key("gemini")
                saved = self._wait_for(
                    lambda: tokenfile.has_key("gemini", self.ws))
        self.assertTrue(saved, "the validated key was never saved")
        self.assertEqual(tokenfile.load_key("gemini", self.ws),
                         FAKE_GEMINI_KEY)
        window.close()

    def test_invalid_key_is_never_saved(self):
        window = qtwindows.SettingsWindow(self.bench_stub, self.ws)
        with _MockKeyServer(FAKE_GEMINI_KEY) as server:
            url = f"http://127.0.0.1:{server.port}/v1beta/models"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_GEMINI_VALIDATE_URL": url}):
                field = window._key_fields["gemini"]
                field.setText("AIza-wrong-key")
                window._save_key("gemini")
                shown = self._wait_for(
                    lambda: "did not work" in
                    window._key_statuses["gemini"].text())
        self.assertTrue(shown, "no failure message appeared")
        self.assertFalse(tokenfile.has_key("gemini", self.ws))
        window.close()

    def test_clear_key_works(self):
        tokenfile.save_key("groq", self.ws, FAKE_GROQ_KEY)
        window = qtwindows.SettingsWindow(self.bench_stub, self.ws)
        self.assertIn("saved", window._key_statuses["groq"].text().lower())
        window._clear_key("groq")
        self.assertFalse(tokenfile.has_key("groq", self.ws))
        window.close()

    def test_engine_status_shows_ollama_and_groq_lines(self):
        window = qtwindows.SettingsWindow(self.bench_stub, self.ws)
        self.assertIn("ollama", window._engine_labels)
        self.assertIn("groq_key", window._engine_labels)
        self.assertIn("gemini_key", window._engine_labels)
        window.close()

    def test_engine_status_labels_wrap(self):
        # S6 regression: the long fix text that ran off the edge in
        # the real-PC screenshot must wrap.
        window = qtwindows.SettingsWindow(self.bench_stub, self.ws)
        for key, label in window._engine_labels.items():
            self.assertTrue(label.wordWrap(),
                            f"engine value label {key} does not wrap")
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtSetupWindowTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.bench_stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_missing_items_with_fix_buttons(self):
        items = [{"key": "blender", "title": "Blender",
                  "detail": "Not installed.", "fix": "installer"},
                 {"key": "ollama", "title": "Ollama",
                  "detail": "The model is missing.", "fix": "pull_model"},
                 {"key": "gemini_key", "title": "Gemini key",
                  "detail": "No key saved.", "fix": "settings"}]
        with mock.patch.object(bench, "missing_setup_items",
                               return_value=items):
            window = qtwindows.SetupWindow(self.bench_stub, self.ws)
            buttons = window.findChildren(QtWidgets.QPushButton)
            texts = [b.text() for b in buttons]
            self.assertIn("Install", texts)
            self.assertIn("Pull the model", texts)
            self.assertIn("Add in Settings", texts)
            self.assertIn("Check again", texts)
            self.assertIn("Remind me later", texts)
            window.close()

    def test_everything_ready_state(self):
        with mock.patch.object(bench, "missing_setup_items",
                               return_value=[]):
            window = qtwindows.SetupWindow(self.bench_stub, self.ws)
            labels = window.findChildren(QtWidgets.QLabel)
            texts = [label.text() for label in labels]
            self.assertTrue(any("Everything is ready" in t for t in texts),
                            texts)
            window.close()

    def test_remind_later_dismisses_until_next_launch(self):
        window = qtwindows.SetupWindow(self.bench_stub, self.ws)
        window._remind_later()
        self.assertTrue(self.bench_stub._setup_dismissed)
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtS6GeometryTests(unittest.TestCase):
    """S6: windows open maximized on first run; a saved tiny
    geometry falls back to maximized; a saved usable geometry wins."""

    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.settings = QtCore.QSettings("BrokeNBored", "SKULLPT")
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_window_shows_maximized(self):
        self.settings.remove("SettingsWindow/geometry")
        window = qtwindows.SettingsWindow(_BenchStub(self.ws), self.ws)
        qtwindows.show_window(window)
        QtTest.QTest.qWait(100)
        self.assertTrue(window.isMaximized(),
                        "a fresh Settings window did not open maximized")
        window.close()

    def test_saved_tiny_geometry_falls_back_to_maximized(self):
        probe = QtWidgets.QWidget()
        probe.resize(300, 200)
        self.settings.setValue("SettingsWindow/geometry",
                               probe.saveGeometry())
        window = qtwindows.SettingsWindow(_BenchStub(self.ws), self.ws)
        qtwindows.show_window(window)
        QtTest.QTest.qWait(100)
        self.assertTrue(window.isMaximized(),
                        "a saved 300x200 geometry reopened tiny")
        window.close()
        self.settings.remove("SettingsWindow/geometry")

    def test_saved_usable_geometry_is_respected(self):
        probe = QtWidgets.QWidget()
        probe.resize(1000, 600)
        self.settings.setValue("SettingsWindow/geometry",
                               probe.saveGeometry())
        window = qtwindows.SettingsWindow(_BenchStub(self.ws), self.ws)
        qtwindows.show_window(window)
        QtTest.QTest.qWait(100)
        self.assertFalse(window.isMaximized(),
                         "a saved usable geometry was maximized anyway")
        self.assertGreaterEqual(window.width(), 990)
        window.close()
        self.settings.remove("SettingsWindow/geometry")

    def test_restore_geometry_reports_saved_maximized(self):
        probe = QtWidgets.QWidget()
        probe.resize(800, 600)
        probe.showMaximized()
        QtTest.QTest.qWait(50)
        self.settings.setValue("HelpWindow/geometry",
                               probe.saveGeometry())
        probe.close()
        window = QtWidgets.QWidget()
        self.assertTrue(qtwindows.restore_geometry(window, "HelpWindow"))
        self.assertTrue(window.isMaximized())
        self.settings.remove("HelpWindow/geometry")


if __name__ == "__main__":
    unittest.main()
