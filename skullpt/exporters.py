"""Exporters — one Description File, many outputs.

Skeleton v0.1 ships two reference exporters proving the pattern:
- web_ui: a standalone preview page + component CSS + tokens CSS
- svg_mock: a flat SVG mock of the component (doubles as its thumbnail)

Studios to come plug in the same way (blender_scene, unreal_level, ...):
an exporter is a function (desc, out_dir) -> list of output file names.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import recipes


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def export_web_ui(desc: dict, out_dir: Path) -> list[str]:
    """Render parts with the Recipe Library into a previewable web component."""
    body_parts: list[str] = []
    panel_id = None
    for part in desc["parts"]:
        fn = recipes.HTML_RECIPES.get(part["type"])
        if fn is None:
            fragment = (f'<div class="ds-unknown" id="{_esc(part["id"])}">'
                        f'{_esc(part["type"])}: {_esc(part["id"])}</div>')
        else:
            fragment = fn(part, desc)
        if part["type"] == "panel" and panel_id is None:
            panel_id = part["id"]
            body_parts.append(fragment)
        else:
            body_parts.append(fragment)
    # Nest non-panel fragments inside the panel body when a panel exists.
    if panel_id is not None and len(body_parts) > 1:
        panel_html, rest = body_parts[0], body_parts[1:]
        inner = "\n  ".join(rest)
        body = panel_html.replace(
            f'<div class="ds-panel-body" data-part-body="{_esc(panel_id)}"></div>',
            f'<div class="ds-panel-body" data-part-body="{_esc(panel_id)}">\n  {inner}\n  </div>')
        body_parts = [body]
    slug = _slug(desc["name"])
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(desc['name'])}</title>
<link rel="stylesheet" href="{slug}-tokens.css">
<link rel="stylesheet" href="{slug}.css">
</head>
<body>
{chr(10).join(body_parts)}
</body>
</html>
"""
    files = {
        f"{slug}.html": page,
        f"{slug}.css": recipes.component_css(desc),
        f"{slug}-tokens.css": recipes.tokens_css(desc),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    return sorted(files)


def export_svg_mock(desc: dict, out_dir: Path) -> list[str]:
    """A flat SVG mock: panel + a labelled row per part. Also the thumbnail."""
    style = desc.get("style") or {}
    bg = str(style.get("surface", "#1c1a17"))
    fg = str(style.get("text", "#f2ede4"))
    accent = str(style.get("accent", "#b7f04a"))
    rows = []
    y = 64
    for part in desc["parts"]:
        label = part.get("text") or part.get("title") or part["id"]
        fill = accent if part["type"] == "button" and part.get("variant") == "primary" else "#2a2721"
        text_fill = "#14120e" if fill == accent else fg
        rows.append(
            f'<rect x="24" y="{y}" width="352" height="34" rx="8" fill="{fill}"/>'
            f'<text x="36" y="{y + 22}" font-family="sans-serif" font-size="14" '
            f'fill="{text_fill}">{_esc(label)}</text>')
        y += 46
    height = y + 16
    slug = _slug(desc["name"])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="{height}" viewBox="0 0 400 {height}">
<rect width="400" height="{height}" rx="12" fill="{bg}"/>
<text x="24" y="38" font-family="sans-serif" font-size="19" font-weight="bold" fill="{fg}">{_esc(desc['name'])}</text>
{chr(10).join(rows)}
</svg>
"""
    name = f"{slug}.svg"
    (out_dir / name).write_text(svg, encoding="utf-8")
    return [name]


from .blender_export import export_blender_scene  # noqa: E402
from .blender_character import export_blender_character  # noqa: E402

EXPORTERS = {
    "web_ui": export_web_ui,
    "svg_mock": export_svg_mock,
    "blender_scene": export_blender_scene,
    "blender_character": export_blender_character,
}

# Which exporters run by default per description kind.
DEFAULT_EXPORTERS = {
    "ui_component": ("web_ui", "svg_mock"),
    "website": ("web_ui", "svg_mock"),
    "graphic": ("svg_mock",),
    "character": ("blender_character",),
    "scene": ("blender_scene",),
    "project": (),
}


def _slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "asset"
