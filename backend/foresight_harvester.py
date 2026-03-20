"""
foresight_harvester.py — Periodic harvester for the Foresight Knowledge Base.

Scans authoritative sources (FP_THEME_SOURCES) for megatrend signals,
validates them (URL verification + credibility assessment), and stores
validated findings in blob storage. These pre-validated signals are then
injected into per-company foresight generation, avoiding redundant searches.

Refresh-and-clean strategy:
  1. Load existing KB signals for the theme
  2. Re-verify existing URLs — remove any that have gone dead since last harvest
  3. Harvest new signals from Gemini
  4. Deduplicate & merge — add new signals not already in the KB
  5. Cap at MAX_SIGNALS_PER_THEME, ranked by confidence + recency
  6. Store the merged result

This ensures the KB grows over time and a failed harvest never wipes good data.
"""

import json
import os
import re
import time
import concurrent.futures
from datetime import datetime, timezone

from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from source_quality import HIGH_CREDIBILITY_DOMAINS, assess_source_credibility
from strategic_foresight import (
    FP_THEMES_30, FP_THEME_SOURCES, THEME_FALLBACK_MAP,
    _verify_foresight_url, _extract_json_from_text,
)
from storage import BlobStorage

MAX_SIGNALS_PER_THEME = 15


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
    """Build a Gemini prompt to harvest megatrend signals for one FP theme."""

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


def _harvest_single_theme(theme: str, client, search_queries: list[str]) -> dict | None:
    """Harvest megatrend signals for one FP theme using Gemini + Google Search."""

    prompt = _build_harvest_prompt(theme, search_queries)

    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.4,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2000),
            max_output_tokens=6000,
        ), 90),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.4,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
            max_output_tokens=6000,
        ), 60),
    ]

    for model_name, config, timeout_sec in models:
        try:
            print(f"   🌐 [Harvest] {theme} → {model_name}...")
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Harvest {theme} {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
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

    # Remove signals with no verified primary URL
    data["signals"] = [s for s in data["signals"] if s.get("url_verified")]

    data["validation"] = {
        "verified_urls": verified_count,
        "dead_urls": dead_count,
        "signals_after_validation": len(data["signals"]),
    }

    print(f"   🔍 [Validate] {data.get('theme', '?')}: {verified_count} verified, {dead_count} dead → {len(data['signals'])} signals kept")
    return data


def harvest_all_themes(
    themes: list[str] | None = None,
    max_concurrent: int = 2,
    progress_callback=None,
) -> dict:
    """
    Harvest megatrend signals for all (or selected) FP themes.

    Args:
        themes: List of theme names to harvest. None = all 30.
        max_concurrent: Max parallel Gemini calls (keep low to avoid rate limits).
        progress_callback: Optional fn(theme, index, total, status) for progress tracking.

    Returns dict with harvest results and summary.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return {"error": "No GEMINI_API_KEY configured", "harvested": 0}

    client = genai.Client(api_key=gemini_key)
    storage = BlobStorage()

    target_themes = themes or FP_THEMES_30
    total = len(target_themes)
    results = {"harvested": 0, "failed": 0, "total_signals": 0, "themes": [], "started_at": datetime.now(timezone.utc).isoformat()}

    print(f"\n{'='*70}")
    print(f"🌐 FORESIGHT KB HARVEST — {total} themes")
    print(f"{'='*70}\n")

    for i, theme in enumerate(target_themes):
        slug = _theme_to_slug(theme)
        search_queries = FP_THEME_SOURCES.get(theme, [])

        if progress_callback:
            progress_callback(theme, i, total, "harvesting")

        print(f"\n--- [{i+1}/{total}] {theme} ({len(search_queries)} queries) ---")

        # Step 1: Load & clean existing KB signals for this theme
        existing_data = storage.get_foresight_kb(slug)
        existing_signals = []
        if existing_data and existing_data.get("signals"):
            existing_signals = _revalidate_existing_signals(existing_data["signals"], theme)

        # Step 2: Harvest new signals
        data = _harvest_single_theme(theme, client, search_queries)

        new_validated_signals = []
        if data and data.get("signals"):
            # Step 3: Validate new harvest
            data = _validate_harvest_signals(data)
            new_validated_signals = data.get("signals", []) if data else []

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
                "existing_before_clean": len((existing_data or {}).get("signals", [])),
                "existing_after_clean": len(existing_signals),
                "new_harvested": len(new_validated_signals),
                "merged_total": len(merged_signals),
            },
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

        # Rate limit courtesy: pause between themes to avoid Gemini quota hits
        if i < total - 1:
            time.sleep(2)

    results["completed_at"] = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*70}")
    print(f"🌐 HARVEST COMPLETE: {results['harvested']}/{total} themes, {results['total_signals']} signals")
    print(f"{'='*70}\n")

    return results


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
                if sig.get("validated") or sig.get("confidence") in ("high", "medium"):
                    sig["_from_kb"] = True
                    sig["_kb_theme"] = theme
                    signals_found.append(sig)

        # Near-fit fallback: if no signals for this theme, try related themes
        if not signals_found:
            fallback_themes = THEME_FALLBACK_MAP.get(theme, [])
            for fallback_theme in fallback_themes:
                fallback_slug = _theme_to_slug(fallback_theme)
                fallback_data = storage.get_foresight_kb(fallback_slug)
                if fallback_data and fallback_data.get("signals"):
                    for sig in fallback_data["signals"]:
                        if sig.get("validated") or sig.get("confidence") in ("high", "medium"):
                            sig["_from_kb"] = True
                            sig["_kb_theme"] = theme  # Tag with the originally requested theme
                            sig["_kb_fallback_from"] = fallback_theme  # Track substitution source
                            signals_found.append(sig)
                    if signals_found:
                        print(f"   📚 [KB Near-Fit] No data for '{theme}' → using '{fallback_theme}' ({len(signals_found)} signals)")
                        break  # Use the first fallback that has data

        all_signals.extend(signals_found)

    # Sort: high confidence first, then medium, then low
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    all_signals.sort(key=lambda s: confidence_order.get(s.get("confidence", "low"), 3))

    return all_signals


def format_kb_signals_for_prompt(signals: list[dict], max_signals: int = 8) -> str:
    """
    Format KB signals into a context block that can be injected into the
    company-specific foresight prompt. This gives Gemini pre-validated evidence
    to synthesize against, rather than searching from scratch.
    """
    if not signals:
        return ""

    selected = signals[:max_signals]
    lines = [
        "PRE-VALIDATED MEGATREND SIGNALS (from Foresight Knowledge Base):",
        "The following signals have been independently verified with 2+ authoritative sources.",
        "Use these as GROUNDING CONTEXT — synthesize their impact on this specific company.",
        "Do NOT search for additional sources — these signals are pre-validated and sufficient.\n",
    ]

    for i, sig in enumerate(selected, 1):
        theme = sig.get("_kb_theme", sig.get("fp_theme", ""))
        lines.append(f"Signal {i}: {sig['title']}")
        lines.append(f"  Theme: {theme}")
        lines.append(f"  Type: {sig.get('signal_type', 'N/A')} | Timeframe: {sig.get('timeframe', 'N/A')}")
        lines.append(f"  Description: {sig.get('description', '')}")
        lines.append(f"  Primary source: {sig.get('source_url', 'N/A')} ({sig.get('source_credibility', 'N/A')} credibility)")
        n_supporting = len(sig.get("supporting_sources", []))
        lines.append(f"  Corroborating sources: {n_supporting}")
        if sig.get("industries_affected"):
            lines.append(f"  Industries affected: {', '.join(sig['industries_affected'])}")
        if sig.get("kb_dimensions_affected"):
            lines.append(f"  KB dimensions: {', '.join(sig['kb_dimensions_affected'])}")
        lines.append(f"  Confidence: {sig.get('confidence', 'N/A')}")
        lines.append("")

    return "\n".join(lines)
