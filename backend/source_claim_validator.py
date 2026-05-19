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


# ── Single source-claim relevance check ──────────────────────────────────────

def _check_relevance(url: str, claim: str, client, timeout: int = 10) -> dict:
    """
    Fetch the source page and ask Gemini Flash whether it supports the claim.

    Returns dict with:
      - relevance_score: 0-100 (how well the source supports the claim)
      - relevant: bool (score >= threshold)
      - reason: short explanation
    """
    THRESHOLD = 25  # Below this = source doesn't support the claim at all

    page_text = _fetch_page_text(url)
    if not page_text or len(page_text) < 50:
        # Can't fetch content — don't penalise (might be JS-rendered, paywalled, etc.)
        return {"relevance_score": -1, "relevant": True, "reason": "Page content not extractable"}

    prompt = f"""You are a fact-checking assistant. A forensic scanner cited this source URL to support a specific claim. Your job: determine if the source page content actually supports or relates to the claim.

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

    try:
        resp = call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=100,
                ),
            ),
            label="Source-Claim Relevance",
            model="gemini-2.0-flash",
            timeout=timeout,
        )
        raw = (resp.text or "").strip()

        # Parse the JSON response
        import json
        # Handle markdown code blocks
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        score = int(result.get("score", 50))
        reason = result.get("reason", "")

        return {
            "relevance_score": score,
            "relevant": score >= THRESHOLD,
            "reason": reason,
        }
    except Exception as e:
        # On any failure, don't penalise — assume relevant
        return {"relevance_score": -1, "relevant": True, "reason": f"Validation error: {str(e)[:80]}"}


# ── Batch validation for evidence sets ───────────────────────────────────────

def validate_source_claims(
    findings: list,
    max_checks: int = 8,
    max_workers: int = 4,
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
    try:
        client = get_gemini_client()
    except ValueError:
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    # Select candidates: verified sources with snippets
    candidates = []
    for item in findings:
        if not item.get("verified", False):
            continue
        url = (item.get("url") or item.get("source_url") or "").strip()
        snippet = (item.get("snippet") or item.get("description") or "").strip()
        if url and snippet and url.startswith("http") and len(snippet) > 20:
            candidates.append((url, snippet, item))

    if not candidates:
        return {"checked": 0, "irrelevant": 0, "flagged_urls": [], "elapsed_ms": 0}

    # Limit checks for cost control
    to_check = candidates[:max_checks]

    start = time.time()
    results = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {}
        for url, snippet, item in to_check:
            future = executor.submit(_check_relevance, url, snippet, client)
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
                if not result["relevant"]:
                    item["claim_irrelevant"] = True

    elapsed_ms = int((time.time() - start) * 1000)

    checked = len(results)
    flagged = [url for url, r in results.items()
               if r["relevance_score"] >= 0 and not r["relevant"]]

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
