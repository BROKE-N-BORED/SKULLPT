"""Recipe graduation — the library learns from approved work.

The blueprint's rule: recipes are not only written by hand; when a job
is approved, built, and filed, its description graduates into a reusable
recipe — a named template in <workspace>/recipes/ that a later job can
start from instead of a blank page. Applying a recipe copies the
template under a new name; the plan gate still applies to the new job.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import description as desc_mod
from .governor import stamp


def recipes_dir(workspace: str | Path) -> Path:
    path = Path(workspace) / "recipes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "recipe"


def graduate_recipe(workspace: str | Path, job, report: dict) -> Path:
    """Save a filed job's description as a reusable recipe template."""
    desc = job.load_description()
    root = recipes_dir(workspace)
    dest = root / f"{job.dir.name}.recipe.json"
    n = 2
    while dest.exists():
        dest = root / f"{job.dir.name}-{n}.recipe.json"
        n += 1
    recipe = {
        "name": f"{desc['name']} recipe",
        "kind": desc["kind"],
        "graduated_from": job.dir.name,
        "graduated": stamp(),
        "outputs_when_graduated": report.get("outputs", []),
        "template": desc,
    }
    dest.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    return dest


def list_recipes(workspace: str | Path) -> list[dict]:
    root = Path(workspace) / "recipes"
    if not root.exists():
        return []
    out = []
    for path in sorted(root.glob("*.recipe.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"file": path.name, "name": data.get("name"),
                    "kind": data.get("kind"), "graduated_from": data.get("graduated_from")})
    return out


def apply_recipe(workspace: str | Path, recipe_file: str, new_name: str | None = None) -> dict:
    """Load a recipe's template as a fresh description (validated)."""
    path = Path(workspace) / "recipes" / recipe_file
    if not path.exists():
        path = Path(recipe_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    template = data.get("template")
    if not isinstance(template, dict):
        raise ValueError(f"{recipe_file} has no template description")
    desc = json.loads(json.dumps(template))
    if new_name:
        desc["name"] = new_name
    problems = desc_mod.validate(desc)
    if problems:
        raise desc_mod.DescriptionError(
            "recipe template failed validation:\n- " + "\n- ".join(problems))
    desc.setdefault("style", {})
    desc.setdefault("cameras", [])
    desc.setdefault("constraints", [])
    desc.setdefault("budget", {})
    desc.setdefault("rubric", [])
    desc.setdefault("tags", [])
    return desc
