"""The Live Render Window.

Joshua's standing requirement (2026-11-10): a live render window where he
can EXAMINE anything built or being built while work is in progress — a
free inspection camera (tilt, rotate, spin on every axis, zoom, pan) plus
scene cameras HE can reposition, so the renders that get filed come from
angles he chose.

v1 renders the Description File's parts as true-scale boxes (position_cm /
size_cm, z-up) in a single self-contained HTML page — no dependencies, no
network. Served through the tiny local server below, the page polls the
description on disk, so edits (his, the shell's, a future Blender
exporter's) appear in the window WITHOUT resetting his camera. Cameras he
sets are saved back into the Description File itself, so they travel with
the asset into the Library.

When real mesh exporters land, this same window swaps boxes for meshes;
the camera contract (inspection view + named scene cameras stored in the
description) does not change.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import description as desc_mod
from .expand import expand_parts


# ---------------------------------------------------------------- geometry

def part_box(part: dict, index: int) -> dict:
    """Return {id, type, min:[x,y,z], size:[w,d,h], auto:bool, ...} for a part.

    Parts without position/size (e.g. flat UI descriptions) are auto-placed
    in a row so they still show up; `auto` flags that honestly. Lathe parts
    derive their footprint from the profile (position_cm is the centre of
    the base); tube parts derive theirs from the path's bounding box.
    """
    shape = part.get("shape", "box")
    pos = part.get("position_cm")
    size = part.get("size_cm")
    auto = False
    if shape == "lathe" and isinstance(part.get("profile_cm"), list) and part["profile_cm"]:
        radii = [float(pt[0]) for pt in part["profile_cm"]]
        heights = [float(pt[1]) for pt in part["profile_cm"]]
        r_max, h_max = max(radii), max(heights)
        if isinstance(pos, list) and len(pos) == 3:
            pos = [pos[0] - r_max, pos[1] - r_max, pos[2]]
        size = [2 * r_max, 2 * r_max, h_max]
    elif shape == "tube" and isinstance(part.get("path_cm"), list) and part["path_cm"]:
        radius = float(part.get("radius_cm", 2))
        path = part["path_cm"]
        pos = [min(float(pt[i]) for pt in path) - radius for i in range(3)]
        size = [max(float(pt[i]) for pt in path) - min(float(pt[i]) for pt in path) + 2 * radius
                for i in range(3)]
    if not (isinstance(pos, list) and len(pos) == 3):
        pos = [index * 130, 0, 0]
        auto = True
    if not (isinstance(size, list) and len(size) == 3):
        size = [100, 60, 100]
        auto = True
    return {
        "id": part.get("id", f"part{index}"),
        "type": part.get("type", "part"),
        "min": [float(v) for v in pos],
        "size": [float(v) for v in size],
        "auto": auto,
        "shape": shape,
        "profile": part.get("profile_cm"),
        "path": part.get("path_cm"),
        "radius": part.get("radius_cm"),
    }


def scene_bounds(boxes: list[dict]) -> dict:
    """Bounding box + center of the whole scene, in cm."""
    if not boxes:
        return {"min": [0, 0, 0], "max": [100, 100, 100], "center": [50, 50, 50], "span": 100.0}
    lo = [min(b["min"][i] for b in boxes) for i in range(3)]
    hi = [max(b["min"][i] + b["size"][i] for b in boxes) for i in range(3)]
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3)) or 100.0
    return {"min": lo, "max": hi, "center": center, "span": float(span)}


def scene_data(desc: dict) -> dict:
    """Everything the viewer page needs, as one JSON-able dict."""
    boxes = [part_box(p, i) for i, p in enumerate(expand_parts(desc))]
    cameras = []
    for cam in desc.get("cameras", []) or []:
        if isinstance(cam, dict) and "name" in cam:
            cameras.append({
                "name": str(cam["name"]),
                "position_cm": [float(v) for v in cam.get("position_cm", [0, 0, 0])],
                "look_at_cm": [float(v) for v in cam.get("look_at_cm", [0, 0, 0])],
            })
    return {
        "name": desc.get("name", "Untitled"),
        "kind": desc.get("kind", ""),
        "style": desc.get("style", {}) or {},
        "boxes": boxes,
        "bounds": scene_bounds(boxes),
        "cameras": cameras,
    }


# ----------------------------------------------------------------- cameras

def _vec3(value, field: str) -> list[float]:
    if not (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) for v in value)):
        raise desc_mod.DescriptionError(f"camera {field} must be [x, y, z] numbers")
    return [float(v) for v in value]


def upsert_camera(desc: dict, camera: dict) -> dict:
    """Insert or replace a named camera in a description (in place)."""
    name = camera.get("name")
    if not isinstance(name, str) or not name.strip():
        raise desc_mod.DescriptionError("camera needs a non-empty name")
    entry = {
        "name": name.strip(),
        "position_cm": _vec3(camera.get("position_cm"), "position_cm"),
        "look_at_cm": _vec3(camera.get("look_at_cm"), "look_at_cm"),
    }
    cameras = desc.setdefault("cameras", [])
    for i, cam in enumerate(cameras):
        if isinstance(cam, dict) and cam.get("name") == entry["name"]:
            cameras[i] = entry
            return entry
    cameras.append(entry)
    return entry


def delete_camera(desc: dict, name: str) -> bool:
    cameras = desc.get("cameras", []) or []
    kept = [c for c in cameras if not (isinstance(c, dict) and c.get("name") == name)]
    desc["cameras"] = kept
    return len(kept) != len(cameras)


# -------------------------------------------------------------------- page

_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>__TITLE__ — Live Render Window</title>
<style>
  html, body { margin:0; padding:0; height:100%; background:#14100c; color:#f2ede4;
    font-family: ui-monospace, Menlo, Consolas, monospace; overflow:hidden; }
  #top { position:fixed; top:0; left:0; right:0; padding:8px 12px; display:flex;
    gap:12px; align-items:baseline; background:rgba(20,16,12,.92); z-index:5;
    border-bottom:1px solid #3a2f22; flex-wrap:wrap; }
  #top b { font-size:15px; letter-spacing:.04em; }
  #status { color:#c9a86a; font-size:12px; }
  #cv { position:fixed; inset:0; touch-action:none; display:block; }
  #panel { position:fixed; top:44px; right:0; bottom:0; width:228px; overflow-y:auto;
    background:rgba(24,19,14,.95); border-left:1px solid #3a2f22; padding:10px; z-index:5;
    font-size:12px; }
  #panel h3 { margin:10px 0 6px; font-size:11px; text-transform:uppercase;
    letter-spacing:.12em; color:#c9a86a; }
  #panel button { background:#2b2119; color:#f2ede4; border:1px solid #57452c;
    padding:5px 8px; margin:2px 2px 2px 0; cursor:pointer; font:inherit; font-size:12px; }
  #panel button:hover { background:#3a2d1f; }
  #panel button.sel { border-color:#e0a458; color:#e0a458; }
  #panel input { background:#171310; color:#f2ede4; border:1px solid #57452c;
    padding:5px; width:96%; font:inherit; font-size:12px; margin:2px 0; }
  .row { padding:4px 6px; cursor:pointer; border:1px solid transparent; }
  .row:hover { background:#241c14; }
  .row.sel { border-color:#e0a458; }
  .hint { color:#9b8b74; font-size:11px; line-height:1.5; }
  #togglePanel { position:fixed; top:48px; right:8px; z-index:6; display:none;
    background:#2b2119; color:#f2ede4; border:1px solid #57452c; padding:6px 10px; font:inherit; }
  @media (max-width: 700px) {
    #panel { display:none; width:200px; }
    #panel.open { display:block; }
    #togglePanel { display:block; }
  }
</style>
</head>
<body>
<div id="top"><b>__TITLE__</b><span id="kind"></span><span id="status">loading…</span></div>
<canvas id="cv"></canvas>
<button id="togglePanel">Panel</button>
<div id="panel">
  <h3>Views</h3>
  <div>
    <button data-view="iso">Iso</button><button data-view="front">Front</button>
    <button data-view="side">Side</button><button data-view="top">Top</button>
  </div>
  <h3>Examine</h3>
  <div class="hint">Drag: orbit / tilt / spin<br>Right-drag or Shift-drag: pan<br>
  Scroll or pinch: zoom<br>Click a part below to focus it.</div>
  <label class="hint"><input type="checkbox" id="optLabels" checked style="width:auto"> labels</label>
  <label class="hint"><input type="checkbox" id="optGrid" checked style="width:auto"> ground grid</label>
  <h3>Parts</h3>
  <div id="parts"></div>
  <h3>Scene cameras</h3>
  <div id="cameras"></div>
  <input id="camName" placeholder="new camera name">
  <button id="addCam">Add camera at current view</button>
  <button id="setCam">Set selected camera to current view</button>
  <button id="delCam">Delete selected camera</button>
  <div class="hint" id="camNote"></div>
</div>
<script>
"use strict";
var LIVE = __LIVE__;
var DATA = __DATA__;
var cv = document.getElementById("cv"), ctx = cv.getContext("2d");
var view = { az: -0.7, el: 0.55, dist: 900, tx: 0, ty: 0, tz: 0 };
var selPart = null, selCam = null;
var FOV = 0.8;

function resetViewToScene() {
  var b = DATA.bounds;
  view.tx = b.center[0]; view.ty = b.center[1]; view.tz = b.center[2];
  view.dist = Math.max(200, b.span * 1.9);
  view.az = -0.7; view.el = 0.55;
}
resetViewToScene();

function camPos() {
  var ce = Math.cos(view.el), se = Math.sin(view.el);
  return [view.tx + view.dist * ce * Math.sin(view.az),
          view.ty + view.dist * ce * Math.cos(view.az),
          view.tz + view.dist * se];
}
function basis() {
  var p = camPos(), t = [view.tx, view.ty, view.tz];
  var f = [t[0]-p[0], t[1]-p[1], t[2]-p[2]];
  var fl = Math.hypot(f[0], f[1], f[2]) || 1; f = [f[0]/fl, f[1]/fl, f[2]/fl];
  var r = [f[1], -f[0], 0]; var rl = Math.hypot(r[0], r[1], r[2]);
  if (rl < 1e-6) { r = [1, 0, 0]; } else { r = [r[0]/rl, r[1]/rl, 0]; }
  var u = [r[1]*f[2]-r[2]*f[1], r[2]*f[0]-r[0]*f[2], r[0]*f[1]-r[1]*f[0]];
  return { p: p, f: f, r: r, u: u };
}
function project(pt, B, W, H) {
  var v = [pt[0]-B.p[0], pt[1]-B.p[1], pt[2]-B.p[2]];
  var z = v[0]*B.f[0] + v[1]*B.f[1] + v[2]*B.f[2];
  if (z < 1) return null;
  var x = v[0]*B.r[0] + v[1]*B.r[1] + v[2]*B.r[2];
  var y = v[0]*B.u[0] + v[1]*B.u[1] + v[2]*B.u[2];
  var fpx = (H/2) / Math.tan(FOV/2);
  return [W/2 + x*fpx/z, H/2 - y*fpx/z, z];
}
function shade(hex, k) {
  var n = parseInt(hex.slice(1), 16);
  var r = Math.min(255, ((n>>16)&255)*k), g = Math.min(255, ((n>>8)&255)*k),
      b = Math.min(255, (n&255)*k);
  return "rgb(" + (r|0) + "," + (g|0) + "," + (b|0) + ")";
}
var PALETTE = ["#b0763a","#7d8c5c","#5c7d8c","#8c5c74","#a39a4f","#6b5ca5",
               "#4f8c72","#a55c4f","#718355","#946b54","#546e7a","#7a5c8f"];
function partColor(box) {
  var h = 0; for (var i = 0; i < box.id.length; i++) h = (h*31 + box.id.charCodeAt(i)) >>> 0;
  if (box.type === "floor" || box.type === "ground") return "#4a3d2c";
  return PALETTE[h % PALETTE.length];
}
function boxFaces(box) {
  var x = box.min[0], y = box.min[1], z = box.min[2];
  var w = box.size[0], d = box.size[1], h = box.size[2];
  var c = [[x,y,z],[x+w,y,z],[x+w,y+d,z],[x,y+d,z],
           [x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]];
  return [
    { pts: [c[0],c[1],c[2],c[3]], n: [0,0,-1], k: 0.45 },
    { pts: [c[4],c[5],c[6],c[7]], n: [0,0,1],  k: 1.0 },
    { pts: [c[0],c[1],c[5],c[4]], n: [0,-1,0], k: 0.7 },
    { pts: [c[2],c[3],c[7],c[6]], n: [0,1,0],  k: 0.6 },
    { pts: [c[1],c[2],c[6],c[5]], n: [1,0,0],  k: 0.82 },
    { pts: [c[3],c[0],c[4],c[7]], n: [-1,0,0], k: 0.55 }
  ];
}

var LIGHT = [-0.35, -0.55, 0.76];
function faceShade(pts) {
  if (pts.length < 3) return 0.7;
  var a = pts[0], b = pts[1], c = pts[2];
  var u = [b[0]-a[0], b[1]-a[1], b[2]-a[2]], v = [c[0]-a[0], c[1]-a[1], c[2]-a[2]];
  var n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
  var l = Math.hypot(n[0], n[1], n[2]) || 1;
  var d = Math.abs((n[0]*LIGHT[0] + n[1]*LIGHT[1] + n[2]*LIGHT[2]) / l);
  return 0.45 + 0.55 * d;
}
function prismFaces(rings) {
  // rings: array of rings, each ring an array of [x,y,z] points (same length)
  var faces = [];
  function add(pts) { if (pts.length >= 3) faces.push({ pts: pts, k: faceShade(pts) }); }
  for (var r = 0; r + 1 < rings.length; r++) {
    var a = rings[r], b = rings[r + 1];
    for (var i = 0; i < a.length; i++) {
      var j = (i + 1) % a.length;
      add([a[i], a[j], b[j], b[i]]);
    }
  }
  add(rings[0].slice().reverse());
  add(rings[rings.length - 1]);
  return faces;
}
function ringPoints(cx, cy, z, rx, ry, seg) {
  var pts = [];
  for (var i = 0; i < seg; i++) {
    var t = (i / seg) * Math.PI * 2;
    pts.push([cx + rx * Math.cos(t), cy + ry * Math.sin(t), z]);
  }
  return pts;
}
function shapeFaces(box) {
  var x = box.min[0], y = box.min[1], z = box.min[2];
  var w = box.size[0], d = box.size[1], h = box.size[2];
  var cx = x + w / 2, cy = y + d / 2;
  if (box.shape === "cylinder") {
    return prismFaces([ringPoints(cx, cy, z, w / 2, d / 2, 14),
                       ringPoints(cx, cy, z + h, w / 2, d / 2, 14)]);
  }
  if (box.shape === "sphere") {
    var rings = [], lats = [-90, -50, -20, 0, 20, 50, 90];
    lats.forEach(function (lat) {
      var t = lat * Math.PI / 180, rr = Math.cos(t);
      rings.push(ringPoints(cx, cy, z + h / 2 + (h / 2) * Math.sin(t),
                            (w / 2) * rr, (d / 2) * rr, 12));
    });
    return prismFaces(rings);
  }
  if (box.shape === "extrude" && box.profile) {
    var bot = box.profile.map(function (pt) { return [x + pt[0], y + pt[1], z]; });
    var top = box.profile.map(function (pt) { return [x + pt[0], y + pt[1], z + h]; });
    return prismFaces([bot, top]);
  }
  if (box.shape === "lathe" && box.profile) {
    var lrings = box.profile.map(function (pt) {
      return ringPoints(cx, cy, z + pt[1], Math.max(pt[0], 0.01), Math.max(pt[0], 0.01), 12);
    });
    return prismFaces(lrings);
  }
  if (box.shape === "tube" && box.path) {
    var r = box.radius || 2, trings = [];
    for (var s = 0; s < box.path.length; s++) {
      var p = box.path[s];
      var p0 = box.path[Math.max(0, s - 1)], p1 = box.path[Math.min(box.path.length - 1, s + 1)];
      var dir = [p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]];
      var dl = Math.hypot(dir[0], dir[1], dir[2]) || 1; dir = [dir[0]/dl, dir[1]/dl, dir[2]/dl];
      var up = Math.abs(dir[2]) > 0.9 ? [1, 0, 0] : [0, 0, 1];
      var side = [dir[1]*up[2]-dir[2]*up[1], dir[2]*up[0]-dir[0]*up[2], dir[0]*up[1]-dir[1]*up[0]];
      var sl = Math.hypot(side[0], side[1], side[2]) || 1; side = [side[0]/sl, side[1]/sl, side[2]/sl];
      var upv = [side[1]*dir[2]-side[2]*dir[1], side[2]*dir[0]-side[0]*dir[2], side[0]*dir[1]-side[1]*dir[0]];
      var ring = [];
      for (var i2 = 0; i2 < 8; i2++) {
        var t2 = (i2 / 8) * Math.PI * 2;
        ring.push([p[0] + r * (Math.cos(t2) * side[0] + Math.sin(t2) * upv[0]),
                   p[1] + r * (Math.cos(t2) * side[1] + Math.sin(t2) * upv[1]),
                   p[2] + r * (Math.cos(t2) * side[2] + Math.sin(t2) * upv[2])]);
      }
      trings.push(ring);
    }
    return prismFaces(trings);
  }
  return boxFaces(box);
}

function resize() {
  cv.width = window.innerWidth; cv.height = window.innerHeight;
}
window.addEventListener("resize", resize); resize();

function draw() {
  var W = cv.width, H = cv.height, B = basis();
  ctx.fillStyle = (DATA.style && DATA.style.surface) ? "#14100c" : "#14100c";
  ctx.fillRect(0, 0, W, H);
  if (document.getElementById("optGrid").checked) {
    var z0 = DATA.bounds.min[2], step = 50;
    ctx.strokeStyle = "rgba(224,164,88,0.16)"; ctx.lineWidth = 1;
    for (var gx = Math.floor(DATA.bounds.min[0]/step)*step; gx <= DATA.bounds.max[0]; gx += step) {
      var a = project([gx, DATA.bounds.min[1], z0], B, W, H),
          b = project([gx, DATA.bounds.max[1], z0], B, W, H);
      if (a && b) { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    }
    for (var gy = Math.floor(DATA.bounds.min[1]/step)*step; gy <= DATA.bounds.max[1]; gy += step) {
      var c = project([DATA.bounds.min[0], gy, z0], B, W, H),
          d = project([DATA.bounds.max[0], gy, z0], B, W, H);
      if (c && d) { ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.stroke(); }
    }
  }
  var faces = [];
  DATA.boxes.forEach(function (box) {
    var src = (box.shape && box.shape !== "box") ? shapeFaces(box) : boxFaces(box);
    src.forEach(function (f) {
      var cx = 0, cy = 0, cz = 0, n = f.pts.length;
      f.pts.forEach(function (p) { cx += p[0]/n; cy += p[1]/n; cz += p[2]/n; });
      var v = [cx-B.p[0], cy-B.p[1], cz-B.p[2]];
      var depth = v[0]*B.f[0] + v[1]*B.f[1] + v[2]*B.f[2];
      faces.push({ box: box, f: f, depth: depth });
    });
  });
  faces.sort(function (a, b) { return b.depth - a.depth; });
  faces.forEach(function (it) {
    var pts = [], ok = true;
    it.f.pts.forEach(function (p) {
      var s = project(p, B, W, H); if (!s) { ok = false; } else { pts.push(s); }
    });
    if (!ok) return;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.closePath();
    var col = partColor(it.box);
    if (selPart === it.box.id) col = "#e0a458";
    ctx.fillStyle = shade(col, it.f.k);
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.45)"; ctx.lineWidth = 1; ctx.stroke();
  });
  if (document.getElementById("optLabels").checked) {
    ctx.font = "12px ui-monospace, monospace"; ctx.textAlign = "center";
    DATA.boxes.forEach(function (box) {
      var top = [box.min[0] + box.size[0]/2, box.min[1] + box.size[1]/2, box.min[2] + box.size[2] + 6];
      var s = project(top, B, W, H);
      if (s) { ctx.fillStyle = "#f2ede4"; ctx.fillText(box.id + (box.auto ? " (auto-placed)" : ""), s[0], s[1]); }
    });
  }
  DATA.cameras.forEach(function (cam) {
    var s = project(cam.position_cm, B, W, H), t = project(cam.look_at_cm, B, W, H);
    if (!s) return;
    ctx.strokeStyle = (selCam === cam.name) ? "#e0a458" : "rgba(242,237,228,0.5)";
    if (t) { ctx.beginPath(); ctx.moveTo(s[0], s[1]); ctx.lineTo(t[0], t[1]); ctx.stroke(); }
    ctx.fillStyle = (selCam === cam.name) ? "#e0a458" : "#f2ede4";
    ctx.beginPath();
    ctx.moveTo(s[0], s[1]-6); ctx.lineTo(s[0]+6, s[1]); ctx.lineTo(s[0], s[1]+6); ctx.lineTo(s[0]-6, s[1]);
    ctx.closePath(); ctx.fill();
    ctx.font = "11px ui-monospace, monospace"; ctx.textAlign = "center";
    ctx.fillText("cam: " + cam.name, s[0], s[1] - 10);
  });
  requestAnimationFrame(draw);
}
requestAnimationFrame(draw);

var pointers = new Map(), lastPinch = 0;
cv.addEventListener("contextmenu", function (e) { e.preventDefault(); });
cv.addEventListener("pointerdown", function (e) {
  cv.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2 });
});
cv.addEventListener("pointermove", function (e) {
  if (!pointers.has(e.pointerId)) return;
  var prev = pointers.get(e.pointerId);
  var dx = e.clientX - prev.x, dy = e.clientY - prev.y;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, pan: prev.pan });
  if (pointers.size === 2) {
    var ps = Array.from(pointers.values());
    var d = Math.hypot(ps[0].x - ps[1].x, ps[0].y - ps[1].y);
    if (lastPinch > 0) view.dist = clampDist(view.dist * (lastPinch / d));
    lastPinch = d; return;
  }
  if (prev.pan) {
    var B = basis(), s = view.dist * 0.0016;
    view.tx -= dx * s * B.r[0]; view.ty -= dx * s * B.r[1]; view.tz -= dx * s * B.r[2];
    view.tx += dy * s * B.u[0]; view.ty += dy * s * B.u[1]; view.tz += dy * s * B.u[2];
  } else {
    view.az -= dx * 0.006;
    view.el += dy * 0.006;
    if (view.el > 1.53) view.el = 1.53;
    if (view.el < -1.53) view.el = -1.53;
  }
});
function endPointer(e) { pointers.delete(e.pointerId); if (pointers.size < 2) lastPinch = 0; }
cv.addEventListener("pointerup", endPointer);
cv.addEventListener("pointercancel", endPointer);
cv.addEventListener("wheel", function (e) {
  e.preventDefault();
  view.dist = clampDist(view.dist * Math.exp(e.deltaY * 0.0012));
}, { passive: false });
function clampDist(d) { return Math.min(30000, Math.max(30, d)); }

function setView(name) {
  if (name === "iso") { view.az = -0.7; view.el = 0.55; }
  if (name === "front") { view.az = Math.PI; view.el = 0.12; }
  if (name === "side") { view.az = Math.PI / 2; view.el = 0.12; }
  if (name === "top") { view.az = -0.7; view.el = 1.52; }
}
document.querySelectorAll("[data-view]").forEach(function (btn) {
  btn.addEventListener("click", function () { setView(btn.getAttribute("data-view")); });
});
document.getElementById("togglePanel").addEventListener("click", function () {
  document.getElementById("panel").classList.toggle("open");
});

function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
function buildPanel() {
  document.getElementById("kind").textContent = DATA.kind ? "[" + DATA.kind + "]" : "";
  var ph = "";
  DATA.boxes.forEach(function (box) {
    ph += "<div class='row" + (selPart === box.id ? " sel" : "") + "' data-part='" + esc(box.id) + "'>"
        + esc(box.id) + " <span class='hint'>" + esc(box.type) + "</span></div>";
  });
  document.getElementById("parts").innerHTML = ph || "<div class='hint'>no parts</div>";
  document.querySelectorAll("[data-part]").forEach(function (row) {
    row.addEventListener("click", function () {
      selPart = row.getAttribute("data-part");
      DATA.boxes.forEach(function (box) {
        if (box.id === selPart) {
          view.tx = box.min[0] + box.size[0]/2;
          view.ty = box.min[1] + box.size[1]/2;
          view.tz = box.min[2] + box.size[2]/2;
          view.dist = Math.max(120, Math.max(box.size[0], box.size[1], box.size[2]) * 3);
        }
      });
      buildPanel();
    });
  });
  var ch = "";
  DATA.cameras.forEach(function (cam) {
    ch += "<div class='row" + (selCam === cam.name ? " sel" : "") + "' data-cam='" + esc(cam.name) + "'>"
        + esc(cam.name) + "</div>";
  });
  document.getElementById("cameras").innerHTML = ch || "<div class='hint'>no cameras yet — fly to an angle you like and add one</div>";
  document.querySelectorAll("[data-cam]").forEach(function (row) {
    row.addEventListener("click", function () {
      selCam = row.getAttribute("data-cam");
      DATA.cameras.forEach(function (cam) {
        if (cam.name === selCam) {
          var dx = cam.position_cm[0] - cam.look_at_cm[0],
              dy = cam.position_cm[1] - cam.look_at_cm[1],
              dz = cam.position_cm[2] - cam.look_at_cm[2];
          view.dist = clampDist(Math.hypot(dx, dy, dz) || 500);
          view.el = Math.asin(Math.max(-1, Math.min(1, dz / view.dist)));
          view.az = Math.atan2(dx, dy);
          view.tx = cam.look_at_cm[0]; view.ty = cam.look_at_cm[1]; view.tz = cam.look_at_cm[2];
        }
      });
      buildPanel();
    });
  });
  document.getElementById("camNote").textContent = LIVE
    ? "Cameras save into the Description File itself, so they travel with the asset."
    : "Snapshot page: camera saving is off. Run the shell's view command for the live window.";
  var dis = !LIVE;
  ["addCam", "setCam", "delCam"].forEach(function (id) { document.getElementById(id).disabled = dis; });
}
function currentCam(name) {
  var p = camPos();
  function r(v) { return Math.round(v * 10) / 10; }
  return { name: name, position_cm: [r(p[0]), r(p[1]), r(p[2])],
           look_at_cm: [r(view.tx), r(view.ty), r(view.tz)] };
}
function postCam(payload, done) {
  fetch("/api/cameras", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload) })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      document.getElementById("status").textContent = res.ok ? "camera saved: " + (payload.name || payload.delete) : ("save failed: " + (res.error || "?"));
      if (res.ok) poll(true);
      if (done) done(res);
    })
    .catch(function () { document.getElementById("status").textContent = "save failed: server unreachable"; });
}
document.getElementById("addCam").addEventListener("click", function () {
  var name = document.getElementById("camName").value.trim() || ("Camera " + (DATA.cameras.length + 1));
  selCam = name;
  postCam(currentCam(name));
});
document.getElementById("setCam").addEventListener("click", function () {
  if (!selCam) { document.getElementById("status").textContent = "pick a camera first"; return; }
  postCam(currentCam(selCam));
});
document.getElementById("delCam").addEventListener("click", function () {
  if (!selCam) { document.getElementById("status").textContent = "pick a camera first"; return; }
  var gone = selCam; selCam = null;
  postCam({ delete: gone });
});
var lastHash = "";
function hashData(d) {
  return d.boxes.length + "|" + d.cameras.length + "|" +
    d.boxes.map(function (b) { return b.min.join(",") + ":" + b.size.join(",") + ":" + (b.shape || "box"); }).join(";") +
    d.cameras.map(function (c) { return c.position_cm.join(","); }).join(";");
}
function poll(force) {
  if (!LIVE) return;
  fetch("/api/scene").then(function (r) { return r.json(); }).then(function (d) {
    var h = hashData(d);
    if (force || h !== lastHash) {
      var keepCam = selCam, keepPart = selPart;
      DATA = d; selCam = keepCam; selPart = keepPart;
      lastHash = h; buildPanel();
      document.getElementById("status").textContent =
        "live — watching the description file (updated " + new Date().toLocaleTimeString() + ")";
    }
  }).catch(function () {
    document.getElementById("status").textContent = "live — server unreachable, retrying…";
  });
}
buildPanel();
lastHash = hashData(DATA);
document.getElementById("status").textContent = LIVE
  ? "live — watching the description file" : "snapshot — saved page, not live";
if (LIVE) setInterval(function () { poll(false); }, 2000);
</script>
</body>
</html>
"""


def render_page(desc: dict, *, live: bool) -> str:
    data = scene_data(desc)
    title = data["name"].replace("<", "").replace(">", "")
    page = _PAGE.replace("__TITLE__", title)
    page = page.replace("__LIVE__", "true" if live else "false")
    page = page.replace("__DATA__", json.dumps(data))
    return page


# ------------------------------------------------------------------ server

def make_server(description_path: str | Path, host: str = "127.0.0.1", port: int = 8760):
    """HTTP server bound to ONE Description File.

    GET  /              the live viewer page
    GET  /api/scene     current scene data (the page polls this)
    POST /api/cameras   {name, position_cm, look_at_cm} upsert, or {delete: name}
    Camera writes go through description.dump, so a bad write can never
    corrupt the file — it validates first or does not write at all.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    path = Path(description_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict):
            self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

        def do_GET(self):
            try:
                if self.path == "/":
                    desc = desc_mod.load(path)
                    self._send(200, render_page(desc, live=True).encode(), "text/html; charset=utf-8")
                elif self.path == "/api/scene":
                    self._json(200, scene_data(desc_mod.load(path)))
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            except Exception as exc:  # surface the error, never hang the page
                self._json(500, {"ok": False, "error": str(exc)})

        def do_POST(self):
            if self.path != "/api/cameras":
                self._json(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                desc = desc_mod.load(path)
                if "delete" in payload:
                    removed = delete_camera(desc, str(payload["delete"]))
                    desc_mod.dump(desc, path)
                    self._json(200, {"ok": removed, "cameras": desc.get("cameras", [])})
                else:
                    entry = upsert_camera(desc, payload)
                    desc_mod.dump(desc, path)
                    self._json(200, {"ok": True, "camera": entry, "cameras": desc.get("cameras", [])})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def serve(description_path: str | Path, host: str = "127.0.0.1", port: int = 8760) -> str:
    """Serve the live window for a description; returns the URL. Blocks."""
    server = make_server(description_path, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Live Render Window: {url}")
    print(f"Watching: {description_path}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url
