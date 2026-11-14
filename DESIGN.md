---
version: 1.6
name: SKULLPT-design-system
description: The SKULLPT look, a native Linux design and asset shell. A warm near-black canvas, bone text, and amber as the one chromatic accent, sampled from the skull icon's amber crack. Warp-derived palette logic (warm dark, hairline-only elevation, tight radii), Cursor's one-accent discipline, JetBrains Mono on every code surface. Built for an 8 GB no-GPU Mint PC: cheap motion only, nothing decorative that costs a frame.

colors:
  primary: "#f5a623"
  on-primary: "#1a1a1f"
  ink: "#e8e4d8"
  body: "#c9c0ad"
  mute: "#aea69c"
  canvas: "#1a1a1f"
  canvas-soft: "#27272d"
  hairline: "#2e2e33"
  amber-hover: "#f6ad35"
  amber-pressed: "#e19920"
  amber-disabled: "#7d5921"

typography:
  ui:
    fontFamily: Inter, DejaVu Sans, sans-serif
    fontSize: 13px
    fontWeight: 400
  heading:
    fontFamily: Inter, DejaVu Sans, sans-serif
    fontSize: 15px
    fontWeight: 600
  small:
    fontFamily: Inter, DejaVu Sans, sans-serif
    fontSize: 11px
    fontWeight: 400
  mono:
    fontFamily: JetBrains Mono, DejaVu Sans Mono, monospace
    fontSize: 13px
    fontWeight: 400
  pill:
    fontFamily: Inter, DejaVu Sans, sans-serif
    fontSize: 10px
    fontWeight: 600

spacing:
  base: 4px
  card-padding: 10px 8px
  window-margin: 8px
  grid-gap: 8px

radii:
  button: 4px
  card: 6px
  pill: 8px
---

# SKULLPT Design System

Single source of truth for how the Bench looks (v1.5, carried into
v1.6 unchanged — the v1.6 surfaces reuse these components: the
render settings use the Settings window's form rows and the Render
window's groups, the Vary button is a plain secondary button, the
workspace/backup/update actions are plain menu items and dialogs).
The code follows this file: `skullpt/qtdesign.py` holds the tokens, samples
the amber from the icon at runtime, and builds the one stylesheet
`main()` applies. If this file and the code disagree, this file wins
and the code is wrong.

## Overview

SKULLPT is a workshop, not a dashboard. The Bench is five windows,
one per job (U1): Jobs is home (bento job cards, chat, status bar);
Render holds the 3D viewport full size; Images is the native picture
picker; Library is the filed assets; Settings holds engines and the
Hugging Face token. A left icon rail (24px bone icons) and the menu
bar move between them.

The voice is bone and amber on warm near-black: the skull in the
icon, the amber crack in it. Amber is the ONLY chromatic accent,
used as scarcely as Cursor uses orange: primary actions, the
approved state, the selected card's border, the selection outline in
the viewport. Everything else is bone, body, and muted tones on
canvas. No drop shadows anywhere; hairlines and surface contrast
carry elevation.

The bar: a child could pick it up and use it (toy test, U34); it
runs on an 8 GB no-GPU Mint PC (cheap motion only); it fits
design-rules.md; every technique earns its place. Decoration that
gives the app its identity is wanted (Q4: "Does it make the app feel
like itself?"), the amber crack, the skull, the bone-on-black
warmth are the identity.

## Colors

| Token | Hex | Used for |
| --- | --- | --- |
| canvas | `#1a1a1f` | Window backgrounds |
| canvas-soft | `#27272d` | Cards, inputs, raised surfaces |
| hairline | `#2e2e33` | Borders, dividers, grid lines |
| bone | `#e8e4d8` | Primary text, icons |
| body | `#c9c0ad` | Secondary text |
| muted | `#aea69c` | Tertiary text, disabled text |
| amber | `#f5a623` | Primary actions, approved state, selection |
| amber-hover | `#f6ad35` | Primary button hover (amber lightened 8%) |
| amber-pressed | `#e19920` | Primary button pressed (amber darkened 8%) |
| amber-disabled | `#7d5921` | Primary button disabled (amber mixed 55% toward canvas) |
| near-black | `#1a1a1f` | Text on amber |

Palette logic is Warp-derived: warm dark, never neutral gray or
pure black. Canvas-soft and hairline are Warp's steps above its
canvas, applied to SKULLPT's canvas (Warp canvas `#2b2622` to
canvas-soft `#383330` is +13/+13/+14; the same step on `#1a1a1f`
gives `#27272d`; hairline +20/+20/+20 gives `#2e2e33`). Body and
muted are Warp's text tones verbatim.

### The amber, honestly

The redesign report said amber was "sampled from
`skullpt-icon.svg`", but the v1.2 icon was monochrome (bone skull
on canvas, no amber anywhere), so there was nothing to sample. v1.5
fixed the premise instead of faking the sampling: the skull's crack
is now amber `#f5a623`, and the code samples the icon's most
saturated hex at runtime (`qtdesign.sample_amber()`, the crack's
chroma is 210; bone is 16, canvas is 5, so the pick is
unambiguous). The icon is the single source of truth for the
accent. If the icon cannot be read, the code falls back to the
documented `#f5a623`, the fallback is the documented value, not a
second guess.

`#f5a623` was chosen because near-black text on it passes WCAG AA
at 8.55:1 and amber text on canvas passes at 8.55:1 (see the
contrast table below). Hover and pressed states are derived by
mixing toward white and black by 8% (the 0to255 recipe), not picked
by eye.

### Contrast (R4)

Every text pair computed with the WCAG relative-luminance formula
(`qtdesign.contrast_ratio()`); AA is 4.5:1 for normal text. All ten
pairs pass, none close to the line.

| Pair | Ratio | AA |
| --- | --- | --- |
| Bone `#e8e4d8` on canvas `#1a1a1f` | 13.64 | Pass |
| Body `#c9c0ad` on canvas `#1a1a1f` | 9.60 | Pass |
| Muted `#aea69c` on canvas `#1a1a1f` | 7.21 | Pass |
| Bone on canvas-soft `#27272d` | 11.68 | Pass |
| Muted on canvas-soft | 6.18 | Pass |
| Near-black on amber (primary button) | 8.55 | Pass |
| Amber text on canvas | 8.55 | Pass |
| Amber text on canvas-soft | 7.33 | Pass |
| Near-black on amber-hover `#f6ad35` | 9.04 | Pass |
| Near-black on amber-pressed `#e19920` | 7.25 | Pass |

The muted tone is the weakest pair at 6.18:1 on canvas-soft, still
comfortably AA. No pair sits below 6:1.

## Typography

Inter for UI text, JetBrains Mono for code surfaces (debug report,
build report, file paths). Both are bundled as variable TTFs in
`skullpt/fonts/` and registered with Qt at startup
(`QFontDatabase.addApplicationFont`); the stylesheet falls back to
DejaVu Sans / DejaVu Sans Mono when the bundled fonts cannot load.
Both fonts are under the SIL Open Font License 1.1 (license texts
in `skullpt/fonts/OFL-Inter.txt` and `OFL-JetBrainsMono.txt`).

- UI text: Inter 13px, weight 400.
- Headings: Inter 15px, weight 600.
- Small / legend text: Inter 11px, muted.
- Mono surfaces: JetBrains Mono 13px.
- Stage pills: Inter 10px, weight 600, uppercase.
- No em dashes in any visible text (U33). Labels use "and", never a
  bare "&" (U11, Qt eats "&" as a mnemonic marker).

## Layout

- Main window: 1280x720 default, fits the 1366x768 screen with room
  (U5). Every window remembers its geometry in QSettings.
- Jobs home: icon rail 52px on the left; draft row on top; bento
  grid of job cards (3 columns, 8px gaps) plus engine, latest
  render, and could-not-read tiles; selected-job strip at the
  bottom; chat panel on the right.
- Render: viewport takes the left two thirds; parts list, part
  editor, and renders strip on the right third.
- Base spacing 4px; card padding 10px/8px; window margins 8px.
- Nothing is selected by default (U6): the Jobs window opens with
  no job selected, and the detail strip says so plainly.
- First run shows a start-here panel (try an example, or type
  "make ..." in the chat) instead of an empty grid.

## Elevation & Depth

Hairlines and surface contrast only, no drop shadows, ever (Warp's
rule, kept verbatim). Canvas is the floor; canvas-soft is one step
up (cards, inputs, menus); a 1px hairline border marks the edge of
a raised surface. The selected card swaps its hairline border for a
1px amber border. Menus sit on canvas-soft with a hairline border.
Tooltips are canvas-soft with a hairline border.

## Shapes

- Buttons: 4px radius, 36-40px tall (primary buttons 36px minimum
  height plus padding; the Cursor 40px reference is the ceiling).
- Cards: 6px radius.
- Stage pills: 8px radius, 2px/8px padding.
- Inputs: 4px radius.
- Progress bars: 4px radius, amber chunk.
- The viewport grid is drawn in the scene, not in chrome: grid step
  is span/10 in cm, and the legend states the true step.

## Components

- **Buttons.** Primary: amber fill, near-black text, weight 600.
  Secondary: canvas-soft fill, bone text, hairline border. Ghost:
  transparent, body text, canvas-soft on hover. Disabled primary
  uses amber-disabled with muted text. Focus: 1px amber border.
- **Stage pills.** Small uppercase pills for job states (DRAFT,
  APPROVED, BUILDING, CHECKING, REVIEW, FILED, FAILED) and build
  stages (PLAN, BUILD, CHECK, CRITIC, IMAGES, MESH). Monochrome
  plus amber only: APPROVED is amber fill with near-black text;
  BUILDING and CHECKING carry an amber border; FAILED reads via its
  text, not a red. Cursor's timeline-pill pattern, without Cursor's
  five pastel colors, SKULLPT has one accent.
- **Job cards.** QFrame, canvas-soft, hairline border, 6px radius:
  state pill plus title on top, summary in muted text, the gate
  button for the state at the bottom (Approve / Build / File /
  Retry / Open). Clicking the card selects the job; the selected
  card gets the amber border. Hover lifts the border one step.
- **Toasts.** Frameless canvas-soft note in the parent window's
  bottom-right corner, auto-dismiss after 4 seconds, no animation.
  Used for run completion and save confirmations only.
- **Icon rail.** QToolButtons with 24px bone Lucide icons, tooltips
  naming the window. Checked state (where used): canvas-soft
  background with a 2px amber left border.
- **Status bar.** Three zones: engine status (left, stretched),
  current run with progress bar and Cancel button (middle), free
  RAM with the adaptive floor (right).
- **Viewport.** Warm dark clear color; grid lines hairline-gray;
  axes X red, Y green, Z blue (the one place non-amber color is
  allowed, it is scene content, not chrome, and the legend names
  it). Selected part outlined in amber. Hover shows the part name
  in the legend bar.
- **Lists.** Canvas background, hairline border, 4px radius;
  selected item canvas-soft.
- **Tabs.** Selected tab gets a 2px amber bottom border.
- **Checkboxes.** 16px indicator, hairline border, canvas-soft
  fill; checked is amber fill with amber border.

### Icons (R2)

Lucide, vendored as SVG files in `skullpt/icons/` (38 icons),
recolored to bone at load time (`qtdesign.load_icon()` renders the
SVG through QSvgRenderer with `currentColor` replaced). License:
ISC, see `skullpt/icons/LICENSE` (Lucide Icons, copyright 2026).

Five icons were renamed upstream between the brief and the
vendoring; the Bench keeps the brief's file name and the vendored
file is the current Lucide icon, recorded here:

| File | Lucide source (main, 2026-11-14) | Why |
| --- | --- | --- |
| `trash-2.svg` | `trash.svg` | `trash-2` removed upstream |
| `alert-triangle.svg` | `triangle-exclamation-point.svg` | Lucide 1.68.0 renamed `*-alert` to `*-exclamation-point` |
| `grid.svg` | `grid-2x2.svg` | `grid` removed upstream |
| `wand-2.svg` | `wand-sparkles.svg` | `wand-2` renamed upstream |
| `help-circle.svg` | `circle-question-mark.svg` | `help-circle` renamed upstream |

Every vendored file was verified to return HTTP 200 and start with
`<svg`.

## Motion

Budget under 300ms for any motion; the Bench currently uses none
(toasts appear and disappear with no animation). If motion is added
later: ease-out, under 300ms, and off entirely when the system asks
for reduced motion. The viewport camera moves only when the user
drags it, no idle animation, no auto-rotate.

## Do's and Don'ts

- Do use amber only for primary actions, the approved state, the
  selected card, and the viewport selection outline.
- Do not introduce a second chromatic accent. Failed states read
  via text, never red.
- Do keep text bone, body, or muted on canvas or canvas-soft. Do
  not put muted text on any other background without re-checking
  the contrast table.
- Do keep buttons 36-40px tall with 4px radii, cards at 6px.
- Do not add a drop shadow. Hairlines and surface contrast carry
  elevation.
- Do not select anything by default. Do not auto-focus a destructive
  button.
- Do not put an em dash in any visible string. Do not put a bare
  "&" in a visible label.
- Do write copy in plain words (his words or plain facts, U9). Do
  not name third-party brands in documentation (U10), "Hugging
  Face" and "Blender" name real services the user must install or
  sign up for, and are the exceptions the product already uses.
- Do keep the amber sampled from the icon. If the icon changes,
  the accent changes with it, that is the point.
