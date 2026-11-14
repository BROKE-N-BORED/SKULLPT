"""Character Studio: build a rigged human with MPFB2 (v1.7, T26).

A kind="character" description carries a "character" block (11 MPFB
macro sliders, a skin, a rig). This exporter builds the human headless
through MPFB2's own API, HumanService.deserialize_from_dict with a
human_info dict, then reuses the scene build template for props,
stats, LODs, collision, GLB and .blend, and the shared two-phase run
(Blender build, then renders) from blender_export.

MPFB2 is NOT in this repo: the installer downloads it (extension +
system assets pack) on request. Everything this module needs to know
about the install lives here and in install.sh, which carry the same
URLs, sizes and hashes (a test asserts they match).

Verified against the real thing, not the docs: MPFB 2.0.18 in sandbox
Blender 4.5.9, extension installed with `blender --command extension
install-file`, human created headless under --factory-startup with
addon_utils.enable("bl_ext.user_default.mpfb"). Note the module name:
extensions live under bl_ext.<repo>.<id>, so plain `import mpfb`
fails, so the generated script finds the module by scanning
addon_utils.modules() for a name ending in ".mpfb", which also
covers a user-installed copy in another repo.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import blender_export
from .description import CHARACTER_DEFAULT_RIG

# MPFB 2.0.18 (Oct 10, 2026) from the Blender Extensions platform.
# The SHA-256 is published in the download URL itself, and the
# downloaded zip (45,956,992 bytes) was verified against it.
MPFB_VERSION = "2.0.18"
MPFB_SHA256 = "c075b3e9b94b8e5556a5f03f4653602d02ea306de08126e8f55ed6dee28a2dad"
MPFB_URL = ("https://extensions.blender.org/download/sha256:" + MPFB_SHA256
            + "/add-on-mpfb-v2.0.18.zip"
              "?repository=%2Fapi%2Fv1%2Fextensions%2F&blender_version_min=4.2.0")
MPFB_SIZE_BYTES = 45956992
# The MakeHuman system assets pack (CC0): skins, eyes, teeth, hair,
# clothes, proxymeshes. Measured from the real download, 2026-11-14:
# 280,737,770 bytes (the pack page's "267 mb"). files2 is the primary
# mirror, files the secondary; same file on both.
ASSETS_SHA256 = "b542127a8e25547c7c29c19f2d1d2adb9a664c80396ecd694095dbc8028a0107"
ASSETS_URL = ("https://files2.makehumancommunity.org/asset_packs/"
              "makehuman_system_assets/makehuman_system_assets_cc0.zip")
ASSETS_URL_FALLBACK = ("https://files.makehumancommunity.org/asset_packs/"
                       "makehuman_system_assets/makehuman_system_assets_cc0.zip")
ASSETS_SIZE_BYTES = 280737770

# The body parts every Character Studio human gets, so the renders
# show a finished face. Fixed, not user parameters in v1.7: they are
# in the system assets pack (eyes/low-poly/low-poly.mhclo,
# teeth/teeth_base/teeth_base.mhclo).
EYES_ASSET = "low-poly.mhclo"
TEETH_ASSET = "teeth_base.mhclo"
# The default skin for a new character: a real skin in the system
# assets pack (skins/young_caucasian_male/young_caucasian_male.mhmat).
DEFAULT_SKIN = "young_caucasian_male.mhmat"


def default_character_description(name: str) -> dict:
    """The description a new Character Studio job starts from: an
    average adult, GameEngine rig, default skin, no props. The
    budget fits the human (about 34k tris) with room for props."""
    return {
        "name": name,
        "kind": "character",
        "summary": "A character made in Character Studio.",
        "parts": [],
        "character": {
            "gender": 0.5, "age": 0.5, "muscle": 0.5, "weight": 0.5,
            "proportions": 0.5, "height": 0.5, "cupsize": 0.5,
            "firmness": 0.5,
            "race_asian": 0.33, "race_caucasian": 0.33, "race_african": 0.33,
            "skin": DEFAULT_SKIN,
            "rig": CHARACTER_DEFAULT_RIG,
        },
        "budget": {"max_tris": 100000, "preview_px": 640},
        "rubric": ["the human looks like a person",
                   "the skin is not grey",
                   "the rig is attached"],
    }


def _slider(character: dict, key: str, default: float) -> float:
    """One slider value, with its default when the key is missing:
    0.0 is a real value, not a missing one."""
    value = character.get(key, default)
    return float(default if value is None else value)


def character_bounds_cm(character: dict) -> tuple[list[float], list[float]]:
    """(min, size) in cm for the human a character block describes.

    A loose framing box, not a measurement: MPFB's real height comes
    from its macro targets (a probe human at height 0.7, age 0.3
    measured 163 cm against this map's 182 cm, since age pulls it down).
    The box only frames the Iso camera, sizes the collision box and
    draws the viewport mannequin, where loose beats clipped.
    """
    height = _slider(character, "height", 0.5)
    weight = _slider(character, "weight", 0.5)
    muscle = _slider(character, "muscle", 0.5)
    height_cm = 150.0 + height * 45.0
    width_cm = 40.0 + weight * 25.0 + muscle * 10.0
    depth_cm = 22.0 + weight * 18.0
    return ([-width_cm / 2.0, -depth_cm / 2.0, 0.0],
            [width_cm, depth_cm, height_cm])


def normalize_character(character: dict, name: str) -> dict:
    """The character block the build script embeds: every slider
    filled with MPFB's own defaults (0.5, race 0.33 each), the rig
    defaulted, the framing bounds attached."""
    sliders = {}
    for key in ("gender", "age", "muscle", "weight", "proportions",
                "height", "cupsize", "firmness"):
        sliders[key] = _slider(character, key, 0.5)
    for key in ("race_asian", "race_caucasian", "race_african"):
        sliders[key] = _slider(character, key, 0.33)
    bounds_min, bounds_size = character_bounds_cm(character)
    return {
        "name": name,
        "sliders": sliders,
        "skin": character.get("skin"),
        "rig": character.get("rig") or CHARACTER_DEFAULT_RIG,
        "bounds": {"min": bounds_min, "size": bounds_size},
    }


# The MPFB section is inserted into the build template right after
# factory settings (and before the world setup): any earlier and the
# factory reset wipes the human; any later and it is still fine, but
# the human belongs first, before the prop parts.
_MPFB_ANCHOR = ("bpy.ops.wm.read_factory_settings(use_empty=True)\n"
                "scene = bpy.context.scene\n")

_MPFB_SECTION = r'''
# Character Studio (v1.7, T26): build the human with MPFB2.
import addon_utils, importlib
_mpfb_mod = None
for _mod in addon_utils.modules():
    if _mod.__name__.endswith(".mpfb") or _mod.__name__ == "mpfb":
        _mpfb_mod = _mod.__name__
        break
if _mpfb_mod is None:
    raise RuntimeError(
        "Character Studio is not installed: MPFB2 was not found in "
        "Blender. Re-run the SKULLPT installer with the Character "
        "Studio option to install it.")
addon_utils.enable(_mpfb_mod, default_set=True)
HumanService = importlib.import_module(_mpfb_mod + ".services").HumanService

_char = D["character"]
if _char["rig"].startswith("rigify."):
    # Rigify rigs need the bundled Rigify add-on enabled first.
    addon_utils.enable("rigify", default_set=True)
_human_info = HumanService._create_default_human_info_dict()
_human_info["name"] = _char["name"]
_human_info["phenotype"] = {
    "gender": _char["sliders"]["gender"],
    "age": _char["sliders"]["age"],
    "muscle": _char["sliders"]["muscle"],
    "weight": _char["sliders"]["weight"],
    "proportions": _char["sliders"]["proportions"],
    "height": _char["sliders"]["height"],
    "cupsize": _char["sliders"]["cupsize"],
    "firmness": _char["sliders"]["firmness"],
    "race": {
        "asian": _char["sliders"]["race_asian"],
        "caucasian": _char["sliders"]["race_caucasian"],
        "african": _char["sliders"]["race_african"],
    },
}
_human_info["skin_mhmat"] = _char["skin"]
# GAMEENGINE is the plain PBR skin material, the one that exports
# cleanly to GLB/Unreal; the SSS and layered materials do not.
_human_info["skin_material_type"] = "GAMEENGINE"
_human_info["rig"] = _char["rig"]
# Fixed body parts: low-poly eyes and the base teeth, so the renders
# show a finished face. If an asset is missing MPFB only warns and
# the human still builds.
_human_info["eyes"] = "low-poly.mhclo"
_human_info["teeth"] = "teeth_base.mhclo"
_settings = HumanService.get_default_deserialization_settings()
# No subdivision: the base mesh (about 34k tris with eyes and teeth)
# is the game-ready mesh, and the stats must count what ships.
_settings["subdiv_levels"] = 0
_settings["load_clothes"] = False
HumanService.deserialize_from_dict(_human_info, _settings)
print("DS_CHARACTER_OK", _char["name"])
'''


def generate_character_script(desc: dict, out_dir: Path, slug: str) -> str:
    """Build the Blender Python script for a character description:
    the scene build script, plus the character block in the payload,
    the Iso camera reframed on the human (and props, if any), and the
    MPFB section after factory settings. Pure function."""
    character = desc.get("character")
    if not isinstance(character, dict):
        raise RuntimeError(
            "This character job has no character block: the sliders, "
            "skin and rig that say what the human looks like. Open it "
            "in the Character Studio window and save it there.")
    payload = blender_export.build_payload(desc, out_dir, slug)
    payload["character"] = normalize_character(character, slug)
    # Reframe the Iso camera on the human: scene_data bounds cover
    # the prop parts only, and a partless job gets the default cube
    # at (50, 50, 50); the human stands at the origin, feet at z=0.
    # The frame is the union of the human box and the scene bounds
    # (when the job has parts), with the same Iso formula as the
    # scene payload.
    from .viewer import scene_data

    human_min, human_size = character_bounds_cm(character)
    lo = list(human_min)
    hi = [human_min[i] + human_size[i] for i in range(3)]
    if desc.get("parts"):
        b = scene_data(desc)["bounds"]
        span = b["span"]
        for i in range(3):
            lo[i] = min(lo[i], b["center"][i] - span / 2.0)
            hi[i] = max(hi[i], b["center"][i] + span / 2.0)
    center = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3)) or 100.0
    # Same Iso formula as the scene payload, in cm throughout.
    iso_pos = [center[0] - span * 0.85, center[1] - span * 1.15,
               center[2] + span * 0.8]
    for cam in payload["cameras"]:
        if cam["name"] == "Iso (auto)":
            cam["position_cm"] = iso_pos
            cam["look_at_cm"] = center
    script = blender_export.render_build_template(payload)
    if _MPFB_ANCHOR not in script:
        raise RuntimeError(
            "The Blender build template changed and the Character "
            "Studio section has nowhere to go. This is a SKULLPT bug, "
            "not a problem with the description.")
    return script.replace(_MPFB_ANCHOR, _MPFB_ANCHOR + _MPFB_SECTION, 1)


def _config_root() -> Path:
    """Blender's config root: $XDG_CONFIG_HOME when set, else ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def blender_version_dir(exe: str) -> str | None:
    """The config dir version ("4.5") for a Blender binary, from
    `blender --version`. None when it cannot be read."""
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Blender\s+(\d+)\.(\d+)", proc.stdout or "")
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def _mpfb_status(mpfb_dir: Path) -> dict:
    """What a candidate extension dir actually holds."""
    installed = (mpfb_dir / "__init__.py").is_file()
    skins_dir = mpfb_dir / "data" / "skins"
    skins: list[str] = []
    if skins_dir.is_dir():
        skins = sorted({p.name for p in skins_dir.rglob("*.mhmat")})
    return {
        "installed": installed,
        "assets": bool(skins),
        "mpfb_dir": str(mpfb_dir) if installed else None,
        "skins": skins,
    }


def find_mpfb(blender_exe: str | None = None,
              config_dir: Path | str | None = None) -> dict:
    """Where MPFB2 is, and whether the system assets pack is with it.

    Returns {"installed", "assets", "mpfb_dir", "skins"}. With a
    config_dir (a blender/<version> dir) that dir is checked; with a
    blender_exe the version dir for that Blender is checked; with
    neither, every version dir under the config root is scanned and
    the highest version with MPFB wins. No Blender run happens in
    the scan case, so engine status stays cheap.
    """
    if config_dir is not None:
        return _mpfb_status(
            Path(config_dir) / "extensions" / "user_default" / "mpfb")
    root = _config_root() / "blender"
    if blender_exe is not None:
        version = blender_version_dir(blender_exe)
        if version is not None:
            return _mpfb_status(
                root / version / "extensions" / "user_default" / "mpfb")
    best: dict | None = None
    if root.is_dir():
        for version_dir in sorted(root.iterdir(), reverse=True):
            status = _mpfb_status(
                version_dir / "extensions" / "user_default" / "mpfb")
            if status["installed"]:
                best = status
                break
    if best is not None:
        return best
    return {"installed": False, "assets": False, "mpfb_dir": None, "skins": []}


def export_blender_character(desc: dict, out_dir: Path) -> list[str]:
    """Exporter entry point for kind="character": (desc, out_dir) ->
    output file names. Same two-phase run as the scene exporter; the
    pre-flight checks fail plainly before Blender starts when
    Character Studio (or its assets, or the job's skin) is missing,
    because inside Blender a missing skin only warns and the human
    builds grey, a silent wrong result.
    """
    exe = blender_export.find_blender()
    if exe is None:
        raise RuntimeError(
            "Blender was not found on this machine, so no 3D character was built. "
            "Install Blender (or set DESIGNSHELL_BLENDER to its path) and run the build again.")
    status = find_mpfb(blender_exe=exe)
    if not status["installed"]:
        raise RuntimeError(
            "Character Studio is not installed: MPFB2 was not found in "
            "Blender. Re-run the SKULLPT installer with the Character "
            "Studio option to install it.")
    if not status["assets"]:
        raise RuntimeError(
            "Character Studio is missing its skins: the MakeHuman "
            "system assets pack was not found with MPFB2. Re-run the "
            "SKULLPT installer with the Character Studio option to "
            "install it.")
    character = desc.get("character") or {}
    skin = character.get("skin")
    if skin and skin not in status["skins"]:
        raise RuntimeError(
            f"The skin for this character was not found: {skin}. Pick a "
            "skin from the installed assets pack in the Character "
            "Studio window.")
    return blender_export.export_blender_with_build_script(
        desc, out_dir, generate_character_script, "character")
