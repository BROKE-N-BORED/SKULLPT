"""SKULLPT v1.13 tests: the material system, phase 3 (M12-M14).

Backend tests run everywhere; Qt tests skip without PySide6; the
real-Blender tests skip without Blender (or with DESIGNSHELL_SKIP_E2E).
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import __version__  # noqa: E402
from skullpt import bake as bake_mod  # noqa: E402
from skullpt import bench as bench_mod  # noqa: E402
from skullpt import blender_export  # noqa: E402
from skullpt import builder as builder_mod  # noqa: E402
from skullpt import description as desc_mod  # noqa: E402
from skullpt import materials as materials_mod  # noqa: E402
from skullpt import settings as settings_mod  # noqa: E402
from skullpt import unreal as unreal_mod  # noqa: E402
from skullpt.jobs import Job  # noqa: E402

try:
    from PySide6 import QtWidgets  # noqa: F401
    _QT_TESTS = True
except Exception:
    _QT_TESTS = False

SHOP = json.loads((ROOT / "examples" / "shop-panel.description.json").read_text())

WOOD_SPEC = {"family": "wood", "style": "walnut", "layout": "boards",
             "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}
BRICK_SPEC = {"family": "brick", "style": "red", "layout": "stretcher",
              "params": {"seed": 7}}
METAL_SPEC = {"family": "metal", "style": "copper", "layout": "solid",
              "params": {}, "wear": {}}
GLASS_SPEC = {"family": "glass", "style": "clear", "layout": "solid",
              "params": {}, "wear": {}}


def _workspace(tmp: str) -> Path:
    ws = Path(tmp)
    (ws / "jobs").mkdir()
    (ws / "library").mkdir()
    return ws


def _job(ws: Path, desc: dict | None = None) -> Job:
    return Job.create(ws / "jobs", json.loads(json.dumps(desc or SHOP)))


def _mat_desc(tier: str | None = None, displacement: bool = False) -> dict:
    """One walnut-boards part, one brick part, one string part."""
    desc = {
        "name": "material test", "kind": "scene",
        "summary": "three parts for v1.13",
        "parts": [
            {"id": "plank", "type": "prop", "shape": "box",
             "position_cm": [0, 0, 0], "size_cm": [120, 12, 2],
             "material": json.loads(json.dumps(WOOD_SPEC))},
            {"id": "block", "type": "prop", "shape": "box",
             "position_cm": [50, 10, 0], "size_cm": [20, 20, 20],
             "material": json.loads(json.dumps(BRICK_SPEC))},
            {"id": "bare", "type": "prop", "shape": "box",
             "position_cm": [40, 20, 0], "size_cm": [10, 10, 10],
             "material": "metal"},
        ],
        "constraints": [], "rubric": [],
        "budget": {"max_tris": 40000, "preview_px": 640},
    }
    if tier is not None:
        desc["render"] = {"tier": tier}
    if displacement:
        desc["parts"][0]["material"]["displacement"] = True
    return desc


# ---------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.14 everywhere."""

    def test_version_is_113(self):
        self.assertEqual(__version__, "1.14")
        self.assertIn("v1.14", (ROOT / "README.md").read_text())
        self.assertIn("## v1.14", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.14", (ROOT / "DESIGN.md").read_text())


# ---------------------------------------------------------------- M13: tiers


class TierSettingsTests(unittest.TestCase):
    """M13: the tier resolves like the other render settings."""

    def test_default_is_balanced(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            resolved = settings_mod.resolve_render_settings(ws, SHOP)
            self.assertEqual(resolved["tier"], "balanced")

    def test_job_block_wins_over_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            settings_mod.save_render_settings(ws, {"tier": "draft"})
            desc = json.loads(json.dumps(SHOP))
            desc["render"] = {"tier": "final"}
            resolved = settings_mod.resolve_render_settings(ws, desc)
            self.assertEqual(resolved["tier"], "final")

    def test_workspace_default_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            settings_mod.save_render_settings(ws, {"tier": "draft"})
            resolved = settings_mod.resolve_render_settings(ws, SHOP)
            self.assertEqual(resolved["tier"], "draft")

    def test_invalid_tier_falls_back_to_balanced(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            settings_mod.save_render_settings(ws, {"tier": "ultra"})
            resolved = settings_mod.resolve_render_settings(ws, SHOP)
            self.assertEqual(resolved["tier"], "balanced")
            desc = json.loads(json.dumps(SHOP))
            desc["render"] = {"tier": "ultra"}
            resolved = settings_mod.resolve_render_settings(ws, desc)
            self.assertEqual(resolved["tier"], "balanced")

    def test_render_tier_validates(self):
        desc = _mat_desc(tier="final")
        self.assertEqual(desc_mod.validate(desc), [])
        desc = _mat_desc(tier="ultra")
        problems = desc_mod.validate(desc)
        self.assertTrue(any(p.startswith("render.tier") for p in problems),
                        problems)


class NodeCountTierTests(unittest.TestCase):
    """M13: tier-aware node counts, for every family."""

    def test_draft_drops_the_detail_node_per_family(self):
        # A plain spec (no wear, no knots/chips/frost - set
        # explicitly, since wood's default knots are above zero):
        # Draft drops exactly the family's detail node count,
        # nothing else.
        zero_params = {"wood": {"knots": 0}, "paint": {"chip": 0},
                       "glass": {"frost": 0}}
        for family in materials_mod.FAMILIES:
            spec = {"family": family, "layout": "solid",
                    "params": zero_params.get(family, {}), "wear": {}}
            balanced = materials_mod.node_count(spec, "balanced")
            draft = materials_mod.node_count(spec, "draft")
            drop = materials_mod._DRAFT_DROP[family]
            self.assertEqual(draft, balanced - drop, family)
            self.assertLessEqual(draft, balanced, family)

    def test_final_adds_two_nodes_where_there_is_a_bump(self):
        for family in materials_mod.FAMILIES:
            spec = {"family": family, "layout": "solid",
                    "params": {}, "wear": {}}
            balanced = materials_mod.node_count(spec, "balanced")
            final = materials_mod.node_count(spec, "final")
            if family == "glass":
                # Glass without frost has no bump chain.
                self.assertEqual(final, balanced, family)
            else:
                self.assertEqual(final, balanced + 2, family)

    def test_final_adds_two_for_frosted_glass(self):
        spec = {"family": "glass", "style": "frosted", "layout": "solid",
                "params": {}, "wear": {}}
        self.assertEqual(materials_mod.node_count(spec, "final"),
                         materials_mod.node_count(spec, "balanced") + 2)

    def test_draft_prunes_wear_knots_and_detail_together(self):
        # Walnut boards + knots + dirt: balanced 24 (23 + wear).
        # Draft drops the blotch pair (2), the knots (3) and the
        # wear group (1): 24 - 6 = 18.
        self.assertEqual(materials_mod.node_count(WOOD_SPEC), 24)
        self.assertEqual(materials_mod.node_count(WOOD_SPEC, "draft"), 18)
        self.assertEqual(materials_mod.node_count(WOOD_SPEC, "final"), 26)

    def test_draft_always_drops_something_when_worn(self):
        # With wear on, Draft is strictly smaller for every family:
        # the wear group alone guarantees it.
        for family in materials_mod.FAMILIES:
            spec = {"family": family, "layout": "solid",
                    "params": {}, "wear": {"dirt": 0.5}}
            self.assertLess(materials_mod.node_count(spec, "draft"),
                            materials_mod.node_count(spec, "balanced"),
                            family)

    def test_unknown_tier_counts_as_balanced(self):
        self.assertEqual(materials_mod.node_count(WOOD_SPEC, "ultra"),
                         materials_mod.node_count(WOOD_SPEC, "balanced"))

    def test_bake_resolution_by_tier(self):
        self.assertEqual(materials_mod.bake_resolution("draft"), 256)
        self.assertEqual(materials_mod.bake_resolution("balanced"), 512)
        self.assertEqual(materials_mod.bake_resolution("final"), 1024)
        self.assertEqual(materials_mod.bake_resolution("ultra"), 512)


class PreviewTierTests(unittest.TestCase):
    """M13: previews use Draft unless asked otherwise."""

    def test_preview_script_defaults_to_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.preview_material_script(
                WOOD_SPEC, Path(tmp), "prev")
            self.assertIn("'tier': 'draft'", script)

    def test_preview_script_honours_an_explicit_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.preview_material_script(
                WOOD_SPEC, Path(tmp), "prev", tier="final")
            self.assertIn("'tier': 'final'", script)

    def test_build_payload_carries_the_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            desc = _mat_desc(tier="draft")
            payload = blender_export.build_payload(desc, Path(tmp), "t")
            originals = {p["id"]: p["material"]
                         for p in desc["parts"]
                         if isinstance(p.get("material"), dict)}
            specs = [(p["id"], p["material_spec"]) for p in payload["parts"]
                     if p.get("material_spec")]
            self.assertTrue(specs)
            for part_id, spec in specs:
                self.assertEqual(spec["tier"], "draft")
                self.assertEqual(
                    spec["nodes"],
                    materials_mod.node_count(originals[part_id], "draft"),
                    part_id)

    def test_material_report_lists_tier_and_displacement(self):
        report = builder_mod.material_report(_mat_desc(tier="final",
                                                       displacement=True))
        self.assertEqual(report["plank"]["tier"], "final")
        self.assertTrue(report["plank"]["displacement"])
        self.assertEqual(report["plank"]["nodes"],
                         materials_mod.node_count(WOOD_SPEC, "final"))
        self.assertFalse(report["block"]["displacement"])


# ---------------------------------------------------------------- M14: displacement


class DisplacementSpecTests(unittest.TestCase):
    """M14: the displacement flag validates, defaults off, round-trips."""

    def test_validation_rejects_non_booleans(self):
        spec = dict(WOOD_SPEC, displacement="yes")
        problems = materials_mod.validate_material_spec(spec)
        self.assertTrue(any("displacement" in p for p in problems),
                        problems)

    def test_normalize_defaults_off_and_keeps_on(self):
        self.assertFalse(
            materials_mod.normalize_material_spec(WOOD_SPEC)["displacement"])
        spec = dict(WOOD_SPEC, displacement=True)
        self.assertTrue(
            materials_mod.normalize_material_spec(spec)["displacement"])

    def test_presets_round_trip_displacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            spec = dict(WOOD_SPEC, displacement=True)
            materials_mod.save_preset(ws, "disp", spec)
            loaded = materials_mod.load_presets(ws)["disp"]
            self.assertTrue(loaded["displacement"])


class DisplacementEstimateTests(unittest.TestCase):
    """M14: the budget gate counts displaced geometry (16x)."""

    def test_box_sphere_cylinder_multiply_by_16(self):
        for shape, base in (("box", 12), ("sphere", 528),
                            ("cylinder", 92)):
            part = {"shape": shape,
                    "material": {"displacement": True}}
            self.assertEqual(desc_mod.estimate_triangles(part),
                             base * 16, shape)

    def test_without_displacement_the_estimate_is_unchanged(self):
        self.assertEqual(
            desc_mod.estimate_triangles({"shape": "box"}), 12)
        self.assertEqual(
            desc_mod.estimate_triangles(
                {"shape": "box", "material": {"displacement": False}}), 12)

    def test_imported_measured_count_is_untouched(self):
        part = {"shape": "box", "mesh": {"triangles": 5000},
                "material": {"displacement": True}}
        self.assertEqual(desc_mod.estimate_triangles(part), 5000)

    def test_description_estimate_sums_displaced_parts(self):
        desc = _mat_desc(displacement=True)
        # plank 12*16 + block 12 + bare 12 = 216
        self.assertEqual(desc_mod.estimate_description_triangles(desc),
                         216)


class DisplacementGateTests(unittest.TestCase):
    """M14: the CPU gate strips displacement when RAM is short."""

    def test_enough_ram_keeps_displacement(self):
        desc = _mat_desc(displacement=True)
        stripped, note = builder_mod.check_displacement_gate(
            desc, free_mb=10000)
        self.assertEqual(stripped, [])
        self.assertIsNone(note)
        self.assertTrue(desc["parts"][0]["material"]["displacement"])

    def test_low_ram_strips_and_names_the_numbers(self):
        desc = _mat_desc(displacement=True)
        with mock.patch("skullpt.governor.adaptive_min_free_mb",
                        return_value=1000):
            stripped, note = builder_mod.check_displacement_gate(
                desc, free_mb=1200)
        self.assertEqual(stripped, ["plank"])
        self.assertIsNotNone(note)
        self.assertIn("1200", note)
        self.assertIn("1512", note)  # floor 1000 + margin 512
        self.assertIn("bump", note)
        self.assertFalse(desc["parts"][0]["material"]["displacement"])

    def test_no_displaced_parts_is_a_no_op(self):
        desc = _mat_desc()
        stripped, note = builder_mod.check_displacement_gate(
            desc, free_mb=10)
        self.assertEqual(stripped, [])
        self.assertIsNone(note)


class DisplacementTemplateTests(unittest.TestCase):
    """M14: the generated build script wires displacement."""

    def test_script_wires_the_displacement_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.generate_script(
                _mat_desc(displacement=True), Path(tmp), "d")
            self.assertIn('out_node.inputs["Displacement"]', script)
            self.assertIn("displacement_method = \"BOTH\"", script)
            self.assertIn('"displace-subdiv", "SUBSURF"', script)
            self.assertIn('subdivision_type = "SIMPLE"', script)
            self.assertIn("levels = 2", script)
            self.assertIn("modifier_apply", script)

    def test_script_records_displacement_in_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = blender_export.generate_script(
                _mat_desc(displacement=True), Path(tmp), "d")
            self.assertIn('"displacement": displaced', script)


class BuildReportReconciliationTests(unittest.TestCase):
    """M14: a part that asked for displacement but has no bump chain
    gets a plain note in the build report, not a silent mismatch."""

    def test_note_appears_when_measured_says_no(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            desc = _mat_desc(displacement=True)
            desc["parts"][0]["material"] = dict(
                GLASS_SPEC, displacement=True)
            job = _job(ws, desc)
            (job.dir / "build-report.json").write_text(json.dumps({
                "ok": True,
                "materials": {"plank": {"breadcrumb": "Glass",
                                        "nodes": 4, "tier": "balanced",
                                        "displacement": True}}}))
            job.output_dir.mkdir(parents=True, exist_ok=True)
            (job.output_dir / f"{job.dir.name}.stats.json").write_text(
                json.dumps({"total_tris": 12, "parts": {},
                            "materials": {"plank": {"breadcrumb": "Glass",
                                                    "nodes": 4,
                                                    "displacement": False}}}))
            report = bench_mod.build_report(ws, job.dir.name)
            notes = report.get("notes") or []
            self.assertTrue(any("plank" in n and "displacement" in n
                                for n in notes), notes)

    def test_no_note_when_measured_agrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc(displacement=True))
            (job.dir / "build-report.json").write_text(json.dumps({
                "ok": True,
                "materials": {"plank": {"breadcrumb": "Wood",
                                        "nodes": 24, "tier": "balanced",
                                        "displacement": True}}}))
            job.output_dir.mkdir(parents=True, exist_ok=True)
            (job.output_dir / f"{job.dir.name}.stats.json").write_text(
                json.dumps({"total_tris": 192, "parts": {},
                            "materials": {"plank": {"breadcrumb": "Wood",
                                                    "nodes": 24,
                                                    "displacement": True}}}))
            report = bench_mod.build_report(ws, job.dir.name)
            self.assertFalse(report.get("notes"))


# ---------------------------------------------------------------- M12: baking


class BakeablePartsTests(unittest.TestCase):
    """M12: which parts bake, which are named gaps."""

    def test_wood_bakes_four_maps(self):
        bakeable, skipped = bake_mod.bakeable_parts(_mat_desc())
        by_id = {p["id"]: p for p in bakeable}
        self.assertEqual(by_id["plank"]["maps"],
                         ["color", "roughness", "normal", "height"])
        self.assertEqual(by_id["plank"]["metallic"], 0.0)
        self.assertEqual(skipped, [])

    def test_metal_carries_its_metallic_value(self):
        desc = _mat_desc()
        desc["parts"][1]["material"] = json.loads(json.dumps(METAL_SPEC))
        bakeable, _skipped = bake_mod.bakeable_parts(desc)
        by_id = {p["id"]: p for p in bakeable}
        self.assertGreater(by_id["block"]["metallic"], 0.0)

    def test_glass_is_a_named_gap(self):
        desc = _mat_desc()
        desc["parts"][0]["material"] = json.loads(json.dumps(GLASS_SPEC))
        bakeable, skipped = bake_mod.bakeable_parts(desc)
        self.assertNotIn("plank", [p["id"] for p in bakeable])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["id"], "plank")
        self.assertIn("transmissive", skipped[0]["reason"])

    def test_string_materials_are_neither(self):
        bakeable, skipped = bake_mod.bakeable_parts(SHOP)
        self.assertEqual(bakeable, [])
        self.assertEqual(skipped, [])

    def test_imported_parts_are_skipped(self):
        desc = _mat_desc()
        desc["parts"][0]["type"] = "imported"
        bakeable, skipped = bake_mod.bakeable_parts(desc)
        self.assertNotIn("plank", [p["id"] for p in bakeable])
        self.assertEqual(skipped[0]["id"], "plank")
        self.assertIn("imported", skipped[0]["reason"])


class FlatPngTests(unittest.TestCase):
    """M12: the host-side metallic map is a real grayscale PNG."""

    def test_flat_png_decodes_to_the_gray_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metal.png"
            bake_mod.write_flat_png(path, 4, 200)
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            width, height, depth, color = struct.unpack(
                ">IIBB", data[16:26])
            self.assertEqual((width, height, depth, color),
                             (4, 4, 8, 0))
            # The one IDAT chunk decompresses to 4 rows of
            # (filter 0 + 4 gray pixels).
            idat_len = struct.unpack(">I", data[33:37])[0]
            self.assertEqual(data[37:41], b"IDAT")
            raw = zlib.decompress(data[41:41 + idat_len])
            self.assertEqual(raw, (b"\x00" + bytes([200]) * 4) * 4)


class BakeRefusalTests(unittest.TestCase):
    """M12: bake refuses plainly when it cannot bake."""

    def test_no_bakeable_parts_is_a_plain_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, SHOP)
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            self.assertIn("nothing in this job can be baked",
                          result["problems"][0])

    def test_glass_only_names_the_glass_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            desc = _mat_desc()
            desc["parts"] = [desc["parts"][0]]
            desc["parts"][0]["material"] = json.loads(
                json.dumps(GLASS_SPEC))
            job = _job(ws, desc)
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            self.assertIn("transmissive", result["problems"][0])

    def test_missing_blend_means_build_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            result = bake_mod.bake_job(job, ws)
            self.assertFalse(result["ok"])
            self.assertIn("build the job first", result["problems"][0])

    def test_bad_resolution_is_a_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc())
            job.output_dir.mkdir(parents=True, exist_ok=True)
            (job.output_dir / f"{job.dir.name}.blend").write_bytes(b"x")
            with self.assertRaises(ValueError):
                bake_mod.bake_job(job, ws, resolution=2048)


class BakeScriptTests(unittest.TestCase):
    """M12: the bake script generation."""

    def test_script_contains_the_bake_calls(self):
        payload = {"out": "/tmp/out", "slug": "job", "res": 512,
                   "parts": [{"id": "plank",
                              "maps": ["color", "roughness", "normal",
                                       "height"]}]}
        script = blender_export.generate_bake_script(payload)
        self.assertIn("DS_BAKE_DONE", script)
        # The bake type is passed to the operator explicitly (the
        # operator ignores scene.cycles.bake_type, probed on
        # 4.5.9); colour and height bake through the emit-material
        # trick, not a DIFFUSE pass (which bakes black).
        self.assertIn('bpy.ops.object.bake(type=bake_type)', script)
        self.assertIn('"ROUGHNESS"', script)
        self.assertIn('"NORMAL"', script)
        self.assertIn('path, "EMIT"', script)
        self.assertIn("def _emit_material(mat, source):", script)
        self.assertNotIn("use_pass_direct", script)
        # The payload is repr'd with single quotes.
        self.assertIn("'plank'", script)
        compile(script, "bake.py", "exec")


class UnrealMapsTests(unittest.TestCase):
    """M12: the Unreal package carries the baked maps."""

    def _package(self, tmp: str):
        ws = _workspace(tmp)
        job = _job(ws, _mat_desc())
        job.output_dir.mkdir(parents=True, exist_ok=True)
        slug = job.dir.name
        (job.output_dir / f"{slug}.glb").write_bytes(b"glTF")
        (job.output_dir / f"{slug}.blend").write_bytes(b"BLEND")
        for name in (f"{slug}-plank-color.png",
                     f"{slug}-plank-roughness.png",
                     f"{slug}-plank-normal.png",
                     f"{slug}-plank-height.png",
                     f"{slug}-plank-metallic.png"):
            (job.output_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")
        # A render PNG must NOT be packaged as a texture.
        (job.output_dir / "render-iso-auto.png").write_bytes(b"\x89PNG")
        dest = unreal_mod.package_unreal(job, Path(tmp) / "pkg")
        return dest, slug

    def test_maps_are_copied_and_renders_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest, slug = self._package(tmp)
            for map_name in ("color", "roughness", "normal", "height",
                             "metallic"):
                self.assertTrue(
                    (dest / f"{slug}-plank-{map_name}.png").exists(),
                    map_name)
            self.assertFalse((dest / "render-iso-auto.png").exists())

    def test_import_script_lists_textures_and_compiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest, slug = self._package(tmp)
            script = (dest / "import_into_unreal.py").read_text()
            compile(script, "import_into_unreal.py", "exec")
            self.assertIn(f"{slug}-plank-color.png", script)
            self.assertIn(f"{slug}-plank-normal.png", script)
            # Colour is sRGB, the data maps are not.
            self.assertIn(f"('{slug}-plank-color.png', True)", script)
            self.assertIn(f"('{slug}-plank-normal.png', False)", script)
            self.assertIn('"textures": textures', script)

    def test_lineage_and_readme_name_the_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest, slug = self._package(tmp)
            lineage = json.loads((dest / "lineage.json").read_text())
            self.assertEqual(len(lineage["textures"]), 5)
            self.assertIn("baked texture maps",
                          (dest / "README.txt").read_text())


# ---------------------------------------------------------------- CLI


class BakeCliTests(unittest.TestCase):
    """M12/M13: the CLI gains bake + the tier flag."""

    def test_bake_and_tier_appear_in_help(self):
        import io
        from contextlib import redirect_stdout

        from skullpt import cli as cli_mod

        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                cli_mod.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        help_text = buf.getvalue()
        self.assertIn("bake", help_text)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                cli_mod.main(["settings", "--help"])
        self.assertIn("--material-tier", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                cli_mod.main(["bake", "--help"])
        self.assertIn("--resolution", buf.getvalue())

    def test_bake_command_count(self):
        # 30 commands in v1.12 + bake = 31.
        import re
        from skullpt import cli as cli_mod

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                cli_mod.main(["--help"])
        match = re.search(r"\{([a-z0-9,\-]+)\}", buf.getvalue())
        self.assertIsNotNone(match, buf.getvalue())
        commands = match.group(1).split(",")
        self.assertIn("bake", commands)
        self.assertEqual(len(commands), 31)


# ---------------------------------------------------------------- help text


class HelpTextTests(unittest.TestCase):
    """M12-M14: the How to Use index covers baking, tiers, displacement."""

    def test_help_covers_the_new_sections(self):
        text = (ROOT / "skullpt" / "qtwindows.py").read_text()
        for heading in ("<h3>Material quality</h3>",
                        "<h3>Baking PBR maps</h3>",
                        "<h3>True displacement</h3>"):
            self.assertIn(heading, text)
        self.assertIn("Glass does not bake", text)
        self.assertIn("16 times denser", text)


# ---------------------------------------------------------------- Qt


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
            window.deleteLater()
        self._app.processEvents()
        self.tmp.cleanup()

    def _bench(self):
        return _FakeBench(self.ws)


class _FakeBench:
    """A bench stand-in for window tests: actions run for real
    (same pattern as test_v19)."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.refreshed = 0
        self.selected: str | None = None

    def do_action(self, action: str, payload: dict):
        return bench_mod.act(self.workspace, action, payload)

    def refresh(self) -> None:
        self.refreshed += 1


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtTierTests(_QtCase):
    """M13: the tier combo in the Render window saves per job."""

    def test_tier_combo_saves_to_the_job(self):
        from skullpt.qtwindows import RenderWindow

        job = _job(self.ws, _mat_desc())
        bench = self._bench()
        window = RenderWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        window.set_job(job.dir.name)
        self._app.processEvents()
        # v1.14 (A8): Bake stays greyed out until the job is built -
        # this job has no .blend yet, so the button is disabled.
        self.assertFalse(window._bake_btn.isEnabled())
        window._tier_combo.setCurrentIndex(
            window._tier_combo.findData("final"))
        window._save_render_settings()
        desc = job.load_description()
        self.assertEqual(desc["render"]["tier"], "final")

    def test_settings_window_tier_saves_the_default(self):
        from skullpt.qtwindows import SettingsWindow

        window = SettingsWindow(self._bench(), self.ws)
        window.show()
        self._windows.append(window)
        self._app.processEvents()
        window._tier_combo.setCurrentIndex(
            window._tier_combo.findData("draft"))
        self._app.processEvents()
        saved = settings_mod.load_settings(self.ws)
        self.assertEqual(saved["material_tier"], "draft")


@unittest.skipUnless(_QT_TESTS, "needs PySide6")
class QtDisplacementTests(_QtCase):
    """M14: the displacement checkbox in the part editor."""

    def test_checkbox_shows_for_object_materials_only(self):
        from skullpt.qtwindows import RenderWindow

        job = _job(self.ws, _mat_desc())
        bench = self._bench()
        window = RenderWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        window.set_job(job.dir.name)
        self._app.processEvents()
        window._select_part("plank")
        self.assertTrue(window._displacement_check.isVisible())
        self.assertFalse(window._displacement_check.isChecked())
        window._select_part("bare")
        self.assertFalse(window._displacement_check.isVisible())

    def test_toggling_saves_displacement_to_the_spec(self):
        from skullpt.qtwindows import RenderWindow

        job = _job(self.ws, _mat_desc())
        bench = self._bench()
        window = RenderWindow(bench, self.ws)
        window.show()
        self._windows.append(window)
        window.set_job(job.dir.name)
        self._app.processEvents()
        window._select_part("plank")
        window._displacement_check.setChecked(True)
        window._save_part()
        desc = job.load_description()
        part = next(p for p in desc["parts"] if p["id"] == "plank")
        self.assertTrue(part["material"]["displacement"])
        self.assertEqual(part["material"]["family"], "wood")


# ---------------------------------------------------------------- e2e (real Blender)


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class BakeEndToEndTests(unittest.TestCase):
    """M12: a real build, then a real bake, through real Blender."""

    def test_bake_lands_a_pack_per_part(self):
        desc = _mat_desc()
        desc["parts"][1]["material"] = json.loads(json.dumps(METAL_SPEC))
        desc["parts"].append(
            {"id": "window", "type": "prop", "shape": "box",
             "position_cm": [80, 0, 0], "size_cm": [30, 30, 2],
             "material": json.loads(json.dumps(GLASS_SPEC))})
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, desc)
            out_dir = job.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            script = blender_export.generate_script(desc, out_dir,
                                                    job.dir.name)
            script_path = Path(tmp) / "build.py"
            script_path.write_text(script)
            run = subprocess.run(
                [blender_export.find_blender(), "--background",
                 "--factory-startup", "--python", str(script_path)],
                capture_output=True, text=True, timeout=600)
            self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
            result = bake_mod.bake_job(job, ws, resolution=256)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["resolution"], 256)
            slug = job.dir.name
            # Plank: 4 maps. Block (metal): 4 + metallic. Window:
            # glass, skipped with the reason.
            for map_name in ("color", "roughness", "normal", "height"):
                path = out_dir / f"{slug}-plank-{map_name}.png"
                self.assertTrue(path.exists(), map_name)
                self.assertGreater(path.stat().st_size, 100)
                self.assertTrue(path.read_bytes().startswith(
                    b"\x89PNG\r\n\x1a\n"), map_name)
                self.assertTrue(
                    (out_dir / f"{slug}-block-{map_name}.png").exists(),
                    map_name)
            metal_path = out_dir / f"{slug}-block-metallic.png"
            self.assertTrue(metal_path.exists())
            self.assertGreater(metal_path.stat().st_size, 0)
            self.assertFalse(
                (out_dir / f"{slug}-window-color.png").exists())
            skipped_ids = [s["id"] for s in result["skipped"]]
            self.assertIn("window", skipped_ids)
            report = json.loads(
                (job.dir / "bake-report.json").read_text())
            self.assertEqual(report["resolution"], 256)
            self.assertIn("plank", report["parts"])
            self.assertEqual(len(report["maps"]), 9)


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class DisplacementEndToEndTests(unittest.TestCase):
    """M14: a real displaced build through real Blender."""

    def test_displaced_box_is_16x_and_wired(self):
        desc = _mat_desc(displacement=True)
        # The block gets an explicit zero bevel: without it the
        # default "soften" bevel adds triangles to the measured
        # count (the estimate is the base mesh, a lower bound).
        desc["parts"][1]["bevel_cm"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            script = blender_export.generate_script(desc, out_dir, "disp")
            script_path = Path(tmp) / "build.py"
            script_path.write_text(script)
            run = subprocess.run(
                [blender_export.find_blender(), "--background",
                 "--factory-startup", "--python", str(script_path)],
                capture_output=True, text=True, timeout=600)
            self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
            stats = json.loads(
                (out_dir / "disp.stats.json").read_text())
            # The displaced plank: 12 * 16 = 192 tris. The plain
            # block stays 12.
            self.assertEqual(stats["parts"]["plank"]["tris"], 192)
            self.assertEqual(stats["parts"]["block"]["tris"], 12)
            self.assertTrue(stats["materials"]["plank"]["displacement"])
            self.assertFalse(stats["materials"]["block"]["displacement"])
            self.assertTrue((out_dir / "disp.glb").exists())

    def test_glass_without_frost_cannot_displace(self):
        desc = _mat_desc()
        desc["parts"][0]["material"] = dict(GLASS_SPEC,
                                            displacement=True)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            out_dir.mkdir()
            script = blender_export.generate_script(desc, out_dir, "gd")
            script_path = Path(tmp) / "build.py"
            script_path.write_text(script)
            run = subprocess.run(
                [blender_export.find_blender(), "--background",
                 "--factory-startup", "--python", str(script_path)],
                capture_output=True, text=True, timeout=600)
            self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
            stats = json.loads(
                (out_dir / "gd.stats.json").read_text())
            self.assertEqual(stats["parts"]["plank"]["tris"], 12)
            self.assertFalse(stats["materials"]["plank"]["displacement"])


@unittest.skipUnless(
    blender_export.find_blender() and not os.environ.get("DESIGNSHELL_SKIP_E2E"),
    "Blender not installed on this machine (or e2e skipped)")
class TierEndToEndTests(unittest.TestCase):
    """M13: measured node counts match the predicted counts per tier."""

    def _measured(self, tmp: str, tier: str) -> dict:
        desc = _mat_desc(tier=tier)
        out_dir = Path(tmp) / f"out-{tier}"
        out_dir.mkdir(exist_ok=True)
        script = blender_export.generate_script(desc, out_dir, tier)
        script_path = Path(tmp) / f"build-{tier}.py"
        script_path.write_text(script)
        run = subprocess.run(
            [blender_export.find_blender(), "--background",
             "--factory-startup", "--python", str(script_path)],
            capture_output=True, text=True, timeout=600)
        self.assertIn("DS_DONE", run.stdout, run.stdout[-2000:])
        stats = json.loads((out_dir / f"{tier}.stats.json").read_text())
        return stats["materials"]

    def test_measured_equals_predicted_at_every_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            measured = {}
            for tier in ("draft", "balanced", "final"):
                mats = self._measured(tmp, tier)
                measured[tier] = mats
                report = builder_mod.material_report(_mat_desc(tier=tier))
                for part_id, entry in report.items():
                    self.assertEqual(mats[part_id]["nodes"],
                                     entry["nodes"], (tier, part_id))
            # And the tiers actually differ on the walnut part.
            self.assertLess(measured["draft"]["plank"]["nodes"],
                            measured["final"]["plank"]["nodes"])


class DisplacementGateBuildTests(unittest.TestCase):
    """M14: a build under the displacement margin strips the flag,
    records the note, and keeps the user's setting in the file."""

    def test_build_strips_displacement_and_keeps_the_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _workspace(tmp)
            job = _job(ws, _mat_desc(displacement=True))
            job.approve()
            from skullpt import exporters, governor

            def stub_exporter(desc, out_dir, cancel=None, progress=None):
                return []

            with mock.patch("skullpt.governor.free_ram_mb",
                            return_value=1200), \
                    mock.patch("skullpt.governor.adaptive_min_free_mb",
                               return_value=1000), \
                    mock.patch.dict(exporters.EXPORTERS,
                                    {"blender_scene": stub_exporter}):
                report = builder_mod.build_job(job, ws)
            notes = report.get("notes") or []
            self.assertTrue(any("displacement" in n for n in notes),
                            report)
            # The file keeps the setting: the strip was in-memory only.
            saved = job.load_description()
            part = next(p for p in saved["parts"] if p["id"] == "plank")
            self.assertTrue(part["material"]["displacement"])
            log = (job.dir / "session.jsonl").read_text()
            self.assertIn("displacement_skipped", log)


if __name__ == "__main__":
    unittest.main()
