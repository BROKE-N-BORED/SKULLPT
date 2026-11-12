"""The Bench as a native Linux desktop window (Qt).

Joshua's standing requirement: this is FOR LINUX — a real desktop window,
not a web page. (The web Bench in bench.py was Arlo's choice, never his;
v1.0 replaces it as the default. The web version remains as `webbench`.)

One window: LEFT project folders (Jobs / Library / image batches / intake),
CENTER the Live Render Window — a real OpenGL viewport drawing the selected
job's description parts as true-scale 3D shapes, with a free inspection
camera (drag to orbit, right-drag to pan, scroll to zoom) plus named scene
cameras saved into the Description File itself — RIGHT the chat panel.

The viewport tessellates the same shapes the Blender exporter builds
(box / cylinder / sphere / extrude / lathe / tube) from the same normalized
scene data (viewer.scene_data), so what you see is what builds. No web tech
anywhere in this module: no http.server, no HTML, no browser.

Needs PySide6 (already on Joshua's PC — Code Forge runs there). Without it
the CLI says so plainly instead of crashing.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from . import bench as bench_mod
from . import description as desc_mod
from .blender_export import resolve_material
from .expand import expand_parts
from .jobs import Job
from .viewer import scene_data, upsert_camera

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtOpenGL import QOpenGLFunctions_2_1
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    QT_AVAILABLE = True
    QT_ERROR: Exception | None = None
except Exception as exc:  # PySide6 missing — the CLI reports it plainly
    QT_AVAILABLE = False
    QT_ERROR = exc


# ------------------------------------------------------------------ geometry
# Pure functions (no Qt): tessellate a description's parts into triangles,
# in cm, z-up, mirroring blender_export's builders exactly.

def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _tri_normal(a, b, c) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-9:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)


def _quad(out, a, b, c, d) -> None:
    out.append((a, b, c))
    out.append((a, c, d))


def _box_tris(mn, sz) -> list:
    x0, y0, z0 = mn
    x1, y1, z1 = mn[0] + sz[0], mn[1] + sz[1], mn[2] + sz[2]
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    tris: list = []
    _quad(tris, v[0], v[3], v[2], v[1])  # bottom
    _quad(tris, v[4], v[5], v[6], v[7])  # top
    _quad(tris, v[0], v[1], v[5], v[4])  # front (y0)
    _quad(tris, v[2], v[3], v[7], v[6])  # back (y1)
    _quad(tris, v[1], v[2], v[6], v[5])  # right (x1)
    _quad(tris, v[3], v[0], v[4], v[7])  # left (x0)
    return tris


def _cylinder_tris(mn, sz, segments: int = 24) -> list:
    cx, cy = mn[0] + sz[0] / 2, mn[1] + sz[1] / 2
    rx, ry = sz[0] / 2, sz[1] / 2
    z0, z1 = mn[2], mn[2] + sz[2]
    ring0, ring1 = [], []
    for s in range(segments):
        t = 2 * math.pi * s / segments
        ring0.append((cx + rx * math.cos(t), cy + ry * math.sin(t), z0))
        ring1.append((cx + rx * math.cos(t), cy + ry * math.sin(t), z1))
    tris: list = []
    for s in range(segments):
        j = (s + 1) % segments
        _quad(tris, ring0[s], ring0[j], ring1[j], ring1[s])
    top_c, bot_c = (cx, cy, z1), (cx, cy, z0)
    for s in range(segments):
        j = (s + 1) % segments
        tris.append((top_c, ring1[s], ring1[j]))
        tris.append((bot_c, ring0[j], ring0[s]))
    return tris


def _sphere_tris(mn, sz, segments: int = 24, rings: int = 12) -> list:
    cx, cy, cz = mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2] + sz[2] / 2
    rx, ry, rz = sz[0] / 2, sz[1] / 2, sz[2] / 2
    pts = []
    for r in range(rings + 1):
        phi = math.pi * r / rings  # 0 = south pole, pi = north pole
        for s in range(segments):
            theta = 2 * math.pi * s / segments
            pts.append((cx + rx * math.sin(phi) * math.cos(theta),
                        cy + ry * math.sin(phi) * math.sin(theta),
                        cz - rz * math.cos(phi)))
    tris: list = []
    for r in range(rings):
        for s in range(segments):
            j = (s + 1) % segments
            a, b = r * segments + s, r * segments + j
            c, d = (r + 1) * segments + j, (r + 1) * segments + s
            _quad(tris, pts[a], pts[b], pts[c], pts[d])
    return tris


def _extrude_tris(mn, sz, profile) -> list:
    # Fan triangulation: exact for convex profiles; concave profiles render
    # approximated (the Blender build is the authority, this is a preview).
    pts = [(mn[0] + float(p[0]), mn[1] + float(p[1])) for p in profile]
    n = len(pts)
    z0, z1 = mn[2], mn[2] + sz[2]
    bot = [(x, y, z0) for x, y in pts]
    top = [(x, y, z1) for x, y in pts]
    tris: list = []
    for i in range(1, n - 1):
        tris.append((bot[0], bot[i + 1], bot[i]))
        tris.append((top[0], top[i], top[i + 1]))
    for i in range(n):
        j = (i + 1) % n
        _quad(tris, bot[i], bot[j], top[j], top[i])
    return tris


def _lathe_tris(mn, sz, profile, segments: int = 20) -> list:
    cx, cy, z0 = mn[0] + sz[0] / 2, mn[1] + sz[1] / 2, mn[2]
    prof = [(float(p[0]), float(p[1])) for p in profile]
    rings = len(prof)
    verts = []
    for r, pz in prof:
        for s in range(segments):
            t = 2 * math.pi * s / segments
            verts.append((cx + r * math.cos(t), cy + r * math.sin(t), z0 + pz))
    tris: list = []
    for ri in range(rings - 1):
        for s in range(segments):
            j = (s + 1) % segments
            a, b = ri * segments + s, ri * segments + j
            c, d = (ri + 1) * segments + j, (ri + 1) * segments + s
            _quad(tris, verts[a], verts[b], verts[c], verts[d])
    if prof[0][0] > 0.001:
        cap = (cx, cy, z0 + prof[0][1])
        for s in range(segments):
            tris.append((cap, verts[(s + 1) % segments], verts[s]))
    if prof[-1][0] > 0.001:
        base = (rings - 1) * segments
        cap = (cx, cy, z0 + prof[-1][1])
        for s in range(segments):
            tris.append((cap, verts[base + s], verts[base + (s + 1) % segments]))
    return tris


def _tube_tris(path, radius: float, segments: int = 10) -> list:
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in path]
    n = len(pts)

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def norm(a):
        length = math.sqrt(sum(v * v for v in a)) or 1.0
        return (a[0] / length, a[1] / length, a[2] / length)

    rings = []
    for i, p in enumerate(pts):
        p0, p1 = pts[max(0, i - 1)], pts[min(n - 1, i + 1)]
        d = norm(sub(p1, p0))
        up = (1.0, 0.0, 0.0) if abs(d[2]) > 0.9 else (0.0, 0.0, 1.0)
        side = norm(cross(d, up))
        upv = cross(side, d)
        ring = []
        for s in range(segments):
            t = 2 * math.pi * s / segments
            ring.append(tuple(p[k] + radius * (math.cos(t) * side[k]
                                               + math.sin(t) * upv[k])
                              for k in range(3)))
        rings.append(ring)
    tris: list = []
    for i in range(n - 1):
        for s in range(segments):
            j = (s + 1) % segments
            _quad(tris, rings[i][s], rings[i][j], rings[i + 1][j], rings[i + 1][s])
    return tris


def mesh_for_box(box: dict, src: dict) -> dict:
    """Triangles + color for one normalized scene box."""
    mn, sz = box["min"], box["size"]
    shape = box.get("shape", "box")
    if shape == "cylinder":
        tris = _cylinder_tris(mn, sz)
    elif shape == "sphere":
        tris = _sphere_tris(mn, sz)
    elif shape == "extrude" and box.get("profile"):
        tris = _extrude_tris(mn, sz, box["profile"])
    elif shape == "lathe" and box.get("profile"):
        tris = _lathe_tris(mn, sz, box["profile"])
    elif shape == "tube" and box.get("path"):
        tris = _tube_tris(box["path"], float(box.get("radius") or 2))
    else:
        tris = _box_tris(mn, sz)
    color, _, _ = resolve_material(src)
    return {
        "id": box["id"], "type": box.get("type", "part"),
        "tris": tris, "color": hex_to_rgb(color),
        "glass": src.get("material") == "glass",
        "glow": box.get("type") in ("lamp", "light", "lantern"),
    }


def scene_meshes(desc: dict) -> dict:
    """Everything the viewport needs: meshes, bounds, cameras."""
    data = scene_data(desc)
    src_by_id = {p["id"]: p for p in expand_parts(desc)}
    meshes = [mesh_for_box(box, src_by_id.get(box["id"], {}))
              for box in data["boxes"]]
    return {
        "name": data["name"], "kind": data.get("kind", ""),
        "meshes": meshes, "bounds": data["bounds"],
        "cameras": data["cameras"],
    }


# ------------------------------------------------------------- live viewport
# Only defined when Qt is importable, so the geometry above (and the CLI's
# other commands) keep working on a machine without PySide6.

if QT_AVAILABLE:

    class LiveRenderWidget(QOpenGLWidget):
        """A real OpenGL viewport: the description's parts as 3D shapes,
        free orbit / pan / zoom camera, grid + axes. No web tech."""

        def __init__(self, parent=None):
            super().__init__(parent)
            fmt = QtGui.QSurfaceFormat()
            fmt.setVersion(2, 1)
            fmt.setProfile(QtGui.QSurfaceFormat.CompatibilityProfile)
            fmt.setDepthBufferSize(24)
            fmt.setSamples(4)
            self.setFormat(fmt)
            self.setMinimumSize(320, 240)
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            self._scene: dict | None = None
            self._gl: QOpenGLFunctions_2_1 | None = None
            # Orbit camera: target + distance + azimuth/elevation (degrees).
            self.target = (0.0, 0.0, 0.0)
            self.distance = 200.0
            self.azimuth = -126.0
            self.elevation = 29.0
            self._drag_button: QtCore.Qt.MouseButton | None = None
            self._drag_pos: QtCore.QPoint | None = None

        # -- scene ------------------------------------------------------
        def set_scene(self, desc: dict | None) -> None:
            self._scene = scene_meshes(desc) if desc else None
            self.frame()
            self.update()

        def span(self) -> float:
            if self._scene:
                return max(float(self._scene["bounds"]["span"]), 1.0)
            return 100.0

        def frame(self) -> None:
            """Fit the camera to the whole scene (the iso view)."""
            if self._scene:
                c = self._scene["bounds"]["center"]
                self.target = (float(c[0]), float(c[1]), float(c[2]))
            else:
                self.target = (0.0, 0.0, 0.0)
            self.distance = self.span() * 1.6
            self.azimuth = -126.0
            self.elevation = 29.0
            self.update()

        # -- camera math -------------------------------------------------
        def camera_position(self) -> tuple[float, float, float]:
            az, el = math.radians(self.azimuth), math.radians(self.elevation)
            d = self.distance
            return (self.target[0] + d * math.cos(el) * math.cos(az),
                    self.target[1] + d * math.cos(el) * math.sin(az),
                    self.target[2] + d * math.sin(el))

        def set_camera_position(self, position, look_at) -> None:
            self.target = (float(look_at[0]), float(look_at[1]), float(look_at[2]))
            dx = float(position[0]) - self.target[0]
            dy = float(position[1]) - self.target[1]
            dz = float(position[2]) - self.target[2]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist < 1e-6:
                return
            self.distance = dist
            self.elevation = math.degrees(math.asin(max(-1.0, min(1.0, dz / dist))))
            self.azimuth = math.degrees(math.atan2(dy, dx))
            self.update()

        def current_camera(self, name: str) -> dict:
            eye = self.camera_position()
            return {"name": name,
                    "position_cm": [round(v, 2) for v in eye],
                    "look_at_cm": [round(v, 2) for v in self.target]}

        def _view_dir(self):
            eye = self.camera_position()
            dx = self.target[0] - eye[0]
            dy = self.target[1] - eye[1]
            dz = self.target[2] - eye[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            return (dx / length, dy / length, dz / length)

        def _pan(self, dx_pixels: int, dy_pixels: int) -> None:
            vx, vy, vz = self._view_dir()
            # Camera right = view dir x world up (z); camera up = right x dir.
            rx, ry, rz = vy * 1.0 - vz * 0.0, vz * 0.0 - vx * 1.0, 0.0
            # (cross of view dir with (0,0,1)): (vy*1 - vz*0, vz*0 - vx*1, 0)
            rlen = math.sqrt(rx * rx + ry * ry) or 1.0
            rx, ry = rx / rlen, ry / rlen
            ux, uy, uz = ry * vz - 0.0 * vy, 0.0 * vx - rx * vz, rx * vy - ry * vx
            ulen = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
            ux, uy, uz = ux / ulen, uy / ulen, uz / ulen
            scale = self.distance * 0.0012
            self.target = (self.target[0] - rx * dx_pixels * scale
                           + ux * dy_pixels * scale,
                           self.target[1] - ry * dx_pixels * scale
                           + uy * dy_pixels * scale,
                           self.target[2] + uz * dy_pixels * scale)
            self.update()

        # -- GL ----------------------------------------------------------
        def initializeGL(self) -> None:
            self._gl = QOpenGLFunctions_2_1()
            self._gl.initializeOpenGLFunctions()
            gl = self._gl
            gl.glEnable(0x0B71)  # GL_DEPTH_TEST
            gl.glEnable(0x0B50)  # GL_LIGHTING
            gl.glEnable(0x4000)  # GL_LIGHT0
            gl.glEnable(0x0B57)  # GL_COLOR_MATERIAL
            gl.glLightModelfv(0x0B53, (0.35, 0.35, 0.35, 1.0))  # MODEL_AMBIENT
            gl.glLightModelf(0x0B52, 1.0)  # LIGHT_MODEL_TWO_SIDE
            gl.glLightfv(0x4000, 0x1201, (0.9, 0.9, 0.9, 1.0))  # DIFFUSE
            gl.glLightfv(0x4000, 0x1200, (0.15, 0.15, 0.15, 1.0))  # AMBIENT
            gl.glClearColor(0.09, 0.09, 0.10, 1.0)

        def resizeGL(self, width: int, height: int) -> None:
            if self._gl is not None:
                self._gl.glViewport(0, 0, max(1, width), max(1, height))

        def paintGL(self) -> None:
            gl = self._gl
            if gl is None:
                return
            gl.glClear(0x00004000 | 0x00000100)  # COLOR + DEPTH
            width, height = max(1, self.width()), max(1, self.height())
            eye = self.camera_position()
            proj = QtGui.QMatrix4x4()
            proj.perspective(45.0, width / height, max(0.1, self.distance / 100.0),
                             self.distance * 20.0 + self.span() * 10.0)
            view = QtGui.QMatrix4x4()
            view.lookAt(QtGui.QVector3D(*eye),
                        QtGui.QVector3D(*self.target),
                        QtGui.QVector3D(0.0, 0.0, 1.0))
            gl.glMatrixMode(0x1701)  # PROJECTION
            gl.glLoadMatrixf(list(proj.data()))
            gl.glMatrixMode(0x1700)  # MODELVIEW
            gl.glLoadMatrixf(list(view.data()))
            # Directional light from upper-left-front, in eye space.
            gl.glLightfv(0x4000, 0x1203, (-0.5, 0.7, 1.0, 0.0))  # POSITION
            self._draw_grid(gl)
            if self._scene:
                self._draw_meshes(gl, self._scene["meshes"])

        def _draw_grid(self, gl) -> None:
            span = self.span()
            step = span / 10.0
            half = span * 1.2
            z = 0.0
            if self._scene:
                z = min(0.0, float(self._scene["bounds"]["min"][2]))
            gl.glDisable(0x0B50)  # LIGHTING off for lines
            gl.glColor3f(0.25, 0.25, 0.28)
            gl.glBegin(0x0001)  # LINES
            n = int(half / step)
            for i in range(-n, n + 1):
                gl.glVertex3f(i * step, -half, z)
                gl.glVertex3f(i * step, half, z)
                gl.glVertex3f(-half, i * step, z)
                gl.glVertex3f(half, i * step, z)
            gl.glEnd()
            # Axes: X red, Y green, Z blue.
            gl.glBegin(0x0001)
            gl.glColor3f(0.85, 0.25, 0.25)
            gl.glVertex3f(0.0, 0.0, z)
            gl.glVertex3f(span * 0.6, 0.0, z)
            gl.glColor3f(0.25, 0.75, 0.30)
            gl.glVertex3f(0.0, 0.0, z)
            gl.glVertex3f(0.0, span * 0.6, z)
            gl.glColor3f(0.30, 0.45, 0.9)
            gl.glVertex3f(0.0, 0.0, z)
            gl.glVertex3f(0.0, 0.0, z + span * 0.6)
            gl.glEnd()
            gl.glEnable(0x0B50)

        def _draw_meshes(self, gl, meshes) -> None:
            # Opaque first, glass last (blended, no depth writes).
            for glass_pass in (False, True):
                if glass_pass:
                    gl.glEnable(0x0BE2)  # BLEND
                    gl.glBlendFunc(0x0302, 0x0303)  # SRC_ALPHA, ONE_MINUS_SRC_ALPHA
                    gl.glDepthMask(False)
                for mesh in meshes:
                    if mesh["glass"] != glass_pass:
                        continue
                    r, g, b = mesh["color"]
                    if mesh["glow"]:
                        gl.glMaterialfv(0x0408, 0x1601, (r, g, b, 1.0))  # EMISSION
                    else:
                        gl.glMaterialfv(0x0408, 0x1601, (0.0, 0.0, 0.0, 1.0))
                    gl.glColor4f(r, g, b, 0.45 if glass_pass else 1.0)
                    gl.glBegin(0x0004)  # TRIANGLES
                    for a, bv, c in mesh["tris"]:
                        nx, ny, nz = _tri_normal(a, bv, c)
                        gl.glNormal3f(nx, ny, nz)
                        gl.glVertex3f(*a)
                        gl.glVertex3f(*bv)
                        gl.glVertex3f(*c)
                    gl.glEnd()
                if glass_pass:
                    gl.glDepthMask(True)
                    gl.glDisable(0x0BE2)

        # -- mouse -------------------------------------------------------
        def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
            self._drag_button = event.button()
            self._drag_pos = event.position().toPoint()
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
            if self._drag_pos is None:
                super().mouseMoveEvent(event)
                return
            pos = event.position().toPoint()
            dx, dy = pos.x() - self._drag_pos.x(), pos.y() - self._drag_pos.y()
            self._drag_pos = pos
            if self._drag_button == QtCore.Qt.LeftButton:
                self.azimuth = (self.azimuth - dx * 0.5) % 360.0
                self.elevation = max(-89.0, min(89.0, self.elevation + dy * 0.5))
                self.update()
            elif self._drag_button in (QtCore.Qt.RightButton,
                                       QtCore.Qt.MiddleButton):
                self._pan(dx, dy)
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
            self._drag_button = None
            self._drag_pos = None
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.distance *= 1.1 ** (-steps)
                self.distance = max(self.span() * 0.15,
                                    min(self.span() * 30.0, self.distance))
                self.update()
            super().wheelEvent(event)


    # ------------------------------------------------------------ main window

    class BenchWindow(QtWidgets.QMainWindow):
        """The Bench: folders left, live render view center, chat right."""

        def __init__(self, workspace: str | Path):
            super().__init__()
            self.workspace = Path(workspace)
            self.setWindowTitle("Design Shell — Bench")
            self.resize(1280, 800)
            self._selected: str | None = None
            self._pending_runs: dict[str, str] = {}
            self._build_ui()
            self._refresh()
            self._poll_timer = QtCore.QTimer(self)
            self._poll_timer.timeout.connect(self._poll_runs)
            self._poll_timer.start(1000)

        # -- layout -----------------------------------------------------
        def _build_ui(self) -> None:
            splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            splitter.addWidget(self._build_left())
            splitter.addWidget(self._build_center())
            splitter.addWidget(self._build_right())
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 3)
            splitter.setStretchFactor(2, 1)
            self.setCentralWidget(splitter)
            self.statusBar().showMessage(str(self.workspace))
            quit_action = QtGui.QAction("Quit", self)
            quit_action.setShortcut(QtGui.QKeySequence.Quit)
            quit_action.triggered.connect(self.close)
            file_menu = self.menuBar().addMenu("File")
            file_menu.addAction(quit_action)

        def _build_left(self) -> QtWidgets.QWidget:
            pane = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(pane)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.addWidget(QtWidgets.QLabel("Project folders"))
            self._tree = QtWidgets.QTreeWidget()
            self._tree.setHeaderHidden(True)
            self._tree.itemClicked.connect(self._on_tree_clicked)
            layout.addWidget(self._tree, 1)
            self._engines = QtWidgets.QLabel("")
            self._engines.setWordWrap(True)
            layout.addWidget(self._engines)
            layout.addWidget(QtWidgets.QLabel("New job"))
            self._words = QtWidgets.QLineEdit()
            self._words.setPlaceholderText("Describe what to make…")
            self._words.returnPressed.connect(self._draft_job)
            layout.addWidget(self._words)
            draft_btn = QtWidgets.QPushButton("Draft it")
            draft_btn.clicked.connect(self._draft_job)
            layout.addWidget(draft_btn)
            self._example_combo = QtWidgets.QComboBox()
            layout.addWidget(self._example_combo)
            example_btn = QtWidgets.QPushButton("From example")
            example_btn.clicked.connect(self._job_from_example)
            layout.addWidget(example_btn)
            return pane

        def _build_center(self) -> QtWidgets.QWidget:
            pane = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(pane)
            layout.setContentsMargins(6, 6, 6, 6)
            self._viewport = LiveRenderWidget()
            layout.addWidget(self._viewport, 1)
            cam_row = QtWidgets.QHBoxLayout()
            frame_btn = QtWidgets.QPushButton("Frame")
            frame_btn.clicked.connect(self._viewport.frame)
            cam_row.addWidget(frame_btn)
            self._camera_combo = QtWidgets.QComboBox()
            self._camera_combo.currentIndexChanged.connect(self._on_camera_chosen)
            cam_row.addWidget(self._camera_combo, 1)
            save_cam_btn = QtWidgets.QPushButton("Save camera")
            save_cam_btn.clicked.connect(self._save_camera)
            cam_row.addWidget(save_cam_btn)
            layout.addLayout(cam_row)
            self._job_title = QtWidgets.QLabel("No job selected")
            self._job_title.setWordWrap(True)
            layout.addWidget(self._job_title)
            # The summary is long (parts, constraints, budget, rubric) —
            # a capped scroll box, so it never eats the viewport's space.
            self._job_summary = QtWidgets.QTextEdit("")
            self._job_summary.setReadOnly(True)
            self._job_summary.setMaximumHeight(110)
            layout.addWidget(self._job_summary)
            btn_row = QtWidgets.QHBoxLayout()
            self._approve_btn = QtWidgets.QPushButton("Approve")
            self._approve_btn.clicked.connect(lambda: self._do_action("approve"))
            self._build_btn = QtWidgets.QPushButton("Build")
            self._build_btn.clicked.connect(lambda: self._do_action("build"))
            self._file_btn = QtWidgets.QPushButton("File into Library")
            self._file_btn.clicked.connect(lambda: self._do_action("file"))
            btn_row.addWidget(self._approve_btn)
            btn_row.addWidget(self._build_btn)
            btn_row.addWidget(self._file_btn)
            layout.addLayout(btn_row)
            self._verdict = QtWidgets.QLabel("")
            self._verdict.setWordWrap(True)
            layout.addWidget(self._verdict)
            self._renders = QtWidgets.QListWidget()
            self._renders.setMaximumHeight(90)
            self._renders.itemDoubleClicked.connect(self._open_render)
            layout.addWidget(self._renders)
            self._preview = QtWidgets.QLabel("")
            self._preview.setAlignment(QtCore.Qt.AlignCenter)
            self._preview.setMaximumHeight(180)
            layout.addWidget(self._preview)
            return pane

        def _build_right(self) -> QtWidgets.QWidget:
            pane = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(pane)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.addWidget(QtWidgets.QLabel("Chat"))
            self._chat_log = QtWidgets.QTextEdit()
            self._chat_log.setReadOnly(True)
            layout.addWidget(self._chat_log, 1)
            self._action_row = QtWidgets.QHBoxLayout()
            layout.addLayout(self._action_row)
            row = QtWidgets.QHBoxLayout()
            self._chat_input = QtWidgets.QLineEdit()
            self._chat_input.setPlaceholderText(
                "make … · picture of … · approve · build · file · status")
            self._chat_input.returnPressed.connect(self._send_chat)
            row.addWidget(self._chat_input, 1)
            send_btn = QtWidgets.QPushButton("Send")
            send_btn.clicked.connect(self._send_chat)
            row.addWidget(send_btn)
            layout.addLayout(row)
            return pane

        # -- state ------------------------------------------------------
        def _refresh(self) -> None:
            try:
                state = bench_mod.workspace_state(self.workspace)
            except Exception as exc:
                self.statusBar().showMessage(f"could not read workspace: {exc}")
                return
            engines = state["engines"]
            self._engines.setText(
                "Blender: " + ("ready" if engines["blender"] else "MISSING")
                + "   ·   Image engine: "
                + ("ready" if engines["image_engine"] else "MISSING")
                + "   ·   Image model: "
                + ("ready" if engines["image_model"] else "MISSING")
                + "   ·   Gemini key: "
                + ("set" if engines["gemini_key"] else "not set"))
            self._example_combo.clear()
            self._example_combo.addItems(state["examples"])
            jobs = state["jobs"]
            if self._selected and self._selected not in {j["name"] for j in jobs}:
                self._selected = None
            if self._selected is None and jobs:
                self._selected = jobs[-1]["name"]
            self._rebuild_tree(state)
            self._refresh_job_panel(jobs)
            self._refresh_chat_log()

        def _rebuild_tree(self, state: dict) -> None:
            self._tree.clear()
            jobs_item = QtWidgets.QTreeWidgetItem(self._tree, ["Jobs"])
            for job in state["jobs"]:
                child = QtWidgets.QTreeWidgetItem(
                    jobs_item, [f"{job['name']} ({job['state']})"])
                child.setData(0, QtCore.Qt.UserRole, ("job", job["name"]))
            jobs_item.setExpanded(True)
            lib_item = QtWidgets.QTreeWidgetItem(self._tree, ["Library"])
            for entry in state["library"]:
                QtWidgets.QTreeWidgetItem(lib_item, [entry["slug"]])
            img_item = QtWidgets.QTreeWidgetItem(self._tree, ["Image batches"])
            for batch in state["batches"]:
                QtWidgets.QTreeWidgetItem(img_item, [batch["slug"]])
            intake_item = QtWidgets.QTreeWidgetItem(self._tree, ["Intake"])
            for name in state["folders"]["intake"]:
                QtWidgets.QTreeWidgetItem(intake_item, [name])

        def _refresh_job_panel(self, jobs: list[dict]) -> None:
            job = next((j for j in jobs if j["name"] == self._selected), None)
            self._camera_combo.blockSignals(True)
            self._camera_combo.clear()
            self._camera_combo.addItem("Free view")
            if job is None:
                self._job_title.setText("No job selected")
                self._job_summary.setPlainText("")
                self._verdict.setText("")
                self._renders.clear()
                self._renders.setVisible(False)
                self._preview.clear()
                self._preview.setVisible(False)
                self._approve_btn.setEnabled(False)
                self._build_btn.setEnabled(False)
                self._file_btn.setEnabled(False)
                self._viewport.set_scene(None)
                self._camera_combo.blockSignals(False)
                return
            self._job_title.setText(f"{job['title']} — {job['state']}")
            self._job_summary.setPlainText(job["summary"])
            self._approve_btn.setEnabled(job["state"] == "awaiting_approval")
            self._build_btn.setEnabled(job["state"] == "approved")
            self._file_btn.setEnabled(job["state"] == "awaiting_review")
            report = job.get("report")
            if report:
                verdict = ("build passed" if report.get("ok") else "build failed")
                problems = "; ".join(report.get("problems", [])[:3])
                self._verdict.setText(verdict + (f" — {problems}" if problems else ""))
            else:
                self._verdict.setText("")
            self._renders.clear()
            job_dir = self.workspace / "jobs" / job["name"]
            for name in job["renders"]:
                item = QtWidgets.QListWidgetItem(name)
                item.setData(QtCore.Qt.UserRole, str(job_dir / "output" / name))
                self._renders.addItem(item)
            # No renders yet: hide the list + preview so the viewport
            # gets the space instead of two empty boxes.
            self._renders.setVisible(bool(job["renders"]))
            self._preview.setVisible(bool(job["renders"]))
            self._preview.clear()
            if job["renders"]:
                pix = QtGui.QPixmap(str(job_dir / "output" / job["renders"][0]))
                if not pix.isNull():
                    self._preview.setPixmap(pix.scaledToWidth(
                        320, QtCore.Qt.SmoothTransformation))
            try:
                desc = Job(job_dir).load_description()
            except Exception:
                desc = None
            if desc is not None:
                for cam in desc.get("cameras", []) or []:
                    if isinstance(cam, dict) and cam.get("name"):
                        self._camera_combo.addItem(str(cam["name"]))
                self._viewport.set_scene(desc)
            self._camera_combo.blockSignals(False)

        def _refresh_chat_log(self) -> None:
            history = bench_mod.chat_history(self.workspace)
            lines = []
            for entry in history[-40:]:
                who = "You" if entry.get("role") == "user" else "Bench"
                lines.append(f"{who}: {entry.get('text', '')}")
            self._chat_log.setPlainText("\n\n".join(lines))
            self._chat_log.verticalScrollBar().setValue(
                self._chat_log.verticalScrollBar().maximum())

        # -- interactions ------------------------------------------------
        def _on_tree_clicked(self, item: QtWidgets.QTreeWidgetItem, _col: int) -> None:
            data = item.data(0, QtCore.Qt.UserRole)
            if isinstance(data, tuple) and data[0] == "job":
                self._selected = data[1]
                self._refresh()

        def _log(self, who: str, text: str) -> None:
            history = bench_mod.chat_history(self.workspace)
            history.append({"role": "user" if who == "You" else "bot", "text": text})
            del history[:-60]
            self._refresh_chat_log()

        def _do_action(self, action: str, payload: dict | None = None) -> None:
            job_name = (payload or {}).get("job") or self._selected
            if not job_name and action in ("approve", "build", "file"):
                self._log("Bench", "No job selected.")
                return
            try:
                result = bench_mod.act(self.workspace, action,
                                       {"job": job_name, **(payload or {})})
            except Exception as exc:
                self._log("Bench", str(exc))
                return
            run_id = result.get("run")
            if run_id:
                self._pending_runs[run_id] = f"{action} {job_name}"
                self._log("Bench", f"{action} started for {job_name} — "
                                   "I'll report back when it finishes.")
            elif result.get("detail"):
                self._log("Bench", result["detail"])
            self._refresh()

        def _poll_runs(self) -> None:
            if not self._pending_runs:
                return
            for run in bench_mod._runs():
                label = self._pending_runs.get(run["id"])
                if label is None or run["status"] == "running":
                    continue
                del self._pending_runs[run["id"]]
                if run["status"] == "done":
                    self._log("Bench", f"{label}: done — {run['detail']}")
                else:
                    self._log("Bench", f"{label}: failed — {run['detail']}")
                self._refresh()

        def _draft_job(self) -> None:
            words = self._words.text().strip()
            if not words:
                return
            try:
                result = bench_mod.act(self.workspace, "plan",
                                       {"words": words, "kind": "scene"})
            except Exception as exc:
                self._log("Bench", str(exc))
                return
            detail = result.get("detail", "")
            if ": " in detail:
                self._selected = detail.split(": ")[-1].split(" —")[0]
            self._words.clear()
            self._log("Bench", detail)
            self._refresh()

        def _job_from_example(self) -> None:
            name = self._example_combo.currentText()
            if not name:
                return
            try:
                result = bench_mod.act(self.workspace, "new-example",
                                       {"example": name})
            except Exception as exc:
                self._log("Bench", str(exc))
                return
            detail = result.get("detail", "")
            if ": " in detail:
                self._selected = detail.split(": ")[-1]
            self._log("Bench", detail)
            self._refresh()

        def _send_chat(self) -> None:
            text = self._chat_input.text().strip()
            if not text:
                return
            self._chat_input.clear()
            history = bench_mod.chat_history(self.workspace)
            history.append({"role": "user", "text": text})
            result = bench_mod.chat_reply(self.workspace, text,
                                          selected=self._selected)
            history.append({"role": "bot", "text": result["reply"],
                            "actions": result.get("actions", [])})
            del history[:-60]
            self._refresh_chat_log()
            while self._action_row.count():
                item = self._action_row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for action in result.get("actions", []):
                btn = QtWidgets.QPushButton(action["label"])
                btn.clicked.connect(
                    lambda _checked=False, a=action: self._do_action(
                        a["action"], a["payload"]))
                self._action_row.addWidget(btn)
            self._refresh()

        def _on_camera_chosen(self, index: int) -> None:
            if index <= 0 or self._selected is None:
                return
            name = self._camera_combo.currentText()
            try:
                desc = Job(self.workspace / "jobs" / self._selected).load_description()
            except Exception:
                return
            for cam in desc.get("cameras", []) or []:
                if isinstance(cam, dict) and cam.get("name") == name:
                    self._viewport.set_camera_position(cam["position_cm"],
                                                       cam["look_at_cm"])
                    return

        def _save_camera(self) -> None:
            if self._selected is None:
                self._log("Bench", "Select a job first — cameras live in its "
                                   "description.")
                return
            name, ok = QtWidgets.QInputDialog.getText(
                self, "Save camera", "Camera name:")
            if not ok or not name.strip():
                return
            try:
                job = Job(self.workspace / "jobs" / self._selected)
                desc = job.load_description()
                upsert_camera(desc, self._viewport.current_camera(name.strip()))
                desc_mod.dump(desc, job.desc_path)
            except Exception as exc:
                self._log("Bench", str(exc))
                return
            self._log("Bench", f"Camera '{name.strip()}' saved into the job.")
            self._refresh()

        def _open_render(self, item: QtWidgets.QListWidgetItem) -> None:
            path = item.data(QtCore.Qt.UserRole)
            if path:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(path))


    def main(workspace: str | Path) -> int:
        """Open the Bench window. Returns the Qt exit code."""
        if not QT_AVAILABLE:
            print("The Bench window needs PySide6, and it is not installed here.")
            print("Install it with:  pip install PySide6")
            print(f"(import error was: {QT_ERROR})")
            return 2
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        app = QtWidgets.QApplication([])
        app.setApplicationName("Design Shell Bench")
        window = BenchWindow(workspace)
        window.show()
        return app.exec()

else:

    def main(workspace: str | Path) -> int:  # type: ignore[no-redef]
        print("The Bench window needs PySide6, and it is not installed here.")
        print("Install it with:  pip install PySide6")
        print(f"(import error was: {QT_ERROR})")
        return 2
