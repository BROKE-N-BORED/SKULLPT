"""Tests for the native Qt Bench (v1.0).

Geometry tests run anywhere (no Qt). Widget tests need PySide6 AND a GL
capable platform — run them under xvfb-run with the qt-libs on the path
(see AGENTS.md); they skip cleanly when no GL context can be made.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from skullpt import description as desc_mod
from skullpt import qtbench

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _bounds(tris):
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


class GeometryTests(unittest.TestCase):
    def test_hex_to_rgb(self):
        self.assertEqual(qtbench.hex_to_rgb("#7a5230"),
                         (0x7A / 255, 0x52 / 255, 0x30 / 255))

    def test_box_is_12_triangles_inside_its_bounds(self):
        tris = qtbench._box_tris([0, 0, 0], [100, 60, 40])
        self.assertEqual(len(tris), 12)
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        self.assertEqual((x0, x1, y0, y1, z0, z1), (0, 100, 0, 60, 0, 40))

    def test_cylinder_matches_footprint(self):
        tris = qtbench._cylinder_tris([10, 20, 0], [50, 50, 80])
        self.assertGreater(len(tris), 50)
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        self.assertAlmostEqual(x0, 10, places=5)
        self.assertAlmostEqual(x1, 60, places=5)
        self.assertAlmostEqual(z0, 0, places=5)
        self.assertAlmostEqual(z1, 80, places=5)

    def test_sphere_matches_footprint(self):
        tris = qtbench._sphere_tris([0, 0, 0], [60, 60, 60])
        self.assertGreater(len(tris), 100)
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        self.assertAlmostEqual(x0, 0, places=3)
        self.assertAlmostEqual(x1, 60, places=3)
        self.assertAlmostEqual(z0, 0, places=3)
        self.assertAlmostEqual(z1, 60, places=3)

    def test_extrude_offsets_profile_from_min(self):
        tris = qtbench._extrude_tris([100, 100, 0], [50, 50, 30],
                                     [[0, 0], [50, 0], [50, 50], [0, 50]])
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        self.assertEqual((x0, x1, y0, y1, z0, z1), (100, 150, 100, 150, 0, 30))

    def test_lathe_revolves_profile_around_axis(self):
        tris = qtbench._lathe_tris([0, 0, 0], [40, 40, 60],
                                   [[0, 0], [20, 30], [10, 60]])
        self.assertGreater(len(tris), 20)
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        # axis at (20, 20); max radius 20 -> x/y span 0..40
        self.assertAlmostEqual(x0, 0, places=3)
        self.assertAlmostEqual(x1, 40, places=3)
        self.assertAlmostEqual(z0, 0, places=3)
        self.assertAlmostEqual(z1, 60, places=3)

    def test_tube_follows_path_within_radius(self):
        tris = qtbench._tube_tris([[0, 0, 0], [0, 0, 100]], 5)
        self.assertGreater(len(tris), 10)
        (x0, x1), (y0, y1), (z0, z1) = _bounds(tris)
        # A 10-segment ring never quite touches the true radius (decagon
        # vertices sit at cos(pi/10) of it); it must stay inside it.
        self.assertTrue(-5.0 <= x0 <= -4.5)
        self.assertTrue(4.5 <= x1 <= 5.0)
        self.assertAlmostEqual(z0, 0, places=3)
        self.assertAlmostEqual(z1, 100, places=3)

    def test_scene_meshes_workbench_example(self):
        desc = desc_mod.load(EXAMPLES / "workbench.description.json")
        scene = qtbench.scene_meshes(desc)
        self.assertGreater(len(scene["meshes"]), 0)
        self.assertEqual(scene["bounds"]["span"] > 0, True)
        for mesh in scene["meshes"]:
            self.assertGreater(len(mesh["tris"]), 0)
            r, g, b = mesh["color"]
            self.assertTrue(0.0 <= r <= 1.0 and 0.0 <= g <= 1.0
                            and 0.0 <= b <= 1.0)
        shapes = {m["id"] for m in scene["meshes"]}
        self.assertEqual(len(shapes), len(scene["meshes"]))  # one mesh per part

    def test_scene_meshes_unknown_shape_falls_back_to_box(self):
        desc = {"name": "t", "kind": "scene", "parts": [
            {"id": "a", "type": "part", "shape": "box",
             "position_cm": [0, 0, 0], "size_cm": [10, 10, 10]}]}
        scene = qtbench.scene_meshes(desc)
        self.assertEqual(len(scene["meshes"]), 1)
        self.assertEqual(len(scene["meshes"][0]["tris"]), 12)


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls._app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "jobs").mkdir()
        (self.ws / "library").mkdir()
        desc = desc_mod.load(EXAMPLES / "workbench.description.json")
        from skullpt.jobs import Job
        self.job = Job.create(self.ws / "jobs", desc)
        self.window = qtbench.BenchWindow(self.ws)
        self.window.show()
        self._app.processEvents()
        self._app.processEvents()
        ctx = self.window._viewport.context()
        if ctx is None or not ctx.isValid():
            self.skipTest("no GL context on this platform (needs Xvfb + xcb)")

    def tearDown(self):
        self.window.close()
        self.tmp.cleanup()

    def test_viewport_renders_the_scene(self):
        img = self.window._viewport.grabFramebuffer()
        self.assertGreater(img.width(), 0)
        colors = set()
        for y in range(0, img.height(), 4):
            for x in range(0, img.width(), 4):
                colors.add(img.pixel(x, y))
        # Background + grid + axes + several lit part faces: not a blank frame.
        self.assertGreater(len(colors), 20,
                           "viewport frame is (near-)blank — nothing drew")

    def test_orbit_changes_the_picture(self):
        def sample():
            img = self.window._viewport.grabFramebuffer()
            return [img.pixel(x, y)
                    for x in range(0, img.width(), 8)
                    for y in range(0, img.height(), 8)]
        before = sample()
        self.window._viewport.azimuth = (self.window._viewport.azimuth + 90) % 360
        self.window._viewport.update()
        self._app.processEvents()
        self._app.processEvents()
        after = sample()
        self.assertNotEqual(before, after, "orbit did not change the render")

    def test_camera_round_trip(self):
        from skullpt.viewer import upsert_camera
        vp = self.window._viewport
        entry = vp.current_camera("Test cam")
        self.assertEqual(entry["name"], "Test cam")
        self.assertEqual(len(entry["position_cm"]), 3)
        desc = self.job.load_description()
        upsert_camera(desc, entry)
        desc_mod.dump(desc, self.job.desc_path)
        again = self.job.load_description()
        names = [c["name"] for c in again.get("cameras", [])]
        self.assertIn("Test cam", names)
        # And jumping back to it restores the same eye position.
        cam = next(c for c in again["cameras"] if c["name"] == "Test cam")
        vp.set_camera_position(cam["position_cm"], cam["look_at_cm"])
        eye = vp.camera_position()
        for got, want in zip(eye, cam["position_cm"]):
            self.assertAlmostEqual(got, want, places=1)

    def test_job_buttons_follow_state(self):
        state = self.job.state
        self.assertEqual(self.window._approve_btn.isEnabled(),
                         state == "awaiting_approval")
        self.assertEqual(self.window._build_btn.isEnabled(),
                         state == "approved")
        self.assertEqual(self.window._file_btn.isEnabled(),
                         state == "awaiting_review")


if __name__ == "__main__":
    unittest.main()
