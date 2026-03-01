"""
gemini_backoff.py — Retry helper for Gemini API calls.

Handles two classes of transient infrastructure failure:

  503 UNAVAILABLE    — server refuses the request immediately; common during
                       demand spikes on gemini-3.1-pro-preview.

  Connection errors  — server accepts the request, starts streaming, then drops
                       the connection before sending all chunks.  Observed as
                       "peer closed connection without sending complete message
                       body (incomplete chunked read)" after ~5 minutes of
                       apparent progress.  Equally transient; retryable.

Exponential backoff (2s → 4s) gives the spike a moment to pass before
falling through to the next model in the fallback chain.

Optional per-call wall-clock timeout (seconds) wraps fn() in a thread so a
hanging connection doesn't block the entire scan budget.  TimeoutError is
treated as a hard failure and re-raised immediately — the caller should fall
through to the next model rather than retrying a call that already consumed
the full timeout budget.

Usage:
    from gemini_backoff import call_with_503_backoff

    response = call_with_503_backoff(
        lambda: client.models.generate_content(model=..., contents=..., config=...),
        label="General Summary gemini-3.1-pro-preview",
        timeout=90,
    )
"""

import concurrent.futures
import time


def _is_retriable(err_str: str) -> bool:
    """True for transient infrastructure errors that are safe to retry."""
    s = err_str.lower()
    return (
        "503" in s
        or "unavailable" in s
        or "incomplete chunked read" in s
        or "peer closed connection" in s
    )


def call_with_503_backoff(fn, label="Gemini", delays=(2, 4), timeout=None):
    """
    Call fn() with automatic retry on 503 / connection errors.

    fn must be a zero-argument callable that makes a Gemini API request.

    Parameters
    ----------
    fn      : zero-arg callable
    label   : string used in log messages
    delays  : tuple of wait-times in seconds between retries (default: 2s, 4s)
    timeout : optional wall-clock limit per attempt in seconds.
              When set, fn() runs in a thread; if it doesn't complete in time a
              TimeoutError is raised immediately (no retry — caller should
              switch to the next fallback model).

    Retry schedule (retriable errors only):
        attempt 1 — immediate
        attempt 2 — wait delays[0] seconds  (default 2s)
        attempt 3 — wait delays[1] seconds  (default 4s)
        → raise the last exception so the caller's fallback model kicks in

    Non-retriable exceptions and TimeoutError are re-raised immediately.
    Total added latency on repeated retriable errors: sum(delays) = 6s.
    """
    last_exc = None
    waits = [0] + list(delays)   # [no-wait, 2s, 4s]

    for i, wait in enumerate(waits):
        if wait:
            print(f"   ⏳ [{label}] backoff — waiting {wait}s (retry {i}/{len(delays)})...")
            time.sleep(wait)
        try:
            if timeout is not None:
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(fn)
                try:
                    return future.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    pool.shutdown(wait=False)
                    raise TimeoutError(
                        f"[{label}] timed out after {timeout}s — switching to next model"
                    )
                finally:
                    pool.shutdown(wait=False)
            else:
                return fn()

        except (TimeoutError, concurrent.futures.TimeoutError) as te:
            # Hard failure — don't burn retries on a call that already used
            # the full timeout budget; let the caller try the next model.
            print(f"   ⏰ [{label}] {te}")
            raise

        except Exception as e:
            if _is_retriable(str(e)):
                last_exc = e
                if i < len(delays):
                    kind = "503 UNAVAILABLE" if ("503" in str(e) or "unavailable" in str(e).lower()) else "connection error"
                    print(f"   ⚠️ [{label}] {kind} — will retry after backoff")
                    continue
                print(f"   ❌ [{label}] error persists after {len(delays)} retries — escalating to fallback")
                raise last_exc
            # Non-retriable — re-raise immediately, no delay
            raise

    raise last_exc  # safety net
