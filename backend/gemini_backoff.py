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

Exponential backoff (3s → 8s → 20s) with ±30% jitter gives overload spikes
(typically 30-120s) time to pass before falling through to the next model in
the fallback chain.  Jitter prevents thundering herd when batch workers retry
simultaneously.

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
import random
import time

from gemini_circuit import is_open, record_failure, record_success


def _is_retriable(err_str: str) -> bool:
    """True for transient infrastructure errors that are safe to retry."""
    s = err_str.lower()
    return (
        "503" in s
        or "unavailable" in s
        or "incomplete chunked read" in s
        or "peer closed connection" in s
    )


def call_with_503_backoff(fn, label="Gemini", delays=(3, 8, 20), timeout=None, model=None):
    """
    Call fn() with automatic retry on 503 / connection errors.

    fn must be a zero-argument callable that makes a Gemini API request.

    Parameters
    ----------
    fn      : zero-arg callable
    label   : string used in log messages
    delays  : tuple of wait-times in seconds between retries (default: 3s, 8s, 20s)
    timeout : optional wall-clock limit per attempt in seconds.
              When set, fn() runs in a thread; if it doesn't complete in time a
              TimeoutError is raised immediately (no retry — caller should
              switch to the next fallback model).
    model   : optional model name for circuit breaker integration.
              When set, the circuit breaker is checked before calling and
              updated after success/failure.

    Retry schedule (retriable errors only):
        attempt 1 — immediate
        attempt 2 — wait ~3s   (2.1-3.9s with jitter)
        attempt 3 — wait ~8s   (5.6-10.4s with jitter)
        attempt 4 — wait ~20s  (14-26s with jitter)
        → raise the last exception so the caller's fallback model kicks in

    Non-retriable exceptions and TimeoutError are re-raised immediately.
    Total added latency on repeated retriable errors: ~31s (with jitter variance).
    """
    # Circuit breaker: skip immediately if model is in open state
    if model and is_open(model):
        raise RuntimeError(f"[{label}] circuit open for {model} — skipping")

    last_exc = None
    waits = [0] + list(delays)   # [no-wait, 3s, 8s, 20s]

    for i, wait in enumerate(waits):
        if wait:
            jittered = wait * (0.7 + random.random() * 0.6)  # ±30% jitter
            print(f"   ⏳ [{label}] backoff — waiting {jittered:.1f}s (retry {i}/{len(delays)})...")
            time.sleep(jittered)
        try:
            if timeout is not None:
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(fn)
                try:
                    result = future.result(timeout=timeout)
                    if model:
                        record_success(model)
                    return result
                except concurrent.futures.TimeoutError:
                    pool.shutdown(wait=False)
                    if model:
                        record_failure(model)
                    raise TimeoutError(
                        f"[{label}] timed out after {timeout}s — switching to next model"
                    )
                finally:
                    pool.shutdown(wait=False)
            else:
                result = fn()
                if model:
                    record_success(model)
                return result

        except (TimeoutError, concurrent.futures.TimeoutError) as te:
            # Hard failure — don't burn retries on a call that already used
            # the full timeout budget; let the caller try the next model.
            print(f"   ⏰ [{label}] {te}")
            raise

        except Exception as e:
            err_str = str(e)
            if _is_retriable(err_str):
                last_exc = e
                if i < len(delays):
                    kind = "503 UNAVAILABLE" if ("503" in err_str or "unavailable" in err_str.lower()) else "connection error"
                    print(f"   ⚠️ [{label}] {kind} — will retry after backoff")
                    continue
                if model:
                    record_failure(model)
                print(f"   ❌ [{label}] error persists after {len(delays)} retries — escalating to fallback")
                raise last_exc
            # Non-retriable — re-raise immediately, no delay
            raise

    raise last_exc  # safety net
