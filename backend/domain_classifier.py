"""
domain_classifier.py — Industry domain inference from scan evidence.

Used by both main.py and seed_manager.py. Kept separate to avoid circular
imports (main imports seed_manager; seed_manager must not import main).
"""

import os
import time

from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff


KNOWN_DOMAINS = [
    "Technology & Software", "IT Services & Consulting", "Healthcare",
    "Retail & Consumer", "Industrial & Manufacturing", "Energy & Utilities",
    "Financial Services", "Insurance & Pensions", "Forest & Paper",
    "Telecommunications", "Construction & Real Estate",
    "Transport & Logistics", "Media & Publishing", "Other",
]

# Lowercase lookup for fuzzy matching
_DOMAIN_LOOKUP = {d.lower(): d for d in KNOWN_DOMAINS}

# Common aliases / alternate phrasings → canonical domain
_DOMAIN_ALIASES = {
    "technology": "Technology & Software",
    "software": "Technology & Software",
    "tech": "Technology & Software",
    "it services": "IT Services & Consulting",
    "it consulting": "IT Services & Consulting",
    "consulting": "IT Services & Consulting",
    "health": "Healthcare",
    "medical": "Healthcare",
    "pharma": "Healthcare",
    "retail": "Retail & Consumer",
    "consumer": "Retail & Consumer",
    "food": "Retail & Consumer",
    "restaurant": "Retail & Consumer",
    "fmcg": "Retail & Consumer",
    "industrial": "Industrial & Manufacturing",
    "manufacturing": "Industrial & Manufacturing",
    "energy": "Energy & Utilities",
    "utilities": "Energy & Utilities",
    "oil": "Energy & Utilities",
    "finance": "Financial Services",
    "financial": "Financial Services",
    "banking": "Financial Services",
    "insurance": "Insurance & Pensions",
    "pensions": "Insurance & Pensions",
    "forest": "Forest & Paper",
    "paper": "Forest & Paper",
    "forestry": "Forest & Paper",
    "telecom": "Telecommunications",
    "telecommunications": "Telecommunications",
    "construction": "Construction & Real Estate",
    "real estate": "Construction & Real Estate",
    "transport": "Transport & Logistics",
    "logistics": "Transport & Logistics",
    "shipping": "Transport & Logistics",
    "media": "Media & Publishing",
    "publishing": "Media & Publishing",
    "industrial &": "Industrial & Manufacturing",
    "industrial and manufacturing": "Industrial & Manufacturing",
    "industrial & mfg": "Industrial & Manufacturing",
}


def normalize_domain(raw: str) -> str:
    """Normalize a stored domain value to its canonical form.
    Returns the canonical domain or the original value if no match."""
    if not raw:
        return "Other"
    matched = _match_domain(raw)
    return matched if matched else raw


def _match_domain(raw: str) -> str | None:
    """Try to match a raw Gemini response to a known domain.
    Returns canonical domain name or None."""
    cleaned = raw.strip().strip('"').strip("'").strip(".")
    # Exact match (case-insensitive)
    if cleaned.lower() in _DOMAIN_LOOKUP:
        return _DOMAIN_LOOKUP[cleaned.lower()]
    # Alias match
    if cleaned.lower() in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[cleaned.lower()]
    # Substring match: check if any known domain appears in the response
    for domain in KNOWN_DOMAINS:
        if domain.lower() in cleaned.lower():
            return domain
    # Reverse substring: check if the response is a prefix/part of a known domain
    for domain in KNOWN_DOMAINS:
        if cleaned.lower() in domain.lower() and len(cleaned) >= 4:
            return domain
    return None


def _call_classifier(client, model: str, prompt: str, company_name: str, timeout: int = 30) -> str | None:
    """Single classification attempt. Returns domain or None."""
    try:
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=30,
                ),
            ),
            label=f"Domain Classification {model}",
            model=model,
            timeout=timeout,
        )
        result = (response.text or "").strip()
        matched = _match_domain(result)
        if matched and matched != "Other":
            print(f"   [Domain] Inferred: '{matched}' for {company_name} (model={model}, raw='{result}')")
            return matched
        if matched == "Other":
            print(f"   [Domain] Model explicitly returned 'Other' for {company_name}")
            return "Other"
        print(f"   [Domain] Unrecognised response '{result}' from {model}")
        return None
    except Exception as e:
        print(f"   [Domain] {model} failed: {e}")
        return None


def infer_domain(company_name: str, company_url: str, reality_results: dict, financial_results: dict) -> str:
    """
    Classify the company into one of KNOWN_DOMAINS using already-computed
    scan evidence. Only called when the finland_list lookup returned 'Other'.

    Uses Gemini Flash (no thinking) — pure classification, ~1-3s.
    Falls back to a second model on failure.
    Non-fatal: returns 'Other' on any failure.
    """
    try:
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
        if not gemini_key:
            print("   [Domain] No API key available — skipping classification")
            return "Other"

        # Compact evidence summary
        checklist = reality_results.get("checklist", [])
        findings_text = "\n".join(
            f"- {item.get('description', item.get('category', ''))}"
            for item in checklist
            if item.get("found") and item.get("description", item.get("category"))
        )[:1200]

        fin_findings = "\n".join(
            f.get('assessment', '') if isinstance(f, dict) else ''
            for f in (financial_results.get("financial_findings") or [])[:3]
        )

        domains_list = ", ".join(f'"{d}"' for d in KNOWN_DOMAINS if d != "Other")

        prompt = f"""Classify this company into exactly one industry domain.

Company: {company_name}
Website: {company_url or "unknown"}

Key findings from forensic scan:
{findings_text or "(no findings available)"}

Financial context:
{fin_findings or "(none)"}

Choose exactly one from this list: {domains_list}

Rules:
- Output ONLY the domain name, nothing else. No explanation, no quotes, no punctuation.
- Use the company name and website URL as strong signals — they often reveal the industry.
- If the evidence clearly fits one domain, use it.
- If genuinely unclear or mixed, output "Other".
- Do not invent a domain not in the list."""

        client = genai.Client(api_key=gemini_key)

        # Attempt 1: Flash (fast, cheap, good for classification)
        domain = _call_classifier(client, "gemini-3-flash-preview", prompt, company_name, timeout=15)
        if domain:
            return domain

        # Attempt 2: Retry with Pro after brief delay
        time.sleep(1)
        domain = _call_classifier(client, "gemini-2.5-pro", prompt, company_name, timeout=30)
        if domain:
            return domain

        print(f"   ⚠️ [Domain] All classification attempts failed for {company_name}")
        return "Other"
    except Exception as e:
        print(f"   [Domain] Classification failed: {e} — keeping 'Other'")
        return "Other"
