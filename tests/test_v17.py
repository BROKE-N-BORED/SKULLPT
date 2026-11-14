"""SKULLPT v1.7 tests: T26 Character Studio (MPFB2 route).

Covers: the character description vocabulary + validation + budget
estimate, the blender_character exporter (script generation, MPFB
detection, pre-flight failures), the T3 lift for character jobs,
the job actions (new-character, edit-character), the viewport
mannequin, the Character Studio window, the installer option, and
the real-Blender end-to-end character build (skipped without Blender
or MPFB, or when DESIGNSHELL_SKIP_E2E is set).

Backend tests run everywhere; Qt tests skip without PySide6.
"""
from __future__ import annotations

import ast
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import blender_character as char_mod  # noqa: E402
from skullpt import blender_export  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import exporters  # noqa: E402
from skullpt import governor  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt.builder import build_job  # noqa: E402
from skullpt.jobs import GateError, Job  # noqa: E402

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _character_desc(**overrides) -> dict:
    desc = char_mod.default_character_description("Test Character")
    desc.update(overrides)
    return desc


def _high_ram():
    return mock.patch("skullpt.governor.free_ram_mb", return_value=8192)


def _payload_of(script: str) -> dict:
    """The payload dict the generated script embeds."""
    line = script.split("D = ", 1)[1].split("\n", 1)[0]
    return ast.literal_eval(line)


# ------------------------------------------------------- description (T26)

class CharacterDescriptionTests(unittest.TestCase):
    """The character vocabulary: 11 sliders, a skin, a rig."""

    def test_default_character_is_valid(self):
        self.assertEqual(desc_mod.validate(_character_desc()), [])

    def test_character_kind_needs_a_character_block(self):
        desc = _character_desc()
        del desc["character"]
        problems = desc_mod.validate(desc)
        self.assertTrue(any("character block" in p for p in problems))

    def test_character_block_must_be_an_object(self):
        desc = _character_desc(character="tall")
        problems = desc_mod.validate(desc)
        self.assertTrue(any("character must be an object" in p
                            for p in problems))

    def test_slider_out_of_range_is_a_problem(self):
        for bad in (-0.1, 1.5):
            desc = _character_desc()
            desc["character"]["height"] = bad
            problems = desc_mod.validate(desc)
            self.assertTrue(any("character.height" in p for p in problems),
                            (bad, problems))
        for good in (0.0, 1.0):
            desc = _character_desc()
            desc["character"]["height"] = good
            self.assertEqual(desc_mod.validate(desc), [])

    def test_every_slider_is_checked(self):
        for slider in desc_mod.CHARACTER_SLIDERS:
            desc = _character_desc()
            desc["character"][slider] = 2.0
            self.assertTrue(any(f"character.{slider}" in p
                                for p in desc_mod.validate(desc)), slider)

    def test_skin_is_required(self):
        for bad in (None, "", "   "):
            desc = _character_desc()
            if bad is None:
                del desc["character"]["skin"]
            else:
                desc["character"]["skin"] = bad
            problems = desc_mod.validate(desc)
            self.assertTrue(any("character.skin" in p for p in problems),
                            (bad, problems))

    def test_rig_must_be_a_real_mpfb_rig(self):
        desc = _character_desc()
        desc["character"]["rig"] = "not_a_rig"
        problems = desc_mod.validate(desc)
        self.assertTrue(any("character.rig" in p for p in problems))
        for rig in desc_mod.CHARACTER_RIGS:
            desc = _character_desc()
            desc["character"]["rig"] = rig
            self.assertEqual(desc_mod.validate(desc), [], rig)

    def test_empty_parts_allowed_for_character_only(self):
        desc = _character_desc(parts=[])
        self.assertEqual(desc_mod.validate(desc), [])
        scene = json.loads(json.dumps(SHOP))
        scene["parts"] = []
        problems = desc_mod.validate(scene)
        self.assertTrue(any("non-empty list" in p for p in problems))

    def test_character_block_only_on_character_kind(self):
        scene = json.loads(json.dumps(SHOP))
        scene["character"] = _character_desc()["character"]
        problems = desc_mod.validate(scene)
        self.assertTrue(any("only for kind 'character'" in p
                            for p in problems))

    def test_estimate_includes_the_human(self):
        desc = _character_desc(parts=[])
        self.assertEqual(
            desc_mod.estimate_description_triangles(desc),
            desc_mod.CHARACTER_TRIANGLE_ESTIMATE)
        desc["parts"] = [{"id": "crate", "type": "crate", "shape": "box"}]
        self.assertEqual(
            desc_mod.estimate_description_triangles(desc),
            desc_mod.CHARACTER_TRIANGLE_ESTIMATE + 12)

    def test_summarize_names_skin_and_rig(self):
        summary = desc_mod.summarize(_character_desc())
        self.assertIn("young_caucasian_male.mhmat", summary)
        self.assertIn("game_engine", summary)


class CharacterBoundsTests(unittest.TestCase):
    """The framing box: feet at z=0, height from the slider."""

    def test_height_maps_150_to_195_cm(self):
        lo, size = char_mod.character_bounds_cm({"height": 0.0})
        self.assertEqual(size[2], 150.0)
        lo, size = char_mod.character_bounds_cm({"height": 1.0})
        self.assertEqual(size[2], 195.0)

    def test_feet_at_zero_and_centred(self):
        lo, size = char_mod.character_bounds_cm({})
        self.assertEqual(lo[2], 0.0)
        self.assertEqual(lo[0], -size[0] / 2)
        self.assertEqual(lo[1], -size[1] / 2)

    def test_normalize_fills_mpfb_defaults(self):
        block = char_mod.normalize_character({"skin": "x.mhmat"}, "hero")
        self.assertEqual(block["sliders"]["gender"], 0.5)
        self.assertEqual(block["sliders"]["race_asian"], 0.33)
        self.assertEqual(block["rig"], "game_engine")
        self.assertEqual(block["name"], "hero")
        self.assertIn("bounds", block)


# ------------------------------------------------------------- exporter (T26)

class ExporterRegistrationTests(unittest.TestCase):
    """T3 lifted for character: the kind has an exporter now."""

    def test_character_maps_to_blender_character(self):
        self.assertEqual(exporters.DEFAULT_EXPORTERS["character"],
                         ("blender_character",))
        self.assertIn("blender_character", exporters.EXPORTERS)

    def test_project_still_has_no_exporter(self):
        self.assertEqual(exporters.DEFAULT_EXPORTERS["project"], ())


class CharacterScriptTests(unittest.TestCase):
    """generate_character_script: the scene script plus the human."""

    def _script(self, desc=None, slug="test-character"):
        with tempfile.TemporaryDirectory() as tmp:
            return char_mod.generate_character_script(
                desc or _character_desc(), Path(tmp), slug)

    def test_script_compiles_and_carries_the_human(self):
        script = self._script()
        compile(script, "character.py", "exec")
        self.assertIn("deserialize_from_dict", script)
        self.assertIn("GAMEENGINE", script)
        self.assertIn("DS_CHARACTER_OK", script)
        self.assertIn("young_caucasian_male.mhmat", script)
        self.assertIn("game_engine", script)
        self.assertNotIn("__PAYLOAD__", script)
        self.assertNotIn("__SURFACE__", script)

    def test_payload_holds_the_character_block(self):
        payload = _payload_of(self._script())
        self.assertIn("character", payload)
        self.assertEqual(payload["character"]["skin"],
                         "young_caucasian_male.mhmat")
        self.assertEqual(payload["character"]["rig"], "game_engine")
        self.assertEqual(payload["character"]["sliders"]["height"], 0.5)

    def test_iso_camera_frames_the_human(self):
        # A partless job: the Iso camera looks at the human standing
        # at the origin, not the default cube at (50, 50, 50).
        payload = _payload_of(self._script())
        iso = next(c for c in payload["cameras"] if c["name"] == "Iso (auto)")
        self.assertAlmostEqual(iso["look_at_cm"][0], 0.0, places=1)
        self.assertAlmostEqual(iso["look_at_cm"][1], 0.0, places=1)
        self.assertAlmostEqual(iso["look_at_cm"][2], 86.25, places=1)
        # User cameras are untouched.
        desc = _character_desc(cameras=[{"name": "Front",
                                         "position_cm": [0, -300, 150],
                                         "look_at_cm": [0, 0, 100]}])
        payload = _payload_of(self._script(desc))
        front = next(c for c in payload["cameras"] if c["name"] == "Front")
        self.assertEqual(front["position_cm"], [0, -300, 150])

    def test_props_join_the_frame(self):
        desc = _character_desc(parts=[{
            "id": "platform", "type": "platform", "shape": "box",
            "position_cm": [0, 0, 0], "size_cm": [400, 400, 20]}])
        payload = _payload_of(self._script(desc))
        iso = next(c for c in payload["cameras"] if c["name"] == "Iso (auto)")
        # The 400 cm platform widens the frame past the human alone.
        self.assertGreater(iso["look_at_cm"][0], 50.0)

    def test_rigify_rig_enables_rigify(self):
        desc = _character_desc()
        desc["character"]["rig"] = "rigify.human"
        script = self._script(desc)
        self.assertIn('enable("rigify"', script)

    def test_missing_character_block_fails_plainly(self):
        desc = _character_desc()
        del desc["character"]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError) as ctx:
                char_mod.generate_character_script(desc, Path(tmp), "x")
        self.assertIn("character block", str(ctx.exception))

    def test_scene_script_has_no_mpfb_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.generate_script(SHOP, Path(tmp), "shop")
        self.assertNotIn("deserialize_from_dict", script)
        self.assertNotIn("DS_CHARACTER_OK", script)
        self.assertNotIn("character", _payload_of(script))

    def test_refactor_keeps_the_scene_payload(self):
        # build_payload + render_build_template are generate_script
        # split in two: the same description gives the same script.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            before = blender_export.generate_script(SHOP, out, "shop")
            after = blender_export.render_build_template(
                blender_export.build_payload(SHOP, out, "shop"))
        self.assertEqual(before, after)


# -------------------------------------------------------------- find_mpfb (T26)

class FindMpfbTests(unittest.TestCase):
    """MPFB detection against fake Blender config dirs."""

    def _mpfb_dir(self, base: Path, version="4.5", skins=()):
        mpfb = (base / "blender" / version / "extensions"
                / "user_default" / "mpfb")
        (mpfb / "data" / "skins").mkdir(parents=True)
        (mpfb / "__init__.py").write_text("# mpfb\n")
        for skin in skins:
            skin_dir = mpfb / "data" / "skins" / skin
            skin_dir.mkdir()
            (skin_dir / f"{skin}.mhmat").write_text("skin\n")
        return mpfb

    def test_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = char_mod.find_mpfb(config_dir=Path(tmp) / "blender" / "4.5")
        self.assertFalse(status["installed"])
        self.assertFalse(status["assets"])
        self.assertEqual(status["skins"], [])

    def test_extension_without_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._mpfb_dir(base)
            status = char_mod.find_mpfb(config_dir=base / "blender" / "4.5")
        self.assertTrue(status["installed"])
        self.assertFalse(status["assets"])
        self.assertEqual(status["skins"], [])

    def test_extension_with_skins(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._mpfb_dir(base, skins=("young_caucasian_male", "toon01"))
            status = char_mod.find_mpfb(config_dir=base / "blender" / "4.5")
        self.assertTrue(status["installed"])
        self.assertTrue(status["assets"])
        self.assertEqual(status["skins"],
                         ["toon01.mhmat", "young_caucasian_male.mhmat"])

    def test_scan_prefers_the_highest_version_with_mpfb(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._mpfb_dir(base, version="4.5",
                           skins=("young_caucasian_male",))
            self._mpfb_dir(base, version="5.3", skins=("toon01",))
            with mock.patch.dict(os.environ,
                                 {"XDG_CONFIG_HOME": str(base)}):
                status = char_mod.find_mpfb()
        self.assertTrue(status["installed"])
        self.assertIn("5.3", status["mpfb_dir"])
        self.assertEqual(status["skins"], ["toon01.mhmat"])

    def test_blender_version_dir_parses_version_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "blender"
            fake.write_text("#!/bin/sh\necho 'Blender 5.3.0 (hash abc)'\n")
            fake.chmod(0o755)
            self.assertEqual(char_mod.blender_version_dir(str(fake)), "5.3")
            fake.write_text("#!/bin/sh\necho 'not blender at all'\n")
            self.assertIsNone(char_mod.blender_version_dir(str(fake)))

    def test_find_mpfb_for_a_blender_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._mpfb_dir(base, version="4.5",
                           skins=("young_caucasian_male",))
            fake = base / "fake-blender"
            fake.write_text("#!/bin/sh\necho 'Blender 4.5.9'\n")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ,
                                 {"XDG_CONFIG_HOME": str(base)}):
                status = char_mod.find_mpfb(blender_exe=str(fake))
        self.assertTrue(status["installed"])
        self.assertTrue(status["assets"])


class CharacterPreflightTests(unittest.TestCase):
    """The exporter refuses plainly before Blender starts."""

    def _job(self, ws):
        job = Job.create(ws / "jobs", _character_desc())
        job.approve()
        return job

    def test_no_blender_fails_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._job(ws)
            with mock.patch("skullpt.blender_character.blender_export"
                            ".find_blender", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    char_mod.export_blender_character(
                        job.load_description(), job.output_dir)
        self.assertIn("Blender was not found", str(ctx.exception))

    def test_no_mpfb_fails_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._job(ws)
            with mock.patch("skullpt.blender_character.blender_export"
                            ".find_blender", return_value="/usr/bin/blender"), \
                    mock.patch("skullpt.blender_character.find_mpfb",
                               return_value={"installed": False, "assets": False,
                                             "mpfb_dir": None, "skins": []}):
                with self.assertRaises(RuntimeError) as ctx:
                    char_mod.export_blender_character(
                        job.load_description(), job.output_dir)
        self.assertIn("Character Studio is not installed",
                      str(ctx.exception))

    def test_missing_assets_fail_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._job(ws)
            with mock.patch("skullpt.blender_character.blender_export"
                            ".find_blender", return_value="/usr/bin/blender"), \
                    mock.patch("skullpt.blender_character.find_mpfb",
                               return_value={"installed": True, "assets": False,
                                             "mpfb_dir": "/x/mpfb", "skins": []}):
                with self.assertRaises(RuntimeError) as ctx:
                    char_mod.export_blender_character(
                        job.load_description(), job.output_dir)
        self.assertIn("missing its skins", str(ctx.exception))

    def test_unknown_skin_fails_plainly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = self._job(ws)
            with mock.patch("skullpt.blender_character.blender_export"
                            ".find_blender", return_value="/usr/bin/blender"), \
                    mock.patch("skullpt.blender_character.find_mpfb",
                               return_value={"installed": True, "assets": True,
                                             "mpfb_dir": "/x/mpfb",
                                             "skins": ["toon01.mhmat"]}):
                with self.assertRaises(RuntimeError) as ctx:
                    char_mod.export_blender_character(
                        job.load_description(), job.output_dir)
        self.assertIn("young_caucasian_male.mhmat", str(ctx.exception))


# ------------------------------------------------------------ job actions (T26)

class CharacterJobTests(unittest.TestCase):
    """new-character and edit-character, through bench.act."""

    def test_new_character_creates_a_valid_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            result = bench_mod.act(ws, "new-character",
                                   {"name": "Hero"})
            self.assertTrue(result["ok"])
            job = Job(ws / "jobs" / result["job"])
            self.assertEqual(job.state, "awaiting_approval")
            desc = job.load_description()
            self.assertEqual(desc["kind"], "character")
            self.assertEqual(desc["character"]["rig"], "game_engine")

    def test_new_character_defaults_its_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            result = bench_mod.act(ws, "new-character", {})
            self.assertTrue(result["ok"])
            job = Job(ws / "jobs" / result["job"])
            self.assertEqual(job.load_description()["name"],
                             "New character")

    def test_edit_character_saves_and_reopens_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _character_desc())
            job.approve()
            character = dict(job.load_description()["character"])
            character["height"] = 0.9
            character["skin"] = "toon01.mhmat"
            result = bench_mod.act(ws, "edit-character",
                                   {"job": job.dir.name,
                                    "character": character})
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "awaiting_approval")
            desc = Job(ws / "jobs" / job.dir.name).load_description()
            self.assertEqual(desc["character"]["height"], 0.9)
            self.assertEqual(desc["character"]["skin"], "toon01.mhmat")
            self.assertTrue((ws / "jobs" / job.dir.name
                             / "description.before-character-edit.json").exists())

    def test_edit_character_rejects_a_bad_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _character_desc())
            character = dict(job.load_description()["character"])
            character["height"] = 2.0
            with self.assertRaises(desc_mod.DescriptionError):
                bench_mod.act(ws, "edit-character",
                              {"job": job.dir.name, "character": character})
            desc = Job(ws / "jobs" / job.dir.name).load_description()
            self.assertEqual(desc["character"]["height"], 0.5)

    def test_edit_character_refuses_a_scene_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", json.loads(json.dumps(SHOP)))
            with self.assertRaises(ValueError) as ctx:
                bench_mod.act(ws, "edit-character",
                              {"job": job.dir.name,
                               "character": _character_desc()["character"]})
        self.assertIn("not a character job", str(ctx.exception))

    def test_edit_character_refuses_a_building_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _character_desc())
            job.approve()
            job.transition("building")
            with self.assertRaises(GateError):
                bench_mod.act(ws, "edit-character",
                              {"job": job.dir.name,
                               "character": _character_desc()["character"]})


class EngineStatusTests(unittest.TestCase):
    """engine_status gains character_studio."""

    def test_character_studio_follows_mpfb(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            ready = {"installed": True, "assets": True,
                     "mpfb_dir": "/x/mpfb",
                     "skins": ["toon01.mhmat"]}
            with mock.patch("skullpt.blender_character.find_mpfb",
                            return_value=ready):
                self.assertTrue(
                    bench_mod.engine_status(ws)["character_studio"])
            missing = {"installed": True, "assets": False,
                       "mpfb_dir": "/x/mpfb", "skins": []}
            with mock.patch("skullpt.blender_character.find_mpfb",
                            return_value=missing):
                self.assertFalse(
                    bench_mod.engine_status(ws)["character_studio"])


# ------------------------------------------------------------ mannequin (T26)

class MannequinTests(unittest.TestCase):
    """The viewport stand-in: human-shaped, right height, at origin."""

    def test_character_scene_has_a_mannequin(self):
        scene = qtbench.scene_meshes(_character_desc())
        ids = [m["id"] for m in scene["meshes"]]
        self.assertIn("character", ids)
        mannequin = next(m for m in scene["meshes"]
                         if m["id"] == "character")
        self.assertGreater(len(mannequin["tris"]), 100)
        self.assertEqual(mannequin["bounds"]["min"][2], 0.0)
        # Default height slider 0.5 -> 172.5 cm tall.
        self.assertAlmostEqual(mannequin["bounds"]["max"][2], 172.5,
                               places=1)

    def test_mannequin_follows_the_height_slider(self):
        desc = _character_desc()
        desc["character"]["height"] = 1.0
        scene = qtbench.scene_meshes(desc)
        mannequin = next(m for m in scene["meshes"]
                         if m["id"] == "character")
        self.assertAlmostEqual(mannequin["bounds"]["max"][2], 195.0,
                               places=1)

    def test_scene_has_no_mannequin(self):
        scene = qtbench.scene_meshes(json.loads(json.dumps(SHOP)))
        self.assertNotIn("character", [m["id"] for m in scene["meshes"]])

    def test_partless_character_bounds_are_the_mannequin(self):
        scene = qtbench.scene_meshes(_character_desc())
        bounds = scene["bounds"]
        self.assertAlmostEqual(bounds["center"][0], 0.0, places=1)
        self.assertAlmostEqual(bounds["center"][1], 0.0, places=1)
        self.assertAlmostEqual(bounds["span"], 172.5, places=1)

    def test_props_join_the_bounds(self):
        desc = _character_desc(parts=[{
            "id": "platform", "type": "platform", "shape": "box",
            "position_cm": [500, 0, 0], "size_cm": [100, 100, 100]}])
        scene = qtbench.scene_meshes(desc)
        self.assertGreater(scene["bounds"]["max"][0], 500.0)
        self.assertIn("character", [m["id"] for m in scene["meshes"]])

    def test_mannequin_is_clickable(self):
        scene = qtbench.scene_meshes(_character_desc())
        hit = qtbench.pick_mesh(scene["meshes"],
                                (0.0, -500.0, 90.0), (0.0, 1.0, 0.0))
        self.assertEqual(hit, "character")


# ------------------------------------------------------------ installer (T26)

class InstallerTests(unittest.TestCase):
    """--with-character-studio: the flag, the downloads, the step."""

    def setUp(self):
        self.install = (ROOT / "install.sh").read_text(encoding="utf-8")

    def test_flag_exists(self):
        self.assertIn("--with-character-studio", self.install)
        self.assertIn("WITH_CHARACTER=1", self.install)
        self.assertIn("PASSTHROUGH+=(--with-character-studio)", self.install)

    def test_download_values_match_the_module(self):
        # install.sh and blender_character.py carry the same URLs and
        # hashes; a drift between them would download the wrong file.
        # The MPFB URL is built from the SHA in the shell, so it is
        # reconstructed here and compared with the module's constant.
        match = re.search(r'MPFB_SHA256="([0-9a-f]{64})"', self.install)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), char_mod.MPFB_SHA256)
        match = re.search(r'ASSETS_SHA256="([0-9a-f]{64})"', self.install)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), char_mod.ASSETS_SHA256)
        for value in (char_mod.ASSETS_URL, char_mod.ASSETS_URL_FALLBACK):
            self.assertIn(value, self.install)
        url_line = re.search(r'MPFB_URL="([^"]+)"', self.install)
        self.assertIsNotNone(url_line)
        built = url_line.group(1).replace("${MPFB_SHA256}",
                                          char_mod.MPFB_SHA256)
        self.assertEqual(built, char_mod.MPFB_URL)
        self.assertIn(f'MPFB_VERSION="{char_mod.MPFB_VERSION}"',
                      self.install)

    def test_download_verified_takes_a_fallback(self):
        self.assertIn('fallback="${5:-}"', self.install)

    def test_step_60_installs_the_studio(self):
        self.assertIn('step 60 "Installing Character Studio"', self.install)
        self.assertIn("extension install-file", self.install)
        self.assertIn("data/skins", self.install)

    def test_no_blender_is_a_plain_refusal(self):
        self.assertIn("Character Studio needs Blender first",
                      self.install)

    def test_no_sudo_in_installer(self):
        self.assertNotIn("sudo", self.install)


# ------------------------------------------------------------ Qt window (T26)

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


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtCharacterWindowTests(_QtCase):
    """The sixth window: sliders, skin, rig, save, gate."""

    def _studio_ready(self):
        return {"installed": True, "assets": True,
                "mpfb_dir": str(ROOT / "skullpt"),
                "skins": ["toon01.mhmat", "young_caucasian_male.mhmat"]}

    def _character_job(self):
        job = Job.create(self.ws / "jobs", _character_desc())
        return job

    def _open_character(self, window):
        with mock.patch("skullpt.blender_character.find_mpfb",
                        return_value=self._studio_ready()):
            window._open_window("character")
            self._pump()
            opened = window._child_windows["character"]
        self._windows.append(opened)
        return opened

    def test_rail_has_seven_buttons(self):
        window = self._bench()
        # The menu bar carries its own tool button (empty tooltip);
        # the rail's buttons are the ones with tooltips.
        tips = sorted(btn.toolTip()
                      for btn in window.findChildren(QtWidgets.QToolButton)
                      if btn.toolTip())
        self.assertEqual(tips, ["Character Studio", "How to Use",
                                "Images", "Jobs", "Library", "Render",
                                "Settings"])

    def test_character_window_opens_with_its_controls(self):
        from skullpt import qtwindows

        window = self._bench()
        job = self._character_job()
        window.select_job(job.dir.name)
        opened = self._open_character(window)
        self.assertIsInstance(opened, qtwindows.CharacterWindow)
        self.assertTrue(opened.isVisible())
        self.assertEqual(len(opened._sliders), 11)
        self.assertEqual(opened._skin_combo.count(), 2)
        self.assertEqual(opened._rig_combo.count(),
                         len(desc_mod.CHARACTER_RIGS))
        self.assertTrue(opened._save_btn.isEnabled())

    def test_sliders_show_the_job_values(self):
        window = self._bench()
        job = self._character_job()
        desc = job.load_description()
        desc["character"]["height"] = 0.9
        desc["character"]["muscle"] = 0.2
        desc_mod.dump(desc, job.desc_path)
        window.select_job(job.dir.name)
        opened = self._open_character(window)
        self.assertEqual(opened._sliders["height"].value(), 90)
        self.assertEqual(opened._sliders["muscle"].value(), 20)
        self.assertEqual(opened._slider_values["height"].text(), "90%")

    def test_save_writes_the_character_block(self):
        window = self._bench()
        job = self._character_job()
        window.select_job(job.dir.name)
        opened = self._open_character(window)
        opened._sliders["height"].setValue(80)
        opened._save_character()
        desc = Job(self.ws / "jobs" / job.dir.name).load_description()
        self.assertEqual(desc["character"]["height"], 0.8)

    def test_window_without_the_studio_says_so_plainly(self):
        window = self._bench()
        job = self._character_job()
        window.select_job(job.dir.name)
        with mock.patch("skullpt.blender_character.find_mpfb",
                        return_value={"installed": False, "assets": False,
                                      "mpfb_dir": None, "skins": []}):
            window._open_window("character")
            self._pump()
            opened = window._child_windows["character"]
        self._windows.append(opened)
        self.assertIn("not installed", opened._status_note.text())
        self.assertFalse(opened._save_btn.isEnabled())

    def test_set_job_ignores_a_scene_job(self):
        window = self._bench()
        scene_job = Job.create(self.ws / "jobs",
                               json.loads(json.dumps(SHOP)))
        char_job = self._character_job()
        window.select_job(char_job.dir.name)
        opened = self._open_character(window)
        self.assertEqual(opened._job_name, char_job.dir.name)
        opened.set_job(scene_job.dir.name)
        self.assertEqual(opened._job_name, char_job.dir.name)


# ------------------------------------------------- real Blender build (T26)

class AtomicStateSaveTests(unittest.TestCase):
    """job.json saves atomically (v1.7 verification found the race:
    the background-build poll read an empty job.json mid-write)."""

    def test_save_leaves_no_tmp_file_and_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", _character_desc())
            job.approve()
            character = dict(job.load_description()["character"])
            character["height"] = 0.9
            job.edit_character(character)
            self.assertEqual(list(job.dir.glob("*.tmp")), [])
            state = json.loads(job.state_path.read_text())
            self.assertEqual(state["state"], "awaiting_approval")


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class CharacterEndToEndTests(unittest.TestCase):
    """A real character through real Blender + MPFB2."""

    def test_character_builds_blend_glb_and_renders(self):
        status = char_mod.find_mpfb(
            blender_exe=blender_export.find_blender())
        if not (status["installed"] and status["assets"]):
            self.skipTest("MPFB2 or its assets are not installed "
                          "for this Blender")
        desc = _character_desc()
        desc["game_ready"] = True
        desc["render"] = {"engine": "eevee", "resolution_px": 128,
                          "samples": 1, "denoise": False}
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = Job.create(ws / "jobs", desc)
            job.approve()
            with _high_ram():
                report = build_job(job, ws)
            self.assertTrue(report["ok"], report)
            outputs = report["outputs"]
            slug = job.dir.name
            for name in (f"{slug}.blend", f"{slug}.glb", f"{slug}.stats.json",
                         f"{slug}_lod1.glb", f"{slug}_lod2.glb",
                         f"{slug}_collision.glb", "render-iso-auto.png"):
                self.assertIn(name, outputs, outputs)
            stats = json.loads(
                (job.output_dir / f"{slug}.stats.json").read_text())
            self.assertGreater(stats["total_tris"], 30000)
            # The GLB carries the skinned rig: parse its JSON chunk.
            glb = (job.output_dir / f"{slug}.glb").read_bytes()
            magic, version, length = struct.unpack("<III", glb[:12])
            self.assertEqual(magic, 0x46546C67)
            chunk_len, chunk_type = struct.unpack("<II", glb[12:20])
            self.assertEqual(chunk_type, 0x4E4F534A)
            glb_json = json.loads(glb[20:20 + chunk_len])
            self.assertIn("skins", glb_json)
            self.assertTrue(glb_json["skins"], "GLB has no skin")


class GovernorEmptyLockTests(unittest.TestCase):
    """An empty lock file is a contender mid-write, not a stale lock.

    Found by Arlo in v1.7 verification: the old acquire() read an
    empty file as holder 0, called it stale, unlinked it, and won —
    so under load both contenders reported "won" and two heavy jobs
    could run at once. The fix waits and re-reads before deciding
    an empty file is orphaned.
    """

    def test_empty_lock_file_waits_for_the_pid_instead_of_stealing(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            lock_path.write_text("")  # created, pid not yet written

            def delayed_write():
                time.sleep(0.05)
                lock_path.write_text(str(os.getpid()))

            writer = threading.Thread(target=delayed_write)
            writer.start()
            lock = governor.HeavyJobLock(lock_path)
            with _high_ram(), self.assertRaises(governor.GovernorError):
                lock.acquire()
            writer.join()
            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.read_text(), str(os.getpid()))

    def test_empty_lock_file_still_orphaned_is_taken(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "heavy.lock"
            lock_path.write_text("")  # empty, and nobody ever writes
            lock = governor.HeavyJobLock(lock_path)
            with _high_ram():
                lock.acquire()
            self.assertEqual(lock_path.read_text(), str(os.getpid()))
            lock.release()


if __name__ == "__main__":
    unittest.main()
