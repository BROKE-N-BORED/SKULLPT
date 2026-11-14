"""Jobs and the Plan Gate.

A job wraps one Description File through its lifecycle:

  draft -> awaiting_approval -> approved -> building -> checking
        -> awaiting_review -> filed        (or -> failed at any build step)

The gate is enforced here, in code, not by politeness: build() refuses to
run on a job that is not in `approved` state. Every transition is appended
to the job's JSONL session log, and the log + description + build report
travel with the asset into the Library.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import description as desc_mod
from .governor import stamp

STATES = (
    "draft", "awaiting_approval", "approved", "building",
    "checking", "awaiting_review", "filed", "failed",
)


class GateError(RuntimeError):
    """Raised when work is attempted before the user approved it."""


class Job:
    def __init__(self, job_dir: str | Path):
        self.dir = Path(job_dir)
        self.state_path = self.dir / "job.json"
        self.log_path = self.dir / "session.jsonl"
        self.desc_path = self.dir / "description.json"
        self.output_dir = self.dir / "output"
        self._state = json.loads(self.state_path.read_text(encoding="utf-8"))

    # -- creation ---------------------------------------------------------
    @classmethod
    def create(cls, root: str | Path, desc: dict) -> "Job":
        problems = desc_mod.validate(desc)
        if problems:
            raise desc_mod.DescriptionError(
                "Invalid Description File:\n- " + "\n- ".join(problems))
        slug = _slug(desc["name"])
        job_dir = Path(root) / slug
        n = 2
        while job_dir.exists():
            job_dir = Path(root) / f"{slug}-{n}"
            n += 1
        job_dir.mkdir(parents=True)
        (job_dir / "output").mkdir()
        desc_mod.dump(desc, job_dir / "description.json")
        job = cls.__new__(cls)
        job.dir, job.state_path = job_dir, job_dir / "job.json"
        job.log_path, job.desc_path = job_dir / "session.jsonl", job_dir / "description.json"
        job.output_dir = job_dir / "output"
        job._state = {"name": desc["name"], "kind": desc["kind"],
                      "state": "draft", "created": stamp()}
        job._save()
        job.log("created", state="draft")
        job.transition("awaiting_approval")
        return job

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state["state"]

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")

    def log(self, event: str, **fields) -> None:
        row = {"ts": stamp(), "event": event, **fields}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def transition(self, new_state: str, **fields) -> None:
        if new_state not in STATES:
            raise ValueError(f"unknown job state {new_state!r}")
        old = self._state["state"]
        self._state["state"] = new_state
        self._save()
        self.log("transition", **{"from": old, "to": new_state}, **fields)

    # -- the gate ---------------------------------------------------------
    def snapshot_description(self, name: str) -> Path:
        """Copy the job's current description.json to `name` inside the
        job folder, so what was there before a change stays recoverable.
        Used at approve time (what the user approved) and before any
        step that rewrites the description (vision patches, cloud mesh)."""
        dest = self.dir / name
        shutil.copy2(self.desc_path, dest)
        self.log("description_snapshot", file=name)
        return dest

    def approve(self) -> None:
        if self.state != "awaiting_approval":
            raise GateError(f"cannot approve from state {self.state!r}")
        # What the user approved is frozen here — later steps may patch
        # or replace description.json, but the approved copy survives.
        self.snapshot_description("description.approved.json")
        self.transition("approved")

    def retry(self) -> None:
        """Return a failed job to `approved` so it can build again.

        The description was already approved; the failure was in the
        build, not the plan. Only `failed` jobs can retry — anything
        else is a state error, not a retry.
        """
        if self.state != "failed":
            raise GateError(f"cannot retry from state {self.state!r} (needs failed)")
        self._state.pop("failure", None)
        self._save()
        self.transition("approved")
        self.log("retry")

    def require_approved(self) -> None:
        if self.state != "approved":
            raise GateError(
                f"plan gate: job is {self.state!r}, not 'approved' — "
                "the user must approve the description before anything is built"
            )

    def load_description(self) -> dict:
        return desc_mod.load(self.desc_path)

    def fail(self, reason: str) -> None:
        self._state["failure"] = reason
        self._save()
        self.transition("failed", reason=reason)


def _slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "job"


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
