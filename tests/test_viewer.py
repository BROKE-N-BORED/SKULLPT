"""Live Render Window tests: scene geometry and cameras."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import description as desc_mod
from skullpt import viewer

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "tavern-room.description.json"


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.desc = desc_mod.load(EXAMPLE)

    def test_boxes_use_true_positions(self):
        data = viewer.scene_data(self.desc)
        by_id = {b["id"]: b for b in data["boxes"]}
        self.assertEqual(by_id["floor"]["min"], [0.0, 0.0, 0.0])
        self.assertEqual(by_id["lamp"]["min"], [150.0, 115.0, 120.0])
        self.assertFalse(any(b["auto"] for b in data["boxes"]))

    def test_bounds_cover_scene(self):
        data = viewer.scene_data(self.desc)
        self.assertEqual(data["bounds"]["max"], [600.0, 500.0, 152.0])
        self.assertEqual(data["bounds"]["center"], [300.0, 250.0, 76.0])

    def test_missing_geometry_is_auto_placed_and_flagged(self):
        box = viewer.part_box({"id": "x", "type": "button"}, 2)
        self.assertTrue(box["auto"])
        self.assertEqual(box["min"], [260.0, 0.0, 0.0])

    def test_example_cameras_ride_along(self):
        data = viewer.scene_data(self.desc)
        names = [c["name"] for c in data["cameras"]]
        self.assertEqual(names, ["Door view", "Over the counter"])


class CameraTests(unittest.TestCase):
    def test_upsert_adds_then_replaces(self):
        desc = {"name": "T", "kind": "scene", "parts": [{"id": "a", "type": "box"}]}
        viewer.upsert_camera(desc, {"name": "Hero", "position_cm": [1, 2, 3], "look_at_cm": [0, 0, 0]})
        viewer.upsert_camera(desc, {"name": "Hero", "position_cm": [9, 9, 9], "look_at_cm": [0, 0, 0]})
        self.assertEqual(len(desc["cameras"]), 1)
        self.assertEqual(desc["cameras"][0]["position_cm"], [9.0, 9.0, 9.0])
        self.assertEqual(desc_mod.validate(desc), [])

    def test_upsert_rejects_bad_vector(self):
        desc = {"name": "T", "kind": "scene", "parts": [{"id": "a", "type": "box"}]}
        with self.assertRaises(desc_mod.DescriptionError):
            viewer.upsert_camera(desc, {"name": "Bad", "position_cm": [1, 2], "look_at_cm": [0, 0, 0]})

    def test_delete_camera(self):
        desc = {"name": "T", "kind": "scene", "parts": [{"id": "a", "type": "box"}],
                "cameras": [{"name": "A", "position_cm": [0, 0, 0], "look_at_cm": [0, 0, 0]}]}
        self.assertTrue(viewer.delete_camera(desc, "A"))
        self.assertFalse(viewer.delete_camera(desc, "A"))

    def test_validation_catches_bad_camera(self):
        desc = {"name": "T", "kind": "scene", "parts": [{"id": "a", "type": "box"}],
                "cameras": [{"name": "", "position_cm": [0, 0], "look_at_cm": [0, 0, 0]}]}
        problems = desc_mod.validate(desc)
        self.assertTrue(any("cameras[0]" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
