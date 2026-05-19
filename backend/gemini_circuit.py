"""
gemini_circuit.py — Lightweight per-model circuit breaker.

Prevents the app from hammering a Gemini model that is in a 503 storm.
After N consecutive failures within a time window, the model is marked
"open" (skip it) for a cooldown period.  This lets subsequent scan stages
skip a known-broken model instantly instead of burning 60-120s of timeout.

Thread-safe — multiple scan workers can call concurrently.
"""

import threading
import time

# model_name -> {"failures": int, "open_until": float}
_state: dict[str, dict] = {}
_lock = threading.Lock()

# How many consecutive failures before the circuit opens.
FAILURE_THRESHOLD = 3

# How long (seconds) to skip the model after the circuit opens.
DEFAULT_COOLDOWN = 120.0


def is_open(model: str) -> bool:
    """True if model should be skipped (too many recent failures)."""
    with _lock:
        s = _state.get(model)
        if not s:
            return False
        # Only check cooldown expiry if the circuit has actually opened
        # (open_until > 0). Before the threshold is reached, open_until is 0
        # and we must not pop the accumulating failure count.
        if s["open_until"] > 0 and time.time() > s["open_until"]:
            # Cooldown expired — reset and allow traffic again.
            _state.pop(model, None)
            return False
        return s["failures"] >= FAILURE_THRESHOLD


def record_failure(model: str, cooldown: float = DEFAULT_COOLDOWN):
    """Record a failed call.  Opens the circuit after FAILURE_THRESHOLD hits."""
    with _lock:
        s = _state.setdefault(model, {"failures": 0, "open_until": 0})
        s["failures"] += 1
        if s["failures"] >= FAILURE_THRESHOLD:
            s["open_until"] = time.time() + cooldown
            print(f"   ⚡ [Circuit Breaker] {model} — circuit OPEN after {s['failures']} failures (cooldown {cooldown}s)")


def record_success(model: str):
    """Reset failure count on success — model is healthy again."""
    with _lock:
        if model in _state:
            _state.pop(model, None)
