"""SKULLPT v1.18 tests: the Blender add-on manager (F1-F6).

Backend tests fake the one Blender-runner seam in skullpt/addons.py
and drive everything else for real, including the pure-Python
package inspection. Window tests fake at the addons.py function
seam, never at Qt internals. The real-Blender cycle runs only
where a Blender binary is found (the sandbox 4.5.9 included, via
DESIGNSHELL_BLENDER): it installs a throwaway add-on into a
throwaway Blender config and walks the whole life of it.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import addons as addons_mod  # noqa: E402
from skullpt import qtbench  # noqa: E402

from test_v25 import _FakeBench, _QtCase  # noqa: E402

ADDON_INFO = {
    "module": "vanish_fx", "name": "VanishFX", "version": "1.2.0",
    "author": "A Maker", "description": "Dissolves things.",
    "enabled": True,
}

_INIT_WITH_INFO = (
    "bl_info = {\n"
    '    "name": "VanishFX",\n'
    '    "version": (1, 2, 0),\n'
    "}\n\n"
    "def register():\n"
    "    pass\n\n"
    "def unregister():\n"
    "    pass\n")


def _zip_bytes(entries: dict[str, str]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buf.getvalue()


class _FakeRunner:
    """The addons.py Blender seam, faked: records every script
    and answers with the marker payload the test staged for the
    operation the script performs."""

    def __init__(self):
        self.scripts: list[str] = []
        self.list_payload: list = []
        self.install_payload: dict = {"ok": True, "addon": ADDON_INFO}
        self.toggle_payload: dict = {
            "ok": True, "module": "vanish_fx", "enabled": True}
        self.remove_payload: dict = {
            "ok": True, "module": "vanish_fx", "files_gone": True}

    def __call__(self, script, timeout=0, env_extra=None):
        self.scripts.append(script)
        if "addon_install" in script:
            return ("noise line\n" + addons_mod.MARKER_RESULT
                    + json.dumps(self.install_payload) + "\n")
        if "addon_remove" in script:
            return (addons_mod.MARKER_RESULT
                    + json.dumps(self.remove_payload) + "\n")
        if "addon_enable" in script or "addon_disable" in script:
            return (addons_mod.MARKER_RESULT
                    + json.dumps(self.toggle_payload) + "\n")
        return ("Blender banner noise\n" + addons_mod.MARKER_LIST
                + json.dumps(self.list_payload) + "\n")


# ------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.18 everywhere."""

    def test_version_is_118(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.18")
        self.assertIn("v1.18", (ROOT / "README.md").read_text())
        self.assertIn("## v1.18", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.18", (ROOT / "DESIGN.md").read_text())

    def test_changelog_names_the_round(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        section = text.split("## v1.18", 1)[1].split("## v1.17", 1)[0]
        for item in ("F1", "F2", "F3", "F4", "F5", "F6"):
            self.assertIn(item, section)


# ------------------------------------------------- F5: package checks


class InspectPackageTests(unittest.TestCase):
    """The pure-Python package inspection: what can be known
    about a picked file before Blender ever runs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_folder_addon_zip_names_its_module(self):
        path = self._write("vanish.zip", _zip_bytes({
            "vanish_fx/__init__.py": _INIT_WITH_INFO,
            "vanish_fx/core.py": "# code\n",
            "README.md": "hello\n"}))
        self.assertEqual(addons_mod.inspect_package(path), "vanish_fx")

    def test_single_file_zip_names_its_module(self):
        path = self._write("one.zip", _zip_bytes({
            "vanish_fx.py": _INIT_WITH_INFO}))
        self.assertEqual(addons_mod.inspect_package(path), "vanish_fx")

    def test_loose_py_file_names_its_stem(self):
        path = self._write("vanish_fx.py",
                           _INIT_WITH_INFO.encode())
        self.assertEqual(addons_mod.inspect_package(path), "vanish_fx")

    def test_extension_zip_is_refused_before_blender(self):
        path = self._write("ext.zip", _zip_bytes({
            "blender_manifest.toml": 'id = "vanish_fx"\n',
            "vanish_fx/__init__.py": _INIT_WITH_INFO}))
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("extension package", str(caught.exception))
        self.assertIn("classic add-ons", str(caught.exception))

    def test_zip_with_no_addon_is_refused(self):
        path = self._write("empty.zip", _zip_bytes({
            "notes.txt": "nothing here\n",
            "folder/readme.md": "still nothing\n"}))
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("No add-on was found", str(caught.exception))

    def test_zip_without_bl_info_is_refused(self):
        path = self._write("noinfo.zip", _zip_bytes({
            "thing/__init__.py": "def register():\n    pass\n"}))
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("No add-on was found", str(caught.exception))

    def test_top_level_init_zip_is_refused_as_mispackaged(self):
        path = self._write("flat.zip", _zip_bytes({
            "__init__.py": _INIT_WITH_INFO,
            "core.py": "# code\n"}))
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("top level", str(caught.exception))

    def test_other_file_types_are_refused(self):
        path = self._write("addon.txt", b"bl_info = {}\n")
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn(".zip or a .py", str(caught.exception))

    def test_missing_file_is_refused(self):
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(self.root / "gone.zip")
        self.assertIn("could not be found", str(caught.exception))

    def test_damaged_zip_is_refused(self):
        path = self._write("bad.zip", b"this is not a zip at all")
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("could not be read as a zip",
                      str(caught.exception))

    def test_py_without_bl_info_is_refused(self):
        path = self._write("plain.py", b"print('hello')\n")
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.inspect_package(path)
        self.assertIn("No add-on was found", str(caught.exception))


# ------------------------------------------------------------ F1: list


class ListAddonsTests(unittest.TestCase):
    """F1: the list comes from one Blender query, parsed off its
    marker line, noise and all."""

    def setUp(self):
        self.runner = _FakeRunner()
        self.patch = mock.patch.object(
            addons_mod, "_run_blender_script", self.runner)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_list_parses_the_marker_json(self):
        self.runner.list_payload = [ADDON_INFO]
        self.assertEqual(addons_mod.list_addons(), [ADDON_INFO])

    def test_list_script_asks_blender_for_its_own_folder(self):
        addons_mod.list_addons()
        (script,) = self.runner.scripts
        self.assertIn("user_resource", script)
        self.assertIn('"addons"', script)
        self.assertIn("preferences.addons", script)

    def test_list_script_filters_to_the_user_folder(self):
        """User-installed only: the script keeps modules whose
        file lives under the user add-ons folder."""
        addons_mod.list_addons()
        (script,) = self.runner.scripts
        self.assertIn("commonpath", script)

    def test_no_marker_is_a_plain_failure(self):
        with mock.patch.object(
                addons_mod, "_run_blender_script",
                return_value="Blender printed nothing useful\n"):
            with self.assertRaises(addons_mod.AddonError) as caught:
                addons_mod.list_addons()
        self.assertIn("did not answer", str(caught.exception))


class NoBlenderTests(unittest.TestCase):
    """No Blender found: a plain refusal naming the fix, before
    any run is attempted."""

    def test_list_refuses_without_blender(self):
        with mock.patch.object(addons_mod, "find_blender",
                               return_value=None):
            self.assertFalse(addons_mod.blender_available())
            with self.assertRaises(addons_mod.AddonError) as caught:
                addons_mod.list_addons()
        self.assertIn("Setup check", str(caught.exception))

    def test_available_when_blender_is_found(self):
        with mock.patch.object(addons_mod, "find_blender",
                               return_value="/usr/bin/blender"):
            self.assertTrue(addons_mod.blender_available())


# --------------------------------------------------------- F2: install


class InstallAddonTests(unittest.TestCase):
    """F2: install through Blender's own installer, switched on,
    preferences saved. Installing over is the update path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.zip_path = self.root / "vanish.zip"
        self.zip_path.write_bytes(_zip_bytes({
            "vanish_fx/__init__.py": _INIT_WITH_INFO}))
        self.runner = _FakeRunner()
        self.patch = mock.patch.object(
            addons_mod, "_run_blender_script", self.runner)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_install_returns_the_installed_info(self):
        self.assertEqual(
            addons_mod.install_addon(self.zip_path), ADDON_INFO)

    def test_install_script_uses_blenders_installer(self):
        addons_mod.install_addon(self.zip_path)
        (script,) = self.runner.scripts
        self.assertIn("addon_install", script)
        self.assertIn("overwrite=True", script)
        self.assertIn("addon_enable", script)
        self.assertIn("save_userpref", script)
        self.assertIn(str(self.zip_path), script)

    def test_install_diffs_before_and_after(self):
        """The installed module is identified the way Blender
        identifies it: the list before against the list after."""
        addons_mod.install_addon(self.zip_path)
        (script,) = self.runner.scripts
        self.assertIn("before", script)
        self.assertIn("was_present", script)

    def test_refused_install_is_plain(self):
        self.runner.install_payload = {
            "ok": False, "error": "refused"}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.install_addon(self.zip_path)
        self.assertIn("refused to install", str(caught.exception))

    def test_enable_failure_on_a_new_addon_is_plain(self):
        self.runner.install_payload = {
            "ok": False, "error": "enable_failed_new",
            "detail": "boom"}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.install_addon(self.zip_path)
        self.assertIn("taken back out", str(caught.exception))

    def test_enable_failure_on_an_update_is_plain(self):
        self.runner.install_payload = {
            "ok": False, "error": "enable_failed_update",
            "detail": "boom"}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.install_addon(self.zip_path)
        self.assertIn("The new file is in", str(caught.exception))

    def test_extension_zip_never_reaches_blender(self):
        ext = self.root / "ext.zip"
        ext.write_bytes(_zip_bytes({
            "blender_manifest.toml": 'id = "x"\n',
            "vanish_fx/__init__.py": _INIT_WITH_INFO}))
        with self.assertRaises(addons_mod.AddonError):
            addons_mod.install_addon(ext)
        self.assertEqual(self.runner.scripts, [])


# ---------------------------------------------------- F3: switch/remove


class SetEnabledTests(unittest.TestCase):
    """F3 (switch): Blender's own enable and disable, preferences
    saved, the state read back afterwards."""

    def setUp(self):
        self.runner = _FakeRunner()
        self.patch = mock.patch.object(
            addons_mod, "_run_blender_script", self.runner)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_switch_on(self):
        result = addons_mod.set_enabled("vanish_fx", True)
        self.assertEqual(result["enabled"], True)
        (script,) = self.runner.scripts
        self.assertIn("addon_enable", script)
        self.assertIn("save_userpref", script)
        self.assertIn("'vanish_fx'", script)

    def test_switch_off(self):
        self.runner.toggle_payload = {
            "ok": True, "module": "vanish_fx", "enabled": False}
        result = addons_mod.set_enabled("vanish_fx", False)
        self.assertEqual(result["enabled"], False)
        (script,) = self.runner.scripts
        self.assertIn("addon_disable", script)

    def test_refused_switch_is_plain(self):
        self.runner.toggle_payload = {"ok": False, "error": "refused"}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.set_enabled("vanish_fx", True)
        self.assertIn("could not switch that add-on on",
                      str(caught.exception))


class RemoveAddonTests(unittest.TestCase):
    """F3 (remove): listed first, removed through Blender, and
    proven gone by a fresh list afterwards."""

    def setUp(self):
        self.runner = _FakeRunner()
        self.runner.list_payload = [ADDON_INFO]
        self.patch = mock.patch.object(
            addons_mod, "_run_blender_script", self.runner)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_remove_walks_the_whole_path(self):
        calls = {"lists": 0}

        def _run(script, timeout=0, env_extra=None):
            if "addon_remove" in script:
                self.runner.list_payload = []
                return self.runner(script, timeout, env_extra)
            calls["lists"] += 1
            return self.runner(script, timeout, env_extra)

        with mock.patch.object(addons_mod, "_run_blender_script",
                               _run):
            addons_mod.remove_addon("vanish_fx")
        self.assertEqual(calls["lists"], 2)
        remove_scripts = [s for s in self.runner.scripts
                          if "addon_remove" in s]
        self.assertEqual(len(remove_scripts), 1)
        script = remove_scripts[0]
        self.assertIn("addon_disable", script)
        self.assertIn("save_userpref", script)
        # The fallback is in the script: a Blender without the
        # remove command, or files that survive it, are deleted
        # directly.
        self.assertIn("hasattr", script)
        self.assertIn("shutil.rmtree", script)
        self.assertIn("os.unlink", script)

    def test_remove_of_a_missing_addon_is_plain(self):
        self.runner.list_payload = []
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.remove_addon("vanish_fx")
        self.assertIn("not installed", str(caught.exception))
        self.assertFalse(any("addon_remove" in s
                             for s in self.runner.scripts))

    def test_files_that_stay_are_plain(self):
        self.runner.remove_payload = {
            "ok": False, "error": "files_stay"}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.remove_addon("vanish_fx")
        self.assertIn("could not be deleted", str(caught.exception))

    def test_still_listed_afterwards_is_a_failure(self):
        self.runner.remove_payload = {
            "ok": True, "module": "vanish_fx", "files_gone": True}
        with self.assertRaises(addons_mod.AddonError) as caught:
            addons_mod.remove_addon("vanish_fx")
        self.assertIn("still lists", str(caught.exception))


# ------------------------------------------------------------ F4: window


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class AddonsWindowTests(_QtCase):
    """F4: the window at the faked backend seam: it opens, shows
    the empty state, renders a row with its controls, and says
    plainly when there is no Blender."""

    def _window(self, addons, available=True):
        from skullpt.qtwindows import AddonsWindow

        patches = [
            mock.patch.object(addons_mod, "blender_available",
                              return_value=available),
            mock.patch.object(addons_mod, "list_addons",
                              return_value=list(addons)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        window = self._track(
            AddonsWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._settle(window)
        return window

    def _settle(self, window, ms: int = 3000) -> None:
        """Pump until the window's worker call lands."""
        import time as time_mod

        deadline = time_mod.monotonic() + ms / 1000.0
        while window._call is not None \
                and time_mod.monotonic() < deadline:
            self._pump(50)
        self._pump(50)

    def _labels(self, window) -> list[str]:
        from PySide6 import QtWidgets

        return [label.text() for label in
                window.findChildren(QtWidgets.QLabel)]

    def test_window_opens_with_the_empty_state(self):
        window = self._window([])
        self.assertIn(
            "No add-ons installed yet. Install add-on... takes "
            "a .zip or .py file you downloaded.",
            self._labels(window))
        self.assertTrue(window._install_btn.isEnabled())

    def test_a_row_renders_with_its_controls(self):
        from PySide6 import QtWidgets

        window = self._window([ADDON_INFO])
        frames = [w for w in window.findChildren(QtWidgets.QFrame)
                  if w.property("module") == "vanish_fx"]
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        texts = [label.text() for label in
                 frame.findChildren(QtWidgets.QLabel)]
        self.assertIn("VanishFX", texts)
        self.assertTrue(any("1.2.0" in text for text in texts))
        self.assertTrue(any("Dissolves things." in text
                            for text in texts))
        boxes = frame.findChildren(QtWidgets.QCheckBox)
        self.assertEqual(len(boxes), 1)
        self.assertTrue(boxes[0].isChecked())
        buttons = [btn.text() for btn in
                   frame.findChildren(QtWidgets.QPushButton)]
        self.assertIn("Update...", buttons)
        self.assertIn("Remove", buttons)
        self.assertIn("1 add-on installed.", window._status.text())

    def test_toggle_switches_through_the_backend(self):
        from PySide6 import QtWidgets

        window = self._window([ADDON_INFO])
        calls = []

        def _fake_set_enabled(module, on):
            calls.append((module, on))
            return {"module": module, "enabled": on}

        off = dict(ADDON_INFO, enabled=False)
        with mock.patch.object(addons_mod, "set_enabled",
                               _fake_set_enabled), \
                mock.patch.object(addons_mod, "list_addons",
                                  return_value=[off]):
            frame = [w for w in window.findChildren(QtWidgets.QFrame)
                     if w.property("module") == "vanish_fx"][0]
            frame.findChild(QtWidgets.QCheckBox).click()
            self._settle(window)
        self.assertEqual(calls, [("vanish_fx", False)])
        self.assertEqual(window._status.text(),
                         "VanishFX switched off.")

    def test_install_flow_reports_in_plain_words(self):
        window = self._window([])
        with mock.patch.object(
                addons_mod, "install_addon",
                return_value=ADDON_INFO) as install, \
                mock.patch.object(
                    addons_mod, "list_addons",
                    return_value=[ADDON_INFO]), \
                mock.patch(
                    "PySide6.QtWidgets.QFileDialog.getOpenFileName",
                    return_value=("/tmp/vanish.zip", "")):
            window._install_btn.click()
            self._settle(window)
        install.assert_called_once_with("/tmp/vanish.zip")
        self.assertEqual(
            window._status.text(),
            "VanishFX 1.2.0 installed and switched on.")

    def test_remove_asks_once_then_reports(self):
        from PySide6 import QtWidgets

        window = self._window([ADDON_INFO])
        removed = []
        with mock.patch.object(
                addons_mod, "remove_addon",
                side_effect=lambda module: removed.append(module)), \
                mock.patch.object(addons_mod, "list_addons",
                                  return_value=[]), \
                mock.patch.object(
                    QtWidgets.QMessageBox, "question",
                    return_value=QtWidgets.QMessageBox.Yes) as ask:
            frame = [w for w in window.findChildren(QtWidgets.QFrame)
                     if w.property("module") == "vanish_fx"][0]
            for btn in frame.findChildren(QtWidgets.QPushButton):
                if btn.text() == "Remove":
                    btn.click()
            self._settle(window)
        ask.assert_called_once()
        self.assertIn("Remove VanishFX?",
                      ask.call_args.args[2])
        self.assertEqual(removed, ["vanish_fx"])
        self.assertEqual(window._status.text(), "VanishFX removed.")

    def test_no_blender_state_is_plain_and_points_at_setup(self):
        window = self._window([], available=False)
        labels = self._labels(window)
        self.assertTrue(any("Blender was not found" in text
                            for text in labels))
        self.assertTrue(any("Setup check" in text
                            for text in labels))
        self.assertFalse(window._install_btn.isEnabled())

    def test_a_failed_load_says_so_in_the_status(self):
        from skullpt.qtwindows import AddonsWindow

        with mock.patch.object(addons_mod, "blender_available",
                               return_value=True), \
                mock.patch.object(
                    addons_mod, "list_addons",
                    side_effect=addons_mod.AddonError(
                        "Blender ran but did not answer.")):
            window = self._track(
                AddonsWindow(_FakeBench(self.ws), self.ws))
            window.show()
            self._settle(window)
        self.assertEqual(window._status.text(),
                         "Blender ran but did not answer.")


# ------------------------------------------- F6: the real-Blender cycle

_SANDBOX_BLENDER = (Path.home() / "workspace" / "blender-sandbox"
                    / "blender" / "blender")


def _real_blender() -> str | None:
    found = addons_mod.find_blender()
    if found:
        return found
    if _SANDBOX_BLENDER.exists():
        return str(_SANDBOX_BLENDER)
    return None


_CYCLE_SOURCE = '''
bl_info = {
    "name": "Manager Test Addon",
    "version": %s,
    "author": "SKULLPT tests",
    "description": "Throwaway add-on for the manager cycle test.",
    "category": "Object",
}


def register():
    import bpy
    bpy.types.Scene.skullpt_manager_marker = "on"


def unregister():
    pass
'''


@unittest.skipUnless(_real_blender(), "no Blender binary found")
class RealBlenderCycleTests(unittest.TestCase):
    """F6: the whole life of an add-on against a real Blender,
    in a throwaway config: install, listed, off, on, updated,
    removed, files gone. Nothing outside the temp folder is
    touched: Blender is pointed at it with BLENDER_USER_CONFIG
    and BLENDER_USER_SCRIPTS."""

    def test_the_full_cycle(self):
        blender = _real_blender()
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        env = {"BLENDER_USER_CONFIG": str(root / "config"),
               "BLENDER_USER_SCRIPTS": str(root / "scripts")}

        def _make_zip(version: str, filename: str) -> Path:
            path = root / filename
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("manager_test_addon/__init__.py",
                            _CYCLE_SOURCE % version)
            return path

        v1 = _make_zip("(1, 0, 0)", "manager_test_v1.zip")
        v2 = _make_zip("(2, 0, 0)", "manager_test_v2.zip")
        try:
            with mock.patch.dict(
                    os.environ,
                    {"DESIGNSHELL_BLENDER": blender}):
                # Nothing user-installed yet: Blender's bundled
                # add-ons are not SKULLPT's to list.
                self.assertEqual(
                    addons_mod.list_addons(env_extra=env), [])
                info = addons_mod.install_addon(v1, env_extra=env)
                self.assertEqual(info["name"], "Manager Test Addon")
                self.assertEqual(info["version"], "1.0.0")
                self.assertTrue(info["enabled"])
                listed = addons_mod.list_addons(env_extra=env)
                self.assertEqual(
                    [item["module"] for item in listed],
                    ["manager_test_addon"])
                self.assertTrue(listed[0]["enabled"])

                result = addons_mod.set_enabled(
                    "manager_test_addon", False, env_extra=env)
                self.assertFalse(result["enabled"])
                listed = addons_mod.list_addons(env_extra=env)
                self.assertFalse(listed[0]["enabled"])

                result = addons_mod.set_enabled(
                    "manager_test_addon", True, env_extra=env)
                self.assertTrue(result["enabled"])

                info = addons_mod.install_addon(v2, env_extra=env)
                self.assertEqual(info["version"], "2.0.0")
                listed = addons_mod.list_addons(env_extra=env)
                self.assertEqual(listed[0]["version"], "2.0.0")

                addons_mod.remove_addon(
                    "manager_test_addon", env_extra=env)
                self.assertEqual(
                    addons_mod.list_addons(env_extra=env), [])
                self.assertFalse(
                    (root / "scripts" / "addons"
                     / "manager_test_addon").exists())
        finally:
            tmp.cleanup()


class KeycheckUserAgentTests(unittest.TestCase):
    """The 2026-11-26 fix: the key checks (and the seat calls) must
    name a user agent. Groq's edge answers the default Python user
    agent with HTTP 403 (error 1010) before the key is judged, and
    the old code read any 403 as "key not valid" - so good keys
    were refused on the real service while the mocks all passed.
    Proven against the real API: default agent 403, named agent
    401 for the same bad key."""

    def _server(self, answer):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.headers.get("User-Agent"))
                body = b"{}"
                self.send_response(answer)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return server.server_address[1], seen

    def test_groq_check_names_its_user_agent(self):
        from skullpt import keycheck
        port, seen = self._server(200)
        url = f"http://127.0.0.1:{port}/openai/v1/models"
        with mock.patch.dict(
                os.environ, {"DESIGNSHELL_GROQ_VALIDATE_URL": url}):
            ok, _msg = keycheck.validate_groq_key("gsk_whatever")
        self.assertTrue(ok)
        self.assertEqual(seen, [keycheck.USER_AGENT])
        self.assertFalse(seen[0].startswith("Python-urllib"))

    def test_a_403_is_a_refusal_not_an_invalid_key(self):
        from skullpt import keycheck
        port, _seen = self._server(403)
        url = f"http://127.0.0.1:{port}/openai/v1/models"
        with mock.patch.dict(
                os.environ, {"DESIGNSHELL_GROQ_VALIDATE_URL": url}):
            ok, msg = keycheck.validate_groq_key("gsk_whatever")
        self.assertFalse(ok)
        self.assertIn("refused the check", msg)
        self.assertNotIn("did not work", msg)
        self.assertNotIn("gsk_whatever", msg)

    def test_seat_calls_name_a_user_agent(self):
        from skullpt import live
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{}"

        def _fake_urlopen(req, timeout=None):
            captured["ua"] = req.headers.get("User-agent")
            return _Resp()

        with mock.patch("urllib.request.urlopen", _fake_urlopen):
            live._post("http://127.0.0.1:9/unused", {"a": 1},
                       timeout=5)
        self.assertEqual(captured.get("ua"), "SKULLPT-seat-call")


if __name__ == "__main__":
    unittest.main()
