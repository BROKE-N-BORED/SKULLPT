# SKULLPT - v1.16

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
v1.8 is the seamless install: the double-click install now installs
EVERYTHING — Blender, the image engine and its model, Character
Studio, and Ollama with the local model pulled — with the total
download (up to about 7.7 GB on a fresh machine) disclosed before
anything starts. Gemini and Groq keys are pasted and validated in
Settings, like the Hugging Face token, and a first-run Setup check
lists anything still missing with a Fix button for each. Windows
open maximized, and no text is ever clipped off an edge.
v1.9 is the audit fixes (N1-N21): real cancel with live progress, an
installer that fails honestly, Recipes in the Bench, the cloud mesh
using the Settings token, and the web UI removed.
v1.10 is the P2 audit fixes (N22-N50, N52-N54): the viewport draws
real imported mesh geometry, character LODs keep their skins, the
cloud mesh has a verified fallback Space, plans and chat cancel,
damaged files fail in plain words, and the installer is hardened
(pinned and verified Ollama script, cancellable, a new Blender only
swaps in after it proves it runs).
v1.11 is the material system, phase 1 (M1-M6): a material in a
description can now be an object (family, style, layout, params,
wear) instead of a flat preset name, and wood is the first family
(six styles, Solid and Boards layouts, pattern sizes in centimetres).
The wood recipe is a parametric Blender node tree (rings, fibres,
blotch, bump; knots and boards when the spec asks for them), kept
under 25 nodes, with the node count in the build report. The
Material Dial in the Render window picks materials on a circular
dial (double-click a part, or press Dial): Simple or Advanced knobs,
search, Surprise (the seed is recorded), saved presets in
materials.json, and a live preview render on knob release. Ctrl+click
a part to copy its material onto the selected part.
v1.12 is the material system, phase 2 (M7-M11): ten more families
(brick, stone, marble, concrete, metal, fabric, leather, ground,
paint, glass), nine layouts (bonds for brick, parquet, herringbone
and chevron for wood), a wear layer that renders (the dial gains
its Life page), and every pattern size in real centimetres.
v1.13 is the material system, phase 3 and final (M12-M14): PBR pack
baking (colour, roughness, normal, height, and metallic where the
family uses it) into the job's output folder, with the maps carried
into the Unreal package; quality tiers (Draft / Balanced / Final)
for materials, per job or as a workspace default; and true
displacement as an opt-in per material, CPU-gated (it turns off
with a plain note when the machine cannot afford it). v1.14 is the
user-audit fix round (A1-A19): the Render window's right panel
scrolls, the Engines tile pins its content to the top, job cards
show the plain summary with the full description one click away
(Details), image rounds report back in the chat, disabled buttons
look disabled, and the Help covers every window. v1.15 finishes
the audit list (A20-A35): the engine check runs the engine instead
of only looking for the file, the status bar shows Ollama and Groq,
the menu bar uses real menus, the Bench gains a Demo action, and
updates are silent, verified, and ask first (see Updates below).
v1.16 is the second hands-on audit fix round (D1-D14): the
updater can no longer restart-loop on its own leftovers, a filed
job opens read-only in the Render window, a refused image batch
says why inside the Images window, and the word "seat" is gone
from every string a user can see.
Glass does
not bake - its look comes from light passing through it, not from
surface maps - and the bake report says so.
The core shell (install, demo, build loop, library filing) was
verified on the target PC on 2026-11-11, and the Bench opened there
on 2026-11-14 (Settings window, Blender Ready); the full install,
the first real cloud mesh, and the first real Blender build through
the shell are still open verdicts.

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
installer installs EVERYTHING the shell needs, in one run:

- checks Python (>= 3.10),
- creates a private `.venv` in this folder and installs PySide6 into it
  (the Bench window's only dependency — Mint's Python refuses to
  install packages outside a venv, so the shell keeps its own). If
  python3-venv is missing, the installer adds that one system package
  itself (one GUI password prompt) and carries on — it never asks
  you to type anything,
- installs Blender (the pinned LTS — a newer installed Blender is
  never downgraded, and the pinned version is never reinstalled),
- builds the image engine from source and downloads its model
  (about 2.1 GB),
- installs Character Studio (MPFB2 plus the MakeHuman system assets
  pack, about 311 MB),
- installs Ollama and pulls the local model (qwen3:4b, about 2.5 GB)
  — the no-key fallback seat, so the planner works with no keys at
  all,
- runs the shell's test suite as the install proof,
- prints a dependency report,
- puts a **SKULLPT** launcher on the Desktop and in the app menu, and
  a `skullpt` command on your PATH (so `skullpt` works anywhere).

A fresh machine downloads up to about 7.7 GB in total (PySide6 about
80 MB, Blender about 360 MB, the image engine source about 700 MB,
the image model about 2.1 GB, Character Studio about 311 MB, Ollama
about 1.5 GB, the local model about 2.5 GB) — the installer says so
before it starts, and anything already installed is skipped, so a
re-run downloads far less, or nothing at all. All downloads resume
if interrupted and are SHA-256 verified.

Then start it from the launcher, or from `skullpt` — both open the
Bench, the native desktop window. The first time it opens, the Setup
check lists anything still missing — an engine, the local model, or
a key — with a Fix button for each. Your Gemini and Groq keys go in
Settings: paste the key you already have, the app checks it works
and saves it in your workspace only (owner-only, never shown back).
No key is ever typed into a terminal or put in a file by hand.

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
    ./install.sh --with-blender  kept for compatibility — the default
    ./install.sh --with-image-engine
    ./install.sh --with-character-studio
    ./install.sh --with-ollama   install already includes Blender, the
                                 image engine, Character Studio, and
                                 Ollama + the local model; these flags
                                 change nothing now
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

**Live seats (talk to it).** A key comes from the environment first
(`GEMINI_API_KEY`, `GROQ_API_KEY`), then from the workspace's key
file — Settings saves Gemini and Groq keys there (validated against
the real provider before saving, owner-only, gitignored, never
shown back), the same way the Hugging Face token has worked since
v1.5. No key is ever stored anywhere else:

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
from a photo to a mesh — since v1.1 the built-in route is the cloud
mesh step below.

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
is why the installer builds it from source.

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

Variations of a keeper (img2img, `vary`) and guided generation
(IP-Adapter / ControlNet) shipped in v1.6. Batch size is a setting:
`images ... --width 768 --height 512` generates at that size instead
of the default square (v1.10).

## The Bench — the shell's window (v1.0: a native Linux desktop window)

Start it from the **SKULLPT** launcher on the Desktop or in the app
menu (or the `skullpt` command). The terminal way, for reference:

    python3 -m skullpt bench ~/design-shell-workspace

The Bench is nine windows where the workshop lives: a real Linux
desktop app (Qt/PySide6), not a web page, no browser involved. (The
old web Bench was removed in v1.9.) Needs PySide6; `install.sh`
installs it into the shell's private `.venv`, and the Bench says
plainly if it is missing (re-run the installer).

- **Jobs** - home. Bento job cards, the selected-job strip, and the
  chat panel. Type what you want in the new-job box and the planner
  drafts the description (needs the Gemini key), or start from a
  bundled example. The plan-gate buttons (**Approve** → **Build** →
  **File into Library**) live here, with the last build verdict.
- **Render** - the Live Render Window for the selected job: a real
  OpenGL viewport drawing the description's parts as true-scale 3D
  shapes: boxes, cylinders, spheres, extrusions, lathe and tube
  profiles, in their materials' colors. Imported mesh parts draw
  their real geometry, not boxes (v1.10). Drag to orbit, right-drag
  to pan, scroll to zoom. **Frame** fits the whole scene; the camera
  dropdown jumps to a named scene camera; **Save camera** stores the
  current view into the job's description, so it travels with the
  asset. Renders sit under the viewport (double-click one to open
  it).
- **Character Studio** - shapes a rigged human with MPFB2 inside
  Blender (v1.7). The form works before the studio is installed;
  only the skin picker waits for the skins to arrive (v1.10).
- **Images** - image batches: generate candidates from a prompt,
  keep or reject each one, vary a keeper.
- **Library** - filed jobs, searchable, openable as new jobs.
- **Recipes** - recipe manifests: apply one to draft a job, or
  graduate an approved job into a recipe.
- **Settings** - keys and the Hugging Face token (checked against
  Hugging Face before it is saved, v1.10), render defaults,
  workspace backup, update check.
- **Help** - what each window does, in plain words.
- **Setup check** - runs at first launch: Blender, the image engine,
  Character Studio, Ollama, and what is missing, with an installer
  button per gap.

The chat (in Jobs) talks to the workshop: "make a market stall"
drafts a job and offers the Approve button inline; "picture of a
brass lamp" generates candidates; "mesh ~/Pictures/stool.jpg"
turns a photo into a textured 3D mesh on Hugging Face's free GPU
("mesh this photo" uses the selected job's reference photo);
"approve" / "build" / "file" act on the selected job; "retry" sends a
failed job back to approved so it can build again; "status"
reports. Commands work with no key at all, except the mesh step,
which needs HF_TOKEN. Open conversation goes to the planner seat
(Gemini) when its key is set. Without it, the chat says so plainly
instead of faking a brain. The transcript is saved in the workspace
(`chat.jsonl`), so it survives a restart.

Builds and generations run in the background; the chat reports back
when one finishes, and a failure (say, the governor refusing
because RAM is low) prints its plain reason instead of hanging or
pretending.

The bench is a face on the same functions the CLI calls: same jobs,
same gate, same files. The CLI and the picker keep working exactly
as before.

## v1.1 — the cloud mesh step (photo → textured 3D mesh)

The PC has no GPU worth the name, so the mesh step runs on a free
Hugging Face Space instead of locally — Microsoft's official
TRELLIS.2 demo (`microsoft/TRELLIS.2`, MIT licensed) on Hugging
Face ZeroGPU. Free quota is about 5 minutes of GPU a day on a free
account; a mesh takes 3–8 minutes of wall time, most of it queue.

    python3 -m skullpt cloud-mesh <workspace> photo.jpg
    python3 -m skullpt cloud-mesh <workspace> photo.jpg --job my-chair
    # or in the Bench chat:  mesh ~/Pictures/stool.jpg
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

- **HF_TOKEN in the environment, or saved in Settings** (free account
  at huggingface.co, Settings → Access Tokens → New token, Read
  role). A saved token goes to a file in the workspace only
  (`hf_token.key`, owner-only, gitignored) — same rule as the Gemini
  and Groq keys. Without it the step refuses and names the fix; no
  network call is made.
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
  downgraded — the installer leaves it alone and says so. The
  image-engine compile is capped at `-j2` on machines with 8 GB of
  RAM or less.
- **Smaller fixes.** The chat transcript is saved to the workspace
  (`chat.jsonl`) and survives a restart. Library thumbnails use the
  first render PNG, not the SVG mock. Jobs and image batches the
  Bench cannot read are listed with their reasons ("Couldn't read")
  instead of silently vanishing. The unauthorized skull example is
  gated in code — the Bench never offers it.

## v1.16 — the second user-audit fix round (D1-D14)

- **The updater cannot restart-loop (D1).** An applied update
  now clears its staged copy as part of applying, and startup
  checks whether an update already landed before treating a
  staged copy as waiting. A staged update that is genuinely
  waiting still applies at the next start, exactly as before.
  Note: v1.16 itself must be installed by download - the v1.15
  updater carries the loop this fixes.
- **A filed job opens read-only in Render (D2).** The part
  editor and the Material Dial are disabled from the start,
  with the reason on show: "Filed jobs live in the Library.
  Duplicate it to change it." Before, the editor took changes
  and only refused when the dial closed, throwing the work away.
- **A refused image batch says why (D4).** When the RAM
  governor (or a missing engine or model) stops a round before
  any picture exists, the batch is marked failed in the Images
  window and the pane shows the reason, like a failed card does.
- **The Debug Report agrees with the status bar (D3).** Both
  now read the same engine facts, and the report says a Hugging
  Face token is missing when none is saved.
- **No "seat" wording anywhere a user can see it (D5).** The
  keys are described by what they do: the Gemini key plans your
  builds and checks the results; the Groq key fixes drafts that
  fail a check.
- **Render settings with no job loaded (D6).** The controls are
  disabled and the note says these are the workspace defaults
  and that they change in the Settings window.
- **The Engines tile lists all eight statuses (D7)**, Ollama
  and the Groq key included, matching the status bar.
- **The Library's empty state explains itself (D8):** build a
  job, file it from the Jobs window, and it lives here.
- **The Latest render tile packs its content to the top (D9)**,
  so the empty state no longer stretches across the tile.
- **The part editor's fields read blank with no part picked
  (D10)**, not 0.0, as well as disabled.
- **The Images batch list is newest first (D11).**
- **The Settings window shows a plain date (D12)** for the last
  render engine that worked, not a machine timestamp.
- **Governor refusals speak plainly (D13):** how much RAM is
  free, how much is needed, and that waiting is the fix - no
  "floor", no "thrash".
- **The demo's chat line says it once (D14).**

## v1.15 — the audit finish + ask-first updates (A20-A35)

- **The audit list is done.** The remaining fifteen P2 findings:
  the engine check runs the image engine instead of only looking
  for the file; the status bar shows Ollama and Groq and shortens
  cleanly when it runs out of room; the example list starts with
  nothing selected; the Setup header names the buttons that are
  actually there; refusals say "Gemini" instead of the seat name
  and never mention an environment variable; the chat points at
  the Images window; the Library shows plain dates and only the
  files a user cares about; recipe summaries read in full; recipe
  starts and backups log in the chat; the part editor shows a
  material-spec part's material; the dial clears its knob when
  you change pages; image cards keep a fixed height; the menu bar
  uses real menus (File, Jobs, Windows, Help); and the Bench
  gains a Demo action in the Jobs menu.
- **Updates: silent check, verified pull, ask-first restart.**
  When the Bench opens, it quietly asks GitHub whether a newer
  SKULLPT is out (off the UI thread). Up to date, or the check
  fails: nothing happens, no popup. An update exists: it asks
  "Update now?" — Yes downloads the release zip and its .sha256
  sidecar and verifies the checksum before touching anything
  (a mismatch, or a release with no checksum, installs nothing),
  then asks whether to restart now. Yes restarts with the update
  applied; No leaves the verified update staged and it applies
  the next time SKULLPT starts (re-verified first). Only code
  files are replaced — the workspace, key files, and .venv are
  never touched — and a backup of the replaced files is kept
  until the new version launches cleanly. Help, then Check for
  updates, runs the same flow by hand. Every release from v1.15
  on carries a skullpt-vX.Y.zip.sha256 sidecar next to the zip.

## v1.13 - The material system, phase 3 and final (M12-M14)

- **PBR pack baking (M12).** After a job is built, Bake PBR maps
  (Render window, or `bake` on the command line) turns each
  material's node recipe into texture maps in the job's output
  folder: colour, roughness, normal, height, and metallic where
  the family uses it. Baking runs in Blender (Cycles, 1 sample,
  CPU), one material at a time, one map at a time. Metallic is
  always a constant per part, so its map is a flat grayscale PNG
  written by SKULLPT, not a Blender pass. Bake resolution is
  capped at 1024 and follows the material tier (Draft 256,
  Balanced 512, Final 1024). The maps travel into the Unreal
  package, and the import script brings them into Unreal
  alongside the model (colour maps sRGB, the data maps not).
  Glass does not bake: its look comes from light passing through
  it, not from surface maps, so a pack would be a lie - the bake
  report records it as a named gap with the reason.
- **Quality tiers (M13).** Draft / Balanced / Final, set per job
  in the Render window or as a workspace default in Settings.
  Draft wires fewer nodes (no wear layer, no knots, chips or
  frost, one less fine-detail node per family) so builds and
  previews are fast; Balanced is the full recipe; Final adds one
  fine-detail noise node plus a merge node to the bump chain and
  bakes at 1024. The build report lists the tier used and the
  node counts, and the dial's previews use Draft. The pruning is
  systematic: a node whose inputs make it a no-op is never
  created, and the predicted counts still match the measured
  ones in stats.json.
- **True displacement (M14).** An opt-in per material, off by
  default: tick True displacement in the part editor and the
  part is subdivided 16 times denser (level 2) with the bump
  pattern pushing the surface out through the material's
  Displacement socket. Cycles renders show real ridges; EEVEE
  shows bump. The triangle budget gate counts the denser mesh
  (a displaced box estimates 192 triangles, not 12), and the
  build turns displacement off with a plain note when free RAM
  is below the governor's floor plus a 512 MB margin - the
  setting stays on in the description, and the build uses bump
  instead. Honest limits: glTF cannot carry true displacement,
  so the GLB holds the dense mesh and the displacement itself
  shows in Cycles renders only; a material with no bump chain
  (glass without frost) cannot displace, and the build report
  says so.

## v1.12 - The material system, phase 2 (M7-M11)

- **Ten more families (M7).** Brick, stone, marble, concrete,
  metal, fabric, leather, ground, paint and glass join wood - 11
  families, 51 styles. Each family has its own parametric recipe
  on Blender's standard nodes (kiln noise for brick, Voronoi
  cells for stone, wandering veins for marble, stretched brush
  noise for metal, two-wave weave for fabric, and the rest), its
  own knobs, and per-style defaults (frosted glass starts
  frosted, aged paint starts chipped). Metal styles are really
  metallic; glass really transmits. Skin is not a family -
  Character Studio owns character materials.
- **Nine layouts (M8).** Boards, Parquet, Herringbone and Chevron
  for wood; Stack, Stretcher, English, Flemish and Basket Weave
  for brick; Stack and Stretcher for stone and concrete; Solid
  everywhere. The bonds are Brick-node-native (Bias 0 is a stack
  bond, Bias 1 a running bond, probed on Blender 4.5.9); the wood
  layouts rotate the grain per cell on a 3-axis checkerboard
  (parquet flips 90 degrees, herringbone plus/minus 45, chevron
  folds and turns). Bond layouts rotate the face onto the
  texture plane, so a brick wall reads as brick on every face.
  Herringbone and parquet show the grain pattern but no plank
  joints yet (the joints would need ~10 more nodes and break the
  node budget with wear on).
- **The wear layer (M9).** The wear values v1.11 stored now
  render, through one shared "SKULLPT Wear" node group per .blend
  (32 nodes inside it, one group node per worn material): dirt
  and dust settle on upward faces, grime gathers in crevices,
  edges wear light, moss grows on top, rust blooms, chips flake,
  scratches score, wet darkens and slicks. Moss only grows on
  wood, stone and ground; rust only on metal. The dial gains its
  Life page (the tenth).
- **Simple vs Advanced, extended (M10).** Every family curates
  its Simple knobs (seed, scale, and the one or two that matter
  most); Advanced shows everything. Preview renders cover the
  new families, layouts and wear, still hash-cached and off the
  UI thread.
- **Real-world sizes in centimetres (M11).** Every pattern size
  is a real size: a floor board is 12 cm wide, a brick is 21.5 by
  6.5 cm, a parquet cell is 30 cm square, a herringbone plank is
  60 by 12 cm, a stone block is 40 by 20 cm.
- **Node budgets.** Every family's solid recipe stays under 25
  nodes; every combination stays under 40 with wear (worst case:
  herringbone wood with knots and wear, 35). Counts are predicted
  host-side, measured in the build, and listed in the build
  report; the wear group's own count is reported separately.

## v1.11 - The material system, phase 1 (M1-M6)

- **Materials are objects now (M1).** A part's `material` can be an
  object - `{family, style, layout, params, wear}` - validated
  against closed vocabularies, with a breadcrumb ("Wood · White
  Oak · Boards") in the summary, the part editor, and the build
  report. The string form ("wood", "metal", a #hex) keeps working
  exactly as before.
- **A parametric wood recipe (M2/M4).** Wood is the first family:
  six styles (oak, white oak, walnut, maple, cherry, weathered)
  and two layouts (Solid, Boards), all pattern sizes in
  centimetres (a floor board is 12 cm wide). The Blender recipe is
  a node tree of standard nodes - saw-profile ring wave, stretched
  noise fibres, a soft blotch, bump from fibre + ring, with knots
  and a Brick-node boards branch when the spec asks for them. The
  grain runs along the part's longest axis. Node counts stay under
  25 (17 base, 23 worst case) and are predicted host-side, measured
  in the build, and listed in the build report. The recipe's
  structure follows the NK Wood Grain group in video-to-blender
  (https://github.com/gokayfem/video-to-blender, MIT License); the
  nodes are wired here from scratch in plain bpy.
- **The Material Dial (M3).** A circular picker in the Render
  window (double-click a part, or press Dial): nine pages on the
  inner ring, swatches or knobs on the outer ring, the active knob
  with its gauge in the centre. Simple shows the four simple knobs,
  Advanced shows them all (remembered per workspace). Search finds
  styles by name; Surprise rolls a random material and records its
  seed; Save stores a preset. Knob changes recolour the part at
  once; on release, a small Blender preview renders in the
  background (cached by spec hash) and shows in the part editor.
  Number keys 1-9 pick pages. The dial is draggable and minimises
  to a bar. Close applies the material - only when it changed.
- **Presets, breadcrumb, empty state (M5).** Presets live in
  `materials.json` in the workspace (backed up with it) and show
  on the dial's Saved page. A part with no material shows "No
  material here - open the dial to start."
- **The eyedropper (M6).** Ctrl+click a part to copy its material
  onto the selected part - a snapshot, not a link. With nothing
  selected, Ctrl+click selects the part instead.
- **Wear is stored, not rendered yet.** Wear values (dirt, grime,
  edge wear, and the rest) validate and save, but the wear node
  layer is M9 (v1.12) - the dial shows no Life page until the
  values do something.

## v1.10 - The P2 audit fixes (N22-N50, N52-N54)

- **The viewport draws real meshes (N52).** Imported mesh parts used
  to draw as boxes; the Render window now reads the GLB and draws
  the real triangles (indexed and non-indexed, strided attributes,
  node scale and translation), cached per file so a re-exported
  mesh redraws. A part whose mesh is missing still draws its box.
- **Character LODs keep their skins (N53).** The audit said the old
  LODs were unskinned; a check against real Blender 4.5.9 on a real
  MPFB human showed they already kept their skins. The real defect
  was bevel baking: the default "soften" bevel inflated LODs past
  the main GLB (a 12-tri box became 54 tris at lod1). Bevels are
  now hidden for the LOD pass, so LODs are smaller than the main
  GLB as they should be.
- **A verified cloud-mesh fallback (N54).** 99 public TRELLIS.2
  clones were probed; `cpuai/TRELLIS-2` is healthy (running, same
  endpoints as the primary) and is now the fallback Space when the
  primary is down. Set `DESIGNSHELL_CLOUDMESH_FALLBACK_URL=none`
  to turn it off.
- **Plans and chat cancel (N50).** Cancelling a plan or a chat reply
  now stops the live-seat call at once instead of waiting for the
  provider to answer; a cancelled vision call returns the job to
  approved instead of stranding it.
- **Plain words for damaged files (N22-N24).** A damaged seats,
  description, or job file says so plainly instead of crashing on a
  traceback; a damaged library index rebuilds itself from the
  per-folder manifests.
- **Smaller fixes.** Style colors are validated (a hex surface, a
  hex or named accent/text); description snapshots rotate (5 kept);
  `sits_on` checks real footprint overlap; the game-ready gate
  accepts the `max_triangles` alias; the run list shows running and
  queued runs plus the last finished ones; ingest guards against
  malformed GLBs and slow Blender reads; the critic judges at most
  8 renders and names the ones it skipped; image batches take a
  width and height; the HF token is checked against Hugging Face
  before it is saved; backups and model pulls run off the UI thread
  with progress; the installer reports its exit code; Character
  Studio's form works before the studio is installed; saving a
  camera no longer re-frames the view; the log lives in
  `<workspace>/skullpt.log` and the debug report carries its tail.

## v1.9 — The audit fixes (N1–N21 + N51 + N55)

- **Real cancel, live progress.** Cancelling a build now kills the
  Blender or image-engine process at once (the whole process group,
  no orphans) and the Bench shows "Cancelling..." while it dies.
  Blender's output streams into the progress bar while it runs, so a
  long render shows what it is doing instead of a frozen bar.
- **The installer fails honestly now.** Every step checks its own
  result; an optional piece that cannot be installed (image engine,
  image model, Character Studio, launcher) warns and the install
  continues, instead of failing the whole install or claiming
  success it did not earn. Python and Blender stay fatal. An abort
  safety net names the step any unexpected failure died on. The
  image engine source is pinned to a verified commit, not upstream
  master HEAD.
- **Recipes in the Bench.** A Recipes window lists the templates that
  graduate from filed jobs and starts a new draft job from one - the
  plan gate still applies.
- **Cloud mesh uses the Settings token.** The photo-to-mesh preflight
  reads the Hugging Face token saved in Settings; no token means a
  plain refusal, not a crash.
- **The web UI is gone.** The old HTML Bench, viewer page, and
  webbench/view/snapshot commands are removed; SKULLPT is a native
  desktop app only. The Image Picker page stays, because the Code
  Forge bridge drives it from the phone browser.
- **Smaller fixes.** The Images window no longer crashes on batches;
  `.obj` import uses the Blender 4.0+ operator; the part editor
  leaves custom (hex/unknown) materials alone; duplicating a job
  copies its mesh and reference image; only the exact image model
  file counts as present; the open-asset filter adds .obj/.stl/.fbx;
  the planner knows the character vocabulary and refuses a `project`
  draft plainly (no builder yet); the debug scrub redacts Groq and
  Google keys too; no em dashes in visible text anywhere; no
  environment-variable framing left anywhere (keys live in
  Settings); version strings come from one place. MIT licensed
  (LICENSE file at the root).

## v1.8.1 — The new icon

- **A stone skull with a chisel.** The desktop icon is now a stone
  sculpture of a skull with a sculptor's chisel leaning against it,
  replacing the flat cartoon skull. The amber accent in the Bench is
  unchanged: it is now the fixed documented token `#f5a623`, the
  value the old icon's amber crack carried.

## v1.8 — The seamless install (S1–S6)

- **The default install is the full install.** One double-click
  installs everything: Blender (pinned LTS, never downgraded, never
  reinstalled when the pinned version is already there), the image
  engine built from source plus its model, Character Studio (MPFB2
  plus the system assets pack), and Ollama with the local model
  pulled. The `--with-*` flags are kept for compatibility and change
  nothing now. A fresh machine downloads up to about 7.7 GB in total
  — the installer discloses that before anything starts (progress
  window and log), and anything already installed is skipped.
- **Ollama and the local model.** The installer runs Ollama's
  official install script with one GUI password prompt (there is no
  .deb), starts the service, pulls qwen3:4b (about 2.5 GB), and
  confirms the server answers before reporting success. If the model
  pull fails, the install still completes and says plainly that the
  local model is missing — the Bench's Setup check offers to fetch
  it.
- **Keys in Settings.** Gemini and Groq keys are pasted and saved in
  Settings, the same way the Hugging Face token works: the app
  checks the key against the real provider before saving it, and
  stores it in the workspace only (`gemini_key.key`, `groq_key.key`
  — owner-only, gitignored, never shown back, never in a backup).
  The seats read the environment first, then the key file. No fix
  text anywhere points at an environment variable or a terminal.
- **The Setup check.** The first time the Bench opens, one panel
  lists everything missing — an engine, the local model, or a key —
  with a Fix button for each. Engine fixes run the installer (which
  only downloads what is missing); the model fix pulls it; key fixes
  open Settings. It shows once per launch, with "Remind me later",
  and can be reopened any time from File, then Setup check, or from
  Settings. Settings now has Ollama and Groq status lines too — the
  two gaps the real-PC screenshot exposed.
- **Windows open maximized, nothing clipped.** Every window opens
  maximized on first run (a saved geometry still wins after that,
  unless it is too small to read — then maximized wins). The
  Settings content scrolls, and the engine status lines wrap, so no
  text ever runs off an edge.

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
  window says so when it is clicked. Character LODs keep their
  skins and rig like the main mesh, and bevels are left out of the
  LOD pass so LODs stay smaller than the main GLB.
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

The Render window in the Bench is the Live Render Window: a real
OpenGL viewport, a native Linux desktop window, no browser involved.

The window draws the description's parts at true scale (cm, z-up) and
watches the Description File on disk — edits appear live without moving
your camera. Drag to orbit/tilt/spin, right-drag (or Shift-drag) to pan,
scroll or pinch to zoom, click a part to focus it. Scene
cameras are yours: fly to an angle, name it, save it — cameras are stored
in the Description File itself (`cameras: [{name, position_cm,
look_at_cm}]`), so they travel with the asset. Preset views: Iso / Front
/ Side / Top.

The window draws each part's true shape (v0.6: box, cylinder, sphere,
extrude profile, lathe, tube) — an examination layer at true scale, not
the final render; the Blender renders remain the visual verdict. The
camera contract (inspection view + named scene cameras stored in the
description) is unchanged. (The old `view` / `snapshot` commands that
served this as a web page were removed in v1.9.)

## Tests

    python3 -m unittest discover -s tests

674 tests + end-to-end builds: description validation
(incl. cameras, shapes, extrude/lathe/tube profiles, materials,
textures, repeat/mirror), expansion math, constraint checks (a floating
lamp fails), budget gate, heavy-job lock, the plan gate, the full build
to library loop, critic honesty, the render window (geometry incl. true
shapes and real imported meshes, camera save/delete), the making engine (script
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
- The standalone render window (`view`) was removed in v1.9 - the
  Render window in the Bench is the viewport now. The image picker
  is still a local web page served by the shell itself, kept
  deliberately: the Code Forge bridge drives it from the phone
  browser.

## Layout

    skullpt/   the package (description, jobs/gate, governor,
                   constraints, expand, recipes, exporters, blender_export,
                   checks, gameready, critic, seats, live, library,
                   builder, viewer, ingest, intake, graduate, unreal,
                   imagegen, picker, settings, bench, qtbench, qtwindows,
                   qtdesign, cloudmesh, blender_character, tokenfile,
                   keycheck, ollama, update, subproc, backup, applog, cli)
    designshell/   compatibility shim — forwards to skullpt, so the
                   Forge bridge and old `python3 -m designshell` commands
                   keep working
    recipes/       recipe manifests
    examples/      example Description Files
    tests/         the test suite
    Install-SKULLPT.desktop   double-click installer (runs install.sh in GUI mode)
    skullpt-icon.png          the SKULLPT icon (stone skull sculpture with chisel)
    .venv/         private virtualenv, created by install.sh — PySide6
                   lives here and nowhere else
