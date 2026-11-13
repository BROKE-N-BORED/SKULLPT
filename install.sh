#!/usr/bin/env bash
# SKULLPT — install / update.
#
# What this does, in order:
#   1. Checks Python >= 3.10 (the shell core is standard-library only).
#   2. Creates a private .venv in this folder and installs PySide6 into
#      it — the Bench window's only dependency. Mint's Python refuses
#      `pip install` outside a venv, so the shell keeps its own. (The
#      core shell never touches pip; only the Bench window runs from
#      the venv.)
#   3. Checks Blender: reports the version it finds. With --with-blender
#      it downloads the pinned Blender LTS and installs it to
#      /opt/blender (+ a blender command on your PATH), replacing what
#      was there. Without the flag it never touches your Blender.
#   4. Runs the shell's own test suite as the install proof.
#   5. Prints a dependency report: everything checked, with versions.
#   6. Puts a SKULLPT launcher on the Desktop and in the app menu, and
#      a `skullpt` command on your PATH.
#
# Usage:
#   ./install.sh                     install (as above)
#   ./install.sh --with-blender      also install/upgrade Blender (needs sudo)
#   ./install.sh --with-image-engine also build the image engine from
#                                    source and download its model
#                                    (needs git, cmake, a C++ compiler)
set -euo pipefail

# Resolve our own real location (this script may be reached via a link).
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  case "$SOURCE" in /*) ;; *) SOURCE="$DIR/$SOURCE" ;; esac
done
cd -P "$(dirname "$SOURCE")"

BLENDER_VERSION="4.5.9"
BLENDER_TARBALL="blender-${BLENDER_VERSION}-linux-x64.tar.xz"
BLENDER_URL="https://download.blender.org/release/Blender4.5/${BLENDER_TARBALL}"
SD_REPO="https://github.com/leejet/stable-diffusion.cpp"
SD_MODEL_FILE="LCM_Dreamshaper_v7-f16.gguf"
SD_MODEL_URL="https://huggingface.co/Steward/lcm-dreamshaper-v7-gguf/resolve/main/${SD_MODEL_FILE}"
SD_MODEL_HOME="$HOME/design-shell-models"
WITH_BLENDER=0
WITH_IMAGE=0
for arg in "$@"; do
  case "$arg" in
    --with-blender) WITH_BLENDER=1 ;;
    --with-image-engine) WITH_IMAGE=1 ;;
  esac
done

echo "== SKULLPT install =="

# --- 1. Python -------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.10+ first (sudo apt install python3)." >&2
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "ERROR: Python $PY_VER is too old — the shell needs 3.10 or newer." >&2
  exit 1
}
echo "python3: $PY_VER OK (the shell core is standard library only)"

# --- 2. PySide6 (the Bench window) in a private .venv -----------------------
# The Bench is a Qt window, so it needs PySide6. Mint's Python is
# externally managed: `pip install PySide6` and `pip install --user`
# both fail outright, so the shell keeps a private virtualenv in this
# folder. The core shell stays standard-library only — only the Bench
# window runs from the venv.
VENV_PY=".venv/bin/python"
QT_STATUS="NOT FOUND"
QT_FIX="run ./install.sh again"
VENV_FAILED=0
if [ ! -x "$VENV_PY" ]; then
  rm -rf .venv
  echo "Creating the Bench window's private folder (.venv)..."
  if ! python3 -m venv .venv; then
    rm -rf .venv
    echo "PySide6: CANNOT INSTALL — python3-venv is missing on this machine."
    echo "         Fix: sudo apt install python3.12-venv"
    echo "         then run ./install.sh again. (The CLI still works — only"
    echo "         the Bench window needs the venv.)"
    QT_FIX="sudo apt install python3.12-venv, then run ./install.sh again"
    VENV_FAILED=1
  fi
fi
if [ "$VENV_FAILED" = "0" ]; then
  if ! "$VENV_PY" -c "import PySide6" 2>/dev/null; then
    echo "Installing PySide6-Essentials into .venv (one time, may take a minute)..."
    if ! "$VENV_PY" -m pip install PySide6-Essentials; then
      echo "PySide6: install FAILED — check the network and run ./install.sh again."
      echo "         (The CLI still works — only the Bench window needs PySide6.)"
      QT_FIX="check the network, then run ./install.sh again"
    fi
  fi
  if "$VENV_PY" -c "import PySide6" 2>/dev/null; then
    QT_VER="$("$VENV_PY" -c "import PySide6; print(PySide6.__version__)" 2>/dev/null || echo '?')"
    QT_STATUS="$QT_VER in .venv"
    echo "PySide6: $QT_VER OK (in .venv — the Bench window can open)"
  else
    echo "PySide6: NOT FOUND — the Bench window needs it (the CLI still works)."
  fi
fi

# --- 3. Blender ------------------------------------------------------------
find_blender() {
  if [ -n "${DESIGNSHELL_BLENDER:-}" ] && [ -x "${DESIGNSHELL_BLENDER}" ]; then
    echo "$DESIGNSHELL_BLENDER"; return
  fi
  if command -v blender >/dev/null 2>&1; then command -v blender; return; fi
  if [ -x /opt/blender/blender ]; then echo /opt/blender/blender; return; fi
  echo ""
}

BLENDER_BIN="$(find_blender)"
if [ "$WITH_BLENDER" = "1" ]; then
  echo "Installing Blender $BLENDER_VERSION to /opt/blender (sudo)..."
  TMPD="$(mktemp -d)"
  curl -fSL "$BLENDER_URL" -o "$TMPD/$BLENDER_TARBALL"
  tar -xf "$TMPD/$BLENDER_TARBALL" -C "$TMPD"
  sudo rm -rf /opt/blender
  sudo mv "$TMPD/blender-${BLENDER_VERSION}-linux-x64" /opt/blender
  sudo ln -sf /opt/blender/blender /usr/local/bin/blender
  rm -rf "$TMPD"
  BLENDER_BIN="/opt/blender/blender"
  echo "Blender installed."
fi

if [ -n "$BLENDER_BIN" ]; then
  BLENDER_VER="$("$BLENDER_BIN" --version 2>/dev/null | head -1 || echo 'version unknown')"
  echo "blender: $BLENDER_BIN — $BLENDER_VER"
else
  echo "blender: NOT FOUND — scene builds will fail until Blender exists."
  echo "         Fix: ./install.sh --with-blender   (or install Blender yourself,"
  echo "         or set DESIGNSHELL_BLENDER to its path)"
fi

# --- 3b. Image engine (stable-diffusion.cpp + LCM model) --------------------
find_sd() {
  if [ -n "${DESIGNSHELL_SD_CLI:-}" ] && [ -x "${DESIGNSHELL_SD_CLI}" ]; then
    echo "$DESIGNSHELL_SD_CLI"; return
  fi
  if command -v sd-cli >/dev/null 2>&1; then command -v sd-cli; return; fi
  for c in /opt/sd.cpp/bin/sd-cli "$HOME/.local/bin/sd-cli" "$HOME/sd.cpp/build/bin/sd-cli"; do
    if [ -x "$c" ]; then echo "$c"; return; fi
  done
  echo ""
}

find_sd_model() {
  if [ -n "${DESIGNSHELL_SD_MODEL:-}" ] && [ -f "${DESIGNSHELL_SD_MODEL}" ]; then
    echo "$DESIGNSHELL_SD_MODEL"; return
  fi
  if [ -f "$SD_MODEL_HOME/$SD_MODEL_FILE" ]; then echo "$SD_MODEL_HOME/$SD_MODEL_FILE"; return; fi
  local first
  first="$(ls "$SD_MODEL_HOME"/*.gguf 2>/dev/null | head -1 || true)"
  echo "$first"
}

SD_BIN="$(find_sd)"
if [ "$WITH_IMAGE" = "1" ]; then
  if [ -z "$SD_BIN" ]; then
    # The prebuilt Linux binary needs glibc 2.43 (Ubuntu 26.04); Mint
    # ships 2.39, so the engine is built from source here instead.
    for tool in git cmake g++; do
      if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: building the image engine needs $tool." >&2
        echo "       Fix: sudo apt install git cmake build-essential" >&2
        echo "       then run ./install.sh --with-image-engine again." >&2
        exit 1
      fi
    done
    echo "Building the image engine (stable-diffusion.cpp) from source..."
    echo "(one-time compile — several minutes on a small machine)"
    TMPD="$(mktemp -d)"
    git clone --depth 1 --recursive "$SD_REPO" "$TMPD/sd"
    cmake -S "$TMPD/sd" -B "$TMPD/sd/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$TMPD/sd/build" -j"$(nproc)" >/dev/null
    sudo rm -rf /opt/sd.cpp
    sudo mkdir -p /opt/sd.cpp/bin
    sudo cp "$TMPD/sd/build/bin/"* /opt/sd.cpp/bin/
    sudo ln -sf /opt/sd.cpp/bin/sd-cli /usr/local/bin/sd-cli
    rm -rf "$TMPD"
    SD_BIN="/opt/sd.cpp/bin/sd-cli"
    echo "Image engine built: $SD_BIN"
  fi
  if [ -z "$(find_sd_model)" ]; then
    echo "Downloading the image model ($SD_MODEL_FILE, about 2.1 GB)..."
    mkdir -p "$SD_MODEL_HOME"
    curl -fSL "$SD_MODEL_URL" -o "$SD_MODEL_HOME/$SD_MODEL_FILE"
    echo "Model downloaded to $SD_MODEL_HOME/$SD_MODEL_FILE"
  fi
fi

SD_BIN="$(find_sd)"
SD_MODEL="$(find_sd_model)"
if [ -n "$SD_BIN" ]; then
  echo "sd-cli: $SD_BIN"
else
  echo "sd-cli: NOT FOUND — image generation will refuse until the engine exists."
  echo "        Fix: ./install.sh --with-image-engine   (builds it from source)"
fi
if [ -n "$SD_MODEL" ]; then
  echo "image model: $SD_MODEL"
else
  echo "image model: NOT FOUND — download $SD_MODEL_FILE (about 2.1 GB) into"
  echo "        $SD_MODEL_HOME/  or run ./install.sh --with-image-engine"
fi

# --- 4. Verify -------------------------------------------------------------
echo "Running the shell's test suite (fast suite; the Blender end-to-end"
echo "builds are skipped here — they run when Blender work is tested)..."
# With a display, the venv python runs the suite so the Qt window tests
# prove the Bench for real; without one, system python runs the core
# suite (the Qt tests skip cleanly there — they need a display).
TEST_PY=python3
if [ -x "$VENV_PY" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  TEST_PY="$VENV_PY"
fi
# The test run must never fail the install: on some machines the Qt
# tests segfault at interpreter exit *after* printing OK (a known
# PySide6 teardown quirk), so the result is judged by the summary line.
TEST_LOG="$(mktemp)"
DESIGNSHELL_SKIP_E2E=1 "$TEST_PY" -m unittest discover -s tests >"$TEST_LOG" 2>&1 || true
tail -3 "$TEST_LOG"
if grep -q "^OK" "$TEST_LOG"; then
  echo "(test suite passed)"
else
  echo "(test suite reported a problem — see above; the install continues)"
fi
rm -f "$TEST_LOG"

# --- 5. Dependency report --------------------------------------------------
echo ""
echo "== Dependency report =="
echo "python3   $PY_VER (needs >= 3.10)"
if [ "$QT_STATUS" != "NOT FOUND" ]; then
  echo "PySide6   $QT_STATUS (Bench window ready)"
else
  echo "PySide6   NOT FOUND ($QT_FIX)"
fi
if [ -n "$BLENDER_BIN" ]; then
  echo "blender   ${BLENDER_VER:-found} at $BLENDER_BIN (pinned LTS: $BLENDER_VERSION)"
else
  echo "blender   MISSING (pinned LTS: $BLENDER_VERSION — ./install.sh --with-blender)"
fi
if [ -n "$SD_BIN" ]; then
  echo "sd-cli    $SD_BIN (image engine: stable-diffusion.cpp)"
else
  echo "sd-cli    MISSING (./install.sh --with-image-engine)"
fi
if [ -n "$SD_MODEL" ]; then
  echo "img model $SD_MODEL"
else
  echo "img model MISSING ($SD_MODEL_FILE into $SD_MODEL_HOME/)"
fi
echo "core      stdlib only — no pip packages in the system Python"

# --- 6. Desktop launcher + `skullpt` command -------------------------------
# A failed launcher must never fail the install, so this runs as a
# function whose failure only prints a note.
make_launcher() {
  local install_dir desktop_dir apps_dir
  install_dir="$(pwd)"
  desktop_dir="$HOME/Desktop"
  if command -v xdg-user-dir >/dev/null 2>&1; then
    local xdg_desktop
    xdg_desktop="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    [ -n "$xdg_desktop" ] && desktop_dir="$xdg_desktop"
  fi
  apps_dir="$HOME/.local/share/applications"
  mkdir -p "$apps_dir" "$HOME/.local/bin" "$desktop_dir"
  # The absolute path goes straight into Exec — no nested quotes (GLib's
  # desktop parser rejects them and the whole file fails).
  cat > "$desktop_dir/SKULLPT.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SKULLPT
Comment=SKULLPT — native Linux design and asset workshop
Exec="$install_dir/run.sh"
Icon=$install_dir/skullpt-icon.svg
Terminal=false
Categories=Development;Graphics;3DGraphics;
EOF
  cp "$desktop_dir/SKULLPT.desktop" "$apps_dir/SKULLPT.desktop"
  chmod +x "$desktop_dir/SKULLPT.desktop" "$apps_dir/SKULLPT.desktop"
  ln -sf "$install_dir/run.sh" "$HOME/.local/bin/skullpt"
}
if make_launcher; then
  echo "launcher  SKULLPT on your Desktop and in the app menu"
  echo "command   skullpt (works from any folder)"
else
  echo "launcher  NOT CREATED — start with ./run.sh instead"
fi
echo ""
echo "Done. Start it from the SKULLPT launcher, with skullpt, or with ./run.sh"
