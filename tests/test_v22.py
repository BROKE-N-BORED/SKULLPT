"""SKULLPT v1.12 tests: the material system, phase 2 (M7-M11).

Backend tests run everywhere; Qt tests skip without PySide6; the
real-Blender tests skip without Blender (or with DESIGNSHELL_SKIP_E2E).
"""
from __future__ import annotations

import itertools
import json
import math
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import __version__  # noqa: E402
from skullpt import blender_export  # noqa: E402
from skullpt import builder as builder_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import live  # noqa: E402
from skullpt import materials as materials_mod  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())

BRICK_SPEC = {"family": "brick", "style": "red", "layout": "stretcher",
              "params": {"seed": 7}, "wear": {"dirt": 0.4}}

# One part per family, the key layouts, and the wear layer - the
# same set the smoke render judged by eye.
SMOKE_SPECS = {
    "wood_solid": {"family": "wood", "style": "walnut",
                   "params": {"knots": 0.5}},
    "wood_hb": {"family": "wood", "style": "oak",
                "layout": "herringbone", "params": {"knots": 0.5},
                "wear": {"dirt": 0.5}},
    "wood_pq": {"family": "wood", "layout": "parquet"},
    "wood_ch": {"family": "wood", "layout": "chevron"},
    "brick_en": {"family": "brick", "layout": "english"},
    "brick_fl": {"family": "brick", "layout": "flemish"},
    "brick_bw": {"family": "brick", "layout": "basket_weave"},
    "brick_st": {"family": "brick", "layout": "stretcher",
                 "wear": {"dirt": 0.4}},
    "stone": {"family": "stone", "layout": "stack"},
    "marble": {"family": "marble"},
    "concrete": {"family": "concrete", "layout": "stretcher"},
    "metal": {"family": "metal", "style": "copper",
              "wear": {"rust": 0.6}},
    "fabric": {"family": "fabric"},
    "leather": {"family": "leather"},
    "ground": {"family": "ground", "wear": {"moss": 0.5}},
    "paint": {"family": "paint", "style": "aged"},
    "glass": {"family": "glass", "style": "frosted"},
}


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _smoke_desc() -> dict:
    parts = []
    for i, (pid, spec) in enumerate(SMOKE_SPECS.items()):
        parts.append({"id": pid, "type": "prop", "shape": "box",
                      "position_cm": [(i % 5) * 70 - 140,
                                      (i // 5) * 70 - 70, 0],
                      "size_cm": [60, 40, 4],
                      "material": json.loads(json.dumps(spec))})
    return {"name": "v112 smoke", "kind": "scene", "parts": parts,
            "cameras": [], "constraints": [], "rubric": [],
            "budget": {"max_tris": 40000, "preview_px": 640}}


# ---------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.16 everywhere."""

    def test_version_is_1_12(self):
        self.assertEqual(__version__, "1.17")

    def test_docs_name_1_12(self):
        self.assertIn("v1.17", (ROOT / "README.md").read_text())
        self.assertIn("## v1.17", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.17", (ROOT / "DESIGN.md").read_text())


# ---------------------------------------------------------------- M7


class FamilyRegistryTests(unittest.TestCase):
    """M7: eleven families, each complete and closed."""

    def test_eleven_families(self):
        self.assertEqual(len(materials_mod.FAMILIES), 11)
        self.assertEqual(set(materials_mod.FAMILIES),
                         {"wood", "brick", "stone", "marble",
                          "concrete", "metal", "fabric", "leather",
                          "ground", "paint", "glass"})

    def test_no_skin_family(self):
        # Character Studio owns character materials.
        self.assertNotIn("skin", materials_mod.FAMILIES)

    def test_no_tile_or_carpet(self):
        # Neither is named in the task list's M7 set; not invented.
        self.assertNotIn("tile", materials_mod.FAMILIES)
        self.assertNotIn("carpet", materials_mod.FAMILIES)

    def test_fifty_one_styles(self):
        total = sum(len(f["styles"])
                    for f in materials_mod.FAMILIES.values())
        self.assertEqual(total, 51)

    def test_every_family_has_a_complete_table(self):
        for family, table in materials_mod.PARAMS_BY_FAMILY.items():
            for required in ("seed", "scale_cm", "hue", "depth"):
                self.assertIn(required, table, family)
            for name, meta in table.items():
                self.assertEqual(len(meta), 7, (family, name))
                _label, low, high, default, _unit, _simple, page = meta
                self.assertLessEqual(low, default, (family, name))
                self.assertLessEqual(default, high, (family, name))
                self.assertIn(page, materials_mod.PAGES, (family, name))

    def test_every_style_has_a_hex_base(self):
        for family, fam in materials_mod.FAMILIES.items():
            for style, meta in fam["styles"].items():
                base = meta["base"]
                self.assertRegex(base, r"^#[0-9a-f]{6}$",
                                 (family, style, base))
                self.assertRegex(meta["dark"], r"^#[0-9a-f]{6}$",
                                 (family, style))

    def test_every_layout_has_a_label(self):
        for family, fam in materials_mod.FAMILIES.items():
            for layout in fam["layouts"]:
                self.assertIn(layout, materials_mod.LAYOUT_LABELS,
                              (family, layout))

    def test_style_defaults_are_in_range(self):
        for family, fam in materials_mod.FAMILIES.items():
            table = materials_mod.PARAMS_BY_FAMILY[family]
            for style, meta in fam["styles"].items():
                for name, value in meta.get("defaults", {}).items():
                    self.assertIn(name, table, (family, style, name))
                    _l, low, high, _d, _u, _s, _p = table[name]
                    self.assertLessEqual(low, value,
                                         (family, style, name))
                    self.assertLessEqual(value, high,
                                         (family, style, name))

    def test_style_defaults_apply(self):
        spec = materials_mod.normalize_material_spec(
            {"family": "glass", "style": "frosted"})
        self.assertEqual(spec["params"]["frost"], 0.7)
        spec = materials_mod.normalize_material_spec(
            {"family": "paint", "style": "aged"})
        self.assertEqual(spec["params"]["chip"], 0.5)

    def test_user_param_beats_style_default(self):
        spec = materials_mod.normalize_material_spec(
            {"family": "glass", "style": "frosted",
             "params": {"frost": 0.2}})
        self.assertEqual(spec["params"]["frost"], 0.2)

    def test_every_family_validates_and_normalizes(self):
        for family, fam in materials_mod.FAMILIES.items():
            for style in fam["styles"]:
                for layout in fam["layouts"]:
                    spec = {"family": family, "style": style,
                            "layout": layout}
                    norm = materials_mod.normalize_material_spec(spec)
                    self.assertEqual(norm["family"], family)
                    self.assertEqual(norm["style"], style)
                    self.assertEqual(norm["layout"], layout)


class ResolveSpecTests(unittest.TestCase):
    """M7: the dispatcher resolves every family, with the right
    physical properties."""

    def test_metal_is_metallic(self):
        for style in materials_mod.FAMILIES["metal"]["styles"]:
            resolved = materials_mod.resolve_spec(
                materials_mod.normalize_material_spec(
                    {"family": "metal", "style": style}))
            # Blackened metal is the one exception (0.9).
            self.assertGreaterEqual(resolved["metallic"], 0.9, style)

    def test_wood_is_never_metallic(self):
        resolved = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec({"family": "wood"}))
        self.assertEqual(resolved["metallic"], 0.0)

    def test_glass_transmits(self):
        for style in materials_mod.FAMILIES["glass"]["styles"]:
            resolved = materials_mod.resolve_spec(
                materials_mod.normalize_material_spec(
                    {"family": "glass", "style": style}))
            # Smoked glass is the one exception (0.85).
            self.assertGreaterEqual(resolved["transmission"], 0.85, style)
        clear = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "glass", "style": "clear"}))
        self.assertEqual(clear["transmission"], 1.0)

    def test_glass_tint_is_baked_into_the_base_colour(self):
        clear = materials_mod.spec_base_color(
            {"family": "glass", "style": "clear"})
        tinted = materials_mod.spec_base_color(
            {"family": "glass", "style": "tinted_green"})
        self.assertNotEqual(clear, tinted)
        # Tinted green is darker and greener than clear.
        self.assertLess(int(tinted[1:3], 16), int(clear[1:3], 16))
        self.assertGreater(int(tinted[3:5], 16),
                           int(tinted[1:3], 16))

    def test_resolve_material_uses_the_metallic_value(self):
        _color, metallic, _rough = blender_export.resolve_material(
            {"id": "p", "material": {"family": "metal",
                                     "style": "copper"}})
        self.assertEqual(metallic, 1.0)
        _c, wood_metal, _r = blender_export.resolve_material(
            {"id": "p", "material": {"family": "wood"}})
        self.assertEqual(wood_metal, 0.0)

    def test_every_solid_recipe_is_under_25_nodes(self):
        for family, fam in materials_mod.FAMILIES.items():
            for style in fam["styles"]:
                count = materials_mod.node_count(
                    {"family": family, "style": style,
                     "layout": "solid"})
                self.assertLess(count, 25, (family, style, count))


# ---------------------------------------------------------------- M8


class LayoutTests(unittest.TestCase):
    """M8: layouts apply per family only where they make sense,
    and every combination stays in budget."""

    def test_layout_vocab_per_family(self):
        self.assertEqual(tuple(materials_mod.FAMILIES["wood"]["layouts"]),
                         ("solid", "boards", "parquet", "herringbone",
                          "chevron"))
        self.assertEqual(tuple(materials_mod.FAMILIES["brick"]["layouts"]),
                         ("solid", "stack", "stretcher", "english",
                          "flemish", "basket_weave"))
        self.assertEqual(tuple(materials_mod.FAMILIES["stone"]["layouts"]),
                         ("solid", "stack", "stretcher"))
        self.assertEqual(tuple(materials_mod.FAMILIES["marble"]["layouts"]),
                         ("solid",))

    def test_wrong_family_layout_rejected(self):
        desc = {"name": "t", "kind": "scene", "parts": [
            {"id": "p", "type": "prop", "shape": "box",
             "position_cm": [0, 0, 0], "size_cm": [10, 10, 10],
             "material": {"family": "wood", "layout": "english"}}],
            "constraints": [], "rubric": [],
            "budget": {"max_tris": 40000, "preview_px": 640}}
        problems = desc_mod.validate(desc)
        self.assertTrue(any("layout" in p for p in problems), problems)

    def test_every_combo_under_40_with_wear(self):
        worst = 0
        worst_spec = None
        for family, fam in materials_mod.FAMILIES.items():
            table = materials_mod.PARAMS_BY_FAMILY[family]
            for style in fam["styles"]:
                for layout in fam["layouts"]:
                    params = {}
                    if "knots" in table:
                        params["knots"] = 1.0
                    if "chip" in table:
                        params["chip"] = 1.0
                    spec = {"family": family, "style": style,
                            "layout": layout, "params": params,
                            "wear": {"dirt": 0.5}}
                    count = materials_mod.node_count(spec)
                    if count > worst:
                        worst, worst_spec = count, spec
                    self.assertLess(count, 40, (spec, count))
        # The known worst case: herringbone wood, knots, wear.
        self.assertEqual(worst, 35, worst_spec)

    def test_bond_bias(self):
        stack = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "brick", "layout": "stack"}))
        self.assertEqual(stack["bond"]["bias"], 0)
        stretcher = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "brick", "layout": "stretcher"}))
        self.assertEqual(stretcher["bond"]["bias"], 1)

    def test_flemish_header_fraction(self):
        flemish = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "brick", "layout": "flemish"}))
        # A header is the brick's length over length + depth.
        self.assertAlmostEqual(flemish["bond"]["header_frac"],
                               21.5 / (21.5 + 10.25), places=3)
        self.assertAlmostEqual(flemish["bond"]["unit_ratio"],
                               (21.5 + 10.25) / 6.5, places=2)

    def test_face_frame_picks_the_largest_face(self):
        # A wall panel: the XY face is largest.
        rot, row, unit = blender_export._face_frame([120, 60, 4])
        self.assertEqual((rot, row, unit), ((0.0, 0.0, 0.0), 1, 0))
        # A floor slab: the XZ face is largest.
        rot, row, unit = blender_export._face_frame([120, 4, 60])
        self.assertEqual((rot, row, unit), ((math.pi / 2, 0.0, 0.0), 1, 0))
        # A side panel: the YZ face is largest.
        rot, row, unit = blender_export._face_frame([4, 120, 60])
        self.assertEqual((rot, row, unit), ((0.0, math.pi / 2, 0.0), 0, 1))

    def test_bond_prescale_is_uniform(self):
        # One cell is one block height on both face axes, so the
        # Brick node's single mortar size is the same real width
        # on both axes. The uniform axes are the face axes
        # (row, unit) from _face_frame.
        size = [120, 60, 4]
        _rot, row, unit = blender_export._face_frame(size)
        spec = blender_export._part_material_spec(
            {"id": "p",
             "material": {"family": "brick", "layout": "stretcher"}},
            size)
        prescale = spec["bond_prescale"]
        self.assertAlmostEqual(prescale[row], prescale[unit])


# ---------------------------------------------------------------- M9


class WearTests(unittest.TestCase):
    """M9: the wear layer gates per family and costs one node."""

    def test_wear_group_node_count(self):
        self.assertEqual(materials_mod.WEAR_GROUP_NODE_COUNT, 32)

    def test_wear_adds_exactly_one_node(self):
        plain = materials_mod.node_count(
            {"family": "wood", "style": "oak"})
        worn = materials_mod.node_count(
            {"family": "wood", "style": "oak",
             "wear": {"dirt": 0.5}})
        self.assertEqual(worn, plain + 1)

    def test_moss_grows_only_on_wood_stone_ground(self):
        for family in ("wood", "stone", "ground"):
            self.assertTrue(materials_mod.wear_applies(family, "moss"),
                            family)
        for family in ("brick", "marble", "concrete", "metal",
                       "fabric", "leather", "paint", "glass"):
            self.assertFalse(materials_mod.wear_applies(family, "moss"),
                             family)

    def test_rust_only_on_metal(self):
        self.assertTrue(materials_mod.wear_applies("metal", "rust"))
        for family in ("wood", "brick", "stone", "marble", "concrete",
                       "fabric", "leather", "ground", "paint", "glass"):
            self.assertFalse(materials_mod.wear_applies(family, "rust"),
                             family)

    def test_gated_wear_does_not_turn_wear_on(self):
        resolved = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "metal", "wear": {"moss": 0.8}}))
        self.assertFalse(resolved["wear_any"])
        resolved = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "metal", "wear": {"rust": 0.8}}))
        self.assertTrue(resolved["wear_any"])

    def test_edge_width_alone_does_not_turn_wear_on(self):
        # edge_width only shapes edge_wear; on its own it is nothing.
        resolved = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "wood", "wear": {"edge_width": 0.9}}))
        self.assertFalse(resolved["wear_any"])
        resolved = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "wood",
                 "wear": {"edge_width": 0.9, "edge_wear": 0.1}}))
        self.assertTrue(resolved["wear_any"])

    def test_wear_defaults_are_all_zero(self):
        spec = materials_mod.normalize_material_spec(
            {"family": "wood"})
        self.assertTrue(all(v == 0 for v in spec["wear"].values()))

    def test_wear_params_are_seven_tuples(self):
        for name, meta in materials_mod.WEAR_PARAMS.items():
            self.assertEqual(len(meta), 7, name)
            self.assertEqual(meta[6], "Life", name)


# ---------------------------------------------------------------- M10


class SimpleKnobTests(unittest.TestCase):
    """M10: Simple mode curates 3-4 knobs per family."""

    def test_three_or_four_simple_knobs_per_family(self):
        for family, table in materials_mod.PARAMS_BY_FAMILY.items():
            simple = [n for n, m in table.items() if m[5]]
            self.assertGreaterEqual(len(simple), 3, (family, simple))
            self.assertLessEqual(len(simple), 4, (family, simple))
            self.assertIn("seed", simple, family)
            self.assertIn("scale_cm", simple, family)

    def test_life_simple_knobs(self):
        simple = [n for n, m in materials_mod.WEAR_PARAMS.items()
                  if m[5]]
        self.assertEqual(simple, ["dirt", "edge_wear", "wet"])

    def test_ten_pages(self):
        self.assertEqual(len(materials_mod.PAGES), 10)
        self.assertEqual(materials_mod.PAGES[6], "Life")


class PlannerPromptTests(unittest.TestCase):
    """The planner vocabulary covers all eleven families."""

    def test_format_spec_mentions_every_family(self):
        for family in materials_mod.FAMILIES:
            self.assertIn(family, live.FORMAT_SPEC, family)

    def test_format_spec_mentions_layouts_and_wear(self):
        for word in ("herringbone", "flemish", "basket_weave",
                     "dirt", "edge_wear", "rust", "moss"):
            self.assertIn(word, live.FORMAT_SPEC, word)


# ---------------------------------------------------------------- M11


class CmSizeTests(unittest.TestCase):
    """M11: pattern sizes are real centimetres."""

    def test_constants(self):
        self.assertEqual(materials_mod.BOARD_WIDTH_CM, 12.0)
        self.assertEqual(materials_mod.BRICK_LENGTH_CM, 21.5)
        self.assertEqual(materials_mod.BRICK_HEIGHT_CM, 6.5)
        self.assertEqual(materials_mod.BRICK_DEPTH_CM, 10.25)
        self.assertEqual(materials_mod.PARQUET_CELL_CM, 30.0)
        self.assertEqual(materials_mod.HERRINGBONE_LENGTH_CM, 60.0)
        self.assertEqual(materials_mod.HERRINGBONE_WIDTH_CM, 12.0)
        self.assertEqual(materials_mod.CHEVRON_WIDTH_CM, 12.0)
        self.assertEqual(materials_mod.STONE_LENGTH_CM, 40.0)
        self.assertEqual(materials_mod.STONE_HEIGHT_CM, 20.0)

    def test_parquet_cell_is_uniform(self):
        # At scale_cm 100 the mapping is 1:1, so the parity
        # prescale is exactly 1 cell per 30 cm on every axis.
        spec = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "wood", "layout": "parquet",
                 "params": {"scale_cm": 100}}))
        prescale = spec["parity_prescale"]
        self.assertAlmostEqual(prescale[0], 1.0 / 0.30)
        self.assertAlmostEqual(prescale[1], prescale[2])

    def test_herringbone_cell_from_cm(self):
        # At scale_cm 100: 1 cell per 12 cm across the plank,
        # 1 per 60 cm along it.
        spec = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "wood", "layout": "herringbone",
                 "params": {"scale_cm": 100}}))
        prescale = spec["parity_prescale"]
        self.assertAlmostEqual(prescale[1], 1.0 / 0.12)
        self.assertAlmostEqual(prescale[2], 1.0 / 0.60)

    def test_brick_cell_from_cm(self):
        spec = materials_mod.resolve_spec(
            materials_mod.normalize_material_spec(
                {"family": "brick", "layout": "stretcher"}))
        self.assertEqual(spec["bond"]["length_cm"], 21.5)
        self.assertEqual(spec["bond"]["height_cm"], 6.5)


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
class QtLifePageTests(_QtCase):
    """M9/M10: the dial's Life page."""

    def _dial(self, spec=None):
        from skullpt.qtwindows import MaterialDial

        dial = MaterialDial(self.ws, "plank",
                            spec or json.loads(json.dumps(BRICK_SPEC)))
        dial.show()
        self._windows.append(dial)
        self._pump()
        return dial

    def test_ten_pages_in_the_dial(self):
        from skullpt.qtwindows import _DIAL_PAGES
        self.assertEqual(len(_DIAL_PAGES), 10)
        self.assertEqual(_DIAL_PAGES[6], "Life")

    def test_life_page_simple_knobs(self):
        dial = self._dial()
        face = dial.face
        face.set_advanced(False)
        face.set_page(6)  # Life
        keys = [item["key"] for item in face._items()]
        self.assertEqual(keys, ["dirt", "edge_wear", "wet"])

    def test_life_page_advanced_shows_gated_knobs(self):
        dial = self._dial()
        face = dial.face
        face.set_advanced(True)
        face.set_page(6)  # Life
        keys = [item["key"] for item in face._items()]
        # Brick: no moss (not wood/stone/ground), no rust (not metal).
        self.assertNotIn("moss", keys)
        self.assertNotIn("rust", keys)
        self.assertIn("dirt", keys)
        self.assertIn("chips", keys)
        self.assertIn("scratches", keys)

    def test_life_page_gating_per_family(self):
        dial = self._dial({"family": "metal", "style": "iron"})
        face = dial.face
        face.set_advanced(True)
        face.set_page(6)
        keys = [item["key"] for item in face._items()]
        self.assertIn("rust", keys)
        self.assertNotIn("moss", keys)
        dial.face.set_spec({"family": "wood", "style": "oak"})
        keys = [item["key"] for item in face._items()]
        self.assertIn("moss", keys)
        self.assertNotIn("rust", keys)

    def test_wear_knob_drag_changes_the_wear_value(self):
        dial = self._dial()
        face = dial.face
        face.set_advanced(False)
        face.set_page(6)  # Life: dirt, edge_wear, wet
        # Three knobs fill the ring, 120 degrees each; dirt is
        # index 0, at the top, mid-radius 202.
        QtTest.QTest.mouseClick(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(280, 260 - 202))
        self.assertEqual(face._active_knob, "dirt")
        # Drag the centre knob up 100 px: +0.5 on a 0-1 knob.
        QtTest.QTest.mousePress(face, QtCore.Qt.LeftButton,
                                QtCore.Qt.NoModifier,
                                QtCore.QPoint(280, 260))
        QtTest.QTest.mouseMove(face, QtCore.QPoint(280, 160))
        QtTest.QTest.mouseRelease(face, QtCore.Qt.LeftButton,
                                  QtCore.Qt.NoModifier,
                                  QtCore.QPoint(280, 160))
        # Brick spec started with dirt 0.4; +0.5 lands at 0.9.
        self.assertAlmostEqual(face.spec()["wear"]["dirt"], 0.9)
        # And the param store is untouched.
        self.assertNotIn("dirt", face.spec()["params"])

    def test_zero_key_picks_saved(self):
        dial = self._dial()
        face = dial.face
        face.setFocus()
        QtTest.QTest.keyClick(face, QtCore.Qt.Key_0)
        self.assertEqual(face.page, 9)  # Saved

    def test_life_page_centre_text_is_not_clipped(self):
        # The v1.11 lesson, applied to the new page: no bright
        # text pixel may sit within 2 px of the centre rect's
        # left or right edge, on the Life page with no knob active.
        from skullpt.qtwindows import _DIAL_CX, _DIAL_CY
        dial = self._dial()
        dial.face.set_page(6)  # Life: no knob active, idle hint
        self._pump(100)
        img = dial.face.grab().toImage()
        for y in range(_DIAL_CY - 30, _DIAL_CY + 6):
            for x in (_DIAL_CX - 55, _DIAL_CX - 54,
                      _DIAL_CX + 54, _DIAL_CX + 55):
                c = img.pixelColor(x, y)
                self.assertLess(
                    c.red(), 120,
                    f"bright pixel at ({x}, {y}): Life hint clipped")

    def test_dial_opens_on_a_new_family(self):
        dial = self._dial()
        face = dial.face
        face.set_page(1)  # Style
        keys = [item["key"] for item in face._items()]
        self.assertEqual(keys, ["red", "weathered_red", "buff",
                                "dark", "white_painted"])
        face.set_page(2)  # Layout
        keys = [item["key"] for item in face._items()]
        self.assertEqual(keys, ["solid", "stack", "stretcher",
                                "english", "flemish", "basket_weave"])


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtPreviewNonBlockingTests(_QtCase):
    """M10: a preview render never blocks the UI thread."""

    def test_start_preview_returns_at_once(self):
        _window, render = self._render()
        slow = mock.Mock(
            side_effect=lambda spec, ws: (time.sleep(3.0), None)[1])
        with mock.patch.object(blender_export,
                               "render_material_preview", slow):
            started = time.monotonic()
            render._start_preview(BRICK_SPEC)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0,
                        f"_start_preview took {elapsed:.2f}s")
        # Let the background call finish before teardown.
        self._pump(3500)


# ---------------------------------------------------------------- e2e


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class MaterialPhase2EndToEndTests(unittest.TestCase):
    """A real phase-2 build through real Blender + preview renders."""

    def test_phase2_build_records_measured_node_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            script = blender_export.generate_script(
                _smoke_desc(), out_dir, "phase2")
            script_path = Path(tmp) / "build.py"
            script_path.write_text(script)
            run = subprocess.run(
                [blender_export.find_blender(), "--background",
                 "--factory-startup", "--python", str(script_path)],
                capture_output=True, text=True, timeout=600)
            self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
            stats = json.loads(
                (out_dir / "phase2.stats.json").read_text())
            mats = stats["materials"]
            # Measured == predicted for every object part.
            report = builder_mod.material_report(_smoke_desc())
            for part_id, entry in report.items():
                self.assertEqual(mats[part_id]["nodes"], entry["nodes"],
                                 part_id)
            # The wear group was built, with 32 nodes inside it.
            self.assertEqual(stats.get("wear_nodes"), 32)

    def test_preview_render_for_a_new_family_and_worn_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            png = blender_export.render_material_preview(BRICK_SPEC, ws)
            self.assertTrue(png.exists())
            self.assertGreater(png.stat().st_size, 1000)
            # A second call hits the cache, not Blender.
            with mock.patch.object(
                    blender_export, "find_blender",
                    return_value=None):
                again = blender_export.render_material_preview(
                    BRICK_SPEC, ws)
            self.assertEqual(png, again)

    def test_worn_preview_differs_from_clean(self):
        # Wear must actually change the render, not just the count.
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            clean = {"family": "metal", "style": "copper"}
            worn = {"family": "metal", "style": "copper",
                    "wear": {"rust": 1.0, "dirt": 1.0}}
            clean_png = blender_export.render_material_preview(
                clean, ws)
            worn_png = blender_export.render_material_preview(
                worn, ws)
            self.assertNotEqual(clean_png.read_bytes(),
                                worn_png.read_bytes())
