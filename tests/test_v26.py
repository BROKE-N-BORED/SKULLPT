"""SKULLPT v1.16 tests: the second user-audit fix round (D1-D14).

Backend tests run everywhere; Qt tests skip without PySide6. D1 is
the P0: the updater restart-loop. Its tests drive the full cycle
against the fake install + fake release server from test_v25 -
stage, apply, then simulate the next launches exactly the way
qtbench.main() calls handle_pending_at_startup, twice.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

from skullpt import bench as bench_mod  # noqa: E402
from skullpt import governor  # noqa: E402
from skullpt import imagegen  # noqa: E402
from skullpt import qtbench  # noqa: E402
from skullpt import settings as settings_mod  # noqa: E402
from skullpt import updater as updater_mod  # noqa: E402

from test_v25 import (  # noqa: E402
    _FakeBench,
    _FakeReleaseServer,
    _QtCase,
    _dead_pid,
    _job,
    _make_fake_install,
    _make_release_zip,
    _release_result,
    _workspace,
)


# ------------------------------------------------------------- version


class VersionTests(unittest.TestCase):
    """v1.16 everywhere."""

    def test_version_is_116(self):
        from skullpt import __version__

        self.assertEqual(__version__, "1.16")
        self.assertIn("v1.16", (ROOT / "README.md").read_text())
        self.assertIn("## v1.16", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("v1.16", (ROOT / "DESIGN.md").read_text())

    def test_changelog_names_the_round(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        section = text.split("## v1.16", 1)[1].split("## v1.15", 1)[0]
        for item in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8",
                     "D9", "D10", "D11", "D12", "D13", "D14"):
            self.assertIn(item, section)


# ----------------------------------------------------------------- D1


class D1UpdaterLoopTests(unittest.TestCase):
    """D1 (P0): an applied update must never restart-loop.

    The v1.15 bug: run_update's success path never dropped the
    staged .pending-update/, so the relaunched Bench's
    handle_pending_at_startup saw the leftover stage, spawned the
    updater again, and exited - forever.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.install = _make_fake_install(self.root)
        self.zip_bytes = _make_release_zip("9.9")
        digest = hashlib.sha256(self.zip_bytes).hexdigest()
        self.server = _FakeReleaseServer({
            "/skullpt-v9.9.zip": self.zip_bytes,
            "/skullpt-v9.9.zip.sha256":
                f"{digest}  skullpt-v9.9.zip\n".encode()})

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def _stage(self) -> None:
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), self.install)
        self.assertTrue(updater_mod.stage_is_pending(self.install))

    def test_applied_update_never_loops(self):
        """The full cycle: stage, apply, then two launches exactly
        as main() makes them. Both must open the window."""
        self._stage()
        self.assertEqual(
            updater_mod.run_update(self.install, _dead_pid()), 0)
        # The new version is in place...
        self.assertIn('__version__ = "9.9"',
                      (self.install / "skullpt" / "__init__.py")
                      .read_text())
        # ...and the stage went with the apply (fix layer a).
        self.assertFalse(updater_mod.stage_is_pending(self.install))
        # Two simulated launches: no updater spawned, no "restarting".
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            self.assertIsNone(
                updater_mod.handle_pending_at_startup(self.install))
            self.assertIsNone(
                updater_mod.handle_pending_at_startup(self.install))
            spawn.assert_not_called()
        # The Bench still gets to report the update that landed.
        self.assertEqual(
            updater_mod.consume_update_marker(self.install),
            ("9.9", None))

    def test_deferred_update_still_applies(self):
        """The v1.15 behavior for a genuinely waiting update must
        not break: stage, next launch applies it."""
        self._stage()
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            self.assertEqual(
                updater_mod.handle_pending_at_startup(self.install),
                "restarting")
            spawn.assert_called_once()
            self.assertEqual(spawn.call_args.args[0], self.install)
        # The spawned updater owns the stage now; it is still there.
        self.assertTrue(updater_mod.stage_is_pending(self.install))

    def test_leftover_stage_with_marker_is_dropped(self):
        """Fix layer b, marker branch: a stage left behind by an
        apply that already wrote its updated marker (the exact
        v1.15 leftover) is dropped, not re-applied."""
        self._stage()
        (self.install / ".updated-to-9.9").write_text("9.9\n")
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            self.assertIsNone(
                updater_mod.handle_pending_at_startup(self.install))
            spawn.assert_not_called()
        self.assertFalse(updater_mod.stage_is_pending(self.install))

    def test_stage_matching_running_version_is_dropped(self):
        """Fix layer b, version branch: the staged version equals
        the installed version, so it already landed - drop it."""
        install = _make_fake_install(self.root / "second",
                                     version="9.9")
        updater_mod.download_and_stage(
            _release_result(self.server, "9.9"), install)
        self.assertTrue(updater_mod.stage_is_pending(install))
        with mock.patch.object(updater_mod, "spawn_updater") as spawn:
            self.assertIsNone(
                updater_mod.handle_pending_at_startup(install))
            spawn.assert_not_called()
        self.assertFalse(updater_mod.stage_is_pending(install))


# ----------------------------------------------------------------- D3


class D3DebugReportTests(unittest.TestCase):
    """D3: the Debug Report's engine facts are the status bar's,
    read from the same source (workspace_state)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _engines_line(self, report: str) -> str:
        return next(line for line in report.splitlines()
                    if line.startswith("engines:"))

    def test_report_agrees_with_workspace_state(self):
        report = bench_mod.debug_report(self.ws)
        engines = bench_mod.workspace_state(self.ws)["engines"]
        line = self._engines_line(report)
        for key, value in engines.items():
            self.assertIn(f"{key}={'ready' if value else 'missing'}",
                          line)

    def test_no_token_says_missing(self):
        line = self._engines_line(bench_mod.debug_report(self.ws))
        self.assertIn("hf_token=missing", line)

    def test_saved_token_says_ready_and_never_prints_it(self):
        (self.ws / "hf_token.key").write_text("TEST-TOKEN-VALUE-9\n")
        report = bench_mod.debug_report(self.ws)
        self.assertIn("hf_token=ready", self._engines_line(report))
        self.assertNotIn("TEST-TOKEN-VALUE-9", report)


# ----------------------------------------------------------------- D4


class D4FailedBatchTests(unittest.TestCase):
    """D4: a round refused before any picture exists records the
    reason on the batch, where the Images window reads it back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _batch(self, prompt: str = "a red door"):
        return imagegen.new_batch(self.ws, prompt)

    def test_refused_round_records_its_reason(self):
        _batch, bdir = self._batch()
        with mock.patch.object(imagegen, "find_engine",
                               return_value=None):
            with self.assertRaises(imagegen.ImageGenError):
                imagegen.generate(self.ws, bdir)
        saved = imagegen.load_batch(bdir)
        self.assertIn("is not installed", saved.get("error") or "")
        # And the workspace state carries it to the Images window.
        state = bench_mod.workspace_state(self.ws)
        self.assertEqual(len(state["batches"]), 1)
        self.assertIn("is not installed",
                      state["batches"][0]["error"] or "")

    def test_later_success_clears_the_reason(self):
        batch, bdir = self._batch()
        batch["error"] = "an older refusal"
        imagegen.save_batch(bdir, batch)
        with mock.patch.object(imagegen, "_generate_round",
                               return_value=imagegen.load_batch(bdir)):
            result = imagegen.generate(self.ws, bdir)
        self.assertNotIn("error", result)
        self.assertNotIn("error", imagegen.load_batch(bdir))

    def test_cancel_records_nothing(self):
        _batch, bdir = self._batch()
        with mock.patch.object(
                imagegen, "_generate_round",
                side_effect=governor.Cancelled("cancelled")):
            with self.assertRaises(governor.Cancelled):
                imagegen.generate(self.ws, bdir)
        self.assertNotIn("error", imagegen.load_batch(bdir))


# ---------------------------------------------------------------- D11


class D11BatchOrderTests(unittest.TestCase):
    """D11: batches list newest first, by creation time - not by
    folder name, which sorts by prompt words."""

    def test_state_batches_are_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            zebra, _zdir = imagegen.new_batch(ws, "zebra stripes")
            apple, _adir = imagegen.new_batch(ws, "apple pie")
            # Folder-name order would put zebra first; creation
            # order says apple is the newer batch.
            zebra["created"] = "2026-11-19T01:00:00+0000"
            apple["created"] = "2026-11-19T02:00:00+0000"
            imagegen.save_batch(ws / "images" / zebra["slug"], zebra)
            imagegen.save_batch(ws / "images" / apple["slug"], apple)
            state = bench_mod.workspace_state(ws)
            self.assertEqual(
                [b["slug"] for b in state["batches"]],
                [apple["slug"], zebra["slug"]])


# ----------------------------------------------------------------- D5


class D5NoSeatWordingTests(unittest.TestCase):
    """D5: no visible 'seat' wording. Internal identifiers may
    keep the word; text a user reads may not."""

    def test_setup_checklist_details_have_no_seat(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            items = bench_mod.missing_setup_items(ws)
            details = [item["detail"] for item in items]
            self.assertTrue(details)
            for detail in details:
                self.assertNotIn("seat", detail.lower())
            joined = " ".join(details)
            self.assertIn("plans your builds and checks the results",
                          joined)
            self.assertIn("fixes drafts that fail a check", joined)

    def test_the_audited_strings_are_gone(self):
        # The exact visible strings the audit flagged, by file.
        checks = {
            "skullpt/qtbench.py": ("planner seat",),
            "skullpt/critic.py": ("critic seat",),
            "skullpt/cli.py": ("planner seat",),
            "skullpt/qtwindows.py": (
                "planner and critic seats", "repair seat"),
        }
        for rel, needles in checks.items():
            text = (ROOT / rel).read_text()
            for needle in needles:
                self.assertNotIn(needle, text, f"{needle!r} in {rel}")


# ---------------------------------------------------------------- D13


class D13GovernorWordingTests(unittest.TestCase):
    """D13: the refusal says how much RAM is free, how much is
    needed, and that waiting is the fix - no 'floor', no 'thrash'."""

    def test_refusal_wording_is_plain(self):
        with tempfile.TemporaryDirectory() as td:
            lock = governor.HeavyJobLock(Path(td) / "heavy.lock")
            with self.assertRaises(governor.GovernorError) as ctx:
                lock.acquire(min_free_mb=10 ** 9)
        message = str(ctx.exception)
        self.assertIn("MB RAM free", message)
        self.assertIn("wait until other programs close", message)
        self.assertNotIn("floor", message)
        self.assertNotIn("thrash", message)

    def test_debug_report_ram_line_is_plain(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            report = bench_mod.debug_report(ws)
        line = next(l for l in report.splitlines()
                    if l.startswith("free RAM:"))
        self.assertIn("must stay free", line)
        self.assertNotIn("floor", line)


# ---------------------------------------------------------------- D14


class D14DemoLineTests(unittest.TestCase):
    """D14: the demo's completion detail says it once. The run
    line in the Bench adds 'demo: done - ' in front of the detail,
    so the detail itself must not say 'demo done' too."""

    def test_demo_detail_says_it_once(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _workspace(td)
            fake_report = {"ok": True, "outputs": ["panel.svg"],
                           "problems": []}
            with mock.patch("skullpt.builder.build_job",
                            return_value=fake_report):
                result = bench_mod.act(ws, "demo", {})
            self.assertTrue(result["ok"], result)
            run_id = result["run"]
            deadline = time.time() + 15
            run = None
            while time.time() < deadline:
                run = bench_mod._RUNS.get(run_id)
                if run and run["status"] in ("done", "error"):
                    break
                time.sleep(0.05)
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "done", run)
            detail = run["detail"]
            self.assertTrue(detail.endswith("is in the Library"),
                            detail)
            self.assertNotIn("demo done", detail)
            line = f"demo: done - {detail}"
            self.assertEqual(line.count("done"), 1, line)


# ------------------------------------------------------------ Qt: D2


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D2FiledJobRenderTests(_QtCase):
    """D2 (P1): a filed job opens read-only in Render - editor and
    dial disabled up front, with the reason where the user looks."""

    def _render(self, job_name: str):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window.set_job(job_name)
        self._pump(200)
        return window

    def test_filed_job_editor_is_read_only_up_front(self):
        job = _job(self.ws)
        job.transition("filed")
        window = self._render(job.dir.name)
        self.assertTrue(window._job_filed)
        part_id = window._desc["parts"][0]["id"]
        window._fill_editor(part_id)
        self._pump(100)
        for spin in (*window._pos_spins, *window._size_spins,
                     window._bevel_spin, window._radius_spin):
            self.assertFalse(spin.isEnabled())
        self.assertFalse(window._save_part_btn.isEnabled())
        self.assertFalse(window._dial_btn.isEnabled())
        self.assertEqual(
            window._editor_note.text(),
            "Filed jobs live in the Library. "
            "Duplicate it to change it.")
        # The dial does not open for a filed job.
        window._open_dial()
        self.assertIsNone(window._dial)

    def test_open_job_editor_stays_editable(self):
        job = _job(self.ws)
        window = self._render(job.dir.name)
        self.assertFalse(window._job_filed)
        part_id = window._desc["parts"][0]["id"]
        window._fill_editor(part_id)
        self._pump(100)
        self.assertTrue(window._pos_spins[0].isEnabled())
        self.assertTrue(window._save_part_btn.isEnabled())
        self.assertTrue(window._dial_btn.isEnabled())


# ----------------------------------------------------------- Qt: D6


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D6RenderSettingsTests(_QtCase):
    """D6: no job loaded - the render settings are the workspace
    defaults, disabled, and the note says where they change."""

    def test_no_job_disables_controls_and_says_defaults(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        for widget in (window._engine_combo, window._res_spin,
                       window._samples_spin, window._denoise_check,
                       window._tier_combo, window._save_render_btn):
            self.assertFalse(widget.isEnabled(), widget)
        self.assertEqual(
            window._settings_note.text(),
            "These are the workspace defaults. To change them, "
            "open the Settings window.")

    def test_job_loaded_enables_controls_and_says_job(self):
        from skullpt import qtwindows

        job = _job(self.ws)
        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window.set_job(job.dir.name)
        self._pump(200)
        for widget in (window._engine_combo, window._res_spin,
                       window._samples_spin, window._denoise_check,
                       window._tier_combo, window._save_render_btn):
            self.assertTrue(widget.isEnabled(), widget)
        self.assertTrue(window._settings_note.text().startswith(
            "These are this job's own settings."))


# ---------------------------------------------------------- Qt: D10


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D10BlankFieldsTests(_QtCase):
    """D10: no part picked - the value fields read blank, not 0.0.
    A picked part shows its real values again."""

    def test_fields_blank_until_a_part_is_picked(self):
        from skullpt import qtwindows

        job = _job(self.ws)
        window = self._track(
            qtwindows.RenderWindow(_FakeBench(self.ws), self.ws))
        window.show()
        self._pump(200)
        window.set_job(job.dir.name)
        self._pump(200)
        for spin in (*window._pos_spins, *window._size_spins,
                     window._bevel_spin, window._radius_spin):
            self.assertEqual(spin.text(), "")
        part_id = window._desc["parts"][0]["id"]
        window._fill_editor(part_id)
        self._pump(100)
        self.assertNotEqual(window._pos_spins[0].text(), "")


# ----------------------------------------------------- Qt: D4 + D11


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D4ImagesWindowTests(_QtCase):
    """D4 + D11 in the Images window: the failed batch is marked
    in the list, its reason shows in the pane, and the list is
    newest first."""

    def test_failed_batch_is_marked_and_explained(self):
        from PySide6 import QtWidgets

        from skullpt import qtwindows

        older, _odir = imagegen.new_batch(self.ws, "older batch")
        older["created"] = "2026-11-19T01:00:00+0000"
        imagegen.save_batch(self.ws / "images" / older["slug"], older)
        failed, _fdir = imagegen.new_batch(self.ws, "newer batch")
        failed["created"] = "2026-11-19T02:00:00+0000"
        failed["error"] = ("only 100 MB RAM free, and this needs "
                           "1190 MB free to start")
        imagegen.save_batch(self.ws / "images" / failed["slug"],
                            failed)
        window = self._track(
            qtwindows.ImagesWindow(_FakeBench(self.ws), self.ws))
        window.show()
        window.refresh()
        self._pump(200)
        # Newest first, and the failed one says so.
        self.assertEqual(window._batch_list.count(), 2)
        self.assertTrue(
            window._batch_list.item(0).text().endswith("(failed)"),
            window._batch_list.item(0).text())
        # The pane shows the reason, not a blank grid.
        window._show_batch(self.ws / "images" / failed["slug"])
        self._pump(100)
        texts = []
        for i in range(window._grid.count()):
            widget = window._grid.itemAt(i).widget()
            if isinstance(widget, QtWidgets.QLabel):
                texts.append(widget.text())
        joined = "\n".join(texts)
        self.assertIn("This batch failed", joined)
        self.assertIn("only 100 MB RAM free", joined)


# ------------------------------------------------------ Qt: D7 + D9


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D7D9TileTests(_QtCase):
    """D7: the Engines tile lists all eight statuses. D9: the
    Latest render tile packs its content to the top."""

    def test_engines_tile_lists_all_eight(self):
        window = self._bench()
        text = window._engine_tile_text.text()
        for label in ("Blender", "Image engine", "Image model",
                      "Gemini key", "Groq key", "Ollama", "HF token",
                      "Character Studio"):
            self.assertIn(label, text)
        self.assertEqual(text.count("\n"), 7)

    def test_renders_tile_is_top_packed(self):
        from PySide6 import QtCore

        window = self._bench()
        alignment = window._renders_tile_image.alignment()
        self.assertTrue(alignment & QtCore.Qt.AlignTop)
        layout = window._renders_tile_image.parentWidget().layout()
        self.assertIsNotNone(layout)
        self.assertIsNotNone(
            layout.itemAt(layout.count() - 1).spacerItem())


# ------------------------------------------------------------ Qt: D8


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D8LibraryEmptyTests(_QtCase):
    """D8: an empty Library explains how entries get there."""

    def test_empty_library_explains_itself(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.LibraryWindow(_FakeBench(self.ws), self.ws))
        window.show()
        window.refresh()
        self._pump(200)
        self.assertTrue(
            window._detail.text().startswith("Nothing filed yet"),
            window._detail.text())
        self.assertIn("Jobs window", window._detail.text())

    def test_search_with_no_match_says_so(self):
        from skullpt import qtwindows

        window = self._track(
            qtwindows.LibraryWindow(_FakeBench(self.ws), self.ws))
        window.show()
        window._search.setText("zzz-no-match")
        window.refresh()
        self._pump(100)
        self.assertEqual(window._detail.text(),
                         "No entries match your search.")


# ----------------------------------------------------------- Qt: D12


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D12SettingsDateTests(_QtCase):
    """D12: the last-render-engine line shows a plain date."""

    def test_last_engine_line_shows_a_plain_date(self):
        from skullpt import qtwindows

        settings_mod.record_render_engine(self.ws, "CYCLES")
        window = self._track(
            qtwindows.SettingsWindow(_FakeBench(self.ws), self.ws))
        window.show()
        window.refresh()
        self._pump(200)
        text = window._last_engine_label.text()
        self.assertIn("CYCLES", text)
        self.assertRegex(text, r"\(\d{2} [A-Z][a-z]{2} \d{4}\)")
        self.assertNotIn("T", text.split("(", 1)[1])


# ------------------------------------------------------------ Qt: D5


@unittest.skipUnless(qtbench.QT_AVAILABLE, "PySide6 not installed")
class D5SettingsLabelsTests(_QtCase):
    """D5 in Settings: the key rows say what the keys do."""

    def test_engine_labels_have_no_seat(self):
        from skullpt import qtwindows

        for label in qtwindows._ENGINE_LABELS.values():
            self.assertNotIn("seat", label.lower())
        self.assertIn("plans and checks your builds",
                      qtwindows._ENGINE_LABELS["gemini_key"])
        self.assertIn("fixes drafts that fail a check",
                      qtwindows._ENGINE_LABELS["groq_key"])


if __name__ == "__main__":
    unittest.main()
