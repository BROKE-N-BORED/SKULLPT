# SKULLPT — Changelog

## v1.10 - 2026-11-16 - The P2 audit fixes (N22-N50, N52-N54)

The P2 findings from the 2026-11-15 audit, all closed. The
headline: the viewport draws real imported mesh geometry instead of
boxes, character LODs keep their skins, and the cloud mesh has a
fallback Space proven healthy by a fresh probe of 99 public clones.

- N22: damaged seats, description, and job files fail in plain
  words ("this file is damaged") instead of crashing on a
  traceback; a damaged build report falls back to the output
  listing.
- N23: style colors are validated - `surface` must be a #RRGGBB
  hex, `accent` and `text` a hex or a CSS color name.
- N24: a damaged or missing library index rebuilds itself from
  the per-folder manifests, and heals by saving.
- N25: description snapshots rotate - the current name is newest,
  `.1` through `.5` the previous five.
- N26: dead code removed (`texture_px` check, the accent payload
  in the Blender export) and stale module docstrings refreshed.
- N27: `sits_on` checks real footprint overlap (both axes, more
  than a touch); edge-touching does not count.
- N28: the game-ready gate accepts the `max_triangles` budget
  alias, like the governor.
- N29: the run list shows all running, cancelling, and queued
  runs plus the last 8 finished, in order.
- N30: ingest guards against malformed GLBs (plain ValueError)
  and slow Blender reads (a 10-minute timeout, plainly worded).
- N31: Qt slots that can fail (opening a library entry as a job,
  saving a debug report) warn instead of crashing.
- N32: the Hugging Face token is checked against Hugging Face
  before it is saved; an invalid token is never stored.
- N33: workspace backup runs off the UI thread (a background run
  in the Bench, a background call in Settings).
- N34: the Setup window reads the installer's exit code - 0
  refreshes, 3 is a plain "Install cancelled", anything else
  warns with the code.
- N35: the Ollama model pull streams progress (the button shows
  the latest line) and the pull itself is deadline-bounded.
- N36: Character Studio's form and save work before the studio
  is installed; only the skin picker waits for the skins, and
  only the Build button is gated.
- N37: saving a camera refreshes the Render window without
  re-framing, so the saved view survives.
- N38: the Images toast says "kept.", the Groq note is a proper
  sentence, and the photo picker only offers formats the mesh
  step accepts.
- N39: logging - `skullpt.log` in the workspace (rotated, 2 MB
  x 2), a ring buffer whose tail the debug report carries, and
  an exception hook that logs tracebacks.
- N40: no hardcoded `/home/xprnc` paths left in the Bench or
  the README.
- N41: `_preserve_mesh` compares files by size, then streamed
  SHA-256 - never whole-file bytes.
- N42: the critic judges at most 8 renders per build and names
  the skipped ones in the build report.
- N43: the cloud mesh caps downloads at 256 MB (chunked, refuses
  past the cap) and the photo error names all three formats.
- N44: installer hardening - the Ollama script is pinned to the
  v0.40.16 tag and verified by SHA-256 (the old comment claiming
  the script verified itself was wrong); the progress dialog is
  cancellable (exit 3, "Install cancelled"); nested MPFB/assets
  zips are flattened one level; a new Blender lands beside the
  old one and swaps in only after `--version` proves it runs,
  with the old one restored on failure.
- N45: README rewritten where it had gone stale - the Bench
  section describes the nine windows, the image step covers vary
  and guidance (v1.6) and batch size (v1.10), the dead photo
  route lines are gone, the module list is current, and the
  test count is current.
- N46: DESIGN.md names v1.10, counts nine windows, and records
  the amber as the fixed `#f5a623` token.
- N47: the Help text names the Unreal package's real contents
  (the .glb, the .blend, the description, the import script, the
  README).
- N48: image batches take a width and height; `vary` keeps the
  source batch's size.
- N49: the dead SF3D/TripoSR intake route is gone, and the JPEG
  dimension probe reads past 64 KB of marker segments.
- N50: plans and chat cancel - the live-seat call stops at once
  (SeatCancelled, never mistaken for a failure), and a cancelled
  vision call returns the job to approved.
- N52: the viewport draws real mesh geometry - `glb_node_triangles`
  reads a GLB's triangles per node (indexed and non-indexed,
  strided attributes, node scale and translation, 250k-triangle
  sampling cap) and the Render window draws them, cached per
  file.
- N53: character LODs keep their skins, and bevels are hidden for
  the LOD pass. Honest note: the audit said the old LODs were
  unskinned, but a check against real Blender 4.5.9 on a real
  shape-keyed MPFB human (plus a posed-armature test) showed the
  v1.9 LODs already kept their skins - the exporter special-cases
  armatures. The real defect was bevel baking inflating LOD
  triangle counts (a 12-tri box became 54 tris at lod1), fixed by
  hiding bevels for the LOD export. A first v1.10 attempt
  (duplicating meshes and applying DECIMATE directly) was
  reverted: Blender refuses to apply any modifier to a mesh with
  shape keys, so it broke every character build.
- N54: the fallback Space is `cpuai/TRELLIS-2`, proven healthy
  by a probe of 99 public clones on 2026-11-16 (3 running, 1
  sleeping, the rest paused or in error; `zeptotenshi/TRELLIS.2`
  is the healthy second choice, sleeping when idle).
  `DESIGNSHELL_CLOUDMESH_FALLBACK_URL=none` disables it.

## v1.9 — 2026-11-16 — The audit fixes (N1–N21 + N51 + N55)

The P0+P1 findings from the 2026-11-15 audit, all closed. The
headline: cancelling a run now actually stops it, Blender's output
streams live into the progress bar, and the installer fails honestly
instead of failing the whole install on an optional piece or claiming
a success it did not earn.

- N1: the Images window no longer crashes on batches - `refresh`
  reads the batch `slug` (the field state dicts carry) and computes
  kept/pending from the candidates' statuses.
- N2: the cloud-mesh preflight reads the Hugging Face token from
  Settings (`token_value(workspace=...)`); no token is a plain
  refusal, not a crash.
- N3: `.obj` import uses `bpy.ops.wm.obj_import` (Blender 4.0+) with
  the legacy `import_scene.obj` operator as fallback.
- N4: the part editor leaves hex/unknown materials alone (blank combo
  + a note, instead of forcing black metal) and only writes bevel /
  radius where the part already has them or the value is nonzero.
- N5: only the exact model file counts as present, in the shell
  (`find_model`) and the installer (`find_sd_model`) - a guidance
  .gguf in the same folder no longer passes for the main model.
- N6: duplicating a job copies its job-folder files - the mesh GLB
  and the reference image when it lives in the job folder (repointed
  in the copy's description). Shared files outside the folder are
  still shared, not copied.
- N7: mesh resolution is job folder first, then the workspace root;
  the working-directory candidate is gone, so a stray file in the
  launch folder can no longer shadow the job's mesh.
- N8: real cancel - a new `skullpt/subproc.py` runs Blender and
  sd-cli in their own process group and kills the group on cancel
  (no orphaned processes), and the Bench shows a "cancelling" state
  while the kill lands. The queue treats a cancelling run as busy,
  so the next waiter never starts under a dying run.
- N9: live Blender progress - the two-phase run streams Blender's
  output lines into the Bench progress text (throttled, "Saved:"
  lines always through) and advances the fraction as renders land.
  The image engine's lines stream the same way. Blender runs with
  PYTHONUNBUFFERED so the lines arrive live, not in bursts.
- N10: the legacy web UI is removed - the HTML Bench, the viewer
  page + server, and the webbench/view/snapshot CLI commands. The
  Image Picker page (`pick`/`decide`) is KEPT: the Code Forge bridge
  drives it from the phone browser, so removing it would break the
  bridge. The viewer geometry (part boxes, bounds, cameras) stays -
  the Qt viewport draws on it.
- N11: the installer checks every step explicitly (the worker-entry
  `if` suppresses errexit inside `do_install`, so nothing may rely
  on `set -e`). Blender's unpack and privileged install are checked
  and stay fatal; the image-engine build and the Character Studio
  install moved into checked helper functions.
- N12: sd.cpp is pinned to the verified commit
  32d516c0197ed5ca31305b8575e14eb1451fad33 - the installer fetches
  that exact commit (plus submodules), not upstream master HEAD.
- N13: the open-asset filter adds .obj/.stl/.fbx alongside .glb/.blend.
- N14: the planner spec carries the character vocabulary (11 MPFB
  macro sliders, required skin, rig list with game_engine default),
  and `plan --kind project` refuses plainly before the seat is asked
  (no builder for projects yet). Unknown kinds refuse the same way.
- N15: the vision patch loop applies to scenes only - a character
  build's critique now says so plainly instead of silently skipping
  the loop.
- N16: version strings come from `__version__` everywhere (About,
  Help).
- N17: Blender is settled - the installer pins 4.5.9, Help says
  "4.5.9 or newer".
- N18: the debug scrub redacts `gsk_` (Groq) and `AIza` (Google)
  keys alongside the existing prefixes.
- N19: Recipes in the Bench - a Recipes window lists the graduated
  templates and starts a new draft job from one (the plan gate still
  applies), reachable from the icon rail and the menu bar.
- N20: no em dashes in visible text anywhere - the Bench detail
  lines, chat replies, CLI help, installer dialogs, and both
  .desktop comments. A test fails on any em dash in the Python
  sources.
- N21: no environment-variable framing left - the chat help, the
  no-key reply, the CLI help, and the intake routes all point at
  Settings; the dead env-only `_planner_ready` is deleted.
- N51: MIT license (LICENSE at the tree root, Copyright (c) 2026
  Broke n' Bored Studio).
- N55: installer hardening for the real-PC failure - the EXIT trap
  is installed (it existed but was never wired up), so any
  unexpected abort records an INSTALL-FAILED reason naming the step
  it died on; optional components (image-engine build + build-tools
  heal, image-model download, Character Studio) degrade to WARNING
  instead of failing the install; warnings accumulate instead of
  overwriting each other; a failed launcher warns instead of
  reporting a success it did not earn. Python and Blender stay
  fatal, and the venv/PySide6 path stays warning-level by design (a
  missing python3-venv must not brick the whole install).

## v1.8.1 — 2026-11-15 — The new icon

- NEW ICON: the desktop icon is now a stone sculpture of a skull
  with a sculptor's chisel leaning against it (`skullpt-icon.png`,
  512x512), replacing the flat cartoon skull SVG. The old icon
  looked like a virus; the new one says what SKULLPT is - sculpting.
- AMBER ACCENT UNCHANGED: the Bench accent is still `#f5a623`. It
  was sampled from the old icon's amber crack at runtime (v1.5-v1.8);
  a raster icon has no hex to sample, so the sampled value is now
  the fixed documented token (`qtdesign.AMBER()`), and the contrast
  table still validates it.

## v1.8 — 2026-11-14 — The seamless install (S1–S6)

The double-click install now installs everything, the keys are set
up in the app, and a first-run Setup check lists anything still
missing with a Fix button for each. No terminal, no environment
variable, no manual download anywhere in the flow.

- FULL INSTALL BY DEFAULT (S1): the default run installs Blender
  (pinned LTS — a newer installed Blender is never downgraded, and
  the pinned version is never reinstalled when it is already there),
  the image engine built from source plus its model, Character
  Studio (MPFB2 plus the system assets pack, skipped when already
  installed), and Ollama with the local model pulled. The `--with-*`
  flags are kept for compatibility and change nothing now. The
  progress steps cover the longer install honestly (5 Python, 15
  Bench, 20 Blender, 32 image engine, 50 Character Studio, 60
  Ollama, 70 tests, 85 finishing, 100 done, with install sub-steps
  at 22/34/42/52/62/66 when the work actually runs). The installer
  discloses the total download before anything starts — up to about
  7.7 GB on a fresh machine, measured from the real sources
  2026-11-14: PySide6 84,346,433 bytes, Blender 4.5.9 377,929,956
  bytes, the sd.cpp source about 700 MB (a shallow recursive clone
  occupies 715,260,125 bytes on disk), the LCM model 2,134,675,232
  bytes, MPFB 45,956,992 bytes, the assets pack 280,737,770 bytes,
  the Ollama bundle 1,545,767,640 bytes, qwen3:4b about 2.5 GB.
  Anything already installed is skipped.
- OLLAMA INSTALL + MODEL PULL (S2): the installer runs Ollama's
  official install script with one GUI password prompt (there is no
  .deb — the script installs to /usr, creates the ollama user, and
  sets up + starts the systemd service; it verifies its own bundle
  against the release's sha256sum.txt), starts the service when an
  existing install has it stopped, waits for the server to answer
  on 127.0.0.1:11434, pulls qwen3:4b (the model the fallback seat
  already names), and confirms the model is listed before reporting
  success. An Ollama failure is not fatal: the install completes
  and says plainly, in the GUI, that the local model is missing and
  the Bench's Setup check will offer to fetch it. New `ollama`
  module: server/model status (cached 30 s so a down server never
  stalls a refresh), model pull with a plain-language result.
- IN-APP KEY SETUP (S3): Gemini and Groq keys are pasted and saved
  in Settings, following the T9 HF-token pattern exactly — the key
  is checked against the real provider before saving (one read-only
  models-list call; a key that does not validate is never saved),
  then stored in the workspace only (`gemini_key.key`,
  `groq_key.key` — mode 0600, gitignored, never shown back, never in
  a backup, never in code or the repo). The seats read the
  environment first, then the key file — `live` threads the
  workspace through the planner, chat, repairer, and critic paths,
  and the builder's critic choice uses the same check. A "Get a
  key" button opens the provider's key page in the browser, as a
  fallback for someone who does not have a key yet; the primary
  flow is pasting the key you already have. The "Set the
  GEMINI_API_KEY environment variable" text is gone everywhere, as
  are the manual-download fix texts — every fix is in-app.
- FIRST-RUN SETUP CHECK (S4/S5): the Bench verifies every engine on
  launch (Blender, image engine, image model, MPFB2 + assets,
  Ollama server + model, Gemini key, Groq key, HF token) and, when
  something is missing, shows one plain-language panel with a Fix
  button for each item — engine fixes run the installer (which only
  downloads what is missing), the model fix pulls it in a
  background thread, key fixes open Settings. It shows once per
  launch, with "Remind me later", and reopens any time from File,
  then Setup check, or from the button in Settings. Settings gains
  the two status lines the real-PC screenshot showed were missing:
  Ollama (server up/down + model present/missing, with a fix line
  that says which) and the Groq key.
- WINDOWS OPEN MAXIMIZED, NOTHING CLIPPED (S6): every window opens
  maximized on first run — a saved geometry still wins after that,
  unless it is under the usable floor (900x560), in which case
  maximized wins and the normal geometry is widened so un-maximizing
  lands on a usable size. The Settings content scrolls, the engine
  rows are name + wrapping status (not a form column), and every
  long label wraps — no text ever runs off an edge. Verified by
  geometry checks under Xvfb at 1366x768, not by string assertion.
- ALSO IN THIS RELEASE: the dependency report and debug report show
  the Ollama and Groq status (set/not set, never a value); the
  Help index gains a Setup section; the stale "never written to any
  file" line in the README (wrong since v1.5 saved the HF token) is
  corrected; the About line in Help says v1.8 (it still said v1.6).

## v1.7 — 2026-11-14 — Character Studio (T26, the last task-list item)

The final item on the task list: a rigged human, built headless by
MPFB2 inside Blender, shaped in a sixth Bench window. Character
builds are real builds now; only `project` still fails loudly.

- CHARACTER STUDIO (T26): a description of kind `character`
  carries a `character` block — 11 MPFB macro sliders (gender,
  age, muscle, weight, proportions, height, cup size, firmness,
  race_asian, race_caucasian, race_african), a skin (required —
  without one the human stays grey), and a rig (GameEngine by
  default). The new `blender_character` exporter builds the human
  through MPFB2's own `HumanService.deserialize_from_dict` with a
  human_info dict, then reuses the scene build template for props,
  stats, LODs, collision, GLB and .blend, and the shared two-phase
  run (build, then renders). The budget estimate adds the measured
  human count (34,048 tris: body 26,756 + low-poly eyes 172 +
  teeth 7,120, measured from a real MPFB 2.0.18 build); the
  game-ready gate still enforces the exact post-build total.
  Every human gets low-poly eyes and the base teeth, so the
  renders show a finished face. The skin uses the plain PBR
  (GameEngine) material, the one that exports cleanly to GLB.
  Character LODs keep their skins (the v1.7 line that called them
  baked and unskinned was wrong - a check against real Blender
  showed the v1.9 LODs already kept their skins); from v1.10
  bevels are left out of the LOD pass so LODs stay smaller than
  the main GLB.
- GOVERNOR LOCK RACE (found in verification, fixed here): the
  heavy-job lock treated an empty lock file as stale and stole
  it — but an empty file is usually a contender that just created
  it and has not written its pid yet. Under load both contenders
  reported "won" and two heavy jobs could run at once. Acquire
  now waits and re-reads an empty file before deciding it is
  orphaned, so a live contender is never stolen from.
- INSTALLER OPTION: the `--with-character-studio` option
  downloads MPFB 2.0.18 (45,956,992 bytes, SHA-256 published by
  the Blender Extensions platform) and the MakeHuman system
  assets pack (280,737,770 bytes, SHA-256 measured from the real
  download; files2 primary, files backup mirror), installs the
  extension with Blender's own `extension install-file` command
  (manual unzip fallback), and unzips the pack into MPFB's data
  folder. Both downloads resume and are SHA-256 verified, like
  the Blender tarball; `download_verified` takes an optional
  fallback URL now. Blender has to exist first — without it the
  option refuses plainly. The dependency report shows the studio
  status. Nothing about the studio is in the zip or the repo.
- MPFB2 AND BLENDER 5.3.0: 2.0.18's manifest declares
  `blender_version_min = 4.2.0` with no upper bound, and the MPFB
  docs say "Blender 4.2 LTS and newer" — nothing declares 5.3.0
  unsupported. But 2.0.18 predates Blender 5.3 and the MPFB team's
  tested range runs through 5.2, so 5.3.0 is unconfirmed by them;
  the README says so honestly, and the first real 5.3.0 character
  build is still an open verdict. Verified in sandbox Blender
  4.5.9: extension installs, human builds headless with skin and
  GameEngine rig (53 bones), GLB exports with its skinned
  armature.
- CHARACTER STUDIO WINDOW: the sixth Bench window (left rail icon:
  Lucide `user`, newly vendored). Character-job picker plus new
  character, 11 sliders (0-100% with live values), skin picker
  with 96px thumbnails from the assets pack, rig picker, Save
  (snapshots first, returns the job to the plan gate), and a gate
  button that follows the job's state. When the studio is not
  installed the window says so plainly and the form stays
  disabled; creating a job still works. The menu bar and the
  engine tile/status bar gain the Character Studio entry, and the
  Settings window lists it with the installer fix.
- VIEWPORT MANNEQUIN: a character job's viewport shows a
  human-shaped stand-in (leg and arm cylinders, torso capsule,
  head sphere) at the human's height and build from the sliders,
  feet at z=0 — the bounds follow it, so a partless job frames
  the human, not the default cube. Clicking it says the human is
  edited in Character Studio; the Iso camera frames the human
  (plus props, when the job has them).
- RIGS: GameEngine (default), GameEngine with breast bones,
  Default, Default no toes, CMU, Mixamo, Mixamo Unity, OpenPose,
  Rigify, Rigify with toes — MPFB's built-in set, validated
  against the extension's own rig files. Rigify rigs make the
  build script enable Blender's bundled Rigify add-on first
  (verified: a rigify.human build lands with a 185-bone
  armature).
- SMALL FIXES IN PLACE: the scene build script is now
  `build_payload` + `render_build_template`, and the two-phase
  Blender run is shared (`export_blender_with_build_script`) —
  the scene exporter calls the same code it always did, and the
  character exporter reuses it instead of duplicating it. A
  slider value of 0.0 was read as "missing" and replaced with the
  0.5 default (a falsy-`or` bug, caught by the new tests) — fixed
  in the bounds and the payload normalization. Job state saves
  atomically now (write beside job.json and rename): the
  background-build poll used to read an empty job.json when it
  landed mid-write and crash on JSON — found by the v1.7
  verification run. The installer tests staged a copy of the tree
  including a real .venv when one existed, so the nested installer
  reused it and ran the real suite instead of the stubbed one —
  and the real suite contains installer tests, which staged
  another copy and ran another installer, recursing until the
  timeout (found by the v1.7 installer sim, from an installed
  tree). The stage now excludes .venv. The v1.4 T3 test for
  character builds now asserts the new behavior (the job reaches
  the exporter); the project test is unchanged.

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
