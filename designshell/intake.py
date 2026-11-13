"""Photo intake — a photograph becomes a tracked reference, honestly.

The GitHub sweep's pipeline starts with a photo. This module does the
part the shell can stand behind today: the photo is copied into the
workspace, measured (real pixel dimensions, parsed from the file), and
filed as a draft description whose `reference` block records it — so a
planner (or Joshua) builds FROM the photo with the reference on record.

Since v1.1 the first route is built in: `cloudmesh` sends the photo to
the free TRELLIS.2 ZeroGPU space (a few production meshes a day on
free quota) and files the textured GLB + its ingested description in
a job. The local draft route (SF3D / TripoSR) still ends at a GLB that
comes back in through `ingest`.
"""
from __future__ import annotations

import shutil
import struct
from pathlib import Path


def image_size(path: str | Path) -> tuple[int | None, int | None, str]:
    """(width_px, height_px, format) parsed from the file header."""
    data = Path(path).read_bytes()[:64 * 1024]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height, "png"
    if data.startswith(b"GIF8") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return width, height, "gif"
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height, "jpeg"
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + length
        return None, None, "jpeg"
    return None, None, Path(path).suffix.lstrip(".").lower() or "unknown"


ROUTES = (
    "Routes from this photo to a 3D asset:\n"
    "  1. Free cloud mesh (built in, v1.1): cloud-mesh <workspace> <photo> "
    "turns the photo into a textured GLB on a Hugging Face ZeroGPU space "
    "(free quota, a few textured GLBs a day; needs HF_TOKEN) and files "
    "the mesh + its description in a job.\n"
    "  2. Local draft: SF3D or TripoSR on the PC (draft grade, offline) "
    "-> GLB -> ingest <file.glb> <out.description.json>.\n"
    "  3. Build from the photo as a reference: plan a description with "
    "the photo on record and let the Blender exporter build it."
)


def intake_photo(workspace: str | Path, photo: str | Path) -> tuple[dict, Path]:
    """Copy the photo into the workspace and draft its description."""
    src = Path(photo)
    if not src.exists():
        raise FileNotFoundError(f"photo not found: {photo}")
    width, height, fmt = image_size(src)
    slug = "".join(c.lower() if c.isalnum() else "-" for c in src.stem).strip("-") or "photo"
    dest_dir = Path(workspace) / "intake" / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    desc = {
        "name": f"{src.stem.replace('_', ' ').replace('-', ' ').title()} (photo reference)",
        "kind": "graphic",
        "summary": (
            f"Photo reference intaken from {src.name} "
            f"({width}x{height} {fmt}). " + ROUTES.replace("\n", " ")),
        "parts": [{"id": "reference_photo", "type": "image_slot",
                   "alt": src.stem, "text": src.name}],
        "constraints": [],
        "cameras": [],
        "budget": {},
        "rubric": ["The built asset matches the object in the reference photo"],
        "style": {},
        "tags": ["photo-reference"],
        "reference": {"file": src.name, "stored_at": str(dest),
                      "width_px": width, "height_px": height, "format": fmt},
    }
    return desc, dest_dir
