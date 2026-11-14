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

Auth: HF_TOKEN from the environment, or the token saved in the Bench
under Settings (tokenfile.py: one file, mode 0600, in the workspace,
gitignored — never in code, never in the repo; same rule as
GEMINI_API_KEY). No token -> a plain refusal naming the fix (free
account at huggingface.co, Settings -> Access Tokens -> New token,
Read role). The token rides as a Bearer header on every request,
including the GLB download.

Disclosure: every run says plainly, before anything is sent, that the
photo is uploaded to Hugging Face's free GPU and leaves the machine.

Governor: this step deliberately does NOT take the heavy-job lock and
does NOT check the free-RAM floor — it loads no local model and runs
no local compute; it is network-bound. (The governor floor itself is
untouched; builds and image generation still go through it.)

Hardening (v1.6, T28): the upload is retried once on a transient
failure, the GLB extract is retried twice, the queue position from
the Space's SSE estimation events is reported as progress, and a
photo PNG wider than 1024 px is shrunk before upload (the Space
resizes to 512 server-side anyway). A fallback Space can be set with
DESIGNSHELL_CLOUDMESH_FALLBACK_URL: when the primary Space cannot be
reached at all (upload or background-removal stage), the whole flow
runs once against the fallback instead. No fallback Space ships by
default — every public TRELLIS.2 clone checked on 2026-11-14 was
paused or in error, so a hardcoded clone would be a fake fallback.
Quota errors never trigger the fallback: the ZeroGPU quota is
per-account across all Spaces, so a second Space would not help.

Stdlib only (urllib, json, zlib) — no new dependencies. The Space base
URL can be overridden with DESIGNSHELL_CLOUDMESH_URL (the test
suite's mock Gradio server uses this, same pattern as
DESIGNSHELL_GEMINI_URL).
"""
from __future__ import annotations

import json
import mimetypes
import os
import random
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

from . import description as desc_mod
from .governor import Cancelled, stamp
from .ingest import ingest_glb
from .intake import image_size
from .jobs import Job

# The Space. One constant to change if Microsoft's demo ever moves.
SPACE_ID = "microsoft/TRELLIS.2"
SPACE_URL = "https://microsoft-trellis-2.hf.space"

TOKEN_ENV = "HF_TOKEN"
URL_ENV_OVERRIDE = "DESIGNSHELL_CLOUDMESH_URL"
# A second Space to try when the primary cannot be reached at all
# (T28). No default: every public TRELLIS.2 clone checked on
# 2026-11-14 was paused or in error.
FALLBACK_URL_ENV = "DESIGNSHELL_CLOUDMESH_FALLBACK_URL"

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
# /extract_glb is the fragile step; two retries in the same session.
EXTRACT_RETRIES = 2
# The photo upload is retried once on a transient failure.
UPLOAD_RETRIES = 1
# A PNG wider than this on its longest side is shrunk before upload
# (the Space resizes to 512 server-side anyway).
MAX_UPLOAD_DIMENSION = 1024

TOKEN_FIX = (
    "the cloud mesh step needs a Hugging Face token: make a free account at "
    "huggingface.co, then Settings -> Access Tokens -> New token (Read role), "
    "and put it in the environment as HF_TOKEN, or save it in the Bench "
    "under Settings. The Settings copy lives in one file only you can read, "
    "in your workspace, and never in the code or the repo."
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


def fallback_url() -> str | None:
    """The fallback Space's base URL, if one is configured — else None."""
    value = os.environ.get(FALLBACK_URL_ENV, "").strip()
    return value.rstrip("/") if value else None


def _is_connection_failure(exc: CloudMeshError) -> bool:
    """True when the Space could not be reached at all (network down,
    Space paused or erroring, stream dead) — the failures a fallback
    Space can actually help with. A malformed answer from a reachable
    Space is not one of these."""
    text = str(exc)
    return ("could not reach" in text or "stopped answering" in text
            or "timed out" in text or "HTTP 5" in text)


def token_value(token: str | None = None, workspace: str | Path | None = None) -> str:
    """The HF token, or a plain refusal naming the fix.

    Order: an explicit token, then the environment, then the
    workspace's token file (written by the Settings window). The
    token is never printed or logged anywhere in this module."""
    value = token or os.environ.get(TOKEN_ENV, "")
    if not value.strip() and workspace is not None:
        from . import tokenfile

        value = tokenfile.load_token(workspace) or ""
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


# PNG colour types -> channels per pixel (bit depth 8 only; the
# downscaler declines anything else and the original uploads as-is).
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_chunks(data: bytes):
    """(type, payload) for each chunk of a PNG file."""
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack_from(">I4s", data, pos)
        yield ctype.decode("latin-1"), data[pos + 8:pos + 8 + length]
        pos += 12 + length


def _downscale_png(data: bytes, max_dim: int) -> bytes | None:
    """Shrink a PNG to at most max_dim on its longest side, stdlib-only
    (zlib decode, unfilter, nearest-neighbour resample, re-encode).
    Returns None for anything it cannot handle safely (16-bit,
    interlaced, truncated) — the caller then uploads the original."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        ihdr = None
        palette = None
        idat = bytearray()
        for ctype, payload in _png_chunks(data):
            if ctype == "IHDR":
                ihdr = payload
            elif ctype == "PLTE":
                palette = payload
            elif ctype == "IDAT":
                idat += payload
            elif ctype == "IEND":
                break
        if ihdr is None or len(ihdr) != 13:
            return None
        width, height, bit_depth, color_type, _comp, _filt, interlace = \
            struct.unpack(">IIBBBBB", ihdr)
        if bit_depth != 8 or interlace != 0 or color_type not in _PNG_CHANNELS:
            return None
        channels = _PNG_CHANNELS[color_type]
        raw = zlib.decompress(bytes(idat))
        stride = width * channels
        if len(raw) < (stride + 1) * height:
            return None
        # Unfilter (PNG filter types 0-4) into plain scanlines.
        rows = []
        prev = bytearray(stride)
        bpp = channels
        for y in range(height):
            ftype = raw[y * (stride + 1)]
            line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
            if ftype == 1:
                for i in range(bpp, stride):
                    line[i] = (line[i] + line[i - bpp]) & 0xFF
            elif ftype == 2:
                for i in range(stride):
                    line[i] = (line[i] + prev[i]) & 0xFF
            elif ftype == 3:
                for i in range(stride):
                    left = line[i - bpp] if i >= bpp else 0
                    line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
            elif ftype == 4:
                for i in range(stride):
                    a = line[i - bpp] if i >= bpp else 0
                    b = prev[i]
                    c = prev[i - bpp] if i >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[i] = (line[i] + pr) & 0xFF
            elif ftype != 0:
                return None
            rows.append(bytes(line))
            prev = line
        new_w = max(1, round(width * max_dim / max(width, height)))
        new_h = max(1, round(height * max_dim / max(width, height)))
        out = bytearray()
        for y in range(new_h):
            src_y = min(height - 1, y * height // new_h)
            out.append(0)  # filter type 0: none
            row = rows[src_y]
            for x in range(new_w):
                src_x = min(width - 1, x * width // new_w)
                out += row[src_x * channels:(src_x + 1) * channels]
        new_ihdr = struct.pack(">IIBBBBB", new_w, new_h, 8, color_type, 0, 0, 0)

        def chunk(ctype: bytes, payload: bytes) -> bytes:
            return (struct.pack(">I", len(payload)) + ctype + payload
                    + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

        result = bytearray(b"\x89PNG\r\n\x1a\n")
        result += chunk(b"IHDR", new_ihdr)
        if palette is not None:
            result += chunk(b"PLTE", palette)
        result += chunk(b"IDAT", zlib.compress(bytes(out), level=6))
        result += chunk(b"IEND", b"")
        return bytes(result)
    except (ValueError, struct.error, zlib.error, IndexError):
        return None


def prepare_upload_photo(photo: Path) -> tuple[bytes, bool]:
    """The bytes to upload for a photo. A PNG larger than
    MAX_UPLOAD_DIMENSION on its longest side is shrunk first (the
    Space resizes to 512 server-side anyway; a 4000 px phone photo is
    upload time for nothing). JPEGs and GIFs upload as-is — the shell
    has no JPEG encoder, and the Space resizes server-side; the cost
    is upload bandwidth, not GPU quota. Returns (payload, scaled)."""
    data = photo.read_bytes()
    if photo.suffix.lower() != ".png":
        return data, False
    try:
        width, height, _fmt = image_size(photo)
    except OSError:
        return data, False
    if width is None or max(width, height) <= MAX_UPLOAD_DIMENSION:
        return data, False
    scaled = _downscale_png(data, MAX_UPLOAD_DIMENSION)
    if scaled is None:
        return data, False
    return scaled, True


def upload_image(base: str, token: str, photo: Path, session_hash: str,
                 timeout: int = READ_TIMEOUT) -> str:
    """Upload the photo; returns the server-side path Gradio gives back."""
    mime, _ = mimetypes.guess_type(photo.name)
    payload, _scaled = prepare_upload_photo(photo)
    body, ctype = _multipart("files", photo.name, payload,
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
          data: list, deadline: float, progress=None) -> list:
    """Call one Gradio endpoint and wait for its output data list.
    `progress`, when given, is called with ("queue", detail) when the
    Space reports a queue position, so the user sees the wait move."""
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
    return _poll_events(base, token, fn, event_id, deadline, progress=progress)


def _poll_events(base: str, token: str, fn: str, event_id: str,
                 deadline: float, progress=None) -> list:
    """Read the SSE stream until the call completes, errors, or times out.
    Queue-position ("estimation") events are reported through progress
    as ("queue", detail) — the free queue is most of the wait, and a
    silent 5-minute wait looks like a hang."""
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
            if event_name == "estimation" and progress is not None:
                # Gradio queue estimate: {"rank": N, "queue_size": M,
                # "avg_event_process_time": seconds, ...}. The rank is
                # the position in line; the ETA is rank x average time.
                try:
                    est = json.loads(payload)
                    rank = est.get("rank")
                    size = est.get("queue_size")
                    avg = est.get("avg_event_process_time")
                    if isinstance(rank, (int, float)):
                        detail = f"in the queue: position {int(rank) + 1}"
                        if isinstance(size, (int, float)):
                            detail += f" of {int(size)}"
                        if isinstance(avg, (int, float)) and avg > 0:
                            eta = int((int(rank) + 1) * avg)
                            detail += f" — about {eta // 60}m {eta % 60:02d}s to go"
                        progress("queue", detail)
                except ValueError:
                    pass
            # "generating", heartbeats: wait for the end.
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
                  progress=None, cancel=None,
                  workspace: str | Path | None = None) -> tuple[bytes, int]:
    """Photo -> textured GLB bytes, via the TRELLIS.2 Space.

    Returns (glb_bytes, seed). `progress`, when given, is called with
    (stage, detail) as each step lands — the CLI and the Bench log it.
    `cancel`, when given, is a threading.Event checked between stages;
    a set event raises governor.Cancelled and nothing is written.

    Fallback (T28): when the primary Space cannot be reached at all
    during the upload or background-removal stages — before any GPU
    quota is spent — and DESIGNSHELL_CLOUDMESH_FALLBACK_URL names a
    second Space, the whole flow runs once against the fallback. A
    failure during generation or extraction does NOT fall back (the
    quota may already be spent on the primary), and neither does a
    quota error (the quota is per-account across all Spaces).
    """
    photo_path = validate_photo(photo)
    token = token_value(token, workspace=workspace)
    base = (base or base_url()).rstrip("/")
    seed = random.randint(0, 2147483647) if seed is None else int(seed)
    deadline = time.monotonic() + timeout
    stage = {"name": "upload"}

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled("cloud mesh cancelled")

    def note(stage_name: str, detail: str = "") -> None:
        # "queue" is a progress whisper inside a stage, not a stage of
        # its own — it must not move the stage the fallback decision
        # reads (a queue report during preprocess is still preprocess).
        if stage_name != "queue":
            stage["name"] = stage_name
        if progress is not None:
            progress(stage_name, detail)

    def run_flow(flow_base: str) -> tuple[bytes, int]:
        session_hash = uuid.uuid4().hex
        check_cancel()
        note("upload", f"sending {photo_path.name} to {SPACE_ID}")
        upload_error: CloudMeshError | None = None
        server_path = None
        for attempt in range(UPLOAD_RETRIES + 1):
            try:
                server_path = upload_image(
                    flow_base, token, photo_path, session_hash,
                    timeout=max(1, int(deadline - time.monotonic())))
                break
            except CloudMeshError as exc:
                if isinstance(exc, QuotaExhaustedError):
                    raise
                upload_error = exc
                if attempt < UPLOAD_RETRIES:
                    note("upload", "the upload stumbled — trying once more")
                    time.sleep(5)
        if server_path is None:
            raise upload_error or CloudMeshError("the photo upload failed")
        check_cancel()
        note("preprocess", "removing the background on the Space")
        masked = _call(flow_base, token, session_hash, "/preprocess_image",
                       [_image_data(server_path, photo_path)], deadline,
                       progress=note)
        if not masked or not isinstance(masked[0], dict):
            raise CloudMeshError(
                "the Space's background-removal step gave back no image")
        check_cancel()
        note("generate", "building the 3D shape — this is the slow part "
                         "(a few minutes on the free queue)")
        _call(flow_base, token, session_hash, "/image_to_3d",
              [masked[0], seed, RESOLUTION,
               *STAGE_1, *STAGE_2, *STAGE_3], deadline, progress=note)
        glb_bytes: bytes | None = None
        last_error: CloudMeshError | None = None
        for attempt in range(EXTRACT_RETRIES + 1):
            check_cancel()
            note("extract", "pulling the textured GLB down"
                 + (" (retry)" if attempt else ""))
            try:
                files = _call(flow_base, token, session_hash, "/extract_glb",
                              [DECIMATION_TARGET, TEXTURE_SIZE], deadline,
                              progress=note)
                if not files or not isinstance(files[0], dict):
                    raise CloudMeshError(
                        "the Space's GLB step gave back no file")
                glb_bytes = download_file(flow_base, token, files[0], deadline)
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

    try:
        return run_flow(base)
    except CloudMeshError as exc:
        if isinstance(exc, QuotaExhaustedError):
            raise
        fallback = fallback_url()
        if fallback is None or fallback == base:
            raise
        if stage["name"] not in ("upload", "preprocess"):
            raise
        if not _is_connection_failure(exc):
            raise
        note("fallback",
             "the main Space could not be reached — trying the fallback Space")
        return run_flow(fallback)


def _glb_name(photo: Path) -> str:
    stem = "".join(c.lower() if c.isalnum() else "-" for c in photo.stem)
    stem = stem.strip("-") or "photo"
    return f"{stem}-mesh.glb"


def cloud_mesh(workspace: str | Path, photo: str | Path,
               job: str | None = None, seed: int | None = None,
               token: str | None = None, base: str | None = None,
               timeout: int = DEFAULT_TIMEOUT,
               progress=None, cancel=None) -> dict:
    """The whole step: photo -> cloud mesh -> GLB + description in a job.

    The mesh is generated and ingested BEFORE any job is touched, so a
    failed run leaves the workspace exactly as it was. With `job`, the
    mesh lands in that job (which must still be waiting at the plan
    gate — draft or awaiting_approval); without it, a new job is
    created from the ingested description. Either way the job ends at
    awaiting_approval: nothing builds until the description is approved.
    `cancel` is a threading.Event: a set event raises
    governor.Cancelled before any job is touched.
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
        timeout=timeout, progress=progress, cancel=cancel, workspace=ws)
    if cancel is not None and cancel.is_set():
        # The mesh landed but the user stopped the run before it was
        # filed — the workspace stays exactly as it was.
        raise Cancelled("cloud mesh cancelled")

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
    # The mesh block ingest wrote points at the temp GLB, which is
    # already gone — point it at the GLB that lands in the job folder
    # instead (T21): the Blender build imports it, and the Unreal
    # package carries it.
    desc["mesh"] = {"file": _glb_name(photo_path), "format": "glb",
                    "import_scale": 1.0}
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
