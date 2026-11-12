#!/usr/bin/env bash
# Design Shell — install / update.
#
# What this does, in order:
#   1. Checks Python >= 3.10 (the shell is standard-library only —
#      there are deliberately NO pip packages to install or upgrade).
#   2. Checks Blender: reports the version it finds. With --with-blender
#      it downloads the pinned Blender LTS and installs it to
#      /opt/blender (+ a blender command on your PATH), replacing what
#      was there. Without the flag it never touches your Blender.
#   3. Runs the shell's own test suite as the install proof.
#   4. Prints a dependency report: everything checked, with versions.
#
# Usage:
#   ./install.sh                     check + verify only
#   ./install.sh --with-blender      also install/upgrade Blender (needs sudo)
#   ./install.sh --with-image-engine also build the image engine from
#                                    source and download its model
#                                    (needs git, cmake, a C++ compiler)
set -euo pipefail
cd "$(dirname "$0")"

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

echo "== Design Shell install =="

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

# --- 1b. PySide6 (the Bench window) -----------------------------------------
if python3 -c "import PySide6" 2>/dev/null; then
  QT_VER="$(python3 -c "import PySide6; print(PySide6.__version__)" 2>/dev/null || echo '?')"
  echo "PySide6: $QT_VER OK (the Bench window can open)"
else
  echo "PySide6: NOT FOUND — the Bench window needs it (the CLI still works)."
  echo "         Fix: pip install PySide6   (Code Forge needs it too, so it"
  echo "         is usually already installed on this machine)"
fi

# --- 2. Blender ------------------------------------------------------------
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

# --- 2b. Image engine (stable-diffusion.cpp + LCM model) --------------------
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

# --- 3. Verify -------------------------------------------------------------
echo "Running the shell's test suite (fast suite; the Blender end-to-end"
echo "builds are skipped here — they run when Blender work is tested)..."
DESIGNSHELL_SKIP_E2E=1 python3 -m unittest discover -s tests 2>&1 | tail -3

# --- 4. Dependency report --------------------------------------------------
echo ""
echo "== Dependency report =="
echo "python3   $PY_VER (needs >= 3.10)"
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
echo "pip pkgs  none — the shell runs on the Python standard library alone"
echo ""
echo "Done. Start it with: ./run.sh"
