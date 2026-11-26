"""Blender add-on management (v1.18, F1-F3, F5): install, list,
switch on and off, update, and remove Blender add-ons, in plain
words, with Blender itself doing the work.

Every operation is a short generated Blender script run headless
through the same machinery the builds use (find_blender plus the
cancellable runner), so the add-on lands in the folder that the
user's own Blender uses - a portable Blender and a system Blender
are handled by the same code, because Blender is asked where its
user add-ons folder is instead of SKULLPT guessing. Nothing here
writes outside Blender's own config and add-ons folders.

Results cross the process boundary as JSON on a unique marker
line in Blender's output; everything Blender prints besides the
marker is noise (and lands in the app log when something fails).

The package checks that can be done without Blender (is this an
extension package, is there an add-on in this zip at all) are pure
Python and run before any Blender call, so a bad file is refused
before anything changes.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import applog, subproc
from .blender_export import find_blender

MARKER_LIST = "SKULLPT_ADDONS_JSON:"
MARKER_RESULT = "SKULLPT_ADDON_RESULT:"

_LIST_TIMEOUT = 180
_OP_TIMEOUT = 300

_NO_BLENDER = (
    "Blender was not found on this machine, so add-ons cannot be "
    "managed. Use the Setup check from the File menu to install "
    "Blender, then try again.")


class AddonError(Exception):
    """An add-on operation failed, in plain words."""


# ------------------------------------------------- package inspection
#
# Pure Python, no Blender: what can be known about a picked file
# before anything runs. Returns the module name Blender will know
# the add-on by (the first candidate when a zip holds several).


def _zip_candidates(zf: zipfile.ZipFile) -> list[str]:
    """Module names of the classic add-ons a zip holds, in file
    order. A candidate is a top-level folder with an __init__.py
    that carries a bl_info block, or a top-level .py file that
    carries one. A zip whose __init__.py sits at the top level is
    packaged wrong (Blender refuses it too) and holds no candidate.
    """
    names = [n for n in zf.namelist()
             if not n.endswith("/") and "__MACOSX" not in n]
    candidates: list[str] = []

    def _has_bl_info(entry: str) -> bool:
        try:
            text = zf.read(entry).decode("utf-8", errors="replace")
        except (KeyError, RuntimeError, zipfile.BadZipFile):
            return False
        return "bl_info" in text

    top_files = [n for n in names if "/" not in n]
    top_dirs = sorted({n.split("/", 1)[0] for n in names if "/" in n})
    for folder in top_dirs:
        init = f"{folder}/__init__.py"
        if init in names and _has_bl_info(init):
            candidates.append(folder)
    for entry in top_files:
        if entry.endswith(".py") and entry != "__init__.py" \
                and _has_bl_info(entry):
            candidates.append(entry[:-3])
    return candidates


def inspect_package(path: str | Path) -> str:
    """The module name a .zip or .py add-on file will install as.

    Raises AddonError, in plain words, for a file that is not
    there, not a .zip or .py, an extension package (this version
    of SKULLPT installs classic add-ons), a zip packaged with its
    files at the top level, or a file with no add-on in it. Runs
    entirely in Python: a refused file never reaches Blender.
    """
    picked = Path(path)
    if not picked.exists():
        raise AddonError(
            "That file could not be found. It may have been moved "
            "or deleted since it was picked.")
    suffix = picked.suffix.lower()
    if suffix == ".py":
        try:
            text = picked.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AddonError(
                f"That file could not be read: {exc}") from exc
        if "bl_info" not in text:
            raise AddonError(
                "No add-on was found in that file. An add-on .py "
                "carries an info block (bl_info) near the top.")
        return picked.stem
    if suffix != ".zip":
        raise AddonError(
            "SKULLPT installs add-ons from a .zip or a .py file. "
            f"That file is a {suffix or 'file with no ending'}.")
    try:
        with zipfile.ZipFile(picked) as zf:
            names = zf.namelist()
            if any(Path(n).name == "blender_manifest.toml"
                   for n in names):
                raise AddonError(
                    "That zip is an extension package (it holds a "
                    "blender_manifest.toml). This version of SKULLPT "
                    "installs classic add-ons; extension support is "
                    "a later addition.")
            candidates = _zip_candidates(zf)
            top_level_init = "__init__.py" in names
    except zipfile.BadZipFile as exc:
        raise AddonError(
            "That file could not be read as a zip. It may be "
            "damaged, or it may not be an add-on file.") from exc
    if not candidates:
        if top_level_init:
            raise AddonError(
                "That zip is packaged with its files at the top "
                "level. An add-on zip needs the add-on inside its "
                "own folder.")
        raise AddonError(
            "No add-on was found in that zip. An add-on zip holds "
            "a folder (or a .py file) carrying the add-on's info "
            "block (bl_info).")
    return candidates[0]


# ------------------------------------------------------- Blender runs
#
# The runner seam: every Blender call goes through
# _run_blender_script, so tests can fake one function and drive
# the whole backend. Unlike the build runner it does NOT pass
# --factory-startup: the manager must see (and save) the user's
# real preferences, which factory startup would hide.


def _run_blender_script(script: str, timeout: int = _LIST_TIMEOUT,
                        env_extra: dict | None = None) -> str:
    """Run one Blender script headless and return its output.

    `env_extra` overrides environment entries for the Blender
    process (the test suite points Blender at a throwaway config
    and scripts folder this way). Raises AddonError when Blender
    is missing, takes too long, or cannot be started.
    """
    exe = find_blender()
    if exe is None:
        raise AddonError(_NO_BLENDER)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    if env_extra:
        env.update(env_extra)
    script_file = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", suffix=".py", prefix="skullpt-addons-",
                delete=False, encoding="utf-8") as handle:
            handle.write(script)
            script_file = Path(handle.name)
        try:
            _rc, out = subproc.run_cancellable(
                [exe, "--background", "--python", str(script_file)],
                timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            raise AddonError(
                "Blender took too long to answer. It may be busy "
                "or stuck; try again in a moment.") from exc
        except OSError as exc:
            raise AddonError(
                f"Blender could not be started: {exc}") from exc
        return out
    finally:
        if script_file is not None:
            script_file.unlink(missing_ok=True)


def _parse_marker(output: str, marker: str):
    """The JSON payload of the last marker line in Blender's
    output, or None when Blender never printed one."""
    payload = None
    for line in output.splitlines():
        if marker in line:
            payload = line.split(marker, 1)[1].strip()
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


def _run_for_marker(script: str, marker: str, timeout: int,
                    env_extra: dict | None, what: str):
    """Run a script and read its marker payload. A run with no
    marker is a plain failure, with the output tail in the log."""
    output = _run_blender_script(script, timeout=timeout,
                                 env_extra=env_extra)
    payload = _parse_marker(output, marker)
    if payload is None:
        applog.get_logger().error(
            "addons: Blender gave no answer for %s; output tail:\n%s",
            what, "\n".join(output.splitlines()[-20:]))
        raise AddonError(
            "Blender ran but did not answer. It may have failed "
            "to start properly; the details are in the Debug log.")
    return payload


# --------------------------------------------------------- bpy scripts
#
# One shared listing function, embedded in every script that
# needs it: user-installed add-ons only (their files live under
# the user add-ons folder Blender names), with the info block
# read the way Blender reads it.

_LIST_FN = '''
def _skullpt_user_addons():
    import addon_utils
    import bpy
    import os

    user_dir = os.path.abspath(
        bpy.utils.user_resource("SCRIPTS", path="addons"))
    enabled = set(bpy.context.preferences.addons.keys())
    found = []
    for mod in addon_utils.modules():
        path = getattr(mod, "__file__", None)
        if not path:
            continue
        try:
            inside = os.path.commonpath(
                [user_dir, os.path.abspath(path)]) == user_dir
        except ValueError:
            inside = False
        if not inside:
            continue
        info = getattr(mod, "bl_info", None) or {}
        version = info.get("version")
        if isinstance(version, (list, tuple)):
            version = ".".join(str(part) for part in version)
        found.append({
            "module": mod.__name__,
            "name": info.get("name") or mod.__name__,
            "version": str(version) if version else "",
            "author": info.get("author") or "",
            "description": info.get("description") or "",
            "enabled": mod.__name__ in enabled,
        })
    found.sort(key=lambda item: item["name"].casefold())
    return found
'''


def _list_script() -> str:
    return (
        "import json\n"
        + _LIST_FN
        + f'\nprint("{MARKER_LIST}" + json.dumps('
          "_skullpt_user_addons()))\n")


def _install_script(filepath: str, module_hint: str) -> str:
    """Install, then switch on what arrived, then save prefs.

    The new module is found the way Blender finds it: the list
    from before the install against the list after. An enable
    failure on a brand-new add-on takes its files back out, so a
    failed install changes nothing; on an update the new files
    stay (Blender has already replaced the old ones) and the
    failure says so.
    """
    return f'''
import bpy
import json

{_LIST_FN}

result = {{"ok": False}}
try:
    before = {{item["module"] for item in _skullpt_user_addons()}}
    was_present = {module_hint!r} in before
    outcome = bpy.ops.preferences.addon_install(
        filepath={filepath!r}, overwrite=True)
    if "FINISHED" not in outcome:
        result["error"] = "refused"
    else:
        after = _skullpt_user_addons()
        by_module = {{item["module"]: item for item in after}}
        fresh = [item for item in after
                 if item["module"] not in before]
        target = by_module.get({module_hint!r})
        if target is None and fresh:
            target = fresh[0]
        if target is None:
            result["error"] = "missing_after"
        else:
            try:
                for item in fresh:
                    if item["module"] not in \\
                            bpy.context.preferences.addons:
                        bpy.ops.preferences.addon_enable(
                            module=item["module"])
                if target["module"] not in \\
                        bpy.context.preferences.addons:
                    bpy.ops.preferences.addon_enable(
                        module=target["module"])
            except Exception as exc:
                if not was_present:
                    try:
                        bpy.ops.preferences.addon_remove(
                            module=target["module"])
                    except Exception:
                        pass
                    bpy.ops.wm.save_userpref()
                    result["error"] = "enable_failed_new"
                else:
                    bpy.ops.wm.save_userpref()
                    result["error"] = "enable_failed_update"
                result["detail"] = str(exc)
            else:
                bpy.ops.wm.save_userpref()
                settled = {{item["module"]: item
                            for item in _skullpt_user_addons()}}
                result = {{"ok": True,
                          "addon": settled.get(
                              target["module"], target)}}
except Exception as exc:
    result = {{"ok": False, "error": "blender_error",
              "detail": str(exc)}}
print("{MARKER_RESULT}" + json.dumps(result))
'''


def _set_enabled_script(module: str, on: bool) -> str:
    return f'''
import bpy
import json

result = {{"ok": False}}
try:
    if {on!r}:
        outcome = bpy.ops.preferences.addon_enable(
            module={module!r})
    else:
        outcome = bpy.ops.preferences.addon_disable(
            module={module!r})
    if "FINISHED" not in outcome:
        result["error"] = "refused"
    else:
        bpy.ops.wm.save_userpref()
        result = {{"ok": True, "module": {module!r},
                  "enabled": {module!r} in
                  bpy.context.preferences.addons}}
        if result["enabled"] != {on!r}:
            result = {{"ok": False, "error": "refused"}}
except Exception as exc:
    result = {{"ok": False, "error": "blender_error",
              "detail": str(exc)}}
print("{MARKER_RESULT}" + json.dumps(result))
'''


def _remove_script(module: str) -> str:
    """Disable, remove, and prove the files are gone.

    Blender's own remove command deletes the files and then, in a
    headless run, trips over a window redraw that does not exist
    there - the deletion has already happened, so its error is
    caught and the file check below is the verdict. When the
    command is missing entirely, or the files survive it, they
    are deleted here directly.
    """
    return f'''
import bpy
import json
import os
import shutil

import addon_utils

result = {{"ok": False}}
try:
    target_path = None
    is_dir = False
    for mod in addon_utils.modules():
        if mod.__name__ == {module!r}:
            path = getattr(mod, "__file__", None)
            if path:
                if os.path.basename(path) == "__init__.py":
                    target_path = os.path.dirname(path)
                    is_dir = True
                else:
                    target_path = path
            break
    try:
        bpy.ops.preferences.addon_disable(module={module!r})
    except Exception:
        pass
    if hasattr(bpy.ops.preferences, "addon_remove"):
        try:
            bpy.ops.preferences.addon_remove(module={module!r})
        except Exception:
            pass
    bpy.ops.wm.save_userpref()
    if target_path and os.path.exists(target_path):
        if is_dir:
            shutil.rmtree(target_path, ignore_errors=True)
        else:
            try:
                os.unlink(target_path)
            except OSError:
                pass
    gone = not (target_path and os.path.exists(target_path))
    result = {{"ok": bool(gone), "module": {module!r},
              "files_gone": bool(gone)}}
    if not gone:
        result["error"] = "files_stay"
except Exception as exc:
    result = {{"ok": False, "error": "blender_error",
              "detail": str(exc)}}
print("{MARKER_RESULT}" + json.dumps(result))
'''


# ------------------------------------------------------------ public API


def blender_available() -> bool:
    """True when a Blender executable can be found."""
    return find_blender() is not None


def list_addons(env_extra: dict | None = None) -> list[dict]:
    """The user-installed add-ons: dicts with module, name,
    version, author, description, and enabled. One headless
    Blender query; Blender names its own add-ons folder, so the
    portable and system cases are the same code."""
    payload = _run_for_marker(_list_script(), MARKER_LIST,
                              _LIST_TIMEOUT, env_extra, "list")
    if not isinstance(payload, list):
        raise AddonError(
            "Blender's answer about its add-ons could not be "
            "read. The details are in the Debug log.")
    return payload


def _result_or_raise(payload, messages: dict[str, str],
                     fallback: str) -> dict:
    """Turn an operation result payload into its dict, or raise
    the plain AddonError its error code maps to."""
    if not isinstance(payload, dict):
        raise AddonError(fallback)
    if payload.get("ok"):
        return payload
    code = payload.get("error", "")
    if payload.get("detail"):
        applog.get_logger().error(
            "addons: Blender reported %s: %s",
            code or "an error", payload["detail"])
    raise AddonError(messages.get(code, fallback))


def install_addon(path: str | Path,
                  env_extra: dict | None = None) -> dict:
    """Install a .zip or .py add-on through Blender's own
    installer, switch it on, and save Blender's preferences.
    Installing over an add-on that is already there is the update
    path. Returns the installed add-on's info dict."""
    picked = Path(path)
    module_hint = inspect_package(picked)
    payload = _run_for_marker(
        _install_script(str(picked), module_hint), MARKER_RESULT,
        _OP_TIMEOUT, env_extra, "install")
    result = _result_or_raise(payload, {
        "refused": (
            "Blender refused to install that file. It may not be "
            "a working add-on, or it may be packaged in a way "
            "Blender does not accept."),
        "missing_after": (
            "Blender installed the file, but no add-on appeared "
            "afterwards. The file may not hold a working add-on."),
        "enable_failed_new": (
            "Blender could not switch the new add-on on, so it "
            "was taken back out. The add-on may be broken, or made "
            "for a different Blender version."),
        "enable_failed_update": (
            "The new file is in, but Blender could not switch "
            "the add-on on. It may be broken, or made for a "
            "different Blender version."),
    }, "Blender hit a problem installing that add-on. The "
       "details are in the Debug log.")
    return result["addon"]


def set_enabled(module: str, on: bool,
                env_extra: dict | None = None) -> dict:
    """Switch an add-on on or off and save Blender's preferences.
    Returns {"module": ..., "enabled": ...} as Blender reports it
    after the change."""
    payload = _run_for_marker(
        _set_enabled_script(module, on), MARKER_RESULT,
        _OP_TIMEOUT, env_extra, "enable" if on else "disable")
    word = "on" if on else "off"
    return _result_or_raise(payload, {
        "refused": (
            f"Blender could not switch that add-on {word}. It "
            "may be broken, or made for a different Blender "
            "version."),
    }, f"Blender hit a problem switching that add-on {word}. "
       "The details are in the Debug log.")


def remove_addon(module: str, env_extra: dict | None = None) -> None:
    """Remove an add-on: switched off, deleted through Blender's
    own remove command (with a direct file delete as the
    fallback), preferences saved, and the removal proven by a
    fresh list query afterwards."""
    current = list_addons(env_extra=env_extra)
    if module not in {item["module"] for item in current}:
        raise AddonError("That add-on is not installed.")
    payload = _run_for_marker(
        _remove_script(module), MARKER_RESULT, _OP_TIMEOUT,
        env_extra, "remove")
    _result_or_raise(payload, {
        "files_stay": (
            "The add-on's files could not be deleted from "
            "Blender's add-ons folder. They may be open in "
            "another program."),
    }, "Blender hit a problem removing that add-on. The details "
       "are in the Debug log.")
    after = list_addons(env_extra=env_extra)
    if module in {item["module"] for item in after}:
        raise AddonError(
            "Blender still lists that add-on after removing it. "
            "The details are in the Debug log.")
