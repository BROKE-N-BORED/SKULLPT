"""SKULLPT v1.11 tests: the material system, phase 1 (M1-M6).

Backend tests run everywhere; Qt tests skip without PySide6; the
real-Blender tests skip without Blender (or with DESIGNSHELL_SKIP_E2E).
"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import blender_export  # noqa: E402
from skullpt import builder as builder_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import live  # noqa: E402
from skullpt import materials as materials_mod  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())

WOOD_SPEC = {"family": "wood", "style": "walnut", "layout": "boards",
             "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _mat_desc() -> dict:
    """One object-material part, one string-material part, and one
    part with no material."""
    return {
        "name": "material test", "kind": "scene",
        "summary": "three parts for the dial",
        "parts": [
            {"id": "plank", "type": "prop", "shape": "box",
             "position_cm": [0, 0, 0], "size_cm": [120, 12, 2],
             "material": json.loads(json.dumps(WOOD_SPEC))},
            {"id": "block", "type": "prop", "shape": "box",
             "position_cm": [50, 10, 0], "size_cm": [20, 20, 20],
             "material": "metal"},
            {"id": "bare", "type": "prop", "shape": "box",
             "position_cm": [40, 20, 0], "size_cm": [10, 10, 10]},
        ],
        "constraints": [], "rubric": [],
        "budget": {"max_tris": 40000, "preview_px": 640},
    }


# ---------------------------------------------------------------- M1


class MaterialSpecValidationTests(unittest.TestCase):
    """M1: the object form validates against closed vocabularies;
    the string form is untouched."""

    def test_object_form_validates(self):
        self.assertEqual(desc_mod.validate(_mat_desc()), [])

    def test_string_form_untouched(self):
        desc = _mat_desc()
        desc["parts"][0]["material"] = "wood"
        self.assertEqual(desc_mod.validate(desc), [])
        desc["parts"][0]["material"] = "#a1b2c3"
        self.assertEqual(desc_mod.validate(desc), [])

    def test_partial_spec_validates_with_defaults(self):
        desc = _mat_desc()
        desc["parts"][0]["material"] = {"family": "wood"}
        self.assertEqual(desc_mod.validate(desc), [])

    def test_bad_family_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["family"] = "stone"
        problems = desc_mod.validate(desc)
        self.assertTrue(any("family" in p for p in problems), problems)

    def test_bad_style_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["style"] = "teak"
        problems = desc_mod.validate(desc)
        self.assertTrue(any("style" in p for p in problems), problems)

    def test_bad_layout_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["layout"] = "herringbone"
        problems = desc_mod.validate(desc)
        self.assertTrue(any("layout" in p for p in problems), problems)

    def test_out_of_range_param_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["params"]["knots"] = 1.5
        problems = desc_mod.validate(desc)
        self.assertTrue(any("knots" in p for p in problems), problems)

    def test_unknown_param_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["params"]["sparkle"] = 0.5
        problems = desc_mod.validate(desc)
        self.assertTrue(any("sparkle" in p for p in problems), problems)

    def test_bad_wear_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["wear"]["dirt"] = 2.0
        problems = desc_mod.validate(desc)
        self.assertTrue(any("dirt" in p for p in problems), problems)

    def test_non_dict_non_str_material_rejected(self):
        desc = _mat_desc()
        desc["parts"][0]["material"] = 42
        self.assertTrue(desc_mod.validate(desc))

    def test_summarize_shows_the_breadcrumb(self):
        summary = desc_mod.summarize(_mat_desc())
        self.assertIn("Wood · Walnut · Boards", summary)
        self.assertNotIn("{'family'", summary)


class BreadcrumbTests(unittest.TestCase):
    """M1/M5: the breadcrumb from family, style, layout."""

    def test_breadcrumb_format(self):
        self.assertEqual(materials_mod.breadcrumb(WOOD_SPEC),
                         "Wood · Walnut · Boards")

    def test_breadcrumb_defaults(self):
        self.assertEqual(materials_mod.breadcrumb({"family": "wood"}),
                         "Wood · Oak · Solid")

    def test_breadcrumb_unknown_family(self):
        self.assertEqual(materials_mod.breadcrumb({"family": "nope"}),
                         "nope")


class SurpriseTests(unittest.TestCase):
    """M5: Surprise records its seed and is reproducible."""

    def test_surprise_records_a_seed(self):
        spec = materials_mod.surprise_spec(random.Random(7))
        self.assertIn("seed", spec["params"])
        self.assertEqual(materials_mod.validate_material_spec(spec), [])

    def test_surprise_is_reproducible(self):
        a = materials_mod.surprise_spec(random.Random(42))
        b = materials_mod.surprise_spec(random.Random(42))
        self.assertEqual(a, b)


class PresetTests(unittest.TestCase):
    """M5: presets in materials.json."""

    def test_save_load_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            self.assertEqual(materials_mod.load_presets(ws), {})
            materials_mod.save_preset(ws, "dark floor", WOOD_SPEC)
            presets = materials_mod.load_presets(ws)
            self.assertIn("dark floor", presets)
            self.assertEqual(presets["dark floor"]["style"], "walnut")
            self.assertTrue(materials_mod.delete_preset(ws, "dark floor"))
            self.assertEqual(materials_mod.load_presets(ws), {})
            self.assertFalse(materials_mod.delete_preset(ws, "dark floor"))

    def test_damaged_presets_file_reads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            (ws / "materials.json").write_text("{not json")
            self.assertEqual(materials_mod.load_presets(ws), {})

    def test_save_rejects_an_empty_name_and_a_bad_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            with self.assertRaises(ValueError):
                materials_mod.save_preset(ws, "  ", WOOD_SPEC)
            with self.assertRaises(ValueError):
                materials_mod.save_preset(ws, "x", {"family": "nope"})

    def test_presets_back_up_with_the_workspace(self):
        from skullpt import backup

        self.assertIn("materials.json", backup.BACKED_UP_FILES)


# ---------------------------------------------------------------- M2


class NodeCountTests(unittest.TestCase):
    """M2: every wood spec stays under the 25-node budget."""

    def test_every_wood_spec_is_under_25(self):
        for style in materials_mod.FAMILIES["wood"]["styles"]:
            for layout in ("solid", "boards"):
                for knots in (0.0, 0.5):
                    spec = {"family": "wood", "style": style,
                            "layout": layout, "params": {"knots": knots}}
                    count = materials_mod.node_count(spec)
                    self.assertLess(count, 25, spec)
                    self.assertGreaterEqual(count, 17)

    def test_known_counts(self):
        self.assertEqual(materials_mod.node_count(
            {"family": "wood", "style": "oak", "params": {"knots": 0}}),
            17)
        self.assertEqual(materials_mod.node_count(
            {"family": "wood", "style": "oak", "layout": "boards",
             "params": {"knots": 0.5}}), 23)


class MaterialScriptTests(unittest.TestCase):
    """M2/M4: the generated script carries the recipe."""

    def _script(self, desc):
        with tempfile.TemporaryDirectory() as tmp:
            return blender_export.generate_script(desc, Path(tmp), "t")

    def test_wood_recipe_nodes_in_the_script(self):
        script = self._script(_mat_desc())
        for node in ("ShaderNodeTexWave", "ShaderNodeTexNoise",
                     "ShaderNodeMapRange", "ShaderNodeMix",
                     "ShaderNodeBump", "ShaderNodeTexBrick"):
            self.assertIn(node, script)

    def test_solid_layout_disables_the_boards_branch(self):
        # The branch code is in the template (runtime-conditional);
        # the payload is what turns it off.
        desc = _mat_desc()
        desc["parts"][0]["material"]["layout"] = "solid"
        payload = blender_export.build_payload(desc, Path("/tmp"), "t")
        spec = payload["parts"][0]["material_spec"]
        self.assertFalse(spec["boards"])
        self.assertIsNone(spec["board_prescale"])
        self.assertEqual(spec["nodes"], 20)  # base + knots, no boards

    def test_no_knots_means_no_knot_nodes(self):
        desc = _mat_desc()
        desc["parts"][0]["material"]["params"]["knots"] = 0
        desc["parts"][0]["material"]["layout"] = "solid"
        payload = blender_export.build_payload(desc, Path("/tmp"), "t")
        spec = payload["parts"][0]["material_spec"]
        self.assertEqual(spec["knots"], 0)
        self.assertEqual(spec["nodes"], 17)

    def test_ring_spacing_changes_the_wave_scale(self):
        wide = _mat_desc()
        wide["parts"][0]["material"]["params"]["ring_spacing_mm"] = 20.0
        narrow = _mat_desc()
        narrow["parts"][0]["material"]["params"]["ring_spacing_mm"] = 2.0
        wide_scale = blender_export.build_payload(
            wide, Path("/tmp"), "t")["parts"][0]["material_spec"]["ring_scale"]
        narrow_scale = blender_export.build_payload(
            narrow, Path("/tmp"), "t")["parts"][0]["material_spec"]["ring_scale"]
        self.assertLess(wide_scale, narrow_scale)

    def test_object_part_carries_a_spec_and_no_flat_texture(self):
        payload = blender_export.build_payload(_mat_desc(), Path("/tmp"),
                                               "t")
        plank = payload["parts"][0]
        self.assertIsNotNone(plank["material_spec"])
        self.assertIsNone(plank["texture"])
        self.assertEqual(plank["material_spec"]["breadcrumb"],
                         "Wood · Walnut · Boards")
        self.assertEqual(plank["material_spec"]["nodes"], 23)

    def test_string_materials_keep_the_old_texture_chain(self):
        script = self._script(_mat_desc())
        self.assertIn('texture == "wood_grain"', script)
        payload = blender_export.build_payload(_mat_desc(), Path("/tmp"),
                                               "t")
        self.assertIsNone(payload["parts"][1]["material_spec"])
        self.assertEqual(payload["parts"][1]["texture"], "brushed_metal")


class ResolveMaterialTests(unittest.TestCase):
    """M1/M2: resolving an object material."""

    def test_object_form_resolves_to_the_style(self):
        part = {"id": "p", "material": WOOD_SPEC}
        color, metallic, roughness = blender_export.resolve_material(part)
        self.assertEqual(color, "#54371f")  # walnut base
        self.assertEqual(metallic, 0.0)
        self.assertAlmostEqual(roughness, 0.5 * (1 - 0.4 * 0.5), places=6)

    def test_object_form_has_no_flat_texture(self):
        part = {"id": "p", "material": WOOD_SPEC, "texture": "wood_grain"}
        self.assertIsNone(blender_export.resolve_texture(part))

    def test_string_form_unchanged(self):
        self.assertEqual(
            blender_export.resolve_material({"id": "p", "material": "wood"}),
            blender_export.MATERIALS["wood"])
        self.assertEqual(
            blender_export.resolve_texture({"id": "p", "material": "wood"}),
            "wood_grain")


class PlannerPromptTests(unittest.TestCase):
    """M1: the planner knows the object vocabulary, and patches
    can carry an object material."""

    def test_prompt_names_the_object_vocabulary(self):
        self.assertIn("white_oak", live.FORMAT_SPEC)
        self.assertIn("boards", live.FORMAT_SPEC)

    def test_apply_patches_accepts_object_material(self):
        desc = _mat_desc()
        applied = live.apply_patches(desc, [{
            "part": "block", "field": "material",
            "value": {"family": "wood", "style": "cherry"},
            "issue": "test"}])
        self.assertEqual(len(applied), 1)
        self.assertEqual(desc["parts"][1]["material"]["style"], "cherry")

    def test_apply_patches_rejects_a_bad_object_material(self):
        desc = _mat_desc()
        applied = live.apply_patches(desc, [{
            "part": "block", "field": "material",
            "value": {"family": "nope"}, "issue": "test"}])
        self.assertEqual(applied, [])
        self.assertEqual(desc["parts"][1]["material"], "metal")


# ---------------------------------------------------------------- M6


class CopyMaterialTests(unittest.TestCase):
    """M6: the eyedropper copies a snapshot, not a link."""

    def test_copy_material_copies_the_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            result = bench_mod.act(ws, "copy-material", {
                "job": job.dir.name, "from_part": "plank",
                "to_part": "block"})
            self.assertTrue(result["ok"])
            desc = job.load_description()
            block = next(p for p in desc["parts"] if p["id"] == "block")
            self.assertEqual(block["material"]["style"], "walnut")
            self.assertEqual(block["material"]["layout"], "boards")

    def test_copy_is_a_snapshot_not_a_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            bench_mod.act(ws, "copy-material", {
                "job": job.dir.name, "from_part": "plank",
                "to_part": "block"})
            bench_mod.act(ws, "edit-part", {
                "job": job.dir.name, "part": "plank",
                "fields": {"material": {"family": "wood",
                                        "style": "maple"}}})
            desc = job.load_description()
            block = next(p for p in desc["parts"] if p["id"] == "block")
            self.assertEqual(block["material"]["style"], "walnut")

    def test_copy_material_errors_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            with self.assertRaises(ValueError):
                bench_mod.act(ws, "copy-material", {
                    "job": job.dir.name, "from_part": "missing",
                    "to_part": "block"})
            with self.assertRaises(ValueError):
                bench_mod.act(ws, "copy-material", {
                    "job": job.dir.name, "from_part": "plank",
                    "to_part": "missing"})
            with self.assertRaises(ValueError):
                bench_mod.act(ws, "copy-material", {
                    "job": job.dir.name, "from_part": "bare",
                    "to_part": "block"})


# ---------------------------------------------------------------- M2 report


class BuildReportMaterialsTests(unittest.TestCase):
    """M2: node counts in the build report."""

    def test_material_report_lists_object_parts(self):
        report = builder_mod.material_report(_mat_desc())
        self.assertEqual(set(report), {"plank"})
        self.assertEqual(report["plank"]["breadcrumb"],
                         "Wood · Walnut · Boards")
        self.assertEqual(report["plank"]["nodes"], 23)

    def test_material_report_empty_without_object_materials(self):
        self.assertEqual(builder_mod.material_report(SHOP), {})

    def test_build_report_merges_measured_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            (job.dir / "build-report.json").write_text(json.dumps(
                {"ok": True, "materials": {"plank": {
                    "breadcrumb": "Wood · Walnut · Boards",
                    "nodes": 23}}}))
            job.output_dir.mkdir(parents=True, exist_ok=True)
            (job.output_dir / "t.stats.json").write_text(json.dumps(
                {"total_tris": 12, "parts": {},
                 "materials": {"plank": {
                     "breadcrumb": "Wood · Walnut · Boards",
                     "nodes": 23}}}))
            report = bench_mod.build_report(ws, job.dir.name)
            self.assertEqual(
                report["measured_materials"]["plank"]["nodes"], 23)


# ---------------------------------------------------------------- Qt


try:
    from PySide6 import QtCore, QtTest, QtWidgets

    _QT_TESTS = True
except Exception:
    _QT_TESTS = False


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication([]))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)
        self._windows: list[QtWidgets.QWidget] = []

    def tearDown(self):
        for window in self._windows:
            window.close()
        self.tmp.cleanup()

    def _bench(self):
        window = qtbench.BenchWindow(self.ws)
        window.show()
        self._windows.append(window)
        self._app.processEvents()
        return window

    def _pump(self, ms: int = 100):
        QtTest.QTest.qWait(ms)

    def _render(self, job=None):
        window = self._bench()
        if job is not None:
            window.select_job(job.dir.name)
        render = window._render_window
        render.show()
        self._windows.append(render)
        self._pump()
        return window, render


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtDialTests(_QtCase):
    """M3/M5: the Material Dial widget."""

    def _dial(self, spec=None):
        from skullpt.qtwindows import MaterialDial

        dial = MaterialDial(self.ws, "plank",
                            spec or json.loads(json.dumps(WOOD_SPEC)))
        dial.show()
        self._windows.append(dial)
        self._pump()
        return dial

    def test_dial_fits_the_size_cap(self):
        dial = self._dial()
        self.assertLessEqual(dial.width(), 560)
        self.assertLessEqual(dial.face.width(), 560)

    def test_pages_switch_on_click(self):
        dial = self._dial()
        face = dial.face
        # The Layout page wedge (index 2): 40-degree wedges from
        # the top, mid-radius 110.
        mid = math.radians(90.0 - (2 * 40 + 20))
        x = 280 + int(110 * math.cos(mid))
        y = 260 - int(110 * math.sin(mid))
        QtTest.QTest.mouseClick(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(x, y))
        self.assertEqual(face.page, 2)

    def test_number_keys_switch_pages(self):
        dial = self._dial()
        face = dial.face
        face.setFocus()
        QtTest.QTest.keyClick(face, QtCore.Qt.Key_4)
        self.assertEqual(face.page, 3)  # Detail

    def test_picking_a_style_changes_the_spec(self):
        dial = self._dial()
        face = dial.face
        face.set_page(1)  # Style
        seen = []
        face.specChanged.connect(seen.append)
        # Style page: 6 items, 60 degrees each, in registry order
        # (oak, white_oak, walnut, maple, cherry, weathered); maple
        # is index 3, mid-radius 202.
        mid = math.radians(90.0 - (3 * 60 + 30))
        x = 280 + int(202 * math.cos(mid))
        y = 260 - int(202 * math.sin(mid))
        QtTest.QTest.mouseClick(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(x, y))
        self.assertEqual(face.spec()["style"], "maple")
        self.assertTrue(seen)
        self.assertTrue(dial.changed)

    def test_knob_drag_changes_the_value(self):
        dial = self._dial()
        face = dial.face
        face.set_advanced(True)
        face.set_page(5)  # Surface
        # One knob (gloss) fills the ring: click it at the top.
        QtTest.QTest.mouseClick(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(280, 260 - 202))
        self.assertEqual(face._active_knob, "gloss")
        seen = []
        face.specChanged.connect(seen.append)
        # Drag the centre knob up 100 px: +0.5 on a 0-1 knob.
        QtTest.QTest.mousePress(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(280, 260))
        QtTest.QTest.mouseMove(face, QtCore.QPoint(280, 160))
        QtTest.QTest.mouseRelease(face, QtCore.Qt.LeftButton,
                                  QtCore.Qt.NoModifier,
                                  QtCore.QPoint(280, 160))
        self.assertAlmostEqual(face.spec()["params"]["gloss"], 0.9)
        self.assertTrue(seen)

    def test_simple_mode_hides_advanced_knobs(self):
        dial = self._dial()
        face = dial.face
        face.set_advanced(False)
        face.set_page(3)  # Detail
        keys = [item["key"] for item in face._items()]
        self.assertEqual(keys, ["seed", "figure"])
        face.set_advanced(True)
        keys = [item["key"] for item in face._items()]
        self.assertEqual(keys, ["seed", "figure", "knots", "pores",
                                "grain_lines"])

    def test_search_finds_a_style(self):
        dial = self._dial()
        dial._search.setText("cherry")
        self._pump()
        self.assertEqual(dial._results.count(), 1)
        dial._on_search_pick(dial._results.item(0))
        self.assertEqual(dial.spec()["style"], "cherry")

    def test_surprise_changes_the_spec_and_records_a_seed(self):
        dial = self._dial()
        dial._surprise()
        after = dial.spec()
        self.assertIn("seed", after["params"])
        self.assertTrue(dial.changed)

    def test_save_writes_a_preset(self):
        dial = self._dial()
        dial._show_preset_row()
        dial._preset_name.setText("test preset")
        dial._save_preset()
        presets = materials_mod.load_presets(self.ws)
        self.assertIn("test preset", presets)
        self.assertEqual(presets["test preset"]["style"], "walnut")

    def test_close_button_label_is_visible(self):
        # The dial's own stylesheet used to be a bare "background:"
        # rule, which overrode the amber #primary button background
        # while its near-black text still applied: an invisible
        # label. The Close button must render amber now.
        from skullpt import qtdesign
        self._app.setStyleSheet(qtdesign.build_stylesheet())
        try:
            dial = self._dial()
            close_btn = None
            for btn in dial.findChildren(QtWidgets.QPushButton):
                if btn.text() == "Close":
                    close_btn = btn
                    break
            self.assertIsNotNone(close_btn, "no Close button found")
            self._pump(100)
            pix = close_btn.grab()
            # Sample the left padding, not the centre: the centred
            # "Close" text sits in the middle of the button.
            sample = pix.toImage().pixelColor(
                pix.width() // 10, pix.height() // 2)
            # Amber #f5a623 is bright and warm; a dark background
            # would read under 60 on every channel.
            self.assertGreater(sample.red(), 180)
            self.assertGreater(sample.green(), 120)
            self.assertLess(sample.blue(), 100)
        finally:
            self._app.setStyleSheet("")

    def test_center_hint_is_not_clipped(self):
        # The dial's idle hint ("Pick a knob or a style") is wider
        # than the 110 px centre rect at 11 pt bold, so it used to
        # clip at both edges on every knob page. The title now wraps
        # like the breadcrumb below it: no bright text pixel may sit
        # within 2 px of the rect's left or right edge.
        from skullpt.qtwindows import _DIAL_CX, _DIAL_CY
        dial = self._dial()
        dial.face.set_page(3)  # Detail: no knob active, idle hint
        self._pump(100)
        img = dial.face.grab().toImage()
        for y in range(_DIAL_CY - 30, _DIAL_CY + 6):
            for x in (_DIAL_CX - 55, _DIAL_CX - 54,
                      _DIAL_CX + 54, _DIAL_CX + 55):
                c = img.pixelColor(x, y)
                self.assertLess(
                    c.red(), 120,
                    f"bright pixel at ({x}, {y}): centre hint clipped")

    def test_minimise_collapses_to_a_bar(self):
        dial = self._dial()
        full_height = dial.height()
        QtTest.QTest.mouseClick(dial._minimise_btn,
                                QtCore.Qt.LeftButton)
        self.assertFalse(dial._body.isVisible())
        self.assertLess(dial.height(), full_height)
        QtTest.QTest.mouseClick(dial._minimise_btn,
                                QtCore.Qt.LeftButton)
        self.assertTrue(dial._body.isVisible())


# A 1x1 PNG, so the Render-window preview tests exercise the label
# path without Blender.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtRenderDialTests(_QtCase):
    """M3/M5/M6: the dial in the Render window."""

    def setUp(self):
        super().setUp()
        # Previews render off the UI thread; in these tests the
        # render is a stub that returns a real PNG at once, so no
        # Blender run is ever started.
        self._preview_png = Path(self.tmp.name) / "preview.png"
        self._preview_png.write_bytes(_TINY_PNG)
        self._preview_patch = mock.patch.object(
            blender_export, "render_material_preview",
            return_value=self._preview_png)
        self._preview_patch.start()
        self.addCleanup(self._preview_patch.stop)

    def test_breadcrumb_label_for_an_object_material(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("plank")
        self.assertTrue(render._material_crumb.isVisible())
        self.assertEqual(render._material_crumb.text(),
                         "Wood · Walnut · Boards")
        self.assertEqual(render._material_combo.currentIndex(), -1)
        self.assertTrue(render._dial_btn.isEnabled())

    def test_empty_state_for_a_part_with_no_material(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("bare")
        self.assertIn("No material here", render._material_crumb.text())
        self.assertTrue(render._dial_btn.isEnabled())

    def test_string_material_leaves_the_crumb_hidden(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("block")
        self.assertFalse(render._material_crumb.isVisible())
        self.assertEqual(render._material_combo.currentText(), "metal")

    def test_preview_label_shows_the_render(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("plank")
        self._pump(500)
        self.assertTrue(render._preview_label.isVisible())
        self.assertFalse(render._preview_label.pixmap().isNull())

    def test_double_click_opens_the_dial(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render.viewport.partDoubleClicked.emit("plank")
        self._pump()
        self.assertIsNotNone(render._dial)
        render._dial.close()
        self._pump()
        # Closed without changing anything: the description is untouched.
        desc = job.load_description()
        plank = next(p for p in desc["parts"] if p["id"] == "plank")
        self.assertEqual(plank["material"]["style"], "walnut")

    def test_dial_close_applies_a_changed_spec(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("block")
        render._open_dial()
        dial = render._dial
        self.assertIsNotNone(dial)
        dial.face.set_page(1)  # Style
        dial.face._pick_item({"key": "cherry", "kind": "style"})
        self.assertTrue(dial.changed)
        dial.close()
        self._pump()
        desc = job.load_description()
        block = next(p for p in desc["parts"] if p["id"] == "block")
        self.assertIsInstance(block["material"], dict)
        self.assertEqual(block["material"]["style"], "cherry")

    def test_eyedropper_copies_onto_the_selected_part(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._select_part("block")
        render._on_part_eyedropper("plank")
        desc = job.load_description()
        block = next(p for p in desc["parts"] if p["id"] == "block")
        self.assertIsInstance(block["material"], dict)
        self.assertEqual(block["material"]["style"], "walnut")

    def test_eyedropper_with_nothing_selected_selects(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        render._on_part_eyedropper("plank")
        self.assertEqual(render._selected_part, "plank")

    def test_ctrl_click_emits_the_eyedropper_signal(self):
        job = _job(self.ws, _mat_desc())
        _window, render = self._render(job)
        viewport = render.viewport
        ctx = viewport.context()
        if ctx is None or not ctx.isValid():
            self.skipTest("no GL context on this platform (needs Xvfb + xcb)")
        self._pump(200)
        center = QtCore.QPoint(viewport.width() // 2,
                               viewport.height() // 2)
        hit = viewport.pick(center.x(), center.y())
        self.assertIsNotNone(hit, "the center pixel hit no part")
        seen = []
        viewport.partEyedropper.connect(seen.append)
        QtTest.QTest.mouseClick(viewport, QtCore.Qt.LeftButton,
                                QtCore.Qt.ControlModifier, center)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], hit)


# ---------------------------------------------------------------- e2e


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class MaterialEndToEndTests(unittest.TestCase):
    """A real wood build through real Blender + the preview render."""

    def test_wood_build_records_measured_node_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            script = blender_export.generate_script(
                _mat_desc(), out_dir, "mattest")
            script_path = Path(tmp) / "build.py"
            script_path.write_text(script)
            run = subprocess.run(
                [blender_export.find_blender(), "--background",
                 "--factory-startup", "--python", str(script_path)],
                capture_output=True, text=True, timeout=300)
            self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
            stats = json.loads(
                (out_dir / "mattest.stats.json").read_text())
            mats = stats["materials"]
            self.assertEqual(mats["plank"]["nodes"], 23)
            self.assertEqual(mats["plank"]["breadcrumb"],
                             "Wood · Walnut · Boards")
            # Measured == predicted for every object part.
            report = builder_mod.material_report(_mat_desc())
            for part_id, entry in report.items():
                self.assertEqual(mats[part_id]["nodes"], entry["nodes"])

    def test_preview_render_lands_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            png = blender_export.render_material_preview(WOOD_SPEC, ws)
            self.assertTrue(png.exists())
            self.assertGreater(png.stat().st_size, 1000)
            # A second call hits the cache, not Blender.
            with mock.patch.object(
                    blender_export, "find_blender",
                    return_value=None):
                again = blender_export.render_material_preview(
                    WOOD_SPEC, ws)
            self.assertEqual(png, again)
