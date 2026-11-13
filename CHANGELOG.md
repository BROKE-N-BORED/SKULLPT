# SKULLPT — Changelog

## v1.2 — 2026-11-12 — the installer that actually installs, the SKULLPT name, a real program

v1.1's real-PC verdict: install.sh said "Done" but `./run.sh` failed —
PySide6 was never installed, and the fix it printed (`pip install
PySide6`) fails on Mint's externally-managed Python. The Bench had
never opened on the target PC. v1.2 fixes the cause, renames the
product, and makes it a program you can double-click.

- install.sh now CREATES a private `.venv` in the install folder and
  installs PySide6-Essentials into it (never `--user`, never
  `--break-system-packages` — both are wrong on Mint 3.12). If
  python3-venv is missing, install.sh says so plainly and names the
  fix (`sudo apt install python3.12-venv`); it never runs apt itself.
  run.sh prefers `.venv/bin/python` when it has PySide6, else falls
  back to system python3 — after a plain `./install.sh`, a bare
  `./run.sh` opens the Bench with no extra steps. The dependency
  report shows the PySide6 version honestly. The old "pip install
  PySide6" advice is gone from install.sh and from the Bench's
  error text, and the qtbench docstring no longer claims PySide6 is
  already on the machine.
- RENAME: the product is SKULLPT everywhere user-facing — window
  title, README, CHANGELOG, scripts, Bench, docs. The package is
  now `skullpt/`; a thin `designshell/` shim forwards to it so the
  Forge bridge and old `python3 -m designshell` commands keep
  working. The zip is `skullpt-v1.2.zip`, top-level folder `skullpt/`.
  Data paths are UNCHANGED (`~/design-shell-workspace`,
  `~/design-shell-models/`) so no data is orphaned; the Unreal
  destination is now `/Game/SKULLPT/<slug>/`.
- EXECUTABLE PROGRAM: `Install-SKULLPT.desktop` in the zip root —
  double-click it (right-click → Allow Launching first) and the
  installer runs in a terminal. install.sh writes a `SKULLPT.desktop`
  launcher to the Desktop and `~/.local/share/applications` (absolute
  install path in Exec, no nested quotes), links `~/.local/bin/skullpt`
  to run.sh so `skullpt` works as a command anywhere, and ships an
  original skull-motif SVG icon (`skullpt-icon.svg`).
- sd-cli + image model stay behind `--with-image-engine` (source
  build + 2.1 GB download); the report keeps pointing at the flag.

## v1.1 — 2026-11-12 — the cloud mesh step

Photo → textured 3D mesh, on Hugging Face's free GPU instead of the
PC's (which has no GPU worth the name).

- New module `designshell/cloudmesh.py` (stdlib only): a client for
  the official TRELLIS.2 demo Space (`microsoft/TRELLIS.2`, MIT,
  ZeroGPU) over the Gradio HTTP API — upload, `/preprocess_image`,
  `/image_to_3d`, `/extract_glb`, GLB download. The Space's API
  shape was read from its live `/gradio_api/info` on 2026-11-12;
  the Space ID is one module constant (`SPACE_ID`).
- The GLB lands in the job's folder and the existing ingest step
  derives the job's description from it, so the mesh rides the
  normal approve → build → render path. Without `--job` a new job
  is created; with `--job` the mesh lands in a job still waiting at
  the plan gate (an intake job keeps its reference photo on record).
- CLI: `cloud-mesh <workspace> <photo> [--job <slug>] [--seed N]`.
  Bench: `cloud-mesh` action + chat ("mesh <photo path>",
  "mesh this photo" for the selected job's reference photo). Engine
  status gains an HF-token chip/line in both Benches.
- Auth: `HF_TOKEN` from the environment only, never written to a
  file. No token → plain refusal naming the fix, before any network
  call. Every run discloses first that the photo leaves the machine.
- Quota exhausted → plain "daily free GPU time used up — try again
  tomorrow", job left intact. Space down, timeout, bad image, bad
  GLB back → plain-language errors; the mesh is generated and
  ingested before any job is touched, so a failed run leaves the
  workspace exactly as it was. `/extract_glb` is retried once in the
  same session (the generation is cached per session).
- The cloud step takes no heavy-job lock and checks no RAM floor —
  it loads no local model and is network-bound. The governor floor
  itself is untouched.
- Verified against a local mock Gradio server (20 new tests). No
  call against the real Hugging Face Space has been run from the
  build sandbox — the first real mesh is a real-PC verdict.

## v1.0 — 2026-11-11 — the Bench goes native

The Bench became a native Linux desktop window (Qt/PySide6): project
folders left, a real OpenGL live render viewport in the centre, chat
right. No web tech in the new window; the old web Bench moved to the
`webbench` command. Zip packaging fixed to store file-type bits so
GUI extractors keep the execute bit on the scripts.

## v0.9.1 — 2026-11-11 — launch fix

The web Bench opens the browser for you, and bare `./run.sh` starts
the Bench instead of the one-shot demo.

## v0.9 — 2026-11-11 — the Bench as one window

The web Bench rebuilt three-pane: folders left, Live Render Window
centre, chat right; chat routes to real actions.

## v0.8 — 2026-11-11 — the Bench (web)

First Bench: engine chips, job drafting/from-example, job cards,
reference images, Library, settings — one local web page.

## v0.7 — 2026-11-11 — the image step

Local automated lane (stable-diffusion.cpp + LCM DreamShaper) and a
manual Gemini-app handoff lane; 3 candidates per prompt, the keeper
becomes the job's locked reference; critic-vs-reference toggle.

## v0.6 — 2026-11-11 — "all of it"

Live seats (Gemini planner, Groq text, Gemini vision critic with
patch→rebuild), true shapes in the render window, lathe/tube/bevel,
procedural textures, repeat+mirror, game-ready gate, ingest (.glb /
.blend), photo intake, recipe graduation, Unreal packaging.

## v0.1–v0.5 — 2026-11-10/11 — the skeleton

Description File + plan gate, Live Render Window, Blender making
engine, shapes/materials, install.sh / run.sh.
