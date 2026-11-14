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
import os
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
        # Atomic write: job.json is read by the Bench's background-build
        # poll while the build thread saves it, and a plain write_text
        # truncates first — a reader mid-write saw an empty file and
        # crashed on JSON. Write beside it and rename, so a reader
        # always sees a complete file (a crash mid-write leaves the
        # old file, not a torn one).
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.state_path)

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

    # -- management (v1.5, T10) ----------------------------------------
    def delete(self) -> None:
        """Remove the job's folder entirely. The caller confirms first
        (the Bench asks with No as the default); this just does it.
        A job mid-build cannot be deleted — stop the build first."""
        if self.state in ("building", "checking"):
            raise GateError(f"cannot delete a job that is {self.state!r} — "
                            "wait for the build to finish first")
        shutil.rmtree(self.dir)

    def rename(self, new_name: str) -> "Job":
        """Rename the job: new folder slug + new name in job.json and
        in the description, so the title stays consistent everywhere.
        Returns the Job at its new location."""
        name = (new_name or "").strip()
        if not name:
            raise ValueError("a job needs a name — the new name is empty")
        slug = _slug(name)
        if not slug:
            raise ValueError(f"the name {new_name!r} makes no usable job folder")
        if self.state in ("building", "checking"):
            raise GateError(f"cannot rename a job that is {self.state!r} — "
                            "wait for the build to finish first")
        dest = self.dir.parent / slug
        if dest.exists() and dest.resolve() != self.dir.resolve():
            raise ValueError(f"a job named {slug!r} already exists")
        if dest.resolve() == self.dir.resolve():
            return self  # same slug — nothing to move
        desc = self.load_description()
        desc["name"] = name
        problems = desc_mod.validate(desc)
        if problems:
            raise desc_mod.DescriptionError(
                "Invalid Description File:\n- " + "\n- ".join(problems))
        self.dir.rename(dest)
        renamed = Job(dest)
        renamed._state["name"] = name
        renamed._save()
        desc_mod.dump(desc, renamed.desc_path)
        renamed.log("renamed", **{"from": self.dir.name, "to": slug})
        return renamed

    def duplicate(self) -> "Job":
        """A fresh job with the same description: new folder, new
        session log, empty output dir, waiting at the plan gate.
        The description is the asset; the job is the workflow, and
        the copy starts the workflow over."""
        desc = self.load_description()
        desc["name"] = f"{desc.get('name', self.dir.name)} copy"
        return Job.create(self.dir.parent, desc)

    def edit_part(self, part_id: str, fields: dict) -> str:
        """Change one part's parameters (position, size, material,
        bevel, radius) and save the description.

        The description is validated before it is written, and the
        pre-edit copy is snapshotted first. A job that was already
        approved (or further along) goes back to awaiting_approval:
        what was approved is no longer what is there, so the plan
        gate has to see it again. Returns the job's new state."""
        if self.state in ("building", "checking"):
            raise GateError(f"cannot edit a job that is {self.state!r} — "
                            "wait for the build to finish first")
        if self.state == "filed":
            raise GateError("cannot edit a filed job — it lives in the "
                            "Library now; duplicate it to change it")
        desc = self.load_description()
        part = next((p for p in desc.get("parts", []) if p.get("id") == part_id), None)
        if part is None:
            raise ValueError(f"no part {part_id!r} in this job")
        allowed = {"position_cm", "size_cm", "material", "bevel_cm", "radius_cm",
                   "profile_cm", "path_cm", "type"}
        changed = {}
        for field, value in fields.items():
            if field not in allowed:
                raise ValueError(f"field {field!r} is not editable here")
            part[field] = value
            changed[field] = value
        if not changed:
            return self.state
        problems = desc_mod.validate(desc)
        if problems:
            raise desc_mod.DescriptionError(
                "that edit would break the description:\n- " + "\n- ".join(problems))
        self.snapshot_description("description.before-part-edit.json")
        desc_mod.dump(desc, self.desc_path)
        self.log("part_edited", part=part_id, changed=sorted(changed))
        if self.state not in ("draft", "awaiting_approval"):
            # What was approved is gone — the gate sees it again.
            self._state.pop("failure", None)
            self._save()
            self.transition("awaiting_approval", reason="description edited")
        return self.state

    def edit_character(self, character: dict) -> str:
        """Replace the character block (sliders, skin, rig) and save
        the description (v1.7, T26). Same rules as edit_part: the
        description is validated before it is written, the pre-edit
        copy is snapshotted first, and a job past the plan gate goes
        back to awaiting_approval. Returns the job's new state."""
        if self.state in ("building", "checking"):
            raise GateError(f"cannot edit a job that is {self.state!r} — "
                            "wait for the build to finish first")
        if self.state == "filed":
            raise GateError("cannot edit a filed job — it lives in the "
                            "Library now; duplicate it to change it")
        if not isinstance(character, dict):
            raise ValueError("edit-character needs a character object")
        desc = self.load_description()
        if desc.get("kind") != "character":
            raise ValueError(
                f"job {self.dir.name!r} is a {desc.get('kind')!r} job, "
                "not a character job")
        desc["character"] = character
        problems = desc_mod.validate(desc)
        if problems:
            raise desc_mod.DescriptionError(
                "that edit would break the description:\n- " + "\n- ".join(problems))
        self.snapshot_description("description.before-character-edit.json")
        desc_mod.dump(desc, self.desc_path)
        self.log("character_edited")
        if self.state not in ("draft", "awaiting_approval"):
            # What was approved is gone — the gate sees it again.
            self._state.pop("failure", None)
            self._save()
            self.transition("awaiting_approval", reason="description edited")
        return self.state


def recover_stale_jobs(workspace: str | Path) -> list[str]:
    """Jobs left in `building`/`checking` by a crash or a closed Bench
    go back to `approved` with a note — unless the heavy-job lock is
    held by a live process, in which case a build may genuinely be
    running and nothing is touched. Returns the recovered job names."""
    ws = Path(workspace)
    jobs_root = ws / "jobs"
    if not jobs_root.is_dir():
        return []
    lock_path = ws / ".heavy.lock"
    if lock_path.exists():
        try:
            holder = int(lock_path.read_text().strip() or "0")
        except ValueError:
            holder = 0
        if holder > 0 and Path(f"/proc/{holder}").exists():
            return []  # a build is running — hands off
    recovered: list[str] = []
    for job_dir in sorted(p for p in jobs_root.iterdir() if p.is_dir()):
        try:
            job = Job(job_dir)
        except Exception:
            continue  # unreadable jobs are listed elsewhere (U32)
        if job.state in ("building", "checking"):
            old = job.state
            job._state.pop("failure", None)
            job._save()
            job.transition("approved", reason=f"recovered from stale {old}")
            job.log("recovered", **{"from": old})
            recovered.append(job_dir.name)
    return recovered


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
