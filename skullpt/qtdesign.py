"""The SKULLPT look, in one place (R3).

Tokens come from DESIGN.md (the single source of truth for how the
Bench looks). The amber accent is sampled from skullpt-icon.svg at
runtime - the icon is the single source of truth for the accent, so
the hex is never guessed in code. If the icon cannot be read, the
documented amber is used as a fallback (see DESIGN.md).

Pure parts (tokens, amber sampling, contrast math) work without Qt;
the font/icon/stylesheet parts need PySide6 and degrade silently
when it is missing.
"""
from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_ICON_PATH = _ROOT / "skullpt-icon.svg"
_ICONS_DIR = _HERE / "icons"
_FONTS_DIR = _HERE / "fonts"

# The documented amber (DESIGN.md). Only used when the icon cannot
# be read - the live value always comes from sample_amber().
_FALLBACK_AMBER = "#f5a623"

# Warp-derived tokens, skull-adapted (DESIGN.md). Canvas-soft and
# hairline are Warp's steps above its canvas, applied to SKULLPT's
# canvas; muted/body are Warp's text tones, which pass AA on canvas.
CANVAS = "#1a1a1f"
CANVAS_SOFT = "#27272d"
HAIRLINE = "#2e2e33"
BONE = "#e8e4d8"
BODY = "#c9c0ad"
MUTED = "#aea69c"
NEAR_BLACK = "#1a1a1f"

# The Lucide icons the Bench uses (R2). The file test checks each
# one exists under skullpt/icons/.
ICONS = (
    "layout-grid", "box", "image", "library", "settings", "help-circle",
    "plus", "play", "check", "x", "trash-2", "copy", "pencil",
    "rotate-ccw", "refresh-cw", "download", "upload", "folder-open",
    "camera", "eye", "search", "chevron-down", "chevron-right",
    "alert-triangle", "info", "file-text", "clipboard", "save",
    "sun", "moon", "zoom-in", "zoom-out", "move", "maximize",
    "grid", "layers", "scissors", "wand-2",
)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, v)) for v in rgb)


def _chroma(hex_value: str) -> int:
    r, g, b = _hex_to_rgb(hex_value)
    return max(r, g, b) - min(r, g, b)


def sample_amber(icon_path: Path | None = None) -> str:
    """The amber accent, read from the icon file: the most saturated
    hex in the SVG. The icon carries exactly one chromatic color
    (the amber crack, v1.5); canvas and bone are near-grey and lose
    on chroma by a wide margin."""
    path = Path(icon_path) if icon_path is not None else _ICON_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_AMBER
    found = re.findall(r"#[0-9a-fA-F]{6}", text)
    if not found:
        return _FALLBACK_AMBER
    best = max(found, key=_chroma)
    return best.lower()


def AMBER() -> str:
    """The live accent hex (sampled, not hard-coded)."""
    return sample_amber()


def lighten(hex_value: str, amount: float) -> str:
    """Mix toward white by `amount` (0..1). Hover states."""
    r, g, b = _hex_to_rgb(hex_value)
    return _rgb_to_hex(tuple(round(v + (255 - v) * amount) for v in (r, g, b)))


def darken(hex_value: str, amount: float) -> str:
    """Mix toward black by `amount` (0..1). Pressed states."""
    r, g, b = _hex_to_rgb(hex_value)
    return _rgb_to_hex(tuple(round(v * (1.0 - amount)) for v in (r, g, b)))


def mix(first: str, second: str, amount: float) -> str:
    """Mix `first` toward `second` by `amount` (0..1)."""
    a = _hex_to_rgb(first)
    b = _hex_to_rgb(second)
    return _rgb_to_hex(tuple(round(x + (y - x) * amount) for x, y in zip(a, b)))


# ------------------------------------------------------------- contrast (R4)

def _luminance(hex_value: str) -> float:
    def linear(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(hex_value)
    return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colors (1..21)."""
    light = _luminance(foreground)
    dark = _luminance(background)
    high, low = max(light, dark), min(light, dark)
    return (high + 0.05) / (low + 0.05)


def contrast_table() -> list[tuple[str, str, str, float, bool]]:
    """Every text pair the Bench uses: (label, fg, bg, ratio, AA pass).
    AA is 4.5:1 for normal text."""
    amber = AMBER()
    pairs = [
        ("body text on canvas", BONE, CANVAS),
        ("secondary text on canvas", BODY, CANVAS),
        ("muted text on canvas", MUTED, CANVAS),
        ("body text on canvas-soft", BONE, CANVAS_SOFT),
        ("muted text on canvas-soft", MUTED, CANVAS_SOFT),
        ("primary button text on amber", NEAR_BLACK, amber),
        ("amber text on canvas", amber, CANVAS),
        ("amber text on canvas-soft", amber, CANVAS_SOFT),
        ("primary hover text", NEAR_BLACK, lighten(amber, 0.08)),
        ("primary pressed text", NEAR_BLACK, darken(amber, 0.08)),
    ]
    return [(label, fg, bg, contrast_ratio(fg, bg),
             contrast_ratio(fg, bg) >= 4.5)
            for label, fg, bg in pairs]


# ------------------------------------------------------------------ Qt parts

try:
    from PySide6 import QtCore, QtGui, QtWidgets

    _QT = True
except Exception:  # PySide6 missing - the pure parts still work
    _QT = False

_ICON_CACHE: dict[str, object] = {}
_FONTS_LOADED = False


def load_fonts() -> list[str]:
    """Register the bundled Inter + JetBrains Mono with Qt.
    Returns the family names that registered (empty without Qt)."""
    global _FONTS_LOADED
    if not _QT or _FONTS_LOADED:
        return []
    _FONTS_LOADED = True
    loaded: list[str] = []
    db = QtGui.QFontDatabase()
    for name in ("Inter.ttf", "JetBrainsMono.ttf"):
        path = _FONTS_DIR / name
        if not path.exists():
            continue
        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            loaded.extend(db.applicationFontFamilies(font_id))
    return loaded


def load_icon(name: str, color: str = BONE, size: int = 24):
    """A QIcon from a vendored Lucide SVG, recolored to `color`.
    Returns an empty QIcon when Qt or the file is missing."""
    if not _QT:
        return None
    key = f"{name}|{color}|{size}"
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    icon = QtGui.QIcon()
    path = _ICONS_DIR / f"{name}.svg"
    try:
        svg = path.read_text(encoding="utf-8")
    except OSError:
        _ICON_CACHE[key] = icon
        return icon
    svg = svg.replace("currentColor", color)
    try:
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
        if renderer.isValid():
            pixmap = QtGui.QPixmap(size, size)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            icon = QtGui.QIcon(pixmap)
    except Exception:
        icon = QtGui.QIcon()
    _ICON_CACHE[key] = icon
    return icon


def build_stylesheet() -> str:
    """The identity stylesheet (R3): warm near-black canvas, bone
    text, amber as the only chromatic accent, hairline-only
    elevation, 4px buttons, 6px cards."""
    amber = AMBER()
    amber_hover = lighten(amber, 0.08)
    amber_pressed = darken(amber, 0.08)
    amber_disabled = mix(amber, CANVAS, 0.55)
    soft_hover = lighten(CANVAS_SOFT, 0.06)
    return f"""
QMainWindow, QDialog, QWidget {{
    background: {CANVAS};
    color: {BONE};
    font-family: "Inter", "DejaVu Sans", sans-serif;
    font-size: 13px;
}}
QLabel#muted {{ color: {MUTED}; }}
QLabel#small {{ font-size: 11px; color: {MUTED}; }}
QLabel#heading {{ font-size: 15px; font-weight: 600; }}
QLabel#mono, QTextEdit#mono, QPlainTextEdit#mono {{
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}}
QPushButton {{
    background: {CANVAS_SOFT};
    color: {BONE};
    border: 1px solid {HAIRLINE};
    border-radius: 4px;
    min-height: 34px;
    padding: 4px 14px;
}}
QPushButton:hover {{ background: {soft_hover}; }}
QPushButton:pressed {{ background: {CANVAS}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {CANVAS_SOFT}; }}
QPushButton:focus {{ border: 1px solid {amber}; }}
QPushButton#primary {{
    background: {amber};
    color: {NEAR_BLACK};
    border: 1px solid {amber};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {amber_hover}; border-color: {amber_hover}; }}
QPushButton#primary:pressed {{ background: {amber_pressed}; border-color: {amber_pressed}; }}
QPushButton#primary:disabled {{ background: {amber_disabled}; border-color: {amber_disabled}; color: {MUTED}; }}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {BODY};
}}
QPushButton#ghost:hover {{ background: {CANVAS_SOFT}; color: {BONE}; }}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    color: {BODY};
    padding: 6px;
}}
QToolButton:hover {{ background: {CANVAS_SOFT}; color: {BONE}; }}
QToolButton:checked {{
    background: {CANVAS_SOFT};
    color: {BONE};
    border-left: 2px solid {amber};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {CANVAS_SOFT};
    color: {BONE};
    border: 1px solid {HAIRLINE};
    border-radius: 4px;
    padding: 5px 8px;
    selection-background-color: {amber};
    selection-color: {NEAR_BLACK};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {amber};
}}
QLineEdit:disabled, QTextEdit:disabled {{ color: {MUTED}; }}
QFrame#card {{
    background: {CANVAS_SOFT};
    border: 1px solid {HAIRLINE};
    border-radius: 6px;
}}
QFrame#card:hover {{ border-color: {lighten(HAIRLINE, 0.15)}; }}
QFrame#cardSelected {{
    background: {CANVAS_SOFT};
    border: 1px solid {amber};
    border-radius: 6px;
}}
QMenuBar {{ background: {CANVAS}; color: {BONE}; }}
QMenuBar::item:selected {{ background: {CANVAS_SOFT}; }}
QMenu {{
    background: {CANVAS_SOFT};
    color: {BONE};
    border: 1px solid {HAIRLINE};
}}
QMenu::item:selected {{ background: {soft_hover}; }}
QListWidget {{
    background: {CANVAS};
    border: 1px solid {HAIRLINE};
    border-radius: 4px;
}}
QListWidget::item:selected {{ background: {CANVAS_SOFT}; color: {BONE}; }}
QTabWidget::pane {{ border: 1px solid {HAIRLINE}; background: {CANVAS}; }}
QTabBar::tab {{
    background: {CANVAS};
    color: {MUTED};
    padding: 6px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {BONE}; border-bottom: 2px solid {amber}; }}
QProgressBar {{
    background: {CANVAS_SOFT};
    border: 1px solid {HAIRLINE};
    border-radius: 4px;
    text-align: center;
    color: {BONE};
}}
QProgressBar::chunk {{ background: {amber}; border-radius: 3px; }}
QScrollBar:vertical {{
    background: {CANVAS};
    width: 10px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {HAIRLINE};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar:horizontal {{
    background: {CANVAS};
    height: 10px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {HAIRLINE};
    border-radius: 4px;
    min-width: 24px;
}}
QSplitter::handle {{ background: {HAIRLINE}; }}
QStatusBar {{ background: {CANVAS}; border-top: 1px solid {HAIRLINE}; color: {BODY}; }}
QToolTip {{
    background: {CANVAS_SOFT};
    color: {BONE};
    border: 1px solid {HAIRLINE};
    padding: 4px 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {HAIRLINE};
    border-radius: 3px;
    background: {CANVAS_SOFT};
}}
QCheckBox::indicator:checked {{
    background: {amber};
    border-color: {amber};
}}
QGroupBox {{
    border: 1px solid {HAIRLINE};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    color: {BONE};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {BODY}; }}
"""


def apply_theme(app) -> None:
    """Load the bundled fonts and apply the identity stylesheet."""
    if not _QT:
        return
    load_fonts()
    app.setStyleSheet(build_stylesheet())
    font = QtGui.QFont("Inter", 10)
    font.setStyleHint(QtGui.QFont.SansSerif)
    app.setFont(font)
