"""
source_claim_validator.py — Validates that sources actually support their claimed evidence.

After URL verification confirms a source is alive, this module fetches the page content
and uses Gemini Flash to assess whether the source actually supports the specific claim
(snippet) it's attached to. Sources that are alive but irrelevant get flagged.

This fills the gap between URL liveness (source exists) and source credibility (domain
authority) — a source can be alive, high-credibility, and still say nothing related to
the claim it's attached to.
"""

import concurrent.futures
from datetime import datetime
import json
import os
import re
import time

import requests as http_requests
from bs4 import BeautifulSoup
from google.genai import types as genai_types

from gemini_client import get_gemini_client
from gemini_backoff import call_with_503_backoff


# ── Page content extraction ─────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AdaptabilityScanner/1.0)"
}

_MAX_PAGE_CHARS = 3000  # Keep page text compact for the LLM prompt
_ANALYSIS_SUPPORT_THRESHOLD = 55
_PARTIAL_SUPPORT_THRESHOLD = 40
DEFAULT_MODEL_CHAIN = ("gemini-2.0-flash",)
CLEAN_EVIDENCE_MODEL_CHAIN = ("gpt-5.4", "gemini-2.5-pro", "gemini-3-flash-preview")


def _fetch_page_text(url: str, timeout: int = 6) -> str:
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


def _build_relevance_prompt(claim: str, page_text: str) -> str:
    return f"""You are a fact-checking assistant. A forensic scanner cited this source URL to support a specific claim. Your job: determine if the source page content actually supports or relates to the claim.

CLAIM (from the scanner's evidence):
"{claim}"

SOURCE PAGE CONTENT (extracted text):
"{page_text[:2000]}"

Score 0-100:
- 0-24: Source has NO relation to the claim (completely irrelevant topic)
- 25-49: Source is tangentially related but doesn't directly support the claim
- 50-74: Source discusses the same topic and partially supports the claim
- 75-100: Source directly supports or corroborates the claim

Respond with ONLY a JSON object, no other text:
{{"score": <0-100>, "reason": "<one sentence explanation>"}}"""


def _parse_relevance_json(raw: str) -> tuple[int, str]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(raw)
    score = max(0, min(100, int(result.get("score", 50))))
    reason = str(result.get("reason", "") or "").strip()
    return score, reason


def _call_openai_relevance(prompt: str, model: str, timeout: int) -> str:
    api_key = _clean_env_var(os.environ.get("GPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise ValueError("No GPT_API_KEY configured")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=timeout)
    response = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=200,
    )
    return _get_openai_text(response)


def _call_gemini_relevance(prompt: str, model: str, timeout: int):
    client = get_gemini_client()
    config_kwargs = {"temperature": 0.0, "max_output_tokens": 140}
    if model == "gemini-2.5-pro":
        # Gemini 2.5 Pro rejects a zero thinking budget, so keep a tiny budget
        # for deterministic classification while avoiding token starvation.
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=256)
    resp = call_with_503_backoff(
        lambda: client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(**config_kwargs),
        ),
        label=f"Source-Claim Relevance {model}",
        model=model,
        timeout=timeout,
    )
    return (resp.text or "").strip()


def _score_to_status(score: int) -> tuple[bool, str]:
    if score >= _ANALYSIS_SUPPORT_THRESHOLD:
        return True, "supported"
    if score >= _PARTIAL_SUPPORT_THRESHOLD:
        return False, "partial"
    return False, "unsupported"


def _check_relevance(url: str, claim: str, client=None, timeout: int = 10, model_chain=None) -> dict:
    """
    Fetch the source page and ask Gemini Flash whether it supports the claim.

    Returns dict with:
      - relevance_score: 0-100 (how well the source supports the claim)
      - relevant: bool (score >= analysis support threshold)
      - claim_support_status: supported / partial / unsupported / unchecked / unavailable
      - content_verified: bool (readable page text was extracted)
      - source_quote: compact source-text excerpt selected from extracted content
      - reason: short explanation
    """
    page_text = _fetch_page_text(url)
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
        }

    prompt = _build_relevance_prompt(claim, page_text)
    errors = []
    for model in model_chain or DEFAULT_MODEL_CHAIN:
        try:
            if str(model).startswith("gpt-"):
                raw = _call_openai_relevance(prompt, model, timeout)
            elif model == "gemini-2.0-flash" and client is not None:
                resp = call_with_503_backoff(
                    lambda: client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=genai_types.GenerateContentConfig(
                            temperature=0.0,
                            max_output_tokens=100,
                        ),
                    ),
                    label="Source-Claim Relevance",
                    model=model,
                    timeout=timeout,
                )
                raw = (resp.text or "").strip()
            else:
                raw = _call_gemini_relevance(prompt, model, timeout)

            score, reason = _parse_relevance_json(raw)
            relevant, support_status = _score_to_status(score)

            return {
                "relevance_score": score,
                "relevant": relevant,
                "claim_support_status": support_status,
                "content_verified": True,
                "source_quote": _best_source_quote(page_text, claim),
                "reason": reason,
                "verification_model": model,
            }
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
    }


# ── Batch validation for evidence sets ───────────────────────────────────────

def validate_source_claims(
    findings: list,
    max_checks: int = 8,
    max_workers: int = 4,
    model_chain=None,
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
    if model_chain is None or "gemini-2.0-flash" in model_chain:
        try:
            client = get_gemini_client()
        except ValueError:
            if model_chain is None:
                return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {}
        for url, snippet, item in to_check:
            future = executor.submit(_check_relevance, url, snippet, client, 10, model_chain)
            future_to_item[future] = (url, snippet, item)

        for future in concurrent.futures.as_completed(future_to_item):
            url, snippet, item = future_to_item[future]
            try:
                result = future.result(timeout=15)
            except Exception:
                result = {"relevance_score": -1, "relevant": True, "reason": "Timeout"}
            results[url] = result

            # Annotate the finding with relevance data
            if result["relevance_score"] >= 0:
                item["claim_relevance"] = result["relevance_score"]
            item["content_relevance_score"] = result["relevance_score"]
            item["claim_support_status"] = result.get("claim_support_status", "unchecked")
            item["content_verified"] = result.get("content_verified") is True
            item["content_verification_reason"] = result.get("reason", "")
            item["verification_model"] = result.get("verification_model", item.get("verification_model"))
            if result.get("source_quote"):
                item["source_quote"] = result["source_quote"]
            if result["relevant"]:
                item["analysis_verified_at"] = datetime.utcnow().isoformat()
            elif result.get("claim_support_status") == "unsupported":
                item["claim_irrelevant"] = True

    elapsed_ms = int((time.time() - start) * 1000)

    checked = len(results)
    flagged = [url for url, r in results.items()
               if r["relevance_score"] >= 0 and r.get("claim_support_status") == "unsupported"]

    if flagged:
        print(f"   ⚠️ [Source-Claim] {len(flagged)}/{checked} verified sources don't support their claims ({elapsed_ms}ms)")
        for url in flagged:
            reason = results[url].get("reason", "")
            score = results[url].get("relevance_score", "?")
            print(f"      🔴 {url} (score: {score}/100) — {reason}")
    elif checked > 0:
        print(f"   ✅ [Source-Claim] {checked}/{checked} verified sources support their claims ({elapsed_ms}ms)")

    return {
        "checked": checked,
        "irrelevant": len(flagged),
        "flagged_urls": flagged,
        "elapsed_ms": elapsed_ms,
    }


def validate_foresight_claims(foresight_data: dict, max_checks: int = 6) -> dict:
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

    try:
        client = get_gemini_client()
    except ValueError:
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    forces = foresight_data["dimension_forces"]
    candidates = []
    for force in forces:
        if not force.get("url_verified", False):
            continue
        url = (force.get("source_url") or "").strip()
        claim = (force.get("summary") or force.get("force_name") or "").strip()
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
            future = executor.submit(_check_relevance, url, claim, client)
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
                if not result["relevant"]:
                    force["claim_irrelevant"] = True

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
