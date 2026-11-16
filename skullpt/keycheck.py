"""Key validation - a tiny real call that proves a key works.

Saving a key in Settings runs one read-only list call against the
provider: if the key is accepted, the key is saved; if the provider
says the key is not valid, the key is NOT saved and the user hears
why in plain words. The key value never appears in any message.

Endpoint overrides exist so the checks can be tested against a local
mock server (DESIGNSHELL_GEMINI_VALIDATE_URL,
DESIGNSHELL_GROQ_VALIDATE_URL, DESIGNSHELL_HF_VALIDATE_URL); the mock
answers 200 for a good key and 401 for a bad one, the same shape as
the real services.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request

# Where a user who does not have a key yet goes to make one. The
# primary flow is pasting the key you already have; these are the
# fallback buttons in Settings.
GEMINI_KEY_PAGE = "https://aistudio.google.com/apikey"
GROQ_KEY_PAGE = "https://console.groq.com/keys"


def _check(url: str, headers: dict, timeout: float,
           noun: str = "key") -> tuple[bool, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True, "Key accepted."
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            return False, (f"That {noun} did not work - the service said it "
                           f"is not valid. Check the {noun} and try again.")
        return False, (f"The check failed (the service answered "
                       f"HTTP {exc.code}) - the {noun} was not saved.")
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, ("Could not reach the service - check the network "
                       "connection and try again.")


def validate_gemini_key(key: str, timeout: float = 15.0) -> tuple[bool, str]:
    """One read-only models-list call. A 400/401/403 means the key is
    not valid; anything else is a network or service problem."""
    override = os.environ.get("DESIGNSHELL_GEMINI_VALIDATE_URL")
    query = f"key={urllib.parse.quote(key)}"
    if override:
        sep = "&" if "?" in override else "?"
        url = f"{override}{sep}{query}"
    else:
        url = ("https://generativelanguage.googleapis.com/v1beta/models"
               f"?{query}&pageSize=1")
    return _check(url, {}, timeout)


def validate_groq_key(key: str, timeout: float = 15.0) -> tuple[bool, str]:
    """One read-only models-list call with the key as a Bearer token."""
    url = (os.environ.get("DESIGNSHELL_GROQ_VALIDATE_URL")
           or "https://api.groq.com/openai/v1/models")
    return _check(url, {"Authorization": f"Bearer {key}"}, timeout)


def validate_hf_token(token: str, timeout: float = 15.0) -> tuple[bool, str]:
    """One read-only whoami call with the token as a Bearer token
    (v1.10, N32): the Hugging Face token is validated before it is
    saved, like the Gemini and Groq keys."""
    url = (os.environ.get("DESIGNSHELL_HF_VALIDATE_URL")
           or "https://huggingface.co/api/whoami-v2")
    return _check(url, {"Authorization": f"Bearer {token}"}, timeout,
                  noun="token")
