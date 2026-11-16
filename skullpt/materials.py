"""Parametric materials (v1.11, M1-M6): the PolyOrganic-inspired
material system, phase 1.

A material in a description is either the old string form ("wood",
"metal", a #hex) or an OBJECT spec:

    {"family": "wood", "style": "walnut", "layout": "boards",
     "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}

The string form keeps working exactly as before. The object form is
validated against closed vocabularies here: only families, styles,
layouts, params and wear keys listed in this module are accepted.

v1.11 ships ONE family (wood, six styles) and TWO layouts (solid,
boards). Wear values are accepted, validated and stored, but the wear
node layer is M9 (v1.12) - nothing renders them yet, and the dial
shows no Life page until it does. No knobs that do nothing.

The Blender-side recipe (blender_export.make_material) is adapted
from the NK Wood Grain node structure in video-to-blender
(https://github.com/gokayfem/video-to-blender, MIT License) - rings
from a saw-profile wave, stretched noise fibres, a soft blotch, and
a bump from fibre + ring. The recipe itself is written from scratch
here in plain bpy calls; the structure is the part that is borrowed,
and the MIT attribution is recorded in the README.

Node-count budget (M2): target under 25 nodes per material, under
40 with wear. The counts are deterministic host-side functions, and
the Blender build records the measured counts in stats.json so a
test can prove predicted == measured.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import random
from pathlib import Path

PRESETS_FILENAME = "materials.json"
PREVIEW_DIRNAME = "material-previews"
PREVIEW_CACHE_LIMIT = 20

# ---------------------------------------------------------------- families

FAMILIES: dict[str, dict] = {
    "wood": {
        "label": "Wood",
        "styles": {
            "oak": {"label": "Oak", "light": "#b08a5a", "dark": "#6b4a2a",
                    "base": "#8a6844", "roughness": 0.62},
            "white_oak": {"label": "White Oak", "light": "#c8a878",
                          "dark": "#8a6a44", "base": "#a8875f",
                          "roughness": 0.58},
            "walnut": {"label": "Walnut", "light": "#6b4a30",
                       "dark": "#3a2418", "base": "#54371f",
                       "roughness": 0.50},
            "maple": {"label": "Maple", "light": "#d8b888",
                      "dark": "#a8825a", "base": "#c9a36a",
                      "roughness": 0.55},
            "cherry": {"label": "Cherry", "light": "#9a5a3a",
                       "dark": "#5d2f22", "base": "#7a4630",
                       "roughness": 0.52},
            "weathered": {"label": "Weathered", "light": "#9a938a",
                          "dark": "#5c5650", "base": "#7d766e",
                          "roughness": 0.85},
        },
        "layouts": ("solid", "boards"),
    },
}

LAYOUT_LABELS = {"solid": "Solid", "boards": "Boards"}

# board defaults, in centimetres (SKULLPT's native unit; M4)
BOARD_WIDTH_CM = 12.0
BOARD_LENGTH_CM = 120.0

# ---------------------------------------------------------------- params

# name: (label, minimum, maximum, default, unit, simple?, dial page)
# "simple" knobs are the ones the dial shows in Simple mode (design
# section 4a: seed, scale, ring spacing, figure). The rest are
# Advanced-only. Pages: Detail, Form, Surface, Colour, Depth.
WOOD_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "ring_spacing_mm": ("Ring spacing", 1.0, 30.0, 6.7, "mm",
                        True, "Form"),
    "figure": ("Figure", 0.0, 1.0, 0.6, "", True, "Detail"),
    "knots": ("Knots", 0.0, 1.0, 0.3, "", False, "Detail"),
    "pores": ("Pores", 0.0, 1.0, 0.4, "", False, "Detail"),
    "grain_lines": ("Grain lines", 0.0, 1.0, 0.5, "", False, "Detail"),
    "flow": ("Flow", 0.0, 1.0, 0.0, "", False, "Form"),
    "gloss": ("Gloss", 0.0, 1.0, 0.4, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

PARAMS_BY_FAMILY = {"wood": WOOD_PARAMS}

# Wear (design section 4e): accepted, validated and stored in v1.11,
# rendered by the wear node layer in M9 (v1.12). All 0-1, default 0.
WEAR_PARAMS: dict[str, tuple] = {
    "dirt": ("Dirt", 0.0, 1.0, 0.0),
    "grime": ("Grime", 0.0, 1.0, 0.0),
    "edge_wear": ("Edge wear", 0.0, 1.0, 0.0),
    "edge_width": ("Edge width", 0.0, 1.0, 0.0),
    "dust": ("Dust", 0.0, 1.0, 0.0),
    "wet": ("Wet", 0.0, 1.0, 0.0),
    "moss": ("Moss", 0.0, 1.0, 0.0),
    "rust": ("Rust", 0.0, 1.0, 0.0),
    "chips": ("Chips", 0.0, 1.0, 0.0),
    "scratches": ("Scratches", 0.0, 1.0, 0.0),
}

# Dial pages in v1.11: nine, not ten. The Life page arrives with M9,
# when the stored wear values start applying. (design section 4a)
PAGES = ("Material", "Style", "Layout", "Detail", "Form", "Surface",
         "Colour", "Depth", "Saved")

SPEC_KEYS = ("family", "style", "layout", "params", "wear")


# ---------------------------------------------------------------- helpers

def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(c * 255))) for c in rgb)


def _shift_hue(hex_value: str, shift: float) -> str:
    """Shift a hex colour's hue by `shift` (in HSV hue units)."""
    if not shift:
        return hex_value
    r, g, b = _hex_to_rgb(hex_value)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    r2, g2, b2 = colorsys.hsv_to_rgb((h + shift) % 1.0, s, v)
    return _rgb_to_hex((r2, g2, b2))


def _seed_offset(seed: int) -> tuple[float, float, float]:
    """A deterministic coordinate offset per seed (TexNoise and
    TexWave have no seed socket, so the seed moves the coordinates
    through the Mapping node instead)."""
    digest = hashlib.sha256(f"skullpt-seed-{int(seed)}".encode()).digest()
    return tuple(b / 255.0 for b in digest[:3])


# ---------------------------------------------------------------- validation

def validate_material_spec(spec, where: str = "material") -> list[str]:
    """Validate an object-form material spec. Returns a list of
    problems (empty when valid). `spec` must be a dict."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return [f"{where} must be an object with family, style, "
                "layout, params and wear"]
    for key in spec:
        if key not in SPEC_KEYS:
            problems.append(f"{where} has unknown key {key!r}")
    family = spec.get("family")
    if family is None:
        problems.append(f"{where}.family is required")
        return problems
    if family not in FAMILIES:
        problems.append(
            f"{where}.family must be one of "
            f"{', '.join(sorted(FAMILIES))}, got {family!r}")
        return problems
    fam = FAMILIES[family]
    style = spec.get("style")
    if style is not None and style not in fam["styles"]:
        problems.append(
            f"{where}.style must be one of "
            f"{', '.join(sorted(fam['styles']))}, got {style!r}")
    layout = spec.get("layout")
    if layout is not None and layout not in fam["layouts"]:
        problems.append(
            f"{where}.layout must be one of "
            f"{', '.join(fam['layouts'])}, got {layout!r}")
    params = spec.get("params")
    if params is not None:
        if not isinstance(params, dict):
            problems.append(f"{where}.params must be an object")
        else:
            table = PARAMS_BY_FAMILY[family]
            for name, value in params.items():
                if name not in table:
                    problems.append(
                        f"{where}.params has unknown key {name!r}")
                    continue
                _label, low, high, _default, _unit, _simple, _page = \
                    table[name]
                if name == "seed":
                    if not isinstance(value, int) or isinstance(
                            value, bool):
                        problems.append(
                            f"{where}.params.seed must be a whole "
                            "number")
                    elif not low <= value <= high:
                        problems.append(
                            f"{where}.params.seed must be between "
                            f"{low} and {high}")
                elif not isinstance(value, (int, float)) or isinstance(
                        value, bool):
                    problems.append(
                        f"{where}.params.{name} must be a number")
                elif not low <= value <= high:
                    problems.append(
                        f"{where}.params.{name} must be between "
                        f"{low} and {high}")
    wear = spec.get("wear")
    if wear is not None:
        if not isinstance(wear, dict):
            problems.append(f"{where}.wear must be an object")
        else:
            for name, value in wear.items():
                if name not in WEAR_PARAMS:
                    problems.append(
                        f"{where}.wear has unknown key {name!r}")
                    continue
                if not isinstance(value, (int, float)) or isinstance(
                        value, bool):
                    problems.append(
                        f"{where}.wear.{name} must be a number")
                elif not 0.0 <= value <= 1.0:
                    problems.append(
                        f"{where}.wear.{name} must be between 0 and 1")
    return problems


def normalize_material_spec(spec: dict) -> dict:
    """Fill every default in an object spec: style, layout, every
    param and every wear value. The returned spec is always complete.
    Raises ValueError on an invalid spec."""
    problems = validate_material_spec(spec)
    if problems:
        raise ValueError("; ".join(problems))
    family = spec["family"]
    fam = FAMILIES[family]
    table = PARAMS_BY_FAMILY[family]
    params = {name: table[name][3] for name in table}
    params.update(spec.get("params") or {})
    wear = {name: WEAR_PARAMS[name][3] for name in WEAR_PARAMS}
    wear.update(spec.get("wear") or {})
    return {
        "family": family,
        "style": spec.get("style") or next(iter(fam["styles"])),
        "layout": spec.get("layout") or fam["layouts"][0],
        "params": params,
        "wear": wear,
    }


def breadcrumb(spec) -> str:
    """The breadcrumb for a spec: "Wood · White Oak · Boards".
    Tolerant of partial specs (missing fields fall back to defaults);
    falls back to the raw family name for unknown families."""
    if not isinstance(spec, dict):
        return str(spec)
    family = spec.get("family", "?")
    fam = FAMILIES.get(family)
    if fam is None:
        return str(family)
    style = spec.get("style") or next(iter(fam["styles"]))
    style_label = fam["styles"].get(style, {}).get("label", str(style))
    layout = spec.get("layout") or fam["layouts"][0]
    layout_label = LAYOUT_LABELS.get(layout, str(layout))
    return f"{fam['label']} · {style_label} · {layout_label}"


def spec_base_color(spec: dict) -> str:
    """The style's base colour (hue-shifted), for the viewport and
    the dial swatch."""
    norm = normalize_material_spec(spec)
    style = FAMILIES[norm["family"]]["styles"][norm["style"]]
    return _shift_hue(style["base"], norm["params"]["hue"])


# ---------------------------------------------------------------- node counts

# Recipe nodes in make_material (blender_export), counted by hand:
# TexCoord, Mapping, TexWave, Math POWER, VectorMath MULTIPLY
# (fibre stretch), TexNoise (fibre), TexNoise (blotch), MapRange,
# Math MULTIPLY (ring), Math MULTIPLY (fibre), Math ADD, Mix
# (light/dark), VectorMath SCALE (tone), Math ADD (bump), Bump = 15,
# plus the Principled BSDF and the Material Output = 17 base.
# Knots add TexNoise + ColorRamp + Mix = 3. Boards add VectorMath
# MULTIPLY (board prescale) + TexBrick + VectorMath MULTIPLY
# (board tone) = 3. Worst case 23, under the 25 budget.
_BASE_NODES = 17
_KNOTS_NODES = 3
_BOARDS_NODES = 3


def node_count(spec: dict) -> int:
    """The deterministic node count for a spec's recipe, including
    the Principled BSDF and the Material Output."""
    norm = normalize_material_spec(spec)
    total = _BASE_NODES
    if norm["params"]["knots"] > 0:
        total += _KNOTS_NODES
    if norm["layout"] == "boards":
        total += _BOARDS_NODES
    return total


# ---------------------------------------------------------------- resolution

def resolve_spec(spec: dict) -> dict:
    """Resolve a spec to the host-side constants the Blender recipe
    needs. All units are converted here: the template just wires
    nodes. JSON-serialisable (tuples become lists on dump)."""
    norm = normalize_material_spec(spec)
    style = FAMILIES[norm["family"]]["styles"][norm["style"]]
    params = norm["params"]
    hue = params["hue"]
    light = _shift_hue(style["light"], hue)
    dark = _shift_hue(style["dark"], hue)
    # Rings per metre from the ring spacing, then the wave scale
    # (1/2pi converts rings-per-metre to the wave node's scale).
    rings_per_m = 1000.0 / params["ring_spacing_mm"]
    coord_scale = 100.0 / params["scale_cm"]
    knots = params["knots"]
    # The knot mask is a ColorRamp over high-frequency noise: the
    # knots value moves the ramp thresholds, so more knots means
    # more of the noise passes as knots. No extra Math node needed.
    knot_low = max(0.55, 0.92 - knots * 0.35)
    knot_high = min(0.99, knot_low + 0.08)
    boards = norm["layout"] == "boards"
    # Board prescale: the Brick node reads the mapped coordinates
    # and makes one cell per unit, so the coordinates are scaled
    # into board units (length along X, width along Y).
    if boards:
        board_prescale = (
            1.0 / (BOARD_LENGTH_CM / 100.0 * coord_scale),
            1.0 / (BOARD_WIDTH_CM / 100.0 * coord_scale),
            1.0,
        )
    else:
        board_prescale = None
    return {
        "family": norm["family"],
        "style": norm["style"],
        "layout": norm["layout"],
        "breadcrumb": breadcrumb(norm),
        "light": _hex_to_rgb(light),
        "dark": _hex_to_rgb(dark),
        "roughness": style["roughness"] * (1.0 - params["gloss"] * 0.5),
        # Bump strength: depth sets the base, pores deepen it.
        "bump": (0.05 + params["depth"] * 0.3) * (0.5 + params["pores"]),
        "ring_scale": rings_per_m * 0.159155,
        "distort": params["grain_lines"] * 4.0,
        "fibre_k": params["figure"] * 0.5,
        "knots": knots,
        "knot_scale": 14.0,
        "knot_low": knot_low,
        "knot_high": knot_high,
        "boards": boards,
        "board_prescale": board_prescale,
        "coord_scale": coord_scale,
        "flow": params["flow"],
        "seed_loc": _seed_offset(params["seed"]),
        "nodes": node_count(norm),
    }


def spec_hash(spec: dict) -> str:
    """The preview-cache key for a spec: the sha256 of its canonical
    JSON. Two specs that render the same have the same hash."""
    norm = normalize_material_spec(spec)
    payload = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- surprise

def surprise_spec(rng: random.Random | None = None) -> dict:
    """A random spec. The seed is recorded in the spec (params.seed),
    so a Surprise result can be reproduced exactly."""
    rng = rng or random.Random()
    family = rng.choice(sorted(FAMILIES))
    fam = FAMILIES[family]
    style = rng.choice(sorted(fam["styles"]))
    layout = rng.choice(list(fam["layouts"]))
    table = PARAMS_BY_FAMILY[family]
    params: dict[str, object] = {}
    for name, (_label, low, high, _default, _unit, _simple, _page) \
            in table.items():
        if name == "seed":
            params[name] = rng.randint(low, high)
        elif isinstance(low, int) and isinstance(high, int):
            params[name] = rng.randint(low, high)
        else:
            params[name] = round(rng.uniform(low, high), 2)
    return {
        "family": family,
        "style": style,
        "layout": layout,
        "params": params,
        "wear": {},
    }


# ---------------------------------------------------------------- presets

def _presets_path(workspace) -> Path:
    return Path(workspace) / PRESETS_FILENAME


def load_presets(workspace) -> dict[str, dict]:
    """All saved presets: {name: spec}. A missing or damaged file
    reads as no presets, never a crash."""
    path = _presets_path(workspace)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    presets = data.get("presets")
    if not isinstance(presets, dict):
        return {}
    out = {}
    for name, spec in presets.items():
        if isinstance(spec, dict) and not validate_material_spec(spec):
            out[name] = spec
    return out


def save_preset(workspace, name: str, spec: dict) -> None:
    """Save a preset under `name` (overwrites an existing one)."""
    if not name or not name.strip():
        raise ValueError("a preset needs a name")
    problems = validate_material_spec(spec)
    if problems:
        raise ValueError("; ".join(problems))
    presets = load_presets(workspace)
    presets[name.strip()] = normalize_material_spec(spec)
    path = _presets_path(workspace)
    path.write_text(json.dumps({"presets": presets}, indent=2) + "\n")


def delete_preset(workspace, name: str) -> bool:
    """Delete a preset. True when one was removed."""
    presets = load_presets(workspace)
    if name not in presets:
        return False
    del presets[name]
    path = _presets_path(workspace)
    path.write_text(json.dumps({"presets": presets}, indent=2) + "\n")
    return True
