"""The Recipe Library — tested building blocks, not raw generation.

A recipe is a small function that renders ONE kind of part (a panel, a
button, a token sheet) from its parameters in the Description File. The
model's job in later studios is to choose recipes and fill parameters —
the geometry/markup itself lives here, written once and covered by tests.

Recipes graduate: when an approved job invents a pattern worth keeping, it
gets distilled into a new recipe here (library learning), and the shelf
grows. Each recipe also has a JSON manifest in ../recipes/ describing it.
"""
from __future__ import annotations

import html


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _style(desc: dict, key: str, default: str) -> str:
    return str((desc.get("style") or {}).get(key, default))


# -- HTML recipes (each returns an HTML fragment using the part's id) ------

def recipe_panel(part: dict, desc: dict) -> str:
    title = _esc(part.get("title", desc["name"]))
    return (
        f'<section class="ds-panel" id="{_esc(part["id"])}">\n'
        f'  <h2 class="ds-panel-title">{title}</h2>\n'
        f'  <div class="ds-panel-body" data-part-body="{_esc(part["id"])}"></div>\n'
        f"</section>"
    )


def recipe_button(part: dict, desc: dict) -> str:
    label = _esc(part.get("text", part["id"]))
    variant = _esc(part.get("variant", "default"))
    return (
        f'<button class="ds-btn ds-btn-{variant}" id="{_esc(part["id"])}" '
        f'type="button">{label}</button>'
    )


def recipe_text(part: dict, desc: dict) -> str:
    return f'<p class="ds-text" id="{_esc(part["id"])}">{_esc(part.get("text", ""))}</p>'


def recipe_image_slot(part: dict, desc: dict) -> str:
    alt = _esc(part.get("alt", part["id"]))
    return f'<div class="ds-image-slot" id="{_esc(part["id"])}" role="img" aria-label="{alt}">{alt}</div>'


HTML_RECIPES = {
    "panel": recipe_panel,
    "button": recipe_button,
    "text": recipe_text,
    "image_slot": recipe_image_slot,
}

# Part types a recipe does not know are rendered as a labelled placeholder
# by the exporter — visible in previews, never silently dropped.


def tokens_css(desc: dict) -> str:
    """Design tokens from the description's style block, as CSS variables."""
    style = desc.get("style") or {}
    lines = [":root {"]
    for key in sorted(style):
        var = "--ds-" + key.replace("_", "-")
        lines.append(f"  {var}: {style[key]};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def component_css(desc: dict) -> str:
    """Base component stylesheet driven entirely by the tokens above."""
    return """\
.ds-panel { background: var(--ds-surface, #1c1a17); color: var(--ds-text, #f2ede4);
  border: 1px solid var(--ds-border, #3a352d); border-radius: 12px;
  padding: 16px; max-width: 420px; font-family: var(--ds-font, system-ui, sans-serif); }
.ds-panel-title { margin: 0 0 12px; font-size: 20px; letter-spacing: .02em; }
.ds-panel-body { display: flex; flex-direction: column; gap: 10px; }
.ds-btn { font: inherit; padding: 10px 14px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--ds-border, #3a352d);
  background: var(--ds-button, #2a2721); color: var(--ds-text, #f2ede4); }
.ds-btn-primary { background: var(--ds-accent, #b7f04a); color: #14120e; font-weight: 700; }
.ds-btn:hover { filter: brightness(1.15); }
.ds-text { margin: 0; line-height: 1.45; }
.ds-image-slot { border: 1px dashed var(--ds-border, #3a352d); border-radius: 8px;
  min-height: 96px; display: grid; place-items: center; opacity: .75; }
"""
