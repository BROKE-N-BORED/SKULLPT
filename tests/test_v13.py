"""v1.3 — the GUI installer, the python3-venv self-heal, the no-terminal audit.

Joshua's rule: no terminal at all to run or install anything. v1.3
installs behind a progress window (xenity/zenity/yad — the first one
found, in the order Cinnamon's own spices probe), fixes a missing
python3-venv itself with one pkexec call (a GUI password prompt),
and says nothing that asks the user to type a command.

The installer tests below run the REAL install.sh from a staged copy
of the whole tree, with stub commands on PATH: a stub python3 (fakes
`-m venv` and the test run so installs stay fast), stub dialog tools
(that log every invocation and, for --progress, keep the input they
were fed), and a stub pkexec (that logs its command). Nothing here
touches the real system: HOME points at a temp dir, and the stubs
never install anything.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Stub commands ----------------------------------------------------------

# Stub python3: delegates to the real interpreter except for `-m venv`
# (faked: creates .venv/bin/python from the fake-venv-python stub) and
# `-m unittest discover` (faked: a green summary line). With
# STUB_VENV_FAIL_FIRST=1 the first venv creation fails with the real
# ensurepip error text, so the self-heal path can be exercised.
STUB_PYTHON3 = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  if [ "$STUB_VENV_FAIL_FIRST" = "1" ] && [ ! -f "$STUB_STATE/venv-failed-once" ]; then
    touch "$STUB_STATE/venv-failed-once"
    echo "Error: ensurepip is not available" >&2
    echo "The virtual environment was not created successfully because ensurepip is not" >&2
    echo "available.  On Debian/Ubuntu systems, you need to install the python3-venv" >&2
    echo "package using the following command." >&2
    exit 1
  fi
  mkdir -p "$3/bin"
  cp "$STUB_DIR/fake-venv-python" "$3/bin/python"
  chmod +x "$3/bin/python"
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "unittest" ]; then
  if [ "$STUB_UNITTEST_FAIL" = "1" ]; then
    echo "Ran 1 test in 0.0s"
    echo ""
    echo "FAILED (errors=1)"
    exit 1
  fi
  echo "Ran 1 test in 0.0s"
  echo ""
  echo "OK"
  exit 0
fi
exec /usr/bin/python3 "$@"
"""

# Fake .venv python: PySide6 is "missing" until a pip install creates
# the marker file, then it imports and reports a version. Also fakes
# the unittest run (the installer uses the venv python for its test
# run when a display is set).
FAKE_VENV_PYTHON = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "unittest" ]; then
  if [ "$STUB_UNITTEST_FAIL" = "1" ]; then
    echo "Ran 1 test in 0.0s"
    echo ""
    echo "FAILED (errors=1)"
    exit 1
  fi
  echo "Ran 1 test in 0.0s"
  echo ""
  echo "OK"
  exit 0
fi
MARKER="$(dirname "$0")/../pyside6-installed"
case "$*" in
  *"-m pip install"*)
    touch "$MARKER"
    echo "Successfully installed PySide6-Essentials"
    exit 0
    ;;
esac
case "$*" in
  *"import PySide6"*)
    if [ -f "$MARKER" ]; then
      case "$*" in
        *"__version__"*) echo "6.12.0" ;;
      esac
      exit 0
    fi
    echo "ModuleNotFoundError: No module named 'PySide6'" >&2
    exit 1
    ;;
esac
exec /usr/bin/python3 "$@"
"""

# Stub dialog tool (one copy per name: xenity, zenity, yad). Logs every
# invocation (tool name + args) to $DIALOG_LOG. For --progress it keeps
# everything fed on stdin in $DIALOG_DIR/progress-input.txt, so tests
# can assert the percentages and labels flowed. --question exits with
# $ZENITY_QUESTION_EXIT (0 = yes, 1 = no).
STUB_DIALOG = """#!/bin/sh
echo "$(basename "$0") $*" >> "$DIALOG_LOG"
case "$*" in
  *--progress*)
    cat > "$DIALOG_DIR/progress-input.txt"
    exit 0
    ;;
  *--question*)
    exit "${ZENITY_QUESTION_EXIT:-0}"
    ;;
esac
exit 0
"""

# Stub pkexec: logs its command line, exits with $PKEXEC_EXIT.
STUB_PKEXEC = """#!/bin/sh
echo "pkexec $*" >> "$PKEXEC_LOG"
exit "${PKEXEC_EXIT:-0}"
"""


class _StagedInstall(unittest.TestCase):
    """Runs the real install.sh from a staged copy, with stub commands."""

    DIALOGS = ("xenity", "zenity", "yad")

    def _stage(self, tmp, dialogs=("zenity",), run_sh_stub=False):
        """Copy the whole tree into tmp/stage; build the stub bin dir.

        Returns (stage, home, env, paths) where paths holds the log
        and marker locations the tests assert on.
        """
        tmp = Path(tmp)
        stage = tmp / "stage"
        shutil.copytree(ROOT, stage,
                        ignore=shutil.ignore_patterns("__pycache__"))
        (stage / "install.sh").chmod(0o755)
        (stage / "run.sh").chmod(0o755)
        if run_sh_stub:
            (stage / "run.sh").write_text(
                "#!/bin/sh\n"
                "echo LAUNCHED >> \"$RUN_SH_MARKER\"\n")
            (stage / "run.sh").chmod(0o755)
        home = tmp / "home"
        home.mkdir()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").write_text(STUB_PYTHON3)
        (bin_dir / "python3").chmod(0o755)
        (bin_dir / "fake-venv-python").write_text(FAKE_VENV_PYTHON)
        (bin_dir / "fake-venv-python").chmod(0o755)
        (bin_dir / "pkexec").write_text(STUB_PKEXEC)
        (bin_dir / "pkexec").chmod(0o755)
        for name in dialogs:
            (bin_dir / name).write_text(STUB_DIALOG)
            (bin_dir / name).chmod(0o755)
        state_dir = tmp / "state"
        state_dir.mkdir()
        dialog_dir = tmp / "dialogs"
        dialog_dir.mkdir()
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        env["HOME"] = str(home)
        env["DISPLAY"] = ":99"
        env["STUB_DIR"] = str(bin_dir)
        env["STUB_STATE"] = str(state_dir)
        env["DIALOG_LOG"] = str(dialog_dir / "dialog.log")
        env["DIALOG_DIR"] = str(dialog_dir)
        env["PKEXEC_LOG"] = str(tmp / "pkexec.log")
        env["RUN_SH_MARKER"] = str(tmp / "run-sh-launched")
        env["ZENITY_QUESTION_EXIT"] = "1"  # "No" unless a test says yes
        paths = {
            "dialog_log": dialog_dir / "dialog.log",
            "progress_input": dialog_dir / "progress-input.txt",
            "pkexec_log": tmp / "pkexec.log",
            "run_sh_marker": tmp / "run-sh-launched",
            "install_log": stage / "install.log",
            "desktop_launcher": home / "Desktop" / "SKULLPT.desktop",
            "apps_launcher":
                home / ".local" / "share" / "applications" / "SKULLPT.desktop",
            "command_link": home / ".local" / "bin" / "skullpt",
            "venv_python": stage / ".venv" / "bin" / "python",
            "pyside6_marker": stage / ".venv" / "pyside6-installed",
        }
        return stage, home, env, paths

    def _run_install(self, stage, env, *args):
        return subprocess.run(["bash", "install.sh", *args], cwd=stage,
                              env=env, capture_output=True, text=True,
                              timeout=120)

    def _dialog_calls(self, paths):
        if not paths["dialog_log"].exists():
            return []
        return paths["dialog_log"].read_text().splitlines()

    def _assert_installed(self, paths):
        """The install's durable results: launcher, command, venv, log."""
        self.assertTrue(paths["install_log"].exists())
        self.assertTrue(paths["desktop_launcher"].exists())
        self.assertTrue(paths["apps_launcher"].exists())
        self.assertTrue(paths["command_link"].is_symlink())
        self.assertTrue(paths["venv_python"].exists())
        self.assertTrue(paths["pyside6_marker"].exists())


class GuiInstallerTests(_StagedInstall):
    """The GUI installer, run for real against stub dialogs."""

    def test_gui_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = self._dialog_calls(paths)
            kinds = [c.split(" ", 1)[1] for c in calls]
            self.assertTrue(any(k.startswith("--progress") for k in kinds),
                            f"no --progress call in {calls}")
            self.assertTrue(any(k.startswith("--info") for k in kinds),
                            f"no --info call in {calls}")
            self.assertTrue(any(k.startswith("--question") for k in kinds),
                            f"no --question call in {calls}")
            self.assertFalse(any("--error" in k or "--warning" in k
                                for k in kinds), f"unexpected dialog: {calls}")
            question = [k for k in kinds if k.startswith("--question")][0]
            self.assertIn("SKULLPT is installed. Open it now?", question)
            # The progress dialog was fed the step labels + percentages.
            progress = paths["progress_input"].read_text()
            self.assertIn("# Checking Python\n5", progress)
            self.assertIn("# Setting up the Bench window\n15", progress)
            self.assertIn("# Done\n100", progress)
            # Everything is logged, and the durable results landed.
            self.assertIn("# Checking Python",
                          paths["install_log"].read_text())
            self._assert_installed(paths)
            # No system package was needed: pkexec never ran.
            self.assertFalse(paths["pkexec_log"].exists())

    def test_gui_question_yes_launches_run_sh(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp, run_sh_stub=True)
            env["ZENITY_QUESTION_EXIT"] = "0"
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            deadline = time.time() + 5
            while not paths["run_sh_marker"].exists() \
                    and time.time() < deadline:
                time.sleep(0.1)
            self.assertTrue(paths["run_sh_marker"].exists(),
                            "run.sh was not launched after answering Yes")

    def test_venv_failure_heals_via_pkexec_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            env["STUB_VENV_FAIL_FIRST"] = "1"
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            # Exactly one pkexec call, with exactly the intended command.
            expected_pkg = f"python3.{sys.version_info.minor}-venv"
            expected = ("pkexec sh -c apt-get update && "
                        "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                        + expected_pkg)
            self.assertTrue(paths["pkexec_log"].exists())
            lines = paths["pkexec_log"].read_text().splitlines()
            self.assertEqual(lines, [expected])
            # The retry worked: the venv exists, PySide6 installed, and
            # the install finished normally (info + question, no warning).
            self._assert_installed(paths)
            calls = self._dialog_calls(paths)
            self.assertTrue(any("--info" in c for c in calls), calls)
            self.assertFalse(any("--warning" in c or "--error" in c
                                for c in calls), calls)

    def test_pkexec_cancelled_warns_and_install_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            env["STUB_VENV_FAIL_FIRST"] = "1"
            env["PKEXEC_EXIT"] = "127"  # user cancelled the prompt
            res = self._run_install(stage, env, "--gui")
            # The install itself still completes (exit 0); only the
            # Bench window is unavailable.
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = self._dialog_calls(paths)
            warnings = [c for c in calls if "--warning" in c]
            self.assertEqual(len(warnings), 1, calls)
            self.assertIn(
                "SKULLPT needs one system package to install — "
                "re-run the installer and allow it when asked.",
                warnings[0])
            # No success dialog and no open-it-now question: the Bench
            # cannot open without the venv.
            self.assertFalse(any("--info" in c for c in calls), calls)
            self.assertFalse(any("--question" in c for c in calls), calls)
            # The launcher still landed (the install finished); the venv
            # did not (the heal was declined).
            self.assertTrue(paths["desktop_launcher"].exists())
            self.assertFalse(paths["venv_python"].exists())

    def test_no_dialog_tool_falls_back_to_terminal(self):
        real = [d for d in self.DIALOGS if shutil.which(d)]
        if real:
            self.skipTest(f"real dialog tool present in sandbox: {real}")
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp, dialogs=())
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("installing in the terminal instead", res.stdout)
            self.assertFalse(paths["dialog_log"].exists())
            self._assert_installed(paths)

    def test_broken_mktemp_does_not_break_the_install(self):
        # A broken TMPDIR makes mktemp fail; the installer falls back
        # to files in the install folder instead of failing the venv
        # and the test run. (Found by the v1.3 verification: a bad
        # TMPDIR made the whole venv step report "could not be
        # created" when the venv tool had never even run.)
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            mktemp_stub = Path(stage).parent / "bin" / "mktemp"
            mktemp_stub.write_text("#!/bin/sh\nexit 1\n")
            mktemp_stub.chmod(0o755)
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self._assert_installed(paths)
            # The fallback files are cleaned up, not left behind.
            self.assertFalse((stage / ".venv-error.log").exists())
            self.assertFalse((stage / ".test-output.log").exists())

    def test_failing_suite_keeps_its_output_where_the_user_can_find_it(self):
        # When the install-time test run fails, the old message said
        # "see above" — but the log only showed the last three lines
        # and the evidence was deleted, which is no help at all in
        # the GUI installer. The full output is now kept as
        # test-output.log in the install folder, and the message
        # names it. The install still completes either way.
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            env["STUB_UNITTEST_FAIL"] = "1"
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self._assert_installed(paths)
            kept = stage / "test-output.log"
            self.assertTrue(kept.exists())
            self.assertIn("FAILED", kept.read_text())
            log_text = paths["install_log"].read_text()
            self.assertIn("test-output.log", log_text)
            self.assertNotIn("see above", log_text)

    def test_auto_detect_gui_without_a_tty(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp)
            # No --gui flag; stdout is a pipe (no tty), a display is
            # set, and a dialog tool is on PATH: GUI mode engages.
            res = self._run_install(stage, env)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = self._dialog_calls(paths)
            self.assertTrue(any("--progress" in c for c in calls), calls)
            self.assertNotIn("installing in the terminal instead",
                             res.stdout)

    def test_xenity_preferred_over_zenity(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(
                tmp, dialogs=("xenity", "zenity"))
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = self._dialog_calls(paths)
            self.assertTrue(calls)
            self.assertTrue(all(c.startswith("xenity ") for c in calls),
                            calls)

    def test_yad_used_when_it_is_the_only_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, home, env, paths = self._stage(tmp, dialogs=("yad",))
            res = self._run_install(stage, env, "--gui")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            calls = self._dialog_calls(paths)
            self.assertTrue(calls)
            self.assertTrue(all(c.startswith("yad ") for c in calls), calls)


class GuiAndPkexecTextTests(unittest.TestCase):
    """Text-level checks on the GUI mode and the pkexec heal."""

    def setUp(self):
        self.install = (ROOT / "install.sh").read_text()

    def test_gui_and_terminal_flags_exist(self):
        self.assertIn("--gui) GUI_WANTED=", self.install)
        self.assertIn("--terminal) GUI_WANTED=", self.install)
        self.assertIn("SKULLPT_GUI_MODE", self.install)

    def test_auto_detect_needs_no_tty_display_and_dialog(self):
        self.assertIn('[ ! -t 1 ]', self.install)
        self.assertIn('HAS_DISPLAY', self.install)
        self.assertIn('DIALOG_CMD', self.install)

    def test_dialog_probe_order_is_xenity_zenity_yad(self):
        self.assertIn("for tool in xenity zenity yad; do", self.install)

    def test_pkexec_heal_command_is_exact(self):
        self.assertIn(
            'pkexec sh -c "apt-get update && '
            'DEBIAN_FRONTEND=noninteractive apt-get install -y $*"',
            self.install)

    def test_venv_heal_retries_creation(self):
        # ensure_venv tries venv creation twice: once, then again after
        # the pkexec heal.
        self.assertEqual(self.install.count("python3 -m venv .venv"), 2)
        self.assertIn('apt_install_via_pkexec "$VENV_PKG"', self.install)

    def test_opt_installs_use_pkexec_not_sudo(self):
        self.assertNotIn("sudo", self.install)
        self.assertIn("pkexec sh -c 'rm -rf /opt/blender", self.install)
        self.assertIn("pkexec sh -c 'rm -rf /opt/sd.cpp", self.install)

    def test_progress_dialog_flags(self):
        self.assertIn("--progress --title=\"Installing SKULLPT\"",
                      self.install)
        self.assertIn("--auto-close --no-cancel", self.install)

    def test_success_and_failure_dialog_text(self):
        self.assertIn("--info --title=\"SKULLPT installed\"", self.install)
        self.assertIn("SKULLPT is installed. Open it now?", self.install)
        self.assertIn("--error --title=\"SKULLPT install failed\"",
                      self.install)
        self.assertIn("install.log in the SKULLPT folder", self.install)


class NoTerminalAuditTests(unittest.TestCase):
    """No user-facing string presents a terminal command as a required
    step. The install/run path must need none at all."""

    # A command the user would have to type.
    FORBIDDEN = ("sudo", "./install.sh", "./run.sh", "pip install")

    def _message_lines(self, text):
        """Lines that reach the user from a shell script: echo/printf
        output and message-variable assignments. Header comments and
        usage blocks are reference documentation, not instructions."""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "echo " in line or "printf " in line:
                yield line
            elif re.match(r"\s*(QT_FIX|WARNING|reason)=", line):
                yield line

    def test_shell_script_messages(self):
        for name in ("install.sh", "run.sh"):
            text = (ROOT / name).read_text()
            for line in self._message_lines(text):
                for bad in self.FORBIDDEN:
                    self.assertNotIn(bad, line, f"{name}: {line.strip()}")

    def test_python_modules(self):
        for path in sorted((ROOT / "skullpt").glob("*.py")):
            text = path.read_text()
            self.assertNotIn("sudo", text, str(path))
            self.assertNotIn("./install.sh", text, str(path))
            self.assertNotIn("./run.sh", text, str(path))
            self.assertNotRegex(text, r"export [A-Z_]+=", str(path))
            # "pip install" survives only as a negative instruction
            # ("Do not `pip install PySide6`").
            for line in text.splitlines():
                if "pip install" in line:
                    self.assertIn("Do not", line,
                                  f"{path}: {line.strip()}")

    def test_readme_terminal_route_is_optional_only(self):
        readme = (ROOT / "README.md").read_text()
        heading = "### Terminal reference (optional — advanced users)"
        self.assertIn(heading, readme)
        before, rest = readme.split(heading, 1)
        # The subsection runs until the next top-level heading.
        after_match = re.search(r"\n## ", rest)
        after = rest[after_match.start():] if after_match else ""
        for region in (before, after):
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, region,
                                 f"README outside the optional "
                                 f"terminal reference: {bad!r}")

    def test_changelog_v13_section(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        v13, _, _ = changelog.partition("## v1.2")
        self.assertIn("## v1.3", v13)
        for bad in self.FORBIDDEN:
            self.assertNotIn(bad, v13,
                             f"CHANGELOG v1.3 section: {bad!r}")

    def test_desktop_files(self):
        text = (ROOT / "Install-SKULLPT.desktop").read_text()
        self.assertNotIn("sudo", text)
        self.assertIn("Terminal=false", text)


if __name__ == "__main__":
    unittest.main()
