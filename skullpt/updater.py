"""Ask-first auto-update (v1.15): verified pull, staged install,
restart to apply.

The flow, in full:

  1. The Bench checks GitHub on launch (silent) or from Help ->
     Check for updates (manual). When an update exists, the user
     is asked "Update now?" - nothing downloads before they say yes.
  2. Yes downloads the release zip and its .sha256 sidecar into the
     staging dir (<install>/.pending-update/) and verifies the
     checksum. A mismatch, or a release with no sidecar, installs
     nothing - a blind download is never applied.
  3. The user is asked whether to restart now. Yes hands off to
     the updater (this module, run as `python -m skullpt.updater`
     in a separate process): it waits for the Bench to exit, swaps
     the staged code over the install dir, clears the stage, and
     relaunches the Bench. No leaves the verified update staged;
     the next launch applies it (re-verified first).
     The workspace in use rides along (v1.17, E1): it is recorded
     in the staged record when the update is pulled, passed to the
     updater on both hand-offs, written into the updated marker,
     and given to the relaunch (`run.sh bench <workspace>`), so
     the Bench reopens where the user was working and the
     "Updated to vX" note is read by that workspace's Bench.
  4. Only code files are replaced. The workspace, the key files,
     and .venv are never touched: they are not in the zip, and the
     swap only writes files the zip contains. A backup of the
     replaced files is kept until the new version launches cleanly.

Layout (all inside the install dir, dot-prefixed):

  .pending-update/   the staged zip + sidecar + pending.json
  .backup-<old>/     the replaced files, until the new version runs
  .updated-to-<ver>  marker written after a clean swap; the new
                     Bench reads it, tells the user, and cleans up
  .update-failed     marker written when a swap fails; the next
                     Bench launch tells the user plainly

This module imports only the standard library, and it reads
versions by parsing __init__.py text, never by importing the
package: the updater process runs while the install dir is being
swapped underneath it, so it must not depend on the code it is
replacing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DOWNLOAD_TIMEOUT_S = 120
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
WAIT_FOR_EXIT_S = 60.0

PENDING_DIR_NAME = ".pending-update"
PENDING_FILE = "pending.json"
UPDATED_MARKER_PREFIX = ".updated-to-"
FAILED_MARKER = ".update-failed"
BACKUP_PREFIX = ".backup-"


class UpdateError(RuntimeError):
    """A plain-language update failure: shown to the user as-is."""


# --------------------------------------------------------------------- paths

def install_dir() -> Path:
    """The install dir: the folder holding the skullpt/ package."""
    return Path(__file__).resolve().parent.parent


def pending_dir(install: str | Path) -> Path:
    return Path(install) / PENDING_DIR_NAME


def _version_of_init(init_path: Path) -> str | None:
    """The __version__ string out of an __init__.py, by text."""
    try:
        text = init_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"__version__\s*=\s*\"([^\"]+)\"", text)
    return match.group(1) if match else None


# ------------------------------------------------------------------- staging

def parse_sidecar(text: str) -> str | None:
    """The hex digest out of a sha256sum-format sidecar file
    ("<hex>  <filename>" or "<hex> *<filename>"). None when the
    file does not parse."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest = line.split()[0]
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
        return None
    return None


def _download(url: str, dest: Path) -> None:
    """Download a URL to a file. Plain UpdateError on any failure,
    and a partial file is removed, never left behind."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "SKULLPT-updater"})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            size = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise UpdateError(
                            "the download is bigger than expected - "
                            "stopped for safety, nothing installed")
                    fh.write(chunk)
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise UpdateError(f"could not download the update ({exc})") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_and_stage(result: dict, install: str | Path,
                       workspace: str | Path | None = None) -> dict:
    """Download the release zip + sidecar from a check_for_update
    result, verify the checksum, and stage them in the pending dir.
    Returns {"version", "zip", "sha256"} on success. Raises
    UpdateError (plain words) on any refusal: no zip asset, no
    sidecar, a failed download, or a checksum mismatch. A failed
    stage leaves no partial files behind. The workspace in use,
    when given, is recorded in the pending record (v1.17, E1) so
    the eventual relaunch can reopen it."""
    version = result.get("latest")
    zip_url = result.get("zip_url")
    sha_url = result.get("sha256_url")
    if not version:
        raise UpdateError("GitHub did not name a version for the update.")
    if not zip_url:
        raise UpdateError(
            f"v{version} has no download attached, so it cannot be "
            "installed from here. Open the release page instead.")
    if not sha_url:
        raise UpdateError(
            f"v{version} has no checksum to verify against, so it "
            "cannot be installed safely. Nothing was downloaded.")
    pending = pending_dir(install)
    pending.mkdir(parents=True, exist_ok=True)
    zip_name = f"skullpt-v{version}.zip"
    zip_path = pending / zip_name
    sha_path = pending / (zip_name + ".sha256")
    try:
        _download(zip_url, zip_path)
        _download(sha_url, sha_path)
        expected = parse_sidecar(sha_path.read_text(encoding="utf-8"))
        if expected is None:
            raise UpdateError(
                "the release's checksum file does not parse, so the "
                "download cannot be verified. Nothing was installed.")
        actual = _sha256(zip_path)
        if actual != expected:
            raise UpdateError(
                "the download's checksum does not match the release - "
                "it may be damaged. Nothing was installed.")
        record = {"version": version, "zip_name": zip_name,
                  "zip_sha256": actual}
        if workspace:
            record["workspace"] = str(workspace)
        (pending / PENDING_FILE).write_text(json.dumps(
            record, indent=2) + "\n", encoding="utf-8")
    except UpdateError:
        for path in (zip_path, sha_path, pending / PENDING_FILE):
            path.unlink(missing_ok=True)
        raise
    return {"version": version, "zip": zip_path, "sha256": sha_path}


def stage_is_pending(install: str | Path) -> bool:
    """True when a staged update is waiting in the pending dir."""
    pending = pending_dir(install)
    return (pending / PENDING_FILE).exists()


def verify_staged(install: str | Path) -> dict:
    """Re-verify the staged update before applying it: the pending
    record, the zip, and the sidecar must all agree. Returns the
    pending record. Raises UpdateError when anything is missing or
    mismatched - a staged update that cannot be re-verified is
    never applied. Returns None when nothing is pending."""
    pending = pending_dir(install)
    record_path = pending / PENDING_FILE
    if not record_path.exists():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(
            "the waiting update's record cannot be read - it was "
            f"dropped, nothing applied ({exc})") from exc
    version = record.get("version")
    zip_name = record.get("zip_name")
    recorded = record.get("zip_sha256")
    zip_path = pending / (zip_name or "")
    sha_path = pending / ((zip_name or "") + ".sha256")
    if not version or not zip_name or not recorded \
            or not zip_path.is_file() or not sha_path.is_file():
        raise UpdateError(
            "the waiting update is incomplete - it was dropped, "
            "nothing applied")
    try:
        expected = parse_sidecar(sha_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(
            "the waiting update's checksum cannot be read - it was "
            f"dropped, nothing applied ({exc})") from exc
    if expected is None or _sha256(zip_path) != expected \
            or _sha256(zip_path) != recorded:
        raise UpdateError(
            "the waiting update's checksum does not match any more - "
            "it was dropped, nothing applied")
    return record


def drop_pending(install: str | Path) -> None:
    """Remove the staged update: after a failed re-verify, or once
    an update has applied and the stage is spent."""
    shutil.rmtree(pending_dir(install), ignore_errors=True)


# ------------------------------------------------------------------- handoff

def spawn_updater(install: str | Path, wait_pid: int,
                  workspace: str | Path | None = None) -> None:
    """Start the updater as a detached process. It waits for
    wait_pid (the Bench) to exit, swaps the staged code in, and
    relaunches the Bench. The caller quits right after this. The
    workspace in use, when given, is passed through (v1.17, E1)
    so the relaunch reopens it."""
    argv = [sys.executable, "-m", "skullpt.updater",
            "--wait-pid", str(wait_pid),
            "--install-dir", str(install)]
    if workspace:
        argv += ["--workspace", str(workspace)]
    subprocess.Popen(
        argv,
        cwd=str(install), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def handle_pending_at_startup(install: str | Path,
                              workspace: str | Path | None = None
                              ) -> str | None:
    """Apply a waiting update at startup, before the main window
    opens (v1.15): the user said No to restarting last time, so
    the staged update applies on this launch instead.

    Returns "restarting" when the updater was spawned (the caller
    must exit at once - the updater relaunches the Bench on the
    new version), an error string when the staged update failed
    re-verification (it was dropped; the caller tells the user
    after the window opens), or None when nothing was pending.

    The workspace hand-off (v1.17, E1): the launching Bench's own
    workspace wins when given; otherwise the workspace recorded
    in the staged record is used. Either way the updater relaunches
    the Bench on that workspace, not the default one.
    """
    if not stage_is_pending(install):
        return None
    root = Path(install)
    # Already applied (v1.16, D1): a stage left behind by an update
    # that already went in is not waiting. The updated marker is
    # the proof - drop the stage and let the window open, so
    # consume_update_marker can report the outcome. Without this
    # check the leftover stage re-applied itself on every launch
    # and the Bench never opened again.
    if any(root.glob(UPDATED_MARKER_PREFIX + "*")):
        drop_pending(install)
        return None
    try:
        record = verify_staged(install)
    except UpdateError as exc:
        drop_pending(install)
        return str(exc)
    if record is not None:
        # The same proof, without the marker: the install already
        # runs the staged version, so the swap already happened.
        running = _version_of_init(root / "skullpt" / "__init__.py")
        if running is not None and record.get("version") == running:
            drop_pending(install)
            return None
    carried = workspace or (record or {}).get("workspace")
    if carried:
        spawn_updater(install, os.getpid(), carried)
    else:
        spawn_updater(install, os.getpid())
    return "restarting"


def consume_update_marker(install: str | Path) -> tuple[str | None, str | None]:
    """Read the outcome markers the updater leaves behind, and
    clean up after a clean launch. Returns (updated_version,
    failed_reason): exactly one is set. A clean launch deletes the
    backup (the new version runs, so the old files are no longer
    needed) and the staging dir. A failed swap keeps its backup
    for manual recovery and says where it is."""
    root = Path(install)
    for marker in sorted(root.glob(UPDATED_MARKER_PREFIX + "*")):
        version = marker.name[len(UPDATED_MARKER_PREFIX):]
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        marker.unlink(missing_ok=True)
        backup = info.get("backup")
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
        drop_pending(install)
        return version, None
    failed = root / FAILED_MARKER
    if failed.exists():
        try:
            reason = failed.read_text(encoding="utf-8").strip()
        except OSError:
            reason = "the update did not apply"
        failed.unlink(missing_ok=True)
        return None, reason or "the update did not apply"
    return None, None


# --------------------------------------------------------------------- apply

def _wait_for_exit(pid: int, timeout_s: float = WAIT_FOR_EXIT_S) -> bool:
    """True once pid no longer exists (or never did)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        except OSError:
            return True
        time.sleep(0.2)
    return False


def _zip_members(zip_path: Path) -> list[str]:
    """The zip's file entries, as install-relative paths. The zip
    packs the install dir as its top-level skullpt/ folder, so
    each entry loses that prefix. Directory entries and anything
    outside the top folder are skipped."""
    with zipfile.ZipFile(zip_path) as zf:
        out = []
        for name in zf.namelist():
            if name.endswith("/") or not name.startswith("skullpt/"):
                continue
            rel = name[len("skullpt/"):]
            if rel:
                out.append(rel)
        return out


def run_update(install: str | Path, wait_pid: int,
               workspace: str | Path | None = None) -> int:
    """Swap the staged update over the install dir (the updater
    process). Waits for wait_pid to exit, re-verifies the staged
    zip, backs up the files it is about to replace, copies the
    staged files over, writes the updated marker, and relaunches
    the Bench. On any failure it restores the backup, writes the
    failed marker with the reason, and returns 1 - the old install
    keeps working. The workspace in use (v1.17, E1) comes from the
    caller when given, else from the staged record; it is written
    into the updated marker and passed to the relaunch, so the
    Bench reopens the workspace the user was working in."""
    root = Path(install)
    failed_marker = root / FAILED_MARKER

    def fail(reason: str) -> int:
        try:
            failed_marker.write_text(reason + "\n", encoding="utf-8")
        except OSError:
            pass
        return 1

    if not _wait_for_exit(wait_pid):
        return fail("SKULLPT did not close, so the update was not "
                    "applied. The staged update is still waiting and "
                    "will apply the next time SKULLPT starts.")
    try:
        record = verify_staged(root)
    except UpdateError as exc:
        drop_pending(root)
        return fail(str(exc))
    if record is None:
        return fail("the staged update is gone - nothing was applied")
    version = record["version"]
    carried = str(workspace) if workspace else record.get("workspace")
    zip_path = pending_dir(root) / record["zip_name"]
    old_version = _version_of_init(
        root / "skullpt" / "__init__.py") or "unknown"
    backup_dir = root / (BACKUP_PREFIX + old_version)
    updated_marker = root / (UPDATED_MARKER_PREFIX + version)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise UpdateError(
                    f"the staged zip is damaged (bad file: {bad})")
            members = _zip_members(zip_path)
            if "skullpt/__init__.py" not in members:
                raise UpdateError(
                    "the staged zip does not look like a SKULLPT "
                    "release - nothing was applied")
        extract_dir = Path(tempfile.mkdtemp(prefix="skullpt-update-"))
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extract_dir)
            staged_root = extract_dir / "skullpt"
            staged_version = _version_of_init(
                staged_root / "skullpt" / "__init__.py")
            if staged_version != version:
                raise UpdateError(
                    "the staged files say they are "
                    f"v{staged_version or 'unknown'}, not v{version} - "
                    "nothing was applied")
            # Back up every file the swap is about to replace, so a
            # failure can put the old install back exactly.
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            backup_dir.mkdir(parents=True)
            for rel in members:
                src = root / rel
                if src.is_file():
                    dest = backup_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            # The swap: copy the staged files over. Files the zip
            # does not contain (.venv, logs, the staging dir, the
            # backup itself) are never touched.
            for rel in members:
                src = staged_root / rel
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            for script in ("install.sh", "run.sh"):
                path = root / script
                if path.exists():
                    path.chmod(0o755)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
    except (UpdateError, OSError, zipfile.BadZipFile) as exc:
        _restore_backup(root, backup_dir)
        return fail(f"the update could not be applied ({exc}) - the "
                    "previous version is still in place")
    marker_info = {"version": version, "backup": str(backup_dir)}
    if carried:
        marker_info["workspace"] = str(carried)
    try:
        updated_marker.write_text(json.dumps(
            marker_info, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        _restore_backup(root, backup_dir)
        return fail(f"the update could not be recorded ({exc}) - the "
                    "previous version is still in place")
    # The update has applied (v1.16, D1): the staged package is
    # spent, so it goes now, before the relaunch. Leaving it behind
    # made the next launch's startup check treat it as a waiting
    # update and apply it again, forever - the Bench never opened.
    # The stage goes even if the relaunch below fails: the update
    # itself is in, and the user can open the new version from its
    # desktop icon.
    drop_pending(root)
    # The relaunch reopens the workspace the user was in (v1.17,
    # E1): run.sh passes its arguments to the CLI, and the bench
    # command takes the workspace. Without one, plain run.sh
    # opens the default workspace, as before.
    relaunch = [str(root / "run.sh")]
    if carried:
        relaunch += ["bench", str(carried)]
    try:
        subprocess.Popen(relaunch, cwd=str(root),
                         start_new_session=True,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as exc:
        return fail(f"the update applied, but SKULLPT could not be "
                    f"reopened ({exc}) - open it from its desktop icon")
    return 0


def _restore_backup(root: Path, backup_dir: Path) -> None:
    """Put the backed-up files back over a failed swap. Best
    effort: a file that cannot be restored stays as it is, and the
    backup dir is left in place for manual recovery."""
    if not backup_dir.is_dir():
        return
    for path in sorted(backup_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(backup_dir)
        dest = root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    """`python -m skullpt.updater --wait-pid PID
    --install-dir DIR`: the updater process. Waits for the Bench
    (PID) to exit, swaps the staged update in, relaunches."""
    import argparse

    parser = argparse.ArgumentParser(prog="skullpt.updater")
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args(argv)
    return run_update(args.install_dir, args.wait_pid, args.workspace)


if __name__ == "__main__":
    sys.exit(main())
