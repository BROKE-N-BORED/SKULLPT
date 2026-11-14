"""The Bench's windows (R5): Render, Images, Library, Settings, Help,
plus the shared pieces (stage pills, job cards, toasts, debug report).

Each window does one job (U1). Every window remembers its geometry
(U5). No em dashes anywhere in this file (U33); visible labels use
"and", never a bare "&" (U11).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import bench as bench_mod
from . import imagegen
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

    QT_AVAILABLE = True
except Exception:  # PySide6 missing - the CLI reports it plainly
    QT_AVAILABLE = False


# ------------------------------------------------------------ geometry (T13)

def _settings() -> "QtCore.QSettings":
    return QtCore.QSettings("BrokeNBored", "SKULLPT")


def restore_geometry(window, key: str) -> None:
    geometry = _settings().value(f"{key}/geometry")
    if geometry is not None:
        window.restoreGeometry(geometry)


def save_geometry(window, key: str) -> None:
    _settings().setValue(f"{key}/geometry", window.saveGeometry())


class _GeometryWindow(QtWidgets.QMainWindow):
    """A window that remembers its size and position (U5)."""

    GEOMETRY_KEY = "Window"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._geometry_key = self.GEOMETRY_KEY

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        save_geometry(self, self._geometry_key)
        super().closeEvent(event)


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

        self._summary = QtWidgets.QLabel(job.get("summary") or "")
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
        self._summary.setText(job.get("summary") or "")
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
        self._desc: dict | None = None
        self._selected_part: str | None = None
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

        right = QtWidgets.QVBoxLayout()
        parts_title = QtWidgets.QLabel("Parts")
        parts_title.setObjectName("heading")
        right.addWidget(parts_title)
        self._parts_list = QtWidgets.QListWidget()
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
                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(-10000.0, 10000.0)
                spin.setDecimals(1)
                spin.setSingleStep(1.0)
                spins.append(spin)
                row.addWidget(spin)
            form.addRow(label, row)
        self._material_combo = QtWidgets.QComboBox()
        self._material_combo.addItems(sorted(MATERIALS))
        form.addRow("Material", self._material_combo)
        self._bevel_spin = QtWidgets.QDoubleSpinBox()
        self._bevel_spin.setRange(0.0, 100.0)
        self._bevel_spin.setDecimals(1)
        form.addRow("Bevel (cm)", self._bevel_spin)
        self._radius_spin = QtWidgets.QDoubleSpinBox()
        self._radius_spin.setRange(0.0, 1000.0)
        self._radius_spin.setDecimals(1)
        form.addRow("Radius (cm)", self._radius_spin)
        right.addLayout(form)
        self._save_part_btn = QtWidgets.QPushButton("Save part changes")
        self._save_part_btn.setObjectName("primary")
        self._save_part_btn.setEnabled(False)
        self._save_part_btn.clicked.connect(self._save_part)
        right.addWidget(self._save_part_btn)
        right.addWidget(QtWidgets.QLabel(
            "Saving sends the job back for approval."))
        renders_title = QtWidgets.QLabel("Renders")
        renders_title.setObjectName("heading")
        right.addWidget(renders_title)
        self._renders_row = QtWidgets.QHBoxLayout()
        right.addLayout(self._renders_row)
        render_title = QtWidgets.QLabel("Render settings")
        render_title.setObjectName("heading")
        right.addWidget(render_title)
        render_form = QtWidgets.QFormLayout()
        self._engine_combo = QtWidgets.QComboBox()
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
        right.addLayout(render_form)
        self._save_render_btn = QtWidgets.QPushButton("Save render settings")
        self._save_render_btn.setEnabled(False)
        self._save_render_btn.clicked.connect(self._save_render_settings)
        right.addWidget(self._save_render_btn)
        right.addWidget(QtWidgets.QLabel(
            "These are this job's own settings. Changing them does not "
            "send the job back for approval."))
        right.addStretch(1)
        main.addLayout(right, 1)

        self._update_legend()
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
        self._job_name = job_name
        self._desc = None
        self._selected_part = None
        self._viewport.set_selected_part(None)
        self._parts_list.clear()
        self._save_part_btn.setEnabled(False)
        self._save_render_btn.setEnabled(False)
        self._editor_note.setText("Select a part to edit it.")
        self._clear_renders()
        while self._camera_buttons.count():
            item = self._camera_buttons.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if job_name is None:
            self._viewport.set_scene(None)
            self._update_legend()
            self._tri_label.setText("Triangles: -")
            return
        try:
            job = Job(self._workspace / "jobs" / job_name)
            self._desc = job.load_description()
        except Exception as exc:
            self._viewport.set_scene(None)
            self._legend.setText(f"Could not read this job: {exc}")
            self._tri_label.setText("Triangles: -")
            return
        self._viewport.set_scene(self._desc)
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

    def _update_legend(self) -> None:
        step = self._viewport.grid_step()
        self._legend.setText(
            f"Grid: {step:.1f} cm squares. X red, Y green, Z blue. "
            "Drag to orbit, right-drag to pan, scroll to zoom. "
            "Click a part to select it.")

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
            self._renders_row.addWidget(label)

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
        self.set_job(self._job_name)
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
        base_id = _base_part_id(part_id, self._desc)
        part = next((p for p in self._desc.get("parts", [])
                     if p.get("id") == base_id), None)
        if part is None:
            self._save_part_btn.setEnabled(False)
            self._editor_note.setText("Could not find this part to edit.")
            return
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
        index = self._material_combo.findText(material) if material else -1
        self._material_combo.setCurrentIndex(max(0, index))
        self._bevel_spin.setValue(float(part.get("bevel_cm") or 0.0))
        self._radius_spin.setValue(float(part.get("radius_cm") or 0.0))
        self._save_part_btn.setEnabled(True)

    def _save_part(self) -> None:
        if self._job_name is None or self._selected_part is None \
                or self._desc is None:
            return
        base_id = _base_part_id(self._selected_part, self._desc)
        fields = {
            "position_cm": [s.value() for s in self._pos_spins],
            "size_cm": [s.value() for s in self._size_spins],
            "material": self._material_combo.currentText(),
            "bevel_cm": self._bevel_spin.value(),
            "radius_cm": self._radius_spin.value(),
        }
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

    def _load_render_settings(self) -> None:
        """Fill the render controls with this job's effective settings
        (its own block, else the workspace defaults)."""
        if self._desc is None:
            return
        resolved = settings_mod.resolve_render_settings(self._workspace, self._desc)
        index = self._engine_combo.findData(resolved["engine"])
        self._engine_combo.setCurrentIndex(max(0, index))
        self._res_spin.setValue(resolved["resolution_px"])
        self._samples_spin.setValue(resolved["samples"])
        self._denoise_check.setChecked(resolved["denoise"])
        self._save_render_btn.setEnabled(True)

    def _save_render_settings(self) -> None:
        if self._job_name is None:
            return
        result = self._bench.do_action("render-settings", {
            "job": self._job_name,
            "engine": self._engine_combo.currentData(),
            "resolution_px": self._res_spin.value(),
            "samples": self._samples_spin.value(),
            "denoise": self._denoise_check.isChecked()})
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "Could not save",
                "The render settings did not save. The chat panel on "
                "the Jobs window says why.")
            return
        Toast.show_toast(self, "Render settings saved for this job.")


# ------------------------------------------------------------ images window

class _CandidateCard(QtWidgets.QFrame):
    """One generated picture: the image, its state, Keep / Reject, and
    Vary (variations of a kept picture, v1.6 T27)."""

    decided = QtCore.Signal(str, str)  # filename, decision
    vary = QtCore.Signal(str)  # filename

    def __init__(self, batch_dir: Path, candidate: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
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
        buttons = QtWidgets.QHBoxLayout()
        keep_btn = QtWidgets.QPushButton("Keep")
        keep_btn.setObjectName("primary")
        keep_btn.clicked.connect(lambda: self.decided.emit(self._file, "keep"))
        buttons.addWidget(keep_btn)
        reject_btn = QtWidgets.QPushButton("Reject")
        reject_btn.clicked.connect(
            lambda: self.decided.emit(self._file, "reject"))
        buttons.addWidget(reject_btn)
        layout.addLayout(buttons)
        vary_btn = QtWidgets.QPushButton("Vary this picture")
        vary_btn.setEnabled(status == "kept")
        vary_btn.setToolTip("Make variations of this kept picture "
                            "into a new batch.")
        vary_btn.clicked.connect(lambda: self.vary.emit(self._file))
        layout.addWidget(vary_btn)


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
        for batch in state.get("batches", []):
            item = QtWidgets.QListWidgetItem(
                f"{batch['name']} ({batch.get('kept', 0)} kept, "
                f"{batch.get('pending', 0)} waiting)")
            item.setData(QtCore.Qt.UserRole, batch["name"])
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
        self._prompt_label.setText("Pick a batch to see its pictures.")
        self._clear_grid()

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

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
        for candidate in batch.get("candidates", []):
            if candidate.get("status") == "rejected":
                continue
            card = _CandidateCard(self._batch_dir, candidate)
            card.decided.connect(self._decide)
            card.vary.connect(self._vary)
            self._grid.addWidget(card, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

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
            QtWidgets.QMessageBox.warning(self, "Could not decide", str(exc))
            return
        Toast.show_toast(self, f"{filename} {decision}ed." if decision == "keep"
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
        self._detail.setText(
            f"{manifest.get('name', slug)}\n"
            f"Kind: {manifest.get('kind', '-')}\n"
            f"Filed: {manifest.get('filed', '-')}\n"
            f"Files: {', '.join(files) if files else 'none'}\n"
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
        job = Job.create(self._workspace / "jobs", desc)
        self._bench.refresh()
        self._bench.select_job(job.dir.name)
        Toast.show_toast(self, f"Started {job.dir.name} from the library entry.")


# ---------------------------------------------------------- settings window

_ENGINE_LABELS = {
    "blender": "Blender (builds the 3D files)",
    "image_engine": "Image engine (draws reference pictures)",
    "image_model": "Image model (the picture model file)",
    "gemini_key": "Gemini key (planner and critic seats)",
    "hf_token": "Hugging Face token (photo to mesh)",
}

_ENGINE_FIXES = {
    "blender": "Install Blender 5.2 LTS or newer, with the blender "
               "program installed where SKULLPT can find it.",
    "image_engine": imagegen.engine_missing_text(),
    "image_model": imagegen.model_missing_text(),
    "gemini_key": "Set the GEMINI_API_KEY environment variable. "
                  "Without it the planner and critic seats cannot answer.",
    "hf_token": "Save a Hugging Face token below, or set the HF_TOKEN "
                "environment variable, to turn photos into meshes.",
}


class SettingsWindow(_GeometryWindow):
    """Engines, switches, and the Hugging Face token (T9). The token
    is stored in a local file the repo never sees, and this window
    never shows the token back - only whether one is saved."""

    GEOMETRY_KEY = "SettingsWindow"

    def __init__(self, bench, workspace: Path, parent=None):
        super().__init__(parent)
        self._bench = bench
        self._workspace = Path(workspace)
        self.setWindowTitle("SKULLPT - Settings")
        self.setMinimumSize(520, 420)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main = QtWidgets.QVBoxLayout(central)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(12)

        engines_title = QtWidgets.QLabel("Engines")
        engines_title.setObjectName("heading")
        main.addWidget(engines_title)
        self._engine_rows = QtWidgets.QFormLayout()
        self._engine_labels: dict[str, QtWidgets.QLabel] = {}
        for key, label in _ENGINE_LABELS.items():
            value = QtWidgets.QLabel()
            value.setWordWrap(True)
            self._engine_labels[key] = value
            self._engine_rows.addRow(label, value)
        main.addLayout(self._engine_rows)

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
            "never sees. SKULLPT never shows it back.")
        token_note.setWordWrap(True)
        token_note.setObjectName("muted")
        main.addWidget(token_note)
        token_row = QtWidgets.QHBoxLayout()
        self._token_field = QtWidgets.QLineEdit()
        self._token_field.setEchoMode(QtWidgets.QLineEdit.Password)
        self._token_field.setPlaceholderText("Paste your Hugging Face token")
        token_row.addWidget(self._token_field, 1)
        save_btn = QtWidgets.QPushButton("Save token")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_token)
        token_row.addWidget(save_btn)
        clear_btn = QtWidgets.QPushButton("Clear token")
        clear_btn.clicked.connect(self._clear_token)
        token_row.addWidget(clear_btn)
        main.addLayout(token_row)
        self._token_status = QtWidgets.QLabel()
        self._token_status.setObjectName("muted")
        main.addWidget(self._token_status)

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
            "Back up the whole workspace into one zip file. The Hugging "
            "Face token is never in a backup."))
        backup_btn = QtWidgets.QPushButton("Back up workspace")
        backup_btn.clicked.connect(self._backup_workspace)
        main.addWidget(backup_btn)
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
                       self._samples_spin, self._denoise_check):
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
        finally:
            for widget in (self._engine_combo, self._res_spin,
                           self._samples_spin, self._denoise_check):
                widget.blockSignals(False)

    def _save_render_settings(self) -> None:
        settings_mod.save_render_settings(self._workspace, {
            "engine": self._engine_combo.currentData(),
            "resolution_px": self._res_spin.value(),
            "samples": self._samples_spin.value(),
            "denoise": self._denoise_check.isChecked()})

    def _backup_workspace(self) -> None:
        from .backup import backup_workspace

        default = str(Path(self._workspace).parent
                      / f"{self._workspace.name}-backup.zip")
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Back up workspace", default, "Zip files (*.zip)")
        if not dest:
            return
        try:
            result = backup_workspace(self._workspace, dest)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Backup failed", str(exc))
            return
        Toast.show_toast(self, result["note"])

    def refresh(self) -> None:
        """Re-read the engine status and the token state."""
        status = bench_mod.engine_status(self._workspace)
        for key, label in self._engine_labels.items():
            ready = status.get(key, False)
            if ready:
                label.setText("Ready")
            else:
                label.setText(f"Missing. {_ENGINE_FIXES[key]}")
        if tokenfile.has_token(self._workspace):
            self._token_status.setText("Token saved.")
        elif bench_mod.engine_status(self._workspace).get("hf_token"):
            self._token_status.setText("Token set in the environment.")
        else:
            self._token_status.setText("No token saved.")
        last = settings_mod.last_render_engine(self._workspace)
        if last.get("engine"):
            self._last_engine_label.setText(
                "Last render engine that worked here: "
                f"{last['engine']}"
                + (f" ({last['at']})" if last.get("at") else ""))
        else:
            self._last_engine_label.setText(
                "No render has run yet, so the working engine is not known.")

    def _save_critic_toggle(self, checked: bool) -> None:
        current = settings_mod.load_settings(self._workspace)
        current["critic_reference_check"] = checked
        settings_mod.save_settings(self._workspace, current)

    def _save_token(self) -> None:
        token = self._token_field.text().strip()
        if not token:
            QtWidgets.QMessageBox.warning(
                self, "No token", "Paste a token first.")
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
can orbit, and lets you pick a part and edit it. <b>Images</b> is
where you keep or reject generated pictures. <b>Library</b> holds
everything you have filed. <b>Settings</b> shows which engines are
ready and holds the Hugging Face token.</p>
<h3>The five steps</h3>
<ol>
<li><b>Make.</b> Type "make a wooden shop panel" in the chat, or open
the Render window and start from a description file.</li>
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
resolution, Cycles samples, and denoise. A job can set its own in the
Render window. Changing render settings never sends a job back for
approval.</p>
<h3>Builds in a queue</h3>
<p>Only one heavy job runs at a time. If you start a build while
another is running, yours waits in the queue and starts when its turn
comes. Queued builds can be cancelled from the status bar.</p>
<h3>Unreal</h3>
<p>A built job can be packaged for Unreal: open the job, press
Unreal package, and pick a folder. The package has the .fbx, the
textures, and an import script.</p>
<h3>Blender</h3>
<p>Building needs Blender 5.2 LTS or newer installed. The renders
and the game-ready model come from Blender; without it, SKULLPT can
still draft descriptions and show the viewport.</p>
<h3>Your workspace</h3>
<p>Everything lives in your workspace folder: jobs, the library,
image batches, and settings. The folder is shown in Settings. File,
then Back up workspace, to zip the whole workspace into one file
(the Hugging Face token is never in a backup). File, then Open
workspace, switches to another folder right away. Help, then Check
for updates, asks GitHub whether a newer SKULLPT is out; it never
downloads anything by itself.</p>
<h3>About</h3>
<p>SKULLPT v1.6. A standalone Linux design and asset shell, by
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
        browser = QtWidgets.QTextBrowser()
        browser.setHtml(_HELP_HTML)
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
        if path:
            Path(path).write_text(self._text.toPlainText())
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
