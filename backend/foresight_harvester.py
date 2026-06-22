"""
foresight_harvester.py — Periodic harvester for the Foresight Knowledge Base.

Scans authoritative sources (FP_THEME_SOURCES) for megatrend signals,
validates them (URL verification + credibility assessment), and stores
validated findings in blob storage. These pre-validated signals are then
injected into per-company foresight generation, avoiding redundant searches.

Refresh-and-clean strategy:
  1. Load existing KB signals for the theme
  2. Re-verify existing URLs — mark dead/unreadable records, do not wipe themes
  3. Harvest with GPT discovery, Gemini fallback, and Sonnet JSON repair
  4. Deduplicate & merge — add new storable signals not already in the KB
  5. Cap at MAX_SIGNALS_PER_THEME, ranked by confidence + recency
  6. Store the merged result

This ensures the KB grows over time and a failed harvest never wipes good data.
"""

import json
import hashlib
import re
import threading
import time
import concurrent.futures
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from llm_clients import call_anthropic_text, call_openai_text
from gemini_client import GEMINI_HARVEST_MODEL, call_gemini_harvest_text, gemini_api_key
from source_quality import (
    HIGH_CREDIBILITY_DOMAINS,
    assess_source_credibility,
)
from foresight_guardrails import build_foresight_guardrails, annotate_foresight_signal, sanitize_foresight_record, sanitize_foresight_text
from source_claim_validator import CLEAN_EVIDENCE_MODEL_CHAIN, _check_relevance
from strategic_foresight import (
    FP_THEMES_30, FP_THEME_SOURCES, THEME_FALLBACK_MAP,
    _verify_foresight_url, _extract_json_from_text,
    build_strategic_foresight_theme_registry,
    FORESIGHT_SOURCE_QUALITY_TIERS,
)
from storage import BlobStorage
from evidence_contracts import normalize_central_foresight_signal

MAX_SIGNALS_PER_THEME = 15
FORESIGHT_KB_EVIDENCE_VERSION = "foresight-active-v3"
FORESIGHT_CONTEXT_MIN_SCORE = 45
FORESIGHT_CONTEXT_PARTIAL_MIN_SCORE = 35
FORESIGHT_CONTEXT_SUPPORT_STATUSES = {"supported", "partial"}
ACTIVE_FORESIGHT_STATUS = "active_context"
AUDIT_FORESIGHT_STATUS = "rejected_audit"
MIN_SEMANTIC_SNIPPET_WORDS = 35
TIER1_EXCEPTION_MIN_SEMANTIC_SNIPPET_WORDS = 55
FRESHNESS_FRESH_MONTHS = 12
FRESHNESS_CURRENT_MONTHS = 24
FRESHNESS_STALE_MONTHS = 36
FRESHNESS_ACTIVE_STATUSES = {"fresh", "current", "evergreen_context"}
EVERGREEN_SOURCE_TYPES = {
    "regulation",
    "regulatory_context",
    "official_dashboard",
    "statistics",
    "standard",
    "framework",
    "policy_framework",
    "government",
}
HARVEST_GPT_NATIVE_TIMEOUT_SEC = 115
HARVEST_GPT_OUTER_TIMEOUT_SEC = 125
HARVEST_GEMINI_NATIVE_TIMEOUT_SEC = 90
HARVEST_GEMINI_OUTER_TIMEOUT_SEC = 100
HARVEST_SONNET_REPAIR_NATIVE_TIMEOUT_SEC = 55
HARVEST_SONNET_REPAIR_OUTER_TIMEOUT_SEC = 60
HARVEST_TIMEOUT_COOLDOWN_SEC = 5
HARVEST_THEME_WORKERS = 4
HARVEST_PROVIDER_LIMITS = {"openai": 2, "google": 3, "anthropic": 2}
HARVEST_DEFAULT_QUERY_LIMIT = 8
HARVEST_THEME_QUERY_LIMIT = {
    "Data & Digitalisation": 6,
}
HARVEST_BATCH_SIZE_DEFAULT = 4
HARVEST_BATCH_SIZE_HARD = 3
HARVEST_MIN_ACTIVE_TARGET = 3
HARVEST_MAX_NEW_ACTIVE_TARGET = 5
HARVEST_HARD_THEMES = {
    "Communication & Media",
    "Construction & Urbanisation",
    "Data & Digitalisation",
    "Health & Wellbeing",
    "Leisure & Lifestyles",
    "Sciences & Research",
    "Society",
    "Sustainability & Recycling",
}
HARVEST_FRAGILE_THEMES = {
    "Digital & Sharing Economies",
    "Industry & Manufacturing",
    "Nature",
    "Food",
    "Water & Oceans",
}
HARVEST_NORMAL_THEME_BUDGET_SEC = 180
HARVEST_DIFFICULT_THEME_BUDGET_SEC = 300
HARVEST_THEME_QUERY_PRIORITY = {
    "Data & Digitalisation": [
        "site:oecd.org",
        "site:ec.europa.eu",
        "site:weforum.org",
        "site:worldbank.org",
        "site:sitra.fi",
        "site:theodi.org",
        "site:turing.ac.uk",
    ],
}
SOURCE_DOMAIN_PROFILES = {
    "oecd.org": "static_html",
    "ec.europa.eu": "date_hard",
    "worldbank.org": "static_html",
    "weforum.org": "static_html",
    "sitra.fi": "static_html",
    "turing.ac.uk": "static_html",
    "nature.com": "pdf_heavy",
    "science.org": "blocked_risk",
    "thelancet.com": "blocked_risk",
    "mckinsey.com": "dynamic",
    "deloitte.com": "dynamic",
    "gartner.com": "blocked_risk",
    "forrester.com": "blocked_risk",
    "hbr.org": "blocked_risk",
}


class _HarvestRunContext:
    """Shared provider health and concurrency limits for one harvest run."""

    def __init__(self):
        self._lock = threading.Lock()
        self._quarantined: dict[str, str] = {}
        self._semaphores = {
            provider: threading.BoundedSemaphore(limit)
            for provider, limit in HARVEST_PROVIDER_LIMITS.items()
        }

    def quarantine(self, provider: str, reason: str) -> None:
        with self._lock:
            if provider not in self._quarantined:
                self._quarantined[provider] = reason
                print(f"   ⚠️ [Harvest Provider] {provider} quarantined for this run: {reason}")

    def quarantine_reason(self, provider: str) -> str | None:
        with self._lock:
            return self._quarantined.get(provider)

    def quarantined_snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._quarantined)

    def call(self, provider: str, label: str, timeout_sec: int, fn) -> dict:
        reason = self.quarantine_reason(provider)
        if reason:
            return {
                "value": None,
                "status": "provider_quarantined",
                "error": reason,
                "elapsed_ms": 0,
                "lingering": False,
            }
        semaphore = self._semaphores[provider]
        acquired = semaphore.acquire(timeout=max(1, min(15, timeout_sec)))
        if not acquired:
            return {
                "value": None,
                "status": "provider_queue_timeout",
                "elapsed_ms": timeout_sec * 1000,
                "lingering": False,
            }
        try:
            result = _call_harvest_model_with_deadline(label, timeout_sec, fn)
            if result.get("lingering"):
                self.quarantine(provider, f"{label} exceeded its outer deadline")
            return result
        finally:
            semaphore.release()


def _theme_to_slug(theme: str) -> str:
    """Convert a theme name to a storage-safe slug."""
    slug = theme.lower()
    slug = slug.replace(" & ", "_and_").replace("&", "and")
    slug = re.sub(r'[^a-z0-9_]', '_', slug)
    slug = re.sub(r'_{2,}', '_', slug)
    return slug.strip('_')


_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def _canonical_source_url(value: str) -> str:
    """Canonicalize one URL without treating its whole domain as already seen."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if not host:
            return ""
        port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
        path = re.sub(r"/{2,}", "/", parsed.path or "/").rstrip("/") or "/"
        query = [
            (key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
        ]
        return urlunparse(("https", f"{host}{port}", path, "", urlencode(sorted(query)), ""))
    except Exception:
        return raw.lower().rstrip("/")


def _signal_content_hash(sig: dict) -> str:
    text = " ".join(str(sig.get(field) or "") for field in (
        "semantic_snippet", "source_quote", "description", "title",
    ))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if len(text) >= 80 else ""


def _build_theme_source_memory(active: list[dict], audit: list[dict]) -> dict:
    return {
        "active_urls": {u for s in active if (u := _canonical_source_url(s.get("source_url") or s.get("url")))},
        "audit_urls": {u for s in audit if (u := _canonical_source_url(s.get("source_url") or s.get("url")))},
        "content_hashes": {h for s in [*active, *audit] if (h := _signal_content_hash(s))},
    }


def _filter_known_harvest_signals(signals: list[dict], memory: dict) -> tuple[list[dict], dict]:
    """Suppress exact/duplicate material while allowing new URLs on known domains."""
    kept = []
    stats = {"known_active_url": 0, "known_audit_url": 0, "duplicate_content": 0}
    seen_hashes = set(memory.get("content_hashes") or set())
    for signal in signals or []:
        canonical = _canonical_source_url(signal.get("source_url") or signal.get("url"))
        content_hash = _signal_content_hash(signal)
        if canonical and canonical in (memory.get("active_urls") or set()):
            stats["known_active_url"] += 1
            continue
        if canonical and canonical in (memory.get("audit_urls") or set()):
            stats["known_audit_url"] += 1
            continue
        if content_hash and content_hash in seen_hashes:
            stats["duplicate_content"] += 1
            continue
        if canonical:
            signal["canonical_source_url"] = canonical
        if content_hash:
            signal["content_hash"] = content_hash
            seen_hashes.add(content_hash)
        kept.append(signal)
    return kept, stats


def _signal_key(sig: dict) -> str:
    """
    Generate a deduplication key for a signal.
    Matches on source_url (exact) or title similarity (normalized).
    """
    url = _canonical_source_url(sig.get("canonical_source_url") or sig.get("source_url") or "")
    if url:
        return url
    # Fallback: normalized title
    title = (sig.get("title") or "").strip().lower()
    title = re.sub(r'[^a-z0-9 ]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return f"title:{title}"


def _call_harvest_model_with_deadline(label: str, timeout_sec: int, fn):
    """Run a model call behind a final wall-clock guard.

    Provider-native timeouts are intentionally shorter than this guard. That
    makes outer timeout and a potentially lingering worker exceptional rather
    than the normal fallback path.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    started = time.monotonic()
    try:
        return {
            "value": future.result(timeout=timeout_sec),
            "status": "ok",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "lingering": False,
        }
    except concurrent.futures.TimeoutError:
        future.cancel()
        print(f"   ⏱️ [Harvest] {label}: hard deadline exceeded after {timeout_sec}s")
        time.sleep(HARVEST_TIMEOUT_COOLDOWN_SEC)
        return {
            "value": None,
            "status": "provider_timeout",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "lingering": not future.done(),
        }
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {str(exc)[:240]}"
        timed_out = "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
        if timed_out:
            time.sleep(HARVEST_TIMEOUT_COOLDOWN_SEC)
        return {
            "value": None,
            "status": "provider_timeout" if timed_out else "provider_error",
            "error": error_text,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "lingering": False,
        }
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _revalidate_existing_signals(existing_signals: list[dict], theme: str) -> list[dict]:
    """
    Re-verify URLs on existing KB signals.

    Active KB repair is metadata-first: dead or unreadable records are marked
    and later moved to audit storage rather than silently removed here.
    """
    if not existing_signals:
        return []

    # Collect all primary URLs for parallel verification
    urls_to_check = []
    for sig in existing_signals:
        url = sig.get("source_url", "")
        if url:
            urls_to_check.append(url)

    if not urls_to_check:
        return existing_signals

    url_status = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_verify_foresight_url, url, 8): url
            for url in set(urls_to_check)
        }
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                url_status[url] = future.result()
            except Exception:
                url_status[url] = False

    checked = []
    dead = 0
    for sig in existing_signals:
        primary_url = sig.get("source_url", "")
        alive = bool(url_status.get(primary_url, False)) if primary_url else False
        sig["url_verified"] = alive
        sig["verified"] = alive
        if not alive:
            dead += 1
            sig["source_audit_status"] = "source_dead" if primary_url else "missing_source_url"
        checked.append(sig)

    print(f"   🧹 [Clean] {theme}: {len(checked) - dead} existing alive, {dead} dead/unreadable → audited")
    return checked


def _signal_claim_text(sig: dict) -> str:
    """Return the text that a Foresight KB source must actually support."""
    return str(
        sig.get("source_says")
        or sig.get("description")
        or sig.get("summary")
        or sig.get("snippet")
        or sig.get("title")
        or ""
    ).strip()


def _prepare_signal_for_promotion(sig: dict, theme: str) -> dict:
    """Preserve legacy interpretation and normalize fields for source gating."""
    if not isinstance(sig, dict):
        return sig

    claim = _signal_claim_text(sig)
    if claim and not sig.get("legacy_snippet"):
        sig["legacy_snippet"] = claim
    if claim and not sig.get("snippet"):
        # Company evidence uses `snippet` as the claim text.  For KB records the
        # original meaning remains interpretation/context, not a verified quote.
        sig["snippet"] = claim

    url = (sig.get("source_url") or sig.get("url") or "").strip()
    if url and not sig.get("url"):
        sig["url"] = url
    if sig.get("url_verified") is True and sig.get("verified") is not True:
        sig["verified"] = True
    if sig.get("url_verified") is False:
        sig["verified"] = False

    sig.setdefault("source_type", "foresight")
    sig["fp_theme"] = sig.get("fp_theme") or theme
    sig["claim_type"] = "foresight_context"
    sig["company_specific"] = False
    sig["not_evidence_of_deployment"] = True
    sig["requires_company_specific_corroboration"] = True
    sig["modal_language_required"] = True
    sig["excluded_from_company_fact_generation"] = True
    sig["generation_scope"] = "foresight_synthesis_only"
    return sig


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _foresight_source_credibility(sig: dict) -> str:
    cred = str(sig.get("source_credibility") or "").lower().strip()
    if cred not in {"high", "medium", "low"}:
        url = (sig.get("source_url") or sig.get("url") or "").strip()
        cred = assess_source_credibility(url) if url else "low"
        sig["source_credibility"] = cred
    return cred


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(text or "")))


def _tier_domains(tier_name: str) -> set[str]:
    spec = FORESIGHT_SOURCE_QUALITY_TIERS.get(tier_name) or {}
    domains = spec.get("domains") or set()
    return {str(domain).lower().removeprefix("www.") for domain in domains if domain}


def _is_tier1_domain(domain: str) -> bool:
    domain = (domain or "").lower().removeprefix("www.")
    if not domain:
        return False
    return any(domain == d or domain.endswith(f".{d}") for d in _tier_domains("tier_1"))


def _is_tier1_source(sig: dict) -> bool:
    domain = sig.get("source_domain") or _source_domain(sig.get("source_url") or sig.get("url") or "")
    return _is_tier1_domain(domain)


def _source_tier_for_signal(sig: dict, theme: str) -> str:
    """Return a stable source-tier tag without overwriting legacy tags."""
    existing = str(sig.get("source_tier") or "").strip().lower()
    if existing:
        return existing.replace("_", "")
    domain = sig.get("source_domain") or _source_domain(sig.get("source_url") or sig.get("url") or "")
    if _is_tier1_domain(domain):
        return "tier1"
    cred = _foresight_source_credibility(sig)
    curated = _is_curated_theme_source(sig, theme)
    source_type = str(sig.get("source_type") or "").lower()
    if curated and cred == "high":
        return "tier1"
    if curated or cred == "high":
        return "tier2"
    if cred == "medium" or source_type in {"academic", "think_tank", "government", "industry_analyst"}:
        return "tier3"
    return "tier4"


def _semantic_snippet_for_signal(sig: dict) -> str:
    snippet = str(sig.get("semantic_snippet") or "").strip()
    if snippet:
        return snippet
    parts = [
        sig.get("description"),
        sig.get("source_says"),
        sig.get("summary"),
        sig.get("source_quote"),
        sig.get("snippet"),
    ]
    snippet = " ".join(str(p).strip() for p in parts if str(p or "").strip())
    snippet = re.sub(r"\s+", " ", snippet).strip()
    return snippet[:900]


def _parse_source_date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        year = int(value)
        if 1900 <= year <= 2100:
            return datetime(year, 1, 1, tzinfo=timezone.utc)
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        if 1900 <= year <= 2100:
            return datetime(year, 1, 1, tzinfo=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text[:10], pattern)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _source_publication_date(sig: dict) -> datetime | None:
    for key in (
        "published_at",
        "publication_date",
        "published_date",
        "source_date",
        "date",
        "published",
        "last_updated_at",
        "updated_at",
    ):
        parsed = _parse_source_date(sig.get(key))
        if parsed:
            return parsed
    return _parse_source_date(sig.get("published_year"))


def _months_since(dt: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return max(0, (now.year - dt.year) * 12 + (now.month - dt.month) - (1 if now.day < dt.day else 0))


def _is_evergreen_signal(sig: dict) -> bool:
    if sig.get("evergreen_context") is True:
        return True
    source_type = str(sig.get("source_type") or "").lower().strip()
    title = str(sig.get("title") or "").lower()
    description = str(sig.get("description") or sig.get("semantic_snippet") or "").lower()
    evergreen_terms = (
        "framework",
        "regulation",
        "directive",
        "standard",
        "dashboard",
        "statistics",
        "data portal",
        "working plan",
        "taxonomy",
    )
    tier = str(sig.get("source_tier") or "").lower().replace("_", "")
    official_tier = tier in {"tier1", "tier2"}
    return bool(
        official_tier
        and (
            source_type in EVERGREEN_SOURCE_TYPES
            or any(term in title or term in description for term in evergreen_terms)
        )
    )


def _normalize_freshness(sig: dict) -> dict:
    sig = dict(sig)
    now = datetime.now(timezone.utc)
    published = _source_publication_date(sig)
    sig["freshness_checked_at"] = now.isoformat()
    if published:
        age_months = _months_since(published, now)
        sig["published_at"] = published.date().isoformat()
        sig["published_year"] = published.year
        sig["source_age_months"] = age_months
        if age_months <= FRESHNESS_FRESH_MONTHS:
            status = "fresh"
        elif age_months <= FRESHNESS_CURRENT_MONTHS:
            status = "current"
        elif _is_evergreen_signal(sig):
            status = "evergreen_context"
        elif age_months <= FRESHNESS_STALE_MONTHS:
            status = "aging"
        else:
            status = "stale"
        sig["freshness_status"] = status
        sig["freshness_reason"] = (
            f"Source date {sig['published_at']} is {age_months} month(s) old."
            if status != "evergreen_context"
            else f"Source date {sig['published_at']} is older than the active window, but the source is official evergreen context."
        )
        return sig

    sig["source_age_months"] = None
    if _is_evergreen_signal(sig):
        sig["freshness_status"] = "evergreen_context"
        sig["freshness_reason"] = "No publication date found, but source type/tier indicate official evergreen context."
    else:
        sig["freshness_status"] = "unknown_date"
        sig["freshness_reason"] = "No publication or update date was available."
    return sig


def _normalize_foresight_tags(sig: dict, theme: str) -> dict:
    """Normalize central-KB tags while preserving original harvesting tags."""
    sig = dict(sig)
    tags = list(sig.get("tags") or [])
    if tags and not sig.get("original_tags"):
        sig["original_tags"] = tags
    source_url = (sig.get("source_url") or sig.get("url") or "").strip()
    if source_url:
        sig["source_url"] = source_url
        sig["url"] = source_url
        sig["source_domain"] = sig.get("source_domain") or _source_domain(source_url)
    sig["theme"] = sig.get("theme") or theme
    sig["fp_theme"] = sig.get("fp_theme") or theme
    sig["source_tier"] = _source_tier_for_signal(sig, theme)
    sig["source_credibility"] = _foresight_source_credibility(sig)
    sig["time_horizon"] = sig.get("time_horizon") or sig.get("timeframe") or _tag_value(tags, {"6-12mo", "12-24mo", "24-36mo"})
    sig["s_curve_phase"] = sig.get("s_curve_phase") or sig.get("lifecycle_stage") or _tag_value(tags, {
        "weak_signal", "strong_signal", "hype_cycle", "valley_of_death", "go_to_market",
        "market_breakout", "established_trend", "market_saturation", "weakening_trend", "fading_signal",
    })
    sig["signal_direction"] = sig.get("signal_direction") or sig.get("trend_momentum") or _tag_value(tags, {
        "accelerating", "stable", "decelerating", "declining", "emerging",
    })
    sig["semantic_snippet"] = _semantic_snippet_for_signal(sig)
    return _normalize_freshness(sig)


def _tag_value(tags: list, allowed: set[str]) -> str:
    for tag in tags:
        normalized = str(tag).strip().lower()
        if normalized in allowed:
            return normalized
    return ""


def _strip_company_evidence_fields(sig: dict) -> dict:
    """Remove normal company-evidence vocabulary from central KB active records."""
    cleaned = dict(sig)
    for field in _CENTRAL_KB_COMPANY_EVIDENCE_FIELDS:
        cleaned.pop(field, None)
    for field in (
        "promotion_status",
        "source_registry_status",
        "claim_support_status",
        "allowed_as_fact",
        "allowed_as_hypothesis",
        "allowed_as_discovery_question",
        "internal_only",
        "blocked",
        "buying_evidence_eligible",
        "entity_match_status",
        "content_entity_match_status",
        "entity_scope",
        "wrong_entity",
        "wrong_entity_true",
        "target_entity_verified",
        "company_name",
        "company_slug",
        "source_database",
        "scanner_origin",
    ):
        cleaned.pop(field, None)
    return cleaned


def _active_signal_score(sig: dict) -> int:
    tier = str(sig.get("source_tier") or "").lower().replace("_", "")
    tier_score = {"tier1": 100, "tier2": 80, "tier3": 60, "tier4": 35}.get(tier, 45)
    cred_score = {"high": 30, "medium": 18, "low": 4}.get(str(sig.get("source_credibility") or "").lower(), 4)
    snippet_score = min(_word_count(sig.get("semantic_snippet") or ""), 120) // 4
    theme_score = 20 if sig.get("theme_match") is True else 0
    trend_score = 20 if sig.get("trend_supported") is True else 0
    registry_score = {
        "explicit_theme_domain": 20,
        "tier1_theme_exception": 12,
    }.get(str(sig.get("source_registry_match") or ""), 0)
    freshness_score = {
        "fresh": 25,
        "current": 18,
        "evergreen_context": 10,
        "aging": -10,
        "unknown_date": -20,
        "stale": -50,
    }.get(str(sig.get("freshness_status") or ""), -10)
    scan_value = _safe_int(sig.get("scan_enrichment_value") or sig.get("scan_enrichment_score"), 0)
    return tier_score + cred_score + snippet_score + theme_score + trend_score + registry_score + freshness_score + scan_value


def _classify_for_active_kb(sig: dict, theme: str, *, stage: str = "unknown") -> tuple[dict, bool]:
    """Return (normalized record, active?) for the central Foresight KB contract."""
    sig = _prepare_signal_for_promotion(dict(sig), theme)
    sig = _normalize_foresight_tags(sig, theme)
    sig = _strip_company_evidence_fields(sig)
    sig, _removed_contract_fields = normalize_central_foresight_signal(sig)

    source_url = (sig.get("source_url") or sig.get("url") or "").strip()
    source_domain = sig.get("source_domain") or _source_domain(source_url)
    sig["source_domain"] = source_domain
    sig["source_domain_profile"] = _source_domain_profile(source_domain)
    curated = _is_curated_theme_source(sig, theme)
    tier1_exception = (
        not curated
        and _is_tier1_source(sig)
        and sig.get("theme_match") is True
        and sig.get("trend_supported") is True
        and sig.get("company_specific") is not True
    )
    if curated:
        sig["source_registry_match"] = "explicit_theme_domain"
    elif tier1_exception:
        sig["source_registry_match"] = "tier1_theme_exception"
    else:
        sig["source_registry_match"] = "not_in_theme_registry"
    cred = _foresight_source_credibility(sig)
    url_verified = sig.get("url_verified") is True or sig.get("verified") is True
    source_readable = sig.get("source_readable") is True or sig.get("content_verified") is True or bool(sig.get("source_quote")) or bool(sig.get("semantic_snippet"))
    snippet_words = _word_count(sig.get("semantic_snippet") or "")
    min_snippet_words = TIER1_EXCEPTION_MIN_SEMANTIC_SNIPPET_WORDS if tier1_exception else MIN_SEMANTIC_SNIPPET_WORDS
    freshness_status = str(sig.get("freshness_status") or "").strip().lower()
    explicit_theme_mismatch = sig.get("theme_match") is False
    explicit_trend_mismatch = sig.get("trend_supported") is False or str(sig.get("trend_support_status") or "").lower() in {"unsupported", "contradicted"}

    reason = ""
    status = ""
    if not source_url:
        status = "missing_source_url"
        reason = "No primary source URL was available."
    elif not url_verified:
        status = "source_dead"
        reason = "Primary source URL could not be verified as live."
    elif not (curated or tier1_exception):
        status = "non_curated_source"
        reason = "Source is outside the curated Strategic Foresight Theme registry domains and is not a Tier 1 theme exception."
    elif cred not in {"high", "medium"}:
        status = "low_credibility"
        reason = "Source credibility is too low for active macro foresight context."
    elif not source_readable:
        status = "source_unreadable"
        reason = "Source was reachable but no usable text or quote was available."
    elif snippet_words < min_snippet_words:
        status = "snippet_too_short"
        reason = "Semantic snippet is too short for reliable scan-time source selection."
    elif freshness_status not in FRESHNESS_ACTIVE_STATUSES:
        status = f"{freshness_status or 'unknown_date'}_source"
        reason = sig.get("freshness_reason") or "Source freshness did not meet the active Foresight KB window."
    elif explicit_theme_mismatch:
        status = "off_theme"
        reason = "The source did not match the requested foresight theme."
    elif explicit_trend_mismatch:
        status = "trend_not_supported"
        reason = "The source did not support the stored trend direction."

    sig["claim_type"] = "foresight_context"
    sig["company_specific"] = False
    sig["not_evidence_of_deployment"] = True
    sig["requires_company_specific_corroboration"] = True
    sig["modal_language_required"] = True
    sig["central_kb_contract"] = "active_foresight_context"
    sig["foresight_evidence_version"] = FORESIGHT_KB_EVIDENCE_VERSION
    sig["harvest_stage"] = stage

    if status:
        sig["foresight_context_eligible"] = False
        sig["foresight_signal_status"] = AUDIT_FORESIGHT_STATUS
        sig["source_audit_status"] = status
        sig["verification_reason"] = reason
        sig["source_readable"] = bool(source_readable)
        sig["theme_match"] = False if explicit_theme_mismatch else sig.get("theme_match")
        sig["trend_supported"] = False if explicit_trend_mismatch else sig.get("trend_supported")
        return sig, False

    sig["foresight_context_eligible"] = True
    sig["foresight_signal_status"] = ACTIVE_FORESIGHT_STATUS
    sig["source_audit_status"] = "accepted_active"
    sig["source_readable"] = True
    sig["content_verified"] = True
    sig["theme_match"] = True
    sig["trend_supported"] = True
    sig["trend_support_status"] = "supported"
    sig["verification_reason"] = "accepted_active_foresight_context"
    sig["active_signal_score"] = _active_signal_score(sig)
    return sig, True


def _split_active_and_audit(signals: list[dict], theme: str, *, stage: str) -> tuple[list[dict], list[dict], dict]:
    active: list[dict] = []
    audit: list[dict] = []
    stats = {
        "accepted_active": 0,
        "rejected_audit": 0,
        "dead": 0,
        "unreadable": 0,
        "off_theme": 0,
        "not_curated": 0,
        "low_credibility": 0,
        "snippet_too_short": 0,
        "tier1_theme_exception": 0,
        "freshness_rejected": 0,
        "unknown_date": 0,
        "stale": 0,
        "aging": 0,
        "source_domain_profiles": {},
    }
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        normalized, is_active = _classify_for_active_kb(sig, theme, stage=stage)
        profile = normalized.get("source_domain_profile") or "unknown"
        stats["source_domain_profiles"][profile] = stats["source_domain_profiles"].get(profile, 0) + 1
        if is_active:
            active.append(normalized)
            stats["accepted_active"] += 1
            if normalized.get("source_registry_match") == "tier1_theme_exception":
                stats["tier1_theme_exception"] += 1
        else:
            audit.append(normalized)
            stats["rejected_audit"] += 1
            audit_status = normalized.get("source_audit_status")
            if audit_status == "source_dead":
                stats["dead"] += 1
            elif audit_status == "source_unreadable":
                stats["unreadable"] += 1
            elif audit_status == "off_theme":
                stats["off_theme"] += 1
            elif audit_status == "non_curated_source":
                stats["not_curated"] += 1
            elif audit_status == "low_credibility":
                stats["low_credibility"] += 1
            elif audit_status == "snippet_too_short":
                stats["snippet_too_short"] += 1
            elif audit_status in {"aging_source", "stale_source", "unknown_date_source"}:
                stats["freshness_rejected"] += 1
                if audit_status == "unknown_date_source":
                    stats["unknown_date"] += 1
                elif audit_status == "stale_source":
                    stats["stale"] += 1
                elif audit_status == "aging_source":
                    stats["aging"] += 1
    return active, audit, stats


def _merge_harvest_stats(*stats_objects: dict) -> dict:
    merged: dict = {}
    for stats in stats_objects:
        if not isinstance(stats, dict):
            continue
        for key, value in stats.items():
            if isinstance(value, (int, float)):
                merged[key] = merged.get(key, 0) + value
            elif isinstance(value, dict):
                current = merged.setdefault(key, {})
                for inner_key, inner_value in value.items():
                    if isinstance(inner_value, (int, float)):
                        current[inner_key] = current.get(inner_key, 0) + inner_value
                    elif inner_key not in current:
                        current[inner_key] = inner_value
            elif key not in merged:
                merged[key] = value
    return merged


def _harvest_diversity_status(signals: list[dict]) -> dict:
    """Measure whether a refresh is broad enough to stop after three signals."""
    unique: dict[str, dict] = {}
    for signal in signals or []:
        if not isinstance(signal, dict):
            continue
        unique[_signal_key(signal)] = signal
    values = list(unique.values())
    domains = [
        str(sig.get("source_domain") or _source_domain(sig.get("source_url") or "")).lower()
        for sig in values
    ]
    domains = [domain for domain in domains if domain]
    source_types = {str(sig.get("source_type") or "").lower() for sig in values if sig.get("source_type")}
    tiers = {str(sig.get("source_tier") or "").lower() for sig in values if sig.get("source_tier")}
    fresh_count = sum(
        1 for sig in values
        if str(sig.get("freshness_status") or "").lower() in {"fresh", "current", "evergreen_context"}
    )
    domain_counts = {domain: domains.count(domain) for domain in set(domains)}
    max_domain_share = (max(domain_counts.values()) / len(values)) if values and domain_counts else 1.0
    diverse = bool(
        len(values) >= HARVEST_MIN_ACTIVE_TARGET
        and len(set(domains)) >= 3
        and (len(source_types) >= 2 or len(tiers) >= 2)
        and fresh_count >= 2
        and max_domain_share <= 0.5
    )
    return {
        "accepted_active": len(values),
        "distinct_source_domains": len(set(domains)),
        "distinct_source_types": len(source_types),
        "distinct_source_tiers": len(tiers),
        "fresh_or_current": fresh_count,
        "max_domain_share": round(max_domain_share, 3),
        "sufficient_diversity": diverse,
        "early_stop_reason": "sufficient_diversity" if diverse else "insufficient_diversity",
    }


def _theme_allowed_domains(theme: str) -> set[str]:
    domains: set[str] = set()
    for query in FP_THEME_SOURCES.get(theme, []) or []:
        for match in re.findall(r"site:([^\s)]+)", str(query), flags=re.IGNORECASE):
            domain = match.strip().strip('"').strip("'").lower()
            if domain:
                domains.add(domain.removeprefix("www."))
    return domains


def _source_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def _source_domain_profile(domain: str) -> str:
    domain = (domain or "").lower().removeprefix("www.")
    if not domain:
        return "unknown"
    for known, profile in SOURCE_DOMAIN_PROFILES.items():
        known = known.lower().removeprefix("www.")
        if domain == known or domain.endswith(f".{known}"):
            return profile
    if domain.endswith(".pdf"):
        return "pdf_heavy"
    return "static_html"


def _signal_has_context_text(sig: dict) -> bool:
    return bool(
        sig.get("source_quote")
        or sig.get("description")
        or sig.get("summary")
        or sig.get("source_says")
        or sig.get("title")
        or sig.get("signal")
    )


def _is_curated_theme_source(sig: dict, theme: str) -> bool:
    source_url = sig.get("source_url") or sig.get("url") or ""
    domain = _source_domain(source_url)
    if not domain:
        return False
    return any(domain == allowed or domain.endswith("." + allowed) for allowed in _theme_allowed_domains(theme))


def _mark_foresight_context_unavailable(
    sig: dict,
    theme: str,
    reason: str,
    *,
    excluded: bool = False,
) -> dict:
    sig = _prepare_signal_for_promotion(sig, theme)
    for field in (
        "analysis_verified",
        "generation_eligible",
        "claim_support_status",
        "promotion_status",
        "source_registry_status",
    ):
        sig.pop(field, None)
    sig["foresight_context_eligible"] = False
    sig["theme_match"] = False
    sig["trend_supported"] = False
    sig["source_readable"] = False
    sig["content_verified"] = False
    sig["foresight_signal_status"] = "excluded" if excluded else "not_context_eligible"
    sig["source_audit_status"] = "excluded" if excluded else reason
    sig["trend_support_status"] = "unavailable" if excluded else "unchecked"
    sig["verification_reason"] = reason
    sig["content_verification_reason"] = reason
    sig["foresight_evidence_version"] = FORESIGHT_KB_EVIDENCE_VERSION
    return sig


def _foresight_context_supported(sig: dict, result: dict) -> bool:
    support = str(result.get("claim_support_status") or "").lower().strip()
    score = _safe_int(result.get("relevance_score"), 0)
    quote = str(result.get("source_quote") or "").strip()
    content_verified = result.get("content_verified") is True or bool(quote)
    cred = _foresight_source_credibility(sig)
    credible_source = cred in {"high", "medium"}

    # Foresight KB verification is not a company claim-support test.  The
    # question is whether a credible macro/industry source is readable and
    # directionally supports the stored trend signal.  Treat low-score or
    # explicitly unsupported checks as not context-eligible, but allow
    # high-credibility partial/contextual matches to ground foresight synthesis.
    if not content_verified or support == "unsupported":
        return False
    if support in FORESIGHT_CONTEXT_SUPPORT_STATUSES:
        if support == "supported":
            return score >= 25 or credible_source
        return credible_source and score >= 20
    if credible_source and bool(quote) and score >= 20:
        return True
    if credible_source and content_verified and str(sig.get("validated")).lower() == "true" and support not in {"unsupported", "contradicted"}:
        return True
    if credible_source and content_verified and sig.get("theme_match") is not False and sig.get("trend_supported") is not False:
        return True
    return False


def _apply_signal_promotion(sig: dict, result: dict | None, theme: str) -> dict:
    """Apply foresight-context metadata to a Foresight KB signal.

    This is deliberately separate from normal company-evidence promotion:
    a macro signal may be usable for foresight synthesis without becoming
    evidence that the scanned company has deployed, bought, or committed to
    anything.
    """
    sig = _prepare_signal_for_promotion(sig, theme)
    result = result or {}

    score = _safe_int(result.get("relevance_score"), 0)
    if score is not None:
        sig["content_relevance_score"] = score
        sig["content_match_score"] = score
    raw_support = str(result.get("claim_support_status") or sig.get("claim_support_status") or "unchecked").lower()
    sig["foresight_context_support_status"] = raw_support
    source_readable = (
        result.get("content_verified") is True
        or bool(result.get("source_quote"))
        or bool(sig.get("source_quote"))
        or (sig.get("url_verified") is True and _signal_has_context_text(sig))
    )
    sig["source_readable"] = source_readable
    sig["content_verified"] = source_readable or sig.get("content_verified") is True
    reason = result.get("reason") or sig.get("content_verification_reason") or ""
    sig["content_verification_reason"] = reason
    sig["content_match_reason"] = reason
    sig["verification_model"] = result.get("verification_model", sig.get("verification_model"))
    if result.get("source_quote"):
        sig["source_quote"] = result["source_quote"]

    curated_source = _is_curated_theme_source(sig, theme)
    cred = _foresight_source_credibility(sig)
    has_context_text = _signal_has_context_text(sig)
    inferred_macro_support = bool(
        sig.get("url_verified") is True
        and source_readable
        and has_context_text
        and raw_support not in {"unsupported", "contradicted"}
        and sig.get("theme_match") is not False
        and sig.get("trend_supported") is not False
        and (cred in {"high", "medium"} or curated_source)
    )
    eligible = _foresight_context_supported(sig, result) or inferred_macro_support
    sig["foresight_context_eligible"] = eligible
    sig["theme_match"] = bool(
        source_readable
        and raw_support not in {"unsupported", "contradicted"}
        and (score >= 20 or curated_source or sig.get("theme_match") is not False)
    )
    sig["trend_supported"] = bool(
        eligible
        or (
            source_readable
            and has_context_text
            and raw_support not in {"unsupported", "contradicted"}
            and sig["theme_match"]
        )
    )
    for field in (
        "analysis_verified",
        "generation_eligible",
        "claim_support_status",
        "promotion_status",
        "source_registry_status",
    ):
        sig.pop(field, None)
    sig["foresight_signal_status"] = "context_eligible" if eligible else "not_context_eligible"
    if eligible:
        sig["source_audit_status"] = "accepted"
    elif not source_readable:
        sig["source_audit_status"] = "source_unreadable"
    elif raw_support == "unsupported":
        sig["source_audit_status"] = "trend_not_supported"
    elif not sig["theme_match"]:
        sig["source_audit_status"] = "theme_mismatch"
    else:
        sig["source_audit_status"] = "not_context_eligible"
    sig["trend_support_status"] = "supported" if eligible else raw_support
    sig["analysis_scope"] = "foresight_context_only"
    sig["verification_reason"] = (
        "foresight_context_verified"
        if eligible
        else ("claim_unsupported" if raw_support == "unsupported" else "foresight_context_not_eligible")
    )
    sig["foresight_evidence_version"] = FORESIGHT_KB_EVIDENCE_VERSION
    return sig


def _promote_foresight_signals_for_analysis(
    data: dict,
    max_checks: int | None = None,
    max_workers: int = 4,
) -> dict:
    """
    Upgrade Foresight KB signals to the foresight-context verification contract.
    Weak sources stay in the KB for audit, but generated foresight synthesis
    only sees context-eligible signals.
    """
    if not data or not data.get("signals"):
        return data

    theme = data.get("theme", "?")
    signals = [s for s in data.get("signals", []) if isinstance(s, dict)]
    data["signals"] = signals

    candidates = []
    for sig in signals:
        _prepare_signal_for_promotion(sig, theme)
        url = (sig.get("source_url") or sig.get("url") or "").strip()
        claim = _signal_claim_text(sig)
        if not sig.get("url_verified"):
            _mark_foresight_context_unavailable(
                sig,
                theme,
                "url_not_verified" if url else "missing_source_url",
                excluded=bool(url),
            )
            continue
        if url and claim and len(claim) > 20:
            candidates.append((url, claim, sig))
        else:
            _mark_foresight_context_unavailable(sig, theme, "missing_or_too_short_trend_claim")

    to_check = candidates if max_checks is None else candidates[:max_checks]
    content_results = {}

    if to_check:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _check_relevance,
                    url,
                    claim,
                    None,
                    12,
                    CLEAN_EVIDENCE_MODEL_CHAIN,
                ): sig
                for url, claim, sig in to_check
            }
            for future in concurrent.futures.as_completed(futures):
                sig = futures[future]
                try:
                    result = future.result(timeout=24)
                except Exception as exc:
                    result = {
                        "relevance_score": -1,
                        "relevant": False,
                        "claim_support_status": "unchecked",
                        "content_verified": False,
                        "source_quote": "",
                        "reason": f"Verifier failed: {str(exc)[:120]}",
                        "verification_model": "none",
                    }
                content_results[id(sig)] = result

    for sig in signals:
        result = content_results.get(id(sig))
        if result is not None:
            _apply_signal_promotion(sig, result, theme)
        elif sig.get("foresight_evidence_version") != FORESIGHT_KB_EVIDENCE_VERSION:
            # Not checked because of max_checks or missing claim. Keep visible,
            # but do not let legacy snippets become generation evidence.
            _mark_foresight_context_unavailable(sig, theme, "not_checked_during_context_repair")

    if "validation" not in data:
        data["validation"] = {}
    total = len(signals)
    url_verified = sum(1 for s in signals if s.get("url_verified") is True or s.get("verified") is True)
    content_verified = sum(1 for s in signals if s.get("content_verified") is True)
    context_eligible = sum(1 for s in signals if s.get("foresight_context_eligible") is True)
    data["validation"]["analysis_checked"] = len(to_check)
    data["validation"]["total_signals"] = total
    data["validation"]["url_verified"] = url_verified
    data["validation"]["content_verified"] = content_verified
    data["validation"]["foresight_context_eligible"] = context_eligible
    data["validation"]["theme_match"] = sum(1 for s in signals if s.get("theme_match") is True)
    data["validation"]["trend_supported"] = sum(1 for s in signals if s.get("trend_supported") is True)
    data["validation"]["source_readable"] = sum(1 for s in signals if s.get("source_readable") is True)
    data["validation"]["analysis_verified"] = 0
    data["validation"]["generation_eligible"] = 0
    data["validation"]["not_context_eligible"] = sum(
        1 for s in signals if s.get("foresight_signal_status") == "not_context_eligible"
    )
    data["validation"]["excluded"] = sum(1 for s in signals if s.get("foresight_signal_status") == "excluded")
    data["validation"]["foresight_evidence_version"] = FORESIGHT_KB_EVIDENCE_VERSION

    print(
        f"   🛡️ [Foresight KB Repair] {theme}: "
        f"{context_eligible}/{total} context eligible, "
        f"{content_verified} content verified, "
        f"{data['validation']['not_context_eligible']} not context eligible, "
        f"{data['validation']['excluded']} excluded"
    )
    return data


def _merge_signals(existing: list[dict], new_signals: list[dict], max_total: int = MAX_SIGNALS_PER_THEME) -> list[dict]:
    """
    Merge active new signals into active existing KB signals:
      - Deduplicate by source_url or normalized title
      - Keep the stronger active signal when duplicates occur
      - Cap at max_total, ranked by source quality and snippet usefulness
    """
    merged = {}
    for sig in existing:
        if sig.get("foresight_context_eligible") is not True:
            continue
        key = _signal_key(sig)
        merged[key] = sig

    added = 0
    updated = 0
    for sig in new_signals:
        if sig.get("foresight_context_eligible") is not True:
            continue
        key = _signal_key(sig)
        if key in merged and _active_signal_score(sig) <= _active_signal_score(merged[key]):
            continue
        if key in merged:
            updated += 1
        else:
            added += 1
        merged[key] = sig

    result = sorted(
        merged.values(),
        key=lambda s: _active_signal_score(s),
        reverse=True,
    )

    capped = result[:max_total]
    if added or updated:
        print(f"   🔀 [Merge] {added} new, {updated} refreshed, {len(capped)} total (cap {max_total})")

    return capped


def _is_central_kb_storable_signal(sig: dict, *, existing: bool = False) -> bool:
    """Return whether a signal belongs in the central, company-neutral KB.

    Legacy helper retained for compatibility. Active KB storage is now stricter:
    Scanner consumes only `foresight_context_eligible=True`, and rejected
    candidates belong in `foresight_kb_audit`, not active theme files.
    """
    if not isinstance(sig, dict):
        return False
    if sig.get("company_specific") is True or sig.get("wrong_entity") is True:
        return False
    if not (sig.get("source_url") or sig.get("url")):
        return False
    if sig.get("foresight_signal_status") == "excluded":
        return False
    if sig.get("source_audit_status") in {"excluded", "source_dead", "missing_source_url"}:
        return bool(existing)
    if not existing and sig.get("source_audit_status") in {
        "trend_not_supported",
        "theme_mismatch",
        "source_unreadable",
        "low_credibility",
    }:
        return False
    claim_type = str(sig.get("claim_type") or "").lower()
    source_type = str(sig.get("source_type") or sig.get("origin") or "").lower()
    if claim_type and claim_type not in {"foresight_context", "regulatory_context"}:
        return False
    if source_type in {"company_evidence", "stakeholder_candidate", "sales_signal"}:
        return False
    if sig.get("url_verified") is False and sig.get("verified") is False:
        return bool(existing)
    return True


def _select_harvest_queries(theme: str, search_queries: list[str]) -> list[str]:
    """Choose a bounded, high-quality query subset for one harvest pass."""
    queries = [q for q in (search_queries or []) if q]
    limit = HARVEST_THEME_QUERY_LIMIT.get(theme, HARVEST_DEFAULT_QUERY_LIMIT)
    priority_tokens = HARVEST_THEME_QUERY_PRIORITY.get(theme, [])
    if not priority_tokens:
        return queries[:limit]

    selected: list[str] = []
    for token in priority_tokens:
        for query in queries:
            if token in query and query not in selected:
                selected.append(query)
                break
    for query in queries:
        if query not in selected:
            selected.append(query)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _harvest_query_batches(theme: str, search_queries: list[str]) -> list[list[str]]:
    """Split a theme's selected source queries into small timeout-resilient batches."""
    selected = _select_harvest_queries(theme, search_queries)
    if not selected:
        return []
    batch_size = _harvest_theme_profile(theme)["batch_size"]
    return [selected[i:i + batch_size] for i in range(0, len(selected), batch_size)]


def _harvest_theme_profile(theme: str) -> dict:
    if theme in HARVEST_FRAGILE_THEMES:
        return {
            "name": "fragile_micro_batch",
            "batch_size": 2,
            "theme_budget_sec": HARVEST_DIFFICULT_THEME_BUDGET_SEC,
        }
    if theme in HARVEST_HARD_THEMES:
        return {
            "name": "broad",
            "batch_size": HARVEST_BATCH_SIZE_HARD,
            "theme_budget_sec": HARVEST_DIFFICULT_THEME_BUDGET_SEC,
        }
    return {
        "name": "focused",
        "batch_size": HARVEST_BATCH_SIZE_DEFAULT,
        "theme_budget_sec": HARVEST_NORMAL_THEME_BUDGET_SEC,
    }


def _batch_signal_target(query_count: int) -> tuple[int, int]:
    if query_count <= 1:
        return 1, 1
    if query_count == 2:
        return 1, 2
    if query_count == 3:
        return 2, 3
    return 2, 4


def _build_harvest_prompt(
    theme: str,
    search_queries: list[str],
    known_urls: list[str] | None = None,
) -> str:
    """Build a web-research prompt to harvest megatrend signals for one FP theme."""

    selected_queries = _select_harvest_queries(theme, search_queries)
    queries_text = "\n".join(f"- {q}" for q in selected_queries)
    target_min, target_max = _batch_signal_target(len(selected_queries))
    known_urls_text = "\n".join(f"- {url}" for url in (known_urls or [])[:30])
    source_memory_instruction = (
        "\nSOURCE MEMORY — DO NOT RETURN THESE EXACT URLS AGAIN:\n"
        f"{known_urls_text}\n"
        "You may still use other, newer pages from the same domains.\n"
        if known_urls_text else ""
    )

    return f"""You are a Strategic Foresight Research Analyst specializing in megatrend signal detection.

TASK: Identify {target_min}-{target_max} significant current signals/developments for the megatrend theme "{theme}".

These signals must be:
- HORIZONTAL: Industry-agnostic macro forces (not company-specific)
- RECENT: Prefer the last 12 months and normally no older than 24 months unless the source is an official evergreen framework, regulation, standard, or living dashboard
- VERIFIED: From authoritative sources you can cite with real URLs
- ACTIONABLE: Relevant to strategic planning for businesses affected by this theme

MANDATORY SOURCE TARGETING — Search these first:
{queries_text}
{source_memory_instruction}

ALLOWED SOURCE DOMAINS:
Tier 1 (intergovernmental/UN/flagship journals): ipcc.ch, who.int, iea.org, irena.org, imf.org, worldbank.org, oecd.org, nature.com, science.org, thelancet.com, bis.org, fao.org, ilo.org, unep.org, wto.org, unctad.org, unfccc.int, ipbes.net, iom.int, un.org, ec.europa.eu, wmo.int
Tier 2 (think tanks/academic): chathamhouse.org, rand.org, brookings.edu, carnegieendowment.org, csis.org, iiss.org, sipri.org, oxfordmartin.ox.ac.uk, turing.ac.uk, hai.stanford.edu, lse.ac.uk, oii.ox.ac.uk, bruegel.org, cser.ac.uk, belfercenter.org, piie.com
Tier 3 (specialist research/foresight): iftf.org, cifs.dk, soif.org.uk, futures.hawaii.edu, weforum.org, ellenmacarthurfoundation.org, wri.org, rmi.org, iucn.org, fair.work, sitra.fi, nesta.org.uk, c40.org, millennium-project.org, clubofrome.org, hybridcoe.fi, enisa.europa.eu
Tier 4 (commercial/supplementary): mckinsey.com, deloitte.com, gartner.com, forrester.com, bcg.com, pwc.com, hbr.org, reuters.com, ft.com, bloomberg.com, economist.com, idc.com, accenture.com

SOURCE ACCEPTANCE RULE:
- Prefer explicit theme-registry domains from MANDATORY SOURCE TARGETING.
- A Tier 1 institutional source outside the explicit theme registry may be used only if it is clearly theme-matched, trend-supporting, company-neutral macro context, recent/current, and has a strong semantic snippet.
- Tier 2-4 sources outside the explicit theme registry must not be used as primary active signals.

DO NOT USE: blogs, press release wires, social media, content farms, regional tech news, marketing sites.

SIFT PROTOCOL (mandatory):
1. STOP: Verify the signal is real, not marketing spin.
2. INVESTIGATE SOURCE: Is this independent research, not corporate PR?
3. FIND BETTER COVERAGE: Each signal needs 2+ independent corroborating sources.
4. TRACE CLAIMS: Link to ORIGINAL source, not derivative summaries.

For each signal, provide:
- "title": Concise name of the megatrend signal (specific, not vague)
- "description": 2-3 sentences explaining the signal and its strategic implications
- "semantic_snippet": 60-120 words explaining what is changing, why it matters, the affected business capability, likely time horizon, and what kind of company/domain this signal is relevant for. This snippet will later be used for semantic source selection during company scans.
- "signal_type": "Plausible/Maturing" | "Probable/Emerging" | "Weakening/Declining" | "Preposterous/Black Swan"
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "time_horizon": Same value as timeframe, repeated as a stable metadata field
- "source_url": Primary URL from your web search (MUST be real, from allowed domains)
- "published_at": Publication date of the primary source in YYYY-MM-DD format when available
- "published_year": Publication year as an integer
- "last_updated_at": Last update date in YYYY-MM-DD format when available, otherwise null
- "supporting_sources": Array of 1-2 additional corroborating URLs from different orgs. Each: {{"url": "https://...", "title": "Brief title"}}
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "source_domain": Domain of the primary URL
- "source_tier": "tier1" | "tier2" | "tier3" | "tier4" according to the source framework above
- "confidence": "high" (3+ sources) | "medium" (2 sources) | "low" (1 source)
- "industries_affected": Array of 2-4 industry domains most impacted (e.g., ["Technology & Software", "Financial Services", "Healthcare"])
- "kb_dimensions_affected": Array of 1-3 KB dimensions impacted: "Operating Model", "Strategy", "Values", "Service Portfolio", "AI Readiness"
- "lifecycle_stage": Assess where this signal sits on the trend adoption S-curve. One of: "weak_signal" | "strong_signal" | "hype_cycle" | "valley_of_death" | "go_to_market" | "market_breakout" | "established_trend" | "market_saturation" | "weakening_trend" | "fading_signal". Assessment guide: only academic/R&D papers → weak_signal; multiple think-tank reports but no products → strong_signal; mainstream media hype but limited real adoption → hype_cycle; prior hype followed by failures/pullbacks → valley_of_death; real products/regulations launched → go_to_market; scaling adoption metrics → market_breakout; widespread adoption, industry standard → established_trend; market leaders consolidating, growth flattening → market_saturation; replacement technologies emerging → weakening_trend; near-zero new adoption → fading_signal
- "s_curve_phase": Same value as lifecycle_stage, repeated as a stable metadata field
- "trend_momentum": "accelerating" (adoption rate increasing) | "stable" (steady pace) | "decelerating" (slowing down)
- "signal_direction": Same value as trend_momentum, repeated as a stable metadata field

CRITICAL: Every URL must be real and retrieved from web search. Do NOT fabricate URLs.
CRITICAL: Return only signals you believe are usable macro foresight context. Do not include weak/off-theme candidates just to fill the list.

RETURN STRICT JSON ONLY:
{{
  "theme": "{theme}",
  "signals": [
    {{
      "title": "...",
      "description": "...",
      "semantic_snippet": "...",
      "signal_type": "...",
      "timeframe": "...",
      "time_horizon": "...",
      "source_url": "https://...",
      "published_at": "YYYY-MM-DD",
      "published_year": 2026,
      "last_updated_at": "YYYY-MM-DD or null",
      "supporting_sources": [{{"url": "https://...", "title": "..."}}],
      "source_type": "...",
      "source_domain": "...",
      "source_tier": "tier1|tier2|tier3|tier4",
      "confidence": "...",
      "industries_affected": ["...", "..."],
      "kb_dimensions_affected": ["...", "..."],
      "lifecycle_stage": "weak_signal|strong_signal|hype_cycle|valley_of_death|go_to_market|market_breakout|established_trend|market_saturation|weakening_trend|fading_signal",
      "s_curve_phase": "weak_signal|strong_signal|hype_cycle|valley_of_death|go_to_market|market_breakout|established_trend|market_saturation|weakening_trend|fading_signal",
      "trend_momentum": "accelerating|stable|decelerating",
      "signal_direction": "accelerating|stable|decelerating"
    }}
  ]
}}"""


def _harvest_single_theme(
    theme: str,
    search_queries: list[str],
    *,
    batch_index: int = 1,
    batch_total: int = 1,
    theme_deadline_monotonic: float | None = None,
    run_context: _HarvestRunContext | None = None,
    known_urls: list[str] | None = None,
) -> dict:
    """Harvest one batch: GPT discovery, Gemini fallback, Sonnet JSON repair."""

    batch_label = f"batch {batch_index}/{batch_total}"
    attempts: list[dict] = []

    def remaining() -> float:
        if theme_deadline_monotonic is None:
            return HARVEST_DIFFICULT_THEME_BUDGET_SEC
        return max(0.0, theme_deadline_monotonic - time.monotonic())

    def parse_payload(text: str) -> tuple[dict | None, str]:
        if not text or len(text) < 40:
            return None, "empty_response"
        json_str = _extract_json_from_text(text)
        if not json_str:
            return None, "invalid_json"
        try:
            data = json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            return None, "invalid_json"
        if not isinstance(data, dict) or not isinstance(data.get("signals"), list) or not data["signals"]:
            return None, "empty_response"
        return data, "ok"

    def repair_json(text: str, provider_name: str) -> tuple[dict | None, str]:
        if not text or remaining() < 10:
            return None, "invalid_json"
        repair_timeout = min(HARVEST_SONNET_REPAIR_OUTER_TIMEOUT_SEC, max(10, int(remaining())))
        repair_prompt = f"""Repair the following {provider_name} response into valid JSON only.
Preserve its facts, URLs, dates, metadata, and signals. Do not search the web and do not add sources.
Required envelope: {{"theme": {json.dumps(theme)}, "signals": [...]}}.

RESPONSE TO REPAIR:
{text[:30000]}"""
        call_runner = run_context.call if run_context else (
            lambda provider, label, timeout, fn: _call_harvest_model_with_deadline(label, timeout, fn)
        )
        call = call_runner(
            "anthropic", f"{theme} {batch_label} Sonnet JSON repair", repair_timeout,
            lambda: call_anthropic_text(
                repair_prompt,
                model="claude-sonnet-4-6",
                max_tokens=7000,
                timeout=min(HARVEST_SONNET_REPAIR_NATIVE_TIMEOUT_SEC, repair_timeout),
                web_search=False,
            ),
        )
        attempts.append({
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "purpose": "json_repair",
            "status": call.get("status"),
            "elapsed_ms": call.get("elapsed_ms"),
        })
        if call.get("status") != "ok":
            return None, call.get("status") or "invalid_json"
        return parse_payload(str(call.get("value") or ""))

    prompt = _build_harvest_prompt(theme, search_queries, known_urls=known_urls)
    gpt_outer = min(HARVEST_GPT_OUTER_TIMEOUT_SEC, max(1, int(remaining())))
    if gpt_outer < 15:
        return {
            "theme": theme,
            "signals": [],
            "failure_status": "theme_budget_exhausted",
            "provider_attempts": attempts,
        }

    print(
        f"   🌐 [Harvest] {theme} {batch_label} → gpt-5.5 "
        f"(discovery, timeout={gpt_outer}s, queries={len(search_queries)})..."
    )
    call_runner = run_context.call if run_context else (
        lambda provider, label, timeout, fn: _call_harvest_model_with_deadline(label, timeout, fn)
    )
    gpt = call_runner(
        "openai", f"{theme} {batch_label} gpt-5.5", gpt_outer,
        lambda: call_openai_text(
            prompt,
            model="gpt-5.5",
            max_output_tokens=7000,
            timeout=min(HARVEST_GPT_NATIVE_TIMEOUT_SEC, gpt_outer),
            web_search=True,
            search_context_size="medium",
            max_retries=0,
        ),
    )
    attempts.append({
        "provider": "openai",
        "model": "gpt-5.5",
        "purpose": "discovery",
        "status": gpt.get("status"),
        "elapsed_ms": gpt.get("elapsed_ms"),
        "lingering": gpt.get("lingering", False),
        "query_count": len(search_queries),
    })
    gpt_text = str(gpt.get("value") or "")
    data, parse_status = parse_payload(gpt_text) if gpt.get("status") == "ok" else (None, gpt.get("status"))
    if data is None and parse_status == "invalid_json":
        data, parse_status = repair_json(gpt_text, "GPT")
    if data is not None:
        model_name = "gpt-5.5"
    else:
        fallback_queries = search_queries[: max(1, min(2, (len(search_queries) + 1) // 2))]
        if not gemini_api_key():
            print(f"   ℹ️ [Harvest] {theme} {batch_label}: Gemini skipped — GEMINI_API_KEY unavailable")
            attempts.append({
                "provider": "google",
                "model": GEMINI_HARVEST_MODEL,
                "purpose": "discovery_fallback",
                "status": "provider_unavailable",
            })
            return {
                "theme": theme,
                "signals": [],
                "failure_status": parse_status or "provider_unavailable",
                "provider_attempts": attempts,
            }
        gemini_outer = min(HARVEST_GEMINI_OUTER_TIMEOUT_SEC, max(1, int(remaining())))
        if gemini_outer < 15:
            return {
                "theme": theme,
                "signals": [],
                "failure_status": "theme_budget_exhausted",
                "provider_attempts": attempts,
            }
        fallback_prompt = _build_harvest_prompt(theme, fallback_queries, known_urls=known_urls)
        print(
            f"   🌐 [Harvest] {theme} {batch_label} → {GEMINI_HARVEST_MODEL} "
            f"(reduced fallback, timeout={gemini_outer}s, queries={len(fallback_queries)})..."
        )
        gemini = call_runner(
            "google", f"{theme} {batch_label} {GEMINI_HARVEST_MODEL}", gemini_outer,
            lambda: call_gemini_harvest_text(
                fallback_prompt,
                model=GEMINI_HARVEST_MODEL,
                timeout=min(HARVEST_GEMINI_NATIVE_TIMEOUT_SEC, gemini_outer),
                max_output_tokens=7000,
            ),
        )
        attempts.append({
            "provider": "google",
            "model": GEMINI_HARVEST_MODEL,
            "purpose": "discovery_fallback",
            "status": gemini.get("status"),
            "elapsed_ms": gemini.get("elapsed_ms"),
            "lingering": gemini.get("lingering", False),
            "query_count": len(fallback_queries),
        })
        if gemini.get("lingering"):
            return {
                "theme": theme,
                "signals": [],
                "failure_status": "provider_timeout",
                "provider_attempts": attempts,
                "lingering_provider_call": True,
                "lingering_provider": "google",
            }
        gemini_text = str(gemini.get("value") or "")
        data, parse_status = parse_payload(gemini_text) if gemini.get("status") == "ok" else (None, gemini.get("status"))
        if data is None and parse_status == "invalid_json":
            data, parse_status = repair_json(gemini_text, "Gemini")
        if data is None:
            print(f"   ❌ [Harvest] {theme} {batch_label}: discovery failed ({parse_status})")
            return {
                "theme": theme,
                "signals": [],
                "failure_status": parse_status or "empty_response",
                "provider_attempts": attempts,
            }
        model_name = GEMINI_HARVEST_MODEL

    data["model"] = model_name
    data["model_mode"] = "quality"
    data["time_ms"] = sum(int(a.get("elapsed_ms") or 0) for a in attempts)
    data["theme"] = theme
    data["batch_index"] = batch_index
    data["batch_total"] = batch_total
    data["batch_queries"] = search_queries
    data["provider_attempts"] = attempts
    print(f"   ✅ [Harvest] {theme} {batch_label}: {len(data['signals'])} raw signals ({model_name})")
    return data


def _validate_harvest_signals(data: dict) -> dict:
    """
    Validate harvested signals: verify URLs and assess credibility.

    This step does not decide central active-KB eligibility. Rejected or weak
    candidates are routed to audit later so the active KB remains clean without
    losing traceability.
    Modifies and returns the data dict in place.
    """
    if not data or not data.get("signals"):
        return data

    all_urls = []
    for sig in data["signals"]:
        if sig.get("source_url"):
            all_urls.append(("primary", sig, sig["source_url"]))
        for ss in sig.get("supporting_sources", []):
            if ss.get("url"):
                all_urls.append(("supporting", ss, ss["url"]))

    # Parallel URL verification
    url_status = {}
    if all_urls:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_verify_foresight_url, url, 8): url
                for (_, _, url) in all_urls
            }
            for future in concurrent.futures.as_completed(futures):
                url = futures[future]
                try:
                    url_status[url] = future.result()
                except Exception:
                    url_status[url] = False

    verified_count = 0
    dead_count = 0

    for sig in data["signals"]:
        # Check primary URL
        primary_url = sig.get("source_url", "")
        sig["url_verified"] = url_status.get(primary_url, False)
        sig["source_credibility"] = assess_source_credibility(primary_url, sig.get("source_type", ""))

        if sig["url_verified"]:
            verified_count += 1
        else:
            dead_count += 1

        # Check supporting sources
        valid_supporting = []
        for ss in sig.get("supporting_sources", []):
            ss_url = ss.get("url", "")
            ss["url_verified"] = url_status.get(ss_url, False)
            ss["source_credibility"] = assess_source_credibility(ss_url)
            if ss["url_verified"]:
                valid_supporting.append(ss)
        sig["supporting_sources"] = valid_supporting
        sig["source_count"] = 1 + len(valid_supporting) if sig["url_verified"] else len(valid_supporting)

        # Adjust confidence based on actual verification
        if sig["source_count"] >= 3 and sig["source_credibility"] == "high":
            sig["confidence"] = "high"
        elif sig["source_count"] >= 2:
            sig["confidence"] = "medium"
        else:
            sig["confidence"] = "low"

        sig["validated"] = sig["url_verified"] and sig["source_count"] >= 1
        sig["source_domain"] = sig.get("source_domain") or _source_domain(primary_url)
        sig["theme"] = sig.get("theme") or data.get("theme", "")

    # Mark all harvested signals as external foresight context. They are not company-specific facts.
    data["signals"] = [
        _normalize_foresight_tags(
            sanitize_foresight_record(annotate_foresight_signal(s, theme=data.get("theme", ""))),
            data.get("theme", ""),
        )
        for s in data["signals"]
    ]

    data["validation"] = {
        "verified_urls": verified_count,
        "dead_urls": dead_count,
        "signals_after_validation": len(data["signals"]),
    }

    print(f"   🔍 [Validate] {data.get('theme', '?')}: {verified_count} verified, {dead_count} dead → {len(data['signals'])} candidates checked")
    return data


def _verify_signal_content(data: dict, max_checks: int | None = None, max_workers: int = 4) -> dict:
    """
    Backward-compatible wrapper for the stricter KB evidence-promotion step.
    """
    return _promote_foresight_signals_for_analysis(data, max_checks=max_checks, max_workers=max_workers)


def _harvest_theme_group(
    themes: list[str] | None = None,
    progress_callback=None,
    *,
    run_context: _HarvestRunContext | None = None,
    save_registry: bool = False,
) -> dict:
    """
    Harvest megatrend signals for all (or selected) FP themes.

    Args:
        themes: List of theme names to harvest. None = all 30.
        progress_callback: Optional fn(theme, index, total, status) for progress tracking.

    Returns dict with harvest results and summary.
    """
    storage = BlobStorage()
    run_context = run_context or _HarvestRunContext()

    target_themes = themes or FP_THEMES_30
    total = len(target_themes)
    results = {
        "harvested": 0,
        "failed": 0,
        "total_signals": 0,
        "accepted_active": 0,
        "rejected_audit": 0,
        "themes": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    print(f"\n{'='*70}")
    print(f"🌐 FORESIGHT KB HARVEST — {total} themes")
    print(f"{'='*70}\n")

    if save_registry:
        try:
            registry_url = storage.save_foresight_registry(build_strategic_foresight_theme_registry())
            if registry_url:
                results["registry_url"] = registry_url
                print("   📚 [Registry] Strategic Foresight Theme Registry snapshot saved")
        except Exception as exc:
            results["registry_warning"] = f"Could not save registry snapshot: {str(exc)[:160]}"
            print(f"   ⚠️ [Registry] {results['registry_warning']}")

    for i, theme in enumerate(target_themes):
        slug = _theme_to_slug(theme)
        search_queries = FP_THEME_SOURCES.get(theme, [])

        if progress_callback:
            progress_callback(theme, i, total, "harvesting")

        query_batches = _harvest_query_batches(theme, search_queries)
        theme_profile = _harvest_theme_profile(theme)
        theme_started = time.monotonic()
        theme_deadline = theme_started + theme_profile["theme_budget_sec"]
        selected_query_count = sum(len(batch) for batch in query_batches)
        query_label = f"{selected_query_count}/{len(search_queries)} queries" if search_queries else "0 queries"
        selected_domains = sorted({
            match.lower().removeprefix("www.")
            for batch in query_batches
            for query in batch
            for match in re.findall(r"site:([^\s)]+)", str(query), flags=re.IGNORECASE)
        })
        print(
            f"\n--- [{i+1}/{total}] {theme} ({query_label}, {len(query_batches)} batch(es), "
            f"profile={theme_profile['name']}, budget={theme_profile['theme_budget_sec']}s) ---"
        )
        print(f"   🧩 [Harvest Plan] domains={selected_domains or ['unscoped']} batch_size={theme_profile['batch_size']}")

        # Step 1: Load and classify existing KB signals for this theme.
        existing_data = storage.get_foresight_kb(slug)
        existing_before_count = len((existing_data or {}).get("signals", []))
        existing_active = []
        audit_records = []
        existing_stats = {}
        if existing_data and existing_data.get("signals"):
            existing_checked = _revalidate_existing_signals(existing_data["signals"], theme)
            existing_active, existing_audit, existing_stats = _split_active_and_audit(
                existing_checked,
                theme,
                stage="existing_kb",
            )
            audit_records.extend(existing_audit)

        existing_audit_data = None
        get_audit = getattr(storage, "get_foresight_kb_audit", None)
        if callable(get_audit):
            try:
                existing_audit_data = get_audit(slug)
            except Exception as exc:
                print(f"   ⚠️ [Harvest Memory] {theme}: audit memory unavailable: {exc!r}")
        existing_audit_candidates = list((existing_audit_data or {}).get("candidates") or [])
        source_memory = _build_theme_source_memory(existing_active, existing_audit_candidates)
        known_urls = sorted(source_memory["active_urls"] | source_memory["audit_urls"])
        print(
            f"   🧠 [Harvest Memory] {theme}: {len(source_memory['active_urls'])} active URL(s), "
            f"{len(source_memory['audit_urls'])} rejected URL(s), "
            f"{len(source_memory['content_hashes'])} content fingerprint(s)"
        )

        new_active = []
        new_stats = {}
        batch_diagnostics = []
        harvest_model = None

        # Step 2: Harvest new signals in smaller batches. Each batch is allowed
        # to fail independently; existing active KB records remain protected.
        batch_queue = [list(batch) for batch in query_batches]
        batch_index = 0
        while batch_queue:
            query_batch = batch_queue.pop(0)
            batch_index += 1
            theme_remaining = theme_deadline - time.monotonic()
            if theme_remaining < 15:
                print(f"   ⏱️ [Harvest] {theme}: theme budget exhausted before batch {batch_index}; keeping existing KB")
                batch_diagnostics.append({
                    "batch_index": batch_index,
                    "queries": query_batch,
                    "status": "theme_budget_exhausted",
                    "raw_signals": 0,
                    "accepted_active": 0,
                    "rejected_audit": 0,
                })
                break
            data = _harvest_single_theme(
                theme,
                query_batch,
                batch_index=batch_index,
                batch_total=len(query_batches),
                theme_deadline_monotonic=theme_deadline,
                run_context=run_context,
                known_urls=known_urls,
            )
            if not data or not data.get("signals"):
                batch_diagnostics.append({
                    "batch_index": batch_index,
                    "queries": query_batch,
                    "status": (data or {}).get("failure_status", "empty_response"),
                    "raw_signals": 0,
                    "accepted_active": 0,
                    "rejected_audit": 0,
                    "provider_attempts": (data or {}).get("provider_attempts", []),
                    "lingering_provider_call": (data or {}).get("lingering_provider_call", False),
                })
                if (data or {}).get("lingering_provider_call"):
                    provider = (data or {}).get("lingering_provider") or "unknown"
                    print(
                        f"   ⚠️ [Harvest] {theme}: {provider} call may still be lingering; "
                        "stopping only this theme while other theme workers continue"
                    )
                    break
                if len(query_batch) > 1 and theme_deadline - time.monotonic() >= 30:
                    split_batches = [[query] for query in query_batch]
                    batch_queue = split_batches + batch_queue
                    print(
                        f"   🔀 [Harvest] {theme}: failed batch split into "
                        f"{len(split_batches)} one-query micro-batches"
                    )
                continue

            filtered_signals, memory_stats = _filter_known_harvest_signals(
                data.get("signals", []), source_memory,
            )
            data["signals"] = filtered_signals
            if any(memory_stats.values()):
                print(
                    f"   🧠 [Harvest Memory] {theme}: suppressed exact/duplicate results "
                    f"{memory_stats}"
                )
            if not filtered_signals:
                batch_diagnostics.append({
                    "batch_index": batch_index,
                    "queries": query_batch,
                    "status": "source_memory_suppressed",
                    "raw_signals": sum(memory_stats.values()),
                    "accepted_active": 0,
                    "rejected_audit": 0,
                    "source_memory": memory_stats,
                    "provider_attempts": data.get("provider_attempts", []),
                })
                continue

            harvest_model = data.get("model") or harvest_model
            data = _validate_harvest_signals(data)
            batch_active, batch_audit, batch_stats = _split_active_and_audit(
                data.get("signals", []),
                theme,
                stage=f"new_harvest_batch_{batch_index}",
            )
            new_active.extend(batch_active)
            audit_records.extend(batch_audit)
            source_memory = _build_theme_source_memory(
                [*existing_active, *new_active],
                [*existing_audit_candidates, *audit_records],
            )
            known_urls = sorted(source_memory["active_urls"] | source_memory["audit_urls"])
            new_stats = _merge_harvest_stats(new_stats, batch_stats)
            batch_diagnostics.append({
                "batch_index": batch_index,
                "queries": query_batch,
                "status": "validated",
                "raw_signals": len(data.get("signals", [])),
                "accepted_active": len(batch_active),
                "rejected_audit": len(batch_audit),
                "stats": batch_stats,
                "model": data.get("model"),
                "time_ms": data.get("time_ms"),
                "provider_attempts": data.get("provider_attempts", []),
                "source_memory": memory_stats,
            })
            print(
                f"   🧭 [Harvest Batch] {theme} {batch_index} (planned {len(query_batches)}): "
                f"raw={len(data.get('signals', []))}, active={len(batch_active)}, audit={len(batch_audit)}, "
                f"reasons={{{', '.join(f'{k}:{v}' for k, v in batch_stats.items() if isinstance(v, int) and v)}}}"
            )
            if len(new_active) >= HARVEST_MAX_NEW_ACTIVE_TARGET:
                print(f"   ✅ [Harvest] {theme}: active target reached ({len(new_active)} new active); stopping remaining batches")
                break
            diversity = _harvest_diversity_status(new_active)
            if len(new_active) >= HARVEST_MIN_ACTIVE_TARGET:
                if diversity["sufficient_diversity"]:
                    print(
                        f"   ✅ [Harvest] {theme}: diversity target reached; stopping early "
                        f"(domains={diversity['distinct_source_domains']}, types={diversity['distinct_source_types']}, "
                        f"tiers={diversity['distinct_source_tiers']}, fresh={diversity['fresh_or_current']})"
                    )
                    break
                print(
                    f"   ↪️ [Harvest] {theme}: {len(new_active)} active but insufficient diversity; continuing "
                    f"(domains={diversity['distinct_source_domains']}, types={diversity['distinct_source_types']}, "
                    f"tiers={diversity['distinct_source_tiers']}, fresh={diversity['fresh_or_current']})"
                )

        # Step 4: Merge active-only records.
        merged_signals = _merge_signals(existing_active, new_active)
        harvest_diversity = _harvest_diversity_status(new_active)
        theme_elapsed_ms = int((time.monotonic() - theme_started) * 1000)

        if not merged_signals:
            if audit_records:
                audit_data = {
                    "theme": theme,
                    "theme_slug": slug,
                    "candidates": audit_records[:50],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "audit_contract": "not_used_by_scanner_synthesis",
                    "stats": {
                        "existing_before": existing_before_count,
                        "new_candidates": sum(d.get("raw_signals", 0) for d in batch_diagnostics),
                        "rejected_audit": len(audit_records),
                        "batch_diagnostics": batch_diagnostics,
                        "theme_profile": theme_profile,
                        "diversity": harvest_diversity,
                        "theme_elapsed_ms": theme_elapsed_ms,
                    },
                    "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
                }
                storage.save_foresight_kb_audit(slug, audit_data)
            results["failed"] += 1
            status = "no_valid_signals" if batch_diagnostics else "failed"
            results["rejected_audit"] += len(audit_records)
            results["themes"].append({
                "theme": theme,
                "slug": slug,
                "status": status,
                "signals": 0,
                "rejected_audit": len(audit_records),
                "batch_diagnostics": batch_diagnostics,
                "theme_profile": theme_profile,
                "diversity": harvest_diversity,
                "theme_elapsed_ms": theme_elapsed_ms,
            })
            print(f"   ⚠️ [Result] {theme}: no accepted active signals after merge — active KB unchanged; audit={len(audit_records)}")
            continue

        context_eligible_count = sum(1 for s in merged_signals if _is_kb_signal_context_eligible(s))
        audit_count = len(audit_records)

        # Step 5: Build merged data envelope and store
        merged_data = {
            "theme": theme,
            "theme_slug": slug,
            "signals": merged_signals,
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "model": harvest_model or (existing_data or {}).get("model", "unknown"),
            "merge_stats": {
                "existing_before_clean": existing_before_count,
                "existing_active": len(existing_active),
                "new_active": len(new_active),
                "merged_total": len(merged_signals),
                "foresight_context_eligible": context_eligible_count,
                "rejected_audit": audit_count,
                "existing_stats": existing_stats,
                "new_stats": new_stats,
                "batch_diagnostics": batch_diagnostics,
                "theme_profile": theme_profile,
                "diversity": harvest_diversity,
                "theme_elapsed_ms": theme_elapsed_ms,
                "storage_contract": "active_foresight_context_only",
            },
            "validation": {
                "total_signals": len(merged_signals),
                "url_verified": sum(1 for s in merged_signals if s.get("url_verified") is True or s.get("verified") is True),
                "content_verified": sum(1 for s in merged_signals if s.get("content_verified") is True or s.get("source_readable") is True),
                "foresight_context_eligible": context_eligible_count,
                "accepted_active": len(merged_signals),
                "rejected_audit": audit_count,
            },
            "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
        }

        url = storage.save_foresight_kb(slug, merged_data)
        if audit_records:
            storage.save_foresight_kb_audit(slug, {
                "theme": theme,
                "theme_slug": slug,
                "candidates": audit_records[:50],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "audit_contract": "not_used_by_scanner_synthesis",
                "stats": {
                    "existing_before": existing_before_count,
                    "new_candidates": sum(d.get("raw_signals", 0) for d in batch_diagnostics),
                    "rejected_audit": audit_count,
                    "batch_diagnostics": batch_diagnostics,
                    "theme_profile": theme_profile,
                    "diversity": harvest_diversity,
                    "theme_elapsed_ms": theme_elapsed_ms,
                },
                "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
            })
        n_signals = len(merged_signals)
        results["harvested"] += 1
        results["total_signals"] += n_signals
        results["accepted_active"] += context_eligible_count
        results["rejected_audit"] += audit_count
        results["themes"].append({
            "theme": theme,
            "slug": slug,
            "status": "ok",
            "signals": n_signals,
            "high_confidence": sum(1 for s in merged_signals if s.get("confidence") == "high"),
            "stored_url": url,
            "existing_kept": len(existing_active),
            "new_added": len(new_active),
            "foresight_context_eligible": context_eligible_count,
            "rejected_audit": audit_count,
            "batch_diagnostics": batch_diagnostics,
            "theme_profile": theme_profile,
            "diversity": harvest_diversity,
            "theme_elapsed_ms": theme_elapsed_ms,
        })

        print(
            f"   💾 [Store] {theme}: {n_signals} signals saved to foresight_kb/{slug} "
            f"({context_eligible_count} active, {audit_count} audit)"
        )

        # Rate limit courtesy: pause between themes to avoid web-search provider quota hits
        if i < total - 1:
            time.sleep(2)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*70}")
    print(f"🌐 HARVEST COMPLETE: {results['harvested']}/{total} themes, {results['total_signals']} signals")
    print(f"{'='*70}\n")

    return results


def harvest_all_themes(
    themes: list[str] | None = None,
    max_concurrent: int = HARVEST_THEME_WORKERS,
    progress_callback=None,
) -> dict:
    """Harvest themes in isolated workers with shared provider health controls."""
    target_themes = list(themes or FP_THEMES_30)
    total = len(target_themes)
    worker_count = max(1, min(int(max_concurrent or HARVEST_THEME_WORKERS), HARVEST_THEME_WORKERS, total or 1))
    started_at = datetime.now(timezone.utc).isoformat()
    run_context = _HarvestRunContext()
    aggregate = {
        "harvested": 0,
        "failed": 0,
        "total_signals": 0,
        "accepted_active": 0,
        "rejected_audit": 0,
        "themes": [],
        "started_at": started_at,
        "theme_workers": worker_count,
    }

    try:
        registry_url = BlobStorage().save_foresight_registry(build_strategic_foresight_theme_registry())
        if registry_url:
            aggregate["registry_url"] = registry_url
            print("   📚 [Registry] Strategic Foresight Theme Registry snapshot saved")
    except Exception as exc:
        aggregate["registry_warning"] = f"Could not save registry snapshot: {str(exc)[:160]}"
        print(f"   ⚠️ [Registry] {aggregate['registry_warning']}")

    print(
        f"\n🚦 [Harvest Parallel] {total} theme(s), {worker_count} isolated theme worker(s), "
        f"provider limits={HARVEST_PROVIDER_LIMITS}"
    )

    def run_theme(theme: str, index: int) -> dict:
        try:
            return _harvest_theme_group(
                [theme],
                progress_callback=None,
                run_context=run_context,
                save_registry=False,
            )
        except Exception as exc:
            print(f"   ❌ [Harvest Worker] {theme}: {type(exc).__name__}: {exc!r}")
            return {
                "harvested": 0,
                "failed": 1,
                "total_signals": 0,
                "accepted_active": 0,
                "rejected_audit": 0,
                "themes": [{
                    "theme": theme,
                    "status": "worker_exception",
                    "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                }],
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {}
        for index, theme in enumerate(target_themes):
            if progress_callback:
                progress_callback(theme, index, total, "queued")
            futures[pool.submit(run_theme, theme, index)] = (theme, index)
        for future in concurrent.futures.as_completed(futures):
            theme, index = futures[future]
            result = future.result()
            for key in ("harvested", "failed", "total_signals", "accepted_active", "rejected_audit"):
                aggregate[key] += int(result.get(key) or 0)
            aggregate["themes"].extend(result.get("themes") or [])
            if progress_callback:
                progress_callback(theme, index, total, "completed")

    aggregate["themes"].sort(key=lambda item: target_themes.index(item.get("theme")) if item.get("theme") in target_themes else total)
    aggregate["provider_quarantines"] = run_context.quarantined_snapshot()
    aggregate["completed_at"] = datetime.now(timezone.utc).isoformat()
    print(
        f"\n🌐 HARVEST COMPLETE: {aggregate['harvested']}/{total} themes, "
        f"{aggregate['total_signals']} signals; provider quarantines={aggregate['provider_quarantines']}"
    )
    return aggregate


def _is_kb_signal_context_eligible(sig: dict) -> bool:
    """Only verified company-neutral KB context signals may enter foresight prompts."""
    if not isinstance(sig, dict):
        return False
    if sig.get("company_specific") is True or sig.get("wrong_entity") is True:
        return False
    claim_type = str(sig.get("claim_type") or "").lower()
    source_type = str(sig.get("source_type") or sig.get("origin") or "").lower()
    context_safe = (
        claim_type in {"foresight_context", "regulatory_context"}
        or sig.get("not_evidence_of_deployment") is True
        or source_type in {"foresight", "foresight_report", "trend_report", "regulatory_context"}
    )
    return (
        context_safe
        and sig.get("foresight_context_eligible") is True
        and sig.get("foresight_signal_status") in {ACTIVE_FORESIGHT_STATUS, "context_eligible"}
        and sig.get("source_audit_status") in {"accepted_active", "accepted"}
        and (sig.get("url_verified") is True or sig.get("verified") is True)
        and (sig.get("source_readable") is not False or sig.get("content_verified") is True or bool(sig.get("source_quote")))
    )


# Backward-compatible alias for older imports/tests.  The name is legacy; the
# behavior is now foresight-context eligibility, not company evidence generation.
_is_kb_signal_generation_eligible = _is_kb_signal_context_eligible


def get_kb_signals_for_themes(theme_names: list[str]) -> list[dict]:
    """
    Retrieve pre-validated signals from the Foresight KB for a set of FP themes.
    Used by generate_foresight() to inject known signals into the company-specific prompt.

    Near-fit fallback: When a requested theme has no KB data, automatically
    pulls signals from the nearest related theme (via THEME_FALLBACK_MAP)
    instead of triggering a live Google Search. This keeps all foresight data
    flowing through the harvester's controlled validation pipeline.

    Returns a flat list of signal dicts, sorted by confidence (high first).
    """
    storage = BlobStorage()
    all_signals = []

    for theme in theme_names:
        slug = _theme_to_slug(theme)
        data = storage.get_foresight_kb(slug)
        signals_found = []

        if data and data.get("signals"):
            for sig in data["signals"]:
                if _is_kb_signal_context_eligible(sig):
                    promoted = dict(sig)
                    promoted["_from_kb"] = True
                    promoted["_kb_theme"] = theme
                    signals_found.append(promoted)
            if not signals_found:
                validation = data.get("validation") or {}
                total = validation.get("total_signals") or len(data.get("signals") or [])
                eligible = validation.get("foresight_context_eligible", 0)
                audit = validation.get("rejected_audit", 0)
                not_eligible = validation.get("not_context_eligible", validation.get("display_only", 0))
                excluded = validation.get("excluded", 0)
                print(
                    f"   ⚠️ [Foresight KB] {theme}: {eligible}/{total} foresight-context eligible "
                    f"(audit {audit}, not context eligible {not_eligible}, excluded {excluded})"
                )

        # Near-fit fallback: if no signals for this theme, try related themes
        if not signals_found:
            fallback_themes = THEME_FALLBACK_MAP.get(theme, [])
            for fallback_theme in fallback_themes:
                fallback_slug = _theme_to_slug(fallback_theme)
                fallback_data = storage.get_foresight_kb(fallback_slug)
                if fallback_data and fallback_data.get("signals"):
                    for sig in fallback_data["signals"]:
                        if _is_kb_signal_context_eligible(sig):
                            promoted = dict(sig)
                            promoted["_from_kb"] = True
                            promoted["_kb_theme"] = theme  # Tag with the originally requested theme
                            promoted["_kb_fallback_from"] = fallback_theme  # Track substitution source
                            signals_found.append(promoted)
                    if signals_found:
                        print(f"   📚 [KB Near-Fit] No data for '{theme}' → using '{fallback_theme}' ({len(signals_found)} signals)")
                        break  # Use the first fallback that has data

        all_signals.extend(signals_found)

    # Sort: high confidence first, then medium, then low
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    all_signals.sort(key=lambda s: confidence_order.get(s.get("confidence", "low"), 3))

    return all_signals


def _ensure_kb_signal_neutral(sig: dict) -> dict:
    """
    Enforce KB signal invariant: signals must be company-neutral.
    Sets claim_type, company_specific, not_evidence_of_deployment.
    possible_relevance from KB is intentionally NOT emitted in prompts — the LLM
    generates fresh company-specific relevance at scan time using modal language.
    """
    sig = dict(sig)
    sig["claim_type"] = "foresight_context"
    sig["company_specific"] = False
    sig["not_evidence_of_deployment"] = True
    sig["requires_company_specific_corroboration"] = True
    sig["modal_language_required"] = True
    return sig


_CENTRAL_KB_COMPANY_EVIDENCE_FIELDS = {
    "analysis_verified",
    "generation_eligible",
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "internal_only",
    "blocked",
    "buying_evidence_eligible",
    "entity_match_status",
    "content_entity_match_status",
    "entity_scope",
    "wrong_entity",
    "wrong_entity_true",
    "target_entity_verified",
    "claim_support_status",
    "company_name",
    "company_slug",
    "source_database",
    "scanner_origin",
}


_CENTRAL_KB_LEGACY_COMPANY_ELIGIBLE_STATUSES = {
    "generation_eligible",
    "checked_generation_eligible",
    "foresight_context_eligible",
    "checked_foresight_context_eligible",
}


def _central_kb_status(sig: dict) -> str:
    return str(sig.get("promotion_status") or sig.get("source_registry_status") or "").strip().lower()


def _legacy_signal_was_context_eligible(sig: dict) -> bool:
    if sig.get("foresight_context_eligible") is True:
        return True
    if _central_kb_status(sig) in _CENTRAL_KB_LEGACY_COMPANY_ELIGIBLE_STATUSES:
        return True
    return False


def _repair_central_kb_signal(sig: dict, theme: str, *, url_alive: bool | None = None) -> dict:
    """
    Repair a central KB signal to the foresight-context metadata contract.

    This is metadata-only: signals are retained for audit even when not currently
    eligible for scan-time foresight synthesis. Company scans only consume records
    with `foresight_context_eligible=True`. Company-name presence is deliberately
    not an eligibility condition: Repair verifies macro trend support, while any
    company-fit inference requires separate company-specific corroboration.
    """
    clean, _removed_contract_fields = normalize_central_foresight_signal(sig)
    for field in _CENTRAL_KB_COMPANY_EVIDENCE_FIELDS:
        clean.pop(field, None)
    for field in ("promotion_status", "source_registry_status", "claim_support_status"):
        clean.pop(field, None)

    source_url = (clean.get("source_url") or clean.get("url") or "").strip()
    clean["source_url"] = source_url
    clean["claim_type"] = "foresight_context"
    clean["company_specific"] = False
    clean["requires_company_specific_corroboration"] = True
    clean["not_evidence_of_deployment"] = True
    clean["modal_language_required"] = True
    clean["fp_theme"] = clean.get("fp_theme") or theme
    clean["central_kb_contract"] = "foresight_context"
    clean["central_kb_repaired_at"] = datetime.now(timezone.utc).isoformat()

    if url_alive is not None:
        clean["url_verified"] = bool(url_alive)
        clean["verified"] = bool(url_alive)

    if not source_url:
        clean["foresight_context_eligible"] = False
        clean["foresight_signal_status"] = "not_context_eligible"
        clean["source_audit_status"] = "missing_source_url"
        clean["source_readable"] = False
        clean["trend_supported"] = False
        clean["theme_match"] = clean.get("theme_match") is True
        clean["verification_reason"] = "missing_source_url"
        return clean

    if url_alive is False:
        clean["foresight_context_eligible"] = False
        clean["foresight_signal_status"] = "not_context_eligible"
        clean["source_audit_status"] = "source_dead"
        clean["source_readable"] = False
        clean["content_verified"] = False
        clean["trend_supported"] = False
        clean["theme_match"] = clean.get("theme_match") is True
        clean["verification_reason"] = "source_url_not_alive"
        return clean

    source_readable = (
        clean.get("source_readable") is True
        or clean.get("content_verified") is True
        or bool(clean.get("source_quote"))
        or bool(clean.get("description"))
        or bool(clean.get("summary"))
        or bool(clean.get("source_says"))
    )
    clean["source_readable"] = bool(source_readable)
    clean["content_verified"] = clean.get("content_verified") is True or bool(clean.get("source_quote"))
    clean["theme_match"] = clean.get("theme_match") is not False

    explicit_trend_supported = clean.get("trend_supported") is True
    explicitly_rejected = (
        clean.get("trend_supported") is False
        or str(clean.get("source_audit_status") or "").lower() in {"trend_not_supported", "theme_mismatch", "excluded"}
        or str(clean.get("foresight_signal_status") or "").lower() == "excluded"
    )
    inferred_trend_supported = (
        not explicitly_rejected
        and clean["source_readable"]
        and (_signal_has_context_text(clean) or bool(_signal_claim_text(clean)))
    )
    clean["trend_supported"] = bool(explicit_trend_supported or inferred_trend_supported)

    credibility = _foresight_source_credibility(clean)
    curated_source = _is_curated_theme_source(clean, theme)
    credible = credibility in {"high", "medium"} or curated_source
    eligible = bool(
        clean.get("url_verified") is True
        and clean["source_readable"]
        and clean["theme_match"]
        and clean["trend_supported"]
        and credible
    )

    clean["foresight_context_eligible"] = eligible
    clean["foresight_signal_status"] = ACTIVE_FORESIGHT_STATUS if eligible else "not_context_eligible"
    if eligible:
        clean["source_audit_status"] = "accepted_active"
        clean["verification_reason"] = clean.get("verification_reason") or "foresight_context_metadata_repaired"
    elif not clean["source_readable"]:
        clean["source_audit_status"] = "source_unreadable"
        clean["verification_reason"] = "source_not_readable_or_no_cached_quote"
    elif not clean["theme_match"]:
        clean["source_audit_status"] = "theme_mismatch"
        clean["verification_reason"] = "theme_match_not_established"
    elif not clean["trend_supported"]:
        clean["source_audit_status"] = "trend_not_supported"
        clean["verification_reason"] = "trend_support_not_established"
    elif not credible:
        clean["source_audit_status"] = "low_credibility"
        clean["verification_reason"] = "source_credibility_or_registry_fit_not_sufficient"
    else:
        clean["source_audit_status"] = "not_context_eligible"
        clean["verification_reason"] = clean.get("verification_reason") or "foresight_context_not_eligible"
    return clean


def format_kb_signals_for_prompt(signals: list[dict], max_signals: int = 8) -> str:
    """
    Format KB signals into a context block injected into the company-specific
    foresight prompt. Gives the model pre-validated evidence to synthesize against
    rather than searching from scratch.

    KB invariant: each signal is emitted as company-neutral context only.
    The model generates its own company-specific possible_relevance using modal
    language ("may", "could", "should evaluate", "if in scope").
    """
    if not signals:
        return ""

    selected = signals[:max_signals]
    lines = [
        "ANALYSIS-VERIFIED MEGATREND SIGNALS (from Foresight Knowledge Base):",
        "The following signals have reachable sources and content verified for analysis use.",
        "Use these as GROUNDING CONTEXT — synthesize their impact on this specific company.",
        "IMPORTANT: These are foresight_context signals (company_specific=False).",
        "  - Verified source quote is extracted source text.",
        "  - KB interpretation is a company-neutral scanner interpretation, not a quote.",
        "  - Where shown, 'Example hypothesis' is a starting-point hypothesis from the KB.",
        "    Build upon it, challenge it, or replace it — but generate your own assessment",
        "    using modal language (may / could / should evaluate / if in scope / if not",
        "    already completed).  Do NOT copy it verbatim.",
        "  - Do NOT treat these as proof of current company deployment or obligation.\n",
    ]

    for i, sig in enumerate(selected, 1):
        sig = _ensure_kb_signal_neutral(sig)
        theme = sig.get("_kb_theme", sig.get("fp_theme", ""))
        lines.append(f"Signal {i}: {sig.get('title', 'Unknown signal')}")
        lines.append(f"  Theme: {theme}")
        lines.append(f"  Type: {sig.get('signal_type', 'N/A')} | Timeframe: {sig.get('timeframe', 'N/A')}")
        source_quote = (sig.get("source_quote") or "").strip()
        if source_quote:
            lines.append(f"  Verified source quote: {source_quote[:420]}")
        source_interpretation = (sig.get("source_says") or sig.get("description") or "").strip()
        if source_interpretation:
            lines.append(f"  KB interpretation (not a quote): {source_interpretation[:320]}")
        semantic_snippet = (sig.get("semantic_snippet") or "").strip()
        if semantic_snippet:
            lines.append(f"  Semantic selection snippet: {semantic_snippet[:520]}")
        # Include KB possible_relevance as a starting-point hypothesis only — clearly labeled
        # so the LLM treats it as an example to build upon, challenge, or refine rather than
        # recycling it verbatim.  Modal language is still required in the LLM's own output.
        kb_pr = (sig.get("possible_relevance") or "").strip()
        if kb_pr:
            lines.append(f"  Example hypothesis (challenge, refine, or build upon — use modal language): {kb_pr[:200]}")
        lines.append("  Claim type: foresight_context | Company-specific: False | Use modal language only")
        lines.append(
            f"  Primary source: {sig.get('source_url', 'N/A')} "
            f"({sig.get('source_credibility', 'N/A')} credibility)"
        )
        n_supporting = len(sig.get("supporting_sources", []))
        lines.append(f"  Corroborating sources: {n_supporting}")
        if sig.get("industries_affected"):
            lines.append(f"  Industries affected: {', '.join(sig['industries_affected'])}")
        if sig.get("kb_dimensions_affected"):
            lines.append(f"  KB dimensions: {', '.join(sig['kb_dimensions_affected'])}")
        lines.append(f"  Confidence: {sig.get('confidence', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def cleanup_foresight_kb(progress_callback=None) -> dict:
    """
    Standalone central KB metadata repair.

    The central Foresight KB should contain only company-neutral
    foresight-context metadata. This pass is non-destructive by default:
    old company-evidence statuses such as display_only are replaced with
    foresight-specific statuses instead of deleting the signal.
    """
    from strategic_foresight import _verify_foresight_url
    storage = BlobStorage()

    all_kb = storage.get_all_foresight_kb()
    total_themes = len(all_kb)
    results = {
        "themes_checked": 0,
        "themes_cleaned": 0,
        "signals_before": 0,
        "signals_after": 0,
        "signals_removed": 0,
        "url_verified": 0,
        "content_verified": 0,
        "foresight_context_eligible": 0,
        "needs_review": 0,
        "not_context_eligible": 0,
        "rejected_audit": 0,
        "analysis_verified": 0,
        "display_only": 0,
        "excluded": 0,
        "display_only_removed": 0,
        "company_metadata_removed": 0,
        "metadata_repaired": 0,
        "source_dead": 0,
        "source_unreadable": 0,
        "themes_write_skipped": 0,
        "details": [],
    }

    print(f"\n{'='*70}")
    print(f"🧹 FORESIGHT KB VERIFY/REPAIR — {total_themes} themes")
    print(f"{'='*70}\n")

    for i, (slug, theme_data) in enumerate(all_kb.items()):
        theme_name = theme_data.get("theme", slug)
        signals = theme_data.get("signals", [])

        if progress_callback:
            progress_callback(theme_name, i, total_themes, "cleaning")

        if not signals:
            results["themes_checked"] += 1
            continue

        before_count = len(signals)
        results["signals_before"] += before_count

        checked_signals = []
        malformed_removed = 0
        metadata_removed = 0

        for sig in signals:
            if not isinstance(sig, dict):
                malformed_removed += 1
                continue

            source_url = (sig.get("source_url") or sig.get("url") or "").strip()
            url_alive = bool(sig.get("url_verified") is True or sig.get("verified") is True)
            if source_url and not url_alive:
                try:
                    url_alive = bool(_verify_foresight_url(source_url, 8))
                except Exception:
                    url_alive = False

            had_company_fields = any(field in sig for field in _CENTRAL_KB_COMPANY_EVIDENCE_FIELDS) or any(
                field in sig for field in ("promotion_status", "source_registry_status", "claim_support_status")
            )
            checked = dict(sig)
            if source_url:
                checked["source_url"] = source_url
                checked["url"] = source_url
                checked["url_verified"] = bool(url_alive)
                checked["verified"] = bool(url_alive)
                if not url_alive:
                    checked["source_audit_status"] = "source_dead"
            else:
                checked["source_audit_status"] = "missing_source_url"
            checked_signals.append(checked)
            if had_company_fields:
                metadata_removed += 1

        active_records, audit_records, split_stats = _split_active_and_audit(
            checked_signals,
            theme_name,
            stage="repair_kb",
        )
        repaired_records = active_records
        after_count = len(repaired_records)
        removed = malformed_removed
        all_repaired = active_records + audit_records
        url_verified = sum(1 for s in all_repaired if s.get("url_verified") is True or s.get("verified") is True)
        content_verified = sum(1 for s in all_repaired if s.get("content_verified") is True or s.get("source_readable") is True)
        context_eligible = len(active_records)
        needs_review = 0
        not_context_eligible = len(audit_records)
        source_dead = split_stats.get("dead", 0)
        source_unreadable = split_stats.get("unreadable", 0)
        rejected_audit = len(audit_records)
        metadata_repaired = sum(1 for repaired in all_repaired if repaired.get("foresight_evidence_version") == FORESIGHT_KB_EVIDENCE_VERSION)
        analysis_verified = 0
        display_only = 0
        excluded = sum(1 for s in audit_records if s.get("foresight_signal_status") == "excluded")
        results["signals_after"] += after_count
        results["signals_removed"] += removed
        results["url_verified"] += url_verified
        results["content_verified"] += content_verified
        results["foresight_context_eligible"] += context_eligible
        results["needs_review"] += needs_review
        results["not_context_eligible"] += not_context_eligible
        results["rejected_audit"] += rejected_audit
        results["analysis_verified"] += analysis_verified
        results["display_only"] += display_only
        results["excluded"] += excluded
        results["company_metadata_removed"] += metadata_removed
        results["metadata_repaired"] += metadata_repaired
        results["source_dead"] += source_dead
        results["source_unreadable"] += source_unreadable
        results["themes_checked"] += 1

        if audit_records:
            storage.save_foresight_kb_audit(slug, {
                "theme": theme_name,
                "theme_slug": slug,
                "candidates": audit_records[:100],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "audit_contract": "not_used_by_scanner_synthesis",
                "repair_stats": {
                    "before": before_count,
                    "active": after_count,
                    "rejected_audit": rejected_audit,
                    "source_dead": source_dead,
                    "source_unreadable": source_unreadable,
                    "not_curated": split_stats.get("not_curated", 0),
                    "off_theme": split_stats.get("off_theme", 0),
                    "low_credibility": split_stats.get("low_credibility", 0),
                    "snippet_too_short": split_stats.get("snippet_too_short", 0),
                },
                "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
            })

        if after_count == 0 and before_count > 0:
            results["themes_write_skipped"] += 1
            print(
                f"   ⚠️ [{i+1}/{total_themes}] {theme_name}: write skipped to avoid replacing "
                f"{before_count} signal(s) with an empty active file; audit={rejected_audit}"
            )
        elif removed > 0 or repaired_records != signals:
            results["themes_cleaned"] += 1
            # Save only active context signals back to the central KB.
            cleaned_data = {
                **theme_data,
                "signals": repaired_records,
                "last_cleanup": datetime.now(timezone.utc).isoformat(),
                "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
                "central_kb_contract": "active_foresight_context_only",
                "evidence_cleaning_stats": {
                    "before": before_count,
                    "after": after_count,
                    "removed": removed,
                    "source_dead": source_dead,
                    "source_unreadable": source_unreadable,
                    "company_metadata_removed": metadata_removed,
                    "metadata_repaired": metadata_repaired,
                    "foresight_context_eligible": context_eligible,
                    "rejected_audit": rejected_audit,
                    "not_context_eligible": not_context_eligible,
                },
                "validation": {
                    "total_signals": after_count,
                    "url_verified": url_verified,
                    "content_verified": content_verified,
                    "foresight_context_eligible": context_eligible,
                    "accepted_active": context_eligible,
                    "rejected_audit": rejected_audit,
                    "not_context_eligible": 0,
                    "source_dead": source_dead,
                    "source_unreadable": source_unreadable,
                    "excluded": excluded,
                },
            }
            storage.save_foresight_kb(slug, cleaned_data)
            print(
                f"   💾 [{i+1}/{total_themes}] {theme_name}: metadata repaired for "
                f"{metadata_repaired}/{before_count}; active {context_eligible}, "
                f"audit {rejected_audit}, dead marked {source_dead}"
            )
        else:
            print(f"   ✅ [{i+1}/{total_themes}] {theme_name}: {before_count} signals already clean")

        print(f"   🧭 Foresight KB Repair: {theme_name} — {context_eligible}/{before_count} active, audit {rejected_audit}")

        results["details"].append({
            "theme": theme_name,
            "slug": slug,
            "before": before_count,
            "after": after_count,
            "removed": removed,
            "url_verified": url_verified,
            "content_verified": content_verified,
            "foresight_context_eligible": context_eligible,
            "needs_review": needs_review,
            "not_context_eligible": not_context_eligible,
            "rejected_audit": rejected_audit,
            "analysis_verified": analysis_verified,
            "display_only": display_only,
            "excluded": excluded,
            "display_only_removed": 0,
            "dead_removed": 0,
            "source_dead": source_dead,
            "source_unreadable": source_unreadable,
            "company_metadata_removed": metadata_removed,
            "metadata_repaired": metadata_repaired,
            "write_skipped": after_count == 0 and before_count > 0,
        })

    print(f"\n{'='*70}")
    print(
        f"🧹 KB VERIFY/REPAIR COMPLETE: {results['metadata_repaired']} signal metadata repairs; "
        f"{results['foresight_context_eligible']} foresight-context eligible; "
        f"{results['rejected_audit']} moved to audit; "
        f"{results['themes_write_skipped']} active writes skipped"
    )
    print(f"{'='*70}\n")

    return results
