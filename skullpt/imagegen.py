"""Image generation — the front half of the reference pipeline.

A prompt goes in, a batch of candidate pictures comes out; Joshua keeps
or rejects each one, and the keeper becomes a job's locked reference
(the same `reference` slot that photo intake fills). Rejected pictures
are deleted and their seeds recorded, so a re-roll never serves the
same picture twice.

The engine is leejet's stable-diffusion.cpp (binary name `sd-cli`)
driving an SD 1.5 LCM model — the class that fits an 8 GB, CPU-only
machine: ~2.3 GB RAM and minutes per picture instead of the half-hour
a full-step model takes. The shell shells out to it exactly the way it
shells out to Blender: an external tool, a loud failure when it is
missing, and nothing faked when it is not there.

Pins (verified 2026-11-11):
  engine release master-1086-465adf3 — NOTE the prebuilt Linux binary
  needs glibc 2.43 (Ubuntu 26.04); on Mint (glibc 2.39) the engine is
  built from source instead, which install.sh --with-image-engine does.
  model: LCM_Dreamshaper_v7-f16.gguf (2,135 MB) from Hugging Face repo
  Steward/lcm-dreamshaper-v7-gguf.

Guidance (v1.6, T27): a batch may carry a "guidance" record —
img2img from a starting picture (--init-img + --strength), a ControlNet
model + control image (--control-net/--control-image/--control-strength),
or an IP-Adapter model + image (--ip-adapter/--clip_vision/
--ip-adapter-image/--ip-adapter-strength). The flags were verified
against the real source-built sd-cli (master-1088-32d516c) on
2026-11-14. Guidance models live in ~/design-shell-models/ next to the
main model; a missing one refuses plainly, naming what to download.
`vary()` makes img2img variations of a kept candidate into a new batch
linked to the same job.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from pathlib import Path

from . import description as desc_mod
from . import governor
from .intake import image_size

MODEL_FILENAME = "LCM_Dreamshaper_v7-f16.gguf"
MODEL_URL = ("https://huggingface.co/Steward/lcm-dreamshaper-v7-gguf/"
             "resolve/main/" + MODEL_FILENAME)
ENGINE_REPO = "https://github.com/leejet/stable-diffusion.cpp"

# Guidance model homes (v1.6, T27): env override wins, else a scan of
# ~/design-shell-models/ for the first file whose name matches. The
# stable-diffusion.cpp docs list the GGUF control/IP-Adapter models;
# any of them works — the shell does not pin one.
CONTROL_NET_ENV = "DESIGNSHELL_SD_CONTROL_NET"
IP_ADAPTER_ENV = "DESIGNSHELL_SD_IP_ADAPTER"
CLIP_VISION_ENV = "DESIGNSHELL_SD_CLIP_VISION"
CONTROL_NET_PATTERNS = ("*control*.gguf", "*Control*.gguf")
IP_ADAPTER_PATTERNS = ("*ip-adapter*.gguf", "*ip_adapter*.gguf")
CLIP_VISION_PATTERNS = ("*clip-vision*.gguf", "*clip_vision*.gguf")

DEFAULT_COUNT = 3
DEFAULT_SIZE = 512
LCM_STEPS = 4
LCM_CFG = 1.5
DEFAULT_NEGATIVE = "blurry, deformed, distorted, low quality, watermark"
PER_IMAGE_TIMEOUT_S = 1800
DEFAULT_VARY_STRENGTH = 0.6


class ImageGenError(RuntimeError):
    pass


# ------------------------------------------------------------------ finding

def find_engine() -> Path | None:
    """The sd-cli binary, or None. Env override wins, like Blender's."""
    import os

    override = os.environ.get("DESIGNSHELL_SD_CLI")
    if override and Path(override).exists():
        return Path(override)
    for name in ("sd-cli", "sd"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in (Path("/opt/sd.cpp/bin/sd-cli"), Path("/opt/sd.cpp/sd-cli"),
                      Path.home() / ".local" / "bin" / "sd-cli",
                      Path.home() / "sd.cpp" / "build" / "bin" / "sd-cli"):
        if candidate.exists():
            return candidate
    return None


def find_model(workspace: str | Path | None = None) -> Path | None:
    """The model file, or None. Env override wins; then the model home."""
    import os

    override = os.environ.get("DESIGNSHELL_SD_MODEL")
    if override and Path(override).exists():
        return Path(override)
    homes = [Path.home() / "design-shell-models"]
    if workspace is not None:
        homes.append(Path(workspace) / "models")
    for home in homes:
        exact = home / MODEL_FILENAME
        if exact.exists():
            return exact
        if home.exists():
            ggufs = sorted(home.glob("*.gguf"))
            if ggufs:
                return ggufs[0]
    return None


def engine_missing_text() -> str:
    return ("the image engine (stable-diffusion.cpp) is not installed — "
            "nothing was generated and nothing was faked. Fix: add the "
            "image engine through the installer's image-engine option "
            "(see the README's install options). It builds the engine "
            "from source; the prebuilt Linux binary needs a newer system "
            "library than Mint ships.")


def model_missing_text() -> str:
    return (f"the image model is not downloaded — nothing was generated. "
            f"Fix: download {MODEL_FILENAME} (about 2.1 GB) from "
            f"{MODEL_URL} and put it in ~/design-shell-models/, or set "
            f"DESIGNSHELL_SD_MODEL to its path.")


def _find_guidance_model(workspace: str | Path | None, env_name: str,
                         patterns: tuple[str, ...]) -> Path | None:
    """A guidance model file, or None: env override wins, else the
    first name-matching .gguf in the model home."""
    import os

    override = os.environ.get(env_name)
    if override and Path(override).exists():
        return Path(override)
    homes = [Path.home() / "design-shell-models"]
    if workspace is not None:
        homes.append(Path(workspace) / "models")
    for home in homes:
        if not home.exists():
            continue
        for pattern in patterns:
            matches = sorted(home.glob(pattern))
            if matches:
                return matches[0]
    return None


def find_control_net(workspace: str | Path | None = None) -> Path | None:
    return _find_guidance_model(workspace, CONTROL_NET_ENV, CONTROL_NET_PATTERNS)


def find_ip_adapter(workspace: str | Path | None = None) -> Path | None:
    return _find_guidance_model(workspace, IP_ADAPTER_ENV, IP_ADAPTER_PATTERNS)


def find_clip_vision(workspace: str | Path | None = None) -> Path | None:
    return _find_guidance_model(workspace, CLIP_VISION_ENV, CLIP_VISION_PATTERNS)


def guidance_missing_text(kind: str) -> str:
    names = {
        "control_net": ("a ControlNet model", CONTROL_NET_ENV),
        "ip_adapter": ("an IP-Adapter model", IP_ADAPTER_ENV),
        "clip_vision": ("a CLIP vision encoder", CLIP_VISION_ENV),
    }
    label, env_name = names[kind]
    return (f"{label} is needed for this kind of guidance and none was "
            f"found — nothing was generated. Fix: download a {label} "
            f"(.gguf) and put it in ~/design-shell-models/, or set "
            f"{env_name} to its path.")


# -------------------------------------------------------------------- batch

def slugify(text: str) -> str:
    words = [w.lower() for w in text.replace("_", " ").split() if w.isalnum() or any(c.isalnum() for c in w)]
    cleaned = ["".join(c for c in w if c.isalnum()) for w in words]
    return "-".join(w for w in cleaned if w)[:40] or "image"


def batch_path(workspace: str | Path, slug: str) -> Path:
    return Path(workspace) / "images" / slug


def load_batch(batch_dir: str | Path) -> dict:
    path = Path(batch_dir) / "batch.json"
    if not path.exists():
        raise ImageGenError(f"no image batch at {batch_dir}")
    return json.loads(path.read_text())


def save_batch(batch_dir: str | Path, batch: dict) -> None:
    (Path(batch_dir) / "batch.json").write_text(json.dumps(batch, indent=2) + "\n")


def new_batch(workspace: str | Path, prompt: str, job: str | None = None,
              count: int = DEFAULT_COUNT, size: int = DEFAULT_SIZE,
              negative: str = DEFAULT_NEGATIVE) -> tuple[dict, Path]:
    """Create the batch record; generation is a separate step."""
    slug = slugify(prompt) + "-" + time.strftime("%H%M%S")
    bdir = batch_path(workspace, slug)
    suffix = 1
    while bdir.exists():
        suffix += 1
        bdir = batch_path(workspace, f"{slug}-{suffix}")
    bdir.mkdir(parents=True, exist_ok=True)
    batch = {
        "slug": bdir.name,
        "prompt": prompt,
        "negative": negative,
        "job": job,
        "count_per_round": count,
        "size": size,
        "steps": LCM_STEPS,
        "created": governor.stamp(),
        "engine": None,
        "model": None,
        "candidates": [],
        "rejected_seeds": [],
        "reference_file": None,
        # Guidance (T27): img2img / ControlNet / IP-Adapter settings for
        # this batch, stored so a re-roll reuses them. {} = plain
        # text-to-image. "variation_of" links a vary() batch to the
        # keeper it came from.
        "guidance": {},
        "variation_of": None,
    }
    save_batch(bdir, batch)
    return batch, bdir


def _fresh_seed(batch: dict, rng: random.Random) -> int:
    used = {c["seed"] for c in batch["candidates"]} | set(batch["rejected_seeds"])
    seed = rng.randint(1, 2**31 - 1)
    while seed in used:
        seed = rng.randint(1, 2**31 - 1)
    return seed


def _resolve_guidance(workspace: str | Path, batch: dict,
                      overrides: dict | None = None) -> tuple[list[str], dict]:
    """Turn a batch's guidance record (+ explicit overrides, which win)
    into sd-cli flags. Models are resolved here, before the lock is
    taken — a missing model or picture fails fast with a plain message
    instead of halfway through a round. Returns (flags, guidance)."""
    guidance = dict(batch.get("guidance") or {})
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                guidance[key] = value
    flags: list[str] = []
    init_img = guidance.get("init_img")
    if init_img:
        if not Path(init_img).exists():
            raise ImageGenError(
                f"the starting picture for this batch is missing: {init_img} "
                "— it was deleted or moved since the batch was made")
        flags += ["-i", str(init_img),
                  "--strength", str(guidance.get("strength", 0.75))]
    control_net = guidance.get("control_net")
    if control_net:
        model = find_control_net(workspace) if control_net is True else Path(control_net)
        if model is None or not Path(model).exists():
            raise ImageGenError(guidance_missing_text("control_net"))
        control_image = guidance.get("control_image")
        if not control_image or not Path(control_image).exists():
            raise ImageGenError(
                "ControlNet guidance needs a control image — pick the "
                "picture whose shape the generation should follow")
        flags += ["--control-net", str(model),
                  "--control-image", str(control_image),
                  "--control-strength", str(guidance.get("control_strength", 0.9))]
        guidance["control_net"] = str(model)
    ip_adapter = guidance.get("ip_adapter")
    if ip_adapter:
        model = find_ip_adapter(workspace) if ip_adapter is True else Path(ip_adapter)
        if model is None or not Path(model).exists():
            raise ImageGenError(guidance_missing_text("ip_adapter"))
        clip = find_clip_vision(workspace)
        if clip is None:
            raise ImageGenError(guidance_missing_text("clip_vision"))
        ip_image = guidance.get("ip_adapter_image")
        if not ip_image or not Path(ip_image).exists():
            raise ImageGenError(
                "IP-Adapter guidance needs a style image — pick the "
                "picture whose look the generation should borrow")
        flags += ["--ip-adapter", str(model), "--clip_vision", str(clip),
                  "--ip-adapter-image", str(ip_image),
                  "--ip-adapter-strength", str(guidance.get("ip_adapter_strength", 1.0))]
        guidance["ip_adapter"] = str(model)
    return flags, guidance


def generate(workspace: str | Path, batch_dir: str | Path,
             count: int | None = None, timeout_s: int = PER_IMAGE_TIMEOUT_S,
             cancel=None, progress=None, guidance: dict | None = None) -> dict:
    """Generate one round of candidates into the batch.

    Runs under the governor's heavy-job lock. A candidate that fails or
    times out is recorded as failed — the round reports what landed.
    `cancel`, when given, is a threading.Event checked before each
    candidate; a set event raises governor.Cancelled and the candidates
    already landed stay landed. `progress`, when given, is called with
    (done, total, text) as each candidate lands. `guidance`, when
    given, overrides the batch's stored guidance for this round (and
    is stored, so later rounds reuse it).
    """
    batch = load_batch(batch_dir)
    engine = find_engine()
    if engine is None:
        raise ImageGenError(engine_missing_text())
    model = find_model(workspace)
    if model is None:
        raise ImageGenError(model_missing_text())
    flags, resolved = _resolve_guidance(workspace, batch, guidance)
    if resolved != (batch.get("guidance") or {}):
        batch["guidance"] = resolved
        save_batch(batch_dir, batch)
    batch["engine"] = str(engine)
    batch["model"] = model.name
    n = count if count is not None else int(batch.get("count_per_round", DEFAULT_COUNT))
    size = int(batch.get("size", DEFAULT_SIZE))
    rng = random.Random()
    lock = governor.HeavyJobLock(Path(workspace) / ".heavy.lock")
    if progress is not None:
        progress(0, n, "starting the image engine")
    with lock:
        for done in range(n):
            if cancel is not None and cancel.is_set():
                raise governor.Cancelled(
                    f"image batch cancelled — {done} of {n} candidates landed")
            seed = _fresh_seed(batch, rng)
            index = len(batch["candidates"]) + 1
            fname = f"cand-{index:02d}.png"
            out = Path(batch_dir) / fname
            cmd = [str(engine), "-m", str(model),
                   "-p", batch["prompt"], "-n", batch.get("negative", ""),
                   "--steps", str(LCM_STEPS), "--cfg-scale", str(LCM_CFG),
                   "--sampling-method", "lcm",
                   "-W", str(size), "-H", str(size),
                   "-s", str(seed), "-o", str(out)] + flags
            record = {"file": fname, "seed": seed, "status": "pending"}
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout_s)
                if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                    record["status"] = "pending"
                else:
                    record["status"] = "failed"
                    record["error"] = (proc.stderr or proc.stdout or "")[-400:]
                    out.unlink(missing_ok=True)
            except subprocess.TimeoutExpired:
                record["status"] = "failed"
                record["error"] = f"timed out after {timeout_s}s"
                out.unlink(missing_ok=True)
            batch["candidates"].append(record)
            save_batch(batch_dir, batch)
            if progress is not None:
                progress(done + 1, n, f"candidate {done + 1} of {n} landed")
    return batch


def vary(workspace: str | Path, batch_dir: str | Path, filename: str,
         prompt: str | None = None, strength: float = DEFAULT_VARY_STRENGTH,
         count: int = DEFAULT_COUNT, timeout_s: int = PER_IMAGE_TIMEOUT_S,
         cancel=None, progress=None) -> dict:
    """Img2img variations of a kept candidate, into a NEW batch linked
    to the same job (v1.6, T27). The keeper is the starting picture;
    strength is how far the variations may wander from it (0.6 keeps
    the picture recognisable, 0.9 barely remembers it). The new batch
    records "variation_of" so the lineage is visible. A rejected
    candidate cannot be varied — its file is already deleted."""
    batch = load_batch(batch_dir)
    record = next((c for c in batch["candidates"] if c["file"] == filename), None)
    if record is None:
        raise ImageGenError(f"no candidate {filename!r} in this batch")
    if record["status"] != "kept":
        raise ImageGenError(
            f"only a kept candidate can be varied — {filename} is {record['status']}")
    src = Path(batch_dir) / filename
    if not src.exists():
        raise ImageGenError(f"candidate file missing: {src}")
    if not (0.0 < float(strength) <= 1.0):
        raise ImageGenError("variation strength must be between 0 and 1")
    new, new_dir = new_batch(workspace, prompt or batch["prompt"],
                             job=batch.get("job"), count=count,
                             size=int(batch.get("size", DEFAULT_SIZE)),
                             negative=batch.get("negative", DEFAULT_NEGATIVE))
    new["guidance"] = {"init_img": str(src), "strength": float(strength)}
    new["variation_of"] = {"batch": batch["slug"], "file": filename}
    save_batch(new_dir, new)
    return generate(workspace, new_dir, count=count, timeout_s=timeout_s,
                    cancel=cancel, progress=progress)


# ------------------------------------------------------------------ decide

def decide(workspace: str | Path, batch_dir: str | Path, filename: str,
           decision: str) -> dict:
    """Keep or reject one candidate.

    Keep: marks it kept. The first kept candidate of a job-linked batch
    is promoted to the job's reference (and `set_reference` can move
    that honour to another kept candidate later).
    Reject: the file is DELETED and the seed recorded, so the same
    picture is never served again.
    """
    if decision not in ("keep", "reject"):
        raise ImageGenError(f"decision must be keep or reject, got {decision!r}")
    batch = load_batch(batch_dir)
    record = next((c for c in batch["candidates"] if c["file"] == filename), None)
    if record is None:
        raise ImageGenError(f"no candidate {filename!r} in this batch")
    if decision == "reject":
        record["status"] = "rejected"
        if record["seed"] not in batch["rejected_seeds"]:
            batch["rejected_seeds"].append(record["seed"])
        (Path(batch_dir) / filename).unlink(missing_ok=True)
        if batch.get("reference_file") == filename:
            batch["reference_file"] = None
    else:
        record["status"] = "kept"
        if batch.get("job") and not batch.get("reference_file"):
            promote(workspace, batch_dir, filename)
            batch = load_batch(batch_dir)  # promote saved its own copy
        elif not batch.get("reference_file"):
            batch["reference_file"] = filename
    save_batch(batch_dir, batch)
    return batch


def promote(workspace: str | Path, batch_dir: str | Path, filename: str) -> dict:
    """Make a kept candidate the linked job's reference image."""
    batch = load_batch(batch_dir)
    job_name = batch.get("job")
    if not job_name:
        batch["reference_file"] = filename
        save_batch(batch_dir, batch)
        return batch
    from .jobs import Job

    src = Path(batch_dir) / filename
    if not src.exists():
        raise ImageGenError(f"candidate file missing: {src}")
    job = Job(Path(workspace) / "jobs" / job_name)
    dest = job.dir / "reference.png"
    shutil.copy2(src, dest)
    width, height, fmt = image_size(dest)
    desc = job.load_description()
    desc["reference"] = {
        "file": dest.name,
        "stored_at": str(dest),
        "source": "generated",
        "prompt": batch["prompt"],
        "seed": next((c["seed"] for c in batch["candidates"]
                      if c["file"] == filename), None),
        "model": batch.get("model"),
        "width_px": width, "height_px": height, "format": fmt,
    }
    desc_mod.dump(desc, job.desc_path)
    job.log("reference_set", file=filename, seed=desc["reference"]["seed"])
    batch["reference_file"] = filename
    save_batch(batch_dir, batch)
    return batch
