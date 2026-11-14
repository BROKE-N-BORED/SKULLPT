"""The Bench as native Linux desktop windows (Qt).

Joshua's standing requirement: this is FOR LINUX - real desktop
windows, not web pages. (The web Bench in bench.py was Arlo's choice,
never his; v1.0 replaced it as the default. The web version remains
as `webbench`.)

v1.5 splits the Bench into one window per job (U1): Jobs is home
(bento job cards, the selected-job strip, chat, status bar); Render
holds the Live Render Window - a real OpenGL viewport drawing the
selected job's description parts as true-scale 3D shapes, with a
free inspection camera (drag to orbit, right-drag to pan, scroll to
zoom) plus named scene cameras saved into the Description File
itself, click a part to select and edit it; Images is the native
image picker; Library is the filed assets; Settings holds engines
and the Hugging Face token. The windows live in qtwindows.py; this
module holds the viewport, the Jobs window, and main().

The viewport tessellates the same shapes the Blender exporter builds
(box / cylinder / sphere / extrude / lathe / tube) from the same
normalized scene data (viewer.scene_data), so what you see is what
builds. No web tech anywhere in this module: no http.server, no
HTML, no browser.

Needs PySide6, installed by install.sh into the shell's private .venv
(v1.2 - before that, install.sh only warned about it and the Bench never
opened on Joshua's PC, because Mint's Python refuses to install packages
outside a venv). Without PySide6 the CLI says so plainly
instead of crashing.
"""
from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

from . import bench as bench_mod
from . import description as desc_mod
from . import governor
from .blender_export import resolve_material
from .expand import expand_parts
from .jobs import Job
from .viewer import scene_data, upsert_camera

try:
    from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
    from PySide6.QtOpenGL import QOpenGLFunctions_2_1
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    QT_AVAILABLE = True
    QT_ERROR: Exception | None = None
except Exception as exc:  # PySide6 missing - the CLI reports it plainly
    QT_AVAILABLE = False
    QT_ERROR = exc


# ---------------------------------------------------------- startup timings
# P8: where the startup time goes, so a slow launch is diagnosable
# instead of a shrug. Stages are recorded by main() and BenchWindow;
# the debug report prints them.

import time as _time

_STARTUP_T0 = _time.perf_counter()
STARTUP_LOG: list[tuple[str, float]] = []


def log_startup(stage: str) -> None:
    """Record one startup milestone (ms since module import)."""
    STARTUP_LOG.append((stage, (_time.perf_counter() - _STARTUP_T0) * 1000.0))


def startup_timings() -> list[tuple[str, float]]:
    """The recorded (stage, ms) pairs, oldest first."""
    return list(STARTUP_LOG)


def default_workspace() -> Path:
    """The workspace when none is given: the folder chosen in
    Settings, else ~/design-shell-workspace."""
    try:
        from PySide6 import QtCore

        settings = QtCore.QSettings("BrokeNBored", "SKULLPT")
        saved = settings.value("workspace")
        if saved:
            return Path(str(saved))
    except Exception:
        pass
    return Path.home() / "design-shell-workspace"


# ------------------------------------------------------------------ geometry
# Pure functions (no Qt): tessellate a description's parts into triangles,
# in cm, z-up, mirroring blender_export's builders exactly.

def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _selection_rgb() -> tuple[float, float, float]:
    """The viewport's selection highlight in the identity amber,
    sampled from the icon like every other amber in the app. Falls
    back to a fixed amber when the design module cannot load."""
    try:
        from .qtdesign import sample_amber

        return hex_to_rgb(sample_amber())
    except Exception:
        return (0.93, 0.62, 0.12)


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
    """Triangles + color for one normalized scene box.

    Normals are precomputed here, once, and stored with the triangles
    (v1.5, P1) - the viewport used to recompute every normal on every
    frame, so every orbit drag re-did all of them.
    """
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
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    return {
        "id": box["id"], "type": box.get("type", "part"),
        "tris": tris, "normals": [_tri_normal(a, b, c) for a, b, c in tris],
        "bounds": {"min": [min(xs), min(ys), min(zs)],
                   "max": [max(xs), max(ys), max(zs)]},
        "color": hex_to_rgb(color),
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


# ------------------------------------------------------- mesh cache (P2)
# The scene used to be re-tessellated on every refresh even when the
# description had not changed. Scenes are cached by a hash of the
# description JSON; a refresh only re-tessellates when the description
# actually changed. The cache is small (8 scenes) and keyed on
# content, so an edited description simply misses and rebuilds.

_MESH_CACHE: dict[str, dict] = {}
_MESH_CACHE_MAX = 8


def _desc_key(desc: dict) -> str:
    import hashlib

    blob = json.dumps(desc, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def cached_scene_meshes(desc: dict) -> dict:
    """scene_meshes, cached by description content."""
    key = _desc_key(desc)
    scene = _MESH_CACHE.get(key)
    if scene is None:
        scene = scene_meshes(desc)
        if len(_MESH_CACHE) >= _MESH_CACHE_MAX:
            _MESH_CACHE.pop(next(iter(_MESH_CACHE)))
        _MESH_CACHE[key] = scene
    return scene


def clear_mesh_cache() -> None:
    _MESH_CACHE.clear()


# ------------------------------------------------------------- ray picking
# Pure functions (no Qt): a mouse click becomes a ray, and the ray is
# tested against each mesh's bounding box first (cheap rejection) and
# then its triangles (Moller-Trumbore). Used by the viewport for
# click-to-select (T11) and hover-to-name (R9).

def _ray_box(origin, direction, mn, mx) -> bool:
    """Slab test: does the ray hit the axis-aligned box?"""
    t_min, t_max = 0.0, float("inf")
    for i in range(3):
        if abs(direction[i]) < 1e-9:
            if origin[i] < mn[i] or origin[i] > mx[i]:
                return False
        else:
            inv = 1.0 / direction[i]
            t0 = (mn[i] - origin[i]) * inv
            t1 = (mx[i] - origin[i]) * inv
            if t0 > t1:
                t0, t1 = t1, t0
            t_min = max(t_min, t0)
            t_max = min(t_max, t1)
            if t_max < t_min:
                return False
    return True


def _ray_triangle(origin, direction, a, b, c) -> float | None:
    """Moller-Trumbore: distance along the ray to the triangle, or
    None when the ray misses. Back faces count - a part is solid."""
    e1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    e2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    px = direction[1] * e2[2] - direction[2] * e2[1]
    py = direction[2] * e2[0] - direction[0] * e2[2]
    pz = direction[0] * e2[1] - direction[1] * e2[0]
    det = e1[0] * px + e1[1] * py + e1[2] * pz
    if abs(det) < 1e-9:
        return None
    inv = 1.0 / det
    tv = (origin[0] - a[0], origin[1] - a[1], origin[2] - a[2])
    u = (tv[0] * px + tv[1] * py + tv[2] * pz) * inv
    if u < 0.0 or u > 1.0:
        return None
    qx = tv[1] * e1[2] - tv[2] * e1[1]
    qy = tv[2] * e1[0] - tv[0] * e1[2]
    qz = tv[0] * e1[1] - tv[1] * e1[0]
    v = (direction[0] * qx + direction[1] * qy + direction[2] * qz) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2[0] * qx + e2[1] * qy + e2[2] * qz) * inv
    return t if t > 1e-6 else None


def pick_mesh(meshes, origin, direction) -> str | None:
    """The id of the nearest mesh the ray hits, or None."""
    best_id: str | None = None
    best_t = float("inf")
    for mesh in meshes:
        bounds = mesh.get("bounds")
        if bounds is not None and not _ray_box(origin, direction,
                                               bounds["min"], bounds["max"]):
            continue
        for tri in mesh["tris"]:
            t = _ray_triangle(origin, direction, *tri)
            if t is not None and t < best_t:
                best_t = t
                best_id = mesh["id"]
    return best_id


# ------------------------------------------------------------- live viewport
# Only defined when Qt is importable, so the geometry above (and the CLI's
# other commands) keep working on a machine without PySide6.

if QT_AVAILABLE:

    class LiveRenderWidget(QOpenGLWidget):
        """A real OpenGL viewport: the description's parts as 3D shapes,
        free orbit / pan / zoom camera, grid + axes. No web tech."""

        partSelected = QtCore.Signal(str)  # a click landed on a part
        partHovered = QtCore.Signal(str)  # "" when hovering empty space

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
            self.setMouseTracking(True)  # hover works with no button held
            self._scene: dict | None = None
            self._gl: QOpenGLFunctions_2_1 | None = None
            self._selected_part: str | None = None
            self._hovered_part: str | None = None
            self._press_pos: QtCore.QPoint | None = None
            self._last_pick_pos: QtCore.QPoint | None = None
            # Orbit camera: target + distance + azimuth/elevation (degrees).
            self.target = (0.0, 0.0, 0.0)
            self.distance = 200.0
            self.azimuth = -126.0
            self.elevation = 29.0
            self._drag_button: QtCore.Qt.MouseButton | None = None
            self._drag_pos: QtCore.QPoint | None = None

        # -- picking ----------------------------------------------------
        def set_selected_part(self, part_id: str | None) -> None:
            """Highlight a part (or clear the highlight with None)."""
            self._selected_part = part_id
            self.update()

        @property
        def selected_part(self) -> str | None:
            return self._selected_part

        @property
        def hovered_part(self) -> str | None:
            return self._hovered_part

        def grid_step(self) -> float:
            """The grid spacing in cm - the legend's true step (span/10)."""
            return self.span() / 10.0

        def pick(self, x: int, y: int) -> str | None:
            """The part id under pixel (x, y), or None (T11)."""
            if not self._scene or self.width() < 1 or self.height() < 1:
                return None
            proj, view = self._matrices()
            inv_proj, ok1 = proj.inverted()
            inv_view, ok2 = view.inverted()
            if not (ok1 and ok2):
                return None
            nx = 2.0 * x / self.width() - 1.0
            ny = 1.0 - 2.0 * y / self.height()
            near_eye = inv_proj.map(QtGui.QVector3D(nx, ny, -1.0))
            far_eye = inv_proj.map(QtGui.QVector3D(nx, ny, 1.0))
            near_world = inv_view.map(near_eye)
            far_world = inv_view.map(far_eye)
            direction = (far_world.x() - near_world.x(),
                         far_world.y() - near_world.y(),
                         far_world.z() - near_world.z())
            length = math.sqrt(sum(v * v for v in direction)) or 1.0
            direction = tuple(v / length for v in direction)
            return pick_mesh(self._scene["meshes"],
                             self.camera_position(), direction)

        # -- scene ------------------------------------------------------
        def set_scene(self, desc: dict | None) -> None:
            self._scene = cached_scene_meshes(desc) if desc else None
            self._selected_part: str | None = None
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

        def _matrices(self):
            """The projection + view matrices paintGL draws with, shared
            with pick() so the ray matches the picture."""
            width, height = max(1, self.width()), max(1, self.height())
            eye = self.camera_position()
            proj = QtGui.QMatrix4x4()
            proj.perspective(45.0, width / height,
                             max(0.1, self.distance / 100.0),
                             self.distance * 20.0 + self.span() * 10.0)
            view = QtGui.QMatrix4x4()
            view.lookAt(QtGui.QVector3D(*eye),
                        QtGui.QVector3D(*self.target),
                        QtGui.QVector3D(0.0, 0.0, 1.0))
            return proj, view

        def paintGL(self) -> None:
            gl = self._gl
            if gl is None:
                return
            gl.glClear(0x00004000 | 0x00000100)  # COLOR + DEPTH
            proj, view = self._matrices()
            gl.glMatrixMode(0x1701)  # PROJECTION
            gl.glLoadMatrixf(list(proj.data()))
            gl.glMatrixMode(0x1700)  # MODELVIEW
            gl.glLoadMatrixf(list(view.data()))
            # Directional light from upper-left-front, in eye space.
            gl.glLightfv(0x4000, 0x1203, (-0.5, 0.7, 1.0, 0.0))  # POSITION
            self._draw_grid(gl)
            if self._scene:
                self._draw_meshes(gl, self._scene["meshes"])
                self._draw_selection(gl)

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
                    normals = mesh.get("normals")
                    for i, (a, bv, c) in enumerate(mesh["tris"]):
                        if normals is not None:
                            nx, ny, nz = normals[i]
                        else:  # a mesh built without normals (old code)
                            nx, ny, nz = _tri_normal(a, bv, c)
                        gl.glNormal3f(nx, ny, nz)
                        gl.glVertex3f(*a)
                        gl.glVertex3f(*bv)
                        gl.glVertex3f(*c)
                    gl.glEnd()
                if glass_pass:
                    gl.glDepthMask(True)
                    gl.glDisable(0x0BE2)

        def _draw_selection(self, gl) -> None:
            """The selected part's edges in the identity amber."""
            if not self._scene or not self._selected_part:
                return
            mesh = next((m for m in self._scene["meshes"]
                         if m["id"] == self._selected_part), None)
            if mesh is None:
                return
            r, g, b = _selection_rgb()
            gl.glDisable(0x0B50)  # LIGHTING off for the outline
            gl.glColor3f(r, g, b)
            gl.glPolygonMode(0x0408, 0x0001)  # FRONT_AND_BACK, LINE
            gl.glBegin(0x0004)  # TRIANGLES
            for a, bv, c in mesh["tris"]:
                gl.glVertex3f(*a)
                gl.glVertex3f(*bv)
                gl.glVertex3f(*c)
            gl.glEnd()
            gl.glPolygonMode(0x0408, 0x0002)  # FILL
            gl.glEnable(0x0B50)

        # -- mouse -------------------------------------------------------
        def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
            self._drag_button = event.button()
            self._drag_pos = event.position().toPoint()
            self._press_pos = event.position().toPoint()
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
            pos = event.position().toPoint()
            if self._drag_pos is None:
                # Hover (no button held): re-pick only after 4+ px.
                if (self._last_pick_pos is None
                        or abs(pos.x() - self._last_pick_pos.x())
                        + abs(pos.y() - self._last_pick_pos.y()) >= 4):
                    self._last_pick_pos = pos
                    hovered = self.pick(pos.x(), pos.y())
                    if hovered != self._hovered_part:
                        self._hovered_part = hovered
                        self.partHovered.emit(hovered or "")
                super().mouseMoveEvent(event)
                return
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
            if (event.button() == QtCore.Qt.LeftButton
                    and self._press_pos is not None
                    and self._drag_pos is not None):
                moved = (abs(self._drag_pos.x() - self._press_pos.x())
                         + abs(self._drag_pos.y() - self._press_pos.y()))
                if moved < 4:  # a click, not a drag: pick the part
                    part = self.pick(int(event.position().x()),
                                     int(event.position().y()))
                    self.set_selected_part(part)
                    if part is not None:
                        self.partSelected.emit(part)
            self._drag_button = None
            self._drag_pos = None
            self._press_pos = None
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
        """The Jobs window: home. Bento job cards, the selected-job
        strip, the chat panel, and the status bar. Render, Images,
        Library, and Settings live in their own windows, opened from
        the icon rail or the menu bar."""

        def __init__(self, workspace: str | Path):
            super().__init__()
            from .qtwindows import RenderWindow, restore_geometry

            self.workspace = Path(workspace)
            self.setWindowTitle("SKULLPT - Jobs")
            self.resize(1280, 720)
            self._selected: str | None = None  # nothing selected by default (U6)
            self._pending_runs: dict[str, str] = {}
            self._current_run_id: str | None = None
            self._cards: dict[str, object] = {}
            self._child_windows: dict[str, QtWidgets.QMainWindow] = {}
            self._chat_lock = threading.Lock()
            self._render_window = RenderWindow(self, self.workspace)
            self._child_windows["render"] = self._render_window
            self._build_ui()
            log_startup("jobs window built")
            restore_geometry(self, "JobsWindow")
            if self.width() < 640:
                self.resize(1280, 720)
            self._refresh()
            log_startup("first refresh")
            self._recover_stale()
            self._poll_timer = QtCore.QTimer(self)
            self._poll_timer.timeout.connect(self._poll_runs)
            self._poll_timer.start(500)

        @property
        def _viewport(self):
            """The Render window's viewport (kept for the old tests and
            for anything that drives the viewport through the Bench)."""
            return self._render_window.viewport

        # -- layout -----------------------------------------------------
        def _build_ui(self) -> None:
            from .qtdesign import load_icon

            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            outer = QtWidgets.QHBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)

            # Icon rail: one button per window (24px bone icons).
            rail = QtWidgets.QVBoxLayout()
            rail.setContentsMargins(4, 8, 4, 8)
            rail.setSpacing(4)
            rail_widget = QtWidgets.QWidget()
            rail_widget.setLayout(rail)
            rail_widget.setFixedWidth(52)
            for name, icon, tip in (
                    ("jobs", "layout-grid", "Jobs"),
                    ("render", "box", "Render"),
                    ("images", "image", "Images"),
                    ("library", "library", "Library"),
                    ("settings", "settings", "Settings"),
                    ("help", "help-circle", "How to Use")):
                btn = QtWidgets.QToolButton()
                btn.setIcon(load_icon(icon))
                btn.setIconSize(QtCore.QSize(24, 24))
                btn.setToolTip(tip)
                btn.clicked.connect(
                    lambda _checked=False, n=name: self._open_window(n))
                rail.addWidget(btn)
            rail.addStretch(1)
            outer.addWidget(rail_widget)

            # Main area: draft row, bento grid, detail strip.
            main = QtWidgets.QVBoxLayout()
            main.setContentsMargins(8, 8, 8, 8)
            main.setSpacing(8)
            top = QtWidgets.QHBoxLayout()
            title = QtWidgets.QLabel("Jobs")
            title.setObjectName("heading")
            top.addWidget(title)
            self._words = QtWidgets.QLineEdit()
            self._words.setPlaceholderText("Describe what to make")
            self._words.returnPressed.connect(self._draft_job)
            top.addWidget(self._words, 1)
            draft_btn = QtWidgets.QPushButton("Draft it")
            draft_btn.setObjectName("primary")
            draft_btn.clicked.connect(self._draft_job)
            top.addWidget(draft_btn)
            self._example_combo = QtWidgets.QComboBox()
            self._example_combo.setSizeAdjustPolicy(
                QtWidgets.QComboBox.AdjustToContentsOnFirstShow)
            top.addWidget(self._example_combo)
            example_btn = QtWidgets.QPushButton("From example")
            example_btn.clicked.connect(self._job_from_example)
            top.addWidget(example_btn)
            main.addLayout(top)

            # First-run panel: shown when there are no jobs yet.
            self._first_run = QtWidgets.QFrame()
            self._first_run.setObjectName("card")
            first_layout = QtWidgets.QVBoxLayout(self._first_run)
            first_layout.setContentsMargins(16, 16, 16, 16)
            welcome = QtWidgets.QLabel("Welcome to SKULLPT")
            welcome.setObjectName("heading")
            first_layout.addWidget(welcome)
            first_layout.addWidget(QtWidgets.QLabel(
                "SKULLPT turns plain descriptions into 3D assets. "
                "Start here:"))
            try_btn = QtWidgets.QPushButton("Try an example")
            try_btn.setObjectName("primary")
            try_btn.clicked.connect(self._try_example)
            first_layout.addWidget(try_btn)
            first_layout.addWidget(QtWidgets.QLabel(
                "Or type 'make a wooden shop panel' in the chat on the right."))
            how_btn = QtWidgets.QPushButton("How to Use")
            how_btn.setObjectName("ghost")
            how_btn.clicked.connect(lambda: self._open_window("help"))
            first_layout.addWidget(how_btn)
            main.addWidget(self._first_run)

            # Bento grid: job cards plus the engine, renders, and
            # could-not-read tiles.
            self._scroll = QtWidgets.QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._grid_holder = QtWidgets.QWidget()
            self._grid = QtWidgets.QGridLayout(self._grid_holder)
            self._grid.setSpacing(8)
            self._scroll.setWidget(self._grid_holder)
            main.addWidget(self._scroll, 1)
            self._build_tiles()
            main.addLayout(self._build_detail_strip())
            outer.addLayout(main, 3)

            # Chat panel.
            outer.addLayout(self._build_chat(), 1)

            self._build_menus()
            self._build_status_bar()

        def _build_tiles(self) -> None:
            from .qtwindows import JobCard  # noqa: F401  (kept for clarity)

            self._engine_tile = QtWidgets.QFrame()
            self._engine_tile.setObjectName("card")
            tile_layout = QtWidgets.QVBoxLayout(self._engine_tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            tile_layout.addWidget(QtWidgets.QLabel("Engines"))
            self._engine_tile_text = QtWidgets.QLabel()
            self._engine_tile_text.setObjectName("muted")
            self._engine_tile_text.setWordWrap(True)
            tile_layout.addWidget(self._engine_tile_text)
            settings_btn = QtWidgets.QPushButton("Open Settings")
            settings_btn.clicked.connect(lambda: self._open_window("settings"))
            tile_layout.addWidget(settings_btn)

            self._renders_tile = QtWidgets.QFrame()
            self._renders_tile.setObjectName("card")
            renders_layout = QtWidgets.QVBoxLayout(self._renders_tile)
            renders_layout.setContentsMargins(10, 8, 10, 8)
            renders_layout.addWidget(QtWidgets.QLabel("Latest render"))
            self._renders_tile_image = QtWidgets.QLabel("No renders yet.")
            self._renders_tile_image.setObjectName("muted")
            self._renders_tile_image.setAlignment(QtCore.Qt.AlignCenter)
            renders_layout.addWidget(self._renders_tile_image)
            self._renders_tile_text = QtWidgets.QLabel(
                "Build a job to see its renders here.")
            self._renders_tile_text.setObjectName("muted")
            self._renders_tile_text.setWordWrap(True)
            renders_layout.addWidget(self._renders_tile_text)

            self._skipped_tile = QtWidgets.QFrame()
            self._skipped_tile.setObjectName("card")
            skipped_layout = QtWidgets.QVBoxLayout(self._skipped_tile)
            skipped_layout.setContentsMargins(10, 8, 10, 8)
            skipped_layout.addWidget(QtWidgets.QLabel("Could not read"))
            self._skipped_tile_text = QtWidgets.QLabel()
            self._skipped_tile_text.setObjectName("muted")
            self._skipped_tile_text.setWordWrap(True)
            skipped_layout.addWidget(self._skipped_tile_text)

        def _build_detail_strip(self) -> QtWidgets.QLayout:
            from .qtwindows import StagePill

            strip = QtWidgets.QFrame()
            strip.setObjectName("card")
            layout = QtWidgets.QVBoxLayout(strip)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(4)
            top = QtWidgets.QHBoxLayout()
            self._detail_pill = StagePill("draft")
            top.addWidget(self._detail_pill)
            self._job_title = QtWidgets.QLabel("No job selected")
            self._job_title.setStyleSheet("font-weight: 600;")
            self._job_title.setWordWrap(True)
            top.addWidget(self._job_title, 1)
            layout.addLayout(top)
            self._job_summary = QtWidgets.QLabel(
                "Pick a job card above, or draft a new job.")
            self._job_summary.setObjectName("muted")
            self._job_summary.setWordWrap(True)
            self._job_summary.setMaximumHeight(48)
            layout.addWidget(self._job_summary)
            self._verdict = QtWidgets.QLabel("")
            self._verdict.setWordWrap(True)
            layout.addWidget(self._verdict)
            btn_row = QtWidgets.QHBoxLayout()
            self._approve_btn = QtWidgets.QPushButton("Approve")
            self._approve_btn.setObjectName("primary")
            self._approve_btn.clicked.connect(lambda: self._do_action("approve"))
            self._build_btn = QtWidgets.QPushButton("Build")
            self._build_btn.clicked.connect(lambda: self._do_action("build"))
            self._file_btn = QtWidgets.QPushButton("File into Library")
            self._file_btn.clicked.connect(lambda: self._do_action("file"))
            self._retry_btn = QtWidgets.QPushButton("Retry")
            self._retry_btn.clicked.connect(lambda: self._do_action("retry"))
            self._report_btn = QtWidgets.QPushButton("Build report")
            self._report_btn.clicked.connect(self._show_build_report)
            btn_row.addWidget(self._approve_btn)
            btn_row.addWidget(self._build_btn)
            btn_row.addWidget(self._file_btn)
            btn_row.addWidget(self._retry_btn)
            btn_row.addWidget(self._report_btn)
            btn_row.addStretch(1)
            self._duplicate_btn = QtWidgets.QPushButton("Duplicate")
            self._duplicate_btn.clicked.connect(self._duplicate_job)
            self._rename_btn = QtWidgets.QPushButton("Rename")
            self._rename_btn.clicked.connect(self._rename_job)
            self._delete_btn = QtWidgets.QPushButton("Delete")
            self._delete_btn.clicked.connect(self._delete_job)
            btn_row.addWidget(self._duplicate_btn)
            btn_row.addWidget(self._rename_btn)
            btn_row.addWidget(self._delete_btn)
            layout.addLayout(btn_row)
            holder = QtWidgets.QVBoxLayout()
            holder.addWidget(strip)
            return holder

        def _build_chat(self) -> QtWidgets.QLayout:
            layout = QtWidgets.QVBoxLayout()
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)
            layout.addWidget(QtWidgets.QLabel("Chat"))
            self._chat_log = QtWidgets.QTextEdit()
            self._chat_log.setReadOnly(True)
            layout.addWidget(self._chat_log, 1)
            self._action_row = QtWidgets.QHBoxLayout()
            layout.addLayout(self._action_row)
            row = QtWidgets.QHBoxLayout()
            self._chat_input = QtWidgets.QLineEdit()
            self._chat_input.setPlaceholderText(
                "make ... / picture of ... / approve / build / file / status")
            self._chat_input.returnPressed.connect(self._send_chat)
            row.addWidget(self._chat_input, 1)
            send_btn = QtWidgets.QPushButton("Send")
            send_btn.clicked.connect(self._send_chat)
            row.addWidget(send_btn)
            layout.addLayout(row)
            return layout

        def _build_menus(self) -> None:
            menu_bar = self.menuBar()
            file_menu = menu_bar.addMenu("File")
            quit_action = QtGui.QAction("Quit", self)
            quit_action.setShortcut(QtGui.QKeySequence.Quit)
            quit_action.triggered.connect(self.close)
            file_menu.addAction(quit_action)

            jobs_menu = menu_bar.addMenu("Jobs")
            jobs_menu.addAction("New job",
                                lambda: self._words.setFocus())
            jobs_menu.addAction("Mesh from photo", self._mesh_from_photo)
            jobs_menu.addAction("Open an asset", self._open_asset)
            jobs_menu.addAction("Unreal package", self._unreal_package)
            jobs_menu.addSeparator()
            jobs_menu.addAction("Duplicate job", self._duplicate_job)
            jobs_menu.addAction("Rename job", self._rename_job)
            jobs_menu.addAction("Delete job", self._delete_job)

            menu_bar.addAction("Render",
                               lambda: self._open_window("render"))
            menu_bar.addAction("Images",
                               lambda: self._open_window("images"))
            menu_bar.addAction("Library",
                               lambda: self._open_window("library"))
            menu_bar.addAction("Settings",
                               lambda: self._open_window("settings"))

            help_menu = menu_bar.addMenu("Help")
            help_menu.addAction("How to Use",
                                lambda: self._open_window("help"))
            help_menu.addAction("Debug report", self._show_debug_report)
            help_menu.addAction("About SKULLPT", self._show_about)

        def _build_status_bar(self) -> None:
            bar = self.statusBar()
            self._engines_label = QtWidgets.QLabel(str(self.workspace))
            bar.addWidget(self._engines_label, 1)
            self._run_label = QtWidgets.QLabel("Idle")
            bar.addPermanentWidget(self._run_label)
            self._run_progress = QtWidgets.QProgressBar()
            self._run_progress.setFixedWidth(160)
            self._run_progress.setVisible(False)
            bar.addPermanentWidget(self._run_progress)
            self._cancel_btn = QtWidgets.QPushButton("Cancel")
            self._cancel_btn.setVisible(False)
            self._cancel_btn.clicked.connect(self._cancel_current_run)
            bar.addPermanentWidget(self._cancel_btn)
            self._ram_label = QtWidgets.QLabel("RAM free: -")
            bar.addPermanentWidget(self._ram_label)

        # -- windows ----------------------------------------------------
        def _open_window(self, name: str) -> None:
            from .qtwindows import (HelpWindow, ImagesWindow, LibraryWindow,
                                    SettingsWindow)

            if name == "jobs":
                self.show()
                self.raise_()
                self.activateWindow()
                return
            window = self._child_windows.get(name)
            if window is None:
                if name == "render":
                    window = self._render_window
                elif name == "images":
                    window = ImagesWindow(self, self.workspace)
                elif name == "library":
                    window = LibraryWindow(self, self.workspace)
                elif name == "settings":
                    window = SettingsWindow(self, self.workspace)
                elif name == "help":
                    window = HelpWindow(self)
                else:
                    return
                self._child_windows[name] = window
            if name == "render":
                window.set_job(self._selected)
            else:
                refresh = getattr(window, "refresh", None)
                if callable(refresh):
                    refresh()
            window.show()
            window.raise_()
            window.activateWindow()

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
            from .qtwindows import save_geometry

            save_geometry(self, "JobsWindow")
            for window in list(self._child_windows.values()):
                window.close()
            super().closeEvent(event)

        # -- state ------------------------------------------------------
        def refresh(self) -> None:
            """Public refresh (child windows call this after a change)."""
            self._refresh()

        def select_job(self, name: str | None) -> None:
            """Select a job (or clear the selection with None)."""
            self._selected = name
            self._refresh()

        def _refresh(self) -> None:
            try:
                state = bench_mod.workspace_state(self.workspace)
            except Exception as exc:
                self.statusBar().showMessage(
                    f"could not read workspace: {exc}")
                return
            engines = state["engines"]
            self._engines_label.setText(
                "Blender: " + ("ready" if engines["blender"] else "MISSING")
                + "   Image engine: "
                + ("ready" if engines["image_engine"] else "MISSING")
                + "   Image model: "
                + ("ready" if engines["image_model"] else "MISSING")
                + "   Gemini key: "
                + ("set" if engines["gemini_key"] else "not set")
                + "   HF token: "
                + ("set" if engines.get("hf_token") else "not set"))
            free = governor.free_ram_mb()
            floor = governor.adaptive_min_free_mb()
            self._ram_label.setText(
                f"RAM free: {free} MB (floor {floor} MB)"
                if free is not None else "RAM free: unknown")
            self._example_combo.blockSignals(True)
            self._example_combo.clear()
            self._example_combo.addItems(state["examples"])
            self._example_combo.blockSignals(False)
            jobs = state["jobs"]
            if self._selected and self._selected not in {j["name"] for j in jobs}:
                self._selected = None
            # No auto-select (U6): the selection stays empty until the
            # user picks a job card.
            self._sync_cards(state)
            self._refresh_detail(jobs)
            self._refresh_chat_log()
            if self._render_window.job_name != self._selected:
                self._render_window.set_job(self._selected)
            elif self._render_window.isVisible():
                self._render_window.refresh()

        def _sync_cards(self, state: dict) -> None:
            """Incremental refresh (T17): cards are updated in place
            by job name, so widget identity survives a refresh."""
            from .qtwindows import JobCard

            jobs = state["jobs"]
            names = {j["name"] for j in jobs}
            for name in [n for n in self._cards if n not in names]:
                card = self._cards.pop(name)
                self._grid.removeWidget(card)
                card.hide()
                card.deleteLater()
            for job in jobs:
                card = self._cards.get(job["name"])
                if card is None:
                    card = JobCard(job)
                    card.cardClicked.connect(self._on_card_clicked)
                    card.gateClicked.connect(self._on_gate_clicked)
                    self._cards[job["name"]] = card
                else:
                    card.update_job(job)
                card.set_selected(job["name"] == self._selected)
            # Reflow the grid (same widgets, new positions).
            for card in self._cards.values():
                self._grid.removeWidget(card)
            for tile in (self._engine_tile, self._renders_tile,
                         self._skipped_tile):
                self._grid.removeWidget(tile)
            row = col = 0
            for job in jobs:
                self._grid.addWidget(self._cards[job["name"]], row, col)
                col += 1
                if col >= 3:
                    col = 0
                    row += 1
            self._update_tiles(state, row, col)
            has_jobs = bool(jobs)
            self._first_run.setVisible(not has_jobs)
            self._scroll.setVisible(has_jobs)

        def _update_tiles(self, state: dict, row: int, col: int) -> None:
            engines = state["engines"]
            bits = [
                "Blender " + ("ready" if engines["blender"] else "missing"),
                "Image engine "
                + ("ready" if engines["image_engine"] else "missing"),
                "Image model "
                + ("ready" if engines["image_model"] else "missing"),
                "Gemini key "
                + ("set" if engines["gemini_key"] else "not set"),
                "HF token "
                + ("set" if engines.get("hf_token") else "not set"),
            ]
            self._engine_tile_text.setText("\n".join(bits))
            latest = None
            for job in reversed(state["jobs"]):
                if job.get("renders"):
                    latest = job
                    break
            if latest is not None:
                path = (self.workspace / "jobs" / latest["name"] / "output"
                        / latest["renders"][0])
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    self._renders_tile_image.setPixmap(pixmap.scaled(
                        160, 120, QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation))
                    self._renders_tile_image.setText("")
                self._renders_tile_text.setText(
                    f"From {latest['name']}. Open Render to see the scene.")
            else:
                self._renders_tile_image.setPixmap(QtGui.QPixmap())
                self._renders_tile_image.setText("No renders yet.")
                self._renders_tile_text.setText(
                    "Build a job to see its renders here.")
            skipped = state.get("skipped", [])
            if skipped:
                self._skipped_tile_text.setText("\n".join(
                    f"{entry['where']} / {entry['name']}: {entry['reason']}"
                    for entry in skipped))
            # Tiles go after the cards in the grid flow.
            for tile in (self._engine_tile, self._renders_tile):
                self._grid.addWidget(tile, row, col)
                col += 1
                if col >= 3:
                    col = 0
                    row += 1
            if skipped:
                self._grid.addWidget(self._skipped_tile, row, col)
                self._skipped_tile.setVisible(True)
            else:
                self._skipped_tile.setVisible(False)

        def _refresh_detail(self, jobs: list[dict]) -> None:
            job = next((j for j in jobs if j["name"] == self._selected), None)
            if job is None:
                self._detail_pill.setVisible(False)
                self._job_title.setText("No job selected")
                self._job_summary.setText(
                    "Pick a job card above, or draft a new job.")
                self._verdict.setText("")
                self._approve_btn.setEnabled(False)
                self._build_btn.setEnabled(False)
                self._file_btn.setEnabled(False)
                self._retry_btn.setEnabled(False)
                self._report_btn.setEnabled(False)
                self._duplicate_btn.setEnabled(False)
                self._rename_btn.setEnabled(False)
                self._delete_btn.setEnabled(False)
                return
            self._detail_pill.setVisible(True)
            self._detail_pill.set_state(job["state"])
            self._job_title.setText(job["title"])
            summary = job.get("summary") or ""
            self._job_summary.setText(
                summary if len(summary) <= 300 else summary[:300] + "...")
            self._approve_btn.setEnabled(job["state"] == "awaiting_approval")
            self._build_btn.setEnabled(job["state"] == "approved")
            self._file_btn.setEnabled(job["state"] == "awaiting_review")
            self._retry_btn.setEnabled(job["state"] == "failed")
            self._report_btn.setEnabled(job.get("report") is not None)
            self._duplicate_btn.setEnabled(True)
            self._rename_btn.setEnabled(job["state"] not in ("building", "checking"))
            self._delete_btn.setEnabled(job["state"] not in ("building", "checking"))
            report = job.get("report")
            if report:
                verdict = ("build passed" if report.get("ok") else "build failed")
                problems = "; ".join(report.get("problems", [])[:3])
                self._verdict.setText(
                    verdict + (f" - {problems}" if problems else ""))
            else:
                self._verdict.setText("")

        def _refresh_chat_log(self) -> None:
            history = bench_mod.chat_history(self.workspace)
            lines = []
            for entry in history[-40:]:
                who = "You" if entry.get("role") == "user" else "Bench"
                lines.append(f"{who}: {entry.get('text', '')}")
            self._chat_log.setPlainText("\n\n".join(lines))
            self._chat_log.verticalScrollBar().setValue(
                self._chat_log.verticalScrollBar().maximum())

        def _recover_stale(self) -> None:
            recovered = bench_mod.recover_stale(self.workspace)
            if recovered:
                self._log("Bench", "Recovered jobs left by a crash: "
                                   + ", ".join(recovered)
                                   + ". They are approved again.")

        # -- interactions ------------------------------------------------
        def _on_card_clicked(self, name: str) -> None:
            self.select_job(name)

        def _on_gate_clicked(self, name: str, action: str) -> None:
            self._selected = name
            if action == "open":
                self._refresh()
                self._open_window("render")
                return
            self._do_action(action, {"job": name})

        def _log(self, who: str, text: str) -> None:
            with self._chat_lock:
                history = bench_mod.chat_history(self.workspace)
                history.append({"role": "user" if who == "You" else "bot",
                                "text": text})
                del history[:-60]
                bench_mod.save_chat_history(self.workspace)
            self._refresh_chat_log()

        def do_action(self, action: str, payload: dict | None = None) -> dict | None:
            """Run one Bench action and refresh. Returns the result
            dict, or None when the action failed (the failure is
            logged in the chat)."""
            job_name = (payload or {}).get("job") or self._selected
            if not job_name and action in ("approve", "build", "file", "retry",
                                           "delete", "rename", "duplicate",
                                           "edit-part", "unreal-package"):
                self._log("Bench", "No job selected.")
                return None
            try:
                result = bench_mod.act(self.workspace, action,
                                       {"job": job_name, **(payload or {})})
            except Exception as exc:
                self._log("Bench", str(exc))
                return None
            run_id = result.get("run")
            if run_id:
                self._pending_runs[run_id] = f"{action} {job_name or ''}".strip()
                self._log("Bench", f"{action} started"
                                   + (f" for {job_name}" if job_name else "")
                                   + " - I will report back when it finishes.")
            elif result.get("detail"):
                self._log("Bench", result["detail"])
            if result.get("job"):
                self._selected = result["job"]
            self._refresh()
            return result

        def _do_action(self, action: str, payload: dict | None = None) -> dict | None:
            return self.do_action(action, payload)

        def start_reroll(self, batch_dir) -> None:
            """Roll another round into a batch (called by the Images
            window). Runs in the background like every long job."""
            try:
                result = bench_mod.act(self.workspace, "reroll",
                                       {"batch": Path(batch_dir).name})
            except Exception as exc:
                self._log("Bench", str(exc))
                return
            if result.get("run"):
                self._pending_runs[result["run"]] = (
                    f"reroll {Path(batch_dir).name}")
                self._log("Bench", "Rolling another round.")

        def _cancel_current_run(self) -> None:
            if self._current_run_id is not None:
                bench_mod.cancel_run(self._current_run_id)

        def _poll_runs(self) -> None:
            from .qtwindows import Toast

            running = [r for r in bench_mod._runs()
                       if r["status"] == "running"
                       and r["id"] in self._pending_runs]
            if running:
                run = running[0]
                self._current_run_id = run["id"]
                text = run.get("progress_text") or run["label"]
                self._run_label.setText(f"{run['kind']}: {text}")
                self._run_progress.setVisible(True)
                if run.get("progress") is not None:
                    self._run_progress.setRange(0, 100)
                    self._run_progress.setValue(int(run["progress"] * 100))
                else:
                    self._run_progress.setRange(0, 0)
                self._cancel_btn.setVisible(True)
            else:
                self._current_run_id = None
                self._run_label.setText("Idle")
                self._run_progress.setVisible(False)
                self._cancel_btn.setVisible(False)
            finished = [r for r in bench_mod._runs()
                        if r["id"] in self._pending_runs
                        and r["status"] != "running"]
            if not finished:
                return
            for run in finished:
                label = self._pending_runs.pop(run["id"])
                if run["status"] == "done":
                    self._on_run_done(run, label)
                    Toast.show_toast(self, f"{label}: done.")
                elif run["status"] == "cancelled":
                    self._log("Bench", f"{label}: cancelled.")
                else:
                    self._log("Bench", f"{label}: failed - {run['detail']}")
            self._refresh()
            for name in ("images", "library"):
                window = self._child_windows.get(name)
                if window is not None and window.isVisible():
                    window.refresh()

        def _on_run_done(self, run: dict, label: str) -> None:
            result = run.get("result") or {}
            kind = run["kind"]
            if kind == "plan" and result.get("job"):
                self._selected = result["job"]
                self._log("Bench", run["detail"])
            elif kind == "chat":
                self._refresh_chat_log()
                self._show_chat_actions(result.get("actions", []))
            elif run["detail"]:
                self._log("Bench", f"{label}: done - {run['detail']}")
            else:
                self._log("Bench", f"{label}: done.")

        def _show_chat_actions(self, actions: list[dict]) -> None:
            while self._action_row.count():
                item = self._action_row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for action in actions:
                btn = QtWidgets.QPushButton(action["label"])
                btn.clicked.connect(
                    lambda _checked=False, a=action: self._do_action(
                        a["action"], a["payload"]))
                self._action_row.addWidget(btn)

        def _draft_job(self) -> None:
            """Draft from the top row. The planner call runs in the
            background (T18) - the window never freezes."""
            words = self._words.text().strip()
            if not words:
                return
            self._words.clear()

            def do_plan(cancel, report):
                report(None, "Drafting - asking the planner seat")
                return bench_mod.act(self.workspace, "plan",
                                     {"words": words, "kind": "scene"})

            run_id = bench_mod._start_run("plan", words[:40], do_plan)
            self._pending_runs[run_id] = f"draft {words[:40]}"
            self._log("Bench", f"Drafting {words!r} - running in the background.")

        def _try_example(self) -> None:
            state = bench_mod.workspace_state(self.workspace)
            examples = state.get("examples", [])
            if not examples:
                self._log("Bench", "No examples found.")
                return
            self._example_combo.setCurrentText(examples[0])
            self._job_from_example()

        def _job_from_example(self) -> None:
            name = self._example_combo.currentText()
            if not name:
                return
            result = self.do_action("new-example", {"example": name})
            if result is not None and result.get("job"):
                self._selected = result["job"]
                self._refresh()

        def _send_chat(self) -> None:
            """Send a chat message. The reply runs in the background
            (T18) - planning and open conversation both call out to a
            seat, and the window must not freeze while they answer."""
            text = self._chat_input.text().strip()
            if not text:
                return
            self._chat_input.clear()
            with self._chat_lock:
                history = bench_mod.chat_history(self.workspace)
                history.append({"role": "user", "text": text})
                del history[:-60]
                bench_mod.save_chat_history(self.workspace)
            self._refresh_chat_log()
            selected = self._selected

            def do_chat(cancel, report):
                with self._chat_lock:
                    history = bench_mod.chat_history(self.workspace)
                    result = bench_mod.chat_reply(self.workspace, text,
                                                  selected=selected)
                    history.append({"role": "bot", "text": result["reply"],
                                    "actions": result.get("actions", [])})
                    del history[:-60]
                    bench_mod.save_chat_history(self.workspace)
                    return result

            run_id = bench_mod._start_run("chat", text[:40], do_chat)
            self._pending_runs[run_id] = f"chat {text[:40]}"

        def _mesh_from_photo(self) -> None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Pick a photo to mesh", "",
                "Photos (*.png *.jpg *.jpeg *.webp *.bmp)")
            if not path:
                return
            self._do_action("cloud-mesh", {"photo": path})

        def _open_asset(self) -> None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open an existing asset", "",
                "Assets (*.glb *.blend)")
            if not path:
                return
            self._do_action("ingest", {"file": path})

        def _unreal_package(self) -> None:
            if not self._selected:
                self._log("Bench", "Select a job first.")
                return
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Choose a folder for the Unreal package", "")
            if not folder:
                return
            self._do_action("unreal-package",
                            {"job": self._selected, "dest": folder})

        def _duplicate_job(self) -> None:
            if self._selected:
                self._do_action("duplicate", {"job": self._selected})

        def _rename_job(self) -> None:
            if not self._selected:
                return
            name, ok = QtWidgets.QInputDialog.getText(
                self, "Rename job", "New name:", text=self._selected)
            if not ok or not name.strip():
                return
            self._do_action("rename", {"job": self._selected,
                                       "name": name.strip()})

        def _delete_job(self) -> None:
            if not self._selected:
                return
            answer = QtWidgets.QMessageBox.question(
                self, "Delete this job?",
                f"Delete {self._selected}? The job folder and its outputs "
                "go away for good.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            self._do_action("delete", {"job": self._selected})
            self._selected = None
            self._refresh()

        def _show_build_report(self) -> None:
            from .qtwindows import BuildReportDialog

            if self._selected:
                BuildReportDialog(self.workspace, self._selected,
                                  self).exec()

        def _show_debug_report(self) -> None:
            from .qtwindows import DebugReportDialog

            DebugReportDialog(self.workspace, self._selected, self).exec()

        def _show_about(self) -> None:
            QtWidgets.QMessageBox.about(
                self, "About SKULLPT",
                "SKULLPT v1.5\nA standalone Linux design and asset shell,\n"
                "by Broke n Bored Studio.")



    def main(workspace: str | Path | None = None) -> int:
        """Open the Bench window. Returns the Qt exit code."""
        if not QT_AVAILABLE:
            print("The Bench window needs PySide6, and it is not installed here.")
            print("Fix: double-click Install SKULLPT in the SKULLPT folder -")
            print("it installs PySide6 into the shell's private .venv.")
            print("(Do not `pip install PySide6` - Mint's Python refuses it.)")
            print(f"(import error was: {QT_ERROR})")
            return 2
        import hashlib

        from .qtdesign import apply_theme

        log_startup("main entered")
        if workspace is None:
            workspace = default_workspace()
        workspace = Path(workspace)
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
        app = QtWidgets.QApplication([])
        app.setApplicationName("SKULLPT Bench")
        apply_theme(app)
        log_startup("theme applied")

        # Single instance (T13): one Bench per workspace. A second
        # launch hands off to the running one instead of opening a
        # second window over the same jobs.
        server_name = ("SKULLPT-" + hashlib.sha256(
            str(workspace.resolve()).encode()).hexdigest()[:16])
        probe = QtNetwork.QLocalSocket()
        probe.connectToServer(server_name)
        if probe.waitForConnected(500):
            probe.write(b"raise")
            probe.waitForBytesWritten(500)
            return 0
        probe.close()
        # A crashed Bench leaves its socket behind; clear it first.
        QtNetwork.QLocalServer.removeServer(server_name)
        server = QtNetwork.QLocalServer()
        server.listen(server_name)

        window = BenchWindow(workspace)
        log_startup("window created")

        def _on_new_connection() -> None:
            conn = server.nextPendingConnection()
            if conn is not None:
                conn.waitForReadyRead(500)
                conn.close()
            window.show()
            window.raise_()
            window.activateWindow()

        server.newConnection.connect(_on_new_connection)
        window.show()
        log_startup("window shown")
        return app.exec()

else:

    def main(workspace: str | Path | None = None) -> int:  # type: ignore[no-redef]
        print("The Bench window needs PySide6, and it is not installed here.")
        print("Fix: double-click Install SKULLPT in the SKULLPT folder -")
        print("it installs PySide6 into the shell's private .venv.")
        print("(Do not `pip install PySide6` - Mint's Python refuses it.)")
        print(f"(import error was: {QT_ERROR})")
        return 2
