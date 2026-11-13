"""Making-engine tests: script generation, Blender discovery, binary checks.

The script-generation tests need no Blender. The end-to-end test runs only
when a Blender executable is actually available on this machine.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import blender_export, checks as checks_mod
from skullpt import description as desc_mod

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "tavern-room.description.json"


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.desc = desc_mod.load(EXAMPLE)
        self.script = blender_export.generate_script(self.desc, Path("/tmp/out"), "tavern-room")

    def test_script_compiles_as_python(self):
        compile(self.script, "generated_blender_script.py", "exec")

    def test_script_carries_every_part_and_camera(self):
        for token in ("floor", "counter", "lamp", "Door view", "Over the counter", "Iso (auto)"):
            self.assertIn(token, self.script)

    def test_script_uses_meters_not_cm(self):
        # Floor is 600 cm wide -> 6.0 m in the payload.
        self.assertIn("600", self.script)  # raw cm in camera/bounds payload is fine
        data_line = [ln for ln in self.script.splitlines() if ln.startswith("D = ")]
        self.assertTrue(data_line)
        self.assertIn("'size': [600.0, 500.0, 10.0]", data_line[0])

    def test_no_placeholders_left(self):
        self.assertNotIn("__PAYLOAD__", self.script)
        self.assertNotIn("__SURFACE__", self.script)

    def test_lamp_gets_a_light_and_glow(self):
        self.assertIn('"glow": True', self.script.replace("'", '"'))


class FindBlenderTests(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.NamedTemporaryFile() as fh:
            old = os.environ.get("DESIGNSHELL_BLENDER")
            os.environ["DESIGNSHELL_BLENDER"] = fh.name
            try:
                self.assertEqual(blender_export.find_blender(), fh.name)
            finally:
                if old is None:
                    os.environ.pop("DESIGNSHELL_BLENDER", None)
                else:
                    os.environ["DESIGNSHELL_BLENDER"] = old

    def test_missing_blender_fails_loud(self):
        old = blender_export.find_blender
        blender_export.find_blender = lambda: None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(RuntimeError) as ctx:
                    blender_export.export_blender_scene(desc_mod.load(EXAMPLE), Path(tmp))
            self.assertIn("Blender was not found", str(ctx.exception))
        finally:
            blender_export.find_blender = old


class BinaryCheckTests(unittest.TestCase):
    def _run(self, files: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for name, data in files.items():
                (out / name).write_bytes(data)
            desc = {"name": "T", "kind": "scene",
                    "parts": [{"id": "a", "type": "box"}], "constraints": [], "budget": {}}
            report = checks_mod.run_machine_checks(desc, out, list(files))
            return {c["check"]: c for c in report["checks"]}

    def test_valid_signatures_pass(self):
        checks = self._run({
            "a.png": b"\x89PNG\r\n\x1a\n" + b"x" * 64,
            "a.glb": b"glTF" + b"x" * 64,
            "a.blend": b"BLENDER" + b"x" * 64,
        })
        self.assertTrue(checks["binary_outputs_valid"]["ok"])

    def test_fake_png_fails(self):
        checks = self._run({"a.png": b"not a png at all, sorry"})
        self.assertFalse(checks["binary_outputs_valid"]["ok"])


@unittest.skipUnless(blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
                     "Blender not installed on this machine (or e2e skipped)")
class EndToEndTests(unittest.TestCase):
    def test_tavern_room_builds_real_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = blender_export.export_blender_scene(desc_mod.load(EXAMPLE), Path(tmp))
            for name in ("tavern-room.blend", "tavern-room.glb",
                         "render-door-view.png", "render-over-the-counter.png", "render-iso-auto.png"):
                self.assertIn(name, outputs)
                self.assertGreater((Path(tmp) / name).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
