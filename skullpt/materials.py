"""Parametric materials (v1.12, M7-M11): the PolyOrganic-inspired
material system, phase 2.

A material in a description is either the old string form ("wood",
"metal", a #hex) or an OBJECT spec:

    {"family": "wood", "style": "walnut", "layout": "boards",
     "params": {"seed": 42, "knots": 0.6}, "wear": {"dirt": 0.2}}

The string form keeps working exactly as before. The object form is
validated against closed vocabularies here: only families, styles,
layouts, params and wear keys listed in this module are accepted.

v1.12 ships ELEVEN families (wood plus brick, stone, marble,
concrete, metal, fabric, leather, ground, paint, glass), NINE
layouts (solid, boards, parquet, herringbone, chevron, stack,
stretcher, english, flemish, basket_weave), and the wear layer: the
wear values v1.11 stored now render through one shared "SKULLPT
Wear" node group, and the dial gains its Life page (the tenth).

The Blender-side recipes (blender_export.make_material) are written
from scratch here in plain bpy calls on Blender's standard nodes.
The wood recipe's structure follows the NK Wood Grain group in
video-to-blender (https://github.com/gokayfem/video-to-blender, MIT
License) - rings from a saw-profile wave, stretched noise fibres, a
soft blotch, and a bump from fibre + ring; the MIT attribution is
recorded in the README. Nothing is taken from PolyOrganic itself
(a $29 closed add-on with no repo): the ideas are reimplemented,
not its node trees.

Node-count budget (M2/M7/M9): the solid recipe of every family
stays under 25 nodes; layouts are additive and every combination
stays under 40 with wear. The counts are deterministic host-side
functions, and the Blender build records the measured counts in
stats.json so a test can prove predicted == measured. The wear
group's internal nodes live inside the group, not in the material:
each worn material costs one group node, and the group's own count
is reported separately as wear_nodes.
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
        "layouts": ("solid", "boards", "parquet", "herringbone",
                    "chevron"),
    },
    "brick": {
        "label": "Brick",
        "styles": {
            "red": {"label": "Red", "light": "#a85848",
                    "dark": "#6e3226", "base": "#8a4436",
                    "roughness": 0.90},
            "weathered_red": {"label": "Weathered Red",
                              "light": "#9a6a58", "dark": "#5e3a30",
                              "base": "#7a5244", "roughness": 0.95,
                              "defaults": {"kiln": 0.8}},
            "buff": {"label": "Buff", "light": "#c8a878",
                     "dark": "#8a6a44", "base": "#a8875f",
                     "roughness": 0.88},
            "dark": {"label": "Dark", "light": "#5a4a44",
                     "dark": "#2e2420", "base": "#443832",
                     "roughness": 0.92},
            "white_painted": {"label": "White Painted",
                              "light": "#e8e2d4", "dark": "#b8b0a0",
                              "base": "#d8d0c0", "roughness": 0.75},
        },
        "layouts": ("solid", "stack", "stretcher", "english",
                    "flemish", "basket_weave"),
    },
    "stone": {
        "label": "Stone",
        "styles": {
            "granite": {"label": "Granite", "light": "#9a938a",
                        "dark": "#5c5650", "base": "#7d766e",
                        "roughness": 0.80},
            "sandstone": {"label": "Sandstone", "light": "#c8a878",
                          "dark": "#8a6a44", "base": "#a8875f",
                          "roughness": 0.92},
            "slate": {"label": "Slate", "light": "#5a6068",
                      "dark": "#2e3238", "base": "#44484e",
                      "roughness": 0.70},
            "limestone": {"label": "Limestone", "light": "#d8d0b8",
                          "dark": "#a89e84", "base": "#c0b498",
                          "roughness": 0.95},
        },
        "layouts": ("solid", "stack", "stretcher"),
    },
    "marble": {
        "label": "Marble",
        "styles": {
            "carrara": {"label": "Carrara", "light": "#ece8e0",
                        "dark": "#a8a49c", "base": "#d8d4cc",
                        "roughness": 0.25},
            "calacatta": {"label": "Calacatta", "light": "#f0ece2",
                          "dark": "#8a7a5a", "base": "#d8cfba",
                          "roughness": 0.22},
            "nero": {"label": "Nero", "light": "#3a3a3e",
                     "dark": "#141416", "base": "#26262a",
                     "roughness": 0.20},
            "verde": {"label": "Verde", "light": "#4a6a58",
                      "dark": "#1e3328", "base": "#34543f",
                      "roughness": 0.28},
        },
        "layouts": ("solid",),
    },
    "concrete": {
        "label": "Concrete",
        "styles": {
            "plain": {"label": "Plain", "light": "#a8a49c",
                      "dark": "#6e6a62", "base": "#8b877f",
                      "roughness": 0.94},
            "polished": {"label": "Polished", "light": "#b8b4ac",
                         "dark": "#7a766e", "base": "#98948c",
                         "roughness": 0.45,
                         "defaults": {"polish": 0.8}},
            "rough": {"label": "Rough", "light": "#98948c",
                      "dark": "#5c5850", "base": "#7a766e",
                      "roughness": 0.98,
                      "defaults": {"aggregate": 0.8}},
            "board_formed": {"label": "Board Formed",
                             "light": "#a89e8c", "dark": "#6a6252",
                             "base": "#88806e", "roughness": 0.96},
        },
        "layouts": ("solid", "stack", "stretcher"),
    },
    "metal": {
        "label": "Metal",
        "styles": {
            "steel": {"label": "Steel", "light": "#b8bcc0",
                      "dark": "#6a6e72", "base": "#8f9397",
                      "roughness": 0.35, "metallic": 1.0},
            "brushed_steel": {"label": "Brushed Steel",
                              "light": "#c0c4c8", "dark": "#787c80",
                              "base": "#9a9ea2", "roughness": 0.42,
                              "metallic": 1.0,
                              "defaults": {"brushed": 0.8}},
            "copper": {"label": "Copper", "light": "#c07850",
                       "dark": "#7a4630", "base": "#a85e3e",
                       "roughness": 0.30, "metallic": 1.0},
            "brass": {"label": "Brass", "light": "#c8a858",
                      "dark": "#8a6a28", "base": "#a8873c",
                      "roughness": 0.32, "metallic": 1.0},
            "iron": {"label": "Iron", "light": "#6a6a6e",
                     "dark": "#3a3a3e", "base": "#525256",
                     "roughness": 0.55, "metallic": 1.0},
            "blackened": {"label": "Blackened", "light": "#3a3a3e",
                          "dark": "#161618", "base": "#26262a",
                          "roughness": 0.62, "metallic": 0.9},
        },
        "layouts": ("solid",),
    },
    "fabric": {
        "label": "Fabric",
        "styles": {
            "canvas": {"label": "Canvas", "light": "#c8b898",
                       "dark": "#8a7a58", "base": "#a89a74",
                       "roughness": 0.95},
            "linen": {"label": "Linen", "light": "#d8d0b8",
                      "dark": "#a89e84", "base": "#c0b498",
                      "roughness": 0.96},
            "wool": {"label": "Wool", "light": "#8a7a6a",
                     "dark": "#5a4c40", "base": "#726453",
                     "roughness": 1.0},
            "denim": {"label": "Denim", "light": "#5a7a9a",
                      "dark": "#2e4a62", "base": "#42627e",
                      "roughness": 0.94},
        },
        "layouts": ("solid",),
    },
    "leather": {
        "label": "Leather",
        "styles": {
            "tan": {"label": "Tan", "light": "#a87850",
                    "dark": "#6a4a2e", "base": "#8a6038",
                    "roughness": 0.60},
            "dark_brown": {"label": "Dark Brown", "light": "#6a4a30",
                           "dark": "#3a2418", "base": "#54371f",
                           "roughness": 0.55},
            "black": {"label": "Black", "light": "#3a3a3e",
                      "dark": "#141416", "base": "#26262a",
                      "roughness": 0.50},
            "oxblood": {"label": "Oxblood", "light": "#6a3038",
                        "dark": "#3a181e", "base": "#52242c",
                        "roughness": 0.52},
        },
        "layouts": ("solid",),
    },
    "ground": {
        "label": "Ground",
        "styles": {
            "soil": {"label": "Soil", "light": "#6a5138",
                     "dark": "#3a2c1e", "base": "#523e28",
                     "roughness": 1.0},
            "gravel": {"label": "Gravel", "light": "#9a938a",
                       "dark": "#5c5650", "base": "#7d766e",
                       "roughness": 1.0},
            "sand": {"label": "Sand", "light": "#d8c898",
                     "dark": "#a89a6a", "base": "#c0b082",
                     "roughness": 0.98},
            "grass_dirt": {"label": "Grass Dirt", "light": "#6a7a4a",
                           "dark": "#3a4a26", "base": "#526236",
                           "roughness": 1.0},
        },
        "layouts": ("solid",),
    },
    "paint": {
        "label": "Paint",
        "styles": {
            "matte_white": {"label": "Matte White",
                            "light": "#ece8e0", "dark": "#c8c2b4",
                            "base": "#dcd6c8", "roughness": 0.85},
            "gloss_red": {"label": "Gloss Red", "light": "#b84030",
                          "dark": "#6e2018", "base": "#93301f",
                          "roughness": 0.25},
            "eggshell_blue": {"label": "Eggshell Blue",
                              "light": "#7a9ab0", "dark": "#46647a",
                              "base": "#5e7f95", "roughness": 0.55},
            "satin_green": {"label": "Satin Green",
                            "light": "#6a8a5a", "dark": "#3a522e",
                            "base": "#526e44", "roughness": 0.45},
            "chalk_black": {"label": "Chalk Black",
                            "light": "#4a4a4e", "dark": "#1e1e20",
                            "base": "#343438", "roughness": 0.90},
            "aged": {"label": "Aged", "light": "#b8a888",
                     "dark": "#7a6a4a", "base": "#988a68",
                     "roughness": 0.80, "defaults": {"chip": 0.5}},
        },
        "layouts": ("solid",),
    },
    "glass": {
        "label": "Glass",
        "styles": {
            "clear": {"label": "Clear", "light": "#d8ecec",
                      "dark": "#a8c8c8", "base": "#c0dcdc",
                      "roughness": 0.05, "transmission": 1.0},
            "frosted": {"label": "Frosted", "light": "#e8ecec",
                        "dark": "#c0c8c8", "base": "#d8dcdc",
                        "roughness": 0.45, "transmission": 1.0,
                        "defaults": {"frost": 0.7}},
            "tinted_green": {"label": "Tinted Green",
                             "light": "#7ab89a", "dark": "#3a7a5a",
                             "base": "#589a78", "roughness": 0.08,
                             "transmission": 1.0,
                             "defaults": {"tint": 0.6}},
            "smoked": {"label": "Smoked", "light": "#6a6a6e",
                       "dark": "#2e2e32", "base": "#4a4a4e",
                       "roughness": 0.10, "transmission": 0.85,
                       "defaults": {"tint": 0.5}},
        },
        "layouts": ("solid",),
    },
}

LAYOUT_LABELS = {
    "solid": "Solid", "boards": "Boards", "parquet": "Parquet",
    "herringbone": "Herringbone", "chevron": "Chevron",
    "stack": "Stack", "stretcher": "Stretcher",
    "english": "English", "flemish": "Flemish",
    "basket_weave": "Basket Weave",
}

# Real-world sizes, in centimetres (SKULLPT's native unit; M11).
# No arbitrary 0-1 scales for sizes: a floor board is 12 cm wide, a
# brick is 21.5 cm long, a parquet cell is 30 cm square.
BOARD_WIDTH_CM = 12.0
BOARD_LENGTH_CM = 120.0
BRICK_LENGTH_CM = 21.5
BRICK_HEIGHT_CM = 6.5
BRICK_DEPTH_CM = 10.25
PARQUET_CELL_CM = 30.0
HERRINGBONE_LENGTH_CM = 60.0
HERRINGBONE_WIDTH_CM = 12.0
CHEVRON_WIDTH_CM = 12.0
STONE_LENGTH_CM = 40.0
STONE_HEIGHT_CM = 20.0
CONCRETE_LENGTH_CM = 40.0
CONCRETE_HEIGHT_CM = 20.0

# ---------------------------------------------------------------- params

# name: (label, minimum, maximum, default, unit, simple?, dial page)
# "simple" knobs are the ones the dial shows in Simple mode (M10:
# curated per family - seed, scale, and the one or two knobs that
# matter most for that family). The rest are Advanced-only. Pages:
# Detail, Form, Surface, Colour, Depth. Every family has seed,
# scale_cm, hue and depth; hue shifts the style colours, depth sets
# the bump strength.
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

BRICK_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "mortar_mm": ("Mortar", 1.0, 20.0, 10.0, "mm", True, "Form"),
    "variation": ("Variation", 0.0, 1.0, 0.5, "", True, "Detail"),
    "kiln": ("Kiln", 0.0, 1.0, 0.5, "", False, "Detail"),
    "gloss": ("Gloss", 0.0, 1.0, 0.2, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

STONE_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "veins": ("Veins", 0.0, 1.0, 0.5, "", True, "Detail"),
    "grain": ("Grain", 0.0, 1.0, 0.5, "", False, "Detail"),
    "polish": ("Polish", 0.0, 1.0, 0.3, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

MARBLE_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "veins": ("Veins", 0.0, 1.0, 0.6, "", True, "Detail"),
    "vein_width": ("Vein width", 0.0, 1.0, 0.4, "", False, "Detail"),
    "contrast": ("Contrast", 0.0, 1.0, 0.6, "", False, "Detail"),
    "polish": ("Polish", 0.0, 1.0, 0.7, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.3, "", False, "Depth"),
}

CONCRETE_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "aggregate": ("Aggregate", 0.0, 1.0, 0.5, "", True, "Detail"),
    "patches": ("Patches", 0.0, 1.0, 0.5, "", False, "Detail"),
    "polish": ("Polish", 0.0, 1.0, 0.2, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

METAL_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "brushed": ("Brushed", 0.0, 1.0, 0.3, "", True, "Detail"),
    "tarnish": ("Tarnish", 0.0, 1.0, 0.2, "", False, "Surface"),
    "gloss": ("Gloss", 0.0, 1.0, 0.6, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.3, "", False, "Depth"),
}

FABRIC_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "weave": ("Weave", 0.0, 1.0, 0.6, "", True, "Detail"),
    "fuzz": ("Fuzz", 0.0, 1.0, 0.4, "", False, "Detail"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

LEATHER_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "pores": ("Pores", 0.0, 1.0, 0.5, "", True, "Detail"),
    "creases": ("Creases", 0.0, 1.0, 0.4, "", False, "Detail"),
    "gloss": ("Gloss", 0.0, 1.0, 0.4, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.5, "", False, "Depth"),
}

GROUND_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "clumps": ("Clumps", 0.0, 1.0, 0.5, "", True, "Detail"),
    "stones": ("Stones", 0.0, 1.0, 0.4, "", False, "Detail"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.6, "", False, "Depth"),
}

PAINT_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "chip": ("Chip", 0.0, 1.0, 0.0, "", True, "Detail"),
    "orange_peel": ("Orange peel", 0.0, 1.0, 0.3, "", False,
                    "Detail"),
    "gloss": ("Gloss", 0.0, 1.0, 0.5, "", False, "Surface"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.3, "", False, "Depth"),
}

GLASS_PARAMS: dict[str, tuple] = {
    "seed": ("Seed", 0, 999999, 0, "", True, "Detail"),
    "scale_cm": ("Scale", 10.0, 1000.0, 120.0, "cm", True, "Form"),
    "frost": ("Frost", 0.0, 1.0, 0.0, "", True, "Detail"),
    "tint": ("Tint", 0.0, 1.0, 0.3, "", False, "Colour"),
    "hue": ("Hue", -0.5, 0.5, 0.0, "", False, "Colour"),
    "depth": ("Depth", 0.0, 1.0, 0.2, "", False, "Depth"),
}

PARAMS_BY_FAMILY = {
    "wood": WOOD_PARAMS, "brick": BRICK_PARAMS,
    "stone": STONE_PARAMS, "marble": MARBLE_PARAMS,
    "concrete": CONCRETE_PARAMS, "metal": METAL_PARAMS,
    "fabric": FABRIC_PARAMS, "leather": LEATHER_PARAMS,
    "ground": GROUND_PARAMS, "paint": PAINT_PARAMS,
    "glass": GLASS_PARAMS,
}

# Wear (design section 4e): rendered by the shared "SKULLPT Wear"
# node group in M9 (v1.12). All 0-1, default 0. Same 7-tuple shape
# as the param tables (label, min, max, default, unit, simple?,
# dial page) so the dial treats wear knobs like any other knob -
# except they live in spec["wear"], not spec["params"]. Simple
# wear knobs (dirt, edge wear, wet) show in Simple mode; the rest
# are Advanced-only. Every wear knob sits on the Life page.
WEAR_PARAMS: dict[str, tuple] = {
    "dirt": ("Dirt", 0.0, 1.0, 0.0, "", True, "Life"),
    "grime": ("Grime", 0.0, 1.0, 0.0, "", False, "Life"),
    "edge_wear": ("Edge wear", 0.0, 1.0, 0.0, "", True, "Life"),
    "edge_width": ("Edge width", 0.0, 1.0, 0.0, "", False, "Life"),
    "dust": ("Dust", 0.0, 1.0, 0.0, "", False, "Life"),
    "wet": ("Wet", 0.0, 1.0, 0.0, "", True, "Life"),
    "moss": ("Moss", 0.0, 1.0, 0.0, "", False, "Life"),
    "rust": ("Rust", 0.0, 1.0, 0.0, "", False, "Life"),
    "chips": ("Chips", 0.0, 1.0, 0.0, "", False, "Life"),
    "scratches": ("Scratches", 0.0, 1.0, 0.0, "", False, "Life"),
}

# Wear gating (design section 4e): moss only grows on wood, stone
# and ground; rust only on metal. Every other wear knob applies
# everywhere. Validation stays permissive (any wear key is accepted
# for any family) - gating only decides what the dial shows and
# what the wear layer renders.
WEAR_APPLIES: dict[str, set] = {
    "moss": {"wood", "stone", "ground"},
    "rust": {"metal"},
}


def wear_applies(family: str, name: str) -> bool:
    """True when a wear knob applies to a family."""
    allowed = WEAR_APPLIES.get(name)
    return allowed is None or family in allowed


# Dial pages in v1.12: ten. The Life page arrives with M9, between
# Surface and Colour (design section 4a order). Number keys 1-9
# pick pages 0-8; 0 picks page 9 (Saved).
PAGES = ("Material", "Style", "Layout", "Detail", "Form", "Surface",
         "Life", "Colour", "Depth", "Saved")

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
    style_name = spec.get("style") or next(iter(fam["styles"]))
    style = fam["styles"][style_name]
    # Defaults layer: table defaults, then the style's own defaults
    # (a frosted glass starts frosted), then the user's params.
    params = {name: table[name][3] for name in table}
    for name, value in (style.get("defaults") or {}).items():
        if name in params:
            params[name] = value
    params.update(spec.get("params") or {})
    wear = {name: WEAR_PARAMS[name][3] for name in WEAR_PARAMS}
    wear.update(spec.get("wear") or {})
    return {
        "family": family,
        "style": style_name,
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
    the dial swatch. Glass tints toward its dark colour by the
    tint param, so the tint shows everywhere without a template
    node."""
    norm = normalize_material_spec(spec)
    style = FAMILIES[norm["family"]]["styles"][norm["style"]]
    base = _shift_hue(style["base"], norm["params"]["hue"])
    if norm["family"] == "glass":
        tint = norm["params"]["tint"]
        if tint > 0:
            dark = _shift_hue(style["dark"], norm["params"]["hue"])
            br, bg, bb = _hex_to_rgb(base)
            dr, dg, db = _hex_to_rgb(dark)
            base = _rgb_to_hex((br + (dr - br) * tint,
                                bg + (dg - bg) * tint,
                                bb + (db - bb) * tint))
    return base


# ---------------------------------------------------------------- node counts

# Recipe nodes in make_material (blender_export), counted by hand.
# Every recipe starts from the same four: TexCoord, Mapping, the
# Principled BSDF and the Material Output.
#
# wood 17: TexWave, Math POWER, VectorMath MULTIPLY (fibre stretch),
#   TexNoise (fibre), TexNoise (blotch), MapRange, Math MULTIPLY
#   (ring), Math MULTIPLY (fibre), Math ADD, Mix (light/dark),
#   VectorMath SCALE (tone), Math ADD (bump), Bump = 13 + 4.
# brick 8: TexNoise (kiln), MapRange, Mix, Bump = 4 + 4.
# stone 10: Voronoi, TexNoise, ColorRamp, Mix, Math ADD (bump),
#   Bump = 6 + 4.
# marble 11: TexNoise (veins), TexNoise (distort), VectorMath
#   MULTIPLY, VectorMath ADD, ColorRamp, Mix, Bump = 7 + 4.
# concrete 10: TexNoise, Voronoi, ColorRamp, Mix, Math ADD, Bump.
# metal 9: VectorMath MULTIPLY (brush stretch), TexNoise (brush),
#   ColorRamp, Mix, Bump = 5 + 4.
# fabric 11: TexWave, TexWave, Math ADD, ColorRamp, Mix, TexNoise
#   (fuzz), Bump = 7 + 4.
# leather 10: Voronoi, TexNoise, ColorRamp, Mix, Math ADD, Bump.
# ground 10: TexNoise, Voronoi, ColorRamp, Mix, Math ADD, Bump.
# paint 8: TexNoise, ColorRamp, Mix, Bump = 4 + 4.
# glass 4: no pattern nodes (frost adds TexNoise + Bump).
_BASE_NODES = {
    "wood": 17, "brick": 8, "stone": 10, "marble": 11,
    "concrete": 10, "metal": 9, "fabric": 11, "leather": 10,
    "ground": 10, "paint": 8, "glass": 4,
}

# Layout extras, counted the same way. A bond (stack/stretcher) is
# VectorMath MULTIPLY (prescale) + TexBrick + VectorMath MULTIPLY
# (tone) = 3. English adds a row parity (SeparateXYZ + FLOOR +
# FLOORED_MODULO) and a second Brick + Mix = 8. Flemish adds the
# unit position (DIVIDE + FRACT + GREATER_THAN) and the course
# offset (FLOOR + FLOORED_MODULO + MULTIPLY + ADD) = 13. Basket
# weave is a 3-axis parity (8) + a 90-degree rotation (4) + prescale
# + Brick + tone = 15. Parquet is the same parity (8) + rotation (4)
# = 12. Herringbone is the parity (8) + a +-45-degree rotation (6)
# = 14. Chevron is prescale + SeparateXYZ + PINGPONG + ADD +
# SUBTRACT + CombineXYZ + unscale = 7. Knots add TexNoise +
# ColorRamp + Mix = 3. Paint chips add Voronoi + ColorRamp + Mix =
# 3. Glass frost adds TexNoise + Bump = 2. A worn material adds one
# node: the shared wear group instance (its 32 internal nodes live
# inside the group, reported separately as wear_nodes).
_LAYOUT_NODES = {
    "boards": 3, "parquet": 12, "herringbone": 14, "chevron": 7,
    "stack": 3, "stretcher": 3, "english": 8, "flemish": 13,
    "basket_weave": 15,
}
_KNOTS_NODES = 3
_CHIP_NODES = 3
_FROST_NODES = 2
_WEAR_GROUP_NODES = 1

# Nodes inside the shared "SKULLPT Wear" group (M9): Group Input,
# Geometry, SeparateXYZ, 2 MapRange, 2 Math (edge width), AO,
# TexNoise, 2 Math (deposit), Mix (deposit), Math + Mix (grime),
# Math + Mix (moss), Math + Mix (rust), ColorRamp + Math + Mix
# (chips), TexWave + Math + Mix (scratches), Math + Mix (edge),
# 3 Math (wet roughness), Group Output = 32.
WEAR_GROUP_NODE_COUNT = 32


def _gated_wear(norm: dict) -> tuple[dict, bool]:
    """The wear values that actually render for this family, and
    whether any of them is on. Inapplicable wear (moss on metal,
    rust on wood) is zeroed here; edge_width alone never turns the
    wear layer on, since it only shapes edge_wear."""
    family = norm["family"]
    wear = {name: (value if wear_applies(family, name) else 0.0)
            for name, value in norm["wear"].items()}
    wear_any = any(v > 0 for k, v in wear.items() if k != "edge_width")
    return wear, wear_any


def node_count(spec: dict) -> int:
    """The deterministic node count for a spec's recipe, including
    the Principled BSDF and the Material Output."""
    norm = normalize_material_spec(spec)
    family = norm["family"]
    total = _BASE_NODES[family]
    total += _LAYOUT_NODES.get(norm["layout"], 0)
    params = norm["params"]
    if family == "wood" and params["knots"] > 0:
        total += _KNOTS_NODES
    if family == "paint" and params["chip"] > 0:
        total += _CHIP_NODES
    if family == "glass" and params["frost"] > 0:
        total += _FROST_NODES
    _wear, wear_any = _gated_wear(norm)
    if wear_any:
        total += _WEAR_GROUP_NODES
    return total


# ---------------------------------------------------------------- resolution

def _resolve_common(norm: dict) -> dict:
    """The keys every family shares."""
    style = FAMILIES[norm["family"]]["styles"][norm["style"]]
    params = norm["params"]
    hue = params["hue"]
    wear, wear_any = _gated_wear(norm)
    return {
        "family": norm["family"],
        "style": norm["style"],
        "layout": norm["layout"],
        "breadcrumb": breadcrumb(norm),
        "light": _hex_to_rgb(_shift_hue(style["light"], hue)),
        "dark": _hex_to_rgb(_shift_hue(style["dark"], hue)),
        "roughness": style["roughness"],
        "metallic": style.get("metallic", 0.0),
        "transmission": style.get("transmission", 0.0),
        "bump": 0.05 + params["depth"] * 0.3,
        "coord_scale": 100.0 / params["scale_cm"],
        "flow": params.get("flow", 0.0),
        "seed_loc": _seed_offset(params["seed"]),
        "wear": wear,
        "wear_any": wear_any,
        "nodes": node_count(norm),
    }


def _resolve_wood(norm: dict, out: dict) -> dict:
    style = FAMILIES["wood"]["styles"][norm["style"]]
    params = norm["params"]
    out["roughness"] = style["roughness"] * (1.0 - params["gloss"] * 0.5)
    out["bump"] = (0.05 + params["depth"] * 0.3) * (0.5 + params["pores"])
    # Rings per metre from the ring spacing, then the wave scale
    # (1/2pi converts rings-per-metre to the wave node's scale).
    rings_per_m = 1000.0 / params["ring_spacing_mm"]
    out["ring_scale"] = rings_per_m * 0.159155
    out["distort"] = params["grain_lines"] * 4.0
    out["fibre_k"] = params["figure"] * 0.5
    knots = params["knots"]
    out["knots"] = knots
    out["knot_scale"] = 14.0
    # The knot mask is a ColorRamp over high-frequency noise: the
    # knots value moves the ramp thresholds, so more knots means
    # more of the noise passes as knots. No extra Math node needed.
    knot_low = max(0.55, 0.92 - knots * 0.35)
    out["knot_low"] = knot_low
    out["knot_high"] = min(0.99, knot_low + 0.08)
    cs = out["coord_scale"]
    layout = norm["layout"]
    out["boards"] = layout == "boards"
    # Board prescale: the Brick node reads the mapped coordinates
    # and makes one cell per unit, so the coordinates are scaled
    # into board units (length along X, width along Y).
    if layout == "boards":
        out["board_prescale"] = (
            1.0 / (BOARD_LENGTH_CM / 100.0 * cs),
            1.0 / (BOARD_WIDTH_CM / 100.0 * cs),
            1.0,
        )
    else:
        out["board_prescale"] = None
    # Layout parity prescales (M8): the parity grid is a 3-axis
    # checkerboard, so every face of the part gets the pattern.
    # Parquet cells are square on all three axes; herringbone cells
    # are plank-shaped, long on texture Z (the grain axis).
    if layout == "parquet":
        cell = 1.0 / (PARQUET_CELL_CM / 100.0 * cs)
        out["parity_prescale"] = (cell, cell, cell)
    elif layout == "herringbone":
        w = 1.0 / (HERRINGBONE_WIDTH_CM / 100.0 * cs)
        ln = 1.0 / (HERRINGBONE_LENGTH_CM / 100.0 * cs)
        out["parity_prescale"] = (w, w, ln)
    else:
        out["parity_prescale"] = None
    # Chevron folds one face axis; which axis is a part-size
    # question, answered in blender_export._part_material_spec.
    out["chevron_cell_cm"] = CHEVRON_WIDTH_CM
    return out


def _resolve_bond(norm: dict, out: dict, length_cm: float,
                  height_cm: float) -> dict:
    """The bond block for brick, stone and concrete: the real-world
    block size plus the bond's own constants. The face rotation and
    the prescale need the part size, so blender_export fills those
    in (_part_material_spec)."""
    layout = norm["layout"]
    bond = None
    if layout in ("stack", "stretcher", "english", "flemish",
                  "basket_weave"):
        bond = {
            "kind": layout,
            "length_cm": length_cm,
            "height_cm": height_cm,
            # Brick Bias 0 is a stack bond (joints align),
            # Bias 1 is a running bond (half-brick stagger) -
            # probed on Blender 4.5.9.
            "bias": 0.0 if layout == "stack" else 1.0,
        }
        if layout == "flemish":
            # One Flemish unit is a stretcher plus a header, in
            # cell units (one cell is one brick height): the
            # stretcher occupies the first length/(length+depth)
            # of the unit, the header the rest.
            bond["unit_ratio"] = (
                (BRICK_LENGTH_CM + BRICK_DEPTH_CM) / BRICK_HEIGHT_CM)
            bond["header_frac"] = (
                BRICK_LENGTH_CM
                / (BRICK_LENGTH_CM + BRICK_DEPTH_CM))
    out["bond"] = bond
    return out


def _resolve_brick(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["brick"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["gloss"] * 0.5)
    # Mortar as a fraction of the brick height: 10 mm on a 65 mm
    # brick is 0.154 of a cell.
    out["mortar"] = params["mortar_mm"] / (BRICK_HEIGHT_CM * 10.0)
    out["variation"] = params["variation"]
    out["kiln_scale"] = 4.0 + params["kiln"] * 8.0
    return _resolve_bond(norm, out, BRICK_LENGTH_CM, BRICK_HEIGHT_CM)


def _resolve_stone(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["stone"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["polish"] * 0.6)
    out["vein_scale"] = 2.0 + params["veins"] * 6.0
    out["grain_scale"] = 6.0 + params["grain"] * 10.0
    return _resolve_bond(norm, out, STONE_LENGTH_CM, STONE_HEIGHT_CM)


def _resolve_marble(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["marble"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["polish"] * 0.6)
    out["vein_scale"] = 1.5 + params["veins"] * 4.0
    out["vein_width"] = 0.05 + params["vein_width"] * 0.3
    out["contrast"] = 0.3 + params["contrast"] * 0.6
    out["bond"] = None
    return out


def _resolve_concrete(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["concrete"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["polish"] * 0.6)
    out["aggregate_scale"] = 8.0 + params["aggregate"] * 24.0
    out["patch_scale"] = 1.0 + params["patches"] * 4.0
    return _resolve_bond(norm, out, CONCRETE_LENGTH_CM,
                         CONCRETE_HEIGHT_CM)


def _resolve_metal(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["metal"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["gloss"] * 0.5)
    out["brushed"] = params["brushed"]
    out["tarnish"] = params["tarnish"]
    out["bond"] = None
    return out


def _resolve_fabric(norm: dict, out: dict) -> dict:
    params = norm["params"]
    out["weave_scale"] = 8.0 + params["weave"] * 24.0
    out["fuzz_scale"] = 20.0 + params["fuzz"] * 40.0
    out["bond"] = None
    return out


def _resolve_leather(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["leather"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["gloss"] * 0.5)
    out["pore_scale"] = 20.0 + params["pores"] * 40.0
    out["crease_scale"] = 2.0 + params["creases"] * 6.0
    out["bond"] = None
    return out


def _resolve_ground(norm: dict, out: dict) -> dict:
    params = norm["params"]
    out["clump_scale"] = 2.0 + params["clumps"] * 6.0
    out["stone_scale"] = 4.0 + params["stones"] * 12.0
    out["bond"] = None
    return out


def _resolve_paint(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["paint"]["styles"][norm["style"]]
    out["roughness"] = style["roughness"] * (1.0 - params["gloss"] * 0.5)
    out["chip"] = params["chip"]
    out["peel_scale"] = 8.0 + params["orange_peel"] * 24.0
    chip_low = max(0.55, 0.92 - params["chip"] * 0.35)
    out["chip_low"] = chip_low
    out["chip_high"] = min(0.99, chip_low + 0.08)
    out["bond"] = None
    return out


def _resolve_glass(norm: dict, out: dict) -> dict:
    params = norm["params"]
    style = FAMILIES["glass"]["styles"][norm["style"]]
    out["roughness"] = min(1.0, style["roughness"]
                           + params["frost"] * 0.5)
    out["frost"] = params["frost"]
    out["frost_scale"] = 6.0 + params["frost"] * 10.0
    out["tint"] = params["tint"]
    out["bond"] = None
    return out


_RESOLVERS = {
    "wood": _resolve_wood, "brick": _resolve_brick,
    "stone": _resolve_stone, "marble": _resolve_marble,
    "concrete": _resolve_concrete, "metal": _resolve_metal,
    "fabric": _resolve_fabric, "leather": _resolve_leather,
    "ground": _resolve_ground, "paint": _resolve_paint,
    "glass": _resolve_glass,
}


def resolve_spec(spec: dict) -> dict:
    """Resolve a spec to the host-side constants the Blender recipe
    needs. All units are converted here: the template just wires
    nodes. JSON-serialisable (tuples become lists on dump)."""
    norm = normalize_material_spec(spec)
    out = _resolve_common(norm)
    return _RESOLVERS[norm["family"]](norm, out)


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
