"""
foresight_guardrails.py — Shared safeguards to keep foresight as foresight.

The module prevents trend, analyst and regulatory sources from being written as
current company-specific facts unless a separate company-specific evidence item
supports that claim.
"""

from __future__ import annotations

import re
from copy import deepcopy

FORESIGHT_CLAIM_TYPE = "foresight_context"
REGULATORY_CLAIM_TYPE = "regulatory_context"
FORBIDDEN_FACT_PATTERNS = [
    r"\bhas\s+deployed\b",
    r"\bhave\s+deployed\b",
    r"\bis\s+deploying\b",
    r"\bare\s+deploying\b",
    r"\bcurrently\s+uses\b",
    r"\buses\s+multi-agent\b",
    r"\bbroad\s+deployment\b",
    r"\brequires\s+.+\bto\b",
    r"\bmust\s+realign\b",
    r"\bmust\s+prioriti[sz]e\b",
    r"\bforces\s+.+\bto\b",
]

FORESIGHT_LANGUAGE_GUARDRAILS = """
FORESIGHT-MODALITY RULES — STRICT:
- Treat all foresight, trend, analyst, scenario and regulatory sources as context, not proof of current company deployment.
- Use modal language: "may", "could", "is likely to influence", "preparation may be needed", "relevant scenario".
- Do NOT write: "has deployed", "is deploying", "currently uses", "broad deployment", "requires the company to", "must realign", or "is non-compliant" unless a separate company-specific source proves it.
- Regulatory sources support what the regulation says and affected contexts; they do not alone prove that the named company must change a specific process.
- If company-specific evidence is missing, phrase recommendations as "validate whether..." or "assess exposure to...".
"""


def build_foresight_guardrails(domain: str = "", applicability: dict | None = None) -> str:
    """Return prompt guardrails with optional domain applicability metadata."""
    app_txt = ""
    if applicability:
        relevant = applicability.get("relevant_frameworks") or []
        conditional = applicability.get("conditional_frameworks") or []
        notes = applicability.get("notes") or []
        app_txt = (
            "\nDOMAIN APPLICABILITY:\n"
            f"- Domain: {domain or applicability.get('domain', 'unknown')}\n"
            f"- Relevant frameworks: {', '.join(relevant) if relevant else 'none specified'}\n"
            f"- Conditional frameworks: {', '.join(conditional) if conditional else 'none specified'}\n"
            f"- Notes: {'; '.join(notes) if notes else 'Use company-specific evidence before asserting obligations.'}\n"
        )
    return FORESIGHT_LANGUAGE_GUARDRAILS + app_txt


def annotate_foresight_signal(signal: dict, *, theme: str = "", domain: str = "") -> dict:
    """Mark a foresight signal so downstream generators cannot mistake it for company evidence."""
    if not isinstance(signal, dict):
        return signal
    sig = deepcopy(signal)
    sig.setdefault("origin", "foresight")
    sig.setdefault("claim_type", FORESIGHT_CLAIM_TYPE)
    sig.setdefault("allowed_claim_types", [FORESIGHT_CLAIM_TYPE])
    sig.setdefault("company_specific", False)
    sig.setdefault("requires_company_specific_corroboration", True)
    sig.setdefault("modal_language_required", True)
    sig.setdefault("forbidden_as_current_company_fact", True)
    if theme:
        sig.setdefault("fp_theme", theme)
        sig.setdefault("_kb_theme", theme)
    if domain:
        sig.setdefault("domain_context", domain)
    return sig


def annotate_foresight_link(link: dict, *, domain: str = "") -> dict:
    """Annotate extracted foresight links with safe claim metadata."""
    if not isinstance(link, dict):
        return link
    link.setdefault("origin", "foresight")
    link.setdefault("claim_type", FORESIGHT_CLAIM_TYPE)
    link.setdefault("allowed_claim_types", [FORESIGHT_CLAIM_TYPE])
    link.setdefault("company_specific", False)
    link.setdefault("requires_company_specific_corroboration", True)
    link.setdefault("modal_language_required", True)
    link.setdefault("forbidden_as_current_company_fact", True)
    link.setdefault("scope", "external-trend")
    if domain:
        link.setdefault("domain_context", domain)
    return link


def validate_foresight_text(text: str) -> list[str]:
    """Return warnings when generated foresight text uses fact-like/company-obligation wording."""
    warnings = []
    if not text:
        return warnings
    for pattern in FORBIDDEN_FACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            warnings.append(f"Forbidden foresight-as-fact wording matched: {pattern}")
    return warnings


def sanitize_foresight_text(text: str) -> str:
    """Light deterministic wording softener for common foresight overclaims."""
    if not text:
        return text
    replacements = [
        (r"\bmust\s+realign\b", "may need to assess"),
        (r"\brequires\s+([^\.]{0,80})\bto\b", r"may influence \1 to"),
        (r"\bhas\s+deployed\b", "has not been proven by foresight sources alone to have deployed"),
        (r"\bis\s+deploying\b", "may be exploring or could need to evaluate"),
        (r"\bbroad\s+deployment\b", "potential broad relevance"),
        (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
    ]
    out = text
    for pat, repl in replacements:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out
