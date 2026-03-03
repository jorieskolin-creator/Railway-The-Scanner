"""
domain_classifier.py — Industry domain inference from scan evidence.

Used by both main.py and seed_manager.py. Kept separate to avoid circular
imports (main imports seed_manager; seed_manager must not import main).
"""

import os

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


def infer_domain(company_name: str, company_url: str, reality_results: dict, financial_results: dict) -> str:
    """
    Classify the company into one of KNOWN_DOMAINS using already-computed
    scan evidence. Only called when the finland_list lookup returned 'Other'.

    Uses Gemini Flash with thinking disabled — pure classification, ~2-4s.
    Non-fatal: returns 'Other' on any failure.
    """
    try:
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
        if not gemini_key:
            return "Other"

        # Compact evidence summary
        checklist = reality_results.get("checklist", [])
        findings_text = "\n".join(
            f"- {item.get('description', item.get('category', ''))}"
            for item in checklist
            if item.get("found") and item.get("description", item.get("category"))
        )[:1200]

        fin_findings = "\n".join(
            str(f) for f in (financial_results.get("financial_findings") or [])[:3]
        )

        domains_list = ", ".join(f'"{d}"' for d in KNOWN_DOMAINS)

        prompt = f"""Classify this company into exactly one industry domain.

Company: {company_name}
Website: {company_url or "unknown"}

Key findings from forensic scan:
{findings_text or "(no findings available)"}

Financial context:
{fin_findings or "(none)"}

Choose exactly one from this list: {domains_list}

Rules:
- Output only the domain name, nothing else. No explanation.
- If the evidence clearly fits one domain, use it.
- If genuinely unclear or mixed, output "Other".
- Do not invent a domain not in the list."""

        client = genai.Client(api_key=gemini_key)
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.5,
                    max_output_tokens=20,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
                ),
            ),
            label="Domain Classification gemini-2.5-pro",
            model="gemini-2.5-pro",
            timeout=30,
        )
        result = (response.text or "").strip().strip('"').strip("'")
        if result in KNOWN_DOMAINS:
            print(f"   [Domain] Inferred: '{result}' for {company_name}")
            return result
        print(f"   [Domain] Unrecognised response '{result}' — keeping 'Other'")
        return "Other"
    except Exception as e:
        print(f"   [Domain] Classification failed: {e} — keeping 'Other'")
        return "Other"
