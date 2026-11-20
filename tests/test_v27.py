"""SKULLPT v1.17 tests: the third user-audit fix round (E1-E4).

Backend tests run everywhere; Qt tests skip without PySide6. E1 is
the round's substance: the workspace in use rides through the
update hand-off, so the relaunched Bench reopens it and the
"Updated to vX" note lands in its chat. Its tests drive the full
cycle against the fake install + fake release server from
test_v25, with a non-default workspace.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt import updater as updater_mod  # noqa: E402
from skullpt import update as update_mod  # noqa: E402

from test_v25 import (  # noqa: E402
    _FakeBench,
    _FakeReleaseServer,
    _QtCase,
    _dead_pid,
    _make_fake_install,
    _release_result,
    _workspace,
)

WOOD_SPEC = {"family": "wood", "style": "walnut", "layout": "boards",
             "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}


# ------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.17 everywhere."""

    def test_version_is_117(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.17")
        self.assertIn("v1.17", (ROOT / "README.md").read_text())
        self.assertIn("## v1.17", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.17", (ROOT / "DESIGN.md").read_text())

    def test_changelog_names_the_round(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        section = text.split("## v1.17", 1)[1].split("## v1.16", 1)[0]
        for item in ("E1", "E2", "E3", "E4"):
            self.assertIn(item, section)


# ----------------------------------------------------------------- E1


def _recording_release_zip(version: str) -> bytes:
    """A fake release zip like test_v25's, but its run.sh records
    the arguments the relaunch was given, one line per launch, so
    a test can see exactly where the relaunched Bench was sent."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("skullpt/skullpt/__init__.py",
                    f'"""Fake SKULLPT."""\n\n__version__ = "{version}"\n')
        zf.writestr("skullpt/skullpt/bench.py", "# new bench\n")
        zf.writestr("skullpt/run.sh",
                    '#!/bin/sh\necho "$@" >> relaunches.log\n')
        zf.writestr("skullpt/install.sh", "#!/bin/sh\nexit 0\n")
    return buf.getvalue()


class E1WorkspaceCarriedTests(unittest.TestCase):
    """E1: the workspace in use rides through the update hand-off:
    recorded at the pull, passed on both hand-offs, written into
    the updated marker, and given to the relaunch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = _make_fake_install(self.root)
        self.zip_bytes = _recording_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})
        self.ws_a = self.root / "workspace-a"
        self.ws_b = self.root / "workspace-b"
        self.ws_a.mkdir()
        self.ws_b.mkdir()

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def _stage(self, workspace=None) -> None:
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install,
            workspace=workspace)
        self.assertTrue(updater_mod.stage_is_pending(self.install))

    def _pending_record(self) -> dict:
        return json.loads(
            (self.install / ".pending-update" / "pending.json")
            .read_text())

    def _relaunch_line(self) -> str:
        """The one line the relaunched run.sh recorded (an empty
        line when the relaunch was given no arguments)."""
        log = self.install / "relaunches.log"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if log.exists() and log.stat().st_size > 0:
                lines = log.read_text().splitlines()
                return lines[0] if lines else ""
            time.sleep(0.1)
        self.fail("the relaunch never ran")

    def _marker_info(self) -> dict:
        return json.loads(
            (self.install / ".updated-to-9.9").read_text())

    def test_stage_records_the_workspace(self):
        self._stage(workspace=self.ws_b)
        self.assertEqual(self._pending_record()["workspace"],
                         str(self.ws_b))

    def test_stage_without_a_workspace_keeps_the_old_shape(self):
        self._stage()
        self.assertNotIn("workspace", self._pending_record())

    def test_apply_relaunches_the_carried_workspace(self):
        """The full cycle with a non-default workspace: apply,
        and the relaunch is aimed at that workspace; the marker
        names it too, and D1's guarantees hold throughout."""
        self._stage(workspace=self.ws_b)
        self.assertEqual(
            updater_mod.run_update(self.install, _dead_pid()), 0)
        self.assertEqual(self._relaunch_line(),
                         f"bench {self.ws_b}")
        self.assertEqual(self._marker_info()["workspace"],
                         str(self.ws_b))
        # D1 holds: the stage went with the apply, and the next
        # launches open the window instead of looping.
        self.assertFalse(updater_mod.stage_is_pending(self.install))
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            self.assertIsNone(
                updater_mod.handle_pending_at_startup(self.install))
            spawn.assert_not_called()
        # The note is there for the Bench that opens on ws_b.
        self.assertEqual(
            updater_mod.consume_update_marker(self.install),
            ("9.9", None))

    def test_apply_without_a_workspace_relaunches_plain(self):
        """A stage recorded by an older version (no workspace in
        it) relaunches exactly as before: plain run.sh."""
        self._stage()
        self.assertEqual(
            updater_mod.run_update(self.install, _dead_pid()), 0)
        self.assertEqual(self._relaunch_line(), "")
        self.assertNotIn("workspace", self._marker_info())

    def test_run_update_argument_wins_over_the_record(self):
        self._stage(workspace=self.ws_a)
        self.assertEqual(
            updater_mod.run_update(self.install, _dead_pid(),
                                   workspace=self.ws_b), 0)
        self.assertEqual(self._relaunch_line(),
                         f"bench {self.ws_b}")
        self.assertEqual(self._marker_info()["workspace"],
                         str(self.ws_b))

    def test_deferred_handoff_prefers_the_launching_workspace(self):
        self._stage(workspace=self.ws_a)
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            result = updater_mod.handle_pending_at_startup(
                self.install, workspace=str(self.ws_b))
        self.assertEqual(result, "restarting")
        spawn.assert_called_once_with(
            self.install, os.getpid(), str(self.ws_b))

    def test_deferred_handoff_falls_back_to_the_staged_workspace(self):
        self._stage(workspace=self.ws_a)
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            result = updater_mod.handle_pending_at_startup(
                self.install)
        self.assertEqual(result, "restarting")
        spawn.assert_called_once_with(
            self.install, os.getpid(), str(self.ws_a))

    def test_deferred_handoff_without_any_workspace_is_unchanged(self):
        self._stage()
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            result = updater_mod.handle_pending_at_startup(
                self.install)
        self.assertEqual(result, "restarting")
        spawn.assert_called_once_with(self.install, os.getpid())

    def test_spawn_updater_passes_the_workspace_through(self):
        with mock.patch.object(updater_mod.subprocess,
                               "Popen") as popen:
            updater_mod.spawn_updater(self.install, 123, self.ws_b)
        argv = popen.call_args.args[0]
        self.assertIn("--workspace", argv)
        self.assertEqual(argv[argv.index("--workspace") + 1],
                         str(self.ws_b))

    def test_spawn_updater_without_a_workspace_is_unchanged(self):
        with mock.patch.object(updater_mod.subprocess,
                               "Popen") as popen:
            updater_mod.spawn_updater(self.install, 123)
        argv = popen.call_args.args[0]
        self.assertNotIn("--workspace", argv)


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class E1BenchSideTests(_QtCase):
    """E1 at the Bench: a launch on a non-default workspace hands
    its own workspace to the updater, and the relaunched Bench on
    that workspace is the one that reads the outcome marker, so
    the note lands in its chat."""

    def setUp(self):
        super().setUp()
        self.install_root = Path(self.tmp.name) / "install-root"
        self.install_root.mkdir()
        self.install = _make_fake_install(self.install_root)
        self.zip_bytes = _recording_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})

    def tearDown(self):
        self.server.close()
        super().tearDown()

    def test_main_hands_its_workspace_to_the_updater(self):
        from PySide6 import QtWidgets

        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install,
            workspace=self.ws)
        with mock.patch.object(updater_mod, "install_dir",
                               return_value=self.install), \
                mock.patch.object(updater_mod,
                                  "spawn_updater") as spawn, \
                mock.patch.object(QtWidgets, "QApplication",
                                  return_value=self._app):
            result = qtbench.main(self.ws)
        self.assertEqual(result, 0)
        spawn.assert_called_once_with(
            self.install, os.getpid(), self.ws)
        # The updater owns the stage now; it is still there.
        self.assertTrue(updater_mod.stage_is_pending(self.install))

    def test_the_note_lands_in_the_relaunched_workspace_chat(self):
        from PySide6 import QtWidgets

        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install,
            workspace=self.ws)
        self.assertEqual(
            updater_mod.run_update(self.install, _dead_pid()), 0)
        window = self._bench()
        with mock.patch.object(updater_mod, "install_dir",
                               return_value=self.install), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "information") as info:
            window._announce_update_outcome()
        info.assert_called_once()
        history = bench_mod.chat_history(self.ws)
        self.assertTrue(any("Updated to v9.9." in entry["text"]
                            for entry in history))
        # Consumed: a second announce finds nothing left to say.
        self.assertEqual(
            updater_mod.consume_update_marker(self.install),
            (None, None))


# ----------------------------------------------------------------- E2


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class E2DialSavePresetTests(_QtCase):
    """E2: the Dial's button says "Save preset" - what it does.
    Applying the material to the part stays on Close."""

    def _dial(self):
        from skullpt.qtwindows import MaterialDial

        dial = self._track(MaterialDial(
            self.ws, "plank", json.loads(json.dumps(WOOD_SPEC))))
        dial.show()
        self._pump(200)
        return dial

    def test_no_button_is_called_just_save(self):
        from PySide6 import QtWidgets

        dial = self._dial()
        texts = [btn.text() for btn in
                 dial.findChildren(QtWidgets.QPushButton)]
        self.assertIn("Save preset", texts)
        self.assertNotIn("Save", texts)

    def test_save_preset_still_opens_the_preset_row(self):
        """The behaviour did not change: the renamed button opens
        the preset-name row, like the old Save did."""
        from PySide6 import QtWidgets

        dial = self._dial()
        self.assertFalse(dial._preset_row.isVisible())
        for btn in dial.findChildren(QtWidgets.QPushButton):
            if btn.text() == "Save preset" and btn.isVisibleTo(dial):
                btn.click()
                break
        self._pump(100)
        self.assertTrue(dial._preset_row.isVisible())

    def test_help_names_the_button_the_same_way(self):
        text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        flat = " ".join(text.split())
        self.assertIn("Save preset stores the material as a "
                      "preset for later", flat)
        self.assertNotIn("Save stores the material", flat)


# ----------------------------------------------------------------- E3


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class E3RecipesPaneTests(_QtCase):
    """E3: the Recipes detail pane agrees with its list: a pick
    prompt when recipes exist but none is picked, the empty text
    only when the list is truly empty."""

    def _recipe(self, ws: Path) -> None:
        recipes = ws / "recipes"
        recipes.mkdir(exist_ok=True)
        (recipes / "oak-table.recipe.json").write_text(json.dumps({
            "name": "Oak Table", "kind": "furniture",
            "graduated_from": "oak-table",
            "template": {"summary": "A sturdy oak table.",
                         "parts": [{"id": "top"}, {"id": "leg"}]},
        }), encoding="utf-8")

    def _window(self, ws: Path):
        from skullpt.qtwindows import RecipesWindow

        window = self._track(
            RecipesWindow(_FakeBench(ws), ws))
        window.show()
        self._pump(200)
        return window

    def test_recipes_exist_none_picked_asks_for_a_pick(self):
        self._recipe(self.ws)
        window = self._window(self.ws)
        self.assertEqual(window._list.count(), 1)
        self.assertEqual(window._detail.text(),
                         "Pick a recipe to see it here.")
        self.assertFalse(window._start_btn.isEnabled())

    def test_no_recipes_shows_the_empty_text(self):
        window = self._window(self.ws)
        self.assertEqual(window._list.count(), 0)
        self.assertTrue(window._detail.text().startswith(
            "No recipes yet."))

    def test_picking_a_recipe_fills_the_pane(self):
        self._recipe(self.ws)
        window = self._window(self.ws)
        window._on_recipe(window._list.item(0))
        self.assertIn("Oak Table", window._detail.text())
        self.assertTrue(window._start_btn.isEnabled())


# ----------------------------------------------------------------- E4


class E4PlainUpdateCheckTests(unittest.TestCase):
    """E4: a failed update check is plain words at the source, so
    every surface gets the plain version: no exception text, no
    "urlopen", no errno."""

    PLAIN = ("could not reach GitHub - check your connection "
             "and try again")

    def test_unreachable_error_is_plain_words(self):
        with mock.patch.object(
                urllib.request, "urlopen",
                side_effect=urllib.error.URLError(
                    "<urlopen error [Errno 111] "
                    "Connection refused>")):
            result = update_mod.check_for_update("1.17")
        self.assertEqual(result["error"], self.PLAIN)
        self.assertNotIn("urlopen", result["error"])
        self.assertNotIn("Errno", result["error"])
        self.assertNotIn("(", result["error"])
        self.assertIsNone(result["latest"])
        self.assertFalse(result["update_available"])

    def test_http_error_is_plain_words_too(self):
        err = urllib.error.HTTPError(
            "https://api.github.com", 503, "Service Unavailable",
            None, None)
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=err):
            result = update_mod.check_for_update("1.17")
        self.assertIn("GitHub answered with an error",
                      result["error"])
        self.assertNotIn("urlopen", result["error"])

    def test_no_surface_formats_the_raw_error(self):
        """The manual dialog's exception path used to format the
        exception itself into the message; both sources now carry
        only the plain sentence."""
        bench_text = (ROOT / "skullpt" / "qtbench.py").read_text()
        self.assertNotIn("could not reach GitHub ({result})",
                         bench_text)
        update_text = (ROOT / "skullpt" / "update.py").read_text()
        self.assertNotIn("could not reach GitHub ({exc})",
                         update_text)


if __name__ == "__main__":
    unittest.main()
