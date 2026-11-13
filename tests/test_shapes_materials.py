"""Item 4 tests: shapes + materials — validation, script generation, e2e."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import blender_export, description as desc_mod

ROOT = Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "examples" / "workbench.description.json"


def _desc(parts, **kw):
    base = {"name": "T", "kind": "scene", "parts": parts}
    base.update(kw)
    return base


class ShapeValidationTests(unittest.TestCase):
    def test_bad_shape_rejected(self):
        problems = desc_mod.validate(_desc([{"id": "a", "type": "x", "shape": "pyramid"}]))
        self.assertTrue(any("shape" in p for p in problems))

    def test_extrude_needs_a_real_profile(self):
        problems = desc_mod.validate(_desc([{"id": "a", "type": "x", "shape": "extrude",
                                             "profile_cm": [[0, 0], [1, 1]]}]))
        self.assertTrue(any("profile_cm" in p for p in problems))
        ok = desc_mod.validate(_desc([{"id": "a", "type": "x", "shape": "extrude",
                                       "profile_cm": [[0, 0], [10, 0], [0, 10]]}]))
        self.assertEqual(ok, [])

    def test_material_must_be_a_string(self):
        problems = desc_mod.validate(_desc([{"id": "a", "type": "x", "material": 7}]))
        self.assertTrue(any("material" in p for p in problems))


class MaterialTests(unittest.TestCase):
    def test_presets_resolve(self):
        self.assertEqual(blender_export.resolve_material({"material": "mahogany"})[0], "#5d2f22")
        self.assertEqual(blender_export.resolve_material({"material": "chrome"})[1], 1.0)
        self.assertEqual(blender_export.resolve_material({"material": "#123456"})[0], "#123456")

    def test_no_material_falls_back_to_palette(self):
        color, metallic, _ = blender_export.resolve_material({"id": "thing", "type": "box"})
        self.assertTrue(color.startswith("#"))
        self.assertEqual(metallic, 0.0)


class WorkbenchScriptTests(unittest.TestCase):
    def setUp(self):
        self.desc = desc_mod.load(WORKBENCH)
        self.script = blender_export.generate_script(self.desc, Path("/tmp/o"), "workbench-corner")

    def test_script_compiles(self):
        compile(self.script, "gen", "exec")

    def test_all_shape_ops_present(self):
        for token in ("primitive_cylinder_add", "primitive_uv_sphere_add",
                      "build_extrude", "primitive_cube_add"):
            self.assertIn(token, self.script)

    def test_materials_carry_metalness(self):
        # black_metal legs: metallic 0.85 rides in the payload.
        self.assertIn("'metallic': 0.85", self.script)
        self.assertIn("'glass': True", self.script)


@unittest.skipUnless(blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
                     "Blender not installed on this machine (or e2e skipped)")
class WorkbenchEndToEndTests(unittest.TestCase):
    def test_workbench_builds_real_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = blender_export.export_blender_scene(desc_mod.load(WORKBENCH), Path(tmp))
            for name in ("workbench-corner.blend", "workbench-corner.glb",
                         "render-front.png", "render-close-on-the-lamp.png", "render-iso-auto.png"):
                self.assertIn(name, outputs)
                self.assertGreater((Path(tmp) / name).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
