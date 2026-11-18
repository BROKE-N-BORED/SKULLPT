"""SKULLPT v1.10 tests: the P2 items from audit #2 (N22-N50, N52-N54).

Plain JSON errors, style hex validation, library recovery, snapshot
rotation, dead-code removal, sits_on footprints, the gameready alias,
the runs listing, ingest guards, the app log, Qt window fixes, the
real-geometry viewport, skinned LODs, and the probed cloud-mesh
fallback. No real API keys anywhere - every key/token value is a
clearly fake placeholder, and every network call goes to a local
mock server.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skullpt import (  # noqa: E402
    applog, bench, blender_export, cloudmesh, constraints, critic,
    description as desc_mod, gameready, imagegen, ingest, intake, jobs,
    keycheck, library as library_mod, live, ollama as ollama_mod,
    seats, tokenfile)
from skullpt.jobs import Job  # noqa: E402

TINY_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02"
            b"\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")

SHOP = {
    "name": "Test shop", "kind": "ui_component",
    "summary": "a shop panel",
    "parts": [
        {"id": "panel", "type": "panel", "title": "Shop"},
        {"id": "buy", "type": "button", "text": "Buy",
         "variant": "primary", "width_chars": 12},
    ],
    "constraints": [{"part": "buy", "type": "text_fits"}],
    "budget": {"max_preview_px": 800},
    "rubric": ["Title and the button are present"],
    "style": {"surface": "#1c1a17", "accent": "#b7f04a",
              "text": "#f2ede4"},
    "tags": ["test"],
}


def _workspace(tmp: str) -> Path:
    ws = Path(tmp) / "ws"
    (ws / "jobs").mkdir(parents=True)
    (ws / "library").mkdir()
    return ws


def _valid_desc(**overrides) -> dict:
    desc = json.loads(json.dumps(SHOP))
    desc.update(overrides)
    return desc


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_glb(path: Path, interleaved: bool = True) -> None:
    """A tiny GLB: node 0 is a translated+scaled indexed quad
    (2 tris), node 1 is a child with a non-indexed triangle.
    The quad's POSITION is interleaved with NORMAL (byteStride 24)
    when `interleaved` is set, so the reader must honor strides."""
    quad = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
            (1.0, 0.0, 1.0), (0.0, 0.0, 1.0)]
    normals = [(0.0, 1.0, 0.0)] * 4
    if interleaved:
        pos_data = b"".join(struct.pack("<6f", *p, *n)
                            for p, n in zip(quad, normals))
        stride = 24
    else:
        pos_data = b"".join(struct.pack("<3f", *p) for p in quad)
        stride = None
    indices = struct.pack("<6H", 0, 1, 2, 0, 2, 3)
    tri = [(0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (0.0, 0.5, 0.0)]
    tri_data = b"".join(struct.pack("<3f", *p) for p in tri)
    bin_data = pos_data + indices + tri_data
    while len(bin_data) % 4:
        bin_data += b"\x00"
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos_data)}]
    if stride:
        views[0]["byteStride"] = stride
    views.append({"buffer": 0, "byteOffset": len(pos_data),
                  "byteLength": len(indices)})
    views.append({"buffer": 0, "byteOffset": len(pos_data) + len(indices),
                  "byteLength": len(tri_data)})
    gltf = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"mesh": 0, "translation": [0.1, 0.2, 0.3],
             "scale": [2.0, 2.0, 2.0], "children": [1]},
            {"mesh": 1},
        ],
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}, "indices": 2}]},
            {"primitives": [{"attributes": {"POSITION": 3}}]},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4,
             "type": "VEC3", "min": [0, 0, 0], "max": [1, 0, 1]},
            {"bufferView": 0, "byteOffset": 12, "componentType": 5126,
             "count": 4, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 6,
             "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 3,
             "type": "VEC3", "min": [0, 0, 0], "max": [0.5, 0.5, 0]},
        ],
        "bufferViews": views,
        "buffers": [{"byteLength": len(bin_data)}],
    }
    json_bytes = json.dumps(gltf).encode()
    while len(json_bytes) % 4:
        json_bytes += b" "
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    out = struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
    out += struct.pack("<I4s", len(bin_data), b"BIN\x00") + bin_data
    path.write_bytes(out)


def _write_jpeg_past_64k(path: Path, width: int = 37,
                        height: int = 53) -> None:
    """A minimal JPEG whose SOF0 sits past the 64 KB mark: SOI,
    several large APP0 segments of padding (a JPEG segment length
    is 16-bit, so no single segment can pass 64 KB), then SOF0,
    then SOS. A reader that only looks at the first 64 KB never
    sees the dimensions."""
    out = bytearray(b"\xff\xd8")  # SOI
    pad = b"\x00" * (30000 - 2)
    for _ in range(3):
        out += b"\xff\xe0" + struct.pack(">H", len(pad) + 2) + pad
    sof = struct.pack(">BHHB", 8, height, width, 1) + bytes([1, 0x11, 0])
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xda" + struct.pack(">H", 4) + b"\x00\x00"
    path.write_bytes(bytes(out))


# ------------------------------------------------------------ N22


class PlainJsonErrorTests(unittest.TestCase):
    def test_damaged_seats_file_is_a_plain_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "seats.json"
            bad.write_text("{not json")
            with self.assertRaises(ValueError) as cm:
                seats.load_seats(bad)
            self.assertIn("damaged", str(cm.exception))
            self.assertIn("seats.json", str(cm.exception))

    def test_damaged_description_is_a_plain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.description.json"
            bad.write_text("[1, 2,")
            with self.assertRaises(desc_mod.DescriptionError) as cm:
                desc_mod.load(bad)
            self.assertIn("damaged", str(cm.exception))

    def test_damaged_job_json_is_a_plain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            (job.dir / "job.json").write_text("{broken")
            with self.assertRaises(ValueError) as cm:
                Job(job.dir)
            self.assertIn("damaged", str(cm.exception))

    def test_damaged_build_report_does_not_block_filing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            job.approve()
            job.dir.joinpath("build-report.json").write_text("{broken")
            out = job.output_dir
            out.mkdir(exist_ok=True)
            (out / "render-iso.png").write_bytes(TINY_PNG)
            job.transition("awaiting_review")
            result = bench.act(ws, "file", {"job": job.dir.name})
            self.assertTrue(result["ok"], result)
            self.assertTrue((ws / "library" / job.dir.name).is_dir())

    def test_cli_value_error_exits_2_not_traceback(self):
        from skullpt import cli

        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            job.desc_path.write_text("{broken")
            code = cli.main(["show", str(ws), job.dir.name])
            self.assertEqual(code, 2)


# ------------------------------------------------------------ N23


class StyleHexTests(unittest.TestCase):
    def test_good_style_passes(self):
        desc = _valid_desc(style={"surface": "#1c1a17",
                                  "accent": "limegreen",
                                  "text": "#f2ede4"})
        self.assertEqual(desc_mod.validate(desc), [])

    def test_named_surface_is_rejected(self):
        desc = _valid_desc(style={"surface": "papayawhip"})
        problems = desc_mod.validate(desc)
        self.assertTrue(any("surface" in p and "#RRGGBB" in p
                            for p in problems), problems)

    def test_bad_hex_and_unknown_word_are_rejected(self):
        desc = _valid_desc(style={"surface": "#12",
                                  "accent": "notacolor"})
        problems = desc_mod.validate(desc)
        self.assertEqual(len(problems), 2, problems)

    def test_non_string_token_is_rejected(self):
        desc = _valid_desc(style={"accent": 42})
        problems = desc_mod.validate(desc)
        self.assertTrue(any("accent" in p for p in problems), problems)

    def test_non_dict_style_is_rejected(self):
        desc = _valid_desc(style="realistic")
        problems = desc_mod.validate(desc)
        self.assertTrue(any("style must be an object" in p
                            for p in problems), problems)

    def test_unknown_style_keys_still_pass(self):
        desc = _valid_desc(style={"surface": "#1c1a17",
                                  "font": "system-ui, sans-serif"})
        self.assertEqual(desc_mod.validate(desc), [])


# ------------------------------------------------------------ N24


class LibraryRecoveryTests(unittest.TestCase):
    def test_filing_into_a_fresh_library_lists_the_entry_once(self):
        # The rebuild reads the manifests, and file_job writes the
        # manifest before it reads the index - without dedupe the
        # first filing lands twice.
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            job.approve()
            lib = library_mod.Library(ws / "library")
            lib.file_job(job, {"ok": True, "outputs": []})
            self.assertEqual(len(lib.search("")), 1)
            self.assertEqual(len(lib.search("shop")), 1)

    def _seed(self, root: Path) -> None:
        entry = root / "stool"
        entry.mkdir()
        (entry / "description.json").write_text(
            json.dumps(_valid_desc(name="Stool")))
        (entry / "manifest.json").write_text(json.dumps(
            {"name": "stool", "title": "Stool",
             "files": ["description.json"]}))

    def test_corrupt_index_rebuilds_from_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            self._seed(root)
            (root / "index.json").write_text("{corrupt")
            lib = library_mod.Library(root)
            entries = lib.search("")
            self.assertEqual([e["name"] for e in entries], ["stool"])
            # The rebuild heals the index on disk.
            self.assertEqual(
                json.loads((root / "index.json").read_text())[0]["name"],
                "stool")

    def test_missing_index_rebuilds_from_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            self._seed(root)
            lib = library_mod.Library(root)
            self.assertEqual([e["name"] for e in lib.search("")], ["stool"])

    def test_unreadable_manifest_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            self._seed(root)
            bad = root / "broken"
            bad.mkdir()
            (bad / "manifest.json").write_text("{broken")
            lib = library_mod.Library(root)
            self.assertEqual([e["name"] for e in lib.search("")], ["stool"])


# ------------------------------------------------------------ N25


class SnapshotRotationTests(unittest.TestCase):
    def test_snapshots_rotate_to_five_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            for n in range(7):
                desc = job.load_description()
                desc["summary"] = f"version {n}"
                desc_mod.dump(desc, job.desc_path)
                job.snapshot_description("approved.json")
            names = sorted(p.name for p in job.dir.iterdir()
                           if p.name.startswith("approved.json"))
            self.assertEqual(names, ["approved.json", "approved.json.1",
                                     "approved.json.2", "approved.json.3",
                                     "approved.json.4", "approved.json.5"])
            newest = json.loads(
                (job.dir / "approved.json").read_text())["summary"]
            oldest = json.loads(
                (job.dir / "approved.json.5").read_text())["summary"]
            self.assertEqual(newest, "version 6")
            self.assertEqual(oldest, "version 1")


# ------------------------------------------------------------ N26


class DeadCodeTests(unittest.TestCase):
    def test_governor_has_no_texture_check(self):
        text = (ROOT / "skullpt" / "governor.py").read_text()
        self.assertNotIn("texture_px", text)
        self.assertNotIn("texture size", text)

    def test_build_payload_has_no_accent(self):
        payload = blender_export.build_payload(_valid_desc(), Path("/tmp"),
                                               "test")
        self.assertNotIn("accent", payload)

    def test_docstrings_describe_current_behavior(self):
        exporters = (ROOT / "skullpt" / "exporters.py").read_text()
        self.assertIn("DEFAULT_EXPORTERS", exporters)
        settings = (ROOT / "skullpt" / "settings.py").read_text()
        self.assertIn("critic_reference_check", settings)
        backup = (ROOT / "skullpt" / "backup.py").read_text()
        self.assertIn("hf_token.key", backup)
        self.assertIn("gemini_key.key", backup)
        self.assertIn("groq_key.key", backup)


# ------------------------------------------------------------ N27


class SitsOnFootprintTests(unittest.TestCase):
    def _desc(self, part_pos, support_pos, part_size=(10, 10, 10),
              support_size=(20, 20, 5)):
        return {
            "name": "t", "kind": "scene", "summary": "t",
            "parts": [
                {"id": "cup", "type": "cup", "shape": "box",
                 "position_cm": list(part_pos), "size_cm": list(part_size)},
                {"id": "table", "type": "table", "shape": "box",
                 "position_cm": list(support_pos),
                 "size_cm": list(support_size)},
            ],
            "constraints": [{"part": "cup", "on": "table",
                             "type": "sits_on"}],
            "budget": {}, "rubric": [], "style": {}, "tags": [],
        }

    def test_overlapping_footprints_pass(self):
        desc = self._desc((5, 5, 5), (0, 0, 0))
        results = constraints.check_constraints(desc)
        self.assertTrue(results[0]["ok"], results)

    def test_beside_the_support_fails(self):
        desc = self._desc((25, 5, 5), (0, 0, 0))
        results = constraints.check_constraints(desc)
        self.assertFalse(results[0]["ok"])
        self.assertIn("footprints", results[0]["detail"])

    def test_edge_touching_does_not_count(self):
        desc = self._desc((20, 5, 5), (0, 0, 0))
        results = constraints.check_constraints(desc)
        self.assertFalse(results[0]["ok"])

    def test_missing_footprint_passes_the_overlap_check(self):
        # A part with no footprint to measure passes - the height
        # check already ran, and the overlap check must not invent a
        # failure from missing data.
        bare = {"id": "cup", "type": "cup"}
        support = {"id": "table", "type": "table", "shape": "box",
                   "position_cm": [0, 0, 0], "size_cm": [20, 20, 5]}
        self.assertTrue(constraints._footprints_overlap(bare, support))


# ------------------------------------------------------------ N28


class GamereadyAliasTests(unittest.TestCase):
    def _gate(self, budget, total_tris):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            desc = _valid_desc(name="test-shop", game_ready=True,
                               budget=budget)
            (out / "test-shop.stats.json").write_text(json.dumps(
                {"total_tris": total_tris,
                 "parts": {"panel": {"tris": 10, "uv": True}}}))
            report = gameready.run_gate(desc, out, [])
            tri = next(c for c in report["checks"]
                       if c["check"] == "triangle_budget")
            return tri

    def test_max_triangles_alias_is_honored(self):
        tri = self._gate({"max_triangles": 100}, 150)
        self.assertFalse(tri["ok"])
        self.assertIn("150 tris vs budget 100", tri["detail"])

    def test_max_tris_still_wins_when_both_set(self):
        tri = self._gate({"max_tris": 200, "max_triangles": 100}, 150)
        self.assertTrue(tri["ok"], tri)
        self.assertIn("budget 200", tri["detail"])


# ------------------------------------------------------------ N29


class RunsListingTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(bench._RUNS)
        bench._RUNS.clear()

    def tearDown(self):
        bench._RUNS.clear()
        bench._RUNS.update(self._saved)

    def _add(self, run_id, status):
        bench._RUNS[run_id] = {"id": run_id, "kind": "plan",
                               "label": run_id, "status": status,
                               "detail": ""}

    def test_running_and_queued_runs_are_never_truncated(self):
        for n in range(12):
            self._add(f"done-{n:02d}", "done")
        self._add("run-1", "running")
        self._add("run-2", "queued")
        listed = bench._runs()
        ids = [r["id"] for r in listed]
        self.assertIn("run-1", ids)
        self.assertIn("run-2", ids)
        # Only the last 8 finished runs survive, in order.
        finished = [i for i in ids if i.startswith("done-")]
        self.assertEqual(finished, [f"done-{n:02d}" for n in range(4, 12)])
        self.assertEqual(ids[:8], [f"done-{n:02d}" for n in range(4, 12)])
        self.assertEqual(ids[8:], ["run-1", "run-2"])

    def test_cancelled_run_counts_as_finished(self):
        for n in range(9):
            self._add(f"done-{n}", "done")
        self._add("cancel-1", "cancelled")
        listed = bench._runs()
        ids = [r["id"] for r in listed]
        self.assertEqual(len(ids), 8)
        self.assertIn("cancel-1", ids)


# ------------------------------------------------------------ N30


class IngestGuardTests(unittest.TestCase):
    def test_malformed_glb_is_a_plain_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.glb"
            # Valid GLB framing, broken structure: a primitive with
            # no attributes raises KeyError inside the parser.
            gltf = {"asset": {"version": "2.0"},
                    "nodes": [{"mesh": 0}],
                    "meshes": [{"primitives": [{}]}]}
            json_bytes = json.dumps(gltf).encode()
            while len(json_bytes) % 4:
                json_bytes += b" "
            total = 12 + 8 + len(json_bytes)
            data = struct.pack("<4sII", b"glTF", 2, total)
            data += struct.pack("<I4s", len(json_bytes), b"JSON")
            data += json_bytes
            bad.write_bytes(data)
            with self.assertRaises(ValueError) as cm:
                ingest.ingest_glb(bad)
            self.assertIn("broken.glb", str(cm.exception))
            self.assertIn("not a readable GLB", str(cm.exception))

    def test_blend_timeout_is_a_plain_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            blend = Path(tmp) / "big.blend"
            blend.write_bytes(b"BLENDER")
            with mock.patch.object(ingest, "find_blender",
                                   return_value="/bin/true"), \
                    mock.patch.object(ingest.subprocess, "run",
                                      side_effect=subprocess.TimeoutExpired(
                                          ["blender"], 600)):
                with self.assertRaises(RuntimeError) as cm:
                    ingest.ingest_blend(blend)
            self.assertIn("took too long", str(cm.exception))
            self.assertIn("10 minutes", str(cm.exception))

    def test_fbx_timeout_is_a_plain_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            fbx = Path(tmp) / "big.fbx"
            fbx.write_bytes(b"Kaydara FBX Binary")
            with mock.patch.object(ingest, "find_blender",
                                   return_value="/bin/true"), \
                    mock.patch.object(ingest.subprocess, "run",
                                      side_effect=subprocess.TimeoutExpired(
                                          ["blender"], 600)):
                with self.assertRaises(RuntimeError) as cm:
                    ingest.ingest_fbx(fbx)
            self.assertIn("took too long", str(cm.exception))


# ------------------------------------------------------------ N41


class SameFileTests(unittest.TestCase):
    def test_same_content_matches_across_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.urandom(2 * 1024 * 1024 + 123)
            a = Path(tmp) / "a.glb"
            b = Path(tmp) / "b.glb"
            a.write_bytes(data)
            b.write_bytes(data)
            self.assertTrue(ingest._same_file(a, b))

    def test_same_size_different_bytes_do_not_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = bytearray(os.urandom(2 * 1024 * 1024 + 123))
            a = Path(tmp) / "a.glb"
            b = Path(tmp) / "b.glb"
            a.write_bytes(bytes(data))
            data[-1] ^= 0xFF
            b.write_bytes(bytes(data))
            self.assertFalse(ingest._same_file(a, b))

    def test_different_sizes_do_not_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.glb"
            b = Path(tmp) / "b.glb"
            a.write_bytes(b"x" * 100)
            b.write_bytes(b"x" * 101)
            self.assertFalse(ingest._same_file(a, b))


# ------------------------------------------------------------ N42


class CriticCapTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = self._saved

    def test_ninth_and_tenth_renders_are_skipped_and_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n in range(10):
                p = Path(tmp) / f"render-{n:02d}.png"
                p.write_bytes(TINY_PNG)
                paths.append(str(p))
            seen = []

            def fake_gemini_text(seat, prompt, image_png=None,
                                 extra_images=None, workspace=None,
                                 cancel=None):
                seen.append(1 + len(extra_images or []))
                return '{"fixes": []}'

            seat = {"provider": "gemini", "model": "m",
                    "env_key": "GEMINI_API_KEY"}
            with mock.patch.object(live, "gemini_text",
                                   side_effect=fake_gemini_text):
                result = critic.GeminiCritic(seat).critique(
                    dict(SHOP), paths)
            self.assertEqual(seen, [8])
            self.assertEqual(len(result["images"]), 8)
            self.assertEqual(result["skipped_images"],
                             ["render-08.png", "render-09.png"])
            self.assertIn("2 more render(s) not judged", result["note"])

    def test_eight_or_fewer_renders_skip_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for n in range(3):
                p = Path(tmp) / f"render-{n}.png"
                p.write_bytes(TINY_PNG)
                paths.append(str(p))
            seat = {"provider": "gemini", "model": "m",
                    "env_key": "GEMINI_API_KEY"}
            with mock.patch.object(
                    live, "gemini_text",
                    return_value='{"fixes": []}'):
                result = critic.GeminiCritic(seat).critique(
                    dict(SHOP), paths)
            self.assertEqual(result["skipped_images"], [])
            self.assertNotIn("not judged", result["note"])


# ------------------------------------------------------------ N43


class CloudMeshCapTests(unittest.TestCase):
    def test_request_refuses_a_body_past_max_bytes(self):
        body = b"x" * 300

        class FakeResp:
            def __init__(self):
                self._off = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n=-1):
                if n is None or n < 0:
                    chunk, self._off = body[self._off:], len(body)
                    return chunk
                chunk = body[self._off:self._off + n]
                self._off += len(chunk)
                return chunk

        with mock.patch.object(cloudmesh.urllib.request, "urlopen",
                               return_value=FakeResp()):
            with self.assertRaises(cloudmesh.CloudMeshError) as cm:
                cloudmesh._request("http://127.0.0.1/x", "tok",
                                   max_bytes=100)
            self.assertIn("refusing", str(cm.exception))

    def test_request_within_the_cap_reads_fine(self):
        body = b"y" * 50

        class FakeResp:
            def __init__(self):
                self._off = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n=-1):
                if n is None or n < 0:
                    chunk, self._off = body[self._off:], len(body)
                    return chunk
                chunk = body[self._off:self._off + n]
                self._off += len(chunk)
                return chunk

        with mock.patch.object(cloudmesh.urllib.request, "urlopen",
                               return_value=FakeResp()):
            out = cloudmesh._request("http://127.0.0.1/x", "tok",
                                     max_bytes=100)
        self.assertEqual(out, body)

    def test_validate_photo_names_all_three_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "notes.txt"
            bad.write_text("not a photo")
            with self.assertRaises(cloudmesh.CloudMeshError) as cm:
                cloudmesh.validate_photo(bad)
            self.assertIn("PNG, JPEG, or GIF", str(cm.exception))

    def test_no_stale_no_account_note(self):
        text = (ROOT / "skullpt" / "cloudmesh.py").read_text()
        self.assertNotIn("2 minutes with no account", text)


# ------------------------------------------------------------ N48


class ImageSizeTests(unittest.TestCase):
    def test_new_batch_records_width_and_height(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            batch, _ = imagegen.new_batch(ws, "a stool", width=768,
                                          height=512)
            self.assertEqual(batch["width"], 768)
            self.assertEqual(batch["height"], 512)

    def test_new_batch_defaults_to_square_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            batch, _ = imagegen.new_batch(ws, "a stool", size=640)
            self.assertEqual((batch["width"], batch["height"]),
                             (640, 640))

    def test_bad_dimensions_are_plain_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            for bad in (0, -10, "wide", True):
                with self.assertRaises(imagegen.ImageGenError):
                    imagegen.new_batch(ws, "a stool", width=bad)

    def test_generate_passes_width_and_height_to_the_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            batch, bdir = imagegen.new_batch(ws, "a stool", width=768,
                                             height=512, count=1)
            seen = {}

            def fake_run(cmd, timeout=None, cancel=None, on_line=None):
                seen["cmd"] = list(cmd)
                out = Path(cmd[cmd.index("-o") + 1])
                out.write_bytes(b"fake-png")
                return 0, ""

            with mock.patch.object(imagegen, "find_engine",
                                   return_value=Path("/bin/sd-cli")), \
                    mock.patch.object(imagegen, "find_model",
                                      return_value=Path("/m/model.gguf")), \
                    mock.patch.object(imagegen.governor, "free_ram_mb",
                                      return_value=8192), \
                    mock.patch("skullpt.subproc.run_cancellable",
                               side_effect=fake_run):
                imagegen.generate(ws, bdir, count=1)
            cmd = seen["cmd"]
            self.assertEqual(cmd[cmd.index("-W") + 1], "768")
            self.assertEqual(cmd[cmd.index("-H") + 1], "512")

    def test_generate_falls_back_to_size_for_old_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            batch, bdir = imagegen.new_batch(ws, "a stool", size=640,
                                             count=1)
            record = imagegen.load_batch(bdir)
            del record["width"]
            del record["height"]
            imagegen.save_batch(bdir, record)
            seen = {}

            def fake_run(cmd, timeout=None, cancel=None, on_line=None):
                seen["cmd"] = list(cmd)
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-png")
                return 0, ""

            with mock.patch.object(imagegen, "find_engine",
                                   return_value=Path("/bin/sd-cli")), \
                    mock.patch.object(imagegen, "find_model",
                                      return_value=Path("/m/model.gguf")), \
                    mock.patch.object(imagegen.governor, "free_ram_mb",
                                      return_value=8192), \
                    mock.patch("skullpt.subproc.run_cancellable",
                               side_effect=fake_run):
                imagegen.generate(ws, bdir, count=1)
            cmd = seen["cmd"]
            self.assertEqual(cmd[cmd.index("-W") + 1], "640")
            self.assertEqual(cmd[cmd.index("-H") + 1], "640")

    def test_vary_passes_source_dimensions_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            batch, bdir = imagegen.new_batch(ws, "a stool", width=768,
                                             height=512)
            keeper = bdir / "cand-01.png"
            keeper.write_bytes(TINY_PNG)
            record = imagegen.load_batch(bdir)
            record["candidates"] = [{"file": "cand-01.png", "seed": 1,
                                     "status": "kept"}]
            imagegen.save_batch(bdir, record)
            seen = {}

            def fake_generate(workspace, new_dir, **kw):
                seen["dir"] = Path(new_dir)
                return {}

            with mock.patch.object(imagegen, "generate",
                                   side_effect=fake_generate):
                imagegen.vary(ws, bdir, "cand-01.png")
            new_batch = imagegen.load_batch(seen["dir"])
            self.assertEqual((new_batch["width"], new_batch["height"]),
                             (768, 512))

    def test_cli_accepts_width_and_height(self):
        from skullpt import cli

        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            with mock.patch.object(imagegen, "new_batch",
                                   return_value=({}, Path(tmp))) as nb, \
                    mock.patch.object(imagegen, "generate",
                                      return_value={"candidates": []}):
                code = cli.main(["images", str(ws), "a stool",
                                 "--width", "768", "--height", "512"])
            self.assertEqual(code, 0)
            _, kwargs = nb.call_args
            self.assertEqual((kwargs.get("width"), kwargs.get("height")),
                             (768, 512))


# ------------------------------------------------------------ N49


class IntakeRouteTests(unittest.TestCase):
    def test_no_sf3d_or_triposr_route(self):
        text = json.dumps(intake.ROUTES)
        self.assertNotIn("SF3D", text)
        self.assertNotIn("TripoSR", text)
        self.assertNotIn("sf3d", text.lower())

    def test_jpeg_dimensions_found_past_64k(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.jpg"
            _write_jpeg_past_64k(path, width=37, height=53)
            width, height, fmt = intake.image_size(path)
            self.assertEqual((width, height, fmt), (37, 53, "jpeg"))


# ------------------------------------------------------------ N50


class CancelTests(unittest.TestCase):
    def test_seat_cancelled_is_not_a_seat_call_failed(self):
        self.assertFalse(issubclass(live.SeatCancelled,
                                    live.SeatCallFailed))

    def test_cancel_mid_call_raises_quickly(self):
        port = _free_port()

        class Slow(BaseHTTPRequestHandler):
            def do_POST(self):
                time.sleep(5)
                body = json.dumps(
                    {"candidates": [{"content": {"parts": [
                        {"text": "hi"}]}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            seat = {"provider": "gemini", "model": "m", "env_key": None}
            cancel = threading.Event()
            threading.Timer(0.3, cancel.set).start()
            with mock.patch.dict(os.environ, {
                    "DESIGNSHELL_GEMINI_URL":
                    f"http://127.0.0.1:{port}/x"}):
                start = time.monotonic()
                with self.assertRaises(live.SeatCancelled):
                    live.gemini_text(seat, "hello", cancel=cancel)
                self.assertLess(time.monotonic() - start, 2.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_cancelled_run_is_cancelled_not_error(self):
        def _raise(cancel, report):
            raise live.SeatCancelled("the seat call was cancelled")

        run_id = bench._start_run("plan", "cancel probe", _raise)
        deadline = time.monotonic() + 5
        status = None
        while time.monotonic() < deadline:
            runs = [r for r in bench._runs() if r["id"] == run_id]
            if runs and runs[0]["status"] in ("cancelled", "error",
                                              "done"):
                status = runs[0]["status"]
                break
            time.sleep(0.05)
        self.assertEqual(status, "cancelled")

    def test_critics_accept_cancel(self):
        offline = critic.OfflineCritic()
        result = offline.critique(dict(SHOP), [],
                                  cancel=threading.Event())
        self.assertEqual(result["critic"], "offline")
        seat = {"provider": "gemini", "model": "m",
                "env_key": "GEMINI_API_KEY"}
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "r.png"
            png.write_bytes(TINY_PNG)
            with mock.patch.dict(os.environ,
                                 {"GEMINI_API_KEY": "test-key"}), \
                    mock.patch.object(
                        live, "gemini_text",
                        return_value='{"fixes": []}') as fake:
                critic.GeminiCritic(seat).critique(
                    dict(SHOP), [str(png)], cancel=threading.Event())
            self.assertIn("cancel", fake.call_args.kwargs)


# ------------------------------------------------------------ N39


class ApplogTests(unittest.TestCase):
    def test_setup_writes_a_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            applog.setup(ws)
            applog.get_logger().info("hello log")
            log = ws / "skullpt.log"
            self.assertTrue(log.exists())
            self.assertIn("hello log", log.read_text())

    def test_tail_returns_recent_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            applog.setup(ws)
            applog.get_logger().info("tail marker one")
            tail = applog.tail(30)
            self.assertTrue(any("tail marker one" in line for line in tail))

    def test_setup_is_idempotent_per_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            applog.setup(ws)
            before = len(applog.get_logger().handlers)
            applog.setup(ws)
            self.assertEqual(len(applog.get_logger().handlers), before)

    def test_setup_never_raises_on_a_bad_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = Path(tmp) / "file-not-dir"
            not_a_dir.write_text("x")
            applog.setup(not_a_dir)  # must not raise
            applog.get_logger().info("still fine")

    def test_debug_report_carries_the_log_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            applog.setup(ws)
            applog.get_logger().info("debug marker two")
            report = bench.debug_report(ws)
            self.assertIn("recent log", report)
            self.assertIn("debug marker two", report)


# ------------------------------------------------------------ N40


class NoHardcodedPathsTests(unittest.TestCase):
    def test_no_home_xprnc_in_bench(self):
        text = (ROOT / "skullpt" / "bench.py").read_text()
        self.assertNotIn("/home/xprnc", text)

    def test_no_home_xprnc_in_readme(self):
        text = (ROOT / "README.md").read_text()
        self.assertNotIn("/home/xprnc", text)


# ------------------------------------------------------------ N32


class KeycheckTokenTests(unittest.TestCase):
    def _server(self, good_token):
        outer_good = good_token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                auth = self.headers.get("Authorization", "")
                if auth == f"Bearer {outer_good}":
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                else:
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_good_token_validates(self):
        server = self._server("hf_good-token-not-real")
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/whoami"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_HF_VALIDATE_URL": url}):
                ok, _ = keycheck.validate_hf_token("hf_good-token-not-real")
            self.assertTrue(ok)
        finally:
            server.shutdown()
            server.server_close()

    def test_bad_token_says_token_not_key(self):
        server = self._server("hf_good-token-not-real")
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/whoami"
            with mock.patch.dict(os.environ,
                                 {"DESIGNSHELL_HF_VALIDATE_URL": url}):
                ok, msg = keycheck.validate_hf_token("hf_wrong")
            self.assertFalse(ok)
            self.assertIn("token", msg)
            self.assertNotIn("key", msg.lower().replace("token", ""))
        finally:
            server.shutdown()
            server.server_close()


# ------------------------------------------------------------ N35


class PullProgressTests(unittest.TestCase):
    def _stub(self, tmp, script_text):
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        script = bin_dir / "ollama"
        script.write_text(script_text)
        script.chmod(0o755)
        return bin_dir

    def test_progress_lines_arrive_split_on_carriage_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self._stub(
                tmp, "#!/bin/sh\nprintf "
                "\"pulling manifest\\rdownloading 42%%\\r\"; "
                "sleep 0.2; echo \"pull complete\"\n")
            lines = []
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
            with mock.patch.dict(os.environ, env, clear=True):
                ok, msg = ollama_mod.pull_model(on_progress=lines.append)
            self.assertTrue(ok, msg)
            self.assertEqual(lines, ["pulling manifest",
                                     "downloading 42%", "pull complete"])

    def test_silent_pull_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = self._stub(tmp, "#!/bin/sh\nsleep 30\n")
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
            with mock.patch.dict(os.environ, env, clear=True):
                start = time.monotonic()
                ok, msg = ollama_mod.pull_model(timeout=2)
                elapsed = time.monotonic() - start
            self.assertFalse(ok)
            self.assertIn("timed out", msg)
            self.assertLess(elapsed, 10)


# ------------------------------------------------------------ N52


class GlbNodeTrianglesTests(unittest.TestCase):
    def test_indexed_quad_with_scale_translation_and_stride(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.glb"
            _write_glb(path)
            tris = ingest.glb_node_triangles(path)
            self.assertEqual(len(tris[0]), 2)
            first = tris[0][0]
            # Vertex (0,0,0), scale 2, translation (0.1, 0.2, 0.3):
            # shell (10, -30, 20). Vertex (1,0,0) -> (210, -30, 20).
            # Float32 storage in the GLB means near-equality, not
            # exact equality.
            expected = [(10.0, -30.0, 20.0), (210.0, -30.0, 20.0),
                        (210.0, -230.0, 20.0)]
            for got, want in zip(first, expected):
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, places=5)

    def test_child_node_inherits_parent_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.glb"
            _write_glb(path)
            tris = ingest.glb_node_triangles(path)
            self.assertEqual(len(tris[1]), 1)
            tri = tris[1][0]
            self.assertEqual(tri[0], (10.0, -30.0, 20.0))
            self.assertEqual(tri[1], (60.0, -30.0, 20.0))
            self.assertEqual(tri[2], (10.0, -30.0, 70.0))

    def test_tightly_packed_positions_read_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tight.glb"
            _write_glb(path, interleaved=False)
            tris = ingest.glb_node_triangles(path)
            self.assertEqual(len(tris[0]), 2)

    def test_sampling_cap_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.glb"
            _write_glb(path)
            with mock.patch.object(ingest, "MAX_VIEWPORT_TRIANGLES", 1):
                tris = ingest.glb_node_triangles(path)
            self.assertEqual(len(tris[0]), 1)

    def test_non_glb_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.glb"
            path.write_bytes(b"not a glb at all")
            with self.assertRaises(ValueError):
                ingest.glb_node_triangles(path)


class ViewportRealGeometryTests(unittest.TestCase):
    def test_imported_parts_draw_real_triangles(self):
        from skullpt import qtbench

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            glb = Path(tmp) / "test.glb"
            _write_glb(glb)
            desc = ingest.ingest_glb(glb, workspace=ws)
            scene = qtbench.scene_meshes(desc, mesh_dirs=[ws])
            counts = {m["id"]: len(m["tris"]) for m in scene["meshes"]}
            self.assertEqual(counts["part_0"], 2)
            self.assertEqual(counts["part_1"], 1)

    def test_without_mesh_dirs_parts_still_draw_boxes(self):
        from skullpt import qtbench

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            glb = Path(tmp) / "test.glb"
            _write_glb(glb)
            desc = ingest.ingest_glb(glb, workspace=ws)
            scene = qtbench.scene_meshes(desc)
            counts = [len(m["tris"]) for m in scene["meshes"]]
            self.assertEqual(counts, [12, 12])

    def test_missing_mesh_file_falls_back_to_boxes(self):
        from skullpt import qtbench

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            glb = Path(tmp) / "test.glb"
            _write_glb(glb)
            desc = ingest.ingest_glb(glb, workspace=ws)
            (ws / "meshes" / "test.glb").unlink()
            scene = qtbench.scene_meshes(desc, mesh_dirs=[ws])
            counts = [len(m["tris"]) for m in scene["meshes"]]
            self.assertEqual(counts, [12, 12])

    def test_cache_rebuilds_when_the_glb_changes(self):
        from skullpt import qtbench

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            glb = Path(tmp) / "test.glb"
            _write_glb(glb)
            desc = ingest.ingest_glb(glb, workspace=ws)
            qtbench.clear_mesh_cache()
            first = qtbench.cached_scene_meshes(desc, mesh_dirs=[ws])
            time.sleep(0.01)
            shutil.copy(glb, ws / "meshes" / "test.glb")
            second = qtbench.cached_scene_meshes(desc, mesh_dirs=[ws])
            self.assertIsNot(second, first)
            # And the same file twice is the same cached scene.
            third = qtbench.cached_scene_meshes(desc, mesh_dirs=[ws])
            self.assertIs(third, second)


# ------------------------------------------------------------ N53


class LodTemplateTests(unittest.TestCase):
    def test_lod_export_hides_bevels_and_evaluates_decimate(self):
        # The exporter special-cases armatures, so export_apply=True
        # keeps skins (verified on a real MPFB human); the v1.10 fix
        # is hiding the bevels, which used to bake into the LODs and
        # inflate them past the main GLB.
        template = blender_export._BUILD_TEMPLATE
        self.assertIn('mod.type == "BEVEL"', template)
        self.assertIn("mod.show_viewport = False", template)
        self.assertIn('modifiers.new("lod", "DECIMATE")', template)
        self.assertIn('export_format="GLB", export_apply=True', template)

    def test_bevels_and_decimate_are_restored_after_the_export(self):
        template = blender_export._BUILD_TEMPLATE
        self.assertIn("mod.show_viewport = viewport", template)
        self.assertIn("mod.show_render = render", template)
        self.assertIn("o.modifiers.remove(mod)", template)

    def test_no_duplicate_apply_path_remains(self):
        # The first v1.10 attempt duplicated meshes and applied
        # DECIMATE directly; real MPFB humans have shape keys and
        # Blender refuses to apply any modifier to a mesh with
        # shape keys, so that path broke every character build.
        # v1.13 (M14) adds the ONE apply the template is allowed
        # to have: the displacement subdivision, on built parts
        # only (the part loop never touches the human, and built
        # parts have no shape keys). The DECIMATE apply stays
        # forbidden - this test pins the apply count at exactly
        # one, so any future apply is a deliberate decision.
        template = blender_export._BUILD_TEMPLATE
        self.assertNotIn("modifier_move_to_index", template)
        self.assertNotIn("export_apply=False", template)
        applies = [line.strip() for line in template.splitlines()
                   if "modifier_apply" in line]
        self.assertEqual(
            applies,
            ["bpy.ops.object.modifier_apply(modifier=subdiv.name)"])


@unittest.skipUnless(
    blender_export.find_blender()
    and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class CharacterLodSkinTests(unittest.TestCase):
    """A real character through real Blender + MPFB2: the LODs keep
    their skins and carry fewer triangles than the main GLB."""

    def test_character_lods_are_skinned_and_smaller(self):
        from skullpt import blender_character as char_mod
        from skullpt.builder import build_job

        status = char_mod.find_mpfb(
            blender_exe=blender_export.find_blender())
        if not (status["installed"] and status["assets"]):
            self.skipTest("MPFB2 or its assets are not installed "
                          "for this Blender")
        desc = char_mod.default_character_description("Test Character")
        desc["game_ready"] = True
        desc["render"] = {"engine": "eevee", "resolution_px": 128,
                          "samples": 1, "denoise": False}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", desc)
            job.approve()
            with mock.patch("skullpt.governor.free_ram_mb",
                            return_value=8192):
                report = build_job(job, ws)
            self.assertTrue(report["ok"], report)
            slug = job.dir.name
            out = job.output_dir

            def glb_info(name):
                data = (out / name).read_bytes()
                chunk_len, _ = struct.unpack_from("<I4s", data, 12)
                gj = json.loads(data[20:20 + chunk_len])
                tris = 0
                for m in gj.get("meshes", []):
                    for p in m.get("primitives", []):
                        acc = gj["accessors"][p["indices"]] \
                            if "indices" in p else \
                            gj["accessors"][p["attributes"]["POSITION"]]
                        tris += acc["count"] // 3
                return len(gj.get("skins", [])), tris

            _, main_tris = glb_info(f"{slug}.glb")
            for suffix in ("lod1", "lod2"):
                skins, tris = glb_info(f"{slug}_{suffix}.glb")
                self.assertGreater(skins, 0,
                                   f"{suffix} has no skin")
                self.assertLess(tris, main_tris,
                                f"{suffix} is not smaller than the main GLB")


# ------------------------------------------------------------ N54


class FallbackTests(unittest.TestCase):
    def test_default_fallback_is_the_probed_clone(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DESIGNSHELL_CLOUDMESH_FALLBACK_URL", None)
            self.assertEqual(cloudmesh.fallback_url(),
                             "https://cpuai-trellis-2.hf.space")

    def test_none_disables_the_fallback(self):
        with mock.patch.dict(
                os.environ,
                {"DESIGNSHELL_CLOUDMESH_FALLBACK_URL": "none"}):
            self.assertIsNone(cloudmesh.fallback_url())

    def test_override_wins_over_the_default(self):
        with mock.patch.dict(
                os.environ,
                {"DESIGNSHELL_CLOUDMESH_FALLBACK_URL":
                 "https://example.com/space/"}):
            self.assertEqual(cloudmesh.fallback_url(),
                             "https://example.com/space")

    def test_disclosure_names_the_fallback(self):
        self.assertIn("cpuai/TRELLIS-2", cloudmesh.DISCLOSURE)


# ------------------------------------------------------------ N44


class InstallerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.install = (ROOT / "install.sh").read_text()

    def test_ollama_script_is_pinned_and_verified(self):
        self.assertIn("ollama/v0.40.16/scripts/install.sh", self.install)
        self.assertIn(
            "25f64b810b947145095956533e1bdf56eacea2673c55a7e586be4515fc882c9f",
            self.install)
        self.assertIn("download_verified", self.install)

    def test_ollama_comment_no_longer_claims_self_verification(self):
        self.assertNotIn("verifies it against the release",
                         self.install)

    def test_no_cancel_is_gone_and_cancel_is_handled(self):
        self.assertNotIn("--no-cancel", self.install)
        self.assertIn("Install cancelled", self.install)
        self.assertIn("PIPESTATUS", self.install)

    def test_nested_zips_are_flattened(self):
        self.assertIn("flatten_one_level", self.install)
        flat = self.install.index("flatten_one_level")
        check = self.install.index("__init__.py", flat)
        self.assertLess(flat, check)

    def test_blender_swaps_only_after_verification(self):
        self.assertIn("/opt/blender.new", self.install)
        self.assertIn("/opt/blender.old", self.install)
        self.assertIn("--version", self.install)

    def test_no_sudo_anywhere(self):
        self.assertNotIn("sudo", self.install)


# ------------------------------------------------------------ N45 + N46


class ReadmeTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "README.md").read_text()

    def test_no_stale_photo_conversion_line(self):
        self.assertNotIn("does not itself convert photos to meshes",
                         self.text)

    def test_no_stale_sf3d_sentence(self):
        self.assertNotIn("SF3D", self.text)
        self.assertNotIn("TripoSR", self.text)

    def test_no_stale_character_studio_bullet(self):
        self.assertNotIn("No Character studio yet", self.text)

    def test_bench_section_describes_multi_window_bench(self):
        self.assertIn("nine windows", self.text)

    def test_module_list_covers_current_modules(self):
        for module in ("tokenfile", "keycheck", "ollama", "update",
                       "subproc", "backup", "blender_character",
                       "qtwindows", "qtdesign", "applog"):
            self.assertIn(module, self.text)

    def test_test_count_is_current(self):
        self.assertNotIn("108 fast tests", self.text)


class DesignDocTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "DESIGN.md").read_text()

    def test_header_names_v112(self):
        self.assertIn("v1.15", self.text.splitlines()[0]
                      + "\n" + "\n".join(self.text.splitlines()[1:60]))

    def test_nine_windows(self):
        self.assertIn("nine windows", self.text)
        self.assertNotIn("five windows", self.text)

    def test_amber_is_a_fixed_token(self):
        self.assertIn("#f5a623", self.text)
        self.assertNotIn("amber sampled from the icon", self.text)


# ------------------------------------------------------------ N47


class HelpTextTests(unittest.TestCase):
    def test_unreal_help_names_the_glb_not_fbx(self):
        text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        start = text.index("<h3>Unreal</h3>")
        paragraph = text[start:start + 400]
        self.assertIn(".glb", paragraph)
        self.assertNotIn(".fbx", paragraph)


# ------------------------------------------------------------ version


class VersionTests(unittest.TestCase):
    def test_version_is_111_everywhere(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.15")
        self.assertIn("v1.15", (ROOT / "README.md").read_text())
        self.assertIn("v1.15", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.15", (ROOT / "DESIGN.md").read_text())

    def test_changelog_has_a_v110_section(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        head = text.split("## v1.9")[0]
        self.assertIn("v1.10", head)
        self.assertIn("N52", head)
        self.assertIn("N53", head)
        self.assertIn("N54", head)


# ------------------------------------------------------------ Qt tests
# Skipped without PySide6; the widget tests need a display (Xvfb).

try:
    from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402
    from skullpt import qtbench, qtwindows  # noqa: E402

    HAS_QT = True
except ImportError:
    HAS_QT = False


class _BenchStub:
    def __init__(self, workspace):
        self.workspace = workspace
        self.refreshed = 0
        self.actions = []

    def refresh(self):
        self.refreshed += 1

    def do_action(self, action, payload=None):
        self.actions.append((action, payload or {}))
        if action == "new-character":
            return {"job": "new-character-job"}
        return {"ok": True}


def _wait_for(condition, timeout_ms=5000):
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while not condition():
        if deadline.hasExpired():
            return False
        QtTest.QTest.qWait(50)
    return True


def _mock_token_server(good_token):
    outer_good = good_token

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") == f"Bearer {outer_good}":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            else:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtTokenValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_token_is_saved_after_validation(self):
        window = qtwindows.SettingsWindow(self.stub, self.ws)
        server = _mock_token_server("hf_good-token-not-real")
        try:
            url = (f"http://127.0.0.1:{server.server_address[1]}"
                   "/whoami")
            with mock.patch.dict(
                    os.environ, {"DESIGNSHELL_HF_VALIDATE_URL": url}):
                window._token_field.setText("hf_good-token-not-real")
                window._save_token()
                saved = _wait_for(lambda: tokenfile.has_token(self.ws))
            self.assertTrue(saved, "the validated token was never saved")
            self.assertEqual(tokenfile.load_token(self.ws),
                             "hf_good-token-not-real")
        finally:
            server.shutdown()
            server.server_close()
            window.close()

    def test_invalid_token_is_never_saved(self):
        window = qtwindows.SettingsWindow(self.stub, self.ws)
        server = _mock_token_server("hf_good-token-not-real")
        try:
            url = (f"http://127.0.0.1:{server.server_address[1]}"
                   "/whoami")
            with mock.patch.dict(
                    os.environ, {"DESIGNSHELL_HF_VALIDATE_URL": url}):
                window._token_field.setText("hf_wrong")
                window._save_token()
                shown = _wait_for(
                    lambda: "did not work" in window._token_status.text())
            self.assertTrue(shown, "no failure message appeared")
            self.assertFalse(tokenfile.has_token(self.ws))
        finally:
            server.shutdown()
            server.server_close()
            window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtBackupBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_backup_runs_off_the_ui_thread(self):
        window = qtwindows.SettingsWindow(self.stub, self.ws)
        dest = str(Path(self.tmp.name) / "backup.zip")
        with mock.patch.object(
                QtWidgets.QFileDialog, "getSaveFileName",
                return_value=(dest, "")), \
                mock.patch.object(qtwindows.Toast, "show_toast"):
            window._backup_workspace()
            landed = _wait_for(lambda: Path(dest).exists())
        self.assertTrue(landed, "the background backup never landed")
        # The file lands in the worker thread; the done signal that
        # re-enables the button arrives on the UI thread just after.
        reenabled = _wait_for(lambda: window._backup_btn.isEnabled())
        self.assertTrue(reenabled, "the button stayed disabled")
        window.close()

    def test_bench_backup_starts_a_background_run(self):
        window = qtbench.BenchWindow(self.ws)
        dest = str(Path(self.tmp.name) / "bench-backup.zip")
        with mock.patch.object(
                QtWidgets.QFileDialog, "getSaveFileName",
                return_value=(dest, "")):
            window._backup_workspace()
            started = _wait_for(
                lambda: any(r["kind"] == "backup"
                            for r in bench._runs()))
        self.assertTrue(started, "no backup run started")
        landed = _wait_for(lambda: Path(dest).exists())
        self.assertTrue(landed, "the background backup never landed")
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtInstallerDoneTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_zero_refreshes_and_toasts(self):
        window = qtwindows.SetupWindow(self.stub, self.ws)
        with mock.patch.object(qtwindows.Toast, "show_toast") as toast:
            window._on_installer_done(0)
        self.assertTrue(any("Installer finished" in str(c)
                            for c in toast.call_args_list))
        window.close()

    def test_exit_three_is_a_cancel_not_a_failure(self):
        window = qtwindows.SetupWindow(self.stub, self.ws)
        with mock.patch.object(qtwindows.Toast, "show_toast") as toast, \
                mock.patch.object(QtWidgets.QMessageBox, "warning") as warn:
            window._on_installer_done(3)
        self.assertTrue(any("cancelled" in str(c).lower()
                            for c in toast.call_args_list))
        warn.assert_not_called()
        window.close()

    def test_nonzero_exit_warns(self):
        window = qtwindows.SetupWindow(self.stub, self.ws)
        with mock.patch.object(QtWidgets.QMessageBox,
                               "warning") as warn:
            window._on_installer_done(1)
        warn.assert_called_once()
        self.assertIn("Installer stopped", str(warn.call_args))
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtCharacterFormTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        (self.ws / "library").mkdir()
        self.stub = _BenchStub(self.ws)
        self._no_studio = {
            "installed": False, "assets": False, "mpfb_dir": None,
            "skins": []}

    def tearDown(self):
        self.tmp.cleanup()

    def _window_with_job(self, state="awaiting_approval"):
        from skullpt import blender_character as char_mod

        desc = char_mod.default_character_description("Test Character")
        job = Job.create(self.ws / "jobs", desc)
        if state != "awaiting_approval":
            job.transition(state)
        with mock.patch.object(char_mod, "find_mpfb",
                               return_value=self._no_studio):
            window = qtwindows.CharacterWindow(self.stub, self.ws)
            window.set_job(job.dir.name)
        return window, job

    def test_form_and_save_work_without_the_studio(self):
        window, _job = self._window_with_job()
        self.assertTrue(window._form_holder.isEnabled())
        self.assertTrue(window._save_btn.isEnabled())
        self.assertFalse(window._skin_combo.isEnabled())
        window.close()

    def test_gate_button_allows_approve_but_not_build(self):
        window, _job = self._window_with_job("awaiting_approval")
        self.assertEqual(window._gate_btn.text(), "Approve")
        self.assertTrue(window._gate_btn.isEnabled())
        window.close()
        window, _job = self._window_with_job("approved")
        self.assertEqual(window._gate_btn.text(), "Build")
        self.assertFalse(window._gate_btn.isEnabled())
        window.close()

    def test_save_keeps_the_job_skin_when_no_skins_exist(self):
        from skullpt import blender_character as char_mod

        window, job = self._window_with_job()
        existing = job.load_description()["character"]["skin"]
        self.assertIsNotNone(existing)
        with mock.patch.object(qtwindows.Toast, "show_toast"):
            window._save_character()
        saved = [p for a, p in self.stub.actions
                 if a == "edit-character"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["character"]["skin"], existing)
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtSaveCameraTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        (self.ws / "library").mkdir()
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_camera_keeps_the_view(self):
        job = Job.create(self.ws / "jobs", _valid_desc())
        window = qtwindows.RenderWindow(self.stub, self.ws)
        window.set_job(job.dir.name)
        viewport = window._viewport
        viewport.azimuth = 42.0
        viewport.elevation = 11.0
        viewport.distance = 333.0
        before = (viewport.target, viewport.distance,
                  viewport.azimuth, viewport.elevation)
        with mock.patch.object(QtWidgets.QInputDialog, "getText",
                               return_value=("Cam 1", True)), \
                mock.patch.object(qtwindows.Toast, "show_toast"):
            window._save_camera()
        after = (viewport.target, viewport.distance,
                 viewport.azimuth, viewport.elevation)
        self.assertEqual(before, after)
        desc = job.load_description()
        self.assertEqual(desc["cameras"][0]["name"], "Cam 1")
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtDecideToastTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_keep_toast_says_kept(self):
        _batch, bdir = imagegen.new_batch(self.ws, "a stool")
        keeper = bdir / "cand-01.png"
        keeper.write_bytes(TINY_PNG)
        record = imagegen.load_batch(bdir)
        record["candidates"] = [{"file": "cand-01.png", "seed": 1,
                                 "status": "pending"}]
        imagegen.save_batch(bdir, record)
        window = qtwindows.ImagesWindow(self.stub, self.ws)
        window._batch_dir = bdir
        with mock.patch.object(qtwindows.Toast,
                               "show_toast") as toast:
            window._decide("cand-01.png", "keep")
        messages = [str(c) for c in toast.call_args_list]
        self.assertTrue(any("cand-01.png kept." in m for m in messages),
                        messages)
        self.assertFalse(any("keeped" in m for m in messages))
        window.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtSlotGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "ws"
        (self.ws / "jobs").mkdir(parents=True)
        (self.ws / "library").mkdir()
        self.stub = _BenchStub(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def test_open_as_job_warns_on_an_invalid_description(self):
        entry = self.ws / "library" / "bad-one"
        entry.mkdir()
        bad = _valid_desc(style="not an object")
        (entry / "description.json").write_text(json.dumps(bad))
        window = qtwindows.LibraryWindow(self.stub, self.ws)
        window._selected_slug = "bad-one"
        with mock.patch.object(QtWidgets.QMessageBox,
                               "warning") as warn:
            window._open_as_job()  # must not raise
        warn.assert_called_once()
        self.assertEqual(list((self.ws / "jobs").iterdir()), [])
        window.close()

    def test_debug_save_warns_on_an_unwritable_path(self):
        dialog = qtwindows.DebugReportDialog(self.ws)
        with mock.patch.object(
                QtWidgets.QFileDialog, "getSaveFileName",
                return_value=(str(Path(self.tmp.name) / "nope"
                                  / "report.txt"), "")), \
                mock.patch.object(QtWidgets.QMessageBox,
                                  "warning") as warn:
            dialog._save()  # must not raise
        warn.assert_called_once()
        dialog.close()


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class QtProgressEmitterTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() \
            or QtWidgets.QApplication([])

    def test_lines_arrive_on_the_ui_thread(self):
        seen = []
        emitter = qtwindows._ProgressEmitter()
        emitter.line.connect(seen.append)
        thread = threading.Thread(target=emitter.line.emit,
                                  args=("pulling 42%",))
        thread.start()
        arrived = _wait_for(lambda: seen == ["pulling 42%"])
        thread.join(timeout=5)
        self.assertTrue(arrived, seen)


@unittest.skipUnless(HAS_QT, "PySide6 not installed")
class SkippedImagesReportTests(unittest.TestCase):
    def test_build_report_names_skipped_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _valid_desc())
            report = {
                "ok": True, "outputs": ["render-iso.png"],
                "critique": {"note": "looks fine",
                             "images": ["render-iso.png"],
                             "skipped_images": ["render-top.png",
                                                "render-side.png"],
                             "fixes": []},
            }
            (job.dir / "build-report.json").write_text(
                json.dumps(report))
            text = qtwindows._format_build_report(ws, job.dir.name)
            self.assertIn("renders not judged (2): render-top.png, "
                          "render-side.png", text)
