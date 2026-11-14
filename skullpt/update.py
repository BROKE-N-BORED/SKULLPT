"""Update check — is there a newer SKULLPT release? (v1.6, T29).

A read-only check against the GitHub releases page: it reads the
latest release tag and compares it with the running version. It never
downloads anything, never installs anything, and never changes a file
— the user decides what to do with the answer. A network failure is
reported plainly ("could not reach GitHub"), not as an error.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

RELEASES_LATEST_URL = (
    "https://api.github.com/repos/BROKE-N-BORED/SKULLPT/releases/latest")
RELEASE_PAGE_URL = "https://github.com/BROKE-N-BORED/SKULLPT/releases"
TIMEOUT_S = 15


def _version_tuple(tag: str) -> tuple[int, ...]:
    """'v1.6' -> (1, 6); anything unparseable -> () (never crashes the
    comparison — an unparseable tag just never wins)."""
    cleaned = tag.strip().lstrip("vV")
    try:
        return tuple(int(part) for part in cleaned.split(".") if part != "")
    except ValueError:
        return ()


def check_for_update(current_version: str) -> dict:
    """{"current", "latest", "update_available", "url", "error"}.
    "error" is a plain-language string when the check could not run;
    "latest" is None then, and update_available is False."""
    try:
        req = urllib.request.Request(
            RELEASES_LATEST_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "SKULLPT-update-check"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"current": current_version, "latest": None,
                "update_available": False, "url": RELEASE_PAGE_URL,
                "error": f"GitHub answered with an error (HTTP {exc.code})"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"current": current_version, "latest": None,
                "update_available": False, "url": RELEASE_PAGE_URL,
                "error": f"could not reach GitHub ({exc})"}
    tag = str(data.get("tag_name") or "")
    latest = tag.lstrip("vV") or None
    update_available = bool(
        latest and _version_tuple(latest) > _version_tuple(current_version))
    url = str(data.get("html_url") or RELEASE_PAGE_URL)
    return {"current": current_version, "latest": latest,
            "update_available": update_available, "url": url, "error": None}
