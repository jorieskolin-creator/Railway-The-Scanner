"""
source_claim_validator.py — Validates that sources actually support their claimed evidence.

After URL verification confirms a source is alive, this module fetches the page content
and uses GPT/Claude to assess whether the source actually supports the specific claim
(snippet) it's attached to. Sources that are alive but irrelevant get flagged.

This fills the gap between URL liveness (source exists) and source credibility (domain
authority) — a source can be alive, high-credibility, and still say nothing related to
the claim it's attached to.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from urllib.parse import urlparse

import requests as http_requests
from bs4 import BeautifulSoup

from entity_identity import assess_entity_match
from llm_clients import call_json_model_chain


# ── Page content extraction ─────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"
}

_MAX_PAGE_CHARS = 3000  # Keep page text compact for the LLM prompt
_DEFAULT_FETCH_TIMEOUT = 12
_ANALYSIS_SUPPORT_THRESHOLD = 55
_PARTIAL_SUPPORT_THRESHOLD = 40
DEFAULT_MODEL_CHAIN = ("gpt-5.4", "claude-sonnet-4-6")
CLEAN_EVIDENCE_MODEL_CHAIN = ("gpt-5.4", "claude-sonnet-4-6", "gpt-5.5")


def _fetch_page_text(url: str, timeout: int = _DEFAULT_FETCH_TIMEOUT) -> str:
    """Fetch a URL and extract readable text content. Returns empty string on failure."""
    try:
        resp = http_requests.get(url, headers=_HEADERS, timeout=timeout,
                                 allow_redirects=True, stream=False)
        if resp.status_code >= 400:
            return ""
        html = resp.text[:50_000]  # cap raw HTML to avoid memory issues
    except Exception:
        return ""

    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "noscript", "iframe", "svg"]):
            tag.decompose()

        parts = []
        # Meta description
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            parts.append(meta["content"])
        # Headings
        for h in soup.find_all(["h1", "h2", "h3"]):
            txt = h.get_text(strip=True)
            if len(txt) > 5:
                parts.append(txt)
        # Paragraphs
        for p in soup.find_all("p"):
            txt = p.get_text(strip=True)
            if len(txt) > 30:
                parts.append(txt)

        text = " ".join(parts)
        return text[:_MAX_PAGE_CHARS]
    except Exception:
        return ""


def _best_source_quote(page_text: str, claim: str, max_chars: int = 420) -> str:
    """Select a compact source-text sentence that best overlaps the claim."""
    if not page_text:
        return ""
    claim_terms = {
        t.lower()
        for t in re.findall(r"[A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9\-]{2,}", claim or "")
    }
    if not claim_terms:
        return page_text[:max_chars].strip()

    sentences = re.split(r"(?<=[.!?])\s+", page_text)
    best = ""
    best_score = -1
    for sentence in sentences:
        cleaned = " ".join(sentence.split())
        if len(cleaned) < 40:
            continue
        terms = {
            t.lower()
            for t in re.findall(r"[A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9\-]{2,}", cleaned)
        }
        score = len(claim_terms & terms)
        if score > best_score:
            best_score = score
            best = cleaned
    return (best or page_text[:max_chars]).strip()[:max_chars]


# ── Single source-claim relevance check ──────────────────────────────────────

def _clean_env_var(value: str) -> str:
    if not value:
        return ""
    if "=" in value:
        try:
            value = value.split("=", 1)[1]
        except Exception:
            pass
    return value.strip().strip('"').strip("'")


def _get_openai_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    parts = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_text = getattr(content, "text", None)
            if content_text:
                parts.append(content_text)
    return "\n".join(parts).strip()


def _host_only_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return url


def _domain_from_any(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = parsed.netloc or parsed.path.split("/", 1)[0]
        return host.replace("www.", "").strip()
    except Exception:
        return raw.replace("www.", "").split("/", 1)[0].strip()


def _is_target_domain(url: str, company_domain: str) -> bool:
    source_host = _domain_from_any(url)
    target_host = _domain_from_any(company_domain)
    if not source_host or not target_host:
        return False
    return source_host == target_host or source_host.endswith(f".{target_host}")


def _is_high_value_source(source_item: dict | None, url: str, company_domain: str) -> bool:
    if not isinstance(source_item, dict):
        return _is_target_domain(url, company_domain)
    credibility = str(source_item.get("source_credibility") or "").lower()
    tier = str(source_item.get("claim_tier") or "").upper()
    source_type = str(source_item.get("source_type") or source_item.get("origin") or "").lower()
    official_types = {
        "company_report", "annual_report", "financial_report", "quarterly_report",
        "company_press_release", "investor_report", "company_website",
        "company_news", "company_filing", "official_company_source",
        "financial_data_aggregator",
    }
    return (
        credibility == "high"
        or tier in {"A", "B", "C"}
        or source_type in official_types
        or _is_target_domain(url, company_domain)
    )


def _content_entity_match(url: str, page_text: str, company_name: str, company_domain: str) -> dict:
    """Assess target-entity evidence using only resolved host + extracted page text.

    Scanner snippets, titles, and URL paths are intentionally excluded here. They
    are useful audit context, but they must not prove that page content describes
    the scanned company.
    """
    return assess_entity_match(
        {"url": _host_only_url(url), "title": "", "snippet": "", "description": "", "source_quote": ""},
        company_name,
        company_domain,
        page_text=page_text,
    )


def _reason_indicates_no_support(reason: str, *, context_only: bool = False) -> bool:
    text = (reason or "").lower()
    if context_only:
        # A macro source is allowed to omit the scanned company. Only language
        # that rejects the trend/force itself is a verification conflict.
        conflict_markers = (
            "unrelated",
            "does not support",
            "doesn't support",
            "not support",
            "no evidence",
            "cannot verify the trend",
            "cannot verify the claim",
        )
    else:
        conflict_markers = (
            "does not mention",
            "doesn't mention",
            "does not include",
            "no mention",
            "not mention",
            "not the target",
            "wrong entity",
            "different entity",
            "different company",
            "appears to be for",
            "rather than",
            "unrelated",
            "does not support",
            "doesn't support",
            "not support",
            "no evidence",
            "cannot verify",
        )
    return any(marker in text for marker in conflict_markers)


def _numeric_tokens(text: str) -> set[str]:
    tokens = set()
    for match in re.findall(r"\d+(?:[.,]\d+)?(?:\s?%|[mkb]|\s?m€|\s?eur)?", text or "", flags=re.IGNORECASE):
        cleaned = re.sub(r"\s+", "", match.lower())
        if cleaned:
            tokens.add(cleaned)
    return tokens


def _quote_value_overlap(claim: str, quote: str, page_text: str) -> bool:
    claim_numbers = _numeric_tokens(claim)
    if not claim_numbers:
        return True
    source_numbers = _numeric_tokens(" ".join([quote or "", page_text or ""]))
    if not source_numbers:
        return False
    required = 1 if len(claim_numbers) <= 2 else 2
    return len(claim_numbers & source_numbers) >= required


def _claim_has_numeric_values(claim: str) -> bool:
    return bool(_numeric_tokens(claim))


def _is_context_only_source(source_item: dict | None) -> bool:
    if not isinstance(source_item, dict):
        return False
    if source_item.get("company_specific") is True:
        return False
    source_type = str(source_item.get("source_type") or source_item.get("origin") or "").lower()
    claim_type = str(source_item.get("claim_type") or "").lower()
    scope = str(source_item.get("scope") or source_item.get("entity_scope") or "").lower()
    allowed = [str(x).lower() for x in source_item.get("allowed_claim_types") or []]
    return (
        source_type in {
            "foresight", "foresight_report", "trend_report", "regulation",
            "regulatory_context", "analyst", "analyst_report", "market_report",
            "industry_report", "research_report", "think_tank", "policy_report",
        }
        or claim_type in {"foresight_context", "regulatory_context"}
        or scope == "external-trend"
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
    )


def _build_relevance_prompt(claim: str, page_text: str, company_name: str = "", company_domain: str = "", context_only: bool = False) -> str:
    target = company_name or "the scanned company"
    domain = company_domain or "unknown domain"
    scope_rule = (
        "This source is external trend/regulatory/market context. It does NOT need to mention the target company, but it must support the external trend claim and must not be described as company-specific proof."
        if context_only else
        "This is company-specific evidence. The extracted source text must identify the target company, its official domain, a known legal entity/subsidiary/former name, or an explicitly scoped parent/owner context."
    )
    evidence_kind = "company-neutral foresight context" if context_only else "company-specific evidence"
    quote_rule = (
        "A supporting_quote must be copied from the source and support the macro trend, force, direction, or regulatory context. It does not need to name the target company."
        if context_only else
        "A supporting_quote must be copied from the source and contain the target/scoped entity name or exact values that prove the claim."
    )
    return f"""You are a strict evidence verifier. A forensic scanner cited this source URL as {evidence_kind}.

TARGET COMPANY:
"{target}"

TARGET DOMAIN:
"{domain}"

CLAIM (from the scanner's evidence):
"{claim}"

SOURCE PAGE CONTENT (extracted text):
"{page_text[:2000]}"

Score 0-100:
- 0-24: Source has NO relation to the claim (completely irrelevant topic)
- 25-49: Source is tangentially related but doesn't directly support the claim
- 50-74: Source discusses the same topic and partially supports the claim
- 75-100: Source directly supports or corroborates the claim

Entity rule:
- {scope_rule}
- A live page about a different same-name company is not support.
- {quote_rule}
- For foresight context, judge only whether the source supports the macro claim. Do not infer that the target company has acted, deployed anything, or is directly exposed.

Respond with ONLY a JSON object, no other text:
{{"score": <0-100>, "claim_supported": <true|false>, "entity_match_status": "exact|scoped|weak|wrong|unknown", "entity_name_found": "<entity name or empty>", "supporting_quote": "<short exact source excerpt or empty>", "unsupported_reason": "<why unsupported or empty>", "reason": "<one sentence explanation>"}}"""


def _parse_relevance_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(raw)
    score = max(0, min(100, int(result.get("score", 50))))
    reason = str(result.get("reason", "") or result.get("unsupported_reason", "") or "").strip()
    claim_supported = result.get("claim_supported")
    if isinstance(claim_supported, str):
        claim_supported = claim_supported.strip().lower() in {"true", "yes", "supported"}
    elif claim_supported is not None:
        claim_supported = bool(claim_supported)
    return {
        "score": score,
        "reason": reason,
        "claim_supported": claim_supported,
        "entity_match_status": str(result.get("entity_match_status") or "").strip().lower(),
        "entity_name_found": str(result.get("entity_name_found") or "").strip(),
        "supporting_quote": str(result.get("supporting_quote") or "").strip(),
        "unsupported_reason": str(result.get("unsupported_reason") or "").strip(),
    }


def _score_to_status(score: int) -> tuple[bool, str]:
    if score >= _ANALYSIS_SUPPORT_THRESHOLD:
        return True, "supported"
    if score >= _PARTIAL_SUPPORT_THRESHOLD:
        return False, "partial"
    return False, "unsupported"


def _evaluate_parsed_relevance(
    parsed: dict,
    *,
    model_used: str,
    claim: str,
    page_text: str,
    content_entity_match: dict,
    content_entity_status: str,
    display_entity_match: dict,
    context_only: bool,
) -> dict:
    score = parsed["score"]
    reason = parsed["reason"]
    relevant, support_status = _score_to_status(score)
    model_support_status = support_status
    model_supporting_quote = parsed.get("supporting_quote") or ""
    supporting_quote = model_supporting_quote or _best_source_quote(page_text, claim)
    entity_verified = content_entity_status in {"exact", "scoped"} or context_only
    model_claim_supported = parsed.get("claim_supported")
    if model_claim_supported is False:
        support_status = "unsupported"
        relevant = False

    conflict_reason = reason or parsed.get("unsupported_reason", "")
    verification_conflict = _reason_indicates_no_support(
        conflict_reason,
        context_only=context_only,
    )
    quote_mismatch = not _quote_value_overlap(claim, supporting_quote, page_text)
    if (not entity_verified and not context_only) or verification_conflict or quote_mismatch:
        support_status = "unsupported"
        relevant = False
        if not reason:
            reason = "Source content did not safely verify the target entity and claim."
        if not entity_verified and not context_only:
            reason = f"Target entity not verified in extracted source content: {content_entity_match.get('entity_match_reason', 'target entity not detected')}"
        elif quote_mismatch:
            reason = "Supporting quote/source content does not contain the target entity or claimed values."

    return {
        "relevance_score": score,
        "relevant": relevant,
        "claim_support_status": support_status,
        "model_claim_support_status": model_support_status,
        "model_claim_supported": model_claim_supported,
        "content_verified": True,
        "source_quote": supporting_quote,
        "model_supporting_quote": model_supporting_quote,
        "reason": reason,
        "verification_model": model_used,
        "entity_verified": entity_verified,
        "entity_name_found": parsed.get("entity_name_found") or "",
        "content_entity_match_status": content_entity_status,
        "content_entity_match_reason": content_entity_match.get("entity_match_reason", ""),
        "verification_conflict": verification_conflict,
        "quote_mismatch": quote_mismatch,
        **display_entity_match,
    }


def _deterministic_support_safe(result: dict, claim: str, page_text: str, context_only: bool) -> bool:
    if not result or (result.get("wrong_entity") is True and not context_only):
        return False
    if result.get("verification_conflict") is True or result.get("quote_mismatch") is True:
        return False
    quote = result.get("source_quote") or ""
    if not quote.strip():
        return False
    if _claim_has_numeric_values(claim) and not _quote_value_overlap(claim, quote, page_text):
        return False
    content_entity_status = str(result.get("content_entity_match_status") or "").lower()
    if context_only:
        return content_entity_status != "wrong"
    return result.get("entity_verified") is True and content_entity_status in {"exact", "scoped"}


def _needs_second_opinion(
    result: dict,
    *,
    source_item: dict | None,
    url: str,
    company_domain: str,
    context_only: bool,
) -> tuple[bool, str]:
    model = str(result.get("verification_model") or "")
    if not model.startswith("gpt-"):
        return False, ""

    content_entity_status = str(result.get("content_entity_match_status") or "").lower()
    model_supported = (
        result.get("model_claim_support_status") == "supported"
        or result.get("model_claim_supported") is True
    )
    suspicious_supported = model_supported and (
        not (result.get("model_supporting_quote") or "").strip()
        or result.get("quote_mismatch") is True
        or result.get("verification_conflict") is True
        or (content_entity_status in {"", "unknown", "weak"} and not context_only)
    )
    if suspicious_supported:
        return True, "gpt_supported_but_evidence_controls_are_weak"

    support_status = str(result.get("claim_support_status") or "").lower()
    high_value = _is_high_value_source(source_item, url, company_domain)
    if high_value and result.get("content_verified") is True and support_status in {"unsupported", "unavailable", "unchecked"}:
        return True, "high_value_source_not_supported_by_primary"

    return False, ""


def _merge_second_opinion(
    primary: dict,
    secondary: dict,
    *,
    review_reason: str,
    claim: str,
    page_text: str,
    context_only: bool,
) -> dict:
    primary_supported = primary.get("claim_support_status") == "supported"
    secondary_supported = secondary.get("claim_support_status") == "supported"
    deterministic_primary = _deterministic_support_safe(primary, claim, page_text, context_only)
    deterministic_secondary = _deterministic_support_safe(secondary, claim, page_text, context_only)
    disagreement = primary_supported != secondary_supported

    if primary_supported and secondary_supported and deterministic_primary:
        merged = dict(primary)
    elif secondary_supported and deterministic_secondary:
        merged = dict(secondary)
    elif primary_supported and deterministic_primary and not disagreement:
        merged = dict(primary)
    else:
        merged = dict(primary)
        merged["claim_support_status"] = "unsupported"
        merged["relevant"] = False
        if disagreement:
            merged["reason"] = (
                "Verifier disagreement; source kept display-only unless deterministic "
                "entity, quote, and claim checks prove support."
            )

    merged["verification_reviewed_by"] = secondary.get("verification_model")
    merged["verification_review_reason"] = review_reason
    merged["verification_disagreement"] = disagreement
    merged["primary_verification_model"] = primary.get("verification_model")
    merged["secondary_verification_model"] = secondary.get("verification_model")
    if secondary.get("source_quote") and not merged.get("source_quote"):
        merged["source_quote"] = secondary["source_quote"]
    return merged


def _check_relevance(
    url: str,
    claim: str,
    client=None,
    timeout: int = 10,
    model_chain=None,
    *,
    company_name: str = "",
    company_domain: str = "",
    source_item: dict | None = None,
) -> dict:
    """
    Fetch the source page and ask GPT/Claude whether it supports the claim.

    Returns dict with:
      - relevance_score: 0-100 (how well the source supports the claim)
      - relevant: bool (score >= analysis support threshold)
      - claim_support_status: supported / partial / unsupported / unchecked / unavailable
      - content_verified: bool (readable page text was extracted)
      - source_quote: compact source-text excerpt selected from extracted content
      - reason: short explanation
    """
    page_text = _fetch_page_text(url)
    display_entity_match = assess_entity_match(
        source_item or {"url": url, "snippet": claim},
        company_name,
        company_domain,
        page_text=page_text,
    )
    content_entity_match = _content_entity_match(url, page_text, company_name, company_domain)
    content_entity_status = str(content_entity_match.get("entity_match_status") or "unknown").lower()
    context_only = _is_context_only_source(source_item)

    if not page_text or len(page_text) < 50:
        # URL liveness is not enough for analysis.  Keep the source visible, but
        # mark it as unavailable so generation paths do not promote it.
        return {
            "relevance_score": -1,
            "relevant": False,
            "claim_support_status": "unavailable",
            "content_verified": False,
            "source_quote": "",
            "reason": "Page content not extractable",
            "entity_verified": False,
            "content_entity_match_status": "unknown",
            "content_entity_match_reason": "page_content_not_extractable",
            **display_entity_match,
        }

    if content_entity_status == "wrong" and not context_only:
        return {
            "relevance_score": 0,
            "relevant": False,
            "claim_support_status": "unsupported",
            "content_verified": True,
            "source_quote": _best_source_quote(page_text, claim),
            "reason": f"Wrong entity: {content_entity_match.get('entity_match_reason', 'target entity not matched')}",
            "entity_verified": False,
            "content_entity_match_status": content_entity_status,
            "content_entity_match_reason": content_entity_match.get("entity_match_reason", ""),
            "verification_conflict": True,
            "quote_mismatch": True,
            **display_entity_match,
        }

    prompt = _build_relevance_prompt(claim, page_text, company_name, company_domain, context_only)
    errors = []
    primary_result = None
    for model in model_chain or DEFAULT_MODEL_CHAIN:
        try:
            raw, model_used = call_json_model_chain(prompt, (model,), max_tokens=320, timeout=timeout)

            parsed = _parse_relevance_json(raw)
            primary_result = _evaluate_parsed_relevance(
                parsed,
                model_used=model_used,
                claim=claim,
                page_text=page_text,
                content_entity_match=content_entity_match,
                content_entity_status=content_entity_status,
                display_entity_match=display_entity_match,
                context_only=context_only,
            )
            review_needed, review_reason = _needs_second_opinion(
                primary_result,
                source_item=source_item,
                url=url,
                company_domain=company_domain,
                context_only=context_only,
            )
            if review_needed and model_used != "claude-sonnet-4-6":
                try:
                    raw2, model_used2 = call_json_model_chain(
                        prompt,
                        ("claude-sonnet-4-6",),
                        max_tokens=320,
                        timeout=timeout,
                    )
                    parsed2 = _parse_relevance_json(raw2)
                    secondary_result = _evaluate_parsed_relevance(
                        parsed2,
                        model_used=model_used2,
                        claim=claim,
                        page_text=page_text,
                        content_entity_match=content_entity_match,
                        content_entity_status=content_entity_status,
                        display_entity_match=display_entity_match,
                        context_only=context_only,
                    )
                    return _merge_second_opinion(
                        primary_result,
                        secondary_result,
                        review_reason=review_reason,
                        claim=claim,
                        page_text=page_text,
                        context_only=context_only,
                    )
                except Exception as review_exc:
                    primary_result["verification_review_reason"] = review_reason
                    primary_result["verification_review_error"] = str(review_exc)[:160]
                    primary_result["primary_verification_model"] = model_used
                    return primary_result

            primary_result["primary_verification_model"] = model_used
            return primary_result
        except Exception as e:
            errors.append(f"{model}: {str(e)[:80]}")

    return {
        "relevance_score": -1,
        "relevant": False,
        "claim_support_status": "unchecked",
        "content_verified": True,
        "source_quote": _best_source_quote(page_text, claim),
        "reason": "Verifier failed: " + " | ".join(errors)[:240],
        "verification_model": "none",
        "entity_verified": context_only or content_entity_status in {"exact", "scoped"},
        "content_entity_match_status": content_entity_status,
        "content_entity_match_reason": content_entity_match.get("entity_match_reason", ""),
        **display_entity_match,
    }


# ── Batch validation for evidence sets ───────────────────────────────────────

def validate_source_claims(
    findings: list,
    max_checks: int = 8,
    max_workers: int = 4,
    model_chain=None,
    company_name: str = "",
    company_domain: str = "",
    time_budget_seconds: int | None = None,
) -> dict:
    """
    Validate that verified evidence sources actually support their claimed snippets.

    Only checks sources that:
    - Are verified (URL is alive)
    - Have both a URL and a non-empty snippet

    Args:
        findings: list of evidence dicts (with url, snippet, verified fields)
        max_checks: max number of sources to validate (cost control)
        max_workers: parallel workers for page fetching + LLM calls

    Returns dict:
    {
        "checked": int,          # how many sources were checked
        "irrelevant": int,       # how many scored below threshold
        "flagged_urls": [str],   # URLs that failed relevance
        "elapsed_ms": int,
    }
    """
    client = None

    def _candidate_priority(item: dict) -> tuple:
        cred = str(item.get("source_credibility") or "").lower()
        tier = str(item.get("claim_tier") or "").upper()
        cred_rank = {"high": 0, "medium": 1, "unknown": 2, "low": 3}.get(cred, 2)
        tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}.get(tier, 3)
        snippet_len = len((item.get("snippet") or item.get("description") or "").strip())
        return (cred_rank, tier_rank, -snippet_len)

    # Select candidates: verified sources with snippets
    candidates = []
    for item in sorted([f for f in findings if isinstance(f, dict)], key=_candidate_priority):
        if not item.get("verified", False):
            continue
        url = (item.get("url") or item.get("source_url") or "").strip()
        snippet = (item.get("snippet") or item.get("description") or "").strip()
        if url and snippet and url.startswith("http") and len(snippet) > 20:
            candidates.append((url, snippet, item))

    if not candidates:
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    # Limit checks for cost control when requested.  Admin evidence cleaning
    # passes max_checks=None so every current stored source can be migrated.
    to_check = candidates if max_checks is None else candidates[:max_checks]

    start = time.time()
    results = {}
    deadline = time.time() + max(1, int(time_budget_seconds)) if time_budget_seconds else None
    timed_out_pairs = 0

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    future_to_item = {}
    try:
        for url, snippet, item in to_check:
            if deadline and time.time() >= deadline:
                timed_out_pairs += 1
                continue
            future = executor.submit(
                _check_relevance,
                url,
                snippet,
                client,
                min(10, max(3, int((deadline - time.time()) if deadline else 10))),
                model_chain,
                company_name=company_name,
                company_domain=company_domain,
                source_item=item,
            )
            future_to_item[future] = (url, snippet, item)

        as_completed_timeout = max(1, deadline - time.time()) if deadline else None
        try:
            completed_iter = concurrent.futures.as_completed(
                future_to_item,
                timeout=as_completed_timeout,
            )
            for future in completed_iter:
                if deadline and time.time() >= deadline:
                    break
                url, snippet, item = future_to_item[future]
                try:
                    result = future.result(timeout=1)
                except Exception:
                    result = {"relevance_score": -1, "relevant": True, "reason": "Timeout"}
                results[url] = result

                # Annotate the finding with relevance data
                if result["relevance_score"] >= 0:
                    item["claim_relevance"] = result["relevance_score"]
                item["content_relevance_score"] = result["relevance_score"]
                support_status = result.get("claim_support_status", "unchecked")
                if support_status == "unsupported":
                    support_status = "unsupported_for_this_claim"
                item["claim_support_status"] = support_status
                item["claim_relation_status"] = support_status
                item["content_verified"] = result.get("content_verified") is True
                item["content_verification_reason"] = result.get("reason", "")
                item["verification_model"] = result.get("verification_model", item.get("verification_model"))
                for key in (
                    "entity_match_status", "entity_match_reason", "canonical_source_host",
                    "content_entity_match_status", "content_entity_match_reason",
                    "entity_verified", "entity_name_found", "verification_conflict",
                    "quote_mismatch", "verification_reviewed_by", "verification_review_reason",
                    "verification_disagreement", "primary_verification_model",
                    "secondary_verification_model", "verification_review_error",
                ):
                    if key in result and result.get(key) not in (None, ""):
                        item[key] = result[key]
                if result.get("entity_scope") and not item.get("entity_scope"):
                    item["entity_scope"] = result["entity_scope"]
                if result.get("entity_match_status") == "wrong":
                    item["wrong_entity"] = True
                    item["entity_match"] = "wrong"
                if result.get("content_entity_match_status") == "wrong":
                    item["wrong_entity"] = True
                    item["entity_match"] = "wrong"
                if result.get("source_quote"):
                    item["source_quote"] = result["source_quote"]
                entity_status = str(result.get("content_entity_match_status") or result.get("entity_match_status") or "").lower()
                if item.get("wrong_entity") is True or entity_status == "wrong":
                    item["source_context_status"] = "wrong_entity"
                    item["claim_irrelevant"] = True
                elif item.get("content_verified") is not True and support_status in {"unavailable", "unchecked"}:
                    item["source_context_status"] = "dead_or_unreadable"
                elif support_status == "supported":
                    item["source_context_status"] = "valid_company_context"
                    item["claim_irrelevant"] = False
                elif entity_status in {"exact", "scoped"} or result.get("entity_verified") is True:
                    item["source_context_status"] = "valid_company_context"
                    item["claim_irrelevant"] = False
                else:
                    item["source_context_status"] = "out_of_context"
        except concurrent.futures.TimeoutError:
            pass
    finally:
        pending = [future for future in future_to_item if not future.done()]
        timed_out_pairs += len(pending)
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    # Mark unstarted/uncompleted pairs as partial instead of letting a late
    # background check mutate evidence after postprocess metrics are finalized.
    checked_urls = set(results)
    for url, _snippet, item in to_check:
        if url in checked_urls:
            continue
        item.setdefault("claim_support_status", "claim_support_pending")
        item.setdefault("claim_relation_status", "claim_support_pending")
        item["source_claim_validation_partial"] = True
        item.setdefault("source_context_status", "valid_company_context")

    elapsed_ms = int((time.time() - start) * 1000)

    checked = len(results)
    claim_failed = [url for url, r in results.items()
                    if r["relevance_score"] >= 0 and r.get("claim_support_status") == "unsupported"]
    wrong_entity = [
        url for url, r in results.items()
        if r.get("entity_match_status") == "wrong" or r.get("content_entity_match_status") == "wrong"
    ]
    entity_failed = [
        url for url, r in results.items()
        if r.get("entity_verified") is False
        or str(r.get("content_entity_match_status") or "").lower() in {"wrong", "weak", "unknown"}
    ]
    quote_mismatch = [
        url for url, r in results.items()
        if r.get("quote_mismatch") is True or r.get("verification_conflict") is True
    ]

    source_context_failed = sorted(set(wrong_entity + entity_failed))

    partial = len(results) < len(to_check)
    if partial:
        print(
            f"   ⚠️ [Source-Claim] partial validation: checked {checked}/{len(to_check)}, "
            f"timed_out_pairs={max(timed_out_pairs, len(to_check) - checked)} ({elapsed_ms}ms)"
        )
    if claim_failed:
        print(f"   ⚠️ [Source-Claim] {len(claim_failed)}/{checked} source/claim relation(s) unsupported ({elapsed_ms}ms)")
        for url in claim_failed:
            reason = results[url].get("reason", "")
            score = results[url].get("relevance_score", "?")
            print(f"      🔴 {url} (score: {score}/100) — {reason}")
    elif checked > 0 and not partial:
        print(f"   ✅ [Source-Claim] {checked}/{checked} verified sources support their claims ({elapsed_ms}ms)")

    return {
        "checked": checked,
        "irrelevant": len(source_context_failed),
        "wrong_entity": len(wrong_entity),
        "entity_failed": len(entity_failed),
        "claim_failed": len(claim_failed),
        "quote_mismatch": len(quote_mismatch),
        "flagged_urls": claim_failed,
        "source_context_failed": source_context_failed,
        "partial": partial,
        "timed_out_pairs": max(timed_out_pairs, len(to_check) - checked),
        "elapsed_ms": elapsed_ms,
    }


def validate_foresight_claims(
    foresight_data: dict,
    max_checks: int = 6,
    *,
    company_name: str = "",
    company_domain: str = "",
) -> dict:
    """
    Validate source-claim relevance for foresight dimension forces.

    Foresight forces use different field names:
    - source_url (instead of url)
    - force_name + summary (instead of snippet)
    - url_verified (instead of verified)

    Adapts the force data to use the shared _check_relevance logic.
    Returns same dict format as validate_source_claims.
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    forces = foresight_data["dimension_forces"]
    candidates = []
    for force in forces:
        if not force.get("url_verified", False):
            continue
        url = (force.get("source_url") or "").strip()
        # Validate what the macro source says, never the separate company-fit
        # interpretation attached by the scan.
        claim = (
            force.get("source_says")
            or force.get("semantic_snippet")
            or force.get("trend")
            or force.get("summary")
            or force.get("force_name")
            or ""
        ).strip()
        if url and claim and url.startswith("http") and len(claim) > 20:
            candidates.append((url, claim, force))

    if not candidates:
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    to_check = candidates[:max_checks]
    start = time.time()
    results = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        future_to_item = {}
        for url, claim, force in to_check:
            future = executor.submit(
                _check_relevance,
                url,
                claim,
                None,
                company_name=company_name,
                company_domain=company_domain,
                source_item={
                    "url": url,
                    "title": force.get("trend") or force.get("force_name") or "",
                    "snippet": claim,
                    "source_type": force.get("source_type", "foresight"),
                    "origin": "foresight",
                    "claim_type": "foresight_context",
                    "company_specific": False,
                    "scope": "external-trend",
                },
            )
            future_to_item[future] = (url, claim, force)

        for future in concurrent.futures.as_completed(future_to_item):
            url, claim, force = future_to_item[future]
            try:
                result = future.result(timeout=15)
            except Exception:
                result = {"relevance_score": -1, "relevant": True, "reason": "Timeout"}
            results[url] = result

            if result["relevance_score"] >= 0:
                force["claim_relevance"] = result["relevance_score"]
            force_support_status = result.get("claim_support_status", force.get("claim_support_status", "unchecked"))
            if force_support_status == "unsupported":
                force_support_status = "unsupported_for_this_claim"
            force["claim_support_status"] = force_support_status
            force["claim_relation_status"] = force_support_status
            force["source_context_status"] = "valid_foresight_context"
            force["content_verified"] = result.get("content_verified") is True
            force["content_relevance_score"] = result.get("relevance_score", force.get("content_relevance_score"))
            force["content_verification_reason"] = result.get("reason", "")
            if result.get("source_quote"):
                force["source_quote"] = result["source_quote"]
            for key in (
                "verification_conflict", "quote_mismatch",
                "verification_reviewed_by", "verification_review_reason",
                "verification_disagreement", "primary_verification_model",
                "secondary_verification_model", "verification_review_error",
            ):
                if key in result and result.get(key) not in (None, ""):
                    force[key] = result[key]
            # Foresight verification has its own contract. A macro source is not
            # rejected because it omits the company, and it is never promoted as
            # proof of company action or deployment.
            supported = result.get("claim_support_status") == "supported"
            for key in (
                "wrong_entity", "wrong_entity_true", "entity_match_status",
                "entity_match_reason", "content_entity_match_status",
                "content_entity_match_reason", "entity_scope", "entity_verified",
                "entity_name_found", "canonical_source_host", "generation_eligible",
                "analysis_verified", "promotion_status",
            ):
                force.pop(key, None)
            force["claim_type"] = "foresight_context"
            force["company_specific"] = False
            force["requires_company_specific_corroboration"] = True
            force["not_evidence_of_deployment"] = True
            force["modal_language_required"] = True
            force["source_readable"] = result.get("content_verified") is True
            force["trend_supported"] = supported
            force["trend_support_status"] = (
                "supported" if supported else result.get("claim_support_status", "unchecked")
            )
            force["foresight_context_eligible"] = bool(
                force.get("url_verified") is True
                and force["source_readable"]
                and supported
                and force.get("theme_match", True) is not False
            )
            force["foresight_signal_status"] = (
                "context_eligible" if force["foresight_context_eligible"] else "not_context_eligible"
            )
            force["source_audit_status"] = (
                "accepted" if force["foresight_context_eligible"] else "trend_not_supported"
            )
            force["verification_reason"] = (
                "macro_source_supports_foresight_claim; company_fit_requires_separate_corroboration"
                if force["foresight_context_eligible"]
                else result.get("reason") or "macro_source_did_not_support_foresight_claim"
            )

    elapsed_ms = int((time.time() - start) * 1000)
    checked = len(results)
    flagged = [url for url, r in results.items()
               if r["relevance_score"] >= 0 and not r["relevant"]]

    if flagged:
        print(f"   ⚠️ [Foresight Source-Claim] {len(flagged)}/{checked} sources don't support their claims ({elapsed_ms}ms)")
    elif checked > 0:
        print(f"   ✅ [Foresight Source-Claim] {checked}/{checked} sources support their claims ({elapsed_ms}ms)")

    return {
        "checked": checked,
        "irrelevant": len(flagged),
        "flagged_urls": flagged,
        "elapsed_ms": elapsed_ms,
    }
