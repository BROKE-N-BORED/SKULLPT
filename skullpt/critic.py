"""The Critic seat — vision checks looks, after machines check facts.

A critic receives the description's rubric and the preview file list, and
returns structured fixes: [{"part": id-or-null, "issue": str, "fix": str}].

The OfflineCritic is a deterministic rubric checker that runs anywhere
(text length vs panel width, contrast of style tokens, rubric items it
can verify from the description itself) and honestly marks the rest as
"needs vision". Since v0.6 the GeminiCritic makes a real vision call via
skullpt.live: it sends the first render PNG plus the rubric and asks
for structured fixes, each optionally carrying ONE machine-appliable
parameter patch. It still refuses clearly when no key is configured or
no render exists — a sandbox pass must never be sold as a vision verdict.
"""
from __future__ import annotations


class CriticNotConfigured(RuntimeError):
    pass


def _lum(hex_color: str) -> float | None:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class OfflineCritic:
    name = "offline"

    def critique(self, desc: dict, previews: list[str],
                 reference_png: bytes | None = None) -> dict:
        fixes: list[dict] = []
        style = desc.get("style") or {}
        text_lum, surf_lum = _lum(str(style.get("text", ""))), _lum(str(style.get("surface", "")))
        if text_lum is not None and surf_lum is not None:
            if abs(text_lum - surf_lum) < 0.35:
                fixes.append({"part": None, "issue": "text/surface contrast looks low",
                              "fix": "raise contrast between style.text and style.surface"})
        for item in desc.get("rubric", []):
            low = item.lower()
            if "overflow" in low or "fits" in low:
                continue  # covered by machine text_fits constraints
            fixes.append({"part": None, "issue": f"rubric item needs vision review: {item}",
                          "fix": "configure the Gemini critic seat for a visual verdict"})
        return {"critic": self.name, "vision": False, "fixes": fixes,
                "note": "offline critic: deterministic checks only — no visual verdict claimed"}


class GeminiCritic:
    name = "gemini"

    def __init__(self, seat: dict):
        self.seat = seat

    def critique(self, desc: dict, previews: list[str],
                 reference_png: bytes | None = None) -> dict:
        import os
        from pathlib import Path

        from . import live

        env_key = self.seat.get("env_key")
        if not env_key or not os.environ.get(env_key):
            raise CriticNotConfigured(
                f"Gemini critic needs {env_key} set in the environment — refusing to fake a visual verdict")
        image_path = None
        for name in previews:
            candidate = Path(name)
            if candidate.suffix == ".png" and candidate.exists():
                image_path = candidate
                break
        if image_path is None:
            raise CriticNotConfigured(
                "Gemini critic needs a render PNG to look at — none was produced")
        rubric = "\n".join(f"- {item}" for item in desc.get("rubric", [])) or "- (no rubric items)"
        parts = "\n".join(
            f"- {p['id']} ({p.get('type')}, {p.get('shape', 'box')}) "
            f"at {p.get('position_cm')} size {p.get('size_cm')}"
            for p in desc.get("parts", []))
        reply_spec = (
            "Reply with ONLY a JSON object: {\"fixes\": [{\"part\": part-id-or-null, "
            "\"issue\": \"what is wrong, briefly\", \"fix\": \"what to change\", "
            "\"patch\": {\"field\": value} or null}]}. A patch may set ONE of "
            "position_cm, size_cm, material, bevel_cm, radius_cm on that part — "
            "parameter changes only. If the render satisfies the rubric, "
            "return {\"fixes\": []}.")
        if reference_png is not None:
            prompt = (
                "You are the vision critic of a design workshop. TWO images "
                "are attached: the FIRST is the reference picture the user "
                "chose for this job; the SECOND is a render of what was "
                "built. Judge the render against the reference first — does "
                "it show the same object, proportions, materials and "
                "colours? — and also against this rubric:\n"
                f"{rubric}\n\nThe description's parts (centimetres, z-up):\n{parts}\n\n"
                + reply_spec)
            text = live.gemini_text(self.seat, prompt, image_png=reference_png,
                                    extra_images=[image_path.read_bytes()])
        else:
            prompt = (
                "You are the vision critic of a design workshop. Look at the "
                "attached render and judge it ONLY against this rubric:\n"
                f"{rubric}\n\nThe description's parts (centimetres, z-up):\n{parts}\n\n"
                + reply_spec)
            text = live.gemini_text(self.seat, prompt, image_png=image_path.read_bytes())
        try:
            data = live._json_from_text(text)
        except live.SeatCallFailed as exc:
            raise CriticNotConfigured(f"Gemini critique was unusable: {exc}") from exc
        fixes = data.get("fixes")
        if not isinstance(fixes, list):
            raise CriticNotConfigured("Gemini critique carried no fixes list")
        return {"critic": self.name, "vision": True, "fixes": fixes,
                "image": image_path.name,
                "reference_compared": reference_png is not None,
                "note": ("live Gemini vision critique of the render against "
                         "the chosen reference" if reference_png is not None else
                         "live Gemini vision critique of the render")}


def get_critic(seats: dict, prefer: str = "offline"):
    if prefer == "gemini":
        return GeminiCritic(seats.get("critic", {}))
    return OfflineCritic()
