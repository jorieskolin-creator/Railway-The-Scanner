"""
gemini_client.py — Thread-safe shared Gemini client singleton.

Replaces ~25 scattered genai.Client(api_key=...) calls with a single
connection-reusing client. Import get_gemini_client() everywhere instead
of creating local clients.
"""

import os
import threading

from google import genai

_client = None
_lock = threading.Lock()


def get_gemini_client() -> genai.Client:
    """Return a shared Gemini client (connection reuse)."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
                if not key:
                    raise ValueError("No GEMINI_API_KEY configured")
                _client = genai.Client(api_key=key)
    return _client
