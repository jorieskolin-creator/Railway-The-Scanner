"""
gemini_client.py — Thread-safe shared Gemini client(s) with 429 key rotation.

Primary key: GEMINI_API_KEY (or legacy API_KEY).
Secondary key: GEMINI_API_KEY_2 — activated only on HTTP 429 (quota exceeded).
503 errors (model unavailable) are NOT routed to the second key; they indicate
a model-side outage where a fresh key won't help.

Usage:
    from gemini_client import get_gemini_client, rotate_gemini_client

    client = get_gemini_client()          # returns current active client
    client = rotate_gemini_client()       # switches to next key, returns new client
"""

import os
import threading

from google import genai

_clients: list[genai.Client] = []
_current_index: int = 0
_lock = threading.Lock()


def _build_clients() -> list[genai.Client]:
    """Initialise all configured API-key clients (called once, under lock)."""
    clients = []
    for env_var in ("GEMINI_API_KEY", "API_KEY"):
        key = os.environ.get(env_var, "").strip()
        if key:
            clients.append(genai.Client(api_key=key))
            break  # only use the first primary key found

    secondary = os.environ.get("GEMINI_API_KEY_2", "").strip()
    if secondary:
        clients.append(genai.Client(api_key=secondary))

    if not clients:
        raise ValueError("No GEMINI_API_KEY configured")
    return clients


def _ensure_init() -> None:
    global _clients
    if not _clients:
        with _lock:
            if not _clients:
                _clients = _build_clients()


def get_gemini_client() -> genai.Client:
    """Return the currently active Gemini client."""
    _ensure_init()
    return _clients[_current_index % len(_clients)]


def rotate_gemini_client() -> genai.Client:
    """Rotate to the next available key and return the new client.

    Call this when a 429 (Too Many Requests) is received.  If only one key is
    configured the same client is returned (no-op rotation).
    """
    global _current_index
    _ensure_init()
    with _lock:
        if len(_clients) > 1:
            _current_index = (_current_index + 1) % len(_clients)
            key_label = "secondary" if _current_index != 0 else "primary"
            print(f"   [GeminiClient] Rotated to {key_label} API key (index {_current_index})")
        else:
            print("   [GeminiClient] 429 received but only one API key configured — no rotation")
    return _clients[_current_index % len(_clients)]
