#!/usr/bin/env bash
# SKULLPT — install / update.
#
# Double-click Install SKULLPT (or run ./install.sh --gui): the install
# runs behind a progress window — no terminal needed. If it needs a
# system package (python3-venv for the Bench window's private folder,
# or build tools for the optional image engine), it installs it itself
# with one GUI password prompt and carries on. When it finishes it asks
# whether to open SKULLPT now. Everything is logged to install.log in
# this folder, so a failure can be diagnosed from the log.
#
# The progress window uses the first dialog tool found, in the order
# Cinnamon's own spices probe: xenity (Mint's maintained Zenity fork),
# zenity, yad. All three take the same flags for the dialogs used here.
# If none is installed, --gui falls back to the terminal installer
# below — the install still works and is still logged, there is just
# no progress window. That is the degraded path, not the plan.
#
# What this does, in order:
#   1. Checks Python >= 3.10 (the shell core is standard-library only).
#   2. Creates a private .venv in this folder and installs PySide6 into
#      it — the Bench window's only dependency. Mint's Python refuses
#      to install packages outside a venv, so the shell keeps its own.
#      (The core shell never touches pip; only the Bench window runs
#      from the venv.) If python3-venv is missing, the installer
#      installs that one system package itself (one GUI password
#      prompt) and retries — it never asks the user to type anything.
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
#   ./install.sh                     install (GUI when launched without
#                                    a terminal and a dialog tool
#                                    exists, else the terminal installer)
#   ./install.sh --gui               install with the progress window
#   ./install.sh --terminal          install in the terminal
#   ./install.sh --with-blender      also install/upgrade Blender (needs
#                                    admin permission — it will ask)
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
SCRIPT_PATH="$(pwd)/install.sh"

BLENDER_VERSION="4.5.9"
BLENDER_TARBALL="blender-${BLENDER_VERSION}-linux-x64.tar.xz"
BLENDER_URL="https://download.blender.org/release/Blender4.5/${BLENDER_TARBALL}"
# SHA-256 of the Blender 4.5.9 tarball, from Blender's official
# checksum file (download.blender.org/release/Blender4.5/blender-4.5.9.sha256).
BLENDER_SHA256="dcdc3eca6c9825bb35a8033b689c053f3cb5a9b0cd2a61b2eac2a49436b4ad3d"
SD_REPO="https://github.com/leejet/stable-diffusion.cpp"
SD_MODEL_FILE="LCM_Dreamshaper_v7-f16.gguf"
SD_MODEL_URL="https://huggingface.co/Steward/lcm-dreamshaper-v7-gguf/resolve/main/${SD_MODEL_FILE}"
# SHA-256 of LCM_Dreamshaper_v7-f16.gguf (2,134,675,232 bytes — the
# Hugging Face repo's file, unchanged since 2023-11-28).
SD_MODEL_SHA256="0642060766117fdeb8cae0ccdc73ee974ac5779be1112d0a82f2b965bbd00e15"
SD_MODEL_HOME="$HOME/design-shell-models"
# Persistent download cache: a mktemp dir is wiped on every run, so a
# download that can be resumed needs a place that survives.
DOWNLOAD_CACHE="$HOME/.cache/skullpt"
WITH_BLENDER=0
WITH_IMAGE=0
GUI_WANTED=""   # "gui" | "terminal" | "" (auto-detect)
WORKER=0
for arg in "$@"; do
  case "$arg" in
    --with-blender) WITH_BLENDER=1 ;;
    --with-image-engine) WITH_IMAGE=1 ;;
    --gui) GUI_WANTED="gui" ;;
    --terminal) GUI_WANTED="terminal" ;;
    --_run-worker) WORKER=1 ;;
  esac
done

# --- Dialog tool -----------------------------------------------------------
# First one found, in the order Cinnamon's own spices probe. Empty when
# none is installed.
find_dialog() {
  for tool in xenity zenity yad; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "$tool"
      return
    fi
  done
  echo ""
}

# --- Progress + outcome markers (worker side) ------------------------------
# step <percent> <label>: in GUI mode prints the lines the progress
# dialog reads ("# text" sets the label, a bare number sets the
# percentage); in terminal mode prints a plain section header. Every
# other output line passes through to the log; the dialog ignores
# lines that are neither a number nor a "# " line. (No other install
# output is a bare number, so nothing else can move the bar by
# accident.) The number goes last: the dialog auto-closes on 100, so
# nothing may be written to the pipe after it.
step() {
  if [ "${SKULLPT_GUI_MODE:-0}" = "1" ]; then
    echo "# $2"
    echo "$1"
  else
    echo "== $2 =="
  fi
}

# gui_fail <text>: records a fatal failure's plain-language reason for
# the GUI wrapper, which shows it in an error dialog. Terminal mode
# already prints the reason itself, so this stays quiet there.
gui_fail() {
  if [ "${SKULLPT_GUI_MODE:-0}" = "1" ]; then
    echo "INSTALL-FAILED: $1"
  fi
}

# gui_warn <text>: records a non-fatal warning (the install finished
# but the Bench window is not ready) for the GUI wrapper, which shows
# it in a warning dialog instead of the open-it-now question.
gui_warn() {
  if [ "${SKULLPT_GUI_MODE:-0}" = "1" ]; then
    echo "INSTALL-WARNING: $1"
  fi
}

# --- Privileged installs ---------------------------------------------------
# Installs system packages with ONE pkexec call — a GUI password
# prompt, no terminal. Used for the python3-venv package and for the
# image-engine build tools. Returns 0 on success; 1 when pkexec is
# missing, the user declines, or apt fails. Callers say so in plain
# language; nobody is ever asked to type a command.
apt_install_via_pkexec() {
  if ! command -v pkexec >/dev/null 2>&1; then
    return 1
  fi
  pkexec sh -c "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y $*"
}

# --- Verified downloads ----------------------------------------------------
# download_verified <url> <dest> <sha256> <label>
# Downloads a file with resume (curl -C - picks up where an interrupted
# run left off) and verifies its SHA-256. A file already present with a
# matching hash is left alone. If the download or the hash check fails,
# the partial file is deleted and the download retried once from
# scratch; a second failure fails with a plain message. Returns 0 on a
# verified file, 1 otherwise.
download_verified() {
  local url="$1" dest="$2" want="$3" label="$4"
  if [ -f "$dest" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ]; then
    echo "$label is already downloaded and verified."
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  local attempt
  for attempt in 1 2; do
    if [ "$attempt" = "2" ]; then
      echo "$label did not come down cleanly — downloading it again from scratch..."
      rm -f "$dest"
    fi
    if ! curl -fSL -C - "$url" -o "$dest"; then
      if [ "$attempt" = "1" ]; then
        continue
      fi
      echo "ERROR: could not download $label — check the network, then re-run the installer." >&2
      return 1
    fi
    if [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$want" ]; then
      echo "$label downloaded and verified."
      return 0
    fi
  done
  echo "ERROR: $label downloaded but does not match its checksum — the download may be corrupted." >&2
  rm -f "$dest"
  return 1
}

# --- The install body (runs in the worker subprocess) ----------------------
do_install() {
  # --- 1. Python -----------------------------------------------------------
  step 5 "Checking Python"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found — SKULLPT needs Python 3.10 or newer." >&2
    gui_fail "Python was not found — SKULLPT needs Python 3.10 or newer."
    return 1
  fi
  PY_VER="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "ERROR: Python $PY_VER is too old — the shell needs 3.10 or newer." >&2
    gui_fail "Python $PY_VER is too old — SKULLPT needs 3.10 or newer."
    return 1
  fi
  echo "python3: $PY_VER OK (the shell core is standard library only)"
  # The venv package for THIS Python (python3.12-venv on Mint 22).
  VENV_PKG="python3.$(python3 -c 'import sys; print(sys.version_info.minor)')-venv"

  # --- 2. PySide6 (the Bench window) in a private .venv ---------------------
  # The Bench is a Qt window, so it needs PySide6. Mint's Python is
  # externally managed: installing packages outside a venv fails
  # outright, so the shell keeps a private virtualenv in this folder.
  # The core shell stays standard-library only — only the Bench window
  # runs from the venv.
  step 15 "Setting up the Bench window"
  VENV_PY=".venv/bin/python"
  QT_STATUS="NOT FOUND"
  QT_FIX="re-run the installer"
  WARNING=""
  VENV_FAILED=0
  if ! ensure_venv; then
    VENV_FAILED=1
  fi
  if [ "$VENV_FAILED" = "0" ]; then
    if ! "$VENV_PY" -c "import PySide6" 2>/dev/null; then
      echo "Installing PySide6-Essentials into .venv (one time, may take a minute)..."
      if ! "$VENV_PY" -m pip install PySide6-Essentials; then
        echo "PySide6: install FAILED — check the network, then re-run the installer."
        echo "         (The CLI still works — only the Bench window needs PySide6.)"
        QT_FIX="check the network, then re-run the installer"
        WARNING="PySide6 could not be installed — check the network, then re-run the installer."
      fi
    fi
    if "$VENV_PY" -c "import PySide6" 2>/dev/null; then
      QT_VER="$("$VENV_PY" -c "import PySide6; print(PySide6.__version__)" 2>/dev/null || echo '?')"
      QT_STATUS="$QT_VER in .venv"
      echo "PySide6: $QT_VER OK (in .venv — the Bench window can open)"
    else
      echo "PySide6: NOT FOUND — the Bench window needs it (the CLI still works)."
      if [ -z "$WARNING" ]; then
        WARNING="PySide6 is not installed — re-run the installer."
      fi
    fi
  fi

  # --- 3. Blender ------------------------------------------------------------
  step 55 "Checking Blender and the image engine"
  BLENDER_BIN="$(find_blender)"
  if [ "$WITH_BLENDER" = "1" ]; then
    # Never downgrade: if the Blender already on the machine is newer
    # than the pinned LTS, leave it alone and say so plainly. An
    # unparseable version falls through to the install (better to
    # install a known-good Blender than to trust a version string
    # that could not be read).
    FOUND_VER=""
    if [ -n "$BLENDER_BIN" ]; then
      FOUND_VER="$("$BLENDER_BIN" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    fi
    if [ -n "$FOUND_VER" ] && [ "$FOUND_VER" != "$BLENDER_VERSION" ] \
       && [ "$(printf '%s\n%s\n' "$BLENDER_VERSION" "$FOUND_VER" | sort -V | head -1)" = "$BLENDER_VERSION" ]; then
      echo "Blender $FOUND_VER is already installed — newer than the pinned $BLENDER_VERSION, so leaving it alone."
    else
      echo "Installing Blender $BLENDER_VERSION to /opt/blender (this needs admin permission — a password window will appear)..."
      mkdir -p "$DOWNLOAD_CACHE"
      if ! download_verified "$BLENDER_URL" "$DOWNLOAD_CACHE/$BLENDER_TARBALL" \
          "$BLENDER_SHA256" "Blender $BLENDER_VERSION"; then
        gui_fail "Blender could not be downloaded — check the network, then re-run the installer."
        return 1
      fi
      TMPD="$(mktemp -d)"
      tar -xf "$DOWNLOAD_CACHE/$BLENDER_TARBALL" -C "$TMPD"
      EXTRACTED="$TMPD/blender-${BLENDER_VERSION}-linux-x64"
      pkexec sh -c 'rm -rf /opt/blender && mv "$1" /opt/blender && ln -sf /opt/blender/blender /usr/local/bin/blender' _ "$EXTRACTED"
      rm -rf "$TMPD"
      BLENDER_BIN="/opt/blender/blender"
      echo "Blender installed."
    fi
  fi

  if [ -n "$BLENDER_BIN" ]; then
    BLENDER_VER="$("$BLENDER_BIN" --version 2>/dev/null | head -1 || echo 'version unknown')"
    echo "blender: $BLENDER_BIN — $BLENDER_VER"
  else
    echo "blender: NOT FOUND — scene builds will fail until Blender exists."
    echo "         The installer can add it (see the README's install options),"
    echo "         or install Blender yourself."
  fi

  # --- 3b. Image engine (stable-diffusion.cpp + LCM model) --------------------
  SD_BIN="$(find_sd)"
  if [ "$WITH_IMAGE" = "1" ]; then
    if [ -z "$SD_BIN" ]; then
      # The prebuilt Linux binary needs glibc 2.43 (Ubuntu 26.04); Mint
      # ships 2.39, so the engine is built from source here instead.
      MISSING_TOOLS=""
      for tool in git cmake g++; do
        if ! command -v "$tool" >/dev/null 2>&1; then
          MISSING_TOOLS="$MISSING_TOOLS $tool"
        fi
      done
      if [ -n "$MISSING_TOOLS" ]; then
        echo "The image engine needs some build tools first — asking for permission to install them..."
        if ! apt_install_via_pkexec git cmake build-essential; then
          echo "ERROR: building the image engine needs git, cmake, and a C++ compiler," >&2
          echo "       and they could not be installed. Re-run the installer and allow it when asked." >&2
          gui_fail "The image engine needs build tools (git, cmake, a C++ compiler) and they could not be installed."
          return 1
        fi
        for tool in git cmake g++; do
          if ! command -v "$tool" >/dev/null 2>&1; then
            echo "ERROR: building the image engine needs $tool, and it is still missing after the install." >&2
            gui_fail "The image engine needs build tools (git, cmake, a C++ compiler) and they could not be installed."
            return 1
          fi
        done
      fi
      echo "Building the image engine (stable-diffusion.cpp) from source..."
      echo "(one-time compile — several minutes on a small machine)"
      TMPD="$(mktemp -d)"
      git clone --depth 1 --recursive "$SD_REPO" "$TMPD/sd"
      cmake -S "$TMPD/sd" -B "$TMPD/sd/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
      # Build parallelism is capped on small machines: a full -j$(nproc)
      # compile can eat the whole 8 GB and thrash. 8 GB or less gets
      # -j2; bigger machines use all their cores.
      MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
      if [ "${MEM_KB:-0}" -le 8388608 ]; then
        BUILD_JOBS=2
      else
        BUILD_JOBS="$(nproc)"
      fi
      cmake --build "$TMPD/sd/build" -j"$BUILD_JOBS" >/dev/null
      pkexec sh -c 'rm -rf /opt/sd.cpp && mkdir -p /opt/sd.cpp/bin && cp "$1/bin/"* /opt/sd.cpp/bin/ && ln -sf /opt/sd.cpp/bin/sd-cli /usr/local/bin/sd-cli' _ "$TMPD/sd/build"
      rm -rf "$TMPD"
      SD_BIN="/opt/sd.cpp/bin/sd-cli"
      echo "Image engine built: $SD_BIN"
    fi
    if [ -f "$SD_MODEL_HOME/$SD_MODEL_FILE" ] || [ -z "$(find_sd_model)" ]; then
      # A pinned model already on disk is verified — a partial or
      # corrupt file from an interrupted run is resumed, not trusted.
      # (When some other .gguf is already there and the pinned file is
      # not, the download is skipped as before.)
      mkdir -p "$SD_MODEL_HOME"
      if ! download_verified "$SD_MODEL_URL" "$SD_MODEL_HOME/$SD_MODEL_FILE" \
          "$SD_MODEL_SHA256" "the image model ($SD_MODEL_FILE, about 2.1 GB)"; then
        gui_fail "The image model could not be downloaded — check the network, then re-run the installer."
        return 1
      fi
    fi
  fi

  SD_BIN="$(find_sd)"
  SD_MODEL="$(find_sd_model)"
  if [ -n "$SD_BIN" ]; then
    echo "sd-cli: $SD_BIN"
  else
    echo "sd-cli: NOT FOUND — image generation will refuse until the engine exists."
    echo "        (The README's install options cover adding it.)"
  fi
  if [ -n "$SD_MODEL" ]; then
    echo "image model: $SD_MODEL"
  else
    echo "image model: NOT FOUND — download $SD_MODEL_FILE (about 2.1 GB) into"
    echo "        $SD_MODEL_HOME/ (the installer's image-engine option does this for you —"
    echo "        see the README's install options)."
  fi

  # --- 4. Verify -------------------------------------------------------------
  step 70 "Running the test suite"
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
  # If mktemp fails (a broken TMPDIR), fall back to a file in the
  # install folder — a missing temp file must never fail the install.
  TEST_LOG="$(mktemp 2>/dev/null || echo "$PWD/.test-output.log")"
  DESIGNSHELL_SKIP_E2E=1 "$TEST_PY" -m unittest discover -s tests >"$TEST_LOG" 2>&1 || true
  tail -3 "$TEST_LOG"
  if grep -q "^OK" "$TEST_LOG"; then
    echo "(test suite passed)"
  else
    # Keep the failure output where the user can find it: the install
    # log only shows the last three lines, and "see above" is no help
    # in the GUI installer. The install still completes either way.
    cp "$TEST_LOG" "$PWD/test-output.log" 2>/dev/null || true
    echo "(test suite reported a problem — details are in test-output.log in the SKULLPT folder; the install continues)"
  fi
  rm -f "$TEST_LOG"

  # --- 5. Dependency report --------------------------------------------------
  step 85 "Finishing up"
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
    echo "blender   MISSING (pinned LTS: $BLENDER_VERSION — the installer can add it; see the README)"
  fi
  if [ -n "$SD_BIN" ]; then
    echo "sd-cli    $SD_BIN (image engine: stable-diffusion.cpp)"
  else
    echo "sd-cli    MISSING (image engine — see the README's install options)"
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
  if make_launcher; then
    echo "launcher  SKULLPT on your Desktop and in the app menu"
    echo "command   skullpt (works from any folder)"
  else
    echo "launcher  NOT CREATED — re-run the installer to try again"
  fi
  echo ""
  echo "Done. Start it from the SKULLPT launcher on your Desktop or in the app menu."
  if [ -n "$WARNING" ]; then
    gui_warn "$WARNING"
  fi
  step 100 "Done"
  return 0
}

# Creates the private .venv. If creation fails because python3-venv is
# missing, installs that one system package via pkexec (one GUI
# password prompt) and retries. Returns 0 with a working .venv; 1
# (with a plain-language note, and WARNING set for the GUI wrapper)
# when the venv cannot be made. Never asks the user to type anything.
ensure_venv() {
  if [ -x "$VENV_PY" ]; then
    return 0
  fi
  rm -rf .venv
  echo "Creating the Bench window's private folder (.venv)..."
  # If mktemp fails (a broken TMPDIR), fall back to a file in the
  # install folder — a missing temp file must never fail the venv.
  local venv_err
  venv_err="$(mktemp 2>/dev/null || echo "$PWD/.venv-error.log")"
  if python3 -m venv .venv 2>"$venv_err"; then
    rm -f "$venv_err"
    return 0
  fi
  if grep -qi "ensurepip\|python3.*-venv" "$venv_err"; then
    rm -f "$venv_err"
    echo "SKULLPT needs one system package to make its private folder — asking for permission to install it..."
    if apt_install_via_pkexec "$VENV_PKG"; then
      rm -rf .venv
      if python3 -m venv .venv 2>/dev/null; then
        return 0
      fi
    fi
    echo "PySide6: CANNOT INSTALL — SKULLPT needs one system package to"
    echo "         install, and the install could not add it. Re-run the"
    echo "         installer and allow it when asked. (The CLI still"
    echo "         works — only the Bench window needs the venv.)"
    QT_FIX="re-run the installer and allow it when asked"
    WARNING="SKULLPT needs one system package to install — re-run the installer and allow it when asked."
    return 1
  fi
  echo "PySide6: CANNOT INSTALL — the private folder could not be created."
  echo "         See install.log in this folder for details. (The CLI"
  echo "         still works — only the Bench window needs the venv.)"
  QT_FIX="see install.log in the SKULLPT folder"
  WARNING="The Bench window's private folder could not be created — see install.log in the SKULLPT folder for details."
  rm -f "$venv_err"
  return 1
}

find_blender() {
  if [ -n "${DESIGNSHELL_BLENDER:-}" ] && [ -x "${DESIGNSHELL_BLENDER}" ]; then
    echo "$DESIGNSHELL_BLENDER"; return
  fi
  if command -v blender >/dev/null 2>&1; then command -v blender; return; fi
  if [ -x /opt/blender/blender ]; then echo /opt/blender/blender; return; fi
  echo ""
}

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

# --- Entry -----------------------------------------------------------------
# The install body runs in a worker subprocess so its output can be
# logged to install.log and, in GUI mode, fed to the progress dialog.
# The worker keeps its own `set -e` semantics; the wrapper below only
# reports the outcome.
if [ "$WORKER" = "1" ]; then
  if do_install; then
    exit 0
  else
    exit $?
  fi
fi

LOG="install.log"
DIALOG_CMD="$(find_dialog)"
HAS_DISPLAY=0
[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && HAS_DISPLAY=1

GUI_MODE=0
if [ "$GUI_WANTED" = "gui" ]; then
  GUI_MODE=1
elif [ "$GUI_WANTED" = "terminal" ]; then
  GUI_MODE=0
elif [ ! -t 1 ] && [ "$HAS_DISPLAY" = "1" ] && [ -n "$DIALOG_CMD" ]; then
  # Launched without a terminal (double-click) with a display and a
  # dialog tool: GUI mode.
  GUI_MODE=1
fi

if [ "$GUI_MODE" = "1" ] && { [ -z "$DIALOG_CMD" ] || [ "$HAS_DISPLAY" = "0" ]; }; then
  # GUI was asked for but cannot run (no dialog tool, or no display):
  # fall back to the terminal installer. Degraded path — the install
  # still works and is still logged; there is just no progress window.
  GUI_MODE=0
  echo "No dialog tool found (looked for xenity, zenity, yad) or no display — installing in the terminal instead." | tee "$LOG"
fi

PASSTHROUGH=()
[ "$WITH_BLENDER" = "1" ] && PASSTHROUGH+=(--with-blender)
[ "$WITH_IMAGE" = "1" ] && PASSTHROUGH+=(--with-image-engine)

if [ "$GUI_MODE" = "1" ]; then
  echo "== SKULLPT install =="
  set +e
  SKULLPT_GUI_MODE=1 bash "$SCRIPT_PATH" --_run-worker "${PASSTHROUGH[@]}" 2>&1 | tee "$LOG" | "$DIALOG_CMD" --progress --title="Installing SKULLPT" --text="Starting…" --percentage=0 --auto-close --no-cancel --width=400
  _statuses=("${PIPESTATUS[@]}")
  set -e
  worker_status=${_statuses[0]}
  fail_line="$(grep -m1 '^INSTALL-FAILED: ' "$LOG" 2>/dev/null || true)"
  warn_line="$(grep -m1 '^INSTALL-WARNING: ' "$LOG" 2>/dev/null || true)"
  if [ "$worker_status" -ne 0 ]; then
    reason="${fail_line#INSTALL-FAILED: }"
    [ -z "$reason" ] && reason="Something went wrong during the install."
    "$DIALOG_CMD" --error --title="SKULLPT install failed" --text="$reason See install.log in the SKULLPT folder for details." --width=400 || true
    exit "$worker_status"
  fi
  if [ -n "$warn_line" ]; then
    reason="${warn_line#INSTALL-WARNING: }"
    "$DIALOG_CMD" --warning --title="SKULLPT installed — with a problem" --text="$reason" --width=400 || true
    exit 0
  fi
  "$DIALOG_CMD" --info --title="SKULLPT installed" --text="SKULLPT is installed. The launcher is on your Desktop and in the app menu." --width=400 || true
  if "$DIALOG_CMD" --question --title="SKULLPT installed" --text="SKULLPT is installed. Open it now?" --width=400; then
    # Detached: the installer exits while the Bench keeps running.
    nohup ./run.sh >/dev/null 2>&1 &
    disown || true
  fi
  exit 0
fi

echo "== SKULLPT install =="
set +e
SKULLPT_GUI_MODE=0 bash "$SCRIPT_PATH" --_run-worker "${PASSTHROUGH[@]}" 2>&1 | tee "$LOG"
_statuses=("${PIPESTATUS[@]}")
set -e
exit "${_statuses[0]}"
