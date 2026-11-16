"""Seats - which brain does which job. Models are connectors, never core.

Four seats, all wired (v1.6): Gemini plans and sees, Groq repairs
drafts that fail validation, local Ollama is the offline fallback when
Gemini cannot be reached. A key comes from the environment first, then
from the workspace's key file (v1.8: Settings saves Gemini and Groq
keys there, owner-only, gitignored - the same precedence the HF token
has always had). A key is never stored anywhere else, and never logged.

Seats removed in v1.5 (they were advertised but had no caller and no
live wiring - the config promised a six-brain workshop and two seats
did all the work): drafter (Groq - no code path ever called it) and
heavy_coder (EXAONE - no live wiring exists at all). The repairer seat
came back in v1.6 with the planner repair loop (T22). `load_seats`
still accepts user-defined roles from seats.json, so nothing breaks
for anyone who configured them; they just stop being promised by
default.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SEATS = {
    "planner": {"provider": "gemini", "model": "gemini-flash-latest", "env_key": "GEMINI_API_KEY"},
    "critic": {"provider": "gemini", "model": "gemini-flash-latest", "env_key": "GEMINI_API_KEY"},
    "repairer": {"provider": "groq", "model": "openai/gpt-oss-20b", "env_key": "GROQ_API_KEY"},
    "fallback": {"provider": "ollama", "model": "qwen3:4b", "env_key": None},
}


def load_seats(path: str | Path | None = None) -> dict:
    """Default seats, optionally overridden by a seats.json file."""
    seats = {role: dict(cfg) for role, cfg in DEFAULT_SEATS.items()}
    if path and Path(path).exists():
        try:
            user = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # A damaged seats file is a plain error, not a traceback
            # (v1.10, N22): the user hears which file and what is
            # wrong with it.
            raise ValueError(
                f"seats file {path} is damaged - it is not valid JSON "
                f"({exc.msg}, line {exc.lineno})") from exc
        for role, cfg in user.items():
            if role in seats and isinstance(cfg, dict):
                seats[role].update(cfg)
            elif isinstance(cfg, dict):
                seats[role] = cfg
    return seats
