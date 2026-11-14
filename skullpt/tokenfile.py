"""The HF token file — the one place a token may rest on disk.

The cloud mesh step needs a Hugging Face token. The token is read from
the environment first (HF_TOKEN); this module is the second home, for
the Settings window: the user pastes the token once and it is written
to <workspace>/hf_token.key, readable only by its owner, with the
workspace's .gitignore covering it so it can never be committed.

The token is never printed, never logged, and never returned by any
status call — callers only ever learn whether one is set.
"""
from __future__ import annotations

import os
from pathlib import Path

TOKEN_FILENAME = "hf_token.key"


def token_path(workspace: str | Path) -> Path:
    return Path(workspace) / TOKEN_FILENAME


def _ensure_gitignored(workspace: str | Path) -> None:
    """Make sure the workspace's .gitignore covers the token file, so
    the token can never be committed even if the workspace is ever
    put under version control."""
    ignore_path = Path(workspace) / ".gitignore"
    try:
        existing = ignore_path.read_text() if ignore_path.exists() else ""
    except OSError:
        return
    lines = [line.strip() for line in existing.splitlines()]
    if TOKEN_FILENAME in lines:
        return
    try:
        with ignore_path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(TOKEN_FILENAME + "\n")
    except OSError:
        pass


def save_token(workspace: str | Path, token: str) -> Path:
    """Write the token to the workspace's token file, owner-only
    permissions (0600). Returns the path. An empty token clears it."""
    value = (token or "").strip()
    path = token_path(workspace)
    if not value:
        return clear_token(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (value + "\n").encode())
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _ensure_gitignored(workspace)
    return path


def load_token(workspace: str | Path) -> str | None:
    """The stored token, or None. The value goes only to the caller
    that is about to use it — never into a log or a status dict."""
    try:
        value = token_path(workspace).read_text().strip()
    except OSError:
        return None
    return value or None


def has_token(workspace: str | Path) -> bool:
    return load_token(workspace) is not None


def clear_token(workspace: str | Path) -> Path:
    path = token_path(workspace)
    try:
        path.unlink()
    except OSError:
        pass
    return path
