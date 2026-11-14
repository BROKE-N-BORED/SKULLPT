# SKULLPT — v1.7

A standalone design & asset workshop. Plain language in, real files out:
detailed UI components, websites, graphics, characters, and small 3D scenes,
made with the tools on your own machine (Blender, Unreal, web) and filed in
one Asset Library you own.

**Status:** the skeleton, live render window, Blender making engine,
shaped parts + materials, install/run scripts, the v0.6 batch below
(live seats, true-shape window, lathe/tube/bevel, procedural textures,
repeat/mirror, game-ready gate, ingest, photo intake, recipe graduation,
Unreal packaging), the v0.7 image step (prompt → candidate images →
keep/reject → the keeper becomes a job's reference; the vision critic
can compare builds against that reference), the v1.0 Bench — the
shell's window, a native Linux desktop app (Qt) — the v1.1 cloud
mesh step (photo → textured 3D mesh on Hugging Face's free GPU, filed
in a job), and the v1.2 installer (PySide6 installed into a private
`.venv` so the Bench opens after a plain install; a desktop launcher
and a `skullpt` command), and the v1.3 GUI installer (double-click →
progress window → "Open it now?"; if python3-venv is missing the
installer adds that one system package itself with a GUI password
prompt, so installing and running need no terminal at all) are built
and tested in the build sandbox. v1.4 fixes the defects the audit
found: a governor refusal no longer strands a job (it stays approved,
and a failed job can retry), open conversation works with a key,
kinds with no exporters (character, project) fail loudly instead of
"building" nothing, the triangle budget is one key (`max_tris`,
estimated from the shapes before the build), the description is
snapshotted before anything mutates it, and the installer's downloads
resume and verify (a newer installed Blender is never downgraded).
v1.5 is the redesign: the Bench is five windows (Jobs home, Render,
Images, Library, Settings) in a bone-and-amber identity from
DESIGN.md, with a native image picker, job management
(delete/rename/duplicate), click-a-part editing in the viewport,
progress and cancel on long work, a settings window with Hugging
Face token setup, and the dead seats removed (the planner falls back
to the local Ollama seat when Gemini cannot be reached).
v1.6 is the P2 task list (T21–T30) plus the Blender performance
items: the cloud mesh is a first-class build input (Blender imports
it, Unreal packages it), the planner repairs its own bad drafts,
the critic judges every render of a build, builds queue up instead
of colliding, render settings (engine, resolution, samples,
denoise) are in the Bench, pictures get img2img variations and
ControlNet/IP-Adapter guidance, the cloud-mesh step is hardened
(retries, a fallback Space, queue position, photo downscale),
workspaces can be switched and backed up and the Bench can check
for a newer release, and ingest now takes .obj/.fbx/.stl and keeps
the mesh.
v1.7 is Character Studio (T26), the last item on the task list: a
sixth Bench window that shapes a rigged human with MPFB2 inside
Blender — 11 macro sliders, a skin from the MakeHuman system assets
pack, and a rig (GameEngine by default). Character builds are real
builds now; only `project` builds still fail loudly (that exporter
is still empty).
The core shell (install, demo, build loop, library filing) was
verified on the target PC on 2026-11-11; the Blender install, the
image-engine install, the Bench's real-PC run, and the first real
cloud mesh are still open verdicts.

## How it works (the short version)

1. **Description File first.** Every job starts as a structured description —
   named parts, parameters, style tokens, machine-checkable constraints,
   a budget sized for a small PC, and the rubric it will be judged on.
   You approve the *description*, not raw code.
2. **Plan → Approve → Build.** The gate is enforced in code: `build` refuses
   to run until the job is approved. A build the governor refuses (low
   RAM, another heavy job running) leaves the job approved, not failed —
   and a failed job can **retry** back to approved (Retry button, the
   "retry" chat verb, or `retry` on the CLI) and build again.
3. **Recipes, not raw generation.** Tested building blocks render each part;
   models (in later steps) will choose recipes and fill parameters.
4. **Machines check facts, vision checks looks.** Constraints (text fits,
   parts sit on what they claim to, budgets hold) are verified for free.
   A critic seat judges previews against the rubric. The offline critic
   does deterministic checks only and says so; it never fakes a visual
   verdict. Since v0.6 the Gemini critic seat makes a real vision call
   when its key is in the environment (and its fixes can patch the
   description and rebuild, at most 2 rounds); without a key the offline
   critic answers and the report says why.
5. **Everything approved lands in the Asset Library** — files, the
   description they were made from, the build report, and the session log,
   indexed by a manifest. Search the library before making anything new.

## Run it (Linux, Python 3.10+)

From this folder:

    python3 -m skullpt demo ~/design-shell-workspace

That runs the bundled Tavern Shop Panel example end to end and files it in
the demo workspace's library.

Your own job, step by step:

    python3 -m skullpt new ~/design-shell-workspace my-thing.description.json
    python3 -m skullpt show ~/design-shell-workspace my-thing
    python3 -m skullpt approve ~/design-shell-workspace my-thing
    python3 -m skullpt build ~/design-shell-workspace my-thing
    python3 -m skullpt file ~/design-shell-workspace my-thing
    python3 -m skullpt search ~/design-shell-workspace tavern

Examples live in `examples/` (a UI panel and a small scene description).

## Install / run

Double-click **Install SKULLPT** in this folder (right-click → Allow
Launching first, if your file manager asks). No terminal needed —
the install runs behind a progress window, and when it finishes it
asks "SKULLPT is installed. Open it now?" — say yes and the Bench
opens. Everything the install does is logged to `install.log` in
this folder, so a failure can be diagnosed from the log. The
installer:

- checks Python (>= 3.10) and Blender,
- creates a private `.venv` in this folder and installs PySide6 into it
  (the Bench window's only dependency — Mint's Python refuses to
  install packages outside a venv, so the shell keeps its own). If
  python3-venv is missing, the installer adds that one system package
  itself (one GUI password prompt) and carries on — it never asks
  you to type anything,
- runs the shell's test suite as the install proof,
- prints a dependency report,
- puts a **SKULLPT** launcher on the Desktop and in the app menu, and
  a `skullpt` command on your PATH (so `skullpt` works anywhere).

Then start it from the launcher, or from `skullpt` — both open the
Bench, the native desktop window.

The shell's core runs on Python's standard library alone — PySide6 is
only for the Bench window, and it lives in the private `.venv`, never
in the system Python. The installer's test run uses the fast suite;
the Blender end-to-end builds are skipped there (DESIGNSHELL_SKIP_E2E)
so installs stay quick.

### Terminal reference (optional — advanced users)

Everything above works without a terminal. The commands below are
only for people who prefer the terminal; nothing in the install or
run path requires them.

    ./install.sh                 install (as above; GUI progress window
                                 when run without a terminal, else the
                                 terminal installer)
    ./install.sh --gui           install with the progress window
    ./install.sh --terminal      install in the terminal
    ./install.sh --with-blender  also download + install the pinned Blender LTS
                                 (4.5.9) into /opt/blender (needs admin
                                 permission — it will ask; replaces an
                                 existing /opt/blender, but never
                                 downgrades: a newer installed Blender
                                 is left alone. The download resumes if
                                 interrupted and is SHA-256 verified.)
    ./install.sh --with-image-engine
                                 also build the image engine from source and
                                 download its model (see the image step below;
                                 needs git, cmake, and a C++ compiler — the
                                 installer adds them itself if they are missing)
    ./install.sh --with-character-studio
                                 also install Character Studio: MPFB 2.0.18
                                 (about 44 MB) plus the MakeHuman system
                                 assets pack (about 268 MB) into the Blender
                                 it finds — Blender has to exist first, so
                                 this refuses plainly without it. Both
                                 downloads resume if interrupted and are
                                 SHA-256 verified; the assets pack has a
                                 backup mirror if the first one fails.
    ./run.sh                     start the Bench — the shell's window
                                 (live render view + chat + folders),
                                 a native Linux desktop window
    ./run.sh demo <workspace>    run the built-in demo into a workspace
    ./run.sh <args...>           pass anything to the shell CLI (view, new, build, ...)

## Shaped parts + named materials (new in v0.4)

Scene parts are no longer limited to boxes. A part may carry:

- `shape`: `box` (default), `cylinder`, `sphere`, or `extrude`.
  `extrude` takes `profile_cm` — a list of [x, y] outline points in cm,
  relative to the part's position — and raises it by the part's height.
  That is how shaped slabs (and later, instrument bodies) are made.
- `material`: a preset name — wood, dark_wood, maple, mahogany, cream,
  plastic_black, metal, black_metal, chrome, gold, brass, glass,
  leather, stone, paper — or a raw `#hex` color. Presets carry real
  metallic/roughness values into the render; glass is translucent.

`examples/workbench.description.json` proves all of it: an extruded
chamfered table top in wood, black-metal cylinder legs, a stool, a
glowing glass sphere lamp, a cream mug — every contact machine-checked
with `sits_on` constraints.

## v0.6 — shapes, textures, live seats, ingest, game-ready, Unreal package

**More shapes.** Two new part shapes join box/cylinder/sphere/extrude:

- `lathe` — spin a `profile_cm` outline of [radius, z] points around the
  part's centre: vases, bottles, knobs, turned legs.
- `tube` — a solid swept along `path_cm` points with `radius_cm`:
  handles, cables, rails, arches.
- `bevel_cm` on box/extrude parts sets a real edge bevel (0 = sharp),
  replacing the fixed default softening.

**Procedural textures.** Materials grow surfaces generated inside
Blender from noise — no image files: `wood_grain` (wood, dark_wood,
maple, mahogany), `brushed_metal` (metal, black_metal, chrome, brass,
gold), `worn_leather` (leather). A part's `texture` field overrides the
material's default; `texture: "none"` forces a flat material.

**Repeat + mirror.** A part may carry
`repeat: {"count": n, "step_cm": [dx, dy, dz]}` (copies in a row, ids
`<id>_2`...) and/or `mirror: {"axis": "x"|"y", "at_cm": n}` (one
reflected copy, `<id>_mirrored`). The render window, the Blender
exporter, and the game-ready gate all work on the expanded set.

**True shapes in the Live Render Window.** The window now draws the
actual shapes — cylinders, spheres, extruded profiles, lathes, and
tubes — not bounding boxes. The camera system is unchanged.

**Live seats (talk to it).** Keys come from environment variables only
(`GEMINI_API_KEY`, `GROQ_API_KEY`), never stored:

    python3 -m skullpt plan <workspace> "a small market stall with a wood counter"
    python3 -m skullpt critique <workspace> <job>

`plan` drafts a Description File from plain words via the planner seat
and creates a job with it — the plan gate still applies: nothing builds
until the description is approved. During a scene build, when the
Gemini critic's key is present, it looks at the actual render PNG,
returns structured fixes, and any fix carrying a parameter patch
(position/size/material/bevel/radius on one part) is applied and the
scene rebuilt — at most 2 rounds, all recorded in the build report's
`vision_loop`. `critique` runs that vision pass on demand against a
job's existing renders. Provider endpoints can be overridden with
`DESIGNSHELL_GEMINI_URL` / `DESIGNSHELL_GROQ_URL` (used by the test
suite's mock server). Honest status: the wiring is verified against a
local mock provider; a call against the real Google/Groq endpoints has
not been run from the build sandbox (no keys there).

**Game-ready gate.** A description with `"game_ready": true` makes the
Blender build also produce `<name>_lod1.glb` and `<name>_lod2.glb`
(decimated), `<name>_collision.glb` (UCX_-named bounding boxes, the
Unreal convention — the boxes also ride in the main GLB), and
`<name>.stats.json` (real triangle counts + UV presence measured on the
evaluated meshes). The build then runs the gate: triangle budget,
UVs on every part, LOD + collision exports landed, engine-safe part
names. The gate's verdict is in the build report and a failed gate
fails the build.

**Open-and-edit: ingest.** Derive a Description File FROM an asset:

    python3 -m skullpt ingest model.glb model.description.json
    python3 -m skullpt ingest scene.blend scene.description.json

`.glb` is parsed directly (node boxes, converted from Y-up metres to
Z-up centimetres; node rotations are noted, not unfolded). `.blend`
is measured by Blender headless. The derived description opens in the
render window and rebuilds through the normal loop; the original file
is never modified.

**Photo intake.**

    python3 -m skullpt intake <workspace> photo.jpg

Copies the photo into `<workspace>/intake/`, measures it (real pixel
dimensions parsed from the file), and creates a draft job whose
description records it as the reference. The command prints the routes
from a photo to a mesh — since v1.1 the first route is built in (the
cloud mesh step below); the local SF3D/TripoSR draft route still ends
at a GLB that returns through `ingest`.
The shell does not itself convert photos to meshes.

**Recipe graduation.** Filing an approved job now also graduates its
description into `<workspace>/recipes/<job>.recipe.json`:

    python3 -m skullpt recipes <workspace>
    python3 -m skullpt apply-recipe <workspace> <recipe-file> [out.json]

**Unreal package.**

    python3 -m skullpt unreal-package <workspace> <job>

Assembles `<job>/<slug>-unreal/`: the GLB/.blend/stats/LOD/collision
files, the source `description.json`, `import_into_unreal.py` (an
Unreal Editor Python script importing the GLB to
`/Game/SKULLPT/<slug>/` and writing `import-result.json`), and
`lineage.json` linking the source description's SHA-256 to the Unreal
destination. The script is generated and syntax-checked; it has not
been run inside Unreal (no engine on the build machine), and lineage
records `"imported": false` until `import-result.json` exists.

`examples/craft-stall.description.json` exercises the whole batch:
beveled extruded counter with wood grain, mirrored cylinder legs, a
lathe glass vase, a tube handle, repeated pickets, a brushed-metal
kettle — marked game-ready.

## The image step (new in v0.7)

The front half of the reference pipeline: a prompt becomes pictures,
you pick the keeper, and the keeper becomes a job's locked reference —
the same `reference` slot photo intake fills.

    python3 -m skullpt images <workspace> "a brass oil lamp, product photo" --job my-job
    python3 -m skullpt pick <workspace>/images/<batch>
    python3 -m skullpt decide <workspace>/images/<batch> cand-01.png keep
    python3 -m skullpt reroll <workspace>/images/<batch>
    python3 -m skullpt vary <workspace>/images/<batch> cand-01.png

Each prompt generates a batch of **3 candidates** at 512×512. `pick`
serves a simple picker page (phone-friendly, same pattern as the Live
Render Window): Keep or Reject per picture. Keeping the first
candidate of a job-linked batch promotes it to that job's reference —
the job's description records the file, the prompt, the seed, and the
model. Rejecting **deletes the file and records its seed**, so a
re-roll never serves the same picture twice. Every batch keeps its
full record in `<workspace>/images/<batch>/batch.json`.

**The engine** is leejet's stable-diffusion.cpp (`sd-cli`) driving an
SD 1.5 LCM model — the class that fits an 8 GB CPU-only machine
(~2.3 GB RAM, 4 sampling steps instead of 20–30, so minutes per
picture instead of a half-hour). The model is
`LCM_Dreamshaper_v7-f16.gguf` (~2.1 GB, Hugging Face repo
`Steward/lcm-dreamshaper-v7-gguf`), kept in `~/design-shell-models/`
(or wherever `DESIGNSHELL_SD_MODEL` points). The engine is found via
`DESIGNSHELL_SD_CLI`, PATH, `/opt/sd.cpp/bin/sd-cli`, or
`~/.local/bin/sd-cli`. Missing engine or model = the command refuses
with the fix in plain words; nothing is faked. Generation runs under
the governor's heavy-job lock and RAM floor, like a Blender build.
One honest wrinkle: the prebuilt Linux binary for sd.cpp needs
glibc 2.43 (Ubuntu 26.04) and will not run on Mint (glibc 2.39), which
is why the installer's image-engine option builds it from source.

**The second lane** is manual, and needs no building: make the picture
in the Gemini app (best free quality) and drop the file in through
`intake` — it lands in the same reference slot. (The Gemini API has no
free image tier, so there is deliberately no automated cloud image
seat; that would break the $0 rule.)

**Critic vs. reference.** When a job has a chosen reference and the
vision critic runs, it can judge the render *against that picture* —
same object, proportions, materials, colours — as well as against the
written rubric. This is a switch:

    python3 -m skullpt settings <workspace> --critic-reference on|off

Default is on. A single job can override it either way with
`"critic_reference": true/false` in its description. The build report
and the critique result record `"reference_compared": true/false`, so
what actually happened is never a guess.

What the image step does **not** do in v0.7: variations of a keeper
(img2img) and guided generation (IP-Adapter / ControlNet) — the local
stack supports both, and they are later additions, not silent gaps.

## The Bench — the shell's window (v1.0: a native Linux desktop window)

Start it from the **SKULLPT** launcher on the Desktop or in the app
menu (or the `skullpt` command). The terminal way, for reference:

    python3 -m skullpt bench ~/design-shell-workspace

One window where the workshop lives — a real Linux desktop app
(Qt/PySide6), not a web page, no browser involved. (The old web
Bench still exists as `webbench` for anyone who wants it, but it is
no longer the default.) Needs PySide6; `install.sh` installs it into
the shell's private `.venv`, and the Bench says plainly if it is
missing (re-run the installer).

The window is laid out in three panes:

- **Left — project folders.** Jobs, Library, image batches, intake.
  Clicking a job selects it everywhere. Engine status (Blender,
  image engine, image model, Gemini key) sits under the folders,
  and the new-job box: type what you want and the planner drafts the
  description (needs the Gemini key), or start from a bundled
  example.
- **Center — the Live Render Window** for the selected job: a real
  OpenGL viewport drawing the description's parts as true-scale 3D
  shapes — boxes, cylinders, spheres, extrusions, lathe and tube
  profiles, in their materials' colors. Drag to orbit, right-drag
  to pan, scroll to zoom. **Frame** fits the whole scene; the camera
  dropdown jumps to a named scene camera; **Save camera** stores the
  current view into the job's description, so it travels with the
  asset. The selected job's state, plan-gate buttons (**Approve** →
  **Build** → **File into Library**), last build verdict, renders
  (double-click one to open it), and a preview of the latest render
  sit directly under the viewport.
- **Right — chat.** Talk to the workshop: "make a market stall"
  drafts a job and offers the Approve button inline; "picture of a
  brass lamp" generates candidates; "mesh /home/xprnc/Pictures/stool.jpg"
  turns a photo into a textured 3D mesh on Hugging Face's free GPU
  ("mesh this photo" uses the selected job's reference photo);
  "approve" / "build" / "file" act on the selected job; "retry" sends a
  failed job back to approved so it can build again; "status"
  reports. Commands work with no key at all, except the mesh step,
  which needs HF_TOKEN. Open conversation goes to the planner seat
  (Gemini) when its key is set — without it, the chat says so plainly
  instead of faking a brain. The transcript is saved in the workspace
  (`chat.jsonl`), so it survives a restart.

Builds and generations run in the background; the chat reports back
when one finishes, and a failure (say, the governor refusing
because RAM is low) prints its plain reason instead of hanging or
pretending.

The bench is a face on the same functions the CLI calls — same jobs,
same gate, same files. The CLI, the render window, and the picker
all keep working exactly as before.

## v1.1 — the cloud mesh step (photo → textured 3D mesh)

The PC has no GPU worth the name, so the mesh step runs on a free
Hugging Face Space instead of locally — Microsoft's official
TRELLIS.2 demo (`microsoft/TRELLIS.2`, MIT licensed) on Hugging
Face ZeroGPU. Free quota is about 5 minutes of GPU a day on a free
account; a mesh takes 3–8 minutes of wall time, most of it queue.

    python3 -m skullpt cloud-mesh <workspace> photo.jpg
    python3 -m skullpt cloud-mesh <workspace> photo.jpg --job my-chair
    # or in the Bench chat:  mesh /home/xprnc/Pictures/stool.jpg
    #                        mesh this photo   (selected job's reference)

The photo is uploaded to the Space (background removed there, then
the shape is generated, then the textured GLB is extracted), the GLB
lands in the job's folder, and the existing ingest step derives the
job's description from it — so the mesh then rides the normal
approve → build → render path like any other job. Without `--job` a
new job is created; with `--job` the mesh lands in that job, which
must still be waiting at the plan gate. Either way the job ends at
awaiting_approval: nothing builds until the description is approved.

What it needs and what it says:

- **HF_TOKEN in the environment** (free account at huggingface.co,
  Settings → Access Tokens → New token, Read role). The token is
  never written to any file — same rule as GEMINI_API_KEY. Without
  it the step refuses and names the fix; no network call is made.
- **Disclosure first.** Every run says plainly, before anything is
  sent, that the photo is uploaded to Hugging Face's free GPU and
  leaves the machine.
- **Quota is a plain message, not a crash.** When the daily free GPU
  time is used up the step says so ("try again tomorrow") and leaves
  the job untouched. Space down, timeout, bad image, and a bad GLB
  back from the Space are all plain-language errors the same way —
  the mesh is generated and ingested before any job is touched, so a
  failed run leaves the workspace exactly as it was.
- **No governor block.** The cloud step takes no heavy-job lock and
  checks no RAM floor — it loads no local model and runs no local
  compute; it is network-bound. (Builds and image generation still
  go through the governor as before.)
- **Stdlib only**, like the rest of the shell — the Gradio HTTP API
  (upload, call, SSE event stream, file download) is spoken with
  urllib. The Space base URL can be overridden with
  DESIGNSHELL_CLOUDMESH_URL (the test suite's mock server uses this).

Honest status: the wiring is verified against a local mock Gradio
server implementing the same API shapes the real Space exposes (its
`/gradio_api/info` was read live on 2026-11-12); no call against the
real Hugging Face Space has been run from the build sandbox (no token
there). The first real mesh happens on a machine with a token.

## v1.4 — the audit fixes (governor, conversation, budgets, snapshots)

- **A governor refusal is not a failure.** When the governor refuses
  a build (low RAM, another heavy job running), the job stays
  approved and the refusal is logged — it used to be marked failed,
  which stranded it in a state no approval could rescue. A failed
  job can **retry** back to approved: the Retry button in the Bench,
  the "retry" chat verb, or `python3 -m skullpt retry <workspace>
  <job>`. The heavy-job lock is now taken atomically (two processes
  starting at once cannot both win it), and the RAM floor is
  re-checked between exporters mid-build.
- **Open conversation works.** The chat's open-conversation path was
  dead (it called a key check that did not exist and passed the
  planner seat the wrong arguments). With GEMINI_API_KEY set, plain
  talk now goes to the planner seat with the workshop's jobs as
  context; without the key it still says so plainly.
- **Zero exporters fail loudly.** Character and project kinds have no
  studio yet, so their builds used to "succeed" with no outputs at
  all. They now fail before the build loop with a plain reason, and
  the machine checks refuse a build with zero outputs.
- **One budget key: `max_tris`.** The governor read `max_triangles`
  while the planner, the game-ready gate, and the examples used
  `max_tris`, so the pre-build gate summed zeros and never fired.
  The key is `max_tris` everywhere now (`max_triangles` is still
  honored as a legacy alias), and the gate estimates triangles from
  the shapes — box 12, cylinder 92, sphere 528, extrude/lathe/tube
  from their profiles — counting every repeat/mirror copy. The
  estimate mirrors Blender's base-mesh builders, so it is a lower
  bound: the gate refuses only when the build is certainly over
  budget, and the game-ready gate enforces the exact count
  after the build.
- **Descriptions are snapshotted before mutation.** Approving a job
  freezes `description.approved.json`; the vision critic's patches
  freeze `description.before-vision-patch.json` first; cloud-mesh
  freezes `description.before-cloud-mesh.json` before overwriting.
  What was approved (or what was there before a rewrite) stays
  recoverable.
- **Installer downloads resume and verify.** The Blender tarball and
  the image model download with resume (an interrupted run picks up
  where it left off) and are SHA-256 verified against hashes
  recorded in install.sh. A newer installed Blender is never
  downgraded — `--with-blender` leaves it alone and says so. The
  image-engine compile is capped at `-j2` on machines with 8 GB of
  RAM or less.
- **Smaller fixes.** The chat transcript is saved to the workspace
  (`chat.jsonl`) and survives a restart. Library thumbnails use the
  first render PNG, not the SVG mock. Jobs and image batches the
  Bench cannot read are listed with their reasons ("Couldn't read")
  instead of silently vanishing. The unauthorized skull example is
  gated in code — the Bench never offers it.

## v1.7 — Character Studio (T26, the last task-list item)

- **Character builds are real builds now.** A description of kind
  `character` carries a `character` block — 11 MPFB macro sliders
  (gender, age, muscle, weight, proportions, height, cup size,
  firmness, and three ancestry sliders), a skin, and a rig — and
  the new `blender_character` exporter builds the human headless
  through MPFB2's own API inside Blender. The T3 fail-loud now
  covers only `project` builds (that exporter is still empty).
- **Character Studio window (sixth Bench window).** Pick a
  character job (or make a new one), shape the human with the
  sliders (0-100% with live values), pick a skin from the
  installed pack (23 of them, each with its thumbnail), pick a
  rig, save, and build. Saving sends the job back for approval,
  like any description edit. The gate button follows the job's
  state (Approve, then Build). When the studio is not installed,
  the window says so plainly and names the installer option.
- **The installer option.** The `--with-character-studio`
  install option downloads MPFB 2.0.18 (about 44 MB, SHA-256
  published by the Blender Extensions platform and verified) and the MakeHuman
  system assets pack (about 268 MB, SHA-256 verified, backup
  mirror if the first fails), installs the extension into the
  Blender it finds with Blender's own extension command, and
  unzips the pack into MPFB's data folder where the skins live.
  Together about 311 MB. Both downloads resume if interrupted.
  Blender has to exist first — without it the option refuses
  plainly. Nothing about the studio is in the zip or the repo;
  it is downloads at install time, like the Blender tarball.
- **MPFB2 and Blender 5.3.0 (the open question, answered).** MPFB
  2.0.18's own manifest declares `blender_version_min = 4.2.0`
  and NO upper bound, and the MPFB docs say "Blender 4.2 LTS and
  newer" — nothing declares 5.3.0 unsupported, and the installer
  does not pin or block any Blender version. But 2.0.18 (Oct 10,
  2026) predates Blender 5.3, and the MPFB team's tested range
  runs through 5.2 — so 5.3.0 is genuinely unconfirmed by them,
  and the first real character build on Blender 5.3.0 is still an
  open verdict. MPFB2 2.0.18 was verified working in sandbox
  Blender 4.5.9: the extension installs, a human builds headless
  with a skin and a GameEngine rig, and the GLB exports with its
  skinned armature.
- **What a build produces.** The human (about 34k triangles with
  its low-poly eyes and teeth — the budget estimate uses the
  measured count), skinned to the chosen rig, plus any prop parts
  from the description, rendered from the job's cameras (the Iso
  camera frames the human, not the default cube). Game-ready
  jobs get LODs and a collision box for the human as well as the
  props. The renders are judged by the critic like any scene's.
  The viewport shows a mannequin at the human's height and build
  until the real renders land — it is a stand-in, and the Render
  window says so when it is clicked. Character LODs are baked
  (decimated, unskinned) in this version.
- **Rigs.** GameEngine (the default), Default, CMU motion
  capture, Mixamo, Mixamo Unity, OpenPose, and Rigify, with the
  no-toes and with-breast variants. Rigify rigs make the exporter
  enable Blender's bundled Rigify add-on first. The skin uses
  the plain PBR (GameEngine) material, the one that exports
  cleanly to GLB and Unreal.

## v1.6 — the P2 list (mesh pipeline, queue, render settings, guidance, backup)

- **The cloud mesh is a first-class input (T21).** A Blender build
  of a cloud-meshed job imports the mesh itself instead of
  rebuilding from the ingested boxes (parts you add on top — a
  floor, props — still build normally). And a job with a mesh but
  no Blender build yet can still be packaged for Unreal: the mesh
  is the main asset, and the package says so.
- **The planner repairs its own bad drafts (T22).** When a drafted
  description fails validation, the repairer seat gets one shot at
  fixing exactly the listed problems (if it has no key, the planner
  seat repairs instead, and the planner's own Ollama fallback is
  still there). The repair is recorded in the plan record; a draft
  that still fails says so, naming both attempts.
- **The critic judges every render (T23).** The vision critic now
  looks at all of a build's renders in one call (every camera
  angle, not just the first PNG), and the build report records
  which renders it judged.
- **Builds queue (T24).** Starting a second build while one is
  running queues it instead of colliding on the heavy-job lock;
  queued builds run in order, and a queued build can be cancelled
  before it starts.
- **Render settings in the Bench (T25).** Engine (auto, EEVEE,
  Cycles), resolution, samples, and denoise live in the Settings
  window (workspace defaults) and in the Render window (per-job
  override). Changing them does not send the job back for
  approval — they are presentation, not content.
- **Pictures: variations and guidance (T27).** A kept candidate can
  be varied (img2img) from the Images window, and the image engine
  takes ControlNet and IP-Adapter guidance models. The guidance
  models are separate downloads that live in
  `~/design-shell-models/` next to the base model: a ControlNet
  model file (`.gguf`, its name contains `controlnet`) and, for
  IP-Adapter, an IP-Adapter model (name contains `ip-adapter`) plus
  a CLIP vision encoder (name contains `clip_vision`). Without the
  model files, guidance is refused plainly — the base picture step
  needs nothing new.
- **Cloud-mesh hardening (T28).** The upload and the GLB extraction
  are retried; a fallback Space can be set with
  `DESIGNSHELL_CLOUDMESH_FALLBACK_URL` (none ships by default —
  every public TRELLIS.2 clone checked on 2026-11-14 was paused or
  in error, so there is no safe default to hardcode); the queue
  position and wait time are shown while the free GPU is busy;
  photos bigger than 1024 px on the longest side are downscaled
  before upload; and a quota error never falls back (the free GPU
  time is per-account, so a second Space would not help).
- **Workspace switcher, backup, update check (T29).** File →
  Open workspace switches workspaces now (not on the next start).
  File → Back up workspace zips the jobs, library, pictures,
  meshes, settings, and chat — the Hugging Face token file is
  never in a backup, and the backup says so. Help → Check for
  updates reads the latest SKULLPT release on GitHub and says
  whether a newer one exists; it never downloads anything.
- **Ingest .obj/.fbx/.stl and keep the mesh (T30).** Ingest now
  takes `.obj` and `.stl` directly, and `.fbx` through Blender's
  own importer, and the mesh is kept: the source file is filed in
  the workspace's `meshes/` folder and the description records it,
  so a build imports the real mesh instead of rebuilding boxes.
  The viewport still draws mesh parts as boxes (the right size —
  the Blender renders show the real mesh).
- **Blender: build and render are separate phases (P3).** The
  render-engine fallback re-runs the renders only, not the whole
  build. And a cheap probe (P4) checks whether EEVEE will work
  headless before a full run can burn its timeout — a failed probe
  goes straight to Cycles, and the Settings window shows the last
  engine that worked here.
- **Character Studio (T26) is NOT in v1.6.** It was designed and
  split to v1.7: it is a second making engine (the MPFB2 character
  system, its extension and asset packs, a character description
  vocabulary, and a sixth Bench window) on its own, and whether
  MPFB2 runs on Blender 5.3.0 is still unconfirmed. The design
  note lives with the task list. Character builds still fail
  loudly, as in v1.4 — no half-working studio ships.

## The making engine — Blender scenes (new in v0.3)

A description of kind `scene` now builds for real. The shell writes a
Blender Python script from the description (parts become real meshes at
true scale in meters, materials from the style tokens, a point light in
every lamp, cameras from the description's own `cameras`) and runs
Blender headless:

    python3 -m skullpt new <workspace> examples/tavern-room.description.json
    python3 -m skullpt approve <workspace> <job>
    python3 -m skullpt build <workspace> <job>

Outputs in the job folder: `<name>.blend` (open it in Blender),
`<name>.glb` (for Unreal / web), one `render-<camera>.png` per saved
camera plus an auto iso render, the `.blender.py` script that built it,
and a build note. Rendering tries EEVEE first and falls back to Cycles
CPU when a headless machine cannot give EEVEE a GPU context — the note
says which engine actually ran. Machine checks now also verify the
binary outputs are real PNG/GLB/.blend files, not empty shells.

Blender is found via `DESIGNSHELL_BLENDER`, then PATH, then
`/opt/blender/blender`. If Blender is missing the build FAILS with a
plain message — it never pretends a scene was built.

## Live Render Window (new in v0.2)

    python3 -m skullpt view examples/tavern-room.description.json
    # open the printed http://127.0.0.1:8760/ in a browser

The window draws the description's parts at true scale (cm, z-up) and
watches the Description File on disk — edits appear live without moving
your camera. Drag to orbit/tilt/spin, right-drag (or Shift-drag) to pan,
scroll or pinch to zoom, click a part in the panel to focus it. Scene
cameras are yours: fly to an angle, name it, save it — cameras are stored
in the Description File itself (`cameras: [{name, position_cm,
look_at_cm}]`), so they travel with the asset. Preset views: Iso / Front
/ Side / Top. A non-live still page can be saved with:

    python3 -m skullpt snapshot <description.json> <out.html>

The window draws each part's true shape (v0.6: box, cylinder, sphere,
extrude profile, lathe, tube) — an examination layer at true scale, not
the final render; the Blender renders remain the visual verdict. The
camera contract (inspection view + named scene cameras stored in the
description) is unchanged.

## Tests

    python3 -m unittest discover -s tests

108 fast tests + end-to-end builds: description validation
(incl. cameras, shapes, extrude/lathe/tube profiles, materials,
textures, repeat/mirror), expansion math, constraint checks (a floating
lamp fails), budget gate, heavy-job lock, the plan gate, the full build
to library loop, critic honesty, the render window (geometry incl. true
shapes, camera save/delete, self-contained page whose script passes a
syntax check, live server round-trip), the making engine (script
generation compiles and carries every part/camera/shape, missing
Blender fails loud, binary output signatures, stale outputs from a
previous build never pass for a new one), the game-ready gate, live
planner + vision critic against a local mock provider, GLB ingest
round-trip, photo intake, recipe graduation, and Unreal packaging.
v0.7 adds: the image step against a fake engine (batch of 3, distinct
seeds, reject deletes + records the seed, keep promotes to the job's
reference, the governor blocks generation, missing engine/model fail
loud), the settings toggle + per-job override resolution, the picker
page (script syntax check, image guard, decide over HTTP), and the
critic's reference mode against a mock Gemini (two images sent, order
named in the prompt). v1.1 adds: the cloud mesh step against a local
mock Gradio server (success round-trip to an ingested description,
into a new job and into an existing intake job, no-token refusal
before any network call, quota-exhausted as a plain message with the
job intact, Space-down, bad image, timeout, GLB extraction retry, and
proof the step takes no governor lock), plus the CLI and Bench/chat
wiring for it. The end-to-end tests build the tavern room, the
workbench, and the craft stall in real Blender when it is installed on
the test machine, and generate a real image when the sd engine and
model are installed.

## What's NOT here yet (on purpose)

- No call against the real Google/Groq endpoints has been run from the
  build sandbox — the live-seat wiring is verified against a local mock
  provider implementing the same response shapes. The first real call
  happens on a machine with keys.
- The Unreal import script has not run inside Unreal — no engine on the
  build machine. It is generated, syntax-checked, and its result file
  is the proof when it does run.
- No call against the real Hugging Face Space has been run from the
  build sandbox — the cloud mesh wiring is verified against a local
  mock Gradio server implementing the same API shapes. The first real
  mesh happens on a machine with a token.
- No Character studio yet (MPFB2 route) — designed for v1.7, not
  built. Building a character or project job fails loudly and says
  so, instead of "succeeding" with no outputs.
- The standalone render window (`view`) and the image picker are
  still local web pages served by the shell itself — only the Bench
  went native in v1.0.

## Layout

    skullpt/   the package (description, jobs/gate, governor,
                   constraints, expand, recipes, exporters, blender_export,
                   checks, gameready, critic, seats, live, library,
                   builder, viewer, ingest, intake, graduate, unreal,
                   imagegen, picker, settings, bench, qtbench, cloudmesh,
                   cli)
    designshell/   compatibility shim — forwards to skullpt, so the
                   Forge bridge and old `python3 -m designshell` commands
                   keep working
    recipes/       recipe manifests
    examples/      example Description Files
    tests/         the test suite
    Install-SKULLPT.desktop   double-click installer (runs install.sh in GUI mode)
    skullpt-icon.svg          the SKULLPT icon (skull motif)
    .venv/         private virtualenv, created by install.sh — PySide6
                   lives here and nowhere else
