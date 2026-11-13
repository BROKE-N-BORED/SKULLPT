#!/usr/bin/env bash
# SKULLPT — run.
#
#   ./run.sh              start the Bench — SKULLPT's window (live render
#                         view + chat + folders), a native Linux desktop
#                         window. No browser, no web page.
#   ./run.sh webbench <ws>  serve the legacy web Bench instead (browser).
#   ./run.sh demo <ws>    run the built-in demo into a workspace, e.g.
#                         ./run.sh demo ~/design-shell-workspace
#   ./run.sh <args...>    pass anything straight to the shell CLI, e.g.
#                         ./run.sh view examples/workbench.description.json
#                         ./run.sh new ~/design-shell-workspace examples/tavern-room.description.json
#
# The Bench window runs from the private .venv install.sh creates (it has
# PySide6); everything else runs on the system Python. If the venv is
# missing or broken, this falls back to system python3 and the Bench
# says plainly what to do (double-click Install SKULLPT).
#
# Preflight: checks python3, and warns (does not block) if Blender is
# missing — 2D work runs fine without it; scene builds will say plainly
# that Blender was not found.
set -euo pipefail

# Resolve our own real location (run.sh is reached via the
# ~/.local/bin/skullpt symlink as well as directly).
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
cd -P "$(dirname "$SOURCE")"

command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 not found — SKULLPT needs Python 3.10 or newer." >&2; exit 1; }

# Prefer the private .venv (it has PySide6 for the Bench window); fall
# back to system python3 when the venv is missing or lacks PySide6.
PY=python3
if [ -x .venv/bin/python ] && .venv/bin/python -c "import PySide6" 2>/dev/null; then
  PY=.venv/bin/python
fi

if [ -z "${DESIGNSHELL_BLENDER:-}" ] && ! command -v blender >/dev/null 2>&1 \
   && [ ! -x /opt/blender/blender ]; then
  echo "Note: Blender not found — scenes cannot build yet (the installer's Blender option adds it — see the README's install options)."
fi

if [ "$#" -eq 0 ]; then
  exec "$PY" -m skullpt bench "$HOME/design-shell-workspace"
else
  exec "$PY" -m skullpt "$@"
fi
