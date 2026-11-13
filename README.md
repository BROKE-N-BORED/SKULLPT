# Design Shell — v1.1

A standalone design & asset workshop. Plain language in, real files out:
detailed UI components, websites, graphics, characters, and small 3D scenes,
made with the tools on your own machine (Blender, Unreal, web) and filed in
one Asset Library you own. Working name only — rename it any time.

**Status:** the skeleton, live render window, Blender making engine,
shaped parts + materials, install/run scripts, the v0.6 batch below
(live seats, true-shape window, lathe/tube/bevel, procedural textures,
repeat/mirror, game-ready gate, ingest, photo intake, recipe graduation,
Unreal packaging), the v0.7 image step (prompt → candidate images →
keep/reject → the keeper becomes a job's reference; the vision critic
can compare builds against that reference), the v1.0 Bench — the
shell's window, a native Linux desktop app (Qt) — and the v1.1 cloud
mesh step (photo → textured 3D mesh on Hugging Face's free GPU, filed
in a job) are built and tested in the build sandbox. The core shell
(install, demo, build loop, library filing) was verified on the target
PC on 2026-11-11; the Blender install, the image-engine install, the
Bench's real-PC run, and the first real cloud mesh are still open
verdicts.

## How it works (the short version)

1. **Description File first.** Every job starts as a structured description —
   named parts, parameters, style tokens, machine-checkable constraints,
   a budget sized for a small PC, and the rubric it will be judged on.
   You approve the *description*, not raw code.
2. **Plan → Approve → Build.** The gate is enforced in code: `build` refuses
   to run until the job is approved.
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

## Run it (Linux, Python 3.10+, no dependencies)

From this folder:

    python3 -m designshell demo ~/design-shell-workspace

That runs the bundled Tavern Shop Panel example end to end and files it in
the demo workspace's library.

Your own job, step by step:

    python3 -m designshell new ~/design-shell-workspace my-thing.description.json
    python3 -m designshell show ~/design-shell-workspace my-thing
    python3 -m designshell approve ~/design-shell-workspace my-thing
    python3 -m designshell build ~/design-shell-workspace my-thing
    python3 -m designshell file ~/design-shell-workspace my-thing
    python3 -m designshell search ~/design-shell-workspace tavern

Examples live in `examples/` (a UI panel and a small scene description).

## Install / run

    ./install.sh                 check Python + Blender + image engine, verify,
                                 print a dependency report
    ./install.sh --with-blender  also download + install the pinned Blender LTS
                                 (4.5.9) into /opt/blender (sudo; replaces an
                                 existing /opt/blender)
    ./install.sh --with-image-engine
                                 also build the image engine from source and
                                 download its model (see the image step below;
                                 needs git, cmake, and a C++ compiler — the
                                 installer says so plainly if they are missing)
    ./run.sh                     start the Bench — the shell's window
                                 (live render view + chat + folders);
                                 opens in your browser
    ./run.sh demo <workspace>    run the built-in demo into a workspace
    ./run.sh <args...>           pass anything to the shell CLI (view, new, build, ...)

The shell has NO pip dependencies — Python's standard library only — so
install is a check-and-verify, and the report says exactly that. The
installer's test run uses the fast suite; the Blender end-to-end builds
are skipped there (DESIGNSHELL_SKIP_E2E) so installs stay quick.

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

    python3 -m designshell plan <workspace> "a small market stall with a wood counter"
    python3 -m designshell critique <workspace> <job>

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

    python3 -m designshell ingest model.glb model.description.json
    python3 -m designshell ingest scene.blend scene.description.json

`.glb` is parsed directly (node boxes, converted from Y-up metres to
Z-up centimetres; node rotations are noted, not unfolded). `.blend`
is measured by Blender headless. The derived description opens in the
render window and rebuilds through the normal loop; the original file
is never modified.

**Photo intake.**

    python3 -m designshell intake <workspace> photo.jpg

Copies the photo into `<workspace>/intake/`, measures it (real pixel
dimensions parsed from the file), and creates a draft job whose
description records it as the reference. The command prints the routes
from a photo to a mesh — since v1.1 the first route is built in (the
cloud mesh step below); the local SF3D/TripoSR draft route still ends
at a GLB that returns through `ingest`.
The shell does not itself convert photos to meshes.

**Recipe graduation.** Filing an approved job now also graduates its
description into `<workspace>/recipes/<job>.recipe.json`:

    python3 -m designshell recipes <workspace>
    python3 -m designshell apply-recipe <workspace> <recipe-file> [out.json]

**Unreal package.**

    python3 -m designshell unreal-package <workspace> <job>

Assembles `<job>/<slug>-unreal/`: the GLB/.blend/stats/LOD/collision
files, the source `description.json`, `import_into_unreal.py` (an
Unreal Editor Python script importing the GLB to
`/Game/DesignShell/<slug>/` and writing `import-result.json`), and
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

    python3 -m designshell images <workspace> "a brass oil lamp, product photo" --job my-job
    python3 -m designshell pick <workspace>/images/<batch>
    python3 -m designshell decide <workspace>/images/<batch> cand-01.png keep
    python3 -m designshell reroll <workspace>/images/<batch>

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
is why `./install.sh --with-image-engine` builds it from source.

**The second lane** is manual, and needs no building: make the picture
in the Gemini app (best free quality) and drop the file in through
`intake` — it lands in the same reference slot. (The Gemini API has no
free image tier, so there is deliberately no automated cloud image
seat; that would break the $0 rule.)

**Critic vs. reference.** When a job has a chosen reference and the
vision critic runs, it can judge the render *against that picture* —
same object, proportions, materials, colours — as well as against the
written rubric. This is a switch:

    python3 -m designshell settings <workspace> --critic-reference on|off

Default is on. A single job can override it either way with
`"critic_reference": true/false` in its description. The build report
and the critique result record `"reference_compared": true/false`, so
what actually happened is never a guess.

What the image step does **not** do in v0.7: variations of a keeper
(img2img) and guided generation (IP-Adapter / ControlNet) — the local
stack supports both, and they are later additions, not silent gaps.

## The Bench — the shell's window (v1.0: a native Linux desktop window)

    ./run.sh
    # or: python3 -m designshell bench ~/design-shell-workspace

One window where the workshop lives — a real Linux desktop app
(Qt/PySide6), not a web page, no browser involved. (The old web
Bench still exists as `webbench` for anyone who wants it, but it is
no longer the default.) Needs PySide6; `install.sh` reports whether
it is there, and the Bench says plainly if it is missing.

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
  "approve" / "build" / "file" act on the selected job; "status"
  reports. Commands work with no key at all, except the mesh step,
  which needs HF_TOKEN. Open conversation goes to the planner seat
  (Gemini) when its key is set — without it, the chat says so plainly
  instead of faking a brain.

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

    python3 -m designshell cloud-mesh <workspace> photo.jpg
    python3 -m designshell cloud-mesh <workspace> photo.jpg --job my-chair
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

## The making engine — Blender scenes (new in v0.3)

A description of kind `scene` now builds for real. The shell writes a
Blender Python script from the description (parts become real meshes at
true scale in meters, materials from the style tokens, a point light in
every lamp, cameras from the description's own `cameras`) and runs
Blender headless:

    python3 -m designshell new <workspace> examples/tavern-room.description.json
    python3 -m designshell approve <workspace> <job>
    python3 -m designshell build <workspace> <job>

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

    python3 -m designshell view examples/tavern-room.description.json
    # open the printed http://127.0.0.1:8760/ in a browser

The window draws the description's parts at true scale (cm, z-up) and
watches the Description File on disk — edits appear live without moving
your camera. Drag to orbit/tilt/spin, right-drag (or Shift-drag) to pan,
scroll or pinch to zoom, click a part in the panel to focus it. Scene
cameras are yours: fly to an angle, name it, save it — cameras are stored
in the Description File itself (`cameras: [{name, position_cm,
look_at_cm}]`), so they travel with the asset. Preset views: Iso / Front
/ Side / Top. A non-live still page can be saved with:

    python3 -m designshell snapshot <description.json> <out.html>

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

- Image variations (img2img off a keeper) and guided generation
  (IP-Adapter / ControlNet) are not wired yet — v0.7 is prompt →
  candidates → pick. The local engine supports both; they are the
  next image-step additions when asked for.
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
- No Character studio yet (MPFB2 route) — characters are a later step.
- The standalone render window (`view`) and the image picker are
  still local web pages served by the shell itself — only the Bench
  went native in v1.0.

## Layout

    designshell/   the package (description, jobs/gate, governor,
                   constraints, expand, recipes, exporters, blender_export,
                   checks, gameready, critic, seats, live, library,
                   builder, viewer, ingest, intake, graduate, unreal,
                   imagegen, picker, settings, bench, qtbench, cloudmesh,
                   cli)
    recipes/       recipe manifests
    examples/      example Description Files
    tests/         the test suite
