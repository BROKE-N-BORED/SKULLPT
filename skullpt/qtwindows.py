"""The Bench's windows (R5): Render, Character Studio, Images, Library,
Recipes, Settings, Help, plus the shared pieces (stage pills, job cards,
toasts, debug report).

Each window does one job (U1). Every window remembers its geometry
(U5). No em dashes anywhere in this file (U33); visible labels use
"and", never a bare "&" (U11).
"""
from __future__ import annotations

import datetime
import json
import subprocess
import threading
import time
from pathlib import Path

from . import bench as bench_mod
from . import graduate
from . import imagegen
from . import keycheck
from . import materials as materials_mod
from . import settings as settings_mod
from . import tokenfile
from .blender_export import MATERIALS
from .expand import expand_parts
from .jobs import Job
from .library import Library
from .qtdesign import (AMBER, BODY, BONE, CANVAS, CANVAS_SOFT, HAIRLINE,
                       MUTED, NEAR_BLACK, load_icon)

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import isValid as _qt_is_valid

    QT_AVAILABLE = True
except Exception:  # PySide6 missing - the CLI reports it plainly
    QT_AVAILABLE = False


# ------------------------------------------------------------ geometry (T13)

def _settings() -> "QtCore.QSettings":
    return QtCore.QSettings("BrokeNBored", "SKULLPT")


# S6: a window smaller than this on the 1366x768 screen cannot show
# its content: a saved geometry under this floor falls back to
# maximized instead of reopening tiny and trapping content.
MIN_USABLE_WIDTH = 900
MIN_USABLE_HEIGHT = 560
_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 720


def restore_geometry(window, key: str) -> bool:
    """Restore a saved geometry. Returns True when a usable saved
    geometry was restored. When none was saved (first run), or the
    saved one is under the usable floor (S6), the window starts
    maximized instead, recorded on the window as _start_maximized,
    which show_window() reads. The saved geometry is still applied
    first, so a round-trip test sees the saved size."""
    geometry = _settings().value(f"{key}/geometry")
    window._start_maximized = True
    if geometry is None:
        return False
    window.restoreGeometry(geometry)
    if window.isMaximized():
        return True
    if (window.width() >= MIN_USABLE_WIDTH
            and window.height() >= MIN_USABLE_HEIGHT):
        window._start_maximized = False
        return True
    return False


def show_window(window) -> None:
    """Show a window the S6 way: maximized on first run, or when the
    saved geometry was too small to read; at its saved size
    otherwise. A maximized window's normal geometry is the size it
    returns to when un-maximized, so a tiny one is widened first."""
    if getattr(window, "_start_maximized", False):
        if (window.width() < MIN_USABLE_WIDTH
                or window.height() < MIN_USABLE_HEIGHT):
            window.resize(_DEFAULT_WIDTH, _DEFAULT_HEIGHT)
        window.showMaximized()
    else:
        window.show()


def save_geometry(window, key: str) -> None:
    _settings().setValue(f"{key}/geometry", window.saveGeometry())


class _GeometryWindow(QtWidgets.QMainWindow):
    """A window that remembers its size and position (U5)."""

    GEOMETRY_KEY = "Window"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._geometry_key = self.GEOMETRY_KEY
        # Until restore_geometry says otherwise, first open is
        # maximized (S6).
        self._start_maximized = True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        save_geometry(self, self._geometry_key)
        super().closeEvent(event)


# ------------------------------------------------------- background calls

class _BackgroundCall(QtCore.QObject):
    """Run a function off the UI thread (U30), call back on it.

    `finished` emits the function's return value (or the exception
    it raised, so the callback can report it plainly. Qt queues the
    signal across threads, so the callback always runs on the UI
    thread. Keep a reference until it finishes; the thread holds the
    call alive, and a deleted receiver drops the signal safely."""

    finished = QtCore.Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - the callback reports it
            result = exc
        self.finished.emit(result)


class _ProgressEmitter(QtCore.QObject):
    """A thread-safe progress line (v1.10, N35): emit from any thread,
    the connected slot runs on the UI thread."""

    line = QtCore.Signal(str)


# ------------------------------------------------------------- stage pill (R6)

# State -> (pill text, fg, bg, border). Monochrome + amber only: the
# approved state is amber, failed reads via its text, not a red.
_PILL_STYLES = {
    "awaiting_approval": ("DRAFT", BONE, CANVAS_SOFT, HAIRLINE),
    "approved": ("APPROVED", NEAR_BLACK, AMBER(), AMBER()),
    "building": ("BUILDING", BONE, CANVAS_SOFT, AMBER()),
    "checking": ("CHECKING", BONE, CANVAS_SOFT, AMBER()),
    "awaiting_review": ("REVIEW", BONE, CANVAS_SOFT, HAIRLINE),
    "filed": ("FILED", MUTED, CANVAS_SOFT, HAIRLINE),
    "failed": ("FAILED", BONE, CANVAS_SOFT, HAIRLINE),
}

_STAGE_LABELS = {
    "plan": "PLAN", "build": "BUILD", "check": "CHECK",
    "critic": "CRITIC", "images": "IMAGES", "mesh": "MESH",
}


class StagePill(QtWidgets.QLabel):
    """A small uppercase pill for a job state or a build stage."""

    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("pill")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed,
                           QtWidgets.QSizePolicy.Fixed)
        self._apply_style(BONE, CANVAS_SOFT, HAIRLINE)

    def _apply_style(self, fg: str, bg: str, border: str) -> None:
        self.setStyleSheet(
            "QLabel#pill { color: %s; background: %s; "
            "border: 1px solid %s; border-radius: 8px; "
            "padding: 2px 8px; font-size: 10px; font-weight: 600; }"
            % (fg, bg, border))

    def set_state(self, state: str) -> None:
        text, fg, bg, border = _PILL_STYLES.get(
            state, (state.upper(), BONE, CANVAS_SOFT, HAIRLINE))
        self.setText(text)
        self._apply_style(fg, bg, border)

    def set_stage(self, stage: str) -> None:
        self.setText(_STAGE_LABELS.get(stage, stage.upper()))
        self._apply_style(BONE, CANVAS_SOFT, AMBER())


# ------------------------------------------------------------- job card (R7)

# The gate button for each state: (button text, action). A filed job
# has nothing left to gate, so its button opens the Render window.
_GATE_BUTTONS = {
    "awaiting_approval": ("Approve", "approve"),
    "approved": ("Build", "build"),
    "building": ("Building", None),
    "checking": ("Checking", None),
    "awaiting_review": ("File", "file"),
    "failed": ("Retry", "retry"),
    "filed": ("Open", "open"),
}


class JobCard(QtWidgets.QFrame):
    """One job in the bento grid: state pill, title, summary, and the
    gate button for its state. Clicking the card selects the job."""

    cardClicked = QtCore.Signal(str)  # job name
    gateClicked = QtCore.Signal(str, str)  # job name, action

    def __init__(self, job: dict, parent=None):
        super().__init__(parent)
        self.job_name = job["name"]
        self.setObjectName("card")
        self.setMinimumHeight(96)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QtWidgets.QHBoxLayout()
        self.pill = StagePill(job["state"])
        self.pill.set_state(job["state"])
        top.addWidget(self.pill)
        top.addStretch(1)
        self._title = QtWidgets.QLabel(job["title"])
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight: 600;")
        top.addWidget(self._title, 1)
        layout.addLayout(top)

        # The plain summary only (v1.14, A3): the raw parts list used
        # to print here verbatim, which defeated the plan gate and
        # made the card tall enough to stretch the whole grid row.
        self._summary = QtWidgets.QLabel(
            job.get("card_summary") or job.get("summary") or "")
        self._summary.setObjectName("muted")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._gate_btn = QtWidgets.QPushButton()
        text, action = _GATE_BUTTONS.get(job["state"], ("Open", "open"))
        self._gate_action = action
        self._gate_btn.setText(text)
        self._gate_btn.setEnabled(action is not None)
        if action == "approve":
            self._gate_btn.setObjectName("primary")
        self._gate_btn.clicked.connect(
            lambda: self.gateClicked.emit(self.job_name,
                                          self._gate_action or ""))
        layout.addWidget(self._gate_btn)

    def update_job(self, job: dict) -> None:
        """Refresh the card in place (T17): same widget, new state."""
        self.pill.set_state(job["state"])
        self._title.setText(job["title"])
        self._summary.setText(
            job.get("card_summary") or job.get("summary") or "")
        text, action = _GATE_BUTTONS.get(job["state"], ("Open", "open"))
        self._gate_action = action
        self._gate_btn.setText(text)
        self._gate_btn.setEnabled(action is not None)
        self._gate_btn.setObjectName("primary" if action == "approve" else "")
        self._gate_btn.style().unpolish(self._gate_btn)
        self._gate_btn.style().polish(self._gate_btn)

    def set_selected(self, selected: bool) -> None:
        self.setObjectName("cardSelected" if selected else "card")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.cardClicked.emit(self.job_name)
        super().mousePressEvent(event)


# ------------------------------------------------------------------ toast (R8)

class Toast(QtWidgets.QWidget):
    """A small note in the corner of its parent window. Plain show,
    no animation, gone after four seconds."""

    def __init__(self, parent, text: str):
        super().__init__(parent, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.setStyleSheet(
            f"QWidget {{ background: {CANVAS_SOFT}; color: {BONE}; "
            f"border: 1px solid {HAIRLINE}; border-radius: 6px; }}")
        self.setFixedWidth(320)
        self.adjustSize()
        if parent is not None:
            geo = parent.geometry()
            self.move(geo.right() - self.width() - 16,
                      geo.bottom() - self.height() - 16)
        QtCore.QTimer.singleShot(4000, self.close)

    @staticmethod
    def show_toast(parent, text: str) -> "Toast":
        toast = Toast(parent, text)
        toast.show()
        return toast


# ------------------------------------------------------------ render window

def _base_part_id(expanded_id: str, desc: dict) -> str:
    """The written part behind an expanded id: repeats are `<id>_2`
    and mirrors are `<id>_mirrored`; the first copy keeps the id."""
    raw_ids = {p.get("id") for p in desc.get("parts", [])}
    if expanded_id in raw_ids:
        return expanded_id
    if expanded_id.endswith("_mirrored"):
        base = expanded_id[: -len("_mirrored")]
        if base in raw_ids:
            return base
    import re

    match = re.match(r"^(.*)_(\d+)$", expanded_id)
    if match and match.group(1) in raw_ids:
        return match.group(1)
    return expanded_id


class _BlankableSpinBox(QtWidgets.QDoubleSpinBox):
    """A double spin that can show blank instead of a number
    (v1.16, D10): with no part picked, the part editor's fields
    read as empty, not as a part full of zeros. Only the display
    blanks - the value itself is untouched - and filling the
    editor for a real part unblanks it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._blank = False

    def set_blank(self, blank: bool) -> None:
        self._blank = blank
        edit = self.lineEdit()
        if edit is not None:
            edit.setText("" if blank else
                         super().textFromValue(self.value()))

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt naming
        if self._blank:
            return ""
        return super().textFromValue(value)


class _ClickableLabel(QtWidgets.QLabel):
    """A label that opens a file when clicked."""

    clicked = QtCore.Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.clicked.emit(self._path)
        super().mousePressEvent(event)


# ------------------------------------------------------------ material dial
#
# The Material Dial (v1.11, M3): a circular picker for parametric
# materials, drawn with QPainter (no image assets). Inner ring: the
# nine pages. Outer ring: the active page's swatches or knobs.
# Centre: the active knob with its gauge, or the picked swatch.
#
# The look follows the BNB UI design library's pie-menu entry (016):
# flick, don't hunt - every slice keeps a distinct direction, and
# number keys 1-9 select pages for people who cannot flick precisely.
# Nine pages is one over the entry's eight-slice ceiling (the design
# names ten pages; Life arrives with M9) - the keyboard alternative
# is what keeps it usable, and DESIGN.md records the tension.

_DIAL_PAGES = materials_mod.PAGES
_DIAL_FACE_W = 560
_DIAL_FACE_H = 520
_DIAL_CX = _DIAL_FACE_W // 2
_DIAL_CY = _DIAL_FACE_H // 2
_DIAL_R_CENTER = 62
_DIAL_R_PAGE_IN = 70
_DIAL_R_PAGE_OUT = 150
_DIAL_R_ITEM_IN = 155
_DIAL_R_ITEM_OUT = 250


class DialFace(QtWidgets.QWidget):
    """The painted disc of the Material Dial. Owns the working spec,
    the active page and the active knob; emits specChanged on every
    change and specSettled on knob release or item pick."""

    specChanged = QtCore.Signal(dict)
    specSettled = QtCore.Signal(dict)

    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(_DIAL_FACE_W, _DIAL_FACE_H)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._spec = materials_mod.normalize_material_spec(spec)
        self._page = 1  # Style: the page a user most often wants
        self._active_knob: str | None = None
        self._advanced = False
        self._presets: dict[str, dict] = {}
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_value = 0.0

    # -- state ------------------------------------------------------
    def spec(self) -> dict:
        return materials_mod.normalize_material_spec(self._spec)

    def set_spec(self, spec: dict) -> None:
        self._spec = materials_mod.normalize_material_spec(spec)
        table = materials_mod.PARAMS_BY_FAMILY[self._spec["family"]]
        if (self._active_knob is not None
                and self._active_knob not in table
                and self._active_knob
                not in materials_mod.WEAR_PARAMS):
            self._active_knob = None
        self.update()
        self.specChanged.emit(self.spec())
        self.specSettled.emit(self.spec())

    def set_page(self, index: int) -> None:
        new_page = max(0, min(len(_DIAL_PAGES) - 1, index))
        if new_page != self._page:
            # The center shows the active knob (v1.15, A32): a knob
            # from the old page would linger in the middle of the new
            # one, as if it belonged there. A fresh page starts with
            # no knob selected.
            self._active_knob = None
        self._page = new_page
        self.update()

    @property
    def page(self) -> int:
        return self._page

    def set_advanced(self, advanced: bool) -> None:
        self._advanced = advanced
        if (self._active_knob is not None
                and not self._knob_visible(self._active_knob)):
            self._active_knob = None
        self.update()

    def set_presets(self, presets: dict) -> None:
        self._presets = dict(presets)
        self.update()

    # -- items ------------------------------------------------------
    def _knob_meta(self, name: str) -> tuple:
        """The 7-tuple for a knob, param or wear. Wear knobs share
        the param tables' shape (label, min, max, default, unit,
        simple?, page) but live in spec["wear"]."""
        if name in materials_mod.WEAR_PARAMS:
            return materials_mod.WEAR_PARAMS[name]
        return materials_mod.PARAMS_BY_FAMILY[self._spec["family"]][name]

    def _knob_store(self, name: str) -> dict:
        """The spec dict a knob's value lives in."""
        if name in materials_mod.WEAR_PARAMS:
            return self._spec["wear"]
        return self._spec["params"]

    def _knob_visible(self, name: str) -> bool:
        return self._advanced or self._knob_meta(name)[5]

    def _items(self) -> list[dict]:
        """The outer ring's items for the active page."""
        page = _DIAL_PAGES[self._page]
        family = self._spec["family"]
        fam = materials_mod.FAMILIES[family]
        if page == "Material":
            return [{"key": name, "label": f["label"], "kind": "family"}
                    for name, f in materials_mod.FAMILIES.items()]
        if page == "Style":
            return [{"key": name, "label": s["label"], "kind": "style",
                     "color": s["base"]}
                    for name, s in fam["styles"].items()]
        if page == "Layout":
            return [{"key": name,
                     "label": materials_mod.LAYOUT_LABELS.get(name, name),
                     "kind": "layout"}
                    for name in fam["layouts"]]
        if page == "Saved":
            return [{"key": name, "label": name, "kind": "preset"}
                    for name in sorted(self._presets)[:12]]
        if page == "Life":
            # Wear knobs, gated per family (moss only on wood,
            # stone and ground; rust only on metal).
            return [{"key": name, "label": meta[0], "kind": "knob",
                     "value": self._format_value(name)}
                    for name, meta in materials_mod.WEAR_PARAMS.items()
                    if materials_mod.wear_applies(family, name)
                    and self._knob_visible(name)]
        table = materials_mod.PARAMS_BY_FAMILY[family]
        return [{"key": name, "label": meta[0], "kind": "knob",
                 "value": self._format_value(name)}
                for name, meta in table.items()
                if meta[6] == page and self._knob_visible(name)]

    def _format_value(self, name: str) -> str:
        _label, _low, _high, _default, unit, _simple, _page = \
            self._knob_meta(name)
        value = self._knob_store(name)[name]
        if name == "seed":
            return str(int(value))
        if unit:
            return f"{value:.1f} {unit}"
        return f"{value:.2f}"

    def _active_item_key(self):
        """The key of the item the outer ring highlights, if any."""
        page = _DIAL_PAGES[self._page]
        if page == "Material":
            return self._spec["family"]
        if page == "Style":
            return self._spec["style"]
        if page == "Layout":
            return self._spec["layout"]
        if page in ("Detail", "Form", "Surface", "Life", "Colour",
                    "Depth"):
            return self._active_knob
        return None

    # -- picking ----------------------------------------------------
    def _pick_item(self, item: dict) -> None:
        kind, key = item["kind"], item["key"]
        if kind == "knob":
            self._active_knob = key
            self.update()
            return
        if kind == "family" and key != self._spec["family"]:
            self.set_spec({"family": key})
            return
        if kind == "style" and key != self._spec["style"]:
            spec = self.spec()
            spec["style"] = key
            self.set_spec(spec)
            return
        if kind == "layout" and key != self._spec["layout"]:
            spec = self.spec()
            spec["layout"] = key
            self.set_spec(spec)
            return
        if kind == "preset":
            self.set_spec(self._presets[key])
            return
        self.update()

    def _knob_step(self, name: str) -> float:
        _label, low, high, _default, _unit, _simple, _page = \
            self._knob_meta(name)
        if name == "seed":
            return 1.0
        return (high - low) / 50.0

    def _set_knob(self, name: str, value: float) -> None:
        _label, low, high, _default, _unit, _simple, _page = \
            self._knob_meta(name)
        value = max(low, min(high, value))
        if name == "seed":
            value = int(round(value))
        store = self._knob_store(name)
        if store[name] == value:
            return
        store[name] = value
        self.update()
        self.specChanged.emit(self.spec())

    def _nudge_knob(self, direction: int) -> None:
        if self._active_knob is None:
            return
        step = self._knob_step(self._active_knob)
        current = self._knob_store(self._active_knob)[self._active_knob]
        self._set_knob(self._active_knob, current + direction * step)
        self.specSettled.emit(self.spec())

    # -- geometry ---------------------------------------------------
    @staticmethod
    def _angle_at(x: int, y: int) -> float:
        """Clockwise degrees from the top (0-360)."""
        import math

        angle = math.degrees(math.atan2(-(y - _DIAL_CY), x - _DIAL_CX))
        return (90.0 - angle) % 360.0

    @staticmethod
    def _radius_at(x: int, y: int) -> float:
        import math

        return math.hypot(x - _DIAL_CX, y - _DIAL_CY)

    # -- events -----------------------------------------------------
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        pos = event.position().toPoint()
        radius = self._radius_at(pos.x(), pos.y())
        if radius < _DIAL_R_CENTER and self._active_knob is not None:
            self._dragging = True
            self._drag_start_y = pos.y()
            self._drag_start_value = \
                self._knob_store(self._active_knob)[self._active_knob]
            return
        angle = self._angle_at(pos.x(), pos.y())
        if _DIAL_R_PAGE_IN <= radius < _DIAL_R_PAGE_OUT:
            self.set_page(int(angle // (360.0 / len(_DIAL_PAGES))))
            return
        if _DIAL_R_ITEM_IN <= radius < _DIAL_R_ITEM_OUT:
            items = self._items()
            if items:
                index = int(angle // (360.0 / len(items)))
                self._pick_item(items[min(index, len(items) - 1)])
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._dragging or self._active_knob is None:
            super().mouseMoveEvent(event)
            return
        _label, low, high, _default, _unit, _simple, _page = \
            self._knob_meta(self._active_knob)
        # Dragging up increases the value; 200 px spans the range.
        moved = self._drag_start_y - event.position().toPoint().y()
        value = self._drag_start_value + moved / 200.0 * (high - low)
        self._set_knob(self._active_knob, value)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            self.specSettled.emit(self.spec())
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        pos = event.position().toPoint()
        if (self._active_knob is not None
                and self._radius_at(pos.x(), pos.y()) < _DIAL_R_CENTER):
            steps = event.angleDelta().y() / 120.0
            if steps:
                self._nudge_knob(1 if steps > 0 else -1)
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if QtCore.Qt.Key_1 <= key <= QtCore.Qt.Key_9:
            self.set_page(key - QtCore.Qt.Key_1)
            return
        if key == QtCore.Qt.Key_0:
            # Ten pages: 1-9 pick pages 0-8, 0 picks page 9 (Saved).
            self.set_page(len(_DIAL_PAGES) - 1)
            return
        if key == QtCore.Qt.Key_Up:
            self._nudge_knob(1)
            return
        if key == QtCore.Qt.Key_Down:
            self._nudge_knob(-1)
            return
        super().keyPressEvent(event)

    # -- painting ---------------------------------------------------
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.fillRect(self.rect(), QtGui.QColor(CANVAS))
            self._paint_disc(painter)
            self._paint_pages(painter)
            self._paint_items(painter)
            self._paint_center(painter)
        finally:
            # An exception mid-paint must not leave the painter
            # active: the backing store segfaults on the next paint.
            painter.end()

    def _paint_disc(self, painter: QtGui.QPainter) -> None:
        painter.setPen(QtGui.QPen(QtGui.QColor(HAIRLINE), 1))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(CANVAS_SOFT)))
        painter.drawEllipse(QtCore.QPointF(_DIAL_CX, _DIAL_CY),
                            _DIAL_R_ITEM_OUT, _DIAL_R_ITEM_OUT)

    def _wedge(self, r_in: float, r_out: float, start: float,
               span: float) -> QtGui.QPainterPath:
        """A donut segment: start/span in clockwise degrees from top."""
        outer = QtCore.QRectF(_DIAL_CX - r_out, _DIAL_CY - r_out,
                              2 * r_out, 2 * r_out)
        inner = QtCore.QRectF(_DIAL_CX - r_in, _DIAL_CY - r_in,
                              2 * r_in, 2 * r_in)
        # Qt angles: 0 is east, positive is counterclockwise.
        # arcMoveTo takes only the start angle (no span).
        qt_start = 90.0 - start
        path = QtGui.QPainterPath()
        path.arcMoveTo(outer, qt_start)
        path.arcTo(outer, qt_start, -span)
        path.arcTo(inner, qt_start - span, span)
        path.closeSubpath()
        return path

    def _paint_pages(self, painter: QtGui.QPainter) -> None:
        import math

        span = 360.0 / len(_DIAL_PAGES)
        font = self.font()
        font.setPointSize(9)
        painter.setFont(font)
        amber = QtGui.QColor(AMBER())
        for index, name in enumerate(_DIAL_PAGES):
            start = index * span
            path = self._wedge(_DIAL_R_PAGE_IN, _DIAL_R_PAGE_OUT,
                               start, span)
            if index == self._page:
                fill = QtGui.QColor(amber)
                fill.setAlpha(60)
                painter.setBrush(QtGui.QBrush(fill))
                painter.setPen(QtGui.QPen(amber, 1))
            else:
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(HAIRLINE), 1))
            painter.drawPath(path)
            mid = math.radians(90.0 - (start + span / 2.0))
            radius = (_DIAL_R_PAGE_IN + _DIAL_R_PAGE_OUT) / 2.0
            x = _DIAL_CX + radius * math.cos(mid)
            y = _DIAL_CY - radius * math.sin(mid)
            painter.setPen(QtGui.QColor(
                BONE if index == self._page else MUTED))
            painter.drawText(QtCore.QRectF(x - 45, y - 10, 90, 20),
                             QtCore.Qt.AlignCenter, name)

    def _paint_items(self, painter: QtGui.QPainter) -> None:
        import math

        items = self._items()
        if not items:
            return
        span = 360.0 / len(items)
        active = self._active_item_key()
        font = self.font()
        font.setPointSize(10)
        painter.setFont(font)
        amber = QtGui.QColor(AMBER())
        for index, item in enumerate(items):
            start = index * span
            path = self._wedge(_DIAL_R_ITEM_IN, _DIAL_R_ITEM_OUT,
                               start, span)
            if item["key"] == active:
                fill = QtGui.QColor(amber)
                fill.setAlpha(60)
                painter.setBrush(QtGui.QBrush(fill))
                painter.setPen(QtGui.QPen(amber, 1))
            else:
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(QtGui.QPen(QtGui.QColor(HAIRLINE), 1))
            painter.drawPath(path)
            mid = math.radians(90.0 - (start + span / 2.0))
            mid_r = (_DIAL_R_ITEM_IN + _DIAL_R_ITEM_OUT) / 2.0
            x = _DIAL_CX + mid_r * math.cos(mid)
            y = _DIAL_CY - mid_r * math.sin(mid)
            color = item.get("color")
            if color:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
                painter.drawEllipse(QtCore.QPointF(x, y - 12), 8, 8)
                painter.setPen(QtGui.QColor(BONE))
                painter.drawText(QtCore.QRectF(x - 55, y, 110, 20),
                                 QtCore.Qt.AlignCenter, item["label"])
            elif item["kind"] == "knob":
                painter.setPen(QtGui.QColor(BONE))
                painter.drawText(QtCore.QRectF(x - 55, y - 10, 110, 20),
                                 QtCore.Qt.AlignCenter, item["label"])
                painter.setPen(QtGui.QColor(MUTED))
                painter.drawText(QtCore.QRectF(x - 55, y + 8, 110, 20),
                                 QtCore.Qt.AlignCenter,
                                 item.get("value", ""))
            else:
                painter.setPen(QtGui.QColor(BONE))
                painter.drawText(QtCore.QRectF(x - 55, y - 10, 110, 20),
                                 QtCore.Qt.AlignCenter, item["label"])

    def _paint_center(self, painter: QtGui.QPainter) -> None:
        painter.setPen(QtGui.QPen(QtGui.QColor(HAIRLINE), 1))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(CANVAS)))
        painter.drawEllipse(QtCore.QPointF(_DIAL_CX, _DIAL_CY),
                            _DIAL_R_CENTER, _DIAL_R_CENTER)
        page = _DIAL_PAGES[self._page]
        if self._active_knob is not None:
            self._paint_knob_center(painter)
            return
        if page in ("Surface", "Colour", "Depth") and not self._items():
            self._paint_center_text(
                painter, "Advanced only",
                "Switch to Advanced to see these knobs.")
            return
        if page == "Saved" and not self._items():
            self._paint_center_text(
                painter, "No presets yet",
                "Save one with the Save button.")
            return
        if page == "Style":
            style = materials_mod.FAMILIES[self._spec["family"]][
                "styles"][self._spec["style"]]
            self._paint_swatch_center(painter, style["label"],
                                      style["base"])
            return
        if page == "Layout":
            self._paint_center_text(
                painter, materials_mod.LAYOUT_LABELS[self._spec["layout"]],
                materials_mod.breadcrumb(self._spec))
            return
        self._paint_center_text(painter, "Pick a knob or a style",
                                materials_mod.breadcrumb(self._spec))

    def _paint_knob_center(self, painter: QtGui.QPainter) -> None:
        name = self._active_knob
        label, low, high, _default, unit, _simple, _page = \
            self._knob_meta(name)
        value = self._knob_store(name)[name]
        fraction = 0.0 if high <= low else (value - low) / (high - low)
        rect = QtCore.QRectF(_DIAL_CX - _DIAL_R_CENTER + 6,
                             _DIAL_CY - _DIAL_R_CENTER + 6,
                             2 * (_DIAL_R_CENTER - 6),
                             2 * (_DIAL_R_CENTER - 6))
        painter.setPen(QtGui.QPen(QtGui.QColor(HAIRLINE), 3))
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(QtGui.QPen(QtGui.QColor(AMBER()), 3))
        painter.drawArc(rect, 90 * 16, -int(fraction * 360 * 16))
        font = self.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(MUTED))
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY - 28, 110, 20),
                         QtCore.Qt.AlignCenter, label)
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(BONE))
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY - 8, 110, 24),
                         QtCore.Qt.AlignCenter, self._format_value(name))
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(MUTED))
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY + 18, 110, 20),
                         QtCore.Qt.AlignCenter,
                         "Drag or scroll")

    def _paint_swatch_center(self, painter: QtGui.QPainter, label: str,
                             color: str) -> None:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        painter.drawEllipse(QtCore.QPointF(_DIAL_CX, _DIAL_CY - 14), 12, 12)
        font = self.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(BONE))
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY + 4, 110, 20),
                         QtCore.Qt.AlignCenter, label)

    def _paint_center_text(self, painter: QtGui.QPainter, title: str,
                           sub: str) -> None:
        font = self.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(BONE))
        # The title wraps like the breadcrumb below it: "Pick a knob
        # or a style" is wider than the 110 px centre rect at 11 pt
        # bold, and without wrapping it clipped at both edges on
        # every knob page. The taller rect keeps the single-line
        # vertical centre (CY - 12) unchanged.
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY - 30, 110, 36),
                         QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                         title)
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(MUTED))
        painter.drawText(QtCore.QRectF(_DIAL_CX - 55, _DIAL_CY + 8, 110, 36),
                         QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                         sub)


class MaterialDial(QtWidgets.QWidget):
    """The Material Dial popup (v1.11, M3): the painted face, a
    search box, and Surprise / Save / Close. Draggable by its
    header, minimises to a bar, closes on Close, Escape, or a
    click outside. Close applies the working spec - but only when
    it actually changed (the Render window decides that)."""

    specChanged = QtCore.Signal(dict)
    specSettled = QtCore.Signal(dict)
    closed = QtCore.Signal()

    def __init__(self, workspace, part_id: str, spec: dict, parent=None):
        super().__init__(parent, QtCore.Qt.Popup
                         | QtCore.Qt.FramelessWindowHint)
        self._workspace = Path(workspace)
        self._part_id = part_id
        self._changed = False
        self._drag_offset: QtCore.QPoint | None = None
        self.setFixedWidth(_DIAL_FACE_W)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        # Scoped to the dial itself: a bare "background: ..." rule
        # is a universal selector that reaches every child and
        # overrides the app stylesheet's amber #primary button
        # background while its near-black text still applies, which
        # made the Close button's label invisible.
        self.setStyleSheet(f"MaterialDial {{ background: {CANVAS}; }}")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QtWidgets.QWidget()
        header_row = QtWidgets.QHBoxLayout(self._header)
        header_row.setContentsMargins(8, 6, 8, 6)
        self._crumb = QtWidgets.QLabel(
            materials_mod.breadcrumb(spec))
        self._crumb.setObjectName("heading")
        header_row.addWidget(self._crumb, 1)
        self._minimise_btn = QtWidgets.QPushButton("Minimise")
        self._minimise_btn.clicked.connect(self._toggle_minimised)
        header_row.addWidget(self._minimise_btn)
        close_btn = QtWidgets.QPushButton("X")
        close_btn.clicked.connect(self.close)
        header_row.addWidget(close_btn)
        outer.addWidget(self._header)

        self._body = QtWidgets.QWidget()
        body = QtWidgets.QVBoxLayout(self._body)
        body.setContentsMargins(8, 0, 8, 8)
        body.setSpacing(6)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search styles...")
        self._search.textChanged.connect(self._on_search)
        self._search.returnPressed.connect(self._on_search_enter)
        body.addWidget(self._search)
        self._results = QtWidgets.QListWidget()
        self._results.setMaximumHeight(66)
        self._results.itemClicked.connect(self._on_search_pick)
        self._results.setVisible(False)
        body.addWidget(self._results)

        self._face = DialFace(spec)
        self._face.specChanged.connect(self._on_face_changed)
        self._face.specSettled.connect(self.specSettled.emit)
        self._face.set_presets(materials_mod.load_presets(workspace))
        body.addWidget(self._face)

        self._preset_row = QtWidgets.QWidget()
        preset_layout = QtWidgets.QHBoxLayout(self._preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        self._preset_name = QtWidgets.QLineEdit()
        self._preset_name.setPlaceholderText("Preset name")
        preset_layout.addWidget(self._preset_name, 1)
        save_preset_btn = QtWidgets.QPushButton("Save preset")
        save_preset_btn.clicked.connect(self._save_preset)
        preset_layout.addWidget(save_preset_btn)
        self._preset_row.setVisible(False)
        body.addWidget(self._preset_row)

        buttons = QtWidgets.QHBoxLayout()
        # The button names the mode you are in (v1.14, A15): it used
        # to read "Advanced" in both modes, with nothing showing
        # which was active. The checked state is amber too, from the
        # stylesheet's QPushButton:checked rule.
        self._advanced_btn = QtWidgets.QPushButton()
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.setChecked(
            settings_mod.dial_advanced(workspace))
        self._update_advanced_btn()
        self._advanced_btn.toggled.connect(self._on_advanced_toggled)
        buttons.addWidget(self._advanced_btn)
        surprise_btn = QtWidgets.QPushButton("Surprise")
        surprise_btn.clicked.connect(self._surprise)
        buttons.addWidget(surprise_btn)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.clicked.connect(self._show_preset_row)
        buttons.addWidget(save_btn)
        close2_btn = QtWidgets.QPushButton("Close")
        close2_btn.setObjectName("primary")
        close2_btn.clicked.connect(self.close)
        buttons.addWidget(close2_btn)
        body.addLayout(buttons)
        self._face.set_advanced(self._advanced_btn.isChecked())

        outer.addWidget(self._body)
        self.setWindowTitle(f"Material Dial - {part_id}")

    # -- state ------------------------------------------------------
    def spec(self) -> dict:
        return self._face.spec()

    @property
    def changed(self) -> bool:
        """True once the user has changed the working spec."""
        return self._changed

    @property
    def face(self) -> DialFace:
        return self._face

    def _on_face_changed(self, spec: dict) -> None:
        self._changed = True
        self._crumb.setText(materials_mod.breadcrumb(spec))
        self.specChanged.emit(spec)

    # -- header: drag + minimise ------------------------------------
    def _toggle_minimised(self) -> None:
        minimised = self._body.isVisible()
        self._body.setVisible(not minimised)
        self._minimise_btn.setText("Expand" if minimised else "Minimise")
        self.adjustSize()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child in (self._crumb, self._header):
                self._drag_offset = (event.globalPosition().toPoint()
                                     - self.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.closed.emit()
        super().closeEvent(event)

    # -- search -----------------------------------------------------
    def _on_search(self, text: str) -> None:
        query = text.strip().lower()
        self._results.clear()
        if not query:
            self._results.setVisible(False)
            return
        for family, fam in materials_mod.FAMILIES.items():
            for style, meta in fam["styles"].items():
                label = f"{fam['label']} - {meta['label']}"
                if query in label.lower() or query in style:
                    item = QtWidgets.QListWidgetItem(label)
                    item.setData(QtCore.Qt.UserRole, (family, style))
                    self._results.addItem(item)
        self._results.setVisible(self._results.count() > 0)

    def _on_search_enter(self) -> None:
        if self._results.count():
            self._on_search_pick(self._results.item(0))

    def _on_search_pick(self, item: QtWidgets.QListWidgetItem) -> None:
        family, style = item.data(QtCore.Qt.UserRole)
        spec = self.spec()
        if family != spec["family"]:
            spec = {"family": family, "style": style}
        else:
            spec["style"] = style
        self._face.set_spec(spec)
        self._face.set_page(_DIAL_PAGES.index("Style"))
        self._search.clear()
        self._results.setVisible(False)

    # -- buttons ----------------------------------------------------
    def _update_advanced_btn(self) -> None:
        """The mode button names the mode it is in."""
        self._advanced_btn.setText(
            "Advanced" if self._advanced_btn.isChecked() else "Simple")

    def _on_advanced_toggled(self, checked: bool) -> None:
        settings_mod.set_dial_advanced(self._workspace, checked)
        self._face.set_advanced(checked)
        self._update_advanced_btn()

    def _surprise(self) -> None:
        self._face.set_spec(materials_mod.surprise_spec())

    def _show_preset_row(self) -> None:
        self._preset_row.setVisible(True)
        self._preset_name.setFocus()

    def _save_preset(self) -> None:
        name = self._preset_name.text().strip()
        if not name:
            self._preset_name.setPlaceholderText("Type a name first")
            return
        materials_mod.save_preset(self._workspace, name, self.spec())
        self._face.set_presets(
            materials_mod.load_presets(self._workspace))
        self._preset_name.clear()
        self._preset_row.setVisible(False)
        Toast.show_toast(self, f"Preset {name} saved.")


class RenderWindow(_GeometryWindow):
    """The viewport, full size: the selected job's parts as true-scale
    3D shapes, click a part to edit it, with a legend, a triangle
    count, cameras, and the job's renders."""

    GEOMETRY_KEY = "RenderWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        from .qtbench import LiveRenderWidget

        self._bench = bench
        self._workspace = Path(workspace)
        self._job_name: str | None = None
        self._job_filed = False
        self._desc: dict | None = None
        self._selected_part: str | None = None
        # The Material Dial (v1.11, M3): one open at a time, editing
        # one part of one job. _dial_original is the part's material
        # as it was when the dial opened (Close only applies a change).
        self._dial: MaterialDial | None = None
        self._dial_part: str | None = None
        self._dial_original = None
        self._preview_token = 0
        self._preview_call: _BackgroundCall | None = None
        self.setWindowTitle("SKULLPT - Render")
        self.setMinimumSize(720, 480)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QHBoxLayout(central)
        main.setContentsMargins(8, 8, 8, 8)

        left = QtWidgets.QVBoxLayout()
        self._viewport = LiveRenderWidget()
        self._viewport.partSelected.connect(self._on_part_selected)
        self._viewport.partHovered.connect(self._on_part_hovered)
        self._viewport.partDoubleClicked.connect(
            self._on_part_double_clicked)
        self._viewport.partEyedropper.connect(self._on_part_eyedropper)
        left.addWidget(self._viewport, 1)

        self._legend = QtWidgets.QLabel()
        self._legend.setObjectName("small")
        self._legend.setWordWrap(True)
        left.addWidget(self._legend)
        self._hover_label = QtWidgets.QLabel("Hovering: nothing")
        self._hover_label.setObjectName("small")
        left.addWidget(self._hover_label)
        self._tri_label = QtWidgets.QLabel("Triangles: -")
        self._tri_label.setObjectName("small")
        left.addWidget(self._tri_label)

        camera_row = QtWidgets.QHBoxLayout()
        reset_btn = QtWidgets.QPushButton("Reset view")
        reset_btn.clicked.connect(self._viewport.frame)
        camera_row.addWidget(reset_btn)
        self._camera_buttons = QtWidgets.QHBoxLayout()
        camera_row.addLayout(self._camera_buttons)
        camera_row.addStretch(1)
        save_cam_btn = QtWidgets.QPushButton("Save camera")
        save_cam_btn.clicked.connect(self._save_camera)
        camera_row.addWidget(save_cam_btn)
        left.addLayout(camera_row)
        main.addLayout(left, 3)

        # The right panel scrolls (v1.14, A1): with a job loaded it
        # holds more than fits on his screen, and without a scroll
        # area Qt squeezed the input rows below their font height so
        # their text was half-clipped and unreadable. Every other
        # window already scrolled; Render did not.
        right_holder = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_holder)
        parts_title = QtWidgets.QLabel("Parts")
        parts_title.setObjectName("heading")
        right.addWidget(parts_title)
        self._parts_list = QtWidgets.QListWidget()
        self._parts_list.setMinimumHeight(96)
        self._parts_list.itemClicked.connect(self._on_list_part)
        right.addWidget(self._parts_list, 1)

        editor_title = QtWidgets.QLabel("Edit part")
        editor_title.setObjectName("heading")
        right.addWidget(editor_title)
        self._editor_note = QtWidgets.QLabel("Select a part to edit it.")
        self._editor_note.setObjectName("small")
        self._editor_note.setWordWrap(True)
        right.addWidget(self._editor_note)
        form = QtWidgets.QFormLayout()
        self._pos_spins: list[QtWidgets.QDoubleSpinBox] = []
        self._size_spins: list[QtWidgets.QDoubleSpinBox] = []
        for label, spins in (("Position (cm)", self._pos_spins),
                             ("Size (cm)", self._size_spins)):
            row = QtWidgets.QHBoxLayout()
            for _ in range(3):
                spin = _BlankableSpinBox()
                spin.setRange(-10000.0, 10000.0)
                spin.setDecimals(1)
                spin.setSingleStep(1.0)
                # Narrow by design (v1.14, A1/A11): three spins share
                # one row of a ~330 px panel, and a scroll area never
                # squeezes content below its minimum width - without
                # this cap the panel ran wider than the window and the
                # Dial button slid off the right edge.
                spin.setMinimumWidth(64)
                spins.append(spin)
                row.addWidget(spin)
            form.addRow(label, row)
        material_row = QtWidgets.QHBoxLayout()
        self._material_combo = QtWidgets.QComboBox()
        # Editable (v1.15, A31): a material-spec part's breadcrumb
        # shows in the combo itself (via setEditText, which leaves
        # the index at -1, so saving never writes it as a preset).
        self._material_combo.setEditable(True)
        self._material_combo.addItems(sorted(MATERIALS))
        material_row.addWidget(self._material_combo, 1)
        self._dial_btn = QtWidgets.QPushButton("Dial")
        self._dial_btn.setEnabled(False)
        self._dial_btn.clicked.connect(self._open_dial)
        material_row.addWidget(self._dial_btn)
        form.addRow("Material", material_row)
        self._material_crumb = QtWidgets.QLabel()
        self._material_crumb.setObjectName("small")
        self._material_crumb.setWordWrap(True)
        self._material_crumb.setVisible(False)
        form.addRow("", self._material_crumb)
        # True displacement (v1.13, M14): only for object materials
        # (a string material has no bump chain to displace with).
        # Off by default; the cost lives in the tooltip, because the
        # full text (341 px) used to force the whole panel wider than
        # the window (v1.14, A1/A11).
        self._displacement_check = QtWidgets.QCheckBox("True displacement")
        self._displacement_check.setToolTip(
            "16x triangles, needs more RAM")
        self._displacement_check.setVisible(False)
        form.addRow("", self._displacement_check)
        self._bevel_spin = _BlankableSpinBox()
        self._bevel_spin.setRange(0.0, 100.0)
        self._bevel_spin.setDecimals(1)
        form.addRow("Bevel (cm)", self._bevel_spin)
        self._radius_spin = _BlankableSpinBox()
        self._radius_spin.setRange(0.0, 1000.0)
        self._radius_spin.setDecimals(1)
        form.addRow("Radius (cm)", self._radius_spin)
        right.addLayout(form)
        self._save_part_btn = QtWidgets.QPushButton("Save part changes")
        self._save_part_btn.setObjectName("primary")
        self._save_part_btn.setEnabled(False)
        self._save_part_btn.clicked.connect(self._save_part)
        right.addWidget(self._save_part_btn)
        # The note labels wrap (v1.14, A1/A11): an unwrapped label's
        # minimum width is its whole text, and the bake note (872 px)
        # used to force the panel wider than the window, hiding the
        # Dial button and the buttons' own text off the right edge.
        save_note = QtWidgets.QLabel(
            "Saving sends the job back for approval.")
        save_note.setWordWrap(True)
        right.addWidget(save_note)
        self._preview_label = QtWidgets.QLabel()
        self._preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self._preview_label.setWordWrap(True)
        self._preview_label.setVisible(False)
        right.addWidget(self._preview_label)
        renders_title = QtWidgets.QLabel("Renders")
        renders_title.setObjectName("heading")
        right.addWidget(renders_title)
        # A grid, three across (v1.14, A1/A11): six 96 px thumbnails
        # in one row would force the panel 600 px wide.
        self._renders_row = QtWidgets.QGridLayout()
        right.addLayout(self._renders_row)
        render_title = QtWidgets.QLabel("Render settings")
        render_title.setObjectName("heading")
        right.addWidget(render_title)
        render_form = QtWidgets.QFormLayout()
        self._engine_combo = QtWidgets.QComboBox()
        # Narrow by design (v1.14, A1/A11): a combo's minimum width
        # is its longest item, and "Auto (try EEVEE, fall back to
        # Cycles)" (258 px) used to force the panel wider than the
        # window. The full text still shows in the dropdown.
        self._engine_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._engine_combo.setMinimumContentsLength(12)
        self._engine_combo.addItem("Auto (try EEVEE, fall back to Cycles)", "auto")
        self._engine_combo.addItem("EEVEE (fast, needs a GPU context)", "eevee")
        self._engine_combo.addItem("Cycles (slow, always works)", "cycles")
        render_form.addRow("Engine", self._engine_combo)
        self._res_spin = QtWidgets.QSpinBox()
        self._res_spin.setRange(128, 4096)
        self._res_spin.setSingleStep(64)
        self._res_spin.setSuffix(" px")
        render_form.addRow("Resolution", self._res_spin)
        self._samples_spin = QtWidgets.QSpinBox()
        self._samples_spin.setRange(1, 512)
        render_form.addRow("Cycles samples", self._samples_spin)
        self._denoise_check = QtWidgets.QCheckBox("Denoise (Cycles only)")
        render_form.addRow("", self._denoise_check)
        self._tier_combo = QtWidgets.QComboBox()
        self._tier_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._tier_combo.setMinimumContentsLength(12)
        self._tier_combo.addItem("Draft (fewer nodes, fast)", "draft")
        self._tier_combo.addItem("Balanced (the full recipe)", "balanced")
        self._tier_combo.addItem("Final (extra detail, 1024 bakes)", "final")
        render_form.addRow("Material quality", self._tier_combo)
        right.addLayout(render_form)
        self._save_render_btn = QtWidgets.QPushButton("Save render settings")
        self._save_render_btn.setEnabled(False)
        self._save_render_btn.clicked.connect(self._save_render_settings)
        right.addWidget(self._save_render_btn)
        self._settings_note = QtWidgets.QLabel(
            "These are this job's own settings. Changing them does not "
            "send the job back for approval.")
        self._settings_note.setWordWrap(True)
        right.addWidget(self._settings_note)
        self._bake_btn = QtWidgets.QPushButton("Bake PBR maps")
        self._bake_btn.setEnabled(False)
        self._bake_btn.clicked.connect(self._bake_maps)
        right.addWidget(self._bake_btn)
        bake_note = QtWidgets.QLabel(
            "Bakes each material into colour, roughness, normal and "
            "height maps in the job's output folder. The job must be "
            "built first. Glass does not bake.")
        bake_note.setWordWrap(True)
        right.addWidget(bake_note)
        right.addStretch(1)
        self._right_scroll = QtWidgets.QScrollArea()
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setWidget(right_holder)
        self._right_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        self._right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        main.addWidget(self._right_scroll, 1)

        self._update_legend()
        # Nothing is selected yet, so the editor starts blank and
        # grey (v1.14, A10) and the render controls show the
        # workspace defaults (v1.14, A12).
        self._clear_editor()
        self._load_render_settings()
        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 720:
            self.resize(1024, 640)

    # -- viewport alias (the Jobs window drives the viewport through it)
    @property
    def viewport(self):
        return self._viewport

    @property
    def job_name(self) -> str | None:
        return self._job_name

    def set_job(self, job_name: str | None) -> None:
        """Show a job in the viewport, or clear it with None."""
        # A job change closes the dial (it edits one job's part);
        # _dial is cleared first, so the dial's `closed` signal
        # applies nothing. Stale previews are discarded by token.
        if self._dial is not None:
            dial, self._dial = self._dial, None
            dial.close()
        self._preview_token += 1
        self._preview_label.setVisible(False)
        self._job_name = job_name
        self._job_filed = False
        self._desc = None
        self._selected_part = None
        self._viewport.set_selected_part(None)
        self._parts_list.clear()
        self._clear_editor()
        self._clear_renders()
        while self._camera_buttons.count():
            item = self._camera_buttons.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if job_name is None:
            self._viewport.set_scene(None)
            self._update_legend()
            self._tri_label.setText("Triangles: -")
            self._load_render_settings()
            return
        try:
            job = Job(self._workspace / "jobs" / job_name)
            self._desc = job.load_description()
            # A filed job is finished work (v1.16, D2): the editor
            # and the dial read it but cannot change it, and say so
            # up front (see _fill_editor).
            self._job_filed = job.state == "filed"
        except Exception as exc:
            self._viewport.set_scene(None)
            self._legend.setText(f"Could not read this job: {exc}")
            self._tri_label.setText("Triangles: -")
            self._load_render_settings()
            return
        # The viewport searches the job folder first, then the
        # workspace, for the description's mesh file (v1.10, N52):
        # an imported part draws its real geometry, not its box.
        self._viewport.set_scene(self._desc, [job.dir, self._workspace])
        for part in expand_parts(self._desc):
            self._parts_list.addItem(part["id"])
        self._update_legend()
        self._update_tri_count()
        self._load_renders(job)
        self._load_render_settings()
        for camera in self._desc.get("cameras", []) or []:
            btn = QtWidgets.QPushButton(camera.get("name", "Camera"))
            btn.clicked.connect(
                lambda _checked=False, c=camera: self._goto_camera(c))
            self._camera_buttons.addWidget(btn)

    def refresh(self) -> None:
        """Re-read the current job (after an edit or a build). The
        camera stays where the user left it."""
        if self._job_name is not None:
            selected = self._selected_part
            camera = (self._viewport.target, self._viewport.distance,
                      self._viewport.azimuth, self._viewport.elevation)
            self.set_job(self._job_name)
            (self._viewport.target, self._viewport.distance,
             self._viewport.azimuth, self._viewport.elevation) = camera
            self._viewport.update()
            if selected is not None:
                self._select_part(selected)

    def _set_editor_enabled(self, enabled: bool) -> None:
        """The part editor's inputs, on or off together."""
        for spin in (*self._pos_spins, *self._size_spins,
                     self._bevel_spin, self._radius_spin):
            spin.setEnabled(enabled)
        self._material_combo.setEnabled(enabled)
        self._displacement_check.setEnabled(enabled)
        self._dial_btn.setEnabled(enabled)
        self._save_part_btn.setEnabled(enabled)

    def _clear_editor(self) -> None:
        """Nothing selected, nothing to edit (v1.14, A10): the fields
        go blank and grey instead of showing made-up defaults that
        look like a part exists. Blank means the display too
        (v1.16, D10): the spins show nothing, not 0.0."""
        for spin in (*self._pos_spins, *self._size_spins,
                     self._bevel_spin, self._radius_spin):
            spin.setValue(0.0)
            spin.set_blank(True)
        self._material_combo.setCurrentIndex(-1)
        self._material_combo.setEditText("")
        self._material_crumb.setVisible(False)
        self._displacement_check.setVisible(False)
        self._displacement_check.setChecked(False)
        self._preview_label.setVisible(False)
        self._editor_note.setText("Select a part to edit it.")
        self._set_editor_enabled(False)

    def _update_legend(self) -> None:
        step = self._viewport.grid_step()
        self._legend.setText(
            f"Grid: {step:.1f} cm squares. X red, Y green, Z blue. "
            "Drag to orbit, right-drag to pan, scroll to zoom. "
            "Click a part to select it. Double-click it for the "
            "Material Dial. Ctrl+click a part to copy its material "
            "onto the selected part.")

    def _update_tri_count(self) -> None:
        if self._job_name is None:
            self._tri_label.setText("Triangles: -")
            return
        report = bench_mod.build_report(self._workspace, self._job_name)
        if report is not None and report.get("total_tris") is not None:
            self._tri_label.setText(
                f"Triangles: {report['total_tris']:,} (measured by the last build)")
            return
        scene = self._viewport._scene
        if scene:
            total = sum(len(m["tris"]) for m in scene["meshes"])
            self._tri_label.setText(
                f"Triangles: about {total:,} (estimate from the viewport)")
        else:
            self._tri_label.setText("Triangles: -")

    def _clear_renders(self) -> None:
        while self._renders_row.count():
            item = self._renders_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _load_renders(self, job: Job) -> None:
        if not job.output_dir.exists():
            return
        pngs = sorted(job.output_dir.glob("*.png"))[:6]
        shown = 0
        for path in pngs:
            pixmap = QtGui.QPixmap(str(path))
            if pixmap.isNull():
                continue
            label = _ClickableLabel(str(path))
            label.setPixmap(pixmap.scaled(
                96, 96, QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))
            label.setToolTip(f"{path.name} - click to open")
            label.clicked.connect(
                lambda p: QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(p)))
            self._renders_row.addWidget(label, shown // 3, shown % 3)
            shown += 1

    def _goto_camera(self, camera: dict) -> None:
        position = camera.get("position_cm")
        look_at = camera.get("look_at_cm")
        if position and look_at:
            self._viewport.set_camera_position(position, look_at)

    def _save_camera(self) -> None:
        if self._job_name is None or self._desc is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save camera", "Camera name:")
        if not ok or not name.strip():
            return
        from .viewer import upsert_camera

        job = Job(self._workspace / "jobs" / self._job_name)
        desc = job.load_description()
        upsert_camera(desc, self._viewport.current_camera(name.strip()))
        from . import description as desc_mod

        desc_mod.dump(desc, job.desc_path)
        # Refresh the view without re-framing (v1.10, N37): set_job
        # re-frames the camera, which would undo the saved view.
        self.refresh()
        Toast.show_toast(self, f"Camera {name.strip()} saved.")

    def _on_part_hovered(self, part_id: str) -> None:
        if part_id:
            self._hover_label.setText(f"Hovering: {part_id}")
        else:
            self._hover_label.setText("Hovering: nothing")

    def _on_part_selected(self, part_id: str) -> None:
        self._select_part(part_id)

    def _on_list_part(self, item) -> None:
        self._select_part(item.text())
        self._viewport.set_selected_part(item.text())

    def _select_part(self, part_id: str) -> None:
        self._selected_part = part_id
        self._viewport.set_selected_part(part_id)
        for row in range(self._parts_list.count()):
            item = self._parts_list.item(row)
            if item.text() == part_id:
                self._parts_list.setCurrentRow(row)
                break
        self._fill_editor(part_id)

    def _fill_editor(self, part_id: str) -> None:
        if self._desc is None:
            return
        if part_id == "character":
            # The viewport mannequin (v1.7, T26): a stand-in, not a
            # part: the human is edited in the Character Studio
            # window, not here.
            self._clear_editor()
            self._editor_note.setText(
                "This is the human. Edit it in the Character Studio window.")
            return
        base_id = _base_part_id(part_id, self._desc)
        part = next((p for p in self._desc.get("parts", [])
                     if p.get("id") == base_id), None)
        if part is None:
            self._clear_editor()
            self._editor_note.setText("Could not find this part to edit.")
            return
        self._set_editor_enabled(True)
        for spin in (*self._pos_spins, *self._size_spins,
                     self._bevel_spin, self._radius_spin):
            spin.set_blank(False)
        if base_id != part_id:
            self._editor_note.setText(
                f"This is a copy of {base_id}. Editing changes the "
                "original part and all its copies.")
        else:
            self._editor_note.setText(f"Editing {base_id}.")
        pos = part.get("position_cm") or [0.0, 0.0, 0.0]
        size = part.get("size_cm") or [0.0, 0.0, 0.0]
        for spin, value in zip(self._pos_spins, pos):
            spin.setValue(float(value))
        for spin, value in zip(self._size_spins, size):
            spin.setValue(float(value))
        material = part.get("material")
        if isinstance(material, dict):
            # An object material (v1.11, M1): the combo shows the
            # breadcrumb as its edit text (v1.15, A31) - the box
            # used to sit blank, as if the part had no material.
            # The index stays -1, so saving never writes the
            # breadcrumb as a preset. The crumb below still shows.
            self._material_combo.setCurrentIndex(-1)
            self._material_combo.setEditText(
                materials_mod.breadcrumb(material))
            self._material_crumb.setText(
                materials_mod.breadcrumb(material))
            self._material_crumb.setVisible(True)
            self._dial_btn.setEnabled(True)
            self._displacement_check.setChecked(
                bool(material.get("displacement", False)))
            self._displacement_check.setVisible(True)
            self._start_preview(material)
        elif isinstance(material, str):
            index = self._material_combo.findText(material)
            if index >= 0:
                self._material_combo.setCurrentIndex(index)
            else:
                # A hex or unknown material is not a named preset: leave
                # the combo blank rather than forcing the first preset
                # (v1.9, N4) - saving keeps the material as it is.
                self._material_combo.setCurrentIndex(-1)
                self._material_combo.setEditText("")
                self._editor_note.setText(
                    self._editor_note.text()
                    + f" Material {material} is custom and stays as it is"
                    " unless you pick a preset.")
            self._material_crumb.setVisible(False)
            self._dial_btn.setEnabled(True)
            self._displacement_check.setVisible(False)
            self._preview_label.setVisible(False)
        else:
            # No material at all (v1.11, M5): the empty state.
            self._material_combo.setCurrentIndex(-1)
            self._material_combo.setEditText("")
            self._material_crumb.setText(
                "No material here - open the dial to start.")
            self._material_crumb.setVisible(True)
            self._dial_btn.setEnabled(True)
            self._displacement_check.setVisible(False)
            self._preview_label.setVisible(False)
        self._bevel_spin.setValue(float(part.get("bevel_cm") or 0.0))
        self._radius_spin.setValue(float(part.get("radius_cm") or 0.0))
        self._save_part_btn.setEnabled(True)
        if self._job_filed:
            # Filed jobs are read-only here, from the start (v1.16,
            # D2): the part's values stay on show, but the editor
            # and the dial cannot change them, and the reason sits
            # where the user is looking - the same pattern the Bake
            # button already uses. Before this, the editor happily
            # took changes and only refused when the dial closed,
            # throwing the work away.
            self._set_editor_enabled(False)
            self._editor_note.setText(
                "Filed jobs live in the Library. "
                "Duplicate it to change it.")

    def _save_part(self) -> None:
        if self._job_name is None or self._selected_part is None \
                or self._desc is None:
            return
        base_id = _base_part_id(self._selected_part, self._desc)
        fields = {
            "position_cm": [s.value() for s in self._pos_spins],
            "size_cm": [s.value() for s in self._size_spins],
        }
        # Material: only when a preset is selected. A blank combo
        # means the part's material is custom (hex/unknown) - it is
        # left exactly as it is (v1.9, N4). The combo is editable
        # (v1.15, A31), and on an editable combo typing a preset
        # name shows the text but leaves the index at -1 - so a
        # typed name that matches a preset counts as picking it,
        # or a typed preset would silently not save. The breadcrumb
        # a material-spec part shows never matches a preset name.
        if self._material_combo.currentIndex() >= 0:
            fields["material"] = self._material_combo.currentText()
        else:
            typed = self._material_combo.currentText().strip()
            if typed and self._material_combo.findText(typed) >= 0:
                fields["material"] = typed
        # Bevel/radius: only where they apply. A part that never had
        # one keeps not having one; a part that had one can still be
        # set back to zero (v1.9, N4).
        part = next((p for p in self._desc.get("parts", [])
                     if p.get("id") == base_id), None)
        if self._bevel_spin.value() or (part is not None and "bevel_cm" in part):
            fields["bevel_cm"] = self._bevel_spin.value()
        if self._radius_spin.value() or (part is not None and "radius_cm" in part):
            fields["radius_cm"] = self._radius_spin.value()
        # Displacement (v1.13, M14): only for object materials, and
        # only when the checkbox changed - the spec travels whole,
        # so nothing else about the material is touched.
        if part is not None and isinstance(part.get("material"), dict) \
                and self._displacement_check.isVisible():
            spec = part["material"]
            checked = self._displacement_check.isChecked()
            if bool(spec.get("displacement", False)) != checked:
                new_spec = dict(spec)
                new_spec["displacement"] = checked
                fields["material"] = new_spec
        result = self._bench.do_action("edit-part", {
            "job": self._job_name, "part": base_id, "fields": fields})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not save",
                "The edit did not save. The chat panel on the Jobs "
                "window says why.")
            return
        Toast.show_toast(self, f"Part {base_id} saved. The job needs approval again.")
        self.refresh()

    # -- the Material Dial (v1.11, M3/M5/M6) -------------------------
    def _open_dial(self) -> None:
        """Open the Material Dial for the selected part."""
        if self._job_name is None or self._selected_part is None \
                or self._desc is None:
            return
        if self._job_filed:
            # No dial for a filed job (v1.16, D2): it would take
            # changes it can never save. The editor note already
            # says why; the toast repeats it where the user acted.
            Toast.show_toast(
                self, "Filed jobs live in the Library. "
                      "Duplicate it to change it.")
            return
        if self._dial is not None:
            self._dial.raise_()
            self._dial.activateWindow()
            return
        base_id = _base_part_id(self._selected_part, self._desc)
        part = next((p for p in self._desc.get("parts", [])
                     if p.get("id") == base_id), None)
        if part is None:
            return
        material = part.get("material")
        if isinstance(material, dict):
            spec = materials_mod.normalize_material_spec(material)
        else:
            # A string material or none: start from the default wood
            # spec. Close only applies when the user changed it.
            spec = materials_mod.normalize_material_spec(
                {"family": "wood"})
        self._dial_part = base_id
        self._dial_original = material
        dial = MaterialDial(self._workspace, base_id, spec, parent=self)
        dial.specChanged.connect(self._on_dial_changed)
        dial.specSettled.connect(self._on_dial_settled)
        dial.closed.connect(self._on_dial_closed)
        self._dial = dial
        dial.adjustSize()
        geo = self.geometry()
        dial.move(geo.center().x() - dial.width() // 2,
                  geo.center().y() - dial.height() // 2)
        dial.show()

    def _on_dial_changed(self, spec: dict) -> None:
        """Live feedback (v1.11, M3): recolour the part at once, on
        every copy of it (repeats and mirrors share the material)."""
        if self._dial_part is None or self._desc is None:
            return
        hex_color = materials_mod.spec_base_color(spec)
        rgb = (int(hex_color[1:3], 16) / 255.0,
               int(hex_color[3:5], 16) / 255.0,
               int(hex_color[5:7], 16) / 255.0)
        for row in range(self._parts_list.count()):
            expanded = self._parts_list.item(row).text()
            if _base_part_id(expanded, self._desc) == self._dial_part:
                self._viewport.set_color_override(expanded, rgb)

    def _on_dial_settled(self, spec: dict) -> None:
        """Knob release (v1.11, M3): a preview render, cached by
        spec hash, never blocking the UI thread."""
        self._start_preview(spec)

    def _on_dial_closed(self) -> None:
        """Close applies the working spec - but only when the user
        actually changed it (v1.11, M3)."""
        dial = self._dial
        self._dial = None
        if dial is None or self._job_name is None or self._desc is None:
            return
        # Clear the live colour override (all copies of the part).
        for row in range(self._parts_list.count()):
            expanded = self._parts_list.item(row).text()
            if _base_part_id(expanded, self._desc) == self._dial_part:
                self._viewport.set_color_override(expanded, None)
        if not dial.changed:
            return
        spec = dial.spec()
        original = self._dial_original
        if isinstance(original, dict):
            # Changed and changed back: the spec is the same, so
            # there is nothing to apply (and no approval round trip).
            if materials_mod.normalize_material_spec(original) == spec:
                return
        result = self._bench.do_action("edit-part", {
            "job": self._job_name, "part": self._dial_part,
            "fields": {"material": spec}})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not save",
                "The material did not save. The chat panel on the Jobs "
                "window says why.")
            return
        Toast.show_toast(
            self, f"Material set to {materials_mod.breadcrumb(spec)}.")
        self.refresh()

    def _on_part_double_clicked(self, part_id: str) -> None:
        """Double-click opens the Material Dial (v1.11, M3)."""
        self._select_part(part_id)
        self._open_dial()

    def _on_part_eyedropper(self, source_id: str) -> None:
        """Ctrl+click: copy the source part's material onto the
        selected part (v1.11, M6). With nothing selected, select
        the source part instead."""
        if self._job_name is None or self._desc is None:
            return
        if self._job_filed:
            # Copying a material onto a filed job is an edit too
            # (v1.16, D2): refuse up front, with the reason.
            Toast.show_toast(
                self, "Filed jobs live in the Library. "
                      "Duplicate it to change it.")
            return
        source = _base_part_id(source_id, self._desc)
        if self._selected_part is None or _base_part_id(
                self._selected_part, self._desc) == source:
            self._select_part(source_id)
            return
        target = _base_part_id(self._selected_part, self._desc)
        result = self._bench.do_action("copy-material", {
            "job": self._job_name, "from_part": source,
            "to_part": target})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not copy",
                "The material did not copy. The chat panel on the Jobs "
                "window says why.")
            return
        Toast.show_toast(self, result.get("detail", "Material copied."))
        self.refresh()

    def _start_preview(self, spec: dict) -> None:
        """Render a material preview off the UI thread (v1.11, M3).
        A newer preview supersedes an older one by token."""
        from . import blender_export as blender_mod

        self._preview_token += 1
        token = self._preview_token
        self._preview_label.setText("Rendering a preview...")
        self._preview_label.setVisible(True)
        call = _BackgroundCall(
            lambda: blender_mod.render_material_preview(
                spec, self._workspace))
        call.finished.connect(
            lambda result: self._on_preview_ready(token, result))
        self._preview_call = call
        call.start()

    def _on_preview_ready(self, token: int, result) -> None:
        if token != self._preview_token:
            return  # a newer preview superseded this one
        if isinstance(result, Exception):
            self._preview_label.setText(str(result))
            return
        pixmap = QtGui.QPixmap(str(result))
        if pixmap.isNull():
            self._preview_label.setText("The preview did not render.")
            return
        self._preview_label.setPixmap(pixmap)

    def _set_render_controls_enabled(self, enabled: bool) -> None:
        """The render settings inputs, on or off together."""
        for widget in (self._engine_combo, self._res_spin,
                       self._samples_spin, self._denoise_check,
                       self._tier_combo, self._save_render_btn):
            widget.setEnabled(enabled)

    def _load_render_settings(self) -> None:
        """Fill the render controls with this job's effective settings
        (its own block, else the workspace defaults). With no job
        loaded the controls show the workspace defaults, not the
        first item in each list (v1.14, A12) - and the controls are
        disabled, with a note saying these are the workspace
        defaults and where they change (v1.16, D6): enabled controls
        whose changes silently went nowhere misled exactly the way
        the old "this job's own settings" note did."""
        resolved = settings_mod.resolve_render_settings(
            self._workspace, self._desc or {})
        index = self._engine_combo.findData(resolved["engine"])
        self._engine_combo.setCurrentIndex(max(0, index))
        self._res_spin.setValue(resolved["resolution_px"])
        self._samples_spin.setValue(resolved["samples"])
        self._denoise_check.setChecked(resolved["denoise"])
        tier_index = self._tier_combo.findData(resolved["tier"])
        self._tier_combo.setCurrentIndex(max(0, tier_index))
        has_job = self._job_name is not None
        self._set_render_controls_enabled(has_job)
        if has_job:
            self._settings_note.setText(
                "These are this job's own settings. Changing them "
                "does not send the job back for approval.")
        else:
            self._settings_note.setText(
                "These are the workspace defaults. To change them, "
                "open the Settings window.")
        self._update_bake_button()

    def _update_bake_button(self) -> None:
        """Bake is offered only when it can run (v1.14, A8): the job
        must be built (its .blend exists) and hold at least one
        material with a recipe. Otherwise the button stays greyed
        out, and hovering over it says why."""
        from . import bake as bake_mod

        if self._job_name is None or self._desc is None:
            self._bake_btn.setEnabled(False)
            self._bake_btn.setToolTip(
                "Open a built 3D job to bake its maps.")
            return
        bakeable, _skipped = bake_mod.bakeable_parts(self._desc)
        if not bakeable:
            self._bake_btn.setEnabled(False)
            self._bake_btn.setToolTip(
                "Nothing in this job can be baked - no part has a "
                "material with a recipe.")
            return
        job = Job(self._workspace / "jobs" / self._job_name)
        blend = job.output_dir / f"{self._job_name}.blend"
        if not blend.exists():
            self._bake_btn.setEnabled(False)
            if self._desc.get("kind") in ("ui_component", "website",
                                          "graphic"):
                self._bake_btn.setToolTip(
                    "This job has no 3D model to bake.")
            else:
                self._bake_btn.setToolTip(
                    "Build the job first - baking needs the built file.")
            return
        self._bake_btn.setEnabled(True)
        self._bake_btn.setToolTip("")

    def _save_render_settings(self) -> None:
        if self._job_name is None:
            return
        result = self._bench.do_action("render-settings", {
            "job": self._job_name,
            "engine": self._engine_combo.currentData(),
            "resolution_px": self._res_spin.value(),
            "samples": self._samples_spin.value(),
            "denoise": self._denoise_check.isChecked(),
            "tier": self._tier_combo.currentData()})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not save",
                "The render settings did not save. The chat panel on "
                "the Jobs window says why.")
            return
        Toast.show_toast(self, "Render settings saved for this job.")

    def _bake_maps(self) -> None:
        """Bake this job's PBR maps (v1.13, M12): a background run,
        like a build - progress shows in the Jobs window, and the
        run can be cancelled there. The job must be built first;
        the bake refuses plainly when it is not."""
        if self._job_name is None:
            return
        result = self._bench.do_action("bake", {"job": self._job_name})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not bake",
                "The bake did not start. The chat panel on the Jobs "
                "window says why.")
            return
        Toast.show_toast(self, "Baking PBR maps for this job.")


# --------------------------------------------------------- character window

_SLIDER_LABELS = {
    "gender": "Gender",
    "age": "Age",
    "muscle": "Muscle",
    "weight": "Weight",
    "proportions": "Proportions",
    "height": "Height",
    "cupsize": "Cup size",
    "firmness": "Firmness",
    "race_asian": "Asian",
    "race_caucasian": "Caucasian",
    "race_african": "African",
}

_RIG_LABELS = {
    "game_engine": "GameEngine (games and Unreal)",
    "game_engine_with_breast": "GameEngine with breast bones",
    "default": "Default (MPFB standard)",
    "default_no_toes": "Default, no toes",
    "cmu_mb": "CMU motion capture",
    "mixamo": "Mixamo",
    "mixamo_unity": "Mixamo (Unity)",
    "openpose": "OpenPose",
    "rigify.human": "Rigify",
    "rigify.human_toes": "Rigify with toes",
}


def _skin_label(filename: str) -> str:
    """A skin .mhmat file name as a readable label."""
    base = filename[:-6] if filename.endswith(".mhmat") else filename
    return base.replace("_", " ").title()


class CharacterWindow(_GeometryWindow):
    """Character Studio (v1.7, T26): shape a human with 11 macro
    sliders, a skin with its thumbnail, and a rig, then save the job
    and build it. The human is built by MPFB2 inside Blender; the
    viewport in the Render window shows a mannequin at the right
    height and build until the real renders land."""

    GEOMETRY_KEY = "CharacterWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        from .description import CHARACTER_RIGS, CHARACTER_SLIDERS

        self._bench = bench
        self._workspace = Path(workspace)
        self._job_name: str | None = None
        self._desc: dict | None = None
        self._job_state: str | None = None
        self._mpfb_dir: str | None = None
        self._studio_ready = False
        self._skins_available = False
        self._gate_action_name: str | None = None
        self.setWindowTitle("SKULLPT - Character Studio")
        self.setMinimumSize(560, 520)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(8)

        self._status_note = QtWidgets.QLabel()
        self._status_note.setObjectName("small")
        self._status_note.setWordWrap(True)
        main.addWidget(self._status_note)

        job_row = QtWidgets.QHBoxLayout()
        job_row.addWidget(QtWidgets.QLabel("Character job"))
        self._job_combo = QtWidgets.QComboBox()
        self._job_combo.currentIndexChanged.connect(self._on_job_changed)
        job_row.addWidget(self._job_combo, 1)
        self._name_edit = QtWidgets.QLineEdit()
        self._name_edit.setPlaceholderText("Name for a new character")
        job_row.addWidget(self._name_edit, 1)
        new_btn = QtWidgets.QPushButton("New character")
        new_btn.clicked.connect(self._new_character)
        job_row.addWidget(new_btn)
        main.addLayout(job_row)

        # The form scrolls: 11 sliders plus skin and rig is tall.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        form_holder = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(form_holder)
        self._sliders: dict[str, QtWidgets.QSlider] = {}
        self._slider_values: dict[str, QtWidgets.QLabel] = {}
        for key in CHARACTER_SLIDERS:
            row = QtWidgets.QHBoxLayout()
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(50)
            slider.valueChanged.connect(
                lambda value, k=key: self._on_slider(k, value))
            value_label = QtWidgets.QLabel("50%")
            value_label.setMinimumWidth(40)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            self._sliders[key] = slider
            self._slider_values[key] = value_label
            form.addRow(_SLIDER_LABELS[key], row)
        ancestry_note = QtWidgets.QLabel(
            "Ancestry sliders mix together; they need not add up to 100%.")
        ancestry_note.setObjectName("small")
        ancestry_note.setWordWrap(True)
        form.addRow("", ancestry_note)
        skin_row = QtWidgets.QHBoxLayout()
        self._skin_combo = QtWidgets.QComboBox()
        self._skin_combo.currentIndexChanged.connect(self._on_skin_changed)
        skin_row.addWidget(self._skin_combo, 1)
        self._skin_thumb = QtWidgets.QLabel()
        self._skin_thumb.setFixedSize(96, 96)
        self._skin_thumb.setAlignment(QtCore.Qt.AlignCenter)
        skin_row.addWidget(self._skin_thumb)
        form.addRow("Skin", skin_row)
        self._rig_combo = QtWidgets.QComboBox()
        for rig in CHARACTER_RIGS:
            self._rig_combo.addItem(_RIG_LABELS.get(rig, rig), rig)
        form.addRow("Rig", self._rig_combo)
        scroll.setWidget(form_holder)
        main.addWidget(scroll, 1)
        self._form_holder = form_holder

        buttons = QtWidgets.QHBoxLayout()
        self._save_btn = QtWidgets.QPushButton("Save character")
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save_character)
        buttons.addWidget(self._save_btn)
        self._gate_btn = QtWidgets.QPushButton("Build")
        self._gate_btn.clicked.connect(self._gate_action)
        buttons.addWidget(self._gate_btn)
        buttons.addStretch(1)
        main.addLayout(buttons)
        self._gate_note = QtWidgets.QLabel(
            "Saving sends the job back for approval.")
        self._gate_note.setObjectName("small")
        self._gate_note.setWordWrap(True)
        main.addWidget(self._gate_note)

        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 560:
            self.resize(640, 640)
        self.refresh()

    # -- state ------------------------------------------------------
    def set_job(self, job_name: str | None) -> None:
        """Open on a job when it is a character job; anything else
        leaves the picker on whatever it had."""
        self.refresh()
        if job_name is None:
            return
        index = self._job_combo.findData(job_name)
        if index >= 0:
            self._job_combo.setCurrentIndex(index)

    def refresh(self) -> None:
        """Re-check the studio install, rebuild the pickers, and
        re-fill the form from the current job."""
        from .blender_character import find_mpfb

        status = find_mpfb()
        self._studio_ready = bool(status["installed"] and status["assets"])
        self._mpfb_dir = status["mpfb_dir"]
        if status["installed"] and not status["assets"]:
            self._status_note.setText(
                "Character Studio is missing its skins: MPFB2 is "
                "installed, but the MakeHuman system assets pack was "
                "not found with it. Use the Setup check to install "
                "it, or re-run the SKULLPT installer. You can still "
                "shape and save the character; only the skin picker "
                "is disabled until the skins arrive.")
        elif not status["installed"]:
            self._status_note.setText(
                "Character Studio is not installed. Use the Setup "
                "check to install MPFB2 and its skins (about 311 MB "
                "to download), or re-run the SKULLPT installer. "
                "You can still create a character job and shape it; "
                "it builds once the studio is installed.")
        else:
            self._status_note.setText(
                "Shape the human, save, then approve and build the job. "
                "The Render window shows a mannequin at the right "
                "height and build until the real renders land.")
        self._skins_available = bool(status["skins"])
        self._skin_combo.blockSignals(True)
        self._skin_combo.clear()
        for skin in status["skins"]:
            self._skin_combo.addItem(_skin_label(skin), skin)
        self._skin_combo.blockSignals(False)
        self._rebuild_jobs()
        self._load_job()
        self._update_gate()

    def _rebuild_jobs(self) -> None:
        try:
            state = bench_mod.workspace_state(self._workspace)
        except Exception:
            state = {"jobs": []}
        current = self._job_name
        self._job_combo.blockSignals(True)
        self._job_combo.clear()
        self._job_states = {}
        for job in state["jobs"]:
            if job.get("kind") != "character":
                continue
            self._job_combo.addItem(job.get("title") or job["name"], job["name"])
            self._job_states[job["name"]] = job.get("state")
        self._job_combo.blockSignals(False)
        if current is not None:
            index = self._job_combo.findData(current)
            if index >= 0:
                self._job_combo.setCurrentIndex(index)
                return
        # No auto-select (U6): with no job chosen, the picker stays
        # empty until the user picks one.
        self._job_combo.setCurrentIndex(-1)
        self._job_name = None

    def _load_job(self) -> None:
        self._desc = None
        self._job_state = None
        if self._job_name is None:
            self._form_holder.setEnabled(False)
            self._save_btn.setEnabled(False)
            self._skin_thumb.setText("")
            return
        self._job_state = self._job_states.get(self._job_name)
        try:
            job = Job(self._workspace / "jobs" / self._job_name)
            self._desc = job.load_description()
        except Exception:
            self._desc = None
        character = (self._desc or {}).get("character") or {}
        for key, slider in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(round(float(character.get(key, 0.5)) * 100)))
            slider.blockSignals(False)
            self._slider_values[key].setText(f"{slider.value()}%")
        skin = character.get("skin")
        if skin:
            index = self._skin_combo.findData(skin)
            if index < 0:
                # The job's skin is not in the installed pack: show it
                # anyway, so the picker never lies about the job.
                self._skin_combo.addItem(_skin_label(skin) + " (not installed)", skin)
                index = self._skin_combo.findData(skin)
            self._skin_combo.setCurrentIndex(index)
        rig = character.get("rig", "game_engine")
        index = self._rig_combo.findData(rig)
        self._rig_combo.setCurrentIndex(max(0, index))
        # The form and save are usable as soon as a job is loaded
        # (v1.10, N36): shaping a character does not need the studio
        # installed - only building does. Only the skin picker needs
        # the installed skins.
        self._form_holder.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._skin_combo.setEnabled(self._skins_available)
        self._on_skin_changed()

    def _update_gate(self) -> None:
        text, action = _GATE_BUTTONS.get(self._job_state or "", ("Build", None))
        self._gate_btn.setText(text)
        self._gate_action_name = action
        # Only the Build step needs the studio installed (v1.10, N36):
        # approve, file, retry, and open all work before it.
        needs_studio = action == "build"
        self._gate_btn.setEnabled(
            bool(self._job_name and action)
            and (self._studio_ready or not needs_studio))

    # -- events -----------------------------------------------------
    def _on_job_changed(self, _index: int) -> None:
        self._job_name = self._job_combo.currentData()
        self._load_job()
        self._update_gate()

    def _on_slider(self, key: str, value: int) -> None:
        self._slider_values[key].setText(f"{value}%")

    def _on_skin_changed(self, _index: int = -1) -> None:
        skin = self._skin_combo.currentData()
        self._skin_thumb.setText("")
        if not skin or not self._mpfb_dir:
            return
        skins_dir = Path(self._mpfb_dir) / "data" / "skins"
        matches = list(skins_dir.rglob(skin)) if skins_dir.is_dir() else []
        if not matches:
            return
        thumb = matches[0].with_suffix(".thumb")
        if not thumb.is_file():
            return
        pixmap = QtGui.QPixmap(str(thumb))
        if pixmap.isNull():
            return
        self._skin_thumb.setPixmap(pixmap.scaled(
            96, 96, QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))

    def _new_character(self) -> None:
        name = self._name_edit.text().strip() or "New character"
        result = self._bench.do_action("new-character", {"name": name})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not create",
                "The character job was not created. The chat panel on "
                "the Jobs window says why.")
            return
        self._name_edit.clear()
        Toast.show_toast(self, f"Character job {result['job']} created.")
        self.refresh()
        index = self._job_combo.findData(result["job"])
        if index >= 0:
            self._job_combo.setCurrentIndex(index)

    def _save_character(self) -> None:
        if self._job_name is None:
            return
        character = {key: slider.value() / 100.0
                     for key, slider in self._sliders.items()}
        skin = self._skin_combo.currentData()
        if not skin:
            # No skins installed: keep the job's existing skin (or
            # the default) instead of saving nothing (v1.10, N36).
            from .blender_character import DEFAULT_SKIN

            skin = (((self._desc or {}).get("character") or {}).get("skin")
                    or DEFAULT_SKIN)
        character["skin"] = skin
        character["rig"] = self._rig_combo.currentData()
        result = self._bench.do_action("edit-character", {
            "job": self._job_name, "character": character})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not save",
                "The character did not save. The chat panel on the "
                "Jobs window says why.")
            return
        Toast.show_toast(
            self, "Character saved. The job needs approval again.")
        self.refresh()

    def _gate_action(self) -> None:
        if self._job_name is None or not self._gate_action_name:
            return
        self._bench.do_action(self._gate_action_name,
                              {"job": self._job_name})
        self.refresh()


# ------------------------------------------------------------ images window

class _CandidateCard(QtWidgets.QFrame):
    """One generated picture: the image, its state, Keep / Reject, and
    Vary (variations of a kept picture, v1.6 T27)."""

    decided = QtCore.Signal(str, str)  # filename, decision
    vary = QtCore.Signal(str)  # filename

    def __init__(self, batch_dir: Path, candidate: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        # One height for every card (v1.15, A33): a failed card's
        # reason used to stretch its card (and its whole row) far
        # taller than the others. Content aligns to the top.
        self.setFixedHeight(400)
        self._file = candidate["file"]
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        pixmap = QtGui.QPixmap(str(batch_dir / candidate["file"]))
        image = QtWidgets.QLabel()
        if not pixmap.isNull():
            image.setPixmap(pixmap.scaled(
                180, 180, QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))
        else:
            image.setText("(no preview)")
        image.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(image)
        name = QtWidgets.QLabel(candidate["file"])
        name.setObjectName("small")
        name.setWordWrap(True)
        layout.addWidget(name)
        status = candidate.get("status", "pending")
        status_label = QtWidgets.QLabel(f"State: {status}")
        status_label.setObjectName("small")
        layout.addWidget(status_label)
        if status == "failed":
            # The reason, in plain words (v1.14, A5): the batch
            # record holds the engine's error, but the card used to
            # show only "State: failed". Capped at 100 characters
            # (v1.15, A33) so a long reason cannot overflow the
            # fixed card height; the known plain errors are all far
            # shorter than that.
            text = imagegen.plain_error(candidate.get("error"))
            if len(text) > 100:
                text = text[:97] + "..."
            reason = QtWidgets.QLabel("Why: " + text)
            reason.setObjectName("small")
            reason.setWordWrap(True)
            layout.addWidget(reason)
        buttons = QtWidgets.QHBoxLayout()
        # Keep and Reject only work on a picture that exists (v1.14,
        # A5): a failed candidate has no file, and offering Keep on
        # it used to pop an error with a full internal file path.
        usable = (status != "failed"
                  and (batch_dir / candidate["file"]).exists())
        keep_btn = QtWidgets.QPushButton("Keep")
        keep_btn.setObjectName("primary")
        keep_btn.setEnabled(usable)
        keep_btn.clicked.connect(lambda: self.decided.emit(self._file, "keep"))
        buttons.addWidget(keep_btn)
        self._keep_btn = keep_btn
        reject_btn = QtWidgets.QPushButton("Reject")
        reject_btn.setEnabled(usable)
        reject_btn.clicked.connect(
            lambda: self.decided.emit(self._file, "reject"))
        buttons.addWidget(reject_btn)
        self._reject_btn = reject_btn
        layout.addLayout(buttons)
        vary_btn = QtWidgets.QPushButton("Vary this picture")
        vary_btn.setEnabled(status == "kept")
        vary_btn.setToolTip("Make variations of this kept picture "
                            "into a new batch.")
        vary_btn.clicked.connect(lambda: self.vary.emit(self._file))
        layout.addWidget(vary_btn)
        # Top-aligned content (v1.15, A33): the stretch takes the
        # slack in the fixed height instead of spreading the rows.
        layout.addStretch(1)


class ImagesWindow(_GeometryWindow):
    """The native image picker (T8): batches on the left, candidates
    on the right. Keep one, reject one (reject deletes it, so it
    asks first), or roll another round into the same batch."""

    GEOMETRY_KEY = "ImagesWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self._batch_dir: Path | None = None
        self.setWindowTitle("SKULLPT - Images")
        self.setMinimumSize(640, 420)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QHBoxLayout(central)
        main.setContentsMargins(8, 8, 8, 8)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Batches"))
        self._batch_list = QtWidgets.QListWidget()
        self._batch_list.itemClicked.connect(self._on_batch)
        left.addWidget(self._batch_list, 1)
        self._reroll_btn = QtWidgets.QPushButton("Roll another round")
        self._reroll_btn.setEnabled(False)
        self._reroll_btn.clicked.connect(self._reroll)
        left.addWidget(self._reroll_btn)
        main.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        self._prompt_label = QtWidgets.QLabel("Pick a batch to see its pictures.")
        self._prompt_label.setWordWrap(True)
        right.addWidget(self._prompt_label)
        # Pictures can start here (v1.14, A7): the empty window used
        # to be a dead end - pictures only came from the chat, and
        # nothing here said so.
        gen_row = QtWidgets.QHBoxLayout()
        self._gen_prompt = QtWidgets.QLineEdit()
        self._gen_prompt.setPlaceholderText(
            "Describe a picture, e.g. a wooden stool")
        self._gen_prompt.textChanged.connect(
            lambda text: self._gen_btn.setEnabled(bool(text.strip())))
        self._gen_prompt.returnPressed.connect(self._generate)
        gen_row.addWidget(self._gen_prompt, 1)
        self._gen_btn = QtWidgets.QPushButton("Generate images")
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self._generate)
        gen_row.addWidget(self._gen_btn)
        right.addLayout(gen_row)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._grid_holder = QtWidgets.QWidget()
        self._grid = QtWidgets.QGridLayout(self._grid_holder)
        self._scroll.setWidget(self._grid_holder)
        right.addWidget(self._scroll, 1)
        main.addLayout(right, 3)

        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 640:
            self.resize(900, 560)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the batches, keeping the current selection."""
        current = self._batch_dir.name if self._batch_dir else None
        self._batch_list.clear()
        state = bench_mod.workspace_state(self._workspace)
        # Newest first (v1.16, D11): the batch you just made sits at
        # the top, every time - not wherever its folder name sorts.
        batches = sorted(state.get("batches", []),
                         key=lambda b: b.get("created") or "",
                         reverse=True)
        for batch in batches:
            # State dicts carry "slug", not "name" (v1.9, N1); kept /
            # pending come from the candidates, not stored counts.
            candidates = batch.get("candidates", [])
            kept = sum(1 for c in candidates if c.get("status") == "kept")
            pending = sum(1 for c in candidates if c.get("status") == "pending")
            if batch.get("error") and not candidates:
                # A batch refused before any picture exists says so
                # in the list (v1.16, D4), not just in an old chat
                # message.
                text = f"{batch['slug']} (failed)"
            else:
                text = f"{batch['slug']} ({kept} kept, {pending} waiting)"
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, batch["slug"])
            self._batch_list.addItem(item)
        if current is not None:
            for row in range(self._batch_list.count()):
                item = self._batch_list.item(row)
                if item.data(QtCore.Qt.UserRole) == current:
                    self._batch_list.setCurrentRow(row)
                    self._show_batch(current)
                    return
        self._batch_dir = None
        self._reroll_btn.setEnabled(False)
        if self._batch_list.count() == 0:
            self._prompt_label.setText(
                "No batches yet. Describe a picture above and press "
                "Generate images, or ask in the chat on the Jobs window.")
        else:
            self._prompt_label.setText("Pick a batch to see its pictures.")
        self._clear_grid()

    def _generate(self) -> None:
        """Start a batch from this window (v1.14, A7). The run is a
        normal Bench action, so the chat hears when it lands."""
        prompt = self._gen_prompt.text().strip()
        if not prompt:
            return
        self._gen_prompt.clear()
        self._bench.do_action("images", {"prompt": prompt})
        Toast.show_toast(self, "Generating 3 pictures. They land in a new batch.")

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        # Drop the bottom stretch row (v1.15, A33) so a fresh batch
        # starts from a clean grid.
        if self._grid.rowCount() > 0:
            self._grid.setRowStretch(self._grid.rowCount() - 1, 0)

    def _on_batch(self, item) -> None:
        self._show_batch(item.data(QtCore.Qt.UserRole))

    def _show_batch(self, name: str) -> None:
        self._batch_dir = self._workspace / "images" / name
        try:
            batch = imagegen.load_batch(self._batch_dir)
        except Exception as exc:
            self._prompt_label.setText(f"Could not read this batch: {exc}")
            return
        self._prompt_label.setText(f"Prompt: {batch.get('prompt', '')}")
        self._reroll_btn.setEnabled(True)
        self._clear_grid()
        row = col = 0
        shown = 0
        for candidate in batch.get("candidates", []):
            if candidate.get("status") == "rejected":
                continue
            card = _CandidateCard(self._batch_dir, candidate)
            card.decided.connect(self._decide)
            card.vary.connect(self._vary)
            self._grid.addWidget(card, row, col)
            shown += 1
            col += 1
            if col >= 3:
                col = 0
                row += 1
        if shown == 0:
            # Never a blank pane (v1.16, D4): a batch refused before
            # any picture exists shows the recorded reason, in the
            # same plain words a failed card uses. A batch with
            # nothing to show and no recorded failure says that too.
            note = QtWidgets.QLabel()
            note.setObjectName("muted")
            note.setWordWrap(True)
            if batch.get("error"):
                note.setText(
                    "This batch failed before any picture was made.\n"
                    f"Why: {imagegen.plain_error(batch['error'])}")
            else:
                note.setText("No pictures to show in this batch.")
            self._grid.addWidget(note, 0, 0)
        # Pin the rows to the top (v1.15, A33): the stretch row after
        # the last card row takes the slack, so a short batch does
        # not spread its cards down the window.
        if self._grid.count():
            self._grid.setRowStretch(self._grid.rowCount(), 1)

    def _decide(self, filename: str, decision: str) -> None:
        if self._batch_dir is None:
            return
        if decision == "reject":
            answer = QtWidgets.QMessageBox.question(
                self, "Reject this picture?",
                "Rejecting deletes the picture for good, and the same "
                "picture will never be served again. Reject it?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
        try:
            imagegen.decide(self._workspace, self._batch_dir,
                            filename, decision)
        except Exception as exc:
            message = str(exc)
            if "candidate file missing" in message:
                # Plain words, not the internal path (v1.14, A5).
                message = ("That picture's file is gone, so there is "
                           "nothing to keep or reject.")
            QtWidgets.QMessageBox.warning(self, "Could not decide", message)
            return
        Toast.show_toast(self, f"{filename} kept." if decision == "keep"
                   else f"{filename} rejected and deleted.")
        self.refresh()
        self._bench.refresh()

    def _reroll(self) -> None:
        if self._batch_dir is None:
            return
        self._bench.start_reroll(self._batch_dir)
        Toast.show_toast(self, "Rolling another round. It runs in the background.")

    def _vary(self, filename: str) -> None:
        if self._batch_dir is None:
            return
        self._bench.start_vary(self._batch_dir, filename)
        Toast.show_toast(self, f"Making variations of {filename}. "
                               "They land in a new batch.")


# ----------------------------------------------------------- library window

class LibraryWindow(_GeometryWindow):
    """The Asset Library: search what is filed, start a new job from
    a filed asset's description."""

    GEOMETRY_KEY = "LibraryWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self._selected_slug: str | None = None
        self.setWindowTitle("SKULLPT - Library")
        self.setMinimumSize(560, 380)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)
        main.setContentsMargins(8, 8, 8, 8)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search the library")
        self._search.textChanged.connect(lambda _text: self.refresh())
        main.addWidget(self._search)

        middle = QtWidgets.QHBoxLayout()
        self._list = QtWidgets.QListWidget()
        self._list.setIconSize(QtCore.QSize(64, 64))
        self._list.itemClicked.connect(self._on_entry)
        middle.addWidget(self._list, 2)

        detail = QtWidgets.QVBoxLayout()
        self._detail = QtWidgets.QLabel("Pick an entry to see it here.")
        self._detail.setWordWrap(True)
        detail.addWidget(self._detail)
        self._open_btn = QtWidgets.QPushButton("Start a job from this")
        self._open_btn.setObjectName("primary")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._open_as_job)
        detail.addWidget(self._open_btn)
        detail.addStretch(1)
        middle.addLayout(detail, 1)
        main.addLayout(middle, 1)

        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 560:
            self.resize(800, 520)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the library for the current search text."""
        current = self._selected_slug
        self._list.clear()
        self._selected_slug = None
        self._open_btn.setEnabled(False)
        hits = Library(self._workspace / "library").search(
            self._search.text())
        if not hits:
            # An empty Library says how entries get here (v1.16,
            # D8), the way the Recipes window already explains
            # itself - "Pick an entry" is a dead end when there are
            # no entries and no hint how there could be.
            if self._search.text().strip():
                self._detail.setText("No entries match your search.")
            else:
                self._detail.setText(
                    "Nothing filed yet. Build a job, then file it "
                    "from the Jobs window, and it will live here.")
        elif current is None:
            self._detail.setText("Pick an entry to see it here.")
        for hit in hits:
            item = QtWidgets.QListWidgetItem(
                f"{hit.get('name', hit.get('slug', ''))}  [{hit.get('kind', '')}]")
            item.setData(QtCore.Qt.UserRole, hit.get("slug", ""))
            thumb = hit.get("thumbnail")
            if thumb:
                pixmap = QtGui.QPixmap(
                    str(self._workspace / "library" / hit["slug"] / thumb))
                if not pixmap.isNull():
                    item.setIcon(QtGui.QIcon(pixmap))
            self._list.addItem(item)
        if current is not None:
            for row in range(self._list.count()):
                item = self._list.item(row)
                if item.data(QtCore.Qt.UserRole) == current:
                    self._list.setCurrentRow(row)
                    self._on_entry(item)
                    return

    def _on_entry(self, item) -> None:
        slug = item.data(QtCore.Qt.UserRole)
        self._selected_slug = slug
        entry_dir = self._workspace / "library" / slug
        manifest_path = entry_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = {}
        files = manifest.get("files", [])
        recipes = manifest.get("recipes", [])
        # A plain date, not a timestamp (v1.15, A28): the filed
        # stamp is ISO ("2026-11-17T00:19:08+0000"), which reads as
        # machine noise. An unparseable stamp shows as-is.
        filed = manifest.get("filed", "-")
        if filed and filed != "-":
            try:
                filed = datetime.datetime.fromisoformat(
                    str(filed)).strftime("%d %b %Y")
            except ValueError:
                pass
        # Only the files a user cares about (v1.15, A28): the
        # internal workings (.blender.py, .render.py, .stats.json,
        # .build-note.txt) are not listed.
        visible = [f for f in files if Path(f).suffix.lower() in
                   {".glb", ".blend", ".png", ".jpg", ".jpeg",
                    ".obj", ".stl", ".fbx", ".gltf"}]
        self._detail.setText(
            f"{manifest.get('name', slug)}\n"
            f"Kind: {manifest.get('kind', '-')}\n"
            f"Filed: {filed}\n"
            f"Files: {', '.join(visible) if visible else 'none'}\n"
            f"Recipes: {', '.join(recipes) if recipes else 'none'}")
        self._open_btn.setEnabled((entry_dir / "description.json").exists())

    def _open_as_job(self) -> None:
        if self._selected_slug is None:
            return
        entry_dir = self._workspace / "library" / self._selected_slug
        try:
            desc = json.loads((entry_dir / "description.json").read_text())
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(
                self, "Could not open", f"No readable description: {exc}")
            return
        try:
            job = Job.create(self._workspace / "jobs", desc)
        except Exception as exc:
            # A description that no longer validates (or a job folder
            # that cannot be made) is a plain message, not a crash
            # (v1.10, N31).
            QtWidgets.QMessageBox.warning(
                self, "Could not open", f"Could not start a job from it: {exc}")
            return
        self._bench.refresh()
        self._bench.select_job(job.dir.name)
        Toast.show_toast(self, f"Started {job.dir.name} from the library entry.")


class RecipesWindow(_GeometryWindow):
    """Graduated recipes: start a new job from a filed job's template.

    A recipe graduates when a job is approved, built, and filed - the
    Library learns from approved work. Starting from one makes a new
    draft job from the template; the plan gate still applies, so the
    new job waits for approval before anything builds (v1.9, N19).
    """

    GEOMETRY_KEY = "RecipesWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self._selected_file: str | None = None
        self.setWindowTitle("SKULLPT - Recipes")
        self.setMinimumSize(560, 380)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)
        main.setContentsMargins(8, 8, 8, 8)

        intro = QtWidgets.QLabel(
            "Recipes graduate from filed jobs. Starting from one makes a "
            "new draft job - it still waits at the plan gate for approval.")
        intro.setWordWrap(True)
        main.addWidget(intro)

        middle = QtWidgets.QHBoxLayout()
        self._list = QtWidgets.QListWidget()
        self._list.itemClicked.connect(self._on_recipe)
        middle.addWidget(self._list, 2)

        detail = QtWidgets.QVBoxLayout()
        self._detail = QtWidgets.QLabel(
            "No recipes yet. File a built job into the Library and its "
            "recipe graduates here.")
        self._detail.setWordWrap(True)
        detail.addWidget(self._detail)
        self._name = QtWidgets.QLineEdit()
        self._name.setPlaceholderText("Name the new job (optional)")
        detail.addWidget(self._name)
        self._start_btn = QtWidgets.QPushButton("Start a job from this")
        self._start_btn.setObjectName("primary")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_job)
        detail.addWidget(self._start_btn)
        detail.addStretch(1)
        middle.addLayout(detail, 1)
        main.addLayout(middle, 1)

        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 560:
            self.resize(800, 520)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the graduated recipes."""
        current = self._selected_file
        self._list.clear()
        self._selected_file = None
        self._start_btn.setEnabled(False)
        recipes = graduate.list_recipes(self._workspace)
        for recipe in recipes:
            item = QtWidgets.QListWidgetItem(
                f"{recipe.get('name', recipe.get('file', ''))}  "
                f"[{recipe.get('kind', '')}]")
            item.setData(QtCore.Qt.UserRole, recipe.get("file", ""))
            self._list.addItem(item)
        if not recipes:
            self._detail.setText(
                "No recipes yet. File a built job into the Library and "
                "its recipe graduates here.")
        if current is not None:
            for row in range(self._list.count()):
                item = self._list.item(row)
                if item.data(QtCore.Qt.UserRole) == current:
                    self._list.setCurrentRow(row)
                    self._on_recipe(item)
                    return

    def _on_recipe(self, item) -> None:
        recipe_file = item.data(QtCore.Qt.UserRole)
        self._selected_file = recipe_file
        path = self._workspace / "recipes" / recipe_file
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        template = data.get("template") or {}
        parts = template.get("parts") or []
        # The summary reads in full (v1.15, A29): it used to stop
        # at 200 characters, mid-sentence, with no way to see the
        # rest. The detail label word-wraps, so nothing is lost.
        summary = str(template.get("summary", "")).strip()
        lines = [
            f"{data.get('name', recipe_file)}",
            f"Kind: {data.get('kind', '-')}",
            f"Graduated from: {data.get('graduated_from', '-')}",
            f"Parts: {len(parts)}",
        ]
        if summary:
            lines.append(f"Summary: {summary}")
        self._detail.setText("\n".join(lines))
        self._start_btn.setEnabled(True)

    def _start_job(self) -> None:
        if self._selected_file is None:
            return
        new_name = self._name.text().strip() or None
        try:
            desc = graduate.apply_recipe(self._workspace, self._selected_file,
                                         new_name)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(
                self, "Could not start from this recipe", str(exc))
            return
        job = Job.create(self._workspace / "jobs", desc)
        self._bench.refresh()
        self._bench.select_job(job.dir.name)
        Toast.show_toast(self, f"Started {job.dir.name} from the recipe.")
        # The chat logs it too (v1.15, A30): a Toast alone vanishes,
        # and the user never sees the job start. The guard keeps
        # test doubles (which have no _log) working.
        log = getattr(self._bench, "_log", None)
        if log is not None:
            log("Bench", f"Started {job.dir.name} from the recipe.")


# ---------------------------------------------------------- settings window

_ENGINE_LABELS = {
    "blender": "Blender (builds the 3D files)",
    "image_engine": "Image engine (draws reference pictures)",
    "image_model": "Image model (the picture model file)",
    "gemini_key": "Gemini key (plans and checks your builds)",
    "groq_key": "Groq key (fixes drafts that fail a check)",
    "ollama": "Ollama (local model, no key needed)",
    "hf_token": "Hugging Face token (photo to mesh)",
    "character_studio": "Character Studio (MPFB2 and its skins)",
}

# v1.8: every fix is in-app: the Setup check, the installer, or a
# key section below. No fix text points at the terminal, an
# environment variable, or a manual download.
_ENGINE_FIXES = {
    "blender": "Use the Setup check to install it, or re-run the "
               "SKULLPT installer.",
    "image_engine": "Use the Setup check to install it, or re-run the "
                    "SKULLPT installer.",
    "image_model": "Use the Setup check to download it, or re-run the "
                   "SKULLPT installer.",
    "gemini_key": "Paste your Gemini key below and save it.",
    "groq_key": "Paste your Groq key below and save it.",
    "ollama": "Use the Setup check to install it, or to pull the "
              "local model.",
    "hf_token": "Save a Hugging Face token below to turn photos "
                "into meshes.",
    "character_studio": "Use the Setup check to install it, or "
                        "re-run the SKULLPT installer.",
}


class SettingsWindow(_GeometryWindow):
    """Engines, switches, and the keys (T9, v1.8 S3). A key is stored
    in a local file the repo never sees, and this window never shows
    a key back - only whether one is saved."""

    GEOMETRY_KEY = "SettingsWindow"

    # Key status lines: (saved in the workspace, set in the
    # environment, not set). Never the value itself.
    _KEY_STATUS = {
        "hf": ("Token saved.", "Token set in the environment.",
               "No token saved."),
        "gemini": ("Gemini key saved.",
                   "Gemini key set in the environment.",
                   "No Gemini key saved."),
        "groq": ("Groq key saved.",
                 "Groq key set in the environment.",
                 "No Groq key saved."),
    }
    _KEY_ENGINE = {"hf": "hf_token", "gemini": "gemini_key",
                   "groq": "groq_key"}

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self._key_fields: dict[str, QtWidgets.QLineEdit] = {}
        self._key_statuses: dict[str, QtWidgets.QLabel] = {}
        self._key_save_buttons: dict[str, QtWidgets.QPushButton] = {}
        self._key_calls: dict[str, _BackgroundCall] = {}
        self._token_call: _BackgroundCall | None = None
        self._backup_call: _BackgroundCall | None = None
        self.setWindowTitle("SKULLPT - Settings")
        self.setMinimumSize(520, 420)

        # S6: the settings content scrolls, so nothing is ever hidden
        # off the bottom edge with no indication it exists.
        content = QtWidgets.QWidget()
        main = QtWidgets.QVBoxLayout(content)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        engines_title = QtWidgets.QLabel("Engines")
        engines_title.setObjectName("heading")
        main.addWidget(engines_title)
        # S6: one row per engine (name + status), not a form: the
        # status label takes the full remaining width and wraps, so
        # a long fix line can never run off the right edge.
        self._engine_rows = QtWidgets.QVBoxLayout()
        self._engine_labels: dict[str, QtWidgets.QLabel] = {}
        for key, label in _ENGINE_LABELS.items():
            row = QtWidgets.QHBoxLayout()
            name = QtWidgets.QLabel(label)
            name.setWordWrap(True)
            row.addWidget(name)
            value = QtWidgets.QLabel()
            value.setWordWrap(True)
            self._engine_labels[key] = value
            row.addWidget(value, 1)
            self._engine_rows.addLayout(row)
        main.addLayout(self._engine_rows)
        setup_btn = QtWidgets.QPushButton("Open the Setup check")
        setup_btn.clicked.connect(self._open_setup_check)
        main.addWidget(setup_btn)
        setup_note = QtWidgets.QLabel(
            "The Setup check lists what is missing and fixes it from "
            "inside the app.")
        setup_note.setWordWrap(True)
        setup_note.setObjectName("muted")
        main.addWidget(setup_note)

        switches_title = QtWidgets.QLabel("Switches")
        switches_title.setObjectName("heading")
        main.addWidget(switches_title)
        self._critic_check = QtWidgets.QCheckBox(
            "Critic compares renders against the reference picture")
        self._critic_check.setChecked(
            settings_mod.load_settings(self._workspace).get(
                "critic_reference_check", True))
        self._critic_check.toggled.connect(self._save_critic_toggle)
        main.addWidget(self._critic_check)

        render_title = QtWidgets.QLabel("Render settings")
        render_title.setObjectName("heading")
        main.addWidget(render_title)
        main.addWidget(QtWidgets.QLabel(
            "Defaults for every build. A job can set its own in the "
            "Render window."))
        render_form = QtWidgets.QFormLayout()
        self._engine_combo = QtWidgets.QComboBox()
        self._engine_combo.addItem("Auto (try EEVEE, fall back to Cycles)", "auto")
        self._engine_combo.addItem("EEVEE (fast, needs a GPU context)", "eevee")
        self._engine_combo.addItem("Cycles (slow, always works)", "cycles")
        self._engine_combo.currentIndexChanged.connect(self._save_render_settings)
        render_form.addRow("Engine", self._engine_combo)
        self._res_spin = QtWidgets.QSpinBox()
        self._res_spin.setRange(128, 4096)
        self._res_spin.setSingleStep(64)
        self._res_spin.setSuffix(" px")
        self._res_spin.valueChanged.connect(self._save_render_settings)
        render_form.addRow("Resolution", self._res_spin)
        self._samples_spin = QtWidgets.QSpinBox()
        self._samples_spin.setRange(1, 512)
        self._samples_spin.valueChanged.connect(self._save_render_settings)
        render_form.addRow("Cycles samples", self._samples_spin)
        self._denoise_check = QtWidgets.QCheckBox("Denoise (Cycles only)")
        self._denoise_check.toggled.connect(self._save_render_settings)
        render_form.addRow("", self._denoise_check)
        self._tier_combo = QtWidgets.QComboBox()
        self._tier_combo.addItem("Draft (fewer nodes, fast)", "draft")
        self._tier_combo.addItem("Balanced (the full recipe)", "balanced")
        self._tier_combo.addItem("Final (extra detail, 1024 bakes)", "final")
        self._tier_combo.currentIndexChanged.connect(self._save_render_settings)
        render_form.addRow("Material quality", self._tier_combo)
        main.addLayout(render_form)
        self._last_engine_label = QtWidgets.QLabel()
        self._last_engine_label.setObjectName("muted")
        self._last_engine_label.setWordWrap(True)
        main.addWidget(self._last_engine_label)

        token_title = QtWidgets.QLabel("Hugging Face token")
        token_title.setObjectName("heading")
        main.addWidget(token_title)
        token_note = QtWidgets.QLabel(
            "The token turns photos into 3D meshes on Hugging Face's free "
            "GPU. It is saved in your workspace only, in a file the repo "
            "never sees. SKULLPT checks the token works before saving "
            "it, and never shows it back.")
        token_note.setWordWrap(True)
        token_note.setObjectName("muted")
        main.addWidget(token_note)
        token_row = QtWidgets.QHBoxLayout()
        self._token_field = QtWidgets.QLineEdit()
        self._token_field.setEchoMode(QtWidgets.QLineEdit.Password)
        self._token_field.setPlaceholderText("Paste your Hugging Face token")
        token_row.addWidget(self._token_field, 1)
        self._token_save_btn = QtWidgets.QPushButton("Save token")
        self._token_save_btn.setObjectName("primary")
        self._token_save_btn.clicked.connect(self._save_token)
        token_row.addWidget(self._token_save_btn)
        clear_btn = QtWidgets.QPushButton("Clear token")
        clear_btn.clicked.connect(self._clear_token)
        token_row.addWidget(clear_btn)
        main.addLayout(token_row)
        self._token_status = QtWidgets.QLabel()
        self._token_status.setObjectName("muted")
        main.addWidget(self._token_status)

        self._add_key_section(
            main, "gemini", "Gemini key",
            "The Gemini key plans your builds and checks the results. "
            "Paste the key you already have. It is saved in your workspace "
            "only, in a file the repo never sees. SKULLPT checks the "
            "key works before saving it, and never shows it back.",
            "Paste your Gemini key",
            keycheck.GEMINI_KEY_PAGE)
        self._add_key_section(
            main, "groq", "Groq key",
            "The Groq key fixes drafts that fail a check. "
            "Paste the key you already have. "
            "It is saved in your workspace only, in a file the repo "
            "never sees. SKULLPT checks the key works before saving "
            "it, and never shows it back.",
            "Paste your Groq key",
            keycheck.GROQ_KEY_PAGE)

        workspace_title = QtWidgets.QLabel("Workspace")
        workspace_title.setObjectName("heading")
        main.addWidget(workspace_title)
        self._workspace_label = QtWidgets.QLabel(str(self._workspace))
        self._workspace_label.setWordWrap(True)
        main.addWidget(self._workspace_label)
        folder_btn = QtWidgets.QPushButton("Choose a different folder")
        folder_btn.clicked.connect(self._choose_workspace)
        main.addWidget(folder_btn)
        main.addWidget(QtWidgets.QLabel(
            "A new folder takes effect the next time SKULLPT opens."))
        backup_title = QtWidgets.QLabel("Backup")
        backup_title.setObjectName("heading")
        main.addWidget(backup_title)
        main.addWidget(QtWidgets.QLabel(
            "Back up the whole workspace into one zip file. Your keys "
            "are never in a backup."))
        backup_btn = QtWidgets.QPushButton("Back up workspace")
        backup_btn.clicked.connect(self._backup_workspace)
        main.addWidget(backup_btn)
        self._backup_btn = backup_btn
        main.addStretch(1)

        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 520:
            self.resize(620, 560)
        self._load_render_settings()
        self.refresh()

    def _load_render_settings(self) -> None:
        """Fill the render controls from the saved workspace defaults
        (signals blocked: loading must not re-save)."""
        saved = settings_mod.load_settings(self._workspace)
        for widget in (self._engine_combo, self._res_spin,
                       self._samples_spin, self._denoise_check,
                       self._tier_combo):
            widget.blockSignals(True)
        try:
            index = self._engine_combo.findData(
                saved.get("render_engine", "auto"))
            self._engine_combo.setCurrentIndex(max(0, index))
            self._res_spin.setValue(
                int(saved.get("render_resolution_px", 640)))
            self._samples_spin.setValue(
                int(saved.get("render_samples", 24)))
            self._denoise_check.setChecked(
                bool(saved.get("render_denoise", False)))
            tier_index = self._tier_combo.findData(
                saved.get("material_tier", "balanced"))
            self._tier_combo.setCurrentIndex(max(0, tier_index))
        finally:
            for widget in (self._engine_combo, self._res_spin,
                           self._samples_spin, self._denoise_check,
                           self._tier_combo):
                widget.blockSignals(False)

    def _save_render_settings(self) -> None:
        settings_mod.save_render_settings(self._workspace, {
            "engine": self._engine_combo.currentData(),
            "resolution_px": self._res_spin.value(),
            "samples": self._samples_spin.value(),
            "denoise": self._denoise_check.isChecked(),
            "tier": self._tier_combo.currentData()})

    def _backup_workspace(self) -> None:
        from .backup import backup_workspace

        default = str(Path(self._workspace).parent
                      / f"{self._workspace.name}-backup.zip")
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Back up workspace", default, "Zip files (*.zip)")
        if not dest:
            return
        # The zip runs off the UI thread (v1.10, N33): a big workspace
        # takes a while, and the window must not freeze.
        self._backup_btn.setEnabled(False)
        call = _BackgroundCall(
            lambda: backup_workspace(self._workspace, dest))
        call.finished.connect(self._on_backup_done)
        self._backup_call = call
        call.start()

    def _on_backup_done(self, result) -> None:
        self._backup_call = None
        self._backup_btn.setEnabled(True)
        if isinstance(result, Exception):
            QtWidgets.QMessageBox.warning(self, "Backup failed", str(result))
            return
        Toast.show_toast(self, result["note"])
        # The chat logs it too (v1.15, A30), the same as the
        # File-menu backup: a Toast alone vanishes.
        log = getattr(self._bench, "_log", None)
        if log is not None:
            log("Bench", result["note"])

    def refresh(self) -> None:
        """Re-read the engine status and the key states."""
        status = bench_mod.engine_status(self._workspace)
        for key, label in self._engine_labels.items():
            ready = status.get(key, False)
            if ready:
                label.setText("Ready")
            elif key == "ollama":
                # Two ways to be missing: the server is down, or the
                # model is not pulled yet. Say which one.
                if not status.get("ollama_server"):
                    label.setText("Missing. The Ollama server is not "
                                  "running. Use the Setup check to "
                                  "install it.")
                else:
                    label.setText("Missing. The server is running but "
                                  "the local model is not pulled yet. "
                                  "Use the Setup check to pull it.")
            else:
                label.setText(f"Missing. {_ENGINE_FIXES[key]}")
        self._token_status.setText(self._key_status_text("hf", status))
        self._key_statuses["gemini"].setText(
            self._key_status_text("gemini", status))
        self._key_statuses["groq"].setText(
            self._key_status_text("groq", status))
        last = settings_mod.last_render_engine(self._workspace)
        if last.get("engine"):
            # A plain date, like the Library shows (v1.16, D12):
            # the stored stamp is ISO machine text
            # ("2026-11-19T03:42:23+0000"). An unparseable stamp
            # shows as-is.
            at = last.get("at")
            if at:
                try:
                    at = datetime.datetime.fromisoformat(
                        str(at)).strftime("%d %b %Y")
                except ValueError:
                    at = str(at)
            self._last_engine_label.setText(
                "Last render engine that worked here: "
                f"{last['engine']}"
                + (f" ({at})" if at else ""))
        else:
            self._last_engine_label.setText(
                "No render has run yet, so the working engine is not known.")

    def _key_status_text(self, kind: str, status: dict) -> str:
        """Saved / set in the environment / not set. Never the value."""
        saved, env, none = self._KEY_STATUS[kind]
        if tokenfile.has_key(kind, self._workspace):
            return saved
        if status.get(self._KEY_ENGINE[kind]):
            return env
        return none

    def _add_key_section(self, main, kind: str, title: str, note: str,
                         placeholder: str, key_page: str) -> None:
        """A Gemini/Groq key section (v1.8 S3): paste the key you
        already have, save it (validated first), clear it. The "Get
        a key" button is only for someone who does not have one yet."""
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("heading")
        main.addWidget(heading)
        note_label = QtWidgets.QLabel(note)
        note_label.setWordWrap(True)
        note_label.setObjectName("muted")
        main.addWidget(note_label)
        row = QtWidgets.QHBoxLayout()
        field = QtWidgets.QLineEdit()
        field.setEchoMode(QtWidgets.QLineEdit.Password)
        field.setPlaceholderText(placeholder)
        row.addWidget(field, 1)
        save_btn = QtWidgets.QPushButton(f"Save {title}")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(lambda: self._save_key(kind))
        row.addWidget(save_btn)
        clear_btn = QtWidgets.QPushButton(f"Clear {title}")
        clear_btn.clicked.connect(lambda: self._clear_key(kind))
        row.addWidget(clear_btn)
        main.addLayout(row)
        get_row = QtWidgets.QHBoxLayout()
        get_btn = QtWidgets.QPushButton(
            f"Get a {title} (opens your browser)")
        get_btn.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(
                QtCore.QUrl(key_page)))
        get_row.addWidget(get_btn)
        get_row.addStretch(1)
        main.addLayout(get_row)
        status = QtWidgets.QLabel()
        status.setObjectName("muted")
        main.addWidget(status)
        self._key_fields[kind] = field
        self._key_statuses[kind] = status
        self._key_save_buttons[kind] = save_btn

    def _save_key(self, kind: str) -> None:
        """Validate the pasted key against the real provider, then
        save it. The check runs off the UI thread (U30); a key that
        does not validate is never saved."""
        value = self._key_fields[kind].text().strip()
        if not value:
            QtWidgets.QMessageBox.warning(
                self, "No key", "Paste a key first.")
            return
        self._key_statuses[kind].setText("Checking the key...")
        self._key_save_buttons[kind].setEnabled(False)
        validate = (keycheck.validate_gemini_key if kind == "gemini"
                    else keycheck.validate_groq_key)
        call = _BackgroundCall(lambda: validate(value))
        call.finished.connect(
            lambda result: self._on_key_validated(kind, value, result))
        self._key_calls[kind] = call
        call.start()

    def _on_key_validated(self, kind: str, value: str, result) -> None:
        self._key_save_buttons[kind].setEnabled(True)
        self._key_calls.pop(kind, None)
        if isinstance(result, Exception):
            self._key_statuses[kind].setText(
                f"The check failed ({result}). The key was not saved.")
            return
        ok, message = result
        if not ok:
            self._key_statuses[kind].setText(message)
            return
        tokenfile.save_key(kind, self._workspace, value)
        self._key_fields[kind].clear()
        self.refresh()
        self._bench.refresh()
        title = "Gemini key" if kind == "gemini" else "Groq key"
        Toast.show_toast(self, f"{title} saved in your workspace.")

    def _clear_key(self, kind: str) -> None:
        tokenfile.clear_key(kind, self._workspace)
        self.refresh()
        self._bench.refresh()
        title = "Gemini key" if kind == "gemini" else "Groq key"
        Toast.show_toast(self, f"{title} cleared.")

    def _open_setup_check(self) -> None:
        opener = getattr(self._bench, "_open_window", None)
        if callable(opener):
            opener("setup")

    def _save_critic_toggle(self, checked: bool) -> None:
        current = settings_mod.load_settings(self._workspace)
        current["critic_reference_check"] = checked
        settings_mod.save_settings(self._workspace, current)

    def _save_token(self) -> None:
        """Validate the pasted token against Hugging Face, then save
        it (v1.10, N32). The check runs off the UI thread (U30), the
        same pattern as _save_key; a token that does not validate is
        never saved."""
        token = self._token_field.text().strip()
        if not token:
            QtWidgets.QMessageBox.warning(
                self, "No token", "Paste a token first.")
            return
        self._token_status.setText("Checking the token...")
        self._token_save_btn.setEnabled(False)
        call = _BackgroundCall(lambda: keycheck.validate_hf_token(token))
        call.finished.connect(
            lambda result: self._on_token_validated(token, result))
        self._token_call = call
        call.start()

    def _on_token_validated(self, token: str, result) -> None:
        self._token_save_btn.setEnabled(True)
        self._token_call = None
        if isinstance(result, Exception):
            self._token_status.setText(
                f"The check failed ({result}). The token was not saved.")
            return
        ok, message = result
        if not ok:
            self._token_status.setText(message)
            return
        tokenfile.save_token(self._workspace, token)
        self._token_field.clear()
        self.refresh()
        self._bench.refresh()
        Toast.show_toast(self, "Token saved in your workspace.")

    def _clear_token(self) -> None:
        tokenfile.clear_token(self._workspace)
        self.refresh()
        self._bench.refresh()
        Toast.show_toast(self, "Token cleared.")

    def _choose_workspace(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose a workspace folder", str(self._workspace))
        if not folder:
            return
        _settings().setValue("workspace", folder)
        Toast.show_toast(self, "Workspace saved. It opens next time SKULLPT starts.")


# -------------------------------------------------------------- setup window

class SetupWindow(_GeometryWindow):
    """The first-run setup check (v1.8, S4/S5): everything missing,
    one button each (Install, Pull the model, or Add in Settings).
    Fixes run the installer (engines), pull the local model
    (Ollama), or open Settings (keys). No terminal anywhere."""

    GEOMETRY_KEY = "SetupWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self._pull_call: _BackgroundCall | None = None
        self._pull_progress: _ProgressEmitter | None = None
        self._installer_call: _BackgroundCall | None = None
        self.setWindowTitle("SKULLPT - Setup check")
        self.setMinimumSize(560, 420)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        intro = QtWidgets.QLabel(
            "Everything SKULLPT needs, in one place. What is missing "
            "is listed below. Press Install, Pull the model, or Add "
            "in Settings next to it and the app takes care of it.")
        intro.setWordWrap(True)
        main.addWidget(intro)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        self._items_layout = QtWidgets.QVBoxLayout(content)
        self._items_layout.setSpacing(12)
        scroll.setWidget(content)
        main.addWidget(scroll, 1)

        bottom = QtWidgets.QHBoxLayout()
        check_btn = QtWidgets.QPushButton("Check again")
        check_btn.clicked.connect(self.refresh)
        bottom.addWidget(check_btn)
        bottom.addStretch(1)
        later_btn = QtWidgets.QPushButton("Remind me later")
        later_btn.clicked.connect(self._remind_later)
        bottom.addWidget(later_btn)
        main.addLayout(bottom)

        restore_geometry(self, self.GEOMETRY_KEY)
        self.refresh()

    def _remind_later(self) -> None:
        """Close until the next launch: the Bench only offers the
        setup check once per launch, so closing is the dismissal."""
        self._bench._setup_dismissed = True
        self.close()

    def refresh(self) -> None:
        """Re-check every engine and rebuild the missing list."""
        while self._items_layout.count():
            item = self._items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        items = bench_mod.missing_setup_items(self._workspace)
        if not items:
            done = QtWidgets.QLabel(
                "Everything is ready. Nothing is missing.")
            done.setWordWrap(True)
            self._items_layout.addWidget(done)
        for item in items:
            self._items_layout.addWidget(self._row(item))
        self._items_layout.addStretch(1)

    def _row(self, item: dict) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("card")
        layout = QtWidgets.QHBoxLayout(frame)
        text = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel(item["title"])
        title.setObjectName("heading")
        text.addWidget(title)
        detail = QtWidgets.QLabel(item["detail"])
        detail.setWordWrap(True)
        detail.setObjectName("muted")
        text.addWidget(detail)
        layout.addLayout(text, 1)
        fix = item["fix"]
        if fix == "installer":
            btn = QtWidgets.QPushButton("Install")
            btn.clicked.connect(
                lambda: self._run_installer(item["key"]))
        elif fix == "pull_model":
            btn = QtWidgets.QPushButton("Pull the model")
            btn.clicked.connect(lambda: self._pull_model(btn))
        else:
            btn = QtWidgets.QPushButton("Add in Settings")
            btn.clicked.connect(self._open_settings)
        layout.addWidget(btn)
        return frame

    def _run_installer(self, key: str) -> None:
        """Launch the SKULLPT installer GUI and report how it ended
        (v1.10, N34): the wait runs off the UI thread, and a failed
        installer is reported instead of claimed as running. Exit 3
        is the installer's own "cancelled" code."""
        install_sh = Path(__file__).resolve().parent.parent / "install.sh"
        if not install_sh.exists():
            QtWidgets.QMessageBox.warning(
                self, "Installer not found",
                "The SKULLPT installer was not found next to this app.")
            return

        def _run() -> int:
            proc = subprocess.Popen([str(install_sh), "--gui"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            return proc.wait()

        call = _BackgroundCall(_run)
        call.finished.connect(self._on_installer_done)
        self._installer_call = call
        call.start()
        Toast.show_toast(
            self, "Installer is running. It only downloads what is "
                  "missing.")

    def _on_installer_done(self, result) -> None:
        self._installer_call = None
        if isinstance(result, Exception):
            QtWidgets.QMessageBox.warning(
                self, "Could not start the installer", str(result))
            return
        if result == 3:
            Toast.show_toast(
                self, "Install cancelled. Run the installer again to finish.")
            return
        if result != 0:
            QtWidgets.QMessageBox.warning(
                self, "Installer stopped",
                f"The installer stopped before finishing (exit {result}). "
                "Run it again from the Setup check.")
            return
        self.refresh()
        Toast.show_toast(self, "Installer finished.")

    def _pull_model(self, button: QtWidgets.QPushButton) -> None:
        """Pull the local model with the Ollama CLI, off the UI
        thread (U30). The pull resumes if it is interrupted."""
        from . import ollama as ollama_mod

        button.setEnabled(False)
        button.setText("Pulling...")
        # The pull's own progress lines drive the button (v1.10, N35):
        # the emitter carries each line to the UI thread, throttled
        # to one update per half second so the button does not flicker.
        progress = _ProgressEmitter()
        last = [0.0]

        def _show(line: str) -> None:
            if not _qt_is_valid(button):
                return
            now = time.monotonic()
            if now - last[0] < 0.5:
                return
            last[0] = now
            button.setText(line[:60] or "Pulling...")

        progress.line.connect(_show)
        call = _BackgroundCall(
            lambda: ollama_mod.pull_model(on_progress=progress.line.emit))
        call.finished.connect(
            lambda result: self._on_pull_done(button, result))
        self._pull_call = call
        self._pull_progress = progress
        call.start()

    def _on_pull_done(self, button: QtWidgets.QPushButton, result) -> None:
        from . import ollama as ollama_mod

        self._pull_call = None
        self._pull_progress = None
        button.setEnabled(True)
        button.setText("Pull the model")
        if isinstance(result, Exception):
            QtWidgets.QMessageBox.warning(self, "Pull failed", str(result))
            return
        ok, message = result
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Pull failed", message)
            return
        ollama_mod.status(force=True)
        self.refresh()
        Toast.show_toast(self, message)

    def _open_settings(self) -> None:
        opener = getattr(self._bench, "_open_window", None)
        if callable(opener):
            opener("settings")
        self.close()


# -------------------------------------------------------------- help window

_HELP_HTML = """<html><body>
<h2>How to Use SKULLPT</h2>
<p>SKULLPT is a workshop that turns plain descriptions into 3D assets:
Blender files, game-ready models, Unreal import packages, and
reference pictures. You describe what you want; SKULLPT drafts it,
you approve it, and only then does anything build.</p>
<h3>The windows</h3>
<p><b>Jobs</b> is home: your jobs as cards, the chat panel, and the
status bar. <b>Render</b> shows the selected job as a 3D scene you
can orbit, and lets you pick a part and edit it. <b>Character
Studio</b> shapes a human with sliders, a skin and a rig.
<b>Images</b> is where you keep or reject generated pictures.
<b>Library</b> holds everything you have filed. <b>Recipes</b>
keeps the recipes your filed jobs graduate into, so you can start
a new job from one. <b>Settings</b> shows which engines are
ready and holds your keys (Gemini, Groq, Hugging Face).</p>
<h3>Setup</h3>
<p>The first time SKULLPT opens, the Setup check lists anything
missing: an engine, the local model, or a key, with a button next
to each one (Install, Pull the model, or Add in Settings).
Engines install from inside the app; keys are pasted in Settings
and saved in your workspace only, in files the repo never sees.
The Setup check can be opened any time from File, then Setup
check, or from the button in Settings.</p>
<h3>The five steps</h3>
<ol>
<li><b>Make.</b> Type "make a wooden shop panel" in the chat, or
start from a bundled example with the From example button.</li>
<li><b>Approve.</b> Read the plain-English summary on the job card.
Nothing builds until you approve it. This is the plan gate.</li>
<li><b>Build.</b> Press Build. SKULLPT exports the Blender file, the
game-ready model, and the renders, then checks them.</li>
<li><b>Look.</b> Open Render to see the scene, or look at the renders
on the card. If it is wrong, edit a part and approve again.</li>
<li><b>File.</b> When it is right, press File. The job goes into the
Library, and its recipe is saved for next time.</li>
</ol>
<h3>Pictures</h3>
<p>Type "picture of a wooden sign" in the chat to generate reference
pictures. Keep the ones you like in the Images window; the first kept
picture becomes the job's reference. A kept picture can be varied
("Vary this picture") into a new batch of pictures like it.</p>
<h3>Mesh from a photo</h3>
<p>Use Jobs, then Mesh from photo, and pick a photo. SKULLPT sends it
to Hugging Face's free GPU and files the textured mesh in a job. You
need a Hugging Face token (Settings) and an internet connection. The
mesh is kept and the build imports it; a meshed job can also be
packaged for Unreal without a Blender build first.</p>
<h3>Opening an existing asset</h3>
<p>Use Jobs, then Open an asset, and pick a .glb, .obj, .stl, .fbx or
.blend file. SKULLPT reads it into a description you can edit. The
original file is never changed; edited versions are filed next to it.
The source mesh is kept in the workspace and the build imports it,
instead of rebuilding from boxes.</p>
<h3>Render settings</h3>
<p>Settings holds the render defaults: engine (Auto tries EEVEE and
falls back to Cycles when EEVEE cannot run on this machine),
resolution, Cycles samples, denoise, and the material quality tier
(Draft, Balanced, Final - how much detail the material recipes
wire). A job can set its own in the Render window. Changing render
settings never sends a job back for approval.</p>
<h3>Materials and the dial</h3>
<p>A part's material can be a flat preset (wood, metal, glass) or a
real procedural material: pick a part in the Render window, press
Dial (or double-click the part), and the Material Dial opens. The
inner ring is the pages (Material, Style, Layout, Detail, Form,
Surface, Life, Colour, Depth, Saved); the outer ring holds the
page's swatches or knobs; the centre shows the active knob with its
gauge. Drag up or down on the centre to turn a knob, or scroll over
it. Number keys 1 to 9 pick pages, 0 picks Saved. The Life page
holds the wear knobs: dirt, edge wear and wet show in Simple mode,
the rest (grime, dust, moss, rust, chips, scratches) in Advanced.
Moss only grows on wood, stone and ground; rust only on metal.
Simple mode shows only the knobs that matter for each material;
Advanced shows everything. The preview at the bottom re-renders
when a knob settles. Surprise rolls a random material and
records its seed, so it can be made again exactly. Save stores the
material as a preset for later. Close applies the material to the
part. A part with no material shows "No material here - open the
dial to start." Ctrl+click a part to copy its material onto the
selected part.</p>
<h3>Material quality</h3>
<p>Every material builds at a quality tier: Draft, Balanced or
Final. Draft wires fewer nodes (no wear layer, no knots, chips or
frost, less fine detail) so builds and previews are fast; Balanced
is the full recipe; Final adds extra fine detail and bakes at 1024.
Set the default in Settings, or override it per job in the Render
window - the build report lists the tier used and the node counts.
The dial's previews always use Draft unless you ask otherwise.</p>
<h3>Baking PBR maps</h3>
<p>After a job is built, press Bake PBR maps in the Render window
to turn each material into texture maps (colour, roughness, normal,
height, and metallic where the family uses it) in the job's output
folder. The bake size follows the material quality tier: Draft 256,
Balanced 512, Final 1024. The maps travel into the Unreal package,
so the imported asset carries its textures. Glass does not bake:
its look comes from light passing through it, not from surface
maps, so there is nothing honest to bake - the bake report says so.</p>
<h3>True displacement</h3>
<p>A part's material can turn its texture into real geometry:
tick True displacement in the part editor. The part is subdivided
16 times denser and the bump pattern pushes the surface out, so
Cycles renders show real ridges instead of a flat bump. It costs
triangles (16 times as many) and RAM, so the budget gate counts
the denser mesh and the build turns displacement off with a plain
note when the machine cannot afford it. The setting stays on in
the description - build again when more memory is free. The GLB
carries the dense mesh; the displacement itself shows in Cycles
renders, while EEVEE shows bump.</p>
<h3>Builds in a queue</h3>
<p>Only one heavy job runs at a time. If you start a build while
another is running, yours waits in the queue and starts when its turn
comes. Queued builds can be cancelled from the status bar.</p>
<h3>Unreal</h3>
<p>A built job can be packaged for Unreal: open the job, press
Unreal package, and pick a folder. The package has the .glb, the
.blend, the description, an import script, and a README. When the
job has been baked, the texture maps go in too, and the import
script brings them into Unreal alongside the model.</p>
<h3>Blender</h3>
<p>Building needs Blender 4.5.9 or newer installed. The renders
and the game-ready model come from Blender; without it, SKULLPT can
still draft descriptions and show the viewport.</p>
<h3>Your workspace</h3>
<p>Everything lives in your workspace folder: jobs, the library,
image batches, and settings. The folder is shown in Settings. File,
then Back up workspace, to zip the whole workspace into one file
(your keys are never in a backup). File, then Open workspace,
switches to another folder right away.</p>
<h3>Updates</h3>
<p>When SKULLPT opens, it quietly asks GitHub whether a newer
version is out. Up to date, or the check fails: nothing happens,
no popup. A newer version: it asks "Update now?" and downloads
nothing until you say yes. The download is checked against the
release checksum before anything is installed; a download that
does not match, or a release with no checksum, is never
installed. Then it asks whether to restart now. Yes restarts with
the update applied; No leaves the verified update waiting and it
applies the next time SKULLPT starts. Only the program's own code
is replaced - your workspace, your keys, and the private
environment are never touched - and a backup of the replaced files
is kept until the new version has launched cleanly. Help, then
Check for updates, runs the same check by hand. Every release from
v1.15 on carries a checksum file next to the download.</p>
<h3>About</h3>
<p>SKULLPT v__SKULLPT_VERSION__. A standalone Linux design and asset shell, by
Broke n Bored Studio. Source: github.com/BROKE-N-BORED/SKULLPT</p>
</body></html>"""


class HelpWindow(_GeometryWindow):
    """The How to Use index (T13): what SKULLPT is, the windows, the
    five steps, and the extras."""

    GEOMETRY_KEY = "HelpWindow"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SKULLPT - How to Use")
        self.setMinimumSize(480, 360)
        from . import __version__

        browser = QtWidgets.QTextBrowser()
        browser.setHtml(_HELP_HTML.replace("__SKULLPT_VERSION__", __version__))
        self.setCentralWidget(browser)
        restore_geometry(self, self.GEOMETRY_KEY)
        if self.width() < 480:
            self.resize(640, 560)


# -------------------------------------------------------- debug report dialog

class DebugReportDialog(QtWidgets.QDialog):
    """The debug report (T15): versions, engines, free RAM, the
    selected job's state and log tail, recent runs. Save it to a
    file or copy it. Never includes secrets or the chat."""

    def __init__(self, workspace: Path, job: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SKULLPT - Debug Report")
        self.setMinimumSize(560, 420)
        layout = QtWidgets.QVBoxLayout(self)
        self._text = QtWidgets.QTextEdit()
        self._text.setObjectName("mono")
        self._text.setReadOnly(True)
        self._text.setPlainText(bench_mod.debug_report(workspace, job))
        layout.addWidget(self._text, 1)
        buttons = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Save to a file")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        copy_btn = QtWidgets.QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _save(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save debug report", "skullpt-debug-report.txt",
            "Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self._text.toPlainText())
        except OSError as exc:
            # A file that cannot be written (read-only folder, full
            # disk) is a plain message, not a crash (v1.10, N31).
            QtWidgets.QMessageBox.warning(
                self, "Could not save", f"The report could not be saved: {exc}")
            return
        Toast.show_toast(self, "Debug report saved.")

    def _copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText(
            self._text.toPlainText())
        Toast.show_toast(self, "Debug report copied.")


# -------------------------------------------------------- build report dialog

class BuildReportDialog(QtWidgets.QDialog):
    """The build report in the Bench (T19): what the last build did,
    which checks passed, what the critic said, and the measured
    triangle count."""

    def __init__(self, workspace: Path, job_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"SKULLPT - Build report for {job_name}")
        self.setMinimumSize(520, 380)
        layout = QtWidgets.QVBoxLayout(self)
        text = QtWidgets.QTextEdit()
        text.setObjectName("mono")
        text.setReadOnly(True)
        text.setPlainText(_format_build_report(workspace, job_name))
        layout.addWidget(text, 1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


def _format_build_report(workspace: Path, job_name: str) -> str:
    report = bench_mod.build_report(workspace, job_name)
    if report is None:
        return f"{job_name} has not built yet. No build report exists."
    lines = [f"Build report for {job_name}",
             f"Result: {'passed' if report.get('ok') else 'failed'}"]
    if report.get("stage"):
        lines.append(f"Stage: {report['stage']}")
    outputs = report.get("outputs") or []
    if outputs:
        lines.append("Outputs:")
        lines.extend(f"  {name}" for name in outputs)
    checks = (report.get("machine_checks") or {}).get("checks") or []
    if checks:
        lines.append("Machine checks:")
        for check in checks:
            verdict = "pass" if check.get("ok") else "FAIL"
            lines.append(f"  {verdict} {check.get('check')}: "
                         f"{check.get('detail', '')}")
    if report.get("total_tris") is not None:
        lines.append(f"Triangles: {report['total_tris']:,} (measured)")
        for name, part in (report.get("tris_by_part") or {}).items():
            lines.append(f"  {name}: {part.get('tris', '-')} tris")
    materials = report.get("materials") or {}
    measured = report.get("measured_materials") or {}
    if materials or measured:
        lines.append("Materials:")
        for name in sorted(set(materials) | set(measured)):
            pred = materials.get(name, {})
            meas = measured.get(name, {})
            crumb = pred.get("breadcrumb") or meas.get("breadcrumb") or name
            pred_nodes = pred.get("nodes")
            meas_nodes = meas.get("nodes")
            if (meas_nodes is not None and pred_nodes is not None
                    and meas_nodes != pred_nodes):
                lines.append(
                    f"  {name}: {crumb} ({meas_nodes} nodes, "
                    f"predicted {pred_nodes})")
            else:
                nodes = meas_nodes if meas_nodes is not None else pred_nodes
                lines.append(f"  {name}: {crumb} ({nodes} nodes)")
    render = report.get("render") or {}
    if render:
        lines.append("Render:")
        lines.append(f"  engine: {render.get('engine', '-')}")
        lines.append(f"  resolution: {render.get('resolution_px', '-')} px")
        lines.append(f"  samples: {render.get('samples', '-')}")
        lines.append(f"  denoise: {'on' if render.get('denoise') else 'off'}")
    critique = report.get("critique") or {}
    if critique:
        lines.append("Critic:")
        if critique.get("note"):
            lines.append(f"  {critique['note']}")
        images = critique.get("images") or []
        if images:
            lines.append(f"  renders judged: {', '.join(images)}")
        # The critic caps at 8 images (v1.10, N42): say which renders
        # were left out, so the report never implies they were judged.
        skipped = critique.get("skipped_images") or []
        if skipped:
            lines.append(f"  renders not judged ({len(skipped)}): "
                         f"{', '.join(skipped)}")
        fixes = critique.get("fixes") or []
        if fixes:
            lines.append(f"  fixes ({len(fixes)}):")
            for fix in fixes[:10]:
                part = fix.get("part") or "whole build"
                lines.append(f"  - {part}: {fix.get('issue', '')}")
                if fix.get("fix"):
                    lines.append(f"    change: {fix['fix']}")
        elif critique.get("vision"):
            lines.append("  no fixes: the renders satisfied the rubric")
    problems = report.get("problems") or []
    if problems:
        lines.append("Problems:")
        lines.extend(f"  - {problem}" for problem in problems)
    return "\n".join(lines) + "\n"


# ----------------------------------------------------- description dialog

class DescriptionDialog(QtWidgets.QDialog):
    """The full description, opened on purpose (v1.14, A3/A14): the
    job card shows the plain summary; this is where the parts,
    numbers, constraints and budget live."""

    def __init__(self, workspace: Path, job_name: str, parent=None):
        super().__init__(parent)
        from . import description as desc_mod

        self.setWindowTitle(f"SKULLPT - Details for {job_name}")
        self.setMinimumSize(520, 380)
        layout = QtWidgets.QVBoxLayout(self)
        self._text = QtWidgets.QTextEdit()
        self._text.setObjectName("mono")
        self._text.setReadOnly(True)
        try:
            desc = Job(Path(workspace) / "jobs" / job_name).load_description()
            self._text.setPlainText(desc_mod.summarize(desc))
        except Exception as exc:
            self._text.setPlainText(
                f"Could not read this job's description: {exc}")
        layout.addWidget(self._text, 1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
