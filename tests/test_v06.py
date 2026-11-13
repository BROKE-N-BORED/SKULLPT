"""v0.6 tests: shapes/textures/expansion, game-ready gate, live seats
(mock-verified), ingest, intake, recipe graduation, Unreal packaging."""
import ast
import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skullpt import blender_export, cli, description as desc_mod
from skullpt import expand, gameready, graduate, ingest, intake, live, unreal, viewer
from skullpt.critic import GeminiCritic
from skullpt.jobs import Job

ROOT = Path(__file__).resolve().parent.parent
STALL = ROOT / "examples" / "craft-stall.description.json"

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _part(**kw):
    base = {"id": "p", "type": "thing"}
    base.update(kw)
    return base


def _desc(parts, **kw):
    desc = {"name": "T", "kind": "scene", "parts": parts}
    desc.update(kw)
    return desc


class ValidationTests(unittest.TestCase):
    def test_lathe_needs_profile(self):
        self.assertTrue(desc_mod.validate(_desc([_part(shape="lathe")])))
        ok = _desc([_part(shape="lathe", profile_cm=[[0, 0], [5, 10]])])
        self.assertEqual(desc_mod.validate(ok), [])

    def test_tube_needs_path(self):
        self.assertTrue(desc_mod.validate(_desc([_part(shape="tube")])))
        ok = _desc([_part(shape="tube", path_cm=[[0, 0, 0], [10, 0, 5]], radius_cm=2)])
        self.assertEqual(desc_mod.validate(ok), [])

    def test_bad_texture_repeat_mirror_rejected(self):
        self.assertTrue(desc_mod.validate(_desc([_part(texture="sparkly")])))
        self.assertTrue(desc_mod.validate(_desc([_part(repeat={"count": 0, "step_cm": [1, 0, 0]})])))
        self.assertTrue(desc_mod.validate(_desc([_part(mirror={"axis": "z", "at_cm": 5})])))
        self.assertTrue(desc_mod.validate(_desc([_part(bevel_cm=-1)])))
        good = _desc([_part(texture="wood_grain", bevel_cm=1.5,
                            repeat={"count": 3, "step_cm": [10, 0, 0]},
                            mirror={"axis": "x", "at_cm": 100})])
        self.assertEqual(desc_mod.validate(good), [])


class ExpandTests(unittest.TestCase):
    def test_repeat_makes_numbered_copies_in_a_row(self):
        desc = _desc([_part(id="post", shape="box", position_cm=[0, 0, 0],
                            size_cm=[10, 10, 50],
                            repeat={"count": 3, "step_cm": [30, 0, 0]})])
        parts = expand.expand_parts(desc)
        self.assertEqual([p["id"] for p in parts], ["post", "post_2", "post_3"])
        self.assertEqual(parts[2]["position_cm"], [60, 0, 0])
        self.assertNotIn("repeat", parts[0])

    def test_mirror_flips_position_across_the_plane(self):
        desc = _desc([_part(id="leg", shape="box", position_cm=[10, 0, 0],
                            size_cm=[8, 8, 40], mirror={"axis": "x", "at_cm": 100})])
        parts = expand.expand_parts(desc)
        self.assertEqual([p["id"] for p in parts], ["leg", "leg_mirrored"])
        self.assertEqual(parts[1]["position_cm"], [182, 0, 0])  # 2*100 - (10+8)

    def test_stall_expands_to_19_parts(self):
        desc = desc_mod.load(STALL)
        self.assertEqual(len(expand.expand_parts(desc)), 19)


class ViewerShapeTests(unittest.TestCase):
    def setUp(self):
        self.desc = desc_mod.load(STALL)
        self.data = viewer.scene_data(self.desc)

    def test_boxes_carry_shapes(self):
        by_id = {b["id"]: b for b in self.data["boxes"]}
        self.assertEqual(by_id["vase"]["shape"], "lathe")
        self.assertEqual(by_id["vase"]["min"], [108.0, 78.0, 79.0])
        self.assertEqual(by_id["handle"]["shape"], "tube")
        self.assertEqual(by_id["picket_8"]["shape"], "box")
        self.assertIn("leg_a_mirrored", by_id)

    def test_page_draws_real_shapes_and_script_parses(self):
        page = viewer.render_page(self.desc, live=True)
        self.assertIn("shapeFaces", page)
        self.assertIn("prismFaces", page)
        node = None
        try:
            node = subprocess.run(["node", "--version"], capture_output=True).returncode == 0
        except OSError:
            node = False
        if node:
            import re
            script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
                fh.write(script)
                path = fh.name
            try:
                res = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stderr)
            finally:
                os.unlink(path)


class GateTests(unittest.TestCase):
    def _out_dir(self, tmp, total=5000, uv=True):
        out = Path(tmp)
        stats = {"parts": {"floor": {"tris": total, "uv": uv}}, "total_tris": total}
        (out / "gate-test.stats.json").write_text(json.dumps(stats))
        for name in ("gate-test_lod1.glb", "gate-test_lod2.glb", "gate-test_collision.glb"):
            (out / name).write_bytes(b"glTF" + b"\x00" * 8)
        return out

    def test_gate_passes_a_ready_asset(self):
        desc = _desc([_part(id="floor")], name="Gate Test", game_ready=True,
                     budget={"max_tris": 10000})
        with tempfile.TemporaryDirectory() as tmp:
            report = gameready.run_gate(desc, self._out_dir(tmp), [])
            self.assertTrue(report["ok"], report)

    def test_gate_fails_over_budget_and_missing_uv(self):
        desc = _desc([_part(id="floor")], name="Gate Test", game_ready=True,
                     budget={"max_tris": 100})
        with tempfile.TemporaryDirectory() as tmp:
            report = gameready.run_gate(desc, self._out_dir(tmp, total=5000, uv=False), [])
            self.assertFalse(report["ok"])
            failed = {c["check"] for c in report["checks"] if not c["ok"]}
            self.assertIn("triangle_budget", failed)
            self.assertIn("uv_maps_present", failed)


class _MockGemini(BaseHTTPRequestHandler):
    plan_desc = {
        "name": "Mock Stall", "kind": "scene",
        "parts": [{"id": "floor", "type": "floor", "shape": "box",
                   "position_cm": [0, 0, 0], "size_cm": [200, 200, 10]}],
        "constraints": [{"type": "on_ground", "part": "floor"}],
        "budget": {"max_tris": 5000, "preview_px": 640}, "rubric": [], "style": {},
    }

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        text = json.dumps(body)
        if "vision critic" in text:
            reply = {"fixes": [{"part": "floor", "issue": "floor floats",
                                "fix": "lower it", "patch": {"position_cm": [0, 0, 0]}}]}
            payload = json.dumps(reply)
        else:
            payload = json.dumps(self.plan_desc)
        resp = {"candidates": [{"content": {"parts": [{"text": payload}]}}]}
        raw = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LiveSeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGemini)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "DESIGNSHELL_GEMINI_URL")}
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["DESIGNSHELL_GEMINI_URL"] = f"http://127.0.0.1:{self.port}/mock"

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_planner_drafts_a_valid_description(self):
        from skullpt.seats import load_seats
        desc = live.plan_from_words("a small stall", load_seats(None))
        self.assertEqual(desc["name"], "Mock Stall")
        self.assertEqual(desc_mod.validate(desc), [])

    def test_vision_critic_returns_structured_fixes(self):
        seat = {"provider": "gemini", "model": "mock", "env_key": "GEMINI_API_KEY"}
        critic = GeminiCritic(seat)
        desc = _desc([_part(id="floor", type="floor")], rubric=["floor sits on the ground"])
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as fh:
            fh.write(TINY_PNG)
            png = fh.name
        try:
            result = critic.critique(desc, [png])
        finally:
            os.unlink(png)
        self.assertTrue(result["vision"])
        self.assertEqual(result["fixes"][0]["part"], "floor")
        patches = live.extract_patches(result["fixes"])
        self.assertEqual(patches[0]["field"], "position_cm")
        applied = live.apply_patches(desc, patches)
        self.assertEqual(len(applied), 1)

    def test_no_key_refuses_loud(self):
        os.environ.pop("GEMINI_API_KEY")
        seat = {"provider": "gemini", "model": "mock", "env_key": "GEMINI_API_KEY"}
        with self.assertRaises(live.SeatNotConfigured):
            live.gemini_text(seat, "hello")


def _make_glb() -> bytes:
    gltf = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "Test Box", "mesh": 0, "translation": [1, 2, 0.5]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"min": [0, 0, 0], "max": [0.5, 0.25, 0.1]}],
    }
    raw = json.dumps(gltf).encode()
    raw += b" " * ((4 - len(raw) % 4) % 4)
    total = 12 + 8 + len(raw)
    return struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(raw), b"JSON") + raw


class IngestTests(unittest.TestCase):
    def test_glb_becomes_a_description_in_shell_coords(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".glb", delete=False) as fh:
            fh.write(_make_glb())
            path = fh.name
        try:
            desc = ingest.ingest_glb(path)
        finally:
            os.unlink(path)
        self.assertEqual(desc_mod.validate(desc), [])
        part = desc["parts"][0]
        self.assertEqual(part["id"], "test_box")
        self.assertEqual(part["size_cm"], [50.0, 10.0, 25.0])
        self.assertEqual(part["position_cm"], [100.0, -60.0, 200.0])

    def test_ingest_rejects_other_files(self):
        with self.assertRaises(ValueError):
            ingest.ingest("model.obj")


class IntakeTests(unittest.TestCase):
    def test_photo_is_measured_and_filed(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "my-chair.png"
            photo.write_bytes(TINY_PNG)
            desc, dest_dir = intake.intake_photo(Path(tmp) / "ws", photo)
            self.assertEqual(desc["reference"]["width_px"], 1)
            self.assertTrue((dest_dir / "my-chair.png").exists())
            self.assertEqual(desc_mod.validate(desc), [])


class GraduateTests(unittest.TestCase):
    def test_filed_pattern_graduates_and_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "jobs").mkdir()
            desc = desc_mod.load(ROOT / "examples" / "shop-panel.description.json")
            job = Job.create(ws / "jobs", desc)
            path = graduate.graduate_recipe(ws, job, {"outputs": []})
            self.assertTrue(path.exists())
            rows = graduate.list_recipes(ws)
            self.assertEqual(len(rows), 1)
            fresh = graduate.apply_recipe(ws, path.name)
            self.assertEqual(fresh["parts"], desc["parts"])


class UnrealPackageTests(unittest.TestCase):
    def test_package_carries_glb_script_and_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "jobs").mkdir()
            desc = desc_mod.load(ROOT / "examples" / "tavern-room.description.json")
            job = Job.create(ws / "jobs", desc)
            (job.output_dir / "tavern-room.glb").write_bytes(b"glTF" + b"\x00" * 16)
            (job.output_dir / "tavern-room.stats.json").write_text('{"total_tris": 10, "parts": {}}')
            dest = unreal.package_unreal(job, Path(tmp) / "pkg")
            names = {p.name for p in dest.iterdir()}
            self.assertIn("import_into_unreal.py", names)
            self.assertIn("lineage.json", names)
            self.assertIn("tavern-room.glb", names)
            script = (dest / "import_into_unreal.py").read_text()
            ast.parse(script)  # must be valid Python (runs inside Unreal)
            self.assertIn("/Game/SKULLPT/tavern-room", script)
            lineage = json.loads((dest / "lineage.json").read_text())
            self.assertFalse(lineage["imported"])
            self.assertEqual(len(lineage["source_description_sha256"]), 64)


class ScriptContentTests(unittest.TestCase):
    def test_stall_script_has_new_shapes_textures_and_game_ready(self):
        desc = desc_mod.load(STALL)
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.generate_script(desc, Path(tmp), "craft-stall")
        compile(script, "generated", "exec")
        for token in ("build_lathe", "build_tube", "wood_grain", "brushed_metal",
                      "ShaderNodeTexNoise", "DS_STATS", "DS_LOD", "DS_COLLISION",
                      "UCX_", "picket_8", "leg_a_mirrored"):
            self.assertIn(token, script)


class FreshnessTests(unittest.TestCase):
    def test_stale_outputs_never_pass_for_a_new_build(self):
        # Regression (found in the v0.6 sandbox builds): a crashed Blender
        # run left a previous build's files in the folder and the exporter
        # counted them as landed. Expected outputs are now cleared first.
        desc = desc_mod.load(ROOT / "examples" / "tavern-room.description.json")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for name in ("tavern-room.glb", "tavern-room.blend", "tavern-room.stats.json",
                         "render-door-view.png", "render-over-the-counter.png",
                         "render-iso-auto.png"):
                (out / name).write_bytes(b"glTF stale")
            fake = Path(tmp) / "fake-blender"
            fake.write_text("#!/bin/sh\nexit 0\n")  # runs, produces nothing
            fake.chmod(0o755)
            saved = os.environ.get("DESIGNSHELL_BLENDER")
            os.environ["DESIGNSHELL_BLENDER"] = str(fake)
            try:
                with self.assertRaises(RuntimeError):
                    blender_export.export_blender_scene(desc, out)
            finally:
                if saved is None:
                    os.environ.pop("DESIGNSHELL_BLENDER", None)
                else:
                    os.environ["DESIGNSHELL_BLENDER"] = saved


@unittest.skipUnless(blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
                     "needs Blender (end-to-end); skipped in the fast suite")
class StallEndToEndTests(unittest.TestCase):
    def test_craft_stall_builds_game_ready_outputs_and_ingests_back(self):
        desc = desc_mod.load(STALL)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            outputs = blender_export.export_blender_scene(desc, out)
            for name in ("craft-stall.glb", "craft-stall.blend", "craft-stall.stats.json",
                         "craft-stall_lod1.glb", "craft-stall_lod2.glb",
                         "craft-stall_collision.glb", "render-front.png",
                         "render-close-on-the-vase.png", "render-iso-auto.png"):
                self.assertIn(name, outputs)
                self.assertGreater((out / name).stat().st_size, 0)
            gate = gameready.run_gate(desc, out, outputs)
            self.assertTrue(gate["ok"], gate)
            # round trip: the GLB we shipped ingests back into a description
            back = ingest.ingest_glb(out / "craft-stall.glb")
            self.assertEqual(desc_mod.validate(back), [])
            self.assertGreaterEqual(len(back["parts"]), 19)


if __name__ == "__main__":
    unittest.main()
