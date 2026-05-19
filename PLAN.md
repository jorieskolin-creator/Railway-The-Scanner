# Implementation Plan: Gemini API Reliability Improvements

## Status Quo — What's Already Done
- Backoff delays upgraded to (3, 8, 20) with jitter
- Circuit breaker (`gemini_circuit.py`) integrated into `call_with_503_backoff`
- `gemini-3-pro-preview` removed from all fallback chains
- `domain_classifier.py` wrapped with `call_with_503_backoff`
- Mistral Large added as independent-provider fallback in synthesis/summary stages
- **KB-first foresight path** — when sufficient pre-validated KB signals exist (>= 3), `generate_foresight()` synthesizes company-specific implications WITHOUT Google Search grounding. Saves 1-2 expensive API calls per scan, reduces 503 exposure, and skips the source retry loop. Falls back to full search when KB is insufficient. (`strategic_foresight.py`, `main.py`)

## What Remains — This Plan

### 1. Shared Gemini Client Singleton (`gemini_client.py`) — NEW FILE
**Risk: Very Low** — pure refactor, no behavior change.

Create `backend/gemini_client.py` with a thread-safe singleton:
```python
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
```

Then replace all 25+ `genai.Client(api_key=...)` calls across 10 files with `get_gemini_client()`. Each file already checks for the key, so the pattern is: remove local client creation, import and call `get_gemini_client()`.

**Files changed:** `main.py`, `scanner_two.py`, `reality_check.py`, `general_summary.py`, `domain_classifier.py`, `seed_manager.py`, `stakeholder_intel.py`, `strategic_foresight.py`, `detailed_report.py`

---

### 2. Centralized Model Fallback (`call_with_model_fallback`) — ADD TO `gemini_backoff.py`
**Risk: Low** — wraps existing `call_with_503_backoff`; each call site becomes simpler.

Add a helper that eliminates the duplicated try/except fallback pattern:
```python
def call_with_model_fallback(
    make_call,      # fn(client, model_name, config) -> response
    model_configs,  # [(model_name, config, timeout), ...]
    client,
    label,
):
    """Try models in order; skip circuit-broken models; return first success."""
    last_exc = None
    for model_name, config, timeout in model_configs:
        if is_open(model_name):
            print(f"   ⚡ [{label}] {model_name} circuit open — skipping")
            continue
        try:
            response = call_with_503_backoff(
                lambda: make_call(client, model_name, config),
                label=f"{label} {model_name}",
                model=model_name,
                timeout=timeout,
            )
            return response, model_name
        except Exception as e:
            last_exc = e
            print(f"   ❌ [{label}] {model_name} failed: {e}")
            continue
    raise last_exc or RuntimeError(f"[{label}] all models exhausted")
```

Refactor the clearest, most-duplicated sites first:
- `reality_check.py` `_clean_with_gemini` (Gemini 3.1 → 2.5 chain)
- `reality_check.py` Stage 1 fact-check (2.5 → 3.1 chain)
- `general_summary.py` (3.1 primary + retry → Mistral fallback)
- `strategic_foresight.py` (3 nearly identical fallback loops)

**Not refactored** (too intertwined with surrounding logic):
- `main.py` synthesis (Opus → Gemini → Mistral with custom ThreadPool per model)
- `scanner_two.py` streaming x_ray_search (uses streaming API with search grounding)

This is conservative — only refactor the straightforward cases.

---

### 3. Domain Classification Caching
**Risk: Very Low** — saves 1 API call per re-scan.

In `domain_classifier.py`, check if the company data already has a `cached_domain` field before calling the API:
```python
def infer_domain(company_name, company_url, reality_results, financial_results, cached_domain=None):
    if cached_domain and cached_domain in KNOWN_DOMAINS and cached_domain != "Other":
        print(f"   [Domain] Using cached domain: '{cached_domain}' for {company_name}")
        return cached_domain
    # ... existing API call ...
```

In `main.py` and `seed_manager.py` (the two callers), pass `cached_domain=data.get("domain")` when calling `infer_domain`. The result is already saved to `data["domain"]` by both callers, so it will be cached on re-scan automatically.

---

### 4. Batch Pacing — Gemini Call Semaphore
**Risk: Low** — only slows down batch mode, never blocks single scans.

Add a module-level semaphore in `gemini_backoff.py`:
```python
import threading
_gemini_semaphore = threading.Semaphore(3)  # max 3 concurrent Gemini calls

def call_with_503_backoff(fn, label="Gemini", delays=(3, 8, 20), timeout=None, model=None, use_semaphore=False):
    if use_semaphore:
        with _gemini_semaphore:
            return _call_with_503_backoff_inner(fn, label, delays, timeout, model)
    return _call_with_503_backoff_inner(fn, label, delays, timeout, model)
```

Only `seed_manager.py` batch workers pass `use_semaphore=True`. Single-scan API calls are unaffected.

---

### 5. Health Probe Before Expensive Scans
**Risk: Very Low** — fail-fast, 3-second check.

Add to `gemini_client.py`:
```python
def gemini_health_probe(timeout=3) -> bool:
    """Quick probe: can we reach Gemini at all? Returns True/False."""
    try:
        client = get_gemini_client()
        client.models.generate_content(
            model="gemini-2.0-flash",
            contents="Say OK",
            config={"max_output_tokens": 5},
        )
        return True
    except:
        return False
```

Called once at scan start in `main.py`. If it fails, the scan can warn but still proceed (the circuit breaker handles failures per-stage anyway). This is informational — not a gate.

---

## Implementation Order (safest-first)

1. **Shared client singleton** — pure infrastructure, zero behavior change
2. **Domain caching** — 3-line change, zero risk
3. **Health probe** — additive, informational only
4. **Centralized fallback** — refactor selected call sites (conservative subset)
5. **Batch semaphore** — only affects `seed_manager.py` batch mode

## What This Plan Does NOT Do (deliberately)
- Does NOT refactor the main.py Opus→Gemini→Mistral synthesis chain (too complex, works fine)
- Does NOT refactor scanner_two.py streaming pipeline (fundamentally different pattern)
- Does NOT change any model names, temperatures, thinking budgets, or prompt text
- Does NOT change any timeout values
- Does NOT modify the circuit breaker logic (already working well)
