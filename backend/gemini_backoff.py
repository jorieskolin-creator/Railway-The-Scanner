"""
gemini_backoff.py — 503 UNAVAILABLE retry helper for Gemini API calls.

Google's gemini-3.1-pro-preview frequently returns 503 during high-demand
periods. Google's own message: "Spikes in demand are usually temporary."

Exponential backoff (2s → 4s) gives the spike a moment to pass before
falling through to the next model in the fallback chain.

Usage:
    from gemini_backoff import call_with_503_backoff

    response = call_with_503_backoff(
        lambda: client.models.generate_content(model=..., contents=..., config=...),
        label="Re-Synthesis gemini-3.1-pro-preview",
    )
"""

import time


def call_with_503_backoff(fn, label="Gemini", delays=(2, 4)):
    """
    Call fn() with automatic retry on 503 UNAVAILABLE responses.

    fn must be a zero-argument callable that makes a Gemini API request.

    Retry schedule:
        attempt 1 — immediate
        attempt 2 — wait delays[0] seconds (default 2s)
        attempt 3 — wait delays[1] seconds (default 4s)
        → raise the last 503 exception so the caller's fallback model kicks in

    Any non-503 exception is re-raised immediately without delay.
    Total added latency on repeated 503: 2+4 = 6s — well within any timeout.
    """
    last_exc = None
    waits = [0] + list(delays)   # [no-wait, 2s, 4s]

    for i, wait in enumerate(waits):
        if wait:
            print(f"   ⏳ [{label}] 503 backoff — waiting {wait}s (retry {i}/{len(delays)})...")
            time.sleep(wait)
        try:
            return fn()
        except Exception as e:
            err = str(e)
            if "503" in err or "UNAVAILABLE" in err.upper():
                last_exc = e
                if i < len(delays):
                    print(f"   ⚠️ [{label}] 503 UNAVAILABLE — will retry after backoff")
                    continue
                # All retries exhausted — caller should try next model
                print(f"   ❌ [{label}] 503 persists after {len(delays)} retries — escalating to fallback")
                raise last_exc
            # Not a 503 — re-raise immediately, no delay
            raise

    raise last_exc  # safety net
