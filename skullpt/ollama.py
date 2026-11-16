"""The local model - Ollama, the no-key seat.

The fallback seat needs the Ollama server on this machine answering
on 127.0.0.1:11434 with the model pulled. This module is the status
check the Bench, the setup panel, and the dependency report all read:
is the server up, is the model present. The check is cached briefly
(30 s) so a down server never stalls a refresh - a refused connection
is fast, but a black-holed socket is not.

The model name comes from the seats table (the fallback seat), never
a second copy of the string.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .seats import DEFAULT_SEATS

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
MODEL_NAME = DEFAULT_SEATS["fallback"]["model"]
_CACHE_TTL_S = 30.0
_cache: dict = {"at": 0.0, "status": None}


def base_url() -> str:
    return os.environ.get("DESIGNSHELL_OLLAMA_BASE", DEFAULT_BASE_URL).rstrip("/")


def _get(path: str, timeout: float) -> dict:
    with urllib.request.urlopen(base_url() + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def server_up(timeout: float = 1.0) -> bool:
    try:
        _get("/api/version", timeout)
        return True
    except Exception:
        return False


def _model_names(tags: dict) -> list[str]:
    return [str(m.get("name", "")) for m in tags.get("models", [])]


def model_present(model: str | None = None, timeout: float = 2.0) -> bool:
    """True when the server lists the model. The tag matters:
    "qwen3:latest" is a different model from "qwen3:4b", so a bare
    name only matches its own ":latest" form."""
    name = model or MODEL_NAME
    try:
        names = _model_names(_get("/api/tags", timeout))
    except Exception:
        return False
    return any(n == name or n == name + ":latest"
               or name == n + ":latest" for n in names)


def status(force: bool = False) -> dict:
    """{"server": bool, "model": bool, "model_name": str} - cached
    for 30 s unless force=True (the setup panel forces a re-check
    after a fix runs)."""
    now = time.monotonic()
    if (not force and _cache["status"] is not None
            and now - _cache["at"] < _CACHE_TTL_S):
        return dict(_cache["status"])
    up = server_up()
    present = model_present() if up else False
    result = {"server": up, "model": present, "model_name": MODEL_NAME}
    _cache.update(at=now, status=dict(result))
    return result


def ollama_binary() -> str | None:
    return shutil.which("ollama")


def pull_model(model: str | None = None,
               timeout: int = 3600, on_progress=None) -> tuple[bool, str]:
    """Pull the model with the Ollama CLI (the server must be up).
    Long - run it in a background thread, never on the UI thread.
    Returns (ok, plain-language message). A failed pull is safe to
    retry: the Ollama CLI resumes a partial download. `on_progress`,
    when given, is called with each output line as it arrives, so
    the Setup window can show the pull moving (v1.10, N35)."""
    name = model or MODEL_NAME
    binary = ollama_binary()
    if binary is None:
        return False, "Ollama is not installed on this machine."
    try:
        proc = subprocess.Popen(
            [binary, "pull", name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        return False, f"Could not run Ollama: {exc}"
    # Stream the output instead of waiting for the process to end:
    # Ollama prints progress lines as the download runs. The read
    # loop is bounded by the deadline (select with a 1 s slice), so
    # a silent stall still times out like the old subprocess.run.
    deadline = time.monotonic() + timeout
    buf = b""
    tail = ""
    assert proc.stdout is not None
    while True:
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            return False, f"Pulling {name} timed out - try again, it resumes."
        ready, _, _ = select.select([proc.stdout], [], [], 1.0)
        if not ready:
            continue
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            break  # EOF: the process closed its stdout
        # Ollama updates progress in place with carriage returns;
        # treat them as line breaks so each update is a line.
        buf += chunk.replace(b"\r", b"\n")
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail = text
                if on_progress is not None:
                    on_progress(text)
    if buf.strip():
        text = buf.decode("utf-8", "replace").strip()
        tail = text
        if on_progress is not None:
            on_progress(text)
    proc.wait()
    if proc.returncode != 0:
        detail = tail or "no reason given"
        return False, f"Pulling {name} failed: {detail}"
    return True, f"{name} is ready."
