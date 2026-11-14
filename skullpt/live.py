"""Live seats — real provider calls behind the planner and critic seats.

Until v0.6 the seats were configuration only. This module is the wire:
stdlib HTTP calls to the provider the seat names, keys read from the
environment at call time and never stored. Endpoint overrides exist so
the wiring itself can be tested against a local mock server:

  DESIGNSHELL_GEMINI_URL   replaces the Gemini generateContent endpoint
  DESIGNSHELL_GROQ_URL     replaces the Groq chat-completions endpoint

No key -> SeatNotConfigured with a plain message. A provider error ->
SeatCallFailed with the provider's own words. Nothing is ever faked:
if the call did not happen, the caller hears about it.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from . import description as desc_mod


class SeatNotConfigured(RuntimeError):
    pass


class SeatCallFailed(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict | None = None, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400] if hasattr(exc, "read") else b""
        raise SeatCallFailed(f"provider returned HTTP {exc.code}: {body!r}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SeatCallFailed(f"provider unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SeatCallFailed(f"provider returned non-JSON: {exc}") from exc


def _key_for(seat: dict) -> str:
    env_key = seat.get("env_key")
    if not env_key:
        return ""
    key = os.environ.get(env_key, "")
    if not key:
        raise SeatNotConfigured(
            f"seat {seat.get('provider')}/{seat.get('model')} needs {env_key} "
            "set in the environment — refusing to fake a live call")
    return key


def gemini_text(seat: dict, prompt: str, image_png: bytes | None = None,
                extra_images: list[bytes] | None = None) -> str:
    key = _key_for(seat)
    override = os.environ.get("DESIGNSHELL_GEMINI_URL")
    if override:
        url = override
    else:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{seat.get('model', 'gemini-flash-latest')}:generateContent?key={key}")
    parts: list[dict] = [{"text": prompt}]
    for image in ([image_png] if image_png is not None else []) + list(extra_images or []):
        parts.append({"inline_data": {"mime_type": "image/png",
                                      "data": base64.b64encode(image).decode()}})
    data = _post(url, {"contents": [{"parts": parts}]})
    try:
        out = []
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    out.append(part["text"])
        text = "\n".join(out).strip()
    except Exception as exc:
        raise SeatCallFailed(f"unexpected Gemini response shape: {exc}") from exc
    if not text:
        raise SeatCallFailed("Gemini returned no text")
    return text


def groq_text(seat: dict, prompt: str) -> str:
    key = _key_for(seat)
    url = os.environ.get("DESIGNSHELL_GROQ_URL") or "https://api.groq.com/openai/v1/chat/completions"
    data = _post(url, {
        "model": seat.get("model", "gpt-oss-20b"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }, headers={"Authorization": f"Bearer {key}"})
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise SeatCallFailed(f"unexpected Groq response shape: {exc}") from exc
    if not text:
        raise SeatCallFailed("Groq returned no text")
    return text


def ollama_text(seat: dict, prompt: str) -> str:
    url = os.environ.get("DESIGNSHELL_OLLAMA_URL") or "http://127.0.0.1:11434/api/generate"
    data = _post(url, {"model": seat.get("model", "qwen3:4b"), "prompt": prompt, "stream": False})
    text = str(data.get("response", "")).strip()
    if not text:
        raise SeatCallFailed("Ollama returned no text")
    return text


def seat_text(seat: dict, prompt: str, image_png: bytes | None = None) -> str:
    provider = seat.get("provider")
    if provider == "gemini":
        return gemini_text(seat, prompt, image_png=image_png)
    if provider == "groq":
        if image_png is not None:
            raise SeatNotConfigured("the Groq seat is text-only here; vision belongs to the Gemini seat")
        return groq_text(seat, prompt)
    if provider == "ollama":
        if image_png is not None:
            raise SeatNotConfigured("the Ollama fallback seat is text-only in this wiring")
        return ollama_text(seat, prompt)
    raise SeatNotConfigured(f"provider {provider!r} has no live wiring in the shell yet")


# ------------------------------------------------------- fallback (v1.5, T20)

# Which seat the last plan/text call actually used, what the primary
# seat said when it failed, and whether the draft needed a repair —
# so the Bench and CLI can report honestly ("drafted by the local
# fallback — Gemini could not be reached", "the repairer fixed two
# problems in the draft") instead of silently switching brains.
_LAST_PLAN: dict = {"seat": None, "primary_error": None,
                    "repaired": False, "repair_seat": None}


def last_plan_seat() -> dict:
    """The record of the most recent plan_from_words call:
    {"seat": "planner"|"fallback"|None, "primary_error": str|None,
     "repaired": bool, "repair_seat": "repairer"|"planner"|"fallback"|None}."""
    return dict(_LAST_PLAN)


def text_with_fallback(seats: dict, prompt: str) -> tuple[str, str, str | None]:
    """Ask the planner seat; if it cannot answer (no key, provider
    error, unreachable), ask the local fallback seat instead.

    Returns (text, role-used, primary-error-or-None). When both fail,
    the PRIMARY seat's error is raised — the user hears the real
    reason, not the fallback's connection-refused noise."""
    primary_error: Exception | None = None
    try:
        return seat_text(seats.get("planner", {}), prompt), "planner", None
    except (SeatNotConfigured, SeatCallFailed) as exc:
        primary_error = exc
    fallback_seat = seats.get("fallback", {})
    try:
        return seat_text(fallback_seat, prompt), "fallback", str(primary_error)
    except (SeatNotConfigured, SeatCallFailed):
        assert primary_error is not None
        raise primary_error


# ------------------------------------------------------------- the planner

FORMAT_SPEC = """\
A Description File is JSON: {"name": str, "kind": "scene", "summary": str,
"parts": [...], "constraints": [...], "budget": {"max_tris": int, "preview_px": 640},
"rubric": [str, ...], "style": {"surface": "#241d16", "accent": "#e0a458", "text": "#f2ede4"}}.
Each part: {"id": lowercase_snake, "type": str, "shape": one of
box|cylinder|sphere|extrude|lathe|tube, "position_cm": [x, y, base_z],
"size_cm": [width, depth, height], "material": one of wood|dark_wood|maple|
mahogany|cream|plastic_black|metal|black_metal|chrome|gold|brass|glass|
leather|stone|paper}.
All geometry is centimetres, z-up, ground plane at z=0. A part sitting on
the floor has base_z equal to the floor's top. extrude parts also need
"profile_cm": [[x, y], ...] outline points (>=3) inside the part's size.
lathe parts need "profile_cm": [[radius, z], ...] (>=2). tube parts need
"path_cm": [[x, y, z], ...] (>=2) and "radius_cm".
Constraints: {"type": "on_ground", "part": id} or
{"type": "sits_on", "part": id, "on": support_id} — a part's base_z must
equal its support's base_z + height. Include a floor part
{"id": "floor", "type": "floor", "shape": "box"} under everything.
Optional per part: "repeat": {"count": n, "step_cm": [dx, dy, dz]},
"mirror": {"axis": "x", "at_cm": n}, "bevel_cm": n, "texture":
wood_grain|brushed_metal|worn_leather|none.
"""


def _json_from_text(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise SeatCallFailed(f"model returned no JSON object: {text[:200]!r}")
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SeatCallFailed(f"model returned invalid JSON: {exc}") from exc


def _repair_text(seats: dict, prompt: str) -> tuple[str, str, str | None]:
    """Ask the repairer seat to fix a draft; if it cannot answer (no
    key, provider error), the planner repairs its own draft instead
    (via text_with_fallback, which itself falls back to Ollama).
    Returns (text, role-used, repairer-error-or-None)."""
    repairer_error: Exception | None = None
    try:
        return seat_text(seats.get("repairer", {}), prompt), "repairer", None
    except (SeatNotConfigured, SeatCallFailed) as exc:
        repairer_error = exc
    text, role, _ = text_with_fallback(seats, prompt)
    return text, role, str(repairer_error)


def plan_from_words(words: str, seats: dict, kind: str = "scene") -> dict:
    """Draft a Description File from plain words via the planner seat.

    The draft still goes through the plan gate — this fills in the form,
    it does not approve anything. If the planner seat cannot answer
    (no key, provider error, unreachable), the local fallback seat
    (Ollama) is tried with the same prompt; when both fail, the
    planner's error is raised. Which seat was used is recorded in
    last_plan_seat() so callers can say so honestly.

    A draft that parses to JSON but fails validation gets ONE repair
    attempt (v1.6, T22): the repairer seat is sent the draft plus the
    validation problems and asked to fix exactly those. If the repair
    validates, it is used and recorded; if it still fails, the error
    names both attempts. A repair is bounded to one attempt — a draft
    that needs two repairs is a draft to rewrite, not to patch.
    """
    _LAST_PLAN.update(repaired=False, repair_seat=None)
    prompt = (
        "You are the planner seat of a design workshop. Turn the request "
        "into ONE Description File. Reply with ONLY the JSON object, no "
        "commentary.\n\n" + FORMAT_SPEC +
        f"\nKind: {kind}\nRequest: {words}\n")
    raw, role = _plan_text(seats, prompt)
    desc = _json_from_text(raw)
    desc.setdefault("kind", kind)
    problems = desc_mod.validate(desc)
    if problems:
        repair_prompt = (
            "You are the repairer seat of a design workshop. A draft "
            "Description File failed validation. Fix ONLY the listed "
            "problems — do not redesign, rename, or add parts. Reply "
            "with ONLY the corrected JSON object, no commentary.\n\n"
            + FORMAT_SPEC +
            "\nProblems:\n- " + "\n- ".join(problems) +
            "\n\nDraft:\n" + json.dumps(desc) + "\n")
        try:
            repaired_raw, repair_role, _ = _repair_text(seats, repair_prompt)
            repaired = _json_from_text(repaired_raw)
            repaired.setdefault("kind", kind)
            still = desc_mod.validate(repaired)
        except (SeatNotConfigured, SeatCallFailed) as exc:
            raise SeatCallFailed(
                "the planner's draft failed validation, and the repair "
                f"attempt could not run ({exc}):\n- " + "\n- ".join(problems)
                + f"\nDraft began: {raw[:200]!r}") from exc
        if still:
            raise SeatCallFailed(
                "the planner's draft failed validation, and the repair "
                "attempt still failed validation:\n- " + "\n- ".join(still)
                + f"\nRepaired draft began: {repaired_raw[:200]!r}")
        desc = repaired
        _LAST_PLAN.update(repaired=True, repair_seat=repair_role)
    for key, default in (("style", {}), ("cameras", []), ("constraints", []),
                         ("budget", {}), ("rubric", []), ("tags", [])):
        desc.setdefault(key, default)
    return desc


def _plan_text(seats: dict, prompt: str) -> tuple[str, str]:
    """The planner call with fallback, recording which seat answered.
    The fallback only covers a seat that could not answer at all; a
    draft that parses to JSON but fails validation is repaired by
    plan_from_words (T22), not here."""
    try:
        text, role, primary_error = text_with_fallback(seats, prompt)
    except (SeatNotConfigured, SeatCallFailed) as exc:
        _LAST_PLAN.update(seat=None, primary_error=str(exc))
        raise
    _LAST_PLAN.update(seat=role, primary_error=primary_error)
    return text, role


# ------------------------------------------------------- vision patch rules

ALLOWED_PATCH_FIELDS = ("position_cm", "size_cm", "material", "bevel_cm", "radius_cm")


def extract_patches(fixes: list[dict]) -> list[dict]:
    """Pull machine-appliable patches out of critic fixes.

    A fix may carry "patch": {"field": value} with one allowed field —
    patches are parameter changes, never rewrites, per the blueprint.
    """
    out = []
    for fix in fixes or []:
        patch = fix.get("patch")
        part = fix.get("part")
        if not isinstance(patch, dict) or not part:
            continue
        for field, value in patch.items():
            if field in ALLOWED_PATCH_FIELDS:
                out.append({"part": part, "field": field, "value": value,
                            "issue": fix.get("issue", "")})
    return out


def apply_patches(desc: dict, patches: list[dict]) -> list[str]:
    """Apply patches to a description in place; return what was applied."""
    by_id = {p["id"]: p for p in desc.get("parts", [])}
    applied = []
    for patch in patches:
        part = by_id.get(patch["part"])
        if part is None:
            continue
        field, value = patch["field"], patch["value"]
        if field in ("position_cm", "size_cm"):
            if not (isinstance(value, list) and len(value) == 3
                    and all(isinstance(v, (int, float)) for v in value)):
                continue
        elif field == "material":
            if not isinstance(value, str):
                continue
        else:  # bevel_cm / radius_cm
            if not isinstance(value, (int, float)) or value < 0:
                continue
        part[field] = value
        applied.append(f"{patch['part']}.{field} = {value!r} ({patch.get('issue', 'critic fix')})")
    return applied
