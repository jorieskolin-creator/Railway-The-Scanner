"""
foresight_harvester.py — Periodic harvester for the Foresight Knowledge Base.

Scans authoritative sources (FP_THEME_SOURCES) for megatrend signals,
validates them (URL verification + credibility assessment), and stores
validated findings in blob storage. These pre-validated signals are then
injected into per-company foresight generation, avoiding redundant searches.

Refresh-and-clean strategy:
  1. Load existing KB signals for the theme
  2. Re-verify existing URLs — remove any that have gone dead since last harvest
  3. Harvest new signals from GPT/Claude web-search models
  4. Deduplicate & merge — add new signals not already in the KB
  5. Cap at MAX_SIGNALS_PER_THEME, ranked by confidence + recency
  6. Store the merged result

This ensures the KB grows over time and a failed harvest never wipes good data.
"""

import json
import re
import time
import concurrent.futures
from datetime import datetime, timezone

from llm_clients import call_anthropic_text, call_openai_text
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
)
from storage import BlobStorage

MAX_SIGNALS_PER_THEME = 15
FORESIGHT_KB_EVIDENCE_VERSION = "foresight-context-v2"
FORESIGHT_CONTEXT_MIN_SCORE = 45
FORESIGHT_CONTEXT_PARTIAL_MIN_SCORE = 35
FORESIGHT_CONTEXT_SUPPORT_STATUSES = {"supported", "partial"}


def _theme_to_slug(theme: str) -> str:
    """Convert a theme name to a storage-safe slug."""
    slug = theme.lower()
    slug = slug.replace(" & ", "_and_").replace("&", "and")
    slug = re.sub(r'[^a-z0-9_]', '_', slug)
    slug = re.sub(r'_{2,}', '_', slug)
    return slug.strip('_')


def _signal_key(sig: dict) -> str:
    """
    Generate a deduplication key for a signal.
    Matches on source_url (exact) or title similarity (normalized).
    """
    url = (sig.get("source_url") or "").strip().rstrip("/").lower()
    if url:
        return url
    # Fallback: normalized title
    title = (sig.get("title") or "").strip().lower()
    title = re.sub(r'[^a-z0-9 ]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return f"title:{title}"


def _revalidate_existing_signals(existing_signals: list[dict], theme: str) -> list[dict]:
    """
    Re-verify URLs on existing KB signals. Remove any whose primary URL has gone dead.
    Returns the cleaned list of still-valid signals.
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
        return []

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

    alive = []
    dead = 0
    for sig in existing_signals:
        primary_url = sig.get("source_url", "")
        if url_status.get(primary_url, False):
            alive.append(sig)
        else:
            dead += 1

    print(f"   🧹 [Clean] {theme}: {len(alive)} existing alive, {dead} expired → removed")
    return alive


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
    sig["source_audit_status"] = "excluded" if excluded else "not_context_eligible"
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
            return score >= FORESIGHT_CONTEXT_PARTIAL_MIN_SCORE or (credible_source and score >= 25)
        return credible_source and score >= 25
    if credible_source and score >= FORESIGHT_CONTEXT_PARTIAL_MIN_SCORE and bool(quote):
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
    source_readable = result.get("content_verified") is True or bool(result.get("source_quote"))
    sig["source_readable"] = source_readable
    sig["content_verified"] = source_readable
    reason = result.get("reason") or sig.get("content_verification_reason") or ""
    sig["content_verification_reason"] = reason
    sig["content_match_reason"] = reason
    sig["verification_model"] = result.get("verification_model", sig.get("verification_model"))
    if result.get("source_quote"):
        sig["source_quote"] = result["source_quote"]

    eligible = _foresight_context_supported(sig, result)
    sig["foresight_context_eligible"] = eligible
    sig["theme_match"] = bool(source_readable and score >= 25 and raw_support != "unsupported")
    sig["trend_supported"] = eligible
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
        else ("claim_unsupported" if raw_support == "unsupported" else "foresight_context_not_supported")
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
    Merge new signals into existing KB signals:
      - Deduplicate by source_url or normalized title
      - New signals with the same key replace older ones (fresher data)
      - Cap at max_total, ranked by confidence then recency
    """
    # Index existing by key
    merged = {}
    for sig in existing:
        key = _signal_key(sig)
        merged[key] = sig

    # New signals overwrite duplicates (fresher), otherwise add
    added = 0
    updated = 0
    for sig in new_signals:
        key = _signal_key(sig)
        if key in merged:
            updated += 1
        else:
            added += 1
        merged[key] = sig  # new always wins (fresher)

    # Rank: high confidence first, then medium, then low; within same confidence, newest first
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    result = sorted(
        merged.values(),
        key=lambda s: (
            confidence_order.get(s.get("confidence", "low"), 3),
            -(s.get("time_ms") or 0),  # proxy for recency
        ),
    )

    capped = result[:max_total]
    if added or updated:
        print(f"   🔀 [Merge] {added} new, {updated} refreshed, {len(capped)} total (cap {max_total})")

    return capped


def _build_harvest_prompt(theme: str, search_queries: list[str]) -> str:
    """Build a web-research prompt to harvest megatrend signals for one FP theme."""

    queries_text = "\n".join(f"- {q}" for q in search_queries[:8])

    return f"""You are a Strategic Foresight Research Analyst specializing in megatrend signal detection.

TASK: Identify the 3-5 most significant current signals/developments for the megatrend theme "{theme}".

These signals must be:
- HORIZONTAL: Industry-agnostic macro forces (not company-specific)
- RECENT: From the last 12 months ({datetime.now().year}-{datetime.now().year + 1})
- VERIFIED: From authoritative sources you can cite with real URLs
- ACTIONABLE: Relevant to strategic planning for businesses affected by this theme

MANDATORY SOURCE TARGETING — Search these first:
{queries_text}

ALLOWED SOURCE DOMAINS (primary sources MUST come from these):
Tier 1 (intergovernmental/UN/flagship journals): ipcc.ch, who.int, iea.org, irena.org, imf.org, worldbank.org, oecd.org, nature.com, science.org, thelancet.com, bis.org, fao.org, ilo.org, unep.org, wto.org, unctad.org, unfccc.int, ipbes.net, iom.int, un.org, ec.europa.eu, wmo.int
Tier 2 (think tanks/academic): chathamhouse.org, rand.org, brookings.edu, carnegieendowment.org, csis.org, iiss.org, sipri.org, oxfordmartin.ox.ac.uk, turing.ac.uk, hai.stanford.edu, lse.ac.uk, oii.ox.ac.uk, bruegel.org, cser.ac.uk, belfercenter.org, piie.com
Tier 3 (specialist research/foresight): iftf.org, cifs.dk, soif.org.uk, futures.hawaii.edu, weforum.org, ellenmacarthurfoundation.org, wri.org, rmi.org, iucn.org, fair.work, sitra.fi, nesta.org.uk, c40.org, millennium-project.org, clubofrome.org, hybridcoe.fi, enisa.europa.eu
Tier 4 (commercial/supplementary): mckinsey.com, deloitte.com, gartner.com, forrester.com, bcg.com, pwc.com, hbr.org, reuters.com, ft.com, bloomberg.com, economist.com, idc.com, accenture.com

DO NOT USE: blogs, press release wires, social media, content farms, regional tech news, marketing sites.

SIFT PROTOCOL (mandatory):
1. STOP: Verify the signal is real, not marketing spin.
2. INVESTIGATE SOURCE: Is this independent research, not corporate PR?
3. FIND BETTER COVERAGE: Each signal needs 2+ independent corroborating sources.
4. TRACE CLAIMS: Link to ORIGINAL source, not derivative summaries.

For each signal, provide:
- "title": Concise name of the megatrend signal (specific, not vague)
- "description": 2-3 sentences explaining the signal and its strategic implications
- "signal_type": "Plausible/Maturing" | "Probable/Emerging" | "Weakening/Declining" | "Preposterous/Black Swan"
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "source_url": Primary URL from your web search (MUST be real, from allowed domains)
- "supporting_sources": Array of 1-2 additional corroborating URLs from different orgs. Each: {{"url": "https://...", "title": "Brief title"}}
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "confidence": "high" (3+ sources) | "medium" (2 sources) | "low" (1 source)
- "industries_affected": Array of 2-4 industry domains most impacted (e.g., ["Technology & Software", "Financial Services", "Healthcare"])
- "kb_dimensions_affected": Array of 1-3 KB dimensions impacted: "Operating Model", "Strategy", "Values", "Service Portfolio", "AI Readiness"
- "lifecycle_stage": Assess where this signal sits on the trend adoption S-curve. One of: "weak_signal" | "strong_signal" | "hype_cycle" | "valley_of_death" | "go_to_market" | "market_breakout" | "established_trend" | "market_saturation" | "weakening_trend" | "fading_signal". Assessment guide: only academic/R&D papers → weak_signal; multiple think-tank reports but no products → strong_signal; mainstream media hype but limited real adoption → hype_cycle; prior hype followed by failures/pullbacks → valley_of_death; real products/regulations launched → go_to_market; scaling adoption metrics → market_breakout; widespread adoption, industry standard → established_trend; market leaders consolidating, growth flattening → market_saturation; replacement technologies emerging → weakening_trend; near-zero new adoption → fading_signal
- "trend_momentum": "accelerating" (adoption rate increasing) | "stable" (steady pace) | "decelerating" (slowing down)

CRITICAL: Every URL must be real and retrieved from web search. Do NOT fabricate URLs.

RETURN STRICT JSON ONLY:
{{
  "theme": "{theme}",
  "signals": [
    {{
      "title": "...",
      "description": "...",
      "signal_type": "...",
      "timeframe": "...",
      "source_url": "https://...",
      "supporting_sources": [{{"url": "https://...", "title": "..."}}],
      "source_type": "...",
      "confidence": "...",
      "industries_affected": ["...", "..."],
      "kb_dimensions_affected": ["...", "..."],
      "lifecycle_stage": "weak_signal|strong_signal|hype_cycle|valley_of_death|go_to_market|market_breakout|established_trend|market_saturation|weakening_trend|fading_signal",
      "trend_momentum": "accelerating|stable|decelerating"
    }}
  ]
}}"""


def _harvest_single_theme(theme: str, search_queries: list[str]) -> dict | None:
    """Harvest megatrend signals for one FP theme using Claude/GPT web search."""

    prompt = _build_harvest_prompt(theme, search_queries)

    models = [
        ("claude-sonnet-4-6", "anthropic", 120),
        ("gpt-5.5", "openai", 120),
    ]

    for model_name, provider, timeout_sec in models:
        try:
            print(f"   🌐 [Harvest] {theme} → {model_name}...")
            t0 = time.time()
            if provider == "anthropic":
                text = call_anthropic_text(
                    prompt,
                    model=model_name,
                    max_tokens=7000,
                    timeout=timeout_sec,
                    web_search=True,
                    max_searches=10,
                    thinking_budget=3000,
                )
            else:
                text = call_openai_text(
                    prompt,
                    model=model_name,
                    max_output_tokens=7000,
                    timeout=timeout_sec,
                    web_search=True,
                    search_context_size="medium",
                )
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 80:
                print(f"   ⚠️ [Harvest] {theme}: {model_name} insufficient data ({len(text) if text else 0} chars, {elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if not json_str:
                print(f"   ⚠️ [Harvest] {theme}: could not extract JSON ({elapsed}ms)")
                continue

            data = json.loads(json_str, strict=False)

            if not data.get("signals"):
                print(f"   ⚠️ [Harvest] {theme}: no signals in response ({elapsed}ms)")
                continue

            data["model"] = model_name
            data["time_ms"] = elapsed
            data["theme"] = theme
            n = len(data["signals"])
            print(f"   ✅ [Harvest] {theme}: {n} signals ({elapsed}ms)")
            return data

        except json.JSONDecodeError as je:
            elapsed = int((time.time() - t0) * 1000)
            print(f"   ⚠️ [Harvest] {theme}: JSON parse failed ({elapsed}ms): {je}")
            continue
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Harvest] {theme}: {model_name} failed ({elapsed}ms): {e}")
            continue

    print(f"   ❌ [Harvest] {theme}: all models failed — skipped")
    return None


def _validate_harvest_signals(data: dict) -> dict:
    """
    Validate harvested signals: verify URLs, assess credibility, remove bad ones.
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

        sig["validated"] = sig["url_verified"] and sig["source_count"] >= 2

    # Mark all harvested signals as external foresight context. They are not company-specific facts.
    data["signals"] = [sanitize_foresight_record(annotate_foresight_signal(s, theme=data.get("theme", ""))) for s in data["signals"]]

    # Remove signals with no verified primary URL
    data["signals"] = [s for s in data["signals"] if s.get("url_verified")]

    data["validation"] = {
        "verified_urls": verified_count,
        "dead_urls": dead_count,
        "signals_after_validation": len(data["signals"]),
    }

    print(f"   🔍 [Validate] {data.get('theme', '?')}: {verified_count} verified, {dead_count} dead → {len(data['signals'])} signals kept")
    return data


def _verify_signal_content(data: dict, max_checks: int | None = None, max_workers: int = 4) -> dict:
    """
    Backward-compatible wrapper for the stricter KB evidence-promotion step.
    """
    return _promote_foresight_signals_for_analysis(data, max_checks=max_checks, max_workers=max_workers)


def harvest_all_themes(
    themes: list[str] | None = None,
    max_concurrent: int = 2,
    progress_callback=None,
) -> dict:
    """
    Harvest megatrend signals for all (or selected) FP themes.

    Args:
        themes: List of theme names to harvest. None = all 30.
        max_concurrent: Reserved for future parallel harvests; current flow is sequential to avoid provider rate limits.
        progress_callback: Optional fn(theme, index, total, status) for progress tracking.

    Returns dict with harvest results and summary.
    """
    storage = BlobStorage()

    target_themes = themes or FP_THEMES_30
    total = len(target_themes)
    results = {"harvested": 0, "failed": 0, "total_signals": 0, "themes": [], "started_at": datetime.now(timezone.utc).isoformat()}

    print(f"\n{'='*70}")
    print(f"🌐 FORESIGHT KB HARVEST — {total} themes")
    print(f"{'='*70}\n")

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

        print(f"\n--- [{i+1}/{total}] {theme} ({len(search_queries)} queries) ---")

        # Step 1: Load & clean existing KB signals for this theme
        existing_data = storage.get_foresight_kb(slug)
        existing_before_count = len((existing_data or {}).get("signals", []))
        existing_signals = []
        if existing_data and existing_data.get("signals"):
            existing_signals = _revalidate_existing_signals(existing_data["signals"], theme)
            promoted_existing = _promote_foresight_signals_for_analysis({
                "theme": theme,
                "signals": existing_signals,
            })
            existing_signals = [
                s for s in promoted_existing.get("signals", [])
                if _is_kb_signal_context_eligible(s)
            ]

        # Step 2: Harvest new signals
        data = _harvest_single_theme(theme, search_queries)

        new_validated_signals = []
        if data and data.get("signals"):
            # Step 3: Validate new harvest (URL liveness + credibility)
            data = _validate_harvest_signals(data)
            # Step 3b: Content verification (signal description vs source page)
            if data and data.get("signals"):
                data = _verify_signal_content(data)
            new_validated_signals = [
                s for s in (data.get("signals", []) if data else [])
                if _is_kb_signal_context_eligible(s)
            ]

        # Step 4: Merge — combine cleaned existing + validated new
        merged_signals = _merge_signals(existing_signals, new_validated_signals)

        if not merged_signals:
            results["failed"] += 1
            status = "no_valid_signals" if (data and data.get("signals") is not None) else "failed"
            results["themes"].append({"theme": theme, "slug": slug, "status": status, "signals": 0})
            print(f"   ⚠️ [Result] {theme}: no signals after merge — skipped")
            continue

        # Step 5: Build merged data envelope and store
        merged_data = {
            "theme": theme,
            "theme_slug": slug,
            "signals": merged_signals,
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "model": data.get("model", (existing_data or {}).get("model", "unknown")) if data else (existing_data or {}).get("model", "unknown"),
            "merge_stats": {
                "existing_before_clean": existing_before_count,
                "existing_after_clean": len(existing_signals),
                "new_harvested": len(new_validated_signals),
                "merged_total": len(merged_signals),
                "storage_contract": "foresight_context_only",
            },
            "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
        }

        url = storage.save_foresight_kb(slug, merged_data)
        n_signals = len(merged_signals)
        results["harvested"] += 1
        results["total_signals"] += n_signals
        results["themes"].append({
            "theme": theme,
            "slug": slug,
            "status": "ok",
            "signals": n_signals,
            "high_confidence": sum(1 for s in merged_signals if s.get("confidence") == "high"),
            "stored_url": url,
            "existing_kept": len(existing_signals),
            "new_added": len(new_validated_signals),
        })

        print(f"   💾 [Store] {theme}: {n_signals} signals saved to foresight_kb/{slug}")

        # Rate limit courtesy: pause between themes to avoid web-search provider quota hits
        if i < total - 1:
            time.sleep(2)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*70}")
    print(f"🌐 HARVEST COMPLETE: {results['harvested']}/{total} themes, {results['total_signals']} signals")
    print(f"{'='*70}\n")

    return results


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
    promotion = str(sig.get("promotion_status") or "").lower()
    return (
        context_safe
        and sig.get("foresight_context_eligible") is True
        and promotion not in {"excluded", "checked_excluded"}
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
                not_eligible = validation.get("not_context_eligible", validation.get("display_only", 0))
                excluded = validation.get("excluded", 0)
                print(
                    f"   ⚠️ [Foresight KB] {theme}: {eligible}/{total} foresight-context eligible "
                    f"(not context eligible {not_eligible}, excluded {excluded})"
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
    with `foresight_context_eligible=True`.
    """
    clean = dict(sig)
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
    )
    clean["source_readable"] = bool(source_readable)
    clean["content_verified"] = clean.get("content_verified") is True or bool(clean.get("source_quote"))
    clean["theme_match"] = clean.get("theme_match") is not False

    legacy_eligible = _legacy_signal_was_context_eligible(sig)
    explicit_trend_supported = clean.get("trend_supported") is True
    inferred_trend_supported = legacy_eligible and clean.get("trend_supported") is not False
    clean["trend_supported"] = bool(explicit_trend_supported or inferred_trend_supported)

    credibility = _foresight_source_credibility(clean)
    credible = credibility in {"high", "medium"}
    eligible = bool(
        clean.get("url_verified") is True
        and clean["source_readable"]
        and clean["theme_match"]
        and clean["trend_supported"]
        and credible
        and legacy_eligible
    )

    clean["foresight_context_eligible"] = eligible
    clean["foresight_signal_status"] = "context_eligible" if eligible else "not_context_eligible"
    if eligible:
        clean["source_audit_status"] = "accepted"
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
        clean["verification_reason"] = "source_credibility_too_low_for_context"
    else:
        clean["source_audit_status"] = "not_context_eligible"
        clean["verification_reason"] = clean.get("verification_reason") or "legacy_record_not_previously_context_eligible"
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
        "not_context_eligible": 0,
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

        repaired_signals = []
        malformed_removed = 0
        metadata_removed = 0
        metadata_repaired = 0

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
            repaired = _repair_central_kb_signal(sig, theme_name, url_alive=url_alive if source_url else None)
            repaired_signals.append(repaired)
            if had_company_fields or repaired != sig:
                metadata_removed += 1
                metadata_repaired += 1

        repaired_records = repaired_signals
        after_count = len(repaired_records)
        removed = malformed_removed
        url_verified = sum(1 for s in repaired_records if s.get("url_verified") is True or s.get("verified") is True)
        content_verified = sum(1 for s in repaired_records if s.get("content_verified") is True or s.get("source_readable") is True)
        context_eligible = sum(1 for s in repaired_records if s.get("foresight_context_eligible") is True)
        not_context_eligible = sum(1 for s in repaired_records if s.get("foresight_signal_status") == "not_context_eligible")
        source_dead = sum(1 for s in repaired_records if s.get("source_audit_status") == "source_dead")
        source_unreadable = sum(1 for s in repaired_records if s.get("source_audit_status") == "source_unreadable")
        analysis_verified = 0
        display_only = 0
        excluded = sum(1 for s in repaired_records if s.get("foresight_signal_status") == "excluded")
        results["signals_after"] += after_count
        results["signals_removed"] += removed
        results["url_verified"] += url_verified
        results["content_verified"] += content_verified
        results["foresight_context_eligible"] += context_eligible
        results["not_context_eligible"] += not_context_eligible
        results["analysis_verified"] += analysis_verified
        results["display_only"] += display_only
        results["excluded"] += excluded
        results["company_metadata_removed"] += metadata_removed
        results["metadata_repaired"] += metadata_repaired
        results["source_dead"] += source_dead
        results["source_unreadable"] += source_unreadable
        results["themes_checked"] += 1

        if after_count == 0 and before_count > 0:
            results["themes_write_skipped"] += 1
            print(
                f"   ⚠️ [{i+1}/{total_themes}] {theme_name}: write skipped to avoid replacing "
                f"{before_count} signal(s) with an empty repaired file"
            )
        elif removed > 0 or repaired_records != signals:
            results["themes_cleaned"] += 1
            # Save the cleaned data back
            cleaned_data = {
                **theme_data,
                "signals": repaired_records,
                "last_cleanup": datetime.now(timezone.utc).isoformat(),
                "foresight_evidence_version": FORESIGHT_KB_EVIDENCE_VERSION,
                "central_kb_contract": "foresight_context_only",
                "evidence_cleaning_stats": {
                    "before": before_count,
                    "after": after_count,
                    "removed": removed,
                    "source_dead": source_dead,
                    "source_unreadable": source_unreadable,
                    "company_metadata_removed": metadata_removed,
                    "metadata_repaired": metadata_repaired,
                    "foresight_context_eligible": context_eligible,
                    "not_context_eligible": not_context_eligible,
                },
                "validation": {
                    "total_signals": after_count,
                    "url_verified": url_verified,
                    "content_verified": content_verified,
                    "foresight_context_eligible": context_eligible,
                    "not_context_eligible": not_context_eligible,
                    "source_dead": source_dead,
                    "source_unreadable": source_unreadable,
                    "excluded": excluded,
                },
            }
            storage.save_foresight_kb(slug, cleaned_data)
            print(
                f"   💾 [{i+1}/{total_themes}] {theme_name}: metadata repaired for "
                f"{metadata_repaired}/{before_count}; context-eligible {context_eligible}, "
                f"not eligible {not_context_eligible}, dead marked {source_dead}"
            )
        else:
            print(f"   ✅ [{i+1}/{total_themes}] {theme_name}: {before_count} signals already clean")

        print(f"   🧭 Foresight KB Repair: {theme_name} — {context_eligible}/{after_count} context eligible")

        results["details"].append({
            "theme": theme_name,
            "slug": slug,
            "before": before_count,
            "after": after_count,
            "removed": removed,
            "url_verified": url_verified,
            "content_verified": content_verified,
            "foresight_context_eligible": context_eligible,
            "not_context_eligible": not_context_eligible,
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
        f"{results['not_context_eligible']} retained as not context eligible"
    )
    print(f"{'='*70}\n")

    return results
