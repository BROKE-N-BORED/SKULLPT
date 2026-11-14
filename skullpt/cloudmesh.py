"""Cloud mesh step — a photo becomes a textured 3D mesh, on someone
else's GPU (new in v1.1).

The shell's PC has no GPU worth the name, so the mesh step runs on a
free Hugging Face Space instead of locally:

  Space: microsoft/TRELLIS.2 — Microsoft's official TRELLIS.2 demo,
  MIT licensed, running on Hugging Face ZeroGPU. Free quota is about
  5 minutes of GPU a day on a free account (2 minutes with no
  account); a mesh takes 3-8 minutes of wall time, most of it queue.
  The Space was verified live and its API shape read from its own
  /gradio_api/info on 2026-11-12 (see the endpoint table below).

  Flow: upload the photo -> /preprocess_image (server-side
  background removal — unmasked backdrops become geometry) ->
  /image_to_3d (the generation; returns only an HTML preview) ->
  /extract_glb (the textured GLB file). The GLB is saved into the
  job's folder and the EXISTING ingest step derives the job's
  description from it, so the mesh then rides the normal
  approve -> build -> render path like any other job.

API shape (from https://microsoft-trellis-2.hf.space/gradio_api/info):
  POST /gradio_api/upload                       multipart, field "files"
  POST /gradio_api/call/v2/<fn>                 {"data": [...], "session_hash": ...}
       (falls back to POST /gradio_api/call/<fn> if the v2 path 404s)
  GET  /gradio_api/call/<event_id>              SSE stream until
       "process_completed" (or "complete"); output data is in the
       event's "output" -> "data" list.
  /preprocess_image  image -> masked image
  /image_to_3d       image, seed, resolution ("512"|"1024"|"1536"),
                     3 stages x (guidance, guidance_rescale, steps,
                     rescale_t) -> HTML preview string
  /extract_glb       decimation_target (100000-500000),
                     texture_size (1024-4096) -> [GLB FileData, GLB FileData]
  The generation is cached per session, so a failed /extract_glb is
  retried once in the same session without re-generating.

Auth: HF_TOKEN from the environment ONLY, never written to any file
(same rule as GEMINI_API_KEY). No token -> a plain refusal naming the
fix (free account at huggingface.co, Settings -> Access Tokens ->
New token, Read role). The token rides as a Bearer header on every
request, including the GLB download.

Disclosure: every run says plainly, before anything is sent, that the
photo is uploaded to Hugging Face's free GPU and leaves the machine.

Governor: this step deliberately does NOT take the heavy-job lock and
does NOT check the free-RAM floor — it loads no local model and runs
no local compute; it is network-bound. (The governor floor itself is
untouched; builds and image generation still go through it.)

Stdlib only (urllib, json) — no new dependencies. The Space base URL
can be overridden with DESIGNSHELL_CLOUDMESH_URL (the test suite's
mock Gradio server uses this, same pattern as DESIGNSHELL_GEMINI_URL).
"""
from __future__ import annotations

import json
import mimetypes
import os
import random
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import description as desc_mod
from .governor import stamp
from .ingest import ingest_glb
from .intake import image_size
from .jobs import Job

# The Space. One constant to change if Microsoft's demo ever moves.
SPACE_ID = "microsoft/TRELLIS.2"
SPACE_URL = "https://microsoft-trellis-2.hf.space"

TOKEN_ENV = "HF_TOKEN"
URL_ENV_OVERRIDE = "DESIGNSHELL_CLOUDMESH_URL"

# Generation defaults — the Space's own UI defaults, at the cheapest
# resolution (512 saves ZeroGPU quota; 1024/1536 cost more per mesh).
RESOLUTION = "512"
STAGE_1 = (7.5, 0.7, 12, 5.0)    # ss guidance, rescale, steps, rescale_t
STAGE_2 = (7.5, 0.5, 12, 3.0)    # shape_slat guidance, rescale, steps, rescale_t
STAGE_3 = (1.0, 0.0, 12, 3.0)    # tex_slat guidance, rescale, steps, rescale_t
DECIMATION_TARGET = 300000       # the Blender target; range 100000-500000
TEXTURE_SIZE = 2048              # range 1024-4096

# A mesh takes 3-8 minutes on ZeroGPU (mostly queue). The whole flow
# gets 15 minutes before the shell gives up and says so.
DEFAULT_TIMEOUT = 900
# A single read that stalls this long with no SSE event is a dead stream.
READ_TIMEOUT = 120
# /extract_glb is the fragile step; one retry in the same session.
EXTRACT_RETRIES = 1

TOKEN_FIX = (
    "the cloud mesh step needs a Hugging Face token: make a free account at "
    "huggingface.co, then Settings -> Access Tokens -> New token (Read role), "
    "and put it in the environment as HF_TOKEN. The token is never written "
    "to any file."
)

DISCLOSURE = (
    "This uploads the photo to Hugging Face's free GPU "
    f"({SPACE_ID}) — the photo leaves this machine. The mesh comes back "
    "as a textured GLB and is filed in the job."
)

QUOTA_MESSAGE = (
    "the daily free GPU time on Hugging Face is used up — try again "
    "tomorrow. (Free accounts get about 5 minutes of GPU a day; the "
    "quota resets 24 hours after first use.)"
)

# Error text from the Space that means the ZeroGPU quota is spent.
_QUOTA_MARKERS = ("quota", "gpu duration", "out of gpu", "exceeded your")


class CloudMeshError(RuntimeError):
    """Plain-language failure of the cloud mesh step."""


class QuotaExhaustedError(CloudMeshError):
    """The ZeroGPU daily free quota is spent."""


def base_url() -> str:
    return os.environ.get(URL_ENV_OVERRIDE) or SPACE_URL


def token_value(token: str | None = None) -> str:
    """The HF token, or a plain refusal naming the fix."""
    value = token or os.environ.get(TOKEN_ENV, "")
    if not value.strip():
        raise CloudMeshError(TOKEN_FIX)
    return value.strip()


def _check_quota(text: str) -> None:
    low = text.lower()
    if any(marker in low for marker in _QUOTA_MARKERS):
        raise QuotaExhaustedError(QUOTA_MESSAGE)


def _headers(token: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _request(url: str, token: str, data: bytes | None = None,
             content_type: str | None = None, timeout: int = READ_TIMEOUT,
             method: str | None = None) -> bytes:
    """One HTTP round trip; HTTP/URL failures become CloudMeshError."""
    req = urllib.request.Request(url, data=data,
                                 headers=_headers(token, content_type),
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        detail = body.strip()[:400] or f"HTTP {exc.code}"
        _check_quota(detail)
        raise CloudMeshError(
            f"the Hugging Face Space answered with an error "
            f"(HTTP {exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise CloudMeshError(
            "could not reach the Hugging Face Space — check the network "
            f"and try again ({exc.reason})") from exc
    except TimeoutError as exc:
        raise CloudMeshError(
            "the Hugging Face Space stopped answering — try again") from exc


def _multipart(field: str, filename: str, payload: bytes, mime: str,
               extra: dict[str, str]) -> tuple[bytes, str]:
    """A multipart/form-data body, built by hand (no requests here)."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in extra.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        + payload + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_image(base: str, token: str, photo: Path, session_hash: str,
                 timeout: int = READ_TIMEOUT) -> str:
    """Upload the photo; returns the server-side path Gradio gives back."""
    mime, _ = mimetypes.guess_type(photo.name)
    body, ctype = _multipart("files", photo.name, photo.read_bytes(),
                             mime or "application/octet-stream",
                             {"session_hash": session_hash})
    raw = _request(f"{base}/gradio_api/upload", token, data=body,
                   content_type=ctype, timeout=timeout, method="POST")
    try:
        parsed = json.loads(raw.decode())
    except ValueError as exc:
        raise CloudMeshError(
            "the Hugging Face Space gave back an upload answer the shell "
            "could not read") from exc
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and first.get("path"):
            return str(first["path"])
    raise CloudMeshError(
        "the Hugging Face Space did not confirm the photo upload")


def _call(base: str, token: str, session_hash: str, fn: str,
          data: list, deadline: float) -> list:
    """Call one Gradio endpoint and wait for its output data list."""
    payload = json.dumps({"data": data, "session_hash": session_hash}).encode()
    event_id: str | None = None
    last_error: CloudMeshError | None = None
    # Current Gradio (v2 path) first, classic path if the Space is older.
    for path in (f"/gradio_api/call/v2{fn}", f"/gradio_api/call{fn}"):
        try:
            raw = _request(f"{base}{path}", token, data=payload,
                           content_type="application/json",
                           timeout=max(1, int(deadline - time.monotonic())),
                           method="POST")
        except CloudMeshError as exc:
            if "HTTP 404" in str(exc) and path.endswith(f"v2{fn}"):
                last_error = exc
                continue
            raise
        try:
            parsed = json.loads(raw.decode())
        except ValueError as exc:
            raise CloudMeshError(
                f"the Space's {fn} answer could not be read") from exc
        if isinstance(parsed, str):
            event_id = parsed
        elif isinstance(parsed, dict):
            event_id = parsed.get("event_id")
        break
    if last_error is not None and event_id is None:
        raise last_error
    if not event_id:
        raise CloudMeshError(f"the Space's {fn} call did not start")
    return _poll_events(base, token, fn, event_id, deadline)


def _poll_events(base: str, token: str, fn: str, event_id: str,
                 deadline: float) -> list:
    """Read the SSE stream until the call completes, errors, or times out."""
    url = f"{base}/gradio_api/call/{event_id}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        stream = urllib.request.urlopen(
            req, timeout=max(1, int(deadline - time.monotonic())))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        _check_quota(body)
        raise CloudMeshError(
            f"the Space's {fn} result stream failed (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise CloudMeshError(
            "could not reach the Hugging Face Space while waiting for "
            f"{fn} ({exc.reason})") from exc
    except TimeoutError as exc:
        # The Space took too long even to answer the stream request.
        raise CloudMeshError(
            f"the mesh step timed out waiting for {fn} — the Space is slow "
            "or the queue is long; try again later") from exc
    event_name = ""
    try:
        for raw_line in stream:
            if time.monotonic() > deadline:
                raise CloudMeshError(
                    f"the mesh step timed out waiting for {fn} — the Space "
                    "is slow or the queue is long; try again later")
            line = raw_line.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if event_name in ("process_completed", "complete"):
                try:
                    data = json.loads(payload)
                except ValueError as exc:
                    raise CloudMeshError(
                        f"the Space's {fn} result could not be read") from exc
                output = data.get("output", {}) or {}
                if data.get("success") is False:
                    _check_quota(str(output))
                    raise CloudMeshError(
                        f"the Space's {fn} step failed: {output}")
                return list(output.get("data", []))
            if event_name == "error":
                try:
                    detail = str(json.loads(payload))
                except ValueError:
                    detail = payload
                _check_quota(detail)
                raise CloudMeshError(f"the Space's {fn} step failed: {detail}")
            # "generating", "estimation", heartbeats: wait for the end.
    except TimeoutError as exc:
        raise CloudMeshError(
            f"the mesh step timed out waiting for {fn} — the Space is slow "
            "or the queue is long; try again later") from exc
    finally:
        stream.close()
    raise CloudMeshError(
        f"the Space's {fn} stream ended without a result — try again")


def download_file(base: str, token: str, file_data: dict,
                  deadline: float) -> bytes:
    """Download a Gradio FileData's URL (relative or absolute)."""
    url = file_data.get("url") or file_data.get("path") or ""
    if not url:
        raise CloudMeshError("the Space's GLB answer had no download link")
    if not url.startswith("http"):
        url = urllib.parse.urljoin(base + "/", url.lstrip("/"))
    return _request(url, token, timeout=max(1, int(deadline - time.monotonic())))


def _image_data(server_path: str, photo: Path) -> dict:
    return {"path": server_path, "orig_name": photo.name,
            "size": photo.stat().st_size,
            "mime_type": mimetypes.guess_type(photo.name)[0] or "image/png",
            "meta": {"_type": "gradio.FileData"}}


def validate_photo(photo: str | Path) -> Path:
    """The photo must exist and be a readable PNG/JPEG/GIF — before any
    network call, so a bad image costs no GPU time."""
    path = Path(photo).expanduser()
    if not path.is_file():
        raise CloudMeshError(f"photo not found: {photo}")
    try:
        width, height, fmt = image_size(path)
    except OSError as exc:
        raise CloudMeshError(f"could not read {photo}: {exc}") from exc
    if fmt not in ("png", "jpeg", "gif") or width is None:
        raise CloudMeshError(
            f"{path.name} does not look like a photo the mesh step can use — "
            "send a PNG or JPEG photo")
    return path


def generate_mesh(photo: str | Path, token: str | None = None,
                  base: str | None = None, seed: int | None = None,
                  timeout: int = DEFAULT_TIMEOUT,
                  progress=None) -> tuple[bytes, int]:
    """Photo -> textured GLB bytes, via the TRELLIS.2 Space.

    Returns (glb_bytes, seed). `progress`, when given, is called with
    (stage, detail) as each step lands — the CLI and the Bench log it.
    """
    photo_path = validate_photo(photo)
    token = token_value(token)
    base = (base or base_url()).rstrip("/")
    seed = random.randint(0, 2147483647) if seed is None else int(seed)
    deadline = time.monotonic() + timeout
    session_hash = uuid.uuid4().hex

    def note(stage: str, detail: str = "") -> None:
        if progress is not None:
            progress(stage, detail)

    note("upload", f"sending {photo_path.name} to {SPACE_ID}")
    server_path = upload_image(base, token, photo_path, session_hash,
                               timeout=max(1, int(deadline - time.monotonic())))
    note("preprocess", "removing the background on the Space")
    masked = _call(base, token, session_hash, "/preprocess_image",
                   [_image_data(server_path, photo_path)], deadline)
    if not masked or not isinstance(masked[0], dict):
        raise CloudMeshError(
            "the Space's background-removal step gave back no image")
    note("generate", "building the 3D shape — this is the slow part "
                     "(a few minutes on the free queue)")
    _call(base, token, session_hash, "/image_to_3d",
          [masked[0], seed, RESOLUTION,
           *STAGE_1, *STAGE_2, *STAGE_3], deadline)
    glb_bytes: bytes | None = None
    last_error: CloudMeshError | None = None
    for attempt in range(EXTRACT_RETRIES + 1):
        note("extract", "pulling the textured GLB down"
             + (" (retry)" if attempt else ""))
        try:
            files = _call(base, token, session_hash, "/extract_glb",
                          [DECIMATION_TARGET, TEXTURE_SIZE], deadline)
            if not files or not isinstance(files[0], dict):
                raise CloudMeshError(
                    "the Space's GLB step gave back no file")
            glb_bytes = download_file(base, token, files[0], deadline)
            break
        except CloudMeshError as exc:
            last_error = exc
            if isinstance(exc, QuotaExhaustedError):
                raise
    if glb_bytes is None:
        raise last_error or CloudMeshError("the GLB step failed")
    if len(glb_bytes) < 100 or glb_bytes[:4] != b"glTF":
        raise CloudMeshError(
            "what came back from the Space is not a GLB file — try again")
    note("done", f"{len(glb_bytes)} bytes of textured mesh")
    return glb_bytes, seed


def _glb_name(photo: Path) -> str:
    stem = "".join(c.lower() if c.isalnum() else "-" for c in photo.stem)
    stem = stem.strip("-") or "photo"
    return f"{stem}-mesh.glb"


def cloud_mesh(workspace: str | Path, photo: str | Path,
               job: str | None = None, seed: int | None = None,
               token: str | None = None, base: str | None = None,
               timeout: int = DEFAULT_TIMEOUT,
               progress=None) -> dict:
    """The whole step: photo -> cloud mesh -> GLB + description in a job.

    The mesh is generated and ingested BEFORE any job is touched, so a
    failed run leaves the workspace exactly as it was. With `job`, the
    mesh lands in that job (which must still be waiting at the plan
    gate — draft or awaiting_approval); without it, a new job is
    created from the ingested description. Either way the job ends at
    awaiting_approval: nothing builds until the description is approved.
    """
    ws = Path(workspace)
    (ws / "jobs").mkdir(parents=True, exist_ok=True)
    photo_path = validate_photo(photo)
    target: Job | None = None
    old_desc: dict | None = None
    if job:
        if not (ws / "jobs" / job).is_dir():
            raise CloudMeshError(f"no job named {job!r}")
        target = Job(ws / "jobs" / job)
        if target.state not in ("draft", "awaiting_approval"):
            raise CloudMeshError(
                f"job {job!r} is {target.state!r} — the cloud mesh step "
                "only lands in a job still waiting at the plan gate")
        old_desc = target.load_description()

    glb_bytes, used_seed = generate_mesh(
        photo_path, token=token, base=base, seed=seed,
        timeout=timeout, progress=progress)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_glb = Path(tmp) / _glb_name(photo_path)
        tmp_glb.write_bytes(glb_bytes)
        desc = ingest_glb(tmp_glb)
    if old_desc is not None:
        # An intake job's reference photo stays on record.
        if old_desc.get("reference"):
            desc["reference"] = old_desc["reference"]
        tags = list(dict.fromkeys(list(desc.get("tags", []))
                                  + list(old_desc.get("tags", []))))
        desc["tags"] = tags
    desc.setdefault("tags", []).append("cloud-mesh")
    desc["cloud_mesh"] = {
        "space": SPACE_ID,
        "photo": photo_path.name,
        "seed": used_seed,
        "glb": _glb_name(photo_path),
        "generated_at": stamp(),
    }
    problems = desc_mod.validate(desc)
    if problems:
        raise CloudMeshError(
            "the mesh came back, but its description failed validation: "
            + "; ".join(problems))

    if target is None:
        target = Job.create(ws / "jobs", desc)
    else:
        # The mesh overwrites the job's description wholesale — freeze
        # what was there first, so it can be recovered.
        target.snapshot_description("description.before-cloud-mesh.json")
        desc_mod.dump(desc, target.desc_path)
    glb_path = target.dir / _glb_name(photo_path)
    glb_path.write_bytes(glb_bytes)
    target.log("cloud_mesh", space=SPACE_ID, photo=photo_path.name,
               seed=used_seed, glb=glb_path.name,
               parts=len(desc["parts"]))
    return {"ok": True, "job": target.dir.name, "state": target.state,
            "glb": str(glb_path), "parts": len(desc["parts"]),
            "seed": used_seed, "space": SPACE_ID}
