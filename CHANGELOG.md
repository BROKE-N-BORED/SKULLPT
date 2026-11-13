# Design Shell — Changelog

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
