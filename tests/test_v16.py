"""SKULLPT v1.6 tests: P2 T21-T30 (T26 splits to v1.7 — design only)
and perf P3/P4.

Backend tests run everywhere; Qt tests skip without PySide6.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys_path = str(ROOT)
import sys  # noqa: E402

sys.path.insert(0, sys_path)
# The tests directory itself: test_v16 reuses the mock Gradio server
# and the GLB builder from the sibling test modules, and those imports
# must work whether the file is run by discover or directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skullpt import backup as backup_mod  # noqa: E402
from skullpt import bench as bench_mod  # noqa: E402
from skullpt import blender_export  # noqa: E402
from skullpt import cloudmesh  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import imagegen  # noqa: E402
from skullpt import ingest  # noqa: E402
from skullpt import live  # noqa: E402
from skullpt import settings as settings_mod  # noqa: E402
from skullpt import unreal  # noqa: E402
from skullpt import update as update_mod  # noqa: E402
from skullpt.critic import GeminiCritic  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())
TAVERN = json.loads((ROOT / "examples" / "tavern-room.description.json").read_text())

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000000020001e221bc330000"
    "000049454e44ae426082")


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _make_png(width: int, height: int, color=(200, 30, 30)) -> bytes:
    """A plain RGB PNG of the given size (filter 0 rows)."""
    raw = bytearray()
    row = bytes(color) * width
    for _ in range(height):
        raw.append(0)
        raw += row

    def chunk(ctype: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + ctype + payload
                + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), level=6))
    out += chunk(b"IEND", b"")
    return bytes(out)


def _png_size(data: bytes) -> tuple[int, int]:
    return struct.unpack(">II", data[16:24])


# ------------------------------------------------- description + settings


class DescriptionV16Tests(unittest.TestCase):
    def test_render_block_valid(self):
        desc = dict(SHOP)
        desc["render"] = {"engine": "cycles", "resolution_px": 1024,
                          "samples": 64, "denoise": True}
        self.assertEqual(desc_mod.validate(desc), [])

    def test_render_block_rejects_bad_engine(self):
        desc = dict(SHOP)
        desc["render"] = {"engine": "octane"}
        problems = desc_mod.validate(desc)
        self.assertTrue(any("render.engine" in p for p in problems))

    def test_render_block_rejects_bad_resolution_and_samples(self):
        desc = dict(SHOP)
        desc["render"] = {"resolution_px": 64, "samples": 9999}
        problems = desc_mod.validate(desc)
        self.assertTrue(any("resolution_px" in p for p in problems))
        self.assertTrue(any("samples" in p for p in problems))

    def test_mesh_block_valid_and_invalid(self):
        desc = dict(SHOP)
        desc["mesh"] = {"file": "meshes/chair.glb", "format": "glb",
                        "import_scale": 1.0}
        self.assertEqual(desc_mod.validate(desc), [])
        desc["mesh"] = {"file": "meshes/chair.glb", "format": "dae"}
        self.assertTrue(any("mesh.format" in p for p in desc_mod.validate(desc)))
        desc["mesh"] = {"file": "", "format": "glb"}
        self.assertTrue(any("mesh.file" in p for p in desc_mod.validate(desc)))
        desc["mesh"] = {"file": "meshes/chair.glb", "import_scale": -1}
        self.assertTrue(any("import_scale" in p for p in desc_mod.validate(desc)))

    def test_part_mesh_valid_and_invalid(self):
        desc = json.loads(json.dumps(SHOP))
        desc["parts"][0]["mesh"] = {"source": "glb", "node": 0,
                                    "vertices": 120, "triangles": 200}
        self.assertEqual(desc_mod.validate(desc), [])
        desc["parts"][0]["mesh"] = {"source": "glb", "triangles": -5}
        self.assertTrue(any("mesh.triangles" in p for p in desc_mod.validate(desc)))
        desc["parts"][0]["mesh"] = {"source": "blend"}
        self.assertTrue(any("mesh.source" in p for p in desc_mod.validate(desc)))

    def test_estimate_uses_recorded_mesh_triangles(self):
        part = {"shape": "box", "mesh": {"source": "glb", "triangles": 4821}}
        self.assertEqual(desc_mod.estimate_triangles(part), 4821)
        plain = {"shape": "box"}
        self.assertEqual(desc_mod.estimate_triangles(plain), 12)


class SettingsV16Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_resolve(self):
        resolved = settings_mod.resolve_render_settings(self.ws, dict(SHOP))
        self.assertEqual(resolved, {"engine": "auto", "resolution_px": 640,
                                    "samples": 24, "denoise": False})

    def test_job_block_wins_over_workspace(self):
        settings_mod.save_render_settings(self.ws, {"engine": "cycles",
                                                    "resolution_px": 800})
        desc = dict(SHOP)
        desc["render"] = {"engine": "eevee"}
        resolved = settings_mod.resolve_render_settings(self.ws, desc)
        self.assertEqual(resolved["engine"], "eevee")
        self.assertEqual(resolved["resolution_px"], 800)

    def test_budget_preview_px_is_the_last_fallback(self):
        desc = dict(SHOP)
        desc["budget"] = {"preview_px": 320}
        resolved = settings_mod.resolve_render_settings(self.ws, desc)
        self.assertEqual(resolved["resolution_px"], 320)

    def test_save_ignores_invalid_values(self):
        settings_mod.save_render_settings(
            self.ws, {"engine": "octane", "resolution_px": 10,
                      "samples": 9999, "denoise": "yes"})
        saved = settings_mod.load_settings(self.ws)
        self.assertEqual(saved["render_engine"], "auto")
        self.assertEqual(saved["render_resolution_px"], 640)
        self.assertEqual(saved["render_samples"], 24)
        self.assertFalse(saved["render_denoise"])

    def test_last_render_engine_round_trip(self):
        self.assertIsNone(settings_mod.last_render_engine(self.ws)["engine"])
        settings_mod.record_render_engine(self.ws, "CYCLES")
        last = settings_mod.last_render_engine(self.ws)
        self.assertEqual(last["engine"], "CYCLES")
        self.assertTrue(last["at"])
        settings_mod.record_render_engine(self.ws, "OCTANE")
        self.assertEqual(settings_mod.last_render_engine(self.ws)["engine"],
                         "CYCLES")


# ------------------------------------------------------- P3 / P4 / T25 / T21


class BlenderSplitTests(unittest.TestCase):
    def test_build_script_compiles_and_never_renders(self):
        script = blender_export.generate_script(TAVERN, Path("/tmp/out"), "tavern-room")
        compile(script, "build.py", "exec")
        self.assertNotIn("bpy.ops.render.render", script)
        self.assertIn("DS_MESH", script)  # the mesh-import branch is present
        for token in ("floor", "counter", "lamp", "Door view", "Iso (auto)"):
            self.assertIn(token, script)

    def test_render_script_compiles_and_renders(self):
        script = blender_export.generate_render_script(
            TAVERN, Path("/tmp/out"), "tavern-room")
        compile(script, "render.py", "exec")
        self.assertIn("open_mainfile", script)
        self.assertIn("bpy.ops.render.render", script)
        self.assertIn("Door view", script)
        self.assertIn("Iso (auto)", script)

    def test_render_script_carries_render_settings(self):
        desc = json.loads(json.dumps(TAVERN))
        desc["render"] = {"engine": "cycles", "resolution_px": 1024,
                          "samples": 64, "denoise": True}
        script = blender_export.generate_render_script(desc, Path("/tmp/out"), "tavern-room")
        self.assertIn("1024", script)
        self.assertIn("64", script)
        self.assertIn("use_denoising", script)

    def test_probe_never_raises_on_a_missing_exe(self):
        self.assertEqual(blender_export.probe_engine("/no/such/blender"), "CYCLES")

    def test_probe_uses_the_real_blender_when_there_is_one(self):
        exe = blender_export.find_blender()
        if exe is None:
            self.skipTest("Blender not installed on this machine")
        result = blender_export.probe_engine(exe)
        self.assertIn(result, ("BLENDER_EEVEE_NEXT", "CYCLES"))

    def test_resolve_mesh_file_none_for_built_jobs(self):
        self.assertIsNone(
            blender_export._resolve_mesh_file(dict(SHOP), Path("/tmp/out")))

    def test_resolve_mesh_file_finds_cloud_glb_in_job_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "jobs" / "stool" 
            out_dir = job_dir / "output"
            out_dir.mkdir(parents=True)
            glb = job_dir / "stool-mesh.glb"
            glb.write_bytes(b"glTF" + b"\x00" * 100)
            desc = dict(SHOP)
            desc["cloud_mesh"] = {"glb": "stool-mesh.glb"}
            path, fmt, scale = blender_export._resolve_mesh_file(desc, out_dir)
            self.assertEqual(path, glb)
            self.assertEqual(fmt, "glb")
            self.assertEqual(scale, 1.0)

    def test_resolve_mesh_file_finds_workspace_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "meshes").mkdir()
            (ws / "jobs" / "chair" / "output").mkdir(parents=True)
            mesh = ws / "meshes" / "chair.obj"
            mesh.write_text("v 0 0 0\n")
            desc = dict(SHOP)
            desc["mesh"] = {"file": "meshes/chair.obj", "format": "obj",
                            "import_scale": 0.01}
            path, fmt, scale = blender_export._resolve_mesh_file(
                desc, ws / "jobs" / "chair" / "output")
            self.assertEqual(path, mesh)
            self.assertEqual(fmt, "obj")
            self.assertEqual(scale, 0.01)

    def test_resolve_mesh_file_fails_loud_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "output"
            out_dir.mkdir()
            desc = dict(SHOP)
            desc["mesh"] = {"file": "meshes/gone.glb", "format": "glb"}
            with self.assertRaises(RuntimeError) as ctx:
                blender_export._resolve_mesh_file(desc, out_dir)
            self.assertIn("gone.glb", str(ctx.exception))

    def test_build_script_imports_the_mesh_and_skips_its_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "output"
            out_dir.mkdir()
            glb = Path(tmp) / "mesh.glb"
            glb.write_bytes(b"glTF" + b"\x00" * 100)
            desc = json.loads(json.dumps(SHOP))
            desc["mesh"] = {"file": str(glb), "format": "glb"}
            script = blender_export.generate_script(desc, out_dir, "shop")
            self.assertIn("import_scene.gltf", script)
            self.assertIn(str(glb), script)
            self.assertIn('part["type"] == "imported"', script)


# ---------------------------------------------------------------- T22


def _valid_draft() -> dict:
    return json.loads(json.dumps(SHOP))


def _invalid_draft() -> dict:
    bad = json.loads(json.dumps(SHOP))
    bad["parts"][0]["shape"] = "pyramid"
    return bad


class RepairLoopTests(unittest.TestCase):
    def setUp(self):
        self.seats = {
            "planner": {"provider": "gemini", "model": "m", "env_key": "GEMINI_API_KEY"},
            "repairer": {"provider": "groq", "model": "m", "env_key": "GROQ_API_KEY"},
            "fallback": {"provider": "ollama", "model": "m", "env_key": None},
        }

    def test_repairer_fixes_an_invalid_draft(self):
        def fake_seat_text(seat, prompt, image_png=None, extra_images=None):
            if seat.get("provider") == "groq":
                return json.dumps(_valid_draft())
            return json.dumps(_invalid_draft())

        with mock.patch.object(live, "seat_text", side_effect=fake_seat_text):
            desc = live.plan_from_words("a shop panel", self.seats)
        self.assertEqual(desc_mod.validate(desc), [])
        used = live.last_plan_seat()
        self.assertTrue(used["repaired"])
        self.assertEqual(used["repair_seat"], "repairer")

    def test_planner_repairs_when_the_repairer_has_no_key(self):
        def fake_seat_text(seat, prompt, image_png=None, extra_images=None):
            if seat.get("provider") == "groq":
                raise live.SeatNotConfigured("no GROQ_API_KEY")
            if "repairer seat" in prompt:
                return json.dumps(_valid_draft())
            return json.dumps(_invalid_draft())

        with mock.patch.object(live, "seat_text", side_effect=fake_seat_text):
            desc = live.plan_from_words("a shop panel", self.seats)
        self.assertEqual(desc_mod.validate(desc), [])
        used = live.last_plan_seat()
        self.assertTrue(used["repaired"])
        self.assertEqual(used["repair_seat"], "planner")

    def test_a_clean_draft_is_not_repaired(self):
        def fake_seat_text(seat, prompt, image_png=None, extra_images=None):
            return json.dumps(_valid_draft())

        with mock.patch.object(live, "seat_text", side_effect=fake_seat_text):
            desc = live.plan_from_words("a shop panel", self.seats)
        self.assertEqual(desc_mod.validate(desc), [])
        self.assertFalse(live.last_plan_seat()["repaired"])
        self.assertIsNone(live.last_plan_seat()["repair_seat"])

    def test_a_failed_repair_names_both_attempts(self):
        def fake_seat_text(seat, prompt, image_png=None, extra_images=None):
            return json.dumps(_invalid_draft())

        with mock.patch.object(live, "seat_text", side_effect=fake_seat_text):
            with self.assertRaises(live.SeatCallFailed) as ctx:
                live.plan_from_words("a shop panel", self.seats)
        self.assertIn("repair", str(ctx.exception))
        self.assertIn("still failed validation", str(ctx.exception))


# ---------------------------------------------------------------- T23


class CriticAllRendersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.png1 = self.dir / "render-door-view.png"
        self.png2 = self.dir / "render-iso-auto.png"
        self.png1.write_bytes(TINY_PNG)
        self.png2.write_bytes(TINY_PNG)
        self._saved = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._saved
        self.tmp.cleanup()

    def test_every_render_is_judged_in_one_call(self):
        calls = []

        def fake_gemini_text(seat, prompt, image_png=None, extra_images=None):
            calls.append({"prompt": prompt, "image_png": image_png,
                          "extra_images": extra_images or []})
            return '{"fixes": []}'

        critic = GeminiCritic({"provider": "gemini", "model": "m",
                               "env_key": "GEMINI_API_KEY"})
        with mock.patch.object(live, "gemini_text", side_effect=fake_gemini_text):
            result = critic.critique(dict(SHOP), [str(self.png1), str(self.png2)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["image_png"], TINY_PNG)
        self.assertEqual(calls[0]["extra_images"], [TINY_PNG])
        self.assertIn("2 renders", calls[0]["prompt"])
        self.assertEqual(result["images"],
                         ["render-door-view.png", "render-iso-auto.png"])
        self.assertEqual(result["image"], "render-door-view.png")
        self.assertTrue(result["vision"])
        self.assertIn("2 renders", result["note"])

    def test_one_render_still_works(self):
        critic = GeminiCritic({"provider": "gemini", "model": "m",
                               "env_key": "GEMINI_API_KEY"})
        with mock.patch.object(live, "gemini_text",
                               return_value='{"fixes": []}'):
            result = critic.critique(dict(SHOP), [str(self.png1)])
        self.assertEqual(result["images"], ["render-door-view.png"])


# ---------------------------------------------------------------- T30


OBJ_TEXT = """# two boxes
o Box1
v 0 0 0
v 10 0 0
v 10 10 0
v 0 10 0
v 0 0 5
v 10 0 5
v 10 10 5
v 0 10 5
f 1 2 3 4
f 5 6 7 8
o Box2
v 20 0 0
v 30 0 0
v 30 10 0
v 20 10 0
v 20 0 5
v 30 0 5
v 30 10 5
v 20 10 5
f 9 10 11 12
f 13 14 15 16
"""

STL_ASCII = """solid wedge
 facet normal 0 0 1
  outer loop
   vertex 0 0 0
   vertex 10 0 0
   vertex 0 10 0
  endloop
 endfacet
 facet normal 0 0 1
  outer loop
   vertex 0 0 5
   vertex 10 0 5
   vertex 0 10 5
  endloop
 endfacet
endsolid wedge
"""


def _binary_stl() -> bytes:
    header = b"binary stl" + b"\x00" * 70
    count = struct.pack("<I", 1)
    record = struct.pack("<12fH",
                         0, 0, 1,  # normal
                         0, 0, 0, 10, 0, 0, 0, 10, 0,  # triangle
                         0)
    return header + count + record


class IngestV16Tests(unittest.TestCase):
    def test_obj_groups_become_parts_with_mesh_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "two-boxes.obj"
            path.write_text(OBJ_TEXT)
            desc = ingest.ingest_obj(path)
        self.assertEqual(len(desc["parts"]), 2)
        box1, box2 = desc["parts"]
        self.assertEqual(box1["id"], "box1")
        # Y-up -> Z-up, 1 unit = 1 cm: (x, -z, y).
        self.assertEqual(box1["position_cm"], [0.0, -5.0, 0.0])
        self.assertEqual(box1["size_cm"], [10.0, 5.0, 10.0])
        self.assertEqual(box1["mesh"]["source"], "obj")
        self.assertEqual(box1["mesh"]["vertices"], 8)
        self.assertEqual(box1["mesh"]["triangles"], 4)
        self.assertEqual(box2["position_cm"], [20.0, -5.0, 0.0])
        self.assertEqual(desc["mesh"]["format"], "obj")
        self.assertEqual(desc["mesh"]["import_scale"], 0.01)
        self.assertIn("1 unit = 1 cm", desc["summary"])
        self.assertEqual(desc_mod.validate(desc), [])

    def test_obj_mesh_is_preserved_in_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            path = Path(tmp) / "two-boxes.obj"
            path.write_text(OBJ_TEXT)
            desc = ingest.ingest_obj(path, ws)
            self.assertEqual(desc["mesh"]["file"], "meshes/two-boxes.obj")
            self.assertTrue((ws / "meshes" / "two-boxes.obj").exists())
            self.assertEqual((ws / "meshes" / "two-boxes.obj").read_text(),
                             OBJ_TEXT)

    def test_obj_rejects_a_file_with_no_vertices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.obj"
            path.write_text("# nothing here\n")
            with self.assertRaises(ValueError):
                ingest.ingest_obj(path)

    def test_stl_ascii_becomes_a_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wedge.stl"
            path.write_text(STL_ASCII)
            desc = ingest.ingest_stl(path)
        self.assertEqual(len(desc["parts"]), 1)
        part = desc["parts"][0]
        self.assertEqual(part["position_cm"], [0.0, 0.0, 0.0])
        self.assertEqual(part["size_cm"], [10.0, 10.0, 5.0])
        self.assertEqual(part["mesh"]["triangles"], 2)
        self.assertEqual(part["mesh"]["vertices"], 6)
        self.assertEqual(desc["mesh"]["format"], "stl")
        self.assertEqual(desc_mod.validate(desc), [])

    def test_stl_binary_becomes_a_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tri.stl"
            path.write_bytes(_binary_stl())
            desc = ingest.ingest_stl(path)
        self.assertEqual(len(desc["parts"]), 1)
        self.assertEqual(desc["parts"][0]["mesh"]["triangles"], 1)
        self.assertEqual(desc["parts"][0]["size_cm"], [10.0, 10.0, 0.0])

    def test_glb_now_records_its_mesh(self):
        from test_v06 import _make_glb

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test-box.glb"
            path.write_bytes(_make_glb())
            desc = ingest.ingest_glb(path)
        self.assertEqual(desc["mesh"]["format"], "glb")
        self.assertEqual(desc["mesh"]["import_scale"], 1.0)
        self.assertEqual(desc["parts"][0]["mesh"]["source"], "glb")
        self.assertEqual(desc["parts"][0]["mesh"]["node"], 0)
        self.assertEqual(desc_mod.validate(desc), [])

    def test_ingest_dispatch_rejects_unsupported_formats(self):
        with self.assertRaises(ValueError) as ctx:
            ingest.ingest("model.dae")
        self.assertIn(".dae", str(ctx.exception))


# ---------------------------------------------------------------- T21


class UnrealMeshTests(unittest.TestCase):
    def _mesh_job(self, ws: Path, with_build: bool) -> Job:
        desc = json.loads(json.dumps(SHOP))
        desc["cloud_mesh"] = {"glb": "stool-mesh.glb"}
        desc["mesh"] = {"file": "stool-mesh.glb", "format": "glb",
                        "import_scale": 1.0}
        job = Job.create(ws / "jobs", desc)
        (job.dir / "stool-mesh.glb").write_bytes(b"glTF" + b"\x00" * 100)
        if with_build:
            job.output_dir.mkdir(parents=True, exist_ok=True)
            (job.output_dir / f"{job.dir.name}.glb").write_bytes(
                b"glTF" + b"\x00" * 100)
        return job

    def test_unbuilt_mesh_job_packages_the_mesh_as_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._mesh_job(ws, with_build=False)
            slug = job.dir.name
            dest = unreal.package_unreal(job, job.dir)
            self.assertTrue((dest / f"{slug}-mesh.glb").exists())
            script = (dest / "import_into_unreal.py").read_text()
            self.assertIn(f"{slug}-mesh.glb", script)
            lineage = json.loads((dest / "lineage.json").read_text())
            self.assertEqual(lineage["mesh_source"], f"{slug}-mesh.glb")
            self.assertTrue(lineage["main_asset_is_source_mesh"])
            readme = (dest / "README.txt").read_text()
            self.assertIn("no Blender build yet", readme)

    def test_built_mesh_job_prefers_the_built_glb(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._mesh_job(ws, with_build=True)
            slug = job.dir.name
            dest = unreal.package_unreal(job, job.dir)
            self.assertTrue((dest / f"{slug}.glb").exists())
            self.assertTrue((dest / f"{slug}-mesh.glb").exists())
            script = (dest / "import_into_unreal.py").read_text()
            self.assertIn(f'GLB_NAME = "{slug}.glb"', script)
            lineage = json.loads((dest / "lineage.json").read_text())
            self.assertEqual(lineage["mesh_source"], f"{slug}-mesh.glb")
            self.assertFalse(lineage["main_asset_is_source_mesh"])

    def test_job_with_no_mesh_and_no_build_still_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws)
            with self.assertRaises(RuntimeError) as ctx:
                unreal.package_unreal(job, job.dir)
            self.assertIn("no GLB output", str(ctx.exception))


# ---------------------------------------------------------------- T28


class PngDownscaleTests(unittest.TestCase):
    def test_large_png_shrinks_to_the_cap(self):
        data = _make_png(2000, 1000)
        scaled = cloudmesh._downscale_png(data, 1024)
        self.assertIsNotNone(scaled)
        self.assertEqual(_png_size(scaled), (1024, 512))
        # The result is a valid PNG the shell can read back.
        self.assertEqual(scaled[:8], b"\x89PNG\r\n\x1a\n")

    def test_small_png_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.png"
            path.write_bytes(_make_png(400, 300))
            payload, scaled = cloudmesh.prepare_upload_photo(path)
            self.assertFalse(scaled)
            self.assertEqual(payload, path.read_bytes())

    def test_large_png_is_shrunk_before_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.png"
            path.write_bytes(_make_png(2048, 2048))
            payload, scaled = cloudmesh.prepare_upload_photo(path)
            self.assertTrue(scaled)
            self.assertEqual(_png_size(payload), (1024, 1024))

    def test_jpeg_uploads_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
            path.write_bytes(data)
            payload, scaled = cloudmesh.prepare_upload_photo(path)
            self.assertFalse(scaled)
            self.assertEqual(payload, data)

    def test_garbage_png_falls_back_to_the_original(self):
        self.assertIsNone(cloudmesh._downscale_png(b"not a png at all", 1024))


class _EstimationServer(BaseHTTPRequestHandler):
    """A Gradio Space that reports a queue position before completing."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/gradio_api/upload":
            self._json(200, ["/tmp/mock/photo.png"])
            return
        self._json(200, {"event_id": "evt-1"})

    def do_GET(self):
        if self.path.startswith("/gradio_api/file="):
            from test_cloudmesh import GLB_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf-binary")
            self.send_header("Content-Length", str(len(GLB_BYTES)))
            self.end_headers()
            self.wfile.write(GLB_BYTES)
            return
        if self.path.startswith("/gradio_api/call/"):
            from test_cloudmesh import GLB_FILE, MASKED_IMAGE
            frames = [
                ("estimation", {"rank": 2, "queue_size": 5,
                                "avg_event_process_time": 30.0}),
                ("process_completed",
                 {"output": {"data": [MASKED_IMAGE]}, "success": True}),
            ]
            # The extract call needs a GLB, not a masked image — the
            # client distinguishes by call order, and this server
            # cannot tell which call an event belongs to, so it
            # answers every call with the masked image except that
            # the extract's download is served above. generate_mesh
            # only checks the extract answer is a dict, so a masked
            # image dict with a URL still downloads the GLB bytes.
            body = "".join(
                f"event: {name}\ndata: {json.dumps(data)}\n\n"
                for name, data in frames).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"detail": "no such route"})

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class CloudMeshHardeningTests(unittest.TestCase):
    """T28, against the mock Gradio server from test_cloudmesh."""

    @classmethod
    def setUpClass(cls):
        from test_cloudmesh import _MockGradio

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockGradio)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from test_cloudmesh import _MockGradio

        _MockGradio.mode = "ok"
        _MockGradio.requests = 0
        _MockGradio.calls = []
        _MockGradio.uploads = []
        _MockGradio.seen_auth = []
        _MockGradio.events = {}
        _MockGradio.extract_calls = 0
        self._saved = {k: os.environ.get(k) for k in
                       ("HF_TOKEN", "DESIGNSHELL_CLOUDMESH_URL",
                        "DESIGNSHELL_CLOUDMESH_FALLBACK_URL")}
        os.environ["HF_TOKEN"] = "test-token"
        os.environ["DESIGNSHELL_CLOUDMESH_URL"] = f"http://127.0.0.1:{self.port}"
        os.environ.pop("DESIGNSHELL_CLOUDMESH_FALLBACK_URL", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        (self.ws / "jobs").mkdir()
        self.photo = Path(self.tmp.name) / "stool.png"
        self.photo.write_bytes(TINY_PNG)

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self.tmp.cleanup()

    def test_cloud_mesh_records_the_mesh_block(self):
        result = cloudmesh.cloud_mesh(self.ws, self.photo)
        job = Job(self.ws / "jobs" / result["job"])
        desc = job.load_description()
        self.assertEqual(desc["mesh"], {"file": "stool-mesh.glb",
                                        "format": "glb", "import_scale": 1.0})
        self.assertEqual(desc_mod.validate(desc), [])

    def test_fallback_space_is_used_when_the_primary_is_down(self):
        # The primary is a closed port; the fallback is the mock server.
        os.environ["DESIGNSHELL_CLOUDMESH_URL"] = "http://127.0.0.1:1"
        os.environ["DESIGNSHELL_CLOUDMESH_FALLBACK_URL"] = (
            f"http://127.0.0.1:{self.port}")
        stages = []
        glb_bytes, _seed = cloudmesh.generate_mesh(
            self.photo, progress=lambda s, d: stages.append(s))
        self.assertEqual(glb_bytes[:4], b"glTF")
        self.assertIn("fallback", stages)

    def test_quota_never_falls_back(self):
        from test_cloudmesh import _MockGradio

        _MockGradio.mode = "quota"
        os.environ["DESIGNSHELL_CLOUDMESH_FALLBACK_URL"] = (
            f"http://127.0.0.1:{self.port}")
        with self.assertRaises(cloudmesh.QuotaExhaustedError):
            cloudmesh.generate_mesh(self.photo)
        # The fallback was never tried: only the primary's calls ran.
        self.assertLessEqual(_MockGradio.requests, 6)

    def test_extract_is_retried_twice_now(self):
        from test_cloudmesh import _MockGradio

        _MockGradio.mode = "extract-flaky"
        glb_bytes, _seed = cloudmesh.generate_mesh(self.photo)
        self.assertEqual(glb_bytes[:4], b"glTF")
        self.assertEqual(_MockGradio.extract_calls, 2)

    def test_queue_position_reaches_progress(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _EstimationServer)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            seen = []
            glb_bytes, _seed = cloudmesh.generate_mesh(
                self.photo, base=f"http://127.0.0.1:{port}",
                progress=lambda s, d: seen.append((s, d)))
            self.assertEqual(glb_bytes[:4], b"glTF")
            queue = [d for s, d in seen if s == "queue"]
            self.assertTrue(queue)
            self.assertIn("position 3", queue[0])
            self.assertIn("of 5", queue[0])
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------- T27


FAKE_SD_RECORDING = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
with open(out + ".argv", "w") as fh:
    fh.write("\\n".join(args))
png = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000000020001e221bc330000"
    "000049454e44ae426082")
open(out, "wb").write(png)
"""


class _FakeImageEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        (self.ws / "library").mkdir(parents=True)
        self.fake = self.tmp / "sd-cli"
        self.fake.write_text(FAKE_SD_RECORDING)
        self.fake.chmod(0o755)
        self.model = self.tmp / "model.gguf"
        self.model.write_bytes(b"GGUF-fake")
        self._env = dict(os.environ)
        os.environ["DESIGNSHELL_SD_CLI"] = str(self.fake)
        os.environ["DESIGNSHELL_SD_MODEL"] = str(self.model)
        self._ram = mock.patch.object(
            imagegen.governor, "free_ram_mb", return_value=8192)
        self._ram.start()

    def tearDown(self):
        self._ram.stop()
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ImageGuidanceTests(_FakeImageEnv):
    def test_img2img_flags_reach_the_engine(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        src = bdir / "source.png"
        src.write_bytes(TINY_PNG)
        batch = imagegen.generate(
            self.ws, bdir,
            guidance={"init_img": str(src), "strength": 0.6})
        argv = (bdir / "cand-01.png.argv").read_text().splitlines()
        self.assertIn("-i", argv)
        self.assertIn(str(src), argv)
        self.assertIn("--strength", argv)
        self.assertIn("0.6", argv)
        self.assertEqual(batch["guidance"]["init_img"], str(src))

    def test_missing_init_image_fails_before_the_lock(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        with self.assertRaises(imagegen.ImageGenError) as ctx:
            imagegen.generate(
                self.ws, bdir,
                guidance={"init_img": str(bdir / "gone.png")})
        self.assertIn("missing", str(ctx.exception))
        self.assertEqual(len(batch["candidates"]), 0)

    def test_control_net_needs_a_model_and_an_image(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        with mock.patch.object(imagegen, "find_control_net", return_value=None):
            with self.assertRaises(imagegen.ImageGenError) as ctx:
                imagegen.generate(self.ws, bdir,
                                  guidance={"control_net": True})
        self.assertIn("ControlNet model", str(ctx.exception))

        control = self.tmp / "control-canny.gguf"
        control.write_bytes(b"GGUF-fake")
        with mock.patch.object(imagegen, "find_control_net",
                               return_value=control):
            with self.assertRaises(imagegen.ImageGenError) as ctx:
                imagegen.generate(self.ws, bdir,
                                  guidance={"control_net": True})
        self.assertIn("control image", str(ctx.exception))

        ctrl_img = bdir / "ctrl.png"
        ctrl_img.write_bytes(TINY_PNG)
        with mock.patch.object(imagegen, "find_control_net",
                               return_value=control):
            batch = imagegen.generate(
                self.ws, bdir,
                guidance={"control_net": True,
                          "control_image": str(ctrl_img),
                          "control_strength": 0.8})
        argv = (bdir / "cand-01.png.argv").read_text().splitlines()
        self.assertIn("--control-net", argv)
        self.assertIn(str(control), argv)
        self.assertIn("--control-image", argv)
        self.assertIn("0.8", argv)

    def test_ip_adapter_needs_clip_vision(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        adapter = self.tmp / "ip-adapter.gguf"
        adapter.write_bytes(b"GGUF-fake")
        style = bdir / "style.png"
        style.write_bytes(TINY_PNG)
        with mock.patch.object(imagegen, "find_ip_adapter",
                               return_value=adapter), \
             mock.patch.object(imagegen, "find_clip_vision",
                               return_value=None):
            with self.assertRaises(imagegen.ImageGenError) as ctx:
                imagegen.generate(
                    self.ws, bdir,
                    guidance={"ip_adapter": True,
                              "ip_adapter_image": str(style)})
        self.assertIn("CLIP vision", str(ctx.exception))

        clip = self.tmp / "clip-vision.gguf"
        clip.write_bytes(b"GGUF-fake")
        with mock.patch.object(imagegen, "find_ip_adapter",
                               return_value=adapter), \
             mock.patch.object(imagegen, "find_clip_vision",
                               return_value=clip):
            batch = imagegen.generate(
                self.ws, bdir,
                guidance={"ip_adapter": True,
                          "ip_adapter_image": str(style)})
        argv = (bdir / "cand-01.png.argv").read_text().splitlines()
        self.assertIn("--ip-adapter", argv)
        self.assertIn("--clip_vision", argv)
        self.assertIn(str(clip), argv)
        self.assertIn("--ip-adapter-image", argv)

    def test_vary_makes_a_new_batch_from_a_keeper(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        batch = imagegen.generate(self.ws, bdir)
        imagegen.decide(self.ws, bdir, "cand-01.png", "keep")
        varied = imagegen.vary(self.ws, bdir, "cand-01.png", count=2)
        self.assertNotEqual(varied["slug"], batch["slug"])
        self.assertEqual(varied["variation_of"],
                         {"batch": batch["slug"], "file": "cand-01.png"})
        self.assertEqual(varied["guidance"]["init_img"],
                         str(bdir / "cand-01.png"))
        self.assertEqual(varied["guidance"]["strength"], 0.6)
        self.assertEqual(len(varied["candidates"]), 2)

    def test_vary_refuses_a_pending_candidate(self):
        batch, bdir = imagegen.new_batch(self.ws, "a wooden stool", count=1)
        imagegen.generate(self.ws, bdir)
        with self.assertRaises(imagegen.ImageGenError) as ctx:
            imagegen.vary(self.ws, bdir, "cand-01.png")
        self.assertIn("only a kept candidate", str(ctx.exception))


# ---------------------------------------------------------------- T24


class QueueTests(unittest.TestCase):
    def setUp(self):
        bench_mod._RUNS.clear()

    def tearDown(self):
        bench_mod._RUNS.clear()

    def _wait_for(self, run_id: str, status: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with bench_mod._RUNS_LOCK:
                run = dict(bench_mod._RUNS.get(run_id, {}))
            if run.get("status") == status:
                return run
            time.sleep(0.02)
        return run

    def test_second_build_queues_behind_the_first(self):
        started = threading.Event()
        release = threading.Event()

        def slow(cancel, report):
            started.set()
            release.wait(timeout=5)
            return "first done"

        first = bench_mod._start_run("build", "a", slow, queue="heavy:test")
        self.assertTrue(started.wait(timeout=5))
        second = bench_mod._start_run("build", "b",
                                      lambda c, r: "second done",
                                      queue="heavy:test")
        with bench_mod._RUNS_LOCK:
            self.assertEqual(bench_mod._RUNS[second]["status"], "queued")
        self.assertEqual(bench_mod.queue_position("heavy:test", second), 1)
        release.set()
        self.assertEqual(self._wait_for(first, "done")["status"], "done")
        self.assertEqual(self._wait_for(second, "done")["status"], "done")
        self.assertEqual(bench_mod._RUNS[second]["detail"], "second done")

    def test_unqueued_runs_still_start_at_once(self):
        first = bench_mod._start_run("chat", "a",
                                     lambda c, r: "one")
        second = bench_mod._start_run("chat", "b",
                                      lambda c, r: "two")
        self.assertEqual(self._wait_for(first, "done")["status"], "done")
        self.assertEqual(self._wait_for(second, "done")["status"], "done")

    def test_cancelling_a_queued_run_drops_it_and_drains(self):
        started = threading.Event()
        release = threading.Event()

        def slow(cancel, report):
            started.set()
            release.wait(timeout=5)
            return "first done"

        first = bench_mod._start_run("build", "a", slow, queue="heavy:test")
        self.assertTrue(started.wait(timeout=5))
        second = bench_mod._start_run("build", "b",
                                      lambda c, r: "second done",
                                      queue="heavy:test")
        third = bench_mod._start_run("build", "c",
                                     lambda c, r: "third done",
                                     queue="heavy:test")
        self.assertTrue(bench_mod.cancel_run(second))
        with bench_mod._RUNS_LOCK:
            self.assertEqual(bench_mod._RUNS[second]["status"], "cancelled")
            self.assertEqual(bench_mod._RUNS[third]["status"], "queued")
        self.assertEqual(bench_mod.queue_position("heavy:test", third), 1)
        release.set()
        self.assertEqual(self._wait_for(third, "done")["status"], "done")

    def test_runs_view_hides_private_fields(self):
        started = threading.Event()
        release = threading.Event()

        def slow(cancel, report):
            started.set()
            release.wait(timeout=5)
            return "done"

        first = bench_mod._start_run("build", "a", slow, queue="heavy:test")
        self.assertTrue(started.wait(timeout=5))
        bench_mod._start_run("build", "b", lambda c, r: "x", queue="heavy:test")
        view = bench_mod._runs()
        self.assertTrue(all("_fn" not in r and "_queue" not in r
                            and "_cancel" not in r for r in view))
        # And the view is JSON-able (a function object would break it).
        json.dumps(view)
        release.set()
        self._wait_for(first, "done")

    def test_build_action_reports_its_queue_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job_a = _job(ws)
            job_b = _job(ws)
            job_a.approve()
            job_b.approve()
            started = threading.Event()

            def slow_build(job, workspace, cancel=None, progress=None):
                started.set()
                time.sleep(0.5)
                return {"ok": True, "outputs": [], "recipes": [],
                        "machine_checks": {"ok": True, "checks": []},
                        "critique": {}}

            with mock.patch("skullpt.builder.build_job",
                            side_effect=slow_build):
                first = bench_mod.act(ws, "build", {"job": job_a.dir.name})
                self.assertNotIn("queued", first)
                second = bench_mod.act(ws, "build", {"job": job_b.dir.name})
                self.assertTrue(second.get("queued"))
                self.assertIn("position 1", second["detail"])
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    runs = {r["id"]: r for r in bench_mod._runs()}
                    if (runs.get(first["run"], {}).get("status") == "done"
                            and runs.get(second["run"], {}).get("status") == "done"):
                        break
                    time.sleep(0.05)
                runs = {r["id"]: r for r in bench_mod._runs()}
                self.assertEqual(runs[first["run"]]["status"], "done")
                self.assertEqual(runs[second["run"]]["status"], "done")
            bench_mod._RUNS.clear()


# ---------------------------------------------------------------- T29


class BackupTests(unittest.TestCase):
    def test_backup_zips_the_workspace_without_the_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws)
            (ws / "hf_token.key").write_text("hf_secret")
            (ws / "chat.jsonl").write_text('{"role": "user"}\n')
            (ws / "meshes").mkdir()
            (ws / "meshes" / "chair.obj").write_text("v 0 0 0\n")
            dest = Path(tmp) / "backup.zip"
            result = backup_mod.backup_workspace(ws, dest)
            self.assertTrue(result["ok"])
            self.assertTrue(dest.exists())
            with zipfile.ZipFile(dest) as zf:
                names = zf.namelist()
            self.assertNotIn("hf_token.key", names)
            self.assertIn("chat.jsonl", names)
            self.assertIn("meshes/chair.obj", names)
            self.assertIn(f"jobs/{job.dir.name}/description.json", names)
            self.assertIn("backup-manifest.json", names)
            with zipfile.ZipFile(dest) as zf:
                manifest = json.loads(zf.read("backup-manifest.json"))
            self.assertIn("never in a backup", manifest["note"])
            self.assertIn("not in the backup", result["note"])

    def test_backup_names_a_default_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            result = backup_mod.backup_workspace(ws)
            path = Path(result["path"])
            self.assertTrue(path.exists())
            self.assertTrue(path.name.startswith(ws.name + "-backup-"))
            self.assertTrue(path.name.endswith(".zip"))

    def test_backup_refuses_a_missing_workspace(self):
        with self.assertRaises(FileNotFoundError):
            backup_mod.backup_workspace("/no/such/workspace")


class UpdateCheckTests(unittest.TestCase):
    def _fake_response(self, payload: dict):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(payload).encode()

        return Resp()

    def test_newer_release_is_reported(self):
        payload = {"tag_name": "v1.7",
                   "html_url": "https://github.com/x/y/releases/tag/v1.7"}
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=self._fake_response(payload)):
            result = update_mod.check_for_update("1.6")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"], "1.7")
        self.assertEqual(result["current"], "1.6")
        self.assertIsNone(result["error"])

    def test_same_version_is_not_an_update(self):
        payload = {"tag_name": "v1.6",
                   "html_url": "https://github.com/x/y/releases/tag/v1.6"}
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=self._fake_response(payload)):
            result = update_mod.check_for_update("1.6")
        self.assertFalse(result["update_available"])

    def test_network_failure_is_plain_not_an_error(self):
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no net")):
            result = update_mod.check_for_update("1.6")
        self.assertFalse(result["update_available"])
        self.assertIsNone(result["latest"])
        self.assertIn("could not reach GitHub", result["error"])


# ------------------------------------------------------- bench actions (v1.6)


class BenchActionsV16Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)
        bench_mod._RUNS.clear()

    def tearDown(self):
        bench_mod._RUNS.clear()
        self.tmp.cleanup()

    def test_render_settings_action_saves_a_job_override(self):
        job = _job(self.ws)
        job.approve()
        result = bench_mod.act(self.ws, "render-settings", {
            "job": job.dir.name, "engine": "cycles",
            "resolution_px": 1024, "samples": 64, "denoise": True})
        self.assertTrue(result["ok"])
        desc = job.load_description()
        self.assertEqual(desc["render"], {"engine": "cycles",
                                          "resolution_px": 1024,
                                          "samples": 64, "denoise": True})
        # Presentation, not content: the job stays approved.
        self.assertEqual(job.state, "approved")

    def test_render_settings_action_rejects_bad_values(self):
        job = _job(self.ws)
        with self.assertRaises(ValueError) as ctx:
            bench_mod.act(self.ws, "render-settings",
                          {"job": job.dir.name, "engine": "octane"})
        self.assertIn("render.engine", str(ctx.exception))

    def test_backup_action(self):
        result = bench_mod.act(self.ws, "backup",
                               {"dest": str(Path(self.tmp.name) / "b.zip")})
        self.assertTrue(result["ok"])
        self.assertTrue(Path(result["path"]).exists())

    def test_update_check_action_reports_plainly(self):
        payload = {"tag_name": "v1.6",
                   "html_url": "https://github.com/x/y/releases/tag/v1.6"}
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with mock.patch.object(urllib.request, "urlopen", return_value=resp):
            result = bench_mod.act(self.ws, "update-check", {})
        self.assertTrue(result["ok"])
        self.assertIn("latest release", result["detail"])


# ------------------------------------------------------------------- Qt (UI)

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
        from skullpt import qtbench

        window = qtbench.BenchWindow(self.ws)
        window.show()
        self._windows.append(window)
        self._app.processEvents()
        return window


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtRenderSettingsTests(_QtCase):
    """T25: the render controls exist, load the saved defaults, and
    save back through the settings layer."""

    def test_settings_window_render_controls_save(self):
        from skullpt.qtwindows import SettingsWindow

        bench = self._bench()
        window = SettingsWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        self._app.processEvents()
        window._engine_combo.setCurrentIndex(
            window._engine_combo.findData("cycles"))
        window._res_spin.setValue(1024)
        window._samples_spin.setValue(64)
        window._denoise_check.setChecked(True)
        self._app.processEvents()
        saved = settings_mod.load_settings(self.ws)
        self.assertEqual(saved["render_engine"], "cycles")
        self.assertEqual(saved["render_resolution_px"], 1024)
        self.assertEqual(saved["render_samples"], 64)
        self.assertTrue(saved["render_denoise"])

    def test_render_window_shows_the_job_settings(self):
        from skullpt.qtwindows import RenderWindow

        job = _job(self.ws)
        desc = job.load_description()
        desc["render"] = {"engine": "cycles", "resolution_px": 800,
                          "samples": 32, "denoise": True}
        import skullpt.description as _d
        _d.dump(desc, job.desc_path)
        bench = self._bench()
        window = RenderWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        window.set_job(job.dir.name)
        self._app.processEvents()
        self.assertEqual(window._engine_combo.currentData(), "cycles")
        self.assertEqual(window._res_spin.value(), 800)
        self.assertEqual(window._samples_spin.value(), 32)
        self.assertTrue(window._denoise_check.isChecked())
        self.assertTrue(window._save_render_btn.isEnabled())

    def test_render_window_save_writes_the_job_override(self):
        from skullpt.qtwindows import RenderWindow

        job = _job(self.ws)
        bench = self._bench()
        window = RenderWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        window.set_job(job.dir.name)
        self._app.processEvents()
        window._engine_combo.setCurrentIndex(
            window._engine_combo.findData("eevee"))
        window._save_render_settings()
        desc = job.load_description()
        self.assertEqual(desc["render"]["engine"], "eevee")


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtVaryButtonTests(_QtCase):
    """T27: Vary is offered only for kept candidates."""

    def test_vary_enabled_only_when_kept(self):
        from skullpt.qtwindows import _CandidateCard

        batch_dir = self.ws / "images" / "b1"
        batch_dir.mkdir(parents=True)
        (batch_dir / "cand-01.png").write_bytes(TINY_PNG)
        kept = _CandidateCard(batch_dir, {"file": "cand-01.png",
                                          "status": "kept"})
        pending = _CandidateCard(batch_dir, {"file": "cand-01.png",
                                             "status": "pending"})
        self._windows.extend([kept, pending])
        vary_kept = [b for b in kept.findChildren(QtWidgets.QPushButton)
                     if b.text() == "Vary this picture"]
        vary_pending = [b for b in pending.findChildren(QtWidgets.QPushButton)
                        if b.text() == "Vary this picture"]
        self.assertEqual(len(vary_kept), 1)
        self.assertTrue(vary_kept[0].isEnabled())
        self.assertEqual(len(vary_pending), 1)
        self.assertFalse(vary_pending[0].isEnabled())


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtWorkspaceMenuTests(_QtCase):
    """T29: the File and Help menus carry the new actions."""

    def test_menus_carry_workspace_backup_and_update(self):
        bench = self._bench()
        bar = bench.menuBar()
        actions = []
        for menu in bar.findChildren(QtWidgets.QMenu):
            actions.extend(a.text() for a in menu.actions())
        self.assertIn("Open workspace...", actions)
        self.assertIn("Back up workspace...", actions)
        self.assertIn("Check for updates...", actions)


if __name__ == "__main__":
    unittest.main()
