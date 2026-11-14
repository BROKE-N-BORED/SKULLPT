# SKULLPT — Changelog

## v1.4 — 2026-11-13 — the audit fixes: governor, conversation, budgets, snapshots

The 2026-11-13 audit found three dead paths and a stranded-job trap;
v1.4 fixes all of them, plus the small weaknesses from the same audit
and the installer performance item.

- SECURITY HYGIENE (Joshua's rule, 2026-11-13): sensitive information
  and APIs are never in code. The tree was audited — all keys
  (GEMINI_API_KEY, GROQ_API_KEY, HF_TOKEN) already come from the
  environment only, and no secret was found in any shipped version
  (v0.1–v1.3 all clean). Added `.gitignore` covering `.env` files,
  key/credential files, and local install artifacts, so a secret
  can never be committed by accident. This rides into the release zip.
- GOVERNOR REFUSAL NO LONGER STRANDS JOBS (T1): a build the governor
  refuses (low RAM, another heavy job running) stays approved and the
  refusal is logged as `governor_refused` — it used to be marked
  failed, which stranded it in a state no approval could rescue. New
  `retry`: a failed job returns to approved — the Retry button in the
  Bench (enabled only for failed jobs), the "retry" chat verb, the
  `retry` Bench action, and `python3 -m skullpt retry <workspace>
  <job>`. Retrying clears the recorded failure.
- OPEN CONVERSATION WORKS (T2): the chat's open-conversation path was
  dead — it called `live._require_key`, which did not exist, and
  passed `gemini_text` the whole seats dict plus a `system` kwarg it
  does not take. The key check is now a real environment check on
  the planner seat's key, and the call passes the planner seat dict
  with the workshop context folded into the prompt text. With
  GEMINI_API_KEY set, plain talk answers; without it the chat still
  says so plainly instead of faking a brain.
- ZERO EXPORTERS FAIL LOUDLY (T3): character and project kinds map to
  no exporters, so their builds "succeeded" with zero outputs. Builds
  now fail before the lock is taken (stage `exporters`, plain reason),
  and the machine checks gained an `outputs_present` check — zero
  outputs is never ok.
- ONE BUDGET KEY: `max_tris` (T4): the governor read `max_triangles`
  while the planner, the game-ready gate, and the examples used
  `max_tris`, so the pre-build gate summed zeros and never fired.
  The key is `max_tris` everywhere (`max_triangles` is still honored
  as a legacy alias when `max_tris` is absent), and the gate now
  estimates triangles from the shapes — box 12, cylinder 92, sphere
  528, extrude/lathe/tube from their profiles — over the
  repeat/mirror expansion. The estimate mirrors Blender's base-mesh
  builders exactly, so it is a lower bound: the gate refuses only
  when the build is certainly over budget; the game-ready gate
  enforces the exact count post-build. The tavern-room example now
  uses `max_tris` and its hand-written per-part `triangles` fields
  are gone. (Craft stall estimates 1,356 vs 2,668 real; tavern room
  36 vs a 500,000 budget.)
- DESCRIPTION SNAPSHOTS (T5): approving a job writes
  `description.approved.json`; the vision critic's patches write
  `description.before-vision-patch.json` before the first patch
  lands; cloud-mesh writes `description.before-cloud-mesh.json`
  before overwriting an existing job's description.
- INSTALLER DOWNLOADS RESUME AND VERIFY (T6): the Blender tarball
  and the image model download with `curl -C -` (an interrupted run
  picks up where it left off) and are SHA-256 verified against hashes
  recorded in install.sh (Blender 4.5.9 and LCM_Dreamshaper_v7-f16).
  A file already present with a matching hash is left alone; a hash
  mismatch deletes the file and retries once from scratch, then
  fails with a plain message. The Blender tarball now downloads to a
  persistent cache (`~/.cache/skullpt`) instead of a mktemp dir, so
  resume works across runs. `--with-blender` never downgrades: a
  newer installed Blender is left alone with a plain note.
- `act()` RETURNS `"job"` (T7): approve/build/file/plan/new-example/
  retry all return the job name as a field; the detail-string
  splitting in bench.py and qtbench.py is gone.
- SMALL FIXES: the skull example is gated in code — the Bench never
  offers `examples/skull.description.json` (the file is untouched);
  the chat transcript is saved to the workspace (`chat.jsonl`) and
  survives a restart; library thumbnails use the first render PNG,
  falling back to the SVG mock; jobs and image batches the Bench
  cannot read are listed with their reasons in the UI ("Couldn't
  read") instead of silently vanishing (U32); the governor lock is
  taken atomically via O_EXCL with stale-lock retry, and the RAM
  floor is re-checked between exporters mid-build.
- IMAGE-ENGINE BUILD CAPPED (P5): the stable-diffusion.cpp compile
  runs at `-j2` on machines with 8 GB of RAM or less (a full
  `-j$(nproc)` compile can eat the whole machine); bigger machines
  still use all their cores.
- TESTS: 57 new tests in `tests/test_v14.py` (governor refusal +
  retry, open conversation against a mock Gemini server, zero
  exporters, triangle estimates, snapshots, installer text checks +
  staged installer runs with stub commands, act() job field, skull
  exclusion, chat persistence, thumbnails, skipped entries, lock
  atomicity + re-check, Qt Retry button). The two `max_triangles`
  tests in test_core.py now use `max_tris`.

## v1.3 — 2026-11-12 — no terminal at all: GUI installer, python3-venv self-heal

Joshua's rule: "I should not need to use the terminal at all to run
or install anything period." v1.2's installer still broke it — if
python3-venv was missing it printed an apt command for the user to
type, and the installer itself ran in a terminal window. v1.3 closes
both gaps.

- GUI INSTALLER: `Install-SKULLPT.desktop` is now Terminal=false and
  launches `install.sh --gui`. The install runs behind a progress
  window (per-step percentages), shows an info dialog on success and
  asks "SKULLPT is installed. Open it now?" (yes opens the Bench),
  and shows a plain-language error dialog with the log path on
  failure. All install output is logged to `install.log` in the
  install folder. The dialog tool is the first found in the order
  Cinnamon's own spices probe — xenity, zenity, yad (xenity is
  Mint's maintained Zenity fork; on a fully-updated Mint 22.x it
  may have replaced zenity). If no dialog tool is installed, the
  installer falls back to the terminal installer — the install
  still works and is still logged; that is the documented degraded
  path, not the plan. `install.sh` without flags auto-detects:
  no terminal + a display + a dialog tool means GUI mode.
- PYTHON3-VENV SELF-HEAL: when `python3 -m venv` fails because
  python3-venv is missing, the installer installs that one system
  package itself with ONE pkexec call (a GUI password prompt, no
  terminal) — `apt-get update && DEBIAN_FRONTEND=noninteractive
  apt-get install -y python3.12-venv` — and retries venv creation
  automatically. If the user declines the prompt or apt fails, the
  installer says so in plain language ("SKULLPT needs one system
  package to install — re-run the installer and allow it when
  asked") and the Bench stays unavailable until then; the CLI still
  works. The image-engine build tools (git, cmake, C++ compiler)
  heal the same way, and the Blender / sd.cpp installs to /opt use
  pkexec too. No terminal command appears in any
  user-facing message, and the installer never asks the user to
  type anything.
- NO-TERMINAL AUDIT: every user-facing string in install.sh, run.sh,
  qtbench.py, bench.py, imagegen.py, the README, and the desktop
  files was checked — the install/run path requires no terminal
  command. The Bench's missing-PySide6 error now says to
  double-click Install SKULLPT; the image-engine messages point at
  the installer's image-engine option and the README's install
  options. The README documents the terminal route only in a
  clearly labeled optional subsection ("Terminal reference
  (optional — advanced users)"). The HF_TOKEN setup for the cloud
  mesh step is unchanged (optional feature config, not install/run).
- TEST-FAILURE OUTPUT KEPT: if the install-time test suite reports a
  problem, its full output is now kept as `test-output.log` in the
  SKULLPT folder and the message names it (the old message said
  "see above" while the evidence was deleted — no help at all in
  the GUI installer). The install still completes either way.
- Otherwise v1.2 behavior is kept: private-venv PySide6 install,
  SKULLPT name + `designshell/` compat shim, launcher + icon +
  `skullpt` command, data paths unchanged, cloud mesh step. The
  held governor-refusal fix stays held.

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
