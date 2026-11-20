"""SKULLPT v1.14 tests: the user-audit fix round (A1-A19).

Backend tests run everywhere; Qt tests skip without PySide6. The UI
fixes get pixel/geometry checks under Xvfb at 1366x768, not just
string asserts - the audit's P0s only showed with a job loaded at
that size, which is why the suite never caught them.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bake as bake_mod  # noqa: E402
from skullpt import bench as bench_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import imagegen  # noqa: E402
from skullpt import qtbench  # noqa: E402
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
        "summary": "two parts for the bake button",
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

    def do_action(self, action: str, payload: dict):
        return bench_mod.act(self.workspace, action, payload)

    def refresh(self) -> None:
        self.refreshed += 1

    def select_job(self, name: str | None) -> None:
        self.selected = name


# -------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.16 everywhere."""

    def test_version_is_114(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.17")
        self.assertIn("v1.17", (ROOT / "README.md").read_text())
        self.assertIn("## v1.17", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.17", (ROOT / "DESIGN.md").read_text())


# ------------------------------------------------------------------ A3


class A3PlainSummaryTests(unittest.TestCase):
    """A3: the job card shows the plain summary, not raw JSON."""

    def test_no_raw_parameters(self):
        text = desc_mod.plain_summary(CRAFT)
        # The raw dump printed key=value pairs ("position_cm=[0, 0,
        # 0]"); plain prose ("mirrored cylinder legs") is fine.
        for raw in ("position_cm=", "size_cm=", "mirror=", "profile=",
                    "repeat=", "accent:", "#c9a227", "[0, 0, 0]"):
            self.assertNotIn(raw, text)
        self.assertIn("Craft Stall", text)
        self.assertIn("9 parts", text)
        self.assertIn("floor", text)

    def test_long_summary_cuts_at_a_word(self):
        desc = json.loads(json.dumps(CRAFT))
        desc["summary"] = "word " * 100
        text = desc_mod.plain_summary(desc)
        self.assertTrue(text.splitlines()[1].endswith("..."))
        self.assertLess(len(text.splitlines()[1]), 250)

    def test_many_parts_collapse(self):
        desc = json.loads(json.dumps(CRAFT))
        text = desc_mod.plain_summary(desc)
        self.assertIn("and 3 more", text)

    def test_workspace_state_carries_card_summary(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            _job(ws)
            state = bench_mod.workspace_state(ws)
            card = state["jobs"][0]["card_summary"]
            self.assertNotIn("position_cm", card)
            self.assertIn("Craft Stall", card)


# ------------------------------------------------------------------ A4


class A4ImageOutcomeTests(unittest.TestCase):
    """A4: an image round's result, in one plain line."""

    def test_all_failed_names_the_reason(self):
        text = bench_mod._image_outcome_text([
            {"status": "failed",
             "error": "sd-cli: /lib/x86_64-linux-gnu/libc.so.6: "
                      "version `GLIBC_2.43' not found"},
            {"status": "failed", "error": "same"},
        ])
        self.assertIn("no pictures landed", text)
        self.assertIn("newer system library", text)
        self.assertNotIn("GLIBC_2.43", text)

    def test_partial_round(self):
        text = bench_mod._image_outcome_text([
            {"status": "pending"}, {"status": "failed", "error": "x"},
            {"status": "pending"}])
        self.assertEqual(text, "2 of 3 pictures landed (1 failed)")

    def test_all_landed(self):
        text = bench_mod._image_outcome_text(
            [{"status": "pending"}, {"status": "pending"}])
        self.assertEqual(text, "2 pictures landed - see them in the "
                               "Images window")

    def test_empty_round(self):
        self.assertEqual(bench_mod._image_outcome_text([]),
                         "no pictures were made")


class A4ChatRunTests(unittest.TestCase):
    """A4: the chat turn hands the run id back, so the Bench can
    report the round when it finishes instead of going silent."""

    def test_picture_request_returns_run(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            with mock.patch.object(
                    bench_mod, "act",
                    return_value={"ok": True, "run": "run-9",
                                  "detail": "generating"}) as act:
                result = bench_mod.chat_reply(
                    ws, "picture of a wooden stool")
            act.assert_called_once()
            self.assertEqual(result["run"], "run-9")
            self.assertIn("wooden stool", result["run_label"])

    def test_round_outcome_counts_only_this_round(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            bdir = ws / "images" / "batch-1"
            batch = {"slug": "batch-1", "candidates": [
                {"status": "kept", "file": "old.png"},
                {"status": "failed", "file": "new.png",
                 "error": "it took too long and was stopped"},
            ]}
            with mock.patch.object(
                    imagegen, "load_batch",
                    return_value={"candidates": [
                        {"status": "kept", "file": "old.png"}]}):
                with mock.patch.object(
                        imagegen, "generate", return_value=batch):
                    result = bench_mod._image_round_outcome(
                        ws, bdir, None, None)
            # The kept picture from the earlier round does not count.
            self.assertIn("no pictures landed", result["detail"])
            self.assertEqual(result["batch"], "batch-1")


# ------------------------------------------------------------------ A5


class A5PlainErrorTests(unittest.TestCase):
    """A5: a failed candidate's raw error, as plain words."""

    def test_glibc(self):
        self.assertEqual(
            imagegen.plain_error("version `GLIBC_2.43' not found"),
            "the image engine needs a newer system library than "
            "this machine has")

    def test_timeout(self):
        self.assertEqual(
            imagegen.plain_error("image 2 timed out after 600 seconds"),
            "it took too long and was stopped")

    def test_missing_binary(self):
        self.assertEqual(
            imagegen.plain_error("sd-cli: No such file or directory"),
            "the image engine could not be started")

    def test_out_of_memory(self):
        self.assertEqual(
            imagegen.plain_error("ggml: out of memory"),
            "the machine ran out of memory")

    def test_empty_error(self):
        self.assertEqual(imagegen.plain_error(None),
                         "the image engine gave no reason")

    def test_unknown_error_shows_last_line_trimmed(self):
        self.assertEqual(
            imagegen.plain_error("first line\nsecond line"),
            "second line")
        long = "x" * 500
        self.assertTrue(imagegen.plain_error(long).endswith("..."))
        self.assertEqual(len(imagegen.plain_error(long)), 200)


# ---------------------------------------------------------------- A8/A9


class A8A9BakeRefusalTests(unittest.TestCase):
    """A8/A9: bake refusals that tell the truth, in plain words."""

    def test_ui_job_has_no_3d_model_to_bake(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws, _mat_desc(kind="ui_component"))
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            self.assertIn("no 3D model to bake",
                          result["problems"][0])

    def test_no_recipe_uses_plain_words(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws)  # craft stall: string materials only
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            problem = result["problems"][0]
            self.assertIn("a material with a recipe", problem)
            self.assertNotIn("object material", problem)

    def test_skipped_part_refusal_has_no_jargon(self):
        desc = _mat_desc(kind="scene")
        desc["parts"][0]["material"] = {
            "family": "glass", "style": "clear", "layout": "solid",
            "params": {}, "wear": {}}
        desc["parts"][1]["material"] = "metal"
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            job = _job(ws, desc)
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            self.assertNotIn("object material",
                             result["problems"][0])
            self.assertIn("glass", result["problems"][0])


# ------------------------------------------------------------ A17/A18/A19


class A17A18A19HelpTests(unittest.TestCase):
    """A17/A18/A19: the Help index covers every window, promises
    only what the app can do, and lists the material tier."""

    def _help_html(self) -> str:
        # Read from source: importing qtwindows needs PySide6, and
        # these are text checks that must run everywhere.
        source = (ROOT / "skullpt" / "qtwindows.py").read_text()
        start = source.index('_HELP_HTML = """') + len('_HELP_HTML = """')
        end = source.index('"""', start)
        # Rendered HTML folds line breaks into spaces, so the test
        # reads it the same way ("Character\nStudio" is one name).
        return " ".join(source[start:end].split())

    def test_windows_lists_all_seven(self):
        windows = self._help_html().split(
            "<h3>The windows</h3>")[1].split("<h3>")[0]
        for name in ("Jobs", "Render", "Character Studio", "Images",
                     "Library", "Recipes", "Settings"):
            self.assertIn(name, windows)

    def test_no_description_file_promise(self):
        html = self._help_html()
        self.assertNotIn("start from a description file", html)
        self.assertIn("From example", html)

    def test_render_settings_lists_the_tier(self):
        settings = self._help_html().split(
            "<h3>Render settings</h3>")[1].split("<h3>")[0]
        self.assertIn("material quality tier", settings)
        for tier in ("Draft", "Balanced", "Final"):
            self.assertIn(tier, settings)


# ------------------------------------------------------------------ A6


class A6StylesheetTests(unittest.TestCase):
    """A6: a disabled primary button goes fully grey, not amber."""

    def test_disabled_primary_rule_is_grey(self):
        from skullpt import qtdesign

        css = qtdesign.build_stylesheet()
        self.assertNotIn("amber_disabled", css)
        self.assertIn(
            "QPushButton#primary:disabled { background: "
            + qtdesign.CANVAS_SOFT, css)


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


class A1RenderPanelTests(_QtCase):
    """A1: the loaded Render panel scrolls, and its text is whole."""

    def _render(self, job_name: str | None = None):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(200)
        if job_name is not None:
            window.set_job(job_name)
            self._pump(200)
        return window

    def test_loaded_panel_scrolls(self):
        job = _job(self.ws)
        window = self._render(job.dir.name)
        scroll = window._right_scroll
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertGreater(
            scroll.widget().sizeHint().height(),
            scroll.viewport().height())

    def test_loaded_panel_text_is_not_squeezed(self):
        job = _job(self.ws)
        window = self._render(job.dir.name)
        spin = window._pos_spins[0]
        # The old defect squeezed the rows below their font height,
        # so "0.0" read as two dots. A whole row is comfortably
        # taller than the font it carries.
        self.assertGreaterEqual(
            spin.height(), spin.fontMetrics().height() + 8)
        # No part picked yet: the field reads blank, not 0.0
        # (v1.16, D10) - the height is still the point here.
        self.assertEqual(spin.text(), "")

    def test_loaded_panel_fits_its_width(self):
        job = _job(self.ws)
        window = self._render(job.dir.name)
        scroll = window._right_scroll
        # The scroll area never squeezes content below its minimum
        # width: if the panel is wider than the viewport, the right
        # edge (Dial button, button text) slides off-screen, unseen
        # and unreachable, because horizontal scrolling is off.
        self.assertLessEqual(scroll.widget().width(),
                             scroll.viewport().width())


class A11DialButtonTests(_QtCase):
    """A11: the Dial button sits fully inside the window."""

    def test_dial_button_fully_visible_with_job_loaded(self):
        from PySide6 import QtCore
        from skullpt import qtwindows

        job = _job(self.ws)
        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(200)
        window.set_job(job.dir.name)
        self._pump(200)
        if window._parts_list.count():
            window._parts_list.setCurrentRow(0)
            window._on_list_part(window._parts_list.item(0))
            self._pump(200)
        dial = window._dial_btn
        self.assertTrue(dial.isEnabled())
        top_left = dial.mapToGlobal(QtCore.QPoint(0, 0))
        bottom_right = dial.mapToGlobal(
            QtCore.QPoint(dial.width(), dial.height()))
        win_top_left = window.mapToGlobal(QtCore.QPoint(0, 0))
        win_bottom_right = window.mapToGlobal(
            QtCore.QPoint(window.width(), window.height()))
        self.assertGreaterEqual(top_left.x(), win_top_left.x())
        self.assertGreaterEqual(top_left.y(), win_top_left.y())
        self.assertLessEqual(bottom_right.x(), win_bottom_right.x())
        self.assertLessEqual(bottom_right.y(), win_bottom_right.y())


class A2EnginesTileTests(_QtCase):
    """A2: the Engines tile shows its status and button in the
    normal view, with jobs present."""

    def _bench(self):
        window = self._track(qtbench.BenchWindow(self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(300)
        return window

    def test_tile_content_is_in_the_visible_grid(self):
        from PySide6 import QtCore, QtWidgets

        _job(self.ws)
        window = self._bench()
        text = window._engine_tile_text
        self.assertIn("Blender", text.text())
        tile = text.parent()
        buttons = [b for b in tile.findChildren(QtWidgets.QPushButton)
                   if b.text() == "Open Settings"]
        self.assertEqual(len(buttons), 1)
        button = buttons[0]
        # The button must sit inside the grid's visible viewport,
        # not below the fold: the old defect parked it at the bottom
        # of a 582 px tile, out of sight.
        viewport = window._scroll.viewport()
        vp_rect = QtCore.QRect(
            viewport.mapToGlobal(QtCore.QPoint(0, 0)),
            viewport.size())
        btn_rect = QtCore.QRect(
            button.mapToGlobal(QtCore.QPoint(0, 0)), button.size())
        self.assertTrue(vp_rect.contains(btn_rect),
                        f"button {btn_rect.getRect()} outside grid "
                        f"viewport {vp_rect.getRect()}")
        # ... and the status text starts near the tile's top, not
        # halfway down it.
        self.assertLess(text.y(), tile.height() // 2)


class A13ChatHintTests(_QtCase):
    """A13: the whole chat hint fits in the input."""

    def test_full_hint_fits(self):
        from PySide6 import QtGui

        from skullpt import qtdesign

        window = self._track(qtbench.BenchWindow(self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(300)
        inp = window._chat_input
        # Measure with the real app font (Inter 10), not the test
        # default: the first version of this test passed on the
        # default font while the shipped font still cut "status"
        # to "st...". The +20 covers the frame and text margins,
        # which the raw width comparison missed.
        qtdesign.load_fonts()
        inp.setFont(QtGui.QFont("Inter", 10))
        hint = inp.placeholderText()
        self.assertIn("status", hint)
        self.assertGreaterEqual(
            inp.width(),
            inp.fontMetrics().horizontalAdvance(hint) + 20)


class A14DetailStripTests(_QtCase):
    """A14: the selected-job strip shows the whole summary."""

    def test_strip_shows_whole_plain_summary(self):
        job = _job(self.ws)
        window = self._track(qtbench.BenchWindow(self.ws))
        window.resize(1366, 768)
        window.show()
        self._pump(300)
        window._on_card_clicked(job.dir.name)
        self._pump(200)
        state = bench_mod.workspace_state(self.ws)
        card = next(j for j in state["jobs"]
                    if j["name"] == job.dir.name)
        self.assertEqual(window._job_summary.text(),
                         card["card_summary"])
        # No height cap: the old 48 px cap cut the summary mid-word.
        self.assertGreater(window._job_summary.maximumHeight(), 200)


class A3JobCardTests(_QtCase):
    """A3: the card itself carries the plain summary only."""

    def test_card_summary_is_plain_and_short(self):
        from skullpt.qtwindows import JobCard

        job = _job(self.ws)
        state = bench_mod.workspace_state(self.ws)
        record = next(j for j in state["jobs"]
                      if j["name"] == job.dir.name)
        card = self._track(JobCard(record))
        card.resize(520, 400)
        card.show()
        self._pump(200)
        text = card._summary.text()
        for raw in ("position_cm=", "size_cm=", "mirror=", "[0, 0, 0]"):
            self.assertNotIn(raw, text)
        # A few wrapped lines, not the fifteen the raw dump took
        # (the craft-stall summary runs to ~175 px at this width).
        self.assertLessEqual(card._summary.height(), 200)


class A6DisabledButtonTests(_QtCase):
    """A6: a disabled primary button renders grey, pixel-checked."""

    def test_disabled_primary_renders_grey(self):
        from PySide6 import QtWidgets
        from skullpt.qtdesign import apply_theme

        apply_theme(self._app)
        button = self._track(QtWidgets.QPushButton("Approve"))
        button.setObjectName("primary")
        button.setEnabled(False)
        button.resize(160, 44)
        button.show()
        self._pump(200)
        # Sample near the left edge, not the centre: the centre holds
        # the "Approve" text, and its pixels are text colour, not the
        # background this test is about.
        image = button.grab().toImage()
        color = image.pixelColor(12, 22)
        # Canvas-soft grey (#27272d); the old amber mix was (125, 89,
        # 33) - clearly amber, and it read as enabled.
        self.assertLess(color.red(), 80)
        self.assertLess(abs(color.red() - color.blue()), 20)

    def test_enabled_primary_still_renders_amber(self):
        from PySide6 import QtWidgets
        from skullpt.qtdesign import apply_theme

        apply_theme(self._app)
        button = self._track(QtWidgets.QPushButton("Approve"))
        button.setObjectName("primary")
        button.resize(160, 44)
        button.show()
        self._pump(200)
        image = button.grab().toImage()
        color = image.pixelColor(12, 22)
        self.assertGreater(color.red(), 150)
        self.assertGreater(color.red() - color.blue(), 100)


class A5FailedCandidateTests(_QtCase):
    """A5: a failed picture shows why, and Keep/Reject go grey."""

    def test_failed_card_shows_reason_and_disables_actions(self):
        from PySide6 import QtWidgets
        from skullpt import qtwindows

        _batch, batch_dir = imagegen.new_batch(self.ws, "a wooden stool")
        record = json.loads((batch_dir / "batch.json").read_text())
        record["candidates"] = [{
            "file": "cand-01.png", "status": "failed",
            "error": "sd-cli: version `GLIBC_2.43' not found"}]
        (batch_dir / "batch.json").write_text(json.dumps(record))
        window = self._track(
            qtwindows.ImagesWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window._on_batch(window._batch_list.item(0))
        self._pump(200)
        cards = window.findChildren(qtwindows._CandidateCard)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertFalse(card._keep_btn.isEnabled())
        self.assertFalse(card._reject_btn.isEnabled())
        reasons = [label.text()
                   for label in card.findChildren(QtWidgets.QLabel)
                   if label.text().startswith("Why:")]
        self.assertEqual(len(reasons), 1)
        self.assertIn("newer system library", reasons[0])


class A7ImagesEmptyTests(_QtCase):
    """A7: the empty Images window offers a way forward."""

    def test_empty_window_offers_generate(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.ImagesWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        self.assertTrue(window._gen_btn.isVisible())
        self.assertEqual(window._gen_btn.text(), "Generate images")
        self.assertIn("Generate images", window._prompt_label.text())
        self.assertFalse(window._gen_btn.isEnabled())
        window._gen_prompt.setText("a wooden stool")
        self.assertTrue(window._gen_btn.isEnabled())


class A8BakeButtonTests(_QtCase):
    """A8: Bake is offered only when it can run."""

    def _render_with(self, desc: dict, blend: bool = False):
        from skullpt import qtwindows

        job = _job(self.ws, desc)
        if blend:
            (job.output_dir).mkdir(parents=True, exist_ok=True)
            (job.output_dir / f"{job.dir.name}.blend").write_bytes(b"")
        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window.set_job(job.dir.name)
        self._pump(200)
        return window

    def test_bake_disabled_on_ui_job(self):
        window = self._render_with(_mat_desc(kind="ui_component"))
        self.assertFalse(window._bake_btn.isEnabled())
        self.assertIn("no 3D model to bake",
                      window._bake_btn.toolTip())

    def test_bake_disabled_until_built(self):
        window = self._render_with(_mat_desc(kind="scene"))
        self.assertFalse(window._bake_btn.isEnabled())
        self.assertIn("Build the job first",
                      window._bake_btn.toolTip())

    def test_bake_enabled_on_built_3d_job(self):
        window = self._render_with(_mat_desc(kind="scene"), blend=True)
        self.assertTrue(window._bake_btn.isEnabled())


class A10EditorEmptyTests(_QtCase):
    """A10: no part selected, nothing to edit."""

    def test_editor_blank_and_grey_with_no_job(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        for spin in (*window._pos_spins, *window._size_spins,
                     window._bevel_spin, window._radius_spin):
            self.assertFalse(spin.isEnabled())
        self.assertFalse(window._material_combo.isEnabled())
        self.assertEqual(window._material_combo.currentIndex(), -1)
        self.assertFalse(window._save_part_btn.isEnabled())
        self.assertFalse(window._dial_btn.isEnabled())
        self.assertEqual(window._editor_note.text(),
                         "Select a part to edit it.")

    def test_editor_blank_and_grey_with_job_but_no_part(self):
        from skullpt import qtwindows

        job = _job(self.ws)
        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window.set_job(job.dir.name)
        self._pump(200)
        self.assertFalse(window._save_part_btn.isEnabled())
        self.assertEqual(window._material_combo.currentIndex(), -1)


class A12WorkspaceDefaultsTests(_QtCase):
    """A12: with no job loaded, the render controls show the
    workspace defaults, not the first item in each list."""

    def test_no_job_shows_workspace_defaults(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        self.assertEqual(window._engine_combo.currentData(), "auto")
        self.assertEqual(window._res_spin.value(), 640)
        self.assertEqual(window._samples_spin.value(), 24)
        self.assertEqual(window._tier_combo.currentData(), "balanced")
        self.assertFalse(window._save_render_btn.isEnabled())


class A15DialModeTests(_QtCase):
    """A15: the dial's mode button names the mode it is in."""

    def _dial(self):
        from skullpt.qtwindows import MaterialDial

        dial = self._track(MaterialDial(
            self.ws, "plank", json.loads(json.dumps(WOOD_SPEC))))
        dial.show()
        self._pump(200)
        return dial

    def test_button_names_simple_mode(self):
        dial = self._dial()
        self.assertFalse(dial._advanced_btn.isChecked())
        self.assertEqual(dial._advanced_btn.text(), "Simple")

    def test_button_names_advanced_mode_after_toggle(self):
        from PySide6 import QtCore, QtTest

        dial = self._dial()
        QtTest.QTest.mouseClick(dial._advanced_btn,
                                QtCore.Qt.LeftButton)
        self._pump(200)
        self.assertTrue(dial._advanced_btn.isChecked())
        self.assertEqual(dial._advanced_btn.text(), "Advanced")


class A16SaveCharacterTests(_QtCase):
    """A16: Save character is greyed out with no character job."""

    def test_save_disabled_with_no_character_job(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.CharacterWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        self.assertFalse(window._save_btn.isEnabled())
        self.assertIsNone(window._job_name)


class A4PollerTests(_QtCase):
    """A4: a chat turn's run is tracked, so the poller reports it."""

    def test_chat_run_is_registered_for_reporting(self):
        window = self._track(qtbench.BenchWindow(self.ws))
        window.show()
        self._pump(200)
        window._on_run_done(
            {"kind": "chat",
             "result": {"run": "run-9",
                        "run_label": "images for \"a stool\"",
                        "actions": []},
             "detail": ""},
            "chat picture of a stool")
        self.assertEqual(window._pending_runs.get("run-9"),
                         "images for \"a stool\"")


if __name__ == "__main__":
    unittest.main()
