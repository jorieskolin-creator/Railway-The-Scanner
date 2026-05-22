"""
gemini_client.py — Thread-safe shared Gemini client.

Uses GEMINI_API_KEY, or legacy API_KEY as a fallback.
"""

import os
import threading

from google import genai

_client: genai.Client | None = None
_lock = threading.Lock()


def _build_client() -> genai.Client:
    """Initialise the configured API-key client (called once, under lock)."""
    for env_var in ("GEMINI_API_KEY", "API_KEY"):
        key = os.environ.get(env_var, "").strip()
        if key:
            return genai.Client(api_key=key)
    raise ValueError("No GEMINI_API_KEY configured")


def _ensure_init() -> None:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = _build_client()


def get_gemini_client() -> genai.Client:
    """Return the shared Gemini client."""
    _ensure_init()
    return _client
