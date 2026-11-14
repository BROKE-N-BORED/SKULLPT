# SKULLPT — Changelog

## v1.6 — 2026-11-14 — the P2 list: mesh pipeline, queue, render settings, guidance, backup

The P2 task list (T21–T30, minus T26) plus the Blender performance
items P3 and P4. T26 Character Studio was designed and split to v1.7
(the design note lives with the task list) — it is a second making
engine on its own, and whether MPFB2 runs on Blender 5.3.0 is still
unconfirmed. Character builds still fail loudly, as in v1.4.

- CLOUD-MESH GLB INTO THE PIPELINE (T21): a Blender build of a
  job with a mesh imports the mesh itself (`bpy.ops.import_scene`)
  and skips the stand-in boxes; added parts still build normally.
  `unreal-package` copies the job's mesh into the package and uses
  it as the main asset when no Blender build exists yet (a .stl is
  copied for reference but never the main asset — Unreal's importer
  does not take STL). Lineage records the mesh source.
- PLANNER REPAIR LOOP (T22): the repairer seat is back in the
  defaults (Groq gpt-oss-20b). A draft that fails validation gets
  ONE repair attempt — the repairer first, the planner seat when
  the repairer has no key — and the plan record notes whether the
  draft was repaired and by whom. A repair that still fails says
  so, naming both attempts.
- CRITIC JUDGES EVERY RENDER (T23): the vision critic looks at all
  of a build's renders in one call and the build report records
  which renders it judged. The Build Report dialog now shows the
  real critique (it was reading keys the critique never had, so it
  always showed a blank verdict — fixed in place).
- JOB QUEUE (T24): a second build while one is running queues
  instead of colliding on the heavy-job lock; queued builds run in
  order, report their position, and can be cancelled before they
  start. The Bench's run poller no longer treats a queued run as
  finished (it used to drop it and log it as failed — fixed in
  place). Image generation and rerolls join the same queue.
- RENDER SETTINGS IN THE UI (T25): engine (auto, EEVEE, Cycles),
  resolution (128–4096), samples (1–512), and denoise — workspace
  defaults in the Settings window, per-job override in the Render
  window, and `settings --render-*` on the CLI. The resolution
  order is job, then the workspace defaults the user actually set,
  then a description's budget.preview_px (older descriptions keep
  rendering as they did), then 640. Changing settings never sends
  a job back to the plan gate.
- IMAGE-STEP V2 (T27): `vary` makes img2img variations of a kept
  candidate into a new batch (the Images window has a Vary button,
  enabled only for kept candidates). `generate` takes guidance:
  `--init-img`/`--strength` for img2img, `--control-net`/
  `--control-image`/`--control-strength` for ControlNet, and
  `--ip-adapter`/`--clip_vision`/`--ip-adapter-image` for
  IP-Adapter — all flags verified against the real source-built
  sd-cli. Guidance models live in `~/design-shell-models/` and are
  found by name; missing model or image is a plain refusal before
  the lock is taken.
- CLOUD-MESH HARDENING (T28): the upload is retried once on a
  transient failure and the GLB extraction twice; a fallback Space
  can be set with `DESIGNSHELL_CLOUDMESH_FALLBACK_URL` (none ships
  by default — every public TRELLIS.2 clone checked on 2026-11-14
  was paused or in error); the SSE queue position and wait time
  reach the progress callback; photos bigger than 1024 px on the
  longest side are downscaled before upload (stdlib PNG decode and
  re-encode — no new dependency); and a quota error never falls
  back, because the free GPU time is per-account across Spaces.
  The cloud-mesh record now also sets the job's `mesh` block, so
  the mesh flows into T21.
- WORKSPACE SWITCHER, BACKUP, UPDATE CHECK (T29): File → Open
  workspace switches now (child windows close, the choice is
  remembered); File → Back up workspace zips the jobs, library,
  pictures, meshes, settings, and chat with a manifest — the
  Hugging Face token file is never in a backup, and the backup
  says so. Help → Check for updates reads the latest SKULLPT
  release on GitHub (read-only, 15 s timeout); it never downloads
  anything. CLI: `backup` and `update-check`. `skullpt.__version__`
  is now the single version source ("1.6").
- INGEST .OBJ/.FBX/.STL + PRESERVE MESH DATA (T30): ingest takes
  `.obj` (stdlib parse, groups/objects as parts) and `.stl`
  (binary and ASCII) directly, and `.fbx` through Blender's own
  importer (no hand-rolled binary FBX parser). The source file is
  filed in the workspace's `meshes/` folder and the description
  records it, so a build imports the real mesh; parts carry their
  recorded triangle counts, which the budget gate uses. The
  viewport still draws mesh parts as boxes (the right size — the
  Blender renders show the real mesh).
- BLENDER SCRIPT SPLIT (P3): `generate_script` is now the build
  script (geometry, materials, stats, GLB/LOD/collision, .blend)
  and `generate_render_script` is the render script (opens the
  .blend, sets the engine, renders the cameras). A render-engine
  fallback re-runs renders only, not the whole build. The vision
  loop still re-runs both (its patches change geometry).
- EEVEE PROBE (P4): before a full run with the engine set to auto,
  a 64 px one-cube probe decides whether EEVEE works headless; a
  failed probe goes straight to Cycles instead of burning the
  600 s timeout. The result is remembered in the workspace
  settings and shown in the Settings window. Engine "cycles"
  skips the probe; engine "eevee" tries EEVEE as today.
- Smaller fixes: the run machinery no longer crashes if a run
  record is gone when its thread finishes (a KeyError found by
  the queue tests); the Settings window's render controls and the
  Render window's per-job controls follow the v1.5 identity
  (DESIGN.md unchanged — the new surfaces reuse its components).

## v1.5 — 2026-11-14 — the redesign: five windows, bone and amber, native picker, job management

The redesign report (2026-11-13) and the P1 task list (T8–T20) plus
the viewport/status performance items. The Bench is no longer one
crowded window: it is five windows, one per job, in a Warp-derived
identity documented in DESIGN.md.

- DESIGN.md (R1): the single source of truth for how the Bench
  looks, in the Stitch 9-section format — Overview, Colors,
  Typography, Layout, Elevation and Depth, Shapes, Components,
  Motion, Do's and Don'ts, with the tokens as frontmatter. It
  records the amber honestly: the v1.2 icon was monochrome, so
  v1.5 put the amber crack in the icon first, and the code samples
  the icon's most saturated hex at runtime.
- LUCIDE ICONS VENDORED (R2): 38 SVG files in `skullpt/icons/`
  with the ISC license. Five were renamed upstream; the Bench keeps
  the brief's file names and DESIGN.md records the current Lucide
  sources.
- IDENTITY STYLESHEET (R3): `skullpt/qtdesign.py` holds the
  tokens (canvas `#1a1a1f`, bone `#e8e4d8`, amber `#f5a623`,
  sampled from the icon), builds the one stylesheet (4px buttons,
  6px cards, hairline-only elevation, no drop shadows), loads the
  bundled Inter and JetBrains Mono fonts, and recolors icons to
  bone at load time.
- CONTRAST VALIDATED (R4): all ten text pairs computed with the
  WCAG formula and recorded in DESIGN.md — every pair passes AA,
  the weakest (muted on canvas-soft) at 6.18:1.
- WINDOW SPLIT (R5): Jobs (home — bento cards, chat, status bar),
  Render (viewport full size), Images (native picker), Library,
  Settings, plus a left icon rail, a Help menu, and How to Use.
  Every window remembers its geometry. The main window is 1280x720
  and fits the 1366x768 screen.
- STAGE PILLS (R6): monochrome plus amber only — APPROVED is amber
  fill, BUILDING and CHECKING carry an amber border, FAILED reads
  via text, not red.
- BENTO JOB CARDS (R7): cards with pill, title, summary, and the
  gate button for the state; the selected card gets an amber border.
  Engine, latest-render, and could-not-read tiles fill the grid.
- TOASTS (R8): a small note in the parent window's corner, gone
  after 4 seconds, no animation — run completions and save
  confirmations only.
- VIEWPORT LEGEND, HOVER, TRI-COUNT (R9): the legend states the
  true grid step (span/10) and the axis colors; hovering a part
  shows its name; the tri-count is measured from the last build
  report when there is one, an estimate from the viewport otherwise.
- NATIVE IMAGE PICKER (T8): the web picker is retired as the only
  surface — the Images window shows batches and candidates natively
  with Keep/Reject (reject deletes the file and records the seed)
  and Reroll. The `pick` command still runs the web picker as a
  legacy surface.
- SETTINGS WINDOW (T9): engine status with plain-language fixes
  for each missing piece, the critic-reference toggle, workspace
  choice, and Hugging Face token setup — the token is saved to
  `hf_token.key` in the workspace (mode 0600, gitignored), never
  shown, never in code. The cloud-mesh step reads the token file
  when no environment token is set.
- JOB MANAGEMENT (T10): delete, rename, duplicate, retry from the
  Jobs window and from the command line (`delete`, `rename`,
  `duplicate`, `recover`). Building and checking jobs cannot be
  deleted or renamed. On startup the Bench recovers jobs stranded
  in building or checking by a crash — only when the heavy-job
  lock is not held by a live process.
- PART EDITING AND PICKING (T11): click a part in the viewport to
  select it (ray-cast against the real mesh triangles); the part
  editor changes position, size, material, bevel, and radius. An
  edit snapshots the description first and sends the job back to
  the plan gate for approval.
- ACTIVITY, PROGRESS, CANCEL (T12): the status bar shows the
  running job with a progress bar and a Cancel button; builds,
  image rounds, and cloud meshes report stage progress and can be
  cancelled at stage boundaries (a cancelled build returns the job
  to approved).
- SINGLE INSTANCE, GEOMETRY, HELP (T13): one Bench per workspace
  (a second launch raises the running one); every window remembers
  its size and position; the Help menu opens How to Use, the debug
  report, and About.
- FIT SCREEN, NO EM DASHES, NO AUTO-SELECT, FIRST RUN (T14): the
  main window is 1280x720; no em dash appears in any visible
  string; nothing is selected by default; the first run shows a
  start-here panel with a try-an-example button.
- DEBUG REPORT (T15): the Help menu's Debug report shows versions,
  engine status, free RAM and the floor, startup timings, the
  selected job's state and build summary, and recent runs — with
  tokens scrubbed and no chat transcript. It can be saved or
  copied.
- FILE PICKERS (T16): mesh-from-photo, open-an-asset, and Unreal
  packaging all use the native file picker; no path is ever typed.
- INCREMENTAL REFRESH (T17): job cards update in place across
  refreshes (widget identity preserved); the viewport keeps its
  camera across a job refresh.
- PLANNING OFF THE UI THREAD (T18): drafting a job and the chat
  both run in background runs, so the window stays responsive while
  the planner thinks.
- BUILD REPORT IN THE BENCH (T19): the Report button opens the
  verdict, outputs, machine checks, measured triangles per part,
  critique, and problems for the selected job.
- DEAD SEATS REMOVED (T20): drafter, repairer, and heavy_coder had
  zero callers (heavy_coder had no live wiring at all) — they are
  out of the default seats, with user-defined seats still loadable
  from seats.json. The planner falls back to the local Ollama seat
  when Gemini cannot be reached, and the chat says so when it does.
- PERF: viewport normals are precomputed per mesh, not per
  triangle per frame (P1); scene meshes are cached by description
  hash (P2); the RAM floor adapts to the machine (15% of total
  RAM, at least 1024 MB) and the Bench shows free RAM beside it
  (P6); the workspace state is cached by file signature and only
  re-read when something changed (P7); startup timings are logged
  and shown in the debug report (P8).
- The `bench` command takes no workspace argument any more (the
  Settings choice decides, falling back to the default workspace);
  `run.sh` with no arguments opens the Bench the same way.

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
