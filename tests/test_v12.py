"""v1.2 tests: the venv installer, the SKULLPT rename, the desktop program.

Run:  python3 -m unittest discover -s tests -v
"""
import configparser
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(args, cwd=None, env=None, timeout=120):
    return subprocess.run(
        args, cwd=cwd or ROOT, env=env, capture_output=True, text=True,
        timeout=timeout)


def _env_without(*names):
    env = dict(os.environ)
    for name in names:
        env.pop(name, None)
    return env


def _tiny_png(path: Path) -> None:
    def chunk(tag, data):
        block = struct.pack(">I", len(data)) + tag + data
        return block + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


class ShimTests(unittest.TestCase):
    """The package is skullpt/ now; designshell/ is a thin compat shim."""

    def test_new_cli_help(self):
        res = _run([PY, "-m", "skullpt", "--help"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("usage: skullpt", res.stdout)
        self.assertIn("SKULLPT", res.stdout)

    def test_shim_cli_help(self):
        res = _run([PY, "-m", "designshell", "--help"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("usage: skullpt", res.stdout)

    def test_shim_is_the_new_package(self):
        code = ("import designshell, skullpt; "
                "assert designshell is skullpt, 'shim must be the package'")
        res = _run([PY, "-c", code])
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_shim_submodule_imports(self):
        code = ("from designshell import bench; "
                "assert callable(bench.serve); "
                "assert callable(bench.workspace_state)")
        res = _run([PY, "-c", code])
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_shim_dotted_import(self):
        code = ("from designshell.bench import workspace_state; "
                "from designshell.jobs import Job; "
                "assert callable(workspace_state); assert Job")
        res = _run([PY, "-c", code])
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_demo_runs_through_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = _run([PY, "-m", "designshell", "demo", tmp])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue((Path(tmp) / "library").exists())

    def test_cloud_mesh_no_token_still_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "photo.png"
            _tiny_png(png)
            env = _env_without("HF_TOKEN")
            res = _run([PY, "-m", "skullpt", "cloud-mesh", tmp, str(png)],
                       env=env)
            self.assertEqual(res.returncode, 1)
            self.assertIn("HF_TOKEN", res.stdout + res.stderr)


class VenvSelectionTests(unittest.TestCase):
    """run.sh prefers .venv/bin/python when it has PySide6, else python3."""

    def _stage(self, tmp, venv_mode):
        """Copy run.sh into tmp; optionally stage a fake .venv python.

        venv_mode: None (no venv), "with-qt" (fake claims PySide6),
        "without-qt" (fake fails the PySide6 import check).
        """
        stage = Path(tmp)
        (stage / "run.sh").write_text((ROOT / "run.sh").read_text())
        (stage / "run.sh").chmod(0o755)
        if venv_mode:
            venv_bin = stage / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            if venv_mode == "with-qt":
                shim = ("#!/bin/sh\n"
                        "echo \"VENV-PYTHON $@\"\n")
            else:
                shim = ("#!/bin/sh\n"
                        "case \"$*\" in\n"
                        "  *import\\ PySide6*) exit 1 ;;\n"
                        "esac\n"
                        "echo \"VENV-PYTHON $@\"\n")
            (venv_bin / "python").write_text(shim)
            (venv_bin / "python").chmod(0o755)
        # Fake system python3, first on PATH, so the test never runs the
        # real interpreter: it just reports which python was picked.
        bin_dir = stage / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").write_text(
            "#!/bin/sh\necho \"SYS-PYTHON $@\"\n")
        (bin_dir / "python3").chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        env["HOME"] = tmp
        return stage, env

    def test_venv_python_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, env = self._stage(tmp, "with-qt")
            res = _run(["bash", "run.sh", "demo", "ws"], cwd=stage, env=env)
            self.assertIn("VENV-PYTHON -m skullpt demo ws", res.stdout)

    def test_no_venv_falls_back_to_system_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, env = self._stage(tmp, None)
            res = _run(["bash", "run.sh", "demo", "ws"], cwd=stage, env=env)
            self.assertIn("SYS-PYTHON -m skullpt demo ws", res.stdout)

    def test_venv_without_pyside6_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, env = self._stage(tmp, "without-qt")
            res = _run(["bash", "run.sh", "demo", "ws"], cwd=stage, env=env)
            self.assertIn("SYS-PYTHON -m skullpt demo ws", res.stdout)
            self.assertNotIn("VENV-PYTHON", res.stdout)

    def test_symlinked_run_sh_still_finds_its_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, env = self._stage(tmp, "with-qt")
            link_dir = Path(tmp) / "elsewhere"
            link_dir.mkdir()
            (link_dir / "skullpt").symlink_to(stage / "run.sh")
            res = _run(["bash", str(link_dir / "skullpt"), "demo", "ws"],
                       cwd=link_dir, env=env)
            self.assertIn("VENV-PYTHON -m skullpt demo ws", res.stdout)


class InstallerTextTests(unittest.TestCase):
    """install.sh / run.sh / qtbench.py say the true thing about PySide6."""

    def setUp(self):
        self.install = (ROOT / "install.sh").read_text()
        self.run = (ROOT / "run.sh").read_text()
        self.qtbench = (ROOT / "skullpt" / "qtbench.py").read_text()

    def test_installer_creates_venv_and_installs_essentials(self):
        self.assertIn("python3 -m venv .venv", self.install)
        self.assertIn("pip install PySide6-Essentials", self.install)

    def test_installer_never_uses_user_or_break_system_packages(self):
        for line in self.install.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "echo" in stripped:
                continue
            self.assertNotIn("--user", line)
            self.assertNotIn("--break-system-packages", line)

    def test_installer_heals_missing_venv_via_pkexec_and_never_uses_sudo(self):
        # v1.3: a missing python3-venv is fixed by the installer itself
        # with ONE pkexec call (a GUI password prompt), then venv
        # creation retries. No terminal command is ever handed to the
        # user, and the word "sudo" appears nowhere in the installer.
        self.assertNotIn("sudo", self.install)
        self.assertIn("apt_install_via_pkexec", self.install)
        self.assertIn(
            'pkexec sh -c "apt-get update && '
            'DEBIAN_FRONTEND=noninteractive apt-get install -y $*"',
            self.install)
        self.assertIn("re-run the installer and allow it when asked",
                      self.install)
        for line in self.install.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "echo" in stripped:
                continue
            # Quoted text only names things for the user; strip it, and
            # strip the helper's own name, then require that no bare
            # apt command remains in the code itself — apt runs only
            # inside the pkexec helper's quoted command string.
            code = re.sub(r'"[^"]*"', '""', stripped)
            code = re.sub(r"'[^']*'", "''", code)
            code = code.replace("apt_install_via_pkexec", "")
            self.assertNotIn("apt", code)

    def test_old_pip_install_advice_is_gone(self):
        self.assertNotIn("Install it with:  pip install PySide6", self.install)
        self.assertNotIn("Install it with:  pip install PySide6", self.qtbench)
        self.assertNotIn("Fix: pip install PySide6", self.install)
        self.assertNotIn("already on Joshua's PC", self.qtbench)

    def test_bench_error_points_at_gui_installer(self):
        # v1.3: the missing-PySide6 error names the double-click
        # installer, never a terminal command.
        self.assertIn("double-click Install SKULLPT", self.qtbench)
        self.assertNotIn("./install.sh", self.qtbench)
        self.assertIn("Do not `pip install PySide6`", self.qtbench)

    def test_run_sh_prefers_venv(self):
        self.assertIn(".venv/bin/python", self.run)
        self.assertIn('import PySide6', self.run)
        self.assertIn("-m skullpt", self.run)

    def test_test_run_cannot_fail_the_install(self):
        # The Qt tests segfault at interpreter exit after printing OK (a
        # known PySide6 teardown quirk); install.sh must judge the run by
        # its summary line, not by the process exit code, or the report
        # and launcher steps never run.
        self.assertIn("|| true", self.install)
        self.assertIn('grep -q "^OK"', self.install)

    def test_dependency_report_covers_pyside6(self):
        self.assertIn("PySide6", self.install)
        self.assertIn('echo "PySide6   $QT_STATUS (Bench window ready)"',
                      self.install)
        self.assertIn('echo "PySide6   NOT FOUND ($QT_FIX)"', self.install)

    def test_installer_creates_launcher_and_command(self):
        self.assertIn("SKULLPT.desktop", self.install)
        self.assertIn(".local/share/applications", self.install)
        self.assertIn(".local/bin/skullpt", self.install)
        self.assertIn("skullpt-icon.svg", self.install)


class DesktopFileTests(unittest.TestCase):
    """The .desktop files parse, and their Exec lines are GLib-safe."""

    def test_installer_desktop_parses(self):
        path = ROOT / "Install-SKULLPT.desktop"
        self.assertTrue(path.exists())
        parser = configparser.RawConfigParser()
        parser.read(path)
        self.assertIn("Desktop Entry", parser.sections())
        entry = parser["Desktop Entry"]
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Name"], "Install SKULLPT")
        # v1.3: the installer runs behind a progress window, not in a
        # terminal.
        self.assertEqual(entry["Terminal"], "false")

    def test_installer_desktop_exec_has_no_nested_quotes(self):
        text = (ROOT / "Install-SKULLPT.desktop").read_text()
        exec_line = [ln for ln in text.splitlines()
                     if ln.startswith("Exec=")][0]
        value = exec_line[len("Exec="):]
        # GLib's desktop parser rejects nested escaped quotes — the whole
        # file then fails with "didn't specify Exec field".
        self.assertNotIn('\\"', value)
        self.assertEqual(value.count('"') % 2, 0)
        self.assertIn("%k", value)
        self.assertIn("install.sh", value)
        # v1.3: the double-click installer launches in GUI mode.
        self.assertIn("--gui", value)

    def test_icon_is_valid_svg(self):
        path = ROOT / "skullpt-icon.svg"
        self.assertTrue(path.exists())
        root = ET.parse(path).getroot()
        self.assertTrue(root.tag.endswith("svg"))
        self.assertGreater(len(list(root)), 0)

    def test_generated_launcher_template_is_glib_safe(self):
        install = (ROOT / "install.sh").read_text()
        # The heredoc writes the absolute path straight into Exec — no
        # nested quotes, no command substitution.
        self.assertIn('Exec="$install_dir/run.sh"', install)
        self.assertIn("Terminal=false", install)
        self.assertIn("Icon=$install_dir/skullpt-icon.svg", install)
        self.assertIn('chmod +x "$desktop_dir/SKULLPT.desktop"'
                      ' "$apps_dir/SKULLPT.desktop"', install)


class RenameTests(unittest.TestCase):
    """SKULLPT everywhere user-facing; only documented leftovers remain."""

    def _text_files(self):
        for pattern in ("skullpt/*.py", "tests/*.py", "designshell/*.py",
                        "*.sh", "*.md", "*.desktop", "*.svg"):
            yield from ROOT.glob(pattern)

    def test_no_design_shell_display_name_left(self):
        # The old display name must not survive anywhere user-facing.
        for path in self._text_files():
            if path.name == "test_v12.py":
                continue
            text = path.read_text()
            self.assertNotIn("Design Shell", text, str(path))
            self.assertNotIn("DesignShell", text, str(path))

    def test_design_shell_lowercase_only_in_data_paths(self):
        allowed = ("design-shell-models", "design-shell-workspace")
        for path in self._text_files():
            if path.name == "test_v12.py":
                continue
            for match in re.finditer(r"design-shell[-\w]*", path.read_text()):
                self.assertTrue(
                    any(match.group(0).startswith(a) for a in allowed),
                    f"{path}: unexpected leftover {match.group(0)!r}")

    def test_old_package_name_only_where_documented(self):
        # "designshell" may appear only in the shim itself and in docs
        # lines that name the shim or the v1.1 module (historical).
        allowed_docs = {"README.md", "CHANGELOG.md"}
        for path in self._text_files():
            if path.name == "test_v12.py":
                continue
            if "designshell" in str(path):
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "designshell" not in line:
                    continue
                self.assertIn(path.name, allowed_docs,
                              f"{path}:{i}: {line.strip()}")
                self.assertTrue(
                    "shim" in line or "cloudmesh" in line
                    or "python3 -m designshell" in line,
                    f"{path}:{i}: {line.strip()}")

    def test_window_title_is_skullpt(self):
        qtbench = (ROOT / "skullpt" / "qtbench.py").read_text()
        self.assertIn('setWindowTitle("SKULLPT', qtbench)
        self.assertIn('setApplicationName("SKULLPT Bench")', qtbench)

    def test_unreal_destination_is_skullpt(self):
        unreal = (ROOT / "skullpt" / "unreal.py").read_text()
        self.assertIn("/Game/SKULLPT/", unreal)


if __name__ == "__main__":
    unittest.main()
