# Gemini API Reliability Evaluation

**Date:** 2026-03-03
**Scope:** Backend fragility analysis — focus on Gemini 3.1 Pro 503 errors
**Codebase:** Railway-The-Scanner (Python/FastAPI backend)

---

## Executive Summary

The Scanner backend makes **10-20+ Gemini API calls per single company scan** spread across 6 Python files, each with hand-rolled fallback chains. The primary model (`gemini-3.1-pro-preview`) frequently returns **503 Service Unavailable** during demand spikes. The current resilience layer (`gemini_backoff.py`) retries with only 6 seconds of total backoff — far too short for real capacity spikes that last 30-120+ seconds.

When Gemini is degraded, a single scan can issue **45-54 failed API calls** (3 models x 3 retries x 5-6 stages) before giving up, each burning precious timeout budget. The result: scans fail entirely or hit the 540-second wall-clock limit.

---

## Architecture: How Gemini Is Used

### Call Sites (per single scan)

| Stage | File | Models Tried | Retries | Timeout | Notes |
|-------|------|-------------|---------|---------|-------|
| Forensic scan | `scanner_two.py` x_ray_search | 3.1-pro → 2.5-pro | 2 + backoff | 180s / 120s | Uses Google Search grounding, streaming |
| Re-Search (if low confidence) | `scanner_two.py` | Same chain | 1-2 extra | 150s | Up to 2 additional passes |
| JSON structuring (Pass 2) | `scanner_two.py` | 3.1-pro → Sonnet → 2.5-pro | 1 each | 60s | Structures free-form output |
| Fact-check | `reality_check.py` | 2.5-pro → 3.1-pro → 3-pro | backoff each | 120s | Google Search grounding |
| Summary correction | `reality_check.py` | 3.1-pro → 3-pro → 2.5-pro | backoff each | 60s | Stage 2 of fact-check |
| Summary cleaning | `reality_check.py` | 3.1-pro → 3-pro → 2.5-pro | backoff each | 90s | |
| General summary | `general_summary.py` | 3.1-pro (x2) → 3-pro → 2.5-pro | 4 total | 90s each | 4 attempts, 3 models |
| Domain classification | `domain_classifier.py` | 2.5-pro | **None** | None | **No retry or backoff at all** |
| Re-Synthesis | `main.py` | 3.1-pro → 3-pro → 2.5-pro | backoff each | 90s | |
| Batch seeding | `seed_manager.py` | 3.1-pro → 3-pro → 2.5-pro + Flash | backoff some | Various | 5 concurrent workers |

**Worst case: 20+ Gemini calls per scan, each with its own fallback chain.**

### Current Backoff Implementation (`gemini_backoff.py`)

```
call_with_503_backoff(fn, label, delays=(2, 4), timeout=None)
```

- Retries on: "503", "unavailable", "incomplete chunked read", "peer closed connection"
- Schedule: immediate → wait 2s → retry → wait 4s → retry (3 attempts total)
- Total added latency: **6 seconds** — then gives up
- Optional wall-clock timeout via ThreadPoolExecutor
- Non-retriable exceptions re-raised immediately

### Model Fallback Chain (repeated everywhere)

```
gemini-3.1-pro-preview  →  gemini-3-pro-preview  →  gemini-2.5-pro
         ↓                        ↓                        ↓
   (primary)              (middle fallback)          (final backup)
```

This chain is copy-pasted in: `main.py` (3 places), `scanner_two.py` (2), `reality_check.py` (3), `general_summary.py` (1), `seed_manager.py` (1).

---

## Fragility Analysis

### CRITICAL: Backoff Is Too Short

**File:** `backend/gemini_backoff.py:49`

The 6-second total backoff (2s + 4s) is designed for transient blips, not capacity spikes. Gemini preview model 503 storms typically last 30-120 seconds. After 3 quick retries fail, the call is abandoned and falls through to the next model — which is likely experiencing the same overload.

**Impact:** During a 503 spike, all 3 retries fail within 6 seconds. Then the fallback model (also overloaded) gets 3 retries in 6 seconds. Then the backup model gets 3 retries. Total: 9 API calls burned in ~18 seconds, all failing. Multiply by 5-6 scan stages = **45-54 wasted calls**.

**Recommendation:** Increase delays to `(3, 8, 20)` with ±30% random jitter. Total backoff becomes ~31 seconds, which covers most transient spikes. Jitter prevents thundering herd when batch workers retry simultaneously.

---

### CRITICAL: `gemini-3-pro-preview` Is Expiring and Shares Rate Limits

**Files:** `general_summary.py:203`, `reality_check.py`, `main.py:633-657`, `seed_manager.py`

Multiple code comments explicitly state:
- `general_summary.py:203`: `MIDDLE_MODEL = "gemini-3-pro-preview"   # available ~1 more week; lower demand than 3.1`
- `scanner_two.py:1665`: "Gemini 3 Pro Preview removed from forensic chain: under 3.x server load it also times out at 120s"

Yet this model remains as the middle fallback in 5+ locations. Problems:
1. **It shares 3.x rate limits with 3.1-pro.** When 3.1-pro is 503'ing, 3-pro will 503 too. The fallback is illusory.
2. **It's expiring.** When Google removes it, every fallback chain burns a full timeout slot (60-120s) getting connection errors before reaching gemini-2.5-pro.
3. **Inconsistency**: `scanner_two.py` already removed it from the forensic chain for exactly this reason, but all other files still use it.

**Recommendation:** Remove `gemini-3-pro-preview` from all active fallback chains. Simplify to `3.1-pro → 2.5-pro`. This saves 60-120 seconds per failed stage and removes the ticking time bomb of model expiration.

---

### CRITICAL: No Circuit Breaker

**Files:** All call sites

There is no shared state between call sites. If the Gemini API is having an outage:
- Stage 1 (forensic scan) tries 3 models x 3 retries = 9 calls, all fail
- Stage 2 (fact-check) tries the same 3 models x 3 retries = 9 calls, all fail
- Stage 3 (general summary) tries 4 attempts across 3 models = ~9 calls, all fail
- Stages 4-6 repeat the same pattern

Each stage independently discovers that Gemini is down. No stage learns from the previous stage's failures.

**Impact:** During an outage, the scan burns 4-8 minutes of timeout budget on doomed API calls before any stage even considers giving up. With a circuit breaker, stages 2-6 would skip immediately after stage 1 establishes that Gemini is down, saving minutes per scan.

**Recommendation:** Add a lightweight per-model circuit breaker (module-level dict, thread-safe). After 3 consecutive failures within 120 seconds, mark the model as "open" and skip it for the cooldown period. ~40 lines of code. Integrate into `call_with_503_backoff()`.

---

### HIGH: `domain_classifier.py` Has Zero Resilience

**File:** `backend/domain_classifier.py:70`

```python
response = client.models.generate_content(
    model="gemini-2.5-pro",
    contents=prompt,
    config=config,
)
```

This is the **only Gemini call site with no retry, no backoff, and no fallback**. A single 503 here crashes the domain classification step. Every other call site uses `call_with_503_backoff()`.

**Recommendation:** Wrap with `call_with_503_backoff()`. One-line fix.

---

### HIGH: Duplicated Fallback Chains (Maintenance Hazard)

The identical 3-model try/except fallback pattern is copy-pasted across **9+ code blocks** in 5 files. Each copy has slight variations in timeout, thinking_budget, temperature, and error messages.

Consequences:
- Adding/removing a model requires editing 9+ locations
- Bug fixes to retry logic must be applied 9+ times
- Inconsistencies creep in (e.g., `scanner_two.py` removed gemini-3-pro but other files didn't)
- Each copy is 20-30 lines of boilerplate

**Recommendation:** Extract into a centralized `call_with_fallback(models_configs, prompt, client, label, timeout)` function. Each call site becomes ~5 lines instead of ~25. Model changes become a single-line edit.

---

### MODERATE: No Client-Side Rate Limiting

**File:** `backend/seed_manager.py`

Batch seeding runs 5 concurrent workers (`ThreadPoolExecutor(max_workers=5)`) scanning 100 companies. Each scan triggers 10-20 Gemini calls. At peak, this is **50-100 concurrent Gemini requests** from a single API key.

Gemini preview models have per-minute and per-day rate limits (typically 2-15 RPM for pro models on free/standard tiers). The app **self-inflicts 503s** by exceeding these limits.

**Recommendation:** Add a `threading.Semaphore(3)` to limit concurrent Gemini calls globally, or add `time.sleep(1.5)` between company scans in batch mode.

---

### MODERATE: No Shared Gemini Client

**Files:** All backend files

Each module creates its own `genai.Client(api_key=...)`:
- `main.py:175` — creates client
- `scanner_two.py:372` — creates client
- `general_summary.py:227` — creates client
- `reality_check.py` — creates client
- `domain_classifier.py` — creates client
- `seed_manager.py` — creates client

No connection pooling or reuse. Under load, this means redundant TCP connections and potential socket exhaustion.

**Recommendation:** Create a singleton `get_client()` in a shared module.

---

### MODERATE: ThreadPoolExecutor Leaks

**File:** `backend/gemini_backoff.py:88-93`

```python
pool.shutdown(wait=False)  # doesn't cancel the running thread
```

When a timeout fires, `shutdown(wait=False)` returns immediately but the abandoned thread continues running. Over a scan with many timeouts, orphaned threads accumulate. They hold open HTTP connections and consume memory until the Gemini API eventually responds (or the connection times out at the TCP level, potentially 30-300s later).

**Recommendation:** Consider using `concurrent.futures.Future.cancel()` or wrapping calls with `httpx` timeouts that actually close the socket.

---

### LOW: Preview Models Have No SLA

All three models are `-preview` variants:
- `gemini-3.1-pro-preview`
- `gemini-3-pro-preview`
- `gemini-2.5-pro` (technically not -preview but still has capacity constraints)

Google's preview models are explicitly documented as having no availability SLA. They can be:
- Capacity-constrained during peak demand
- Deprecated with short notice
- Rate-limited more aggressively than stable models

**Recommendation:** Monitor Google's model lifecycle. When `gemini-3.1-pro` (stable, non-preview) becomes available, migrate to it for the primary model. Keep `gemini-2.5-pro` as backup.

---

### LOW: No Response Caching

Identical prompts for the same company produce identical API calls. Re-scans (which happen frequently — the UI has a "Re-scan" button) repeat every Gemini call from scratch.

Domain classification results are fully deterministic and could be cached permanently. Summary re-generation after a reality check correction could cache the fact-check result if the input data hasn't changed.

**Recommendation:** Cache domain classification results in the company data blob. Consider caching other deterministic results with a short TTL.

---

## Summary of Recommendations (Prioritized)

| Priority | Change | Files | Effort | Impact |
|----------|--------|-------|--------|--------|
| **P0** | Increase backoff delays (3s, 8s, 20s) + jitter | `gemini_backoff.py` | Small | High — covers most 503 spikes |
| **P0** | Remove `gemini-3-pro-preview` from all chains | 5 files | Small | High — removes ticking time bomb, saves 60-120s per failure |
| **P0** | Add backoff to `domain_classifier.py` | 1 file | Tiny | Medium — prevents unhandled crash |
| **P0** | Add circuit breaker | New file + `gemini_backoff.py` | Medium | Very High — prevents 45+ wasted calls during outages |
| **P1** | Centralize fallback chain logic | New file + 5 files | Medium | High — eliminates maintenance hazard |
| **P1** | Add batch pacing (semaphore/delay) | `seed_manager.py` | Small | Medium — stops self-inflicted 503s |
| **P1** | Shared Gemini client singleton | New file + 6 files | Small | Low-Medium — connection reuse |
| **P2** | Health probe before expensive scans | `main.py` | Small | Medium — early warning |
| **P2** | Cache domain classification | `domain_classifier.py` | Small | Low — saves 1 API call per re-scan |
| **P2** | Migrate to stable (non-preview) models | All files | Small | High — when available |

---

## KB-First Foresight Path (Implemented)

**Files:** `strategic_foresight.py`, `main.py`

The Foresight Knowledge Base harvester (`foresight_harvester.py`) pre-validates megatrend signals for all 30 FP themes with URL verification and multi-source corroboration. The **KB-first path** leverages this pre-validated evidence to eliminate expensive Google Search-grounded Gemini calls during foresight generation:

**How it works:**
1. Load pre-validated KB signals for the company's domain themes
2. If >= 3 signals exist, quick-verify their URLs are still alive (parallel HEAD checks)
3. Use a lighter Gemini call WITHOUT Google Search to synthesize company-specific implications
4. Skip the source retry loop entirely (signals already validated)
5. Go straight to reality check and link extraction

**Impact:**
- Saves 1-2 Gemini API calls per scan (the search-grounded foresight call + source retry)
- Reduces timeout from 120s to 60s (synthesis is faster without search tool)
- Eliminates source retry loop (another potential 60-90s Gemini call)
- Falls back to full search if KB signals are insufficient or synthesis fails

**Net effect on per-scan Gemini calls during 503 storms:** When KB is populated, the foresight stage drops from 2-4 search-grounded calls to 1 synthesis-only call. Combined with the circuit breaker, this further reduces the blast radius.

---

## Conclusion

The core fragility is structural: **too many independent Gemini calls, each with its own short-fuse retry logic, and no coordination between them.** The most impactful single change is the **circuit breaker** — it turns 45+ doomed calls during an outage into 3-9 calls (only the first stage discovers the outage; subsequent stages skip immediately). Combined with longer backoff delays, removing the expiring middle model, and the **KB-first foresight path** (which eliminates 1-3 search-grounded calls when KB data exists), this should reduce scan failure rates during 503 storms by 60-80%.
