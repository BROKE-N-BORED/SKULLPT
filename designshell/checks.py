"""The machine-check pipeline — facts before vibes.

Runs, in order: output files exist and are non-empty; HTML outputs parse
and actually contain every part id from the description; Description File
constraints evaluate true; budgets hold. The report is a plain dict that
travels with the asset into the Library.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from . import constraints as con_mod
from . import governor


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for key, val in attrs:
            if key == "id" and val:
                self.ids.add(val)


def run_machine_checks(desc: dict, out_dir: str | Path, outputs: list[str]) -> dict:
    out = Path(out_dir)
    checks: list[dict] = []

    missing = [name for name in outputs if not (out / name).exists() or (out / name).stat().st_size == 0]
    checks.append({"check": "outputs_exist", "ok": not missing,
                   "detail": f"missing/empty: {missing}" if missing else f"{len(outputs)} files"})

    html_files = [n for n in outputs if n.endswith(".html")]
    if html_files:
        parser = _IdCollector()
        parse_error = ""
        try:
            parser.feed((out / html_files[0]).read_text(encoding="utf-8"))
        except Exception as exc:  # malformed HTML should fail the check, not crash it
            parse_error = str(exc)
        absent = [p["id"] for p in desc["parts"] if p["id"] not in parser.ids]
        checks.append({"check": "html_parses_and_contains_all_parts",
                       "ok": not parse_error and not absent,
                       "detail": parse_error or (f"parts missing from output: {absent}" if absent else "all parts present")})

    binary = [n for n in outputs if n.endswith((".png", ".glb", ".blend"))]
    if binary:
        bad = []
        for name in binary:
            data = (out / name).read_bytes()[:8] if (out / name).exists() else b""
            if name.endswith(".png") and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                bad.append(f"{name}: not a PNG")
            elif name.endswith(".glb") and not data.startswith(b"glTF"):
                bad.append(f"{name}: not a GLB")
            elif name.endswith(".blend") and not data.startswith(b"BLENDER"):
                bad.append(f"{name}: not a .blend")
        checks.append({"check": "binary_outputs_valid", "ok": not bad,
                       "detail": "; ".join(bad) or f"{len(binary)} binary outputs have valid signatures"})

    con_results = con_mod.check_constraints(desc)
    failed = [r for r in con_results if not r["ok"]]
    checks.append({"check": "constraints", "ok": not failed,
                   "detail": "; ".join(f"{r['constraint'].get('type')}: {r['detail']}" for r in failed)
                             or f"{len(con_results)} constraints pass"})

    budget_problems = governor.check_budget(desc)
    checks.append({"check": "budget", "ok": not budget_problems,
                   "detail": "; ".join(budget_problems) or "within budget"})

    return {"ok": all(c["ok"] for c in checks), "checks": checks}
