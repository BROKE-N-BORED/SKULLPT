"""Seats — which brain does which job. Models are connectors, never core.

Defaults follow the checked research verdict (cloud brain, local hands):
Gemini plans and sees, Groq drafts and repairs fast, EXAONE lifts heavy
code, local Ollama is the offline fallback only. API keys are read from
environment variables at call time and are NEVER stored by the shell.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SEATS = {
    "planner": {"provider": "gemini", "model": "gemini-flash-latest", "env_key": "GEMINI_API_KEY"},
    "critic": {"provider": "gemini", "model": "gemini-flash-latest", "env_key": "GEMINI_API_KEY"},
    "drafter": {"provider": "groq", "model": "gpt-oss-20b", "env_key": "GROQ_API_KEY"},
    "repairer": {"provider": "groq", "model": "gpt-oss-20b", "env_key": "GROQ_API_KEY"},
    "heavy_coder": {"provider": "exaone", "model": "k-exaone_v2.5", "env_key": "EXAONE_API_KEY"},
    "fallback": {"provider": "ollama", "model": "qwen3:4b", "env_key": None},
}


def load_seats(path: str | Path | None = None) -> dict:
    """Default seats, optionally overridden by a seats.json file."""
    seats = {role: dict(cfg) for role, cfg in DEFAULT_SEATS.items()}
    if path and Path(path).exists():
        user = json.loads(Path(path).read_text(encoding="utf-8"))
        for role, cfg in user.items():
            if role in seats and isinstance(cfg, dict):
                seats[role].update(cfg)
            elif isinstance(cfg, dict):
                seats[role] = cfg
    return seats
