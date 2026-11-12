#!/usr/bin/env bash
# Design Shell — run.
#
#   ./run.sh              start the Bench — the shell's window (live render
#                         view + chat + folders), a native Linux desktop
#                         window. No browser, no web page.
#   ./run.sh webbench <ws>  serve the legacy web Bench instead (browser).
#   ./run.sh demo <ws>    run the built-in demo into a workspace, e.g.
#                         ./run.sh demo ~/design-shell-workspace
#   ./run.sh <args...>    pass anything straight to the shell CLI, e.g.
#                         ./run.sh view examples/workbench.description.json
#                         ./run.sh new ~/design-shell-workspace examples/tavern-room.description.json
#
# Preflight: checks python3, and warns (does not block) if Blender is
# missing — 2D work runs fine without it; scene builds will say plainly
# that Blender was not found.
set -euo pipefail
cd "$(dirname "$0")"

command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 not found — run ./install.sh first." >&2; exit 1; }

if [ -z "${DESIGNSHELL_BLENDER:-}" ] && ! command -v blender >/dev/null 2>&1 \
   && [ ! -x /opt/blender/blender ]; then
  echo "Note: Blender not found — scenes cannot build yet (./install.sh --with-blender fixes it)."
fi

if [ "$#" -eq 0 ]; then
  exec python3 -m designshell bench "$HOME/design-shell-workspace"
else
  exec python3 -m designshell "$@"
fi
