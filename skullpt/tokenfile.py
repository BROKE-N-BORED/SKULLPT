"""The key files - the one place a key or token may rest on disk.

The cloud seats need keys: Gemini (planner and critic), Groq
(repairer), Hugging Face (photo to mesh). A key is read from the
environment first; this module is the second home, for the Settings
window: the user pastes the key once and it is written to
<workspace>/<kind>_key.key (the HF token keeps its older name,
hf_token.key), readable only by its owner, with the workspace's
.gitignore covering it so it can never be committed.

A key is never printed, never logged, and never returned by any
status call - callers only ever learn whether one is set.
"""
from __future__ import annotations

import os
from pathlib import Path

# kind -> filename in the workspace. The HF name is the v1.5 one,
# kept so an existing saved token keeps working.
KEY_FILES = {
    "hf": "hf_token.key",
    "gemini": "gemini_key.key",
    "groq": "groq_key.key",
}

TOKEN_FILENAME = "hf_token.key"

# Environment variable -> key kind, for the seats (env first, then
# the key file - the same precedence the HF token has always had).
ENV_KEY_KINDS = {
    "HF_TOKEN": "hf",
    "GEMINI_API_KEY": "gemini",
    "GROQ_API_KEY": "groq",
}


def key_path(kind: str, workspace: str | Path) -> Path:
    return Path(workspace) / KEY_FILES[kind]


def token_path(workspace: str | Path) -> Path:
    return key_path("hf", workspace)


def _ensure_gitignored(workspace: str | Path) -> None:
    """Make sure the workspace's .gitignore covers every key file, so
    a key can never be committed even if the workspace is ever put
    under version control."""
    ignore_path = Path(workspace) / ".gitignore"
    try:
        existing = ignore_path.read_text() if ignore_path.exists() else ""
    except OSError:
        return
    lines = [line.strip() for line in existing.splitlines()]
    missing = [name for name in KEY_FILES.values() if name not in lines]
    if not missing:
        return
    try:
        with ignore_path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            for name in missing:
                fh.write(name + "\n")
    except OSError:
        pass


def save_key(kind: str, workspace: str | Path, value: str) -> Path:
    """Write a key to the workspace's key file, owner-only
    permissions (0600). Returns the path. An empty value clears it."""
    text = (value or "").strip()
    path = key_path(kind, workspace)
    if not text:
        return clear_key(kind, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (text + "\n").encode())
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _ensure_gitignored(workspace)
    return path


def load_key(kind: str, workspace: str | Path) -> str | None:
    """The stored key, or None. The value goes only to the caller
    that is about to use it - never into a log or a status dict."""
    try:
        value = key_path(kind, workspace).read_text().strip()
    except OSError:
        return None
    return value or None


def has_key(kind: str, workspace: str | Path) -> bool:
    return load_key(kind, workspace) is not None


def clear_key(kind: str, workspace: str | Path) -> Path:
    path = key_path(kind, workspace)
    try:
        path.unlink()
    except OSError:
        pass
    return path


def load_key_for_env(workspace: str | Path, env_key: str) -> str | None:
    """The stored key for a seat's environment variable, or None
    when the variable has no key file (Ollama has no key at all)."""
    kind = ENV_KEY_KINDS.get(env_key)
    if kind is None:
        return None
    return load_key(kind, workspace)


# --- the HF token (v1.5 API, unchanged) -----------------------------------

def save_token(workspace: str | Path, token: str) -> Path:
    return save_key("hf", workspace, token)


def load_token(workspace: str | Path) -> str | None:
    return load_key("hf", workspace)


def has_token(workspace: str | Path) -> bool:
    return has_key("hf", workspace)


def clear_token(workspace: str | Path) -> Path:
    return clear_key("hf", workspace)
