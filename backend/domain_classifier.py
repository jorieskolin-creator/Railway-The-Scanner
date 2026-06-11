"""
domain_classifier.py — Industry domain inference from scan evidence.

Used by both main.py and seed_manager.py. Kept separate to avoid circular
imports (main imports seed_manager; seed_manager must not import main).
"""

import time

from llm_clients import call_json_model_chain


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
    "maritime": "Transport & Logistics",
    "marine": "Transport & Logistics",
    "ports": "Transport & Logistics",
    "media": "Media & Publishing",
    "publishing": "Media & Publishing",
    "industrial &": "Industrial & Manufacturing",
    "industrial and manufacturing": "Industrial & Manufacturing",
    "industrial & mfg": "Industrial & Manufacturing",
}


# Regulatory / foresight applicability by domain. This is deliberately conservative:
# applicability means "relevant context", not proof that a specific company is
# obligated, non-compliant, or using a technology.
DOMAIN_REGULATORY_APPLICABILITY = {
    "Technology & Software": {
        "relevant_frameworks": ["EU AI Act", "GDPR", "Cyber Resilience Act"],
        "conditional_frameworks": ["Digital Product Passport"],
        "notes": ["AI and data regulation are often relevant; DPP needs product-specific evidence."],
    },
    "IT Services & Consulting": {
        "relevant_frameworks": ["EU AI Act", "GDPR", "NIS2"],
        "conditional_frameworks": ["Digital Product Passport"],
        "notes": ["Regulation usually applies through services, customer sectors and systems deployed."],
    },
    "Industrial & Manufacturing": {
        "relevant_frameworks": ["Digital Product Passport", "EU AI Act", "Net-Zero Industry Act"],
        "conditional_frameworks": ["CBAM", "Batteries Regulation"],
        "notes": ["DPP/NZIA relevance depends on product categories and procurement context."],
    },
    "Energy & Utilities": {
        "relevant_frameworks": ["Net-Zero Industry Act", "EU AI Act", "NIS2"],
        "conditional_frameworks": ["Digital Product Passport", "CBAM"],
        "notes": ["NZIA may affect clean-tech and auction/procurement contexts; company-specific obligations require product/context evidence."],
    },
    "Transport & Logistics": {
        "relevant_frameworks": ["EU AI Act", "Fit for 55", "FuelEU Maritime"],
        "conditional_frameworks": ["Net-Zero Industry Act", "Digital Product Passport"],
        "notes": ["Maritime/logistics rules can be relevant, but product-manufacturing rules need company-specific mapping."],
    },
    "Financial Services": {
        "relevant_frameworks": ["EU AI Act", "DORA", "GDPR"],
        "conditional_frameworks": [],
        "notes": ["AI/risk and operational resilience are likely more relevant than industrial product regulation."],
    },
    "Insurance & Pensions": {
        "relevant_frameworks": ["EU AI Act", "DORA", "GDPR"],
        "conditional_frameworks": [],
        "notes": ["High-risk AI relevance depends on underwriting, claims or HR use cases."],
    },
    "Retail & Consumer": {
        "relevant_frameworks": ["Digital Product Passport", "GDPR", "EU AI Act"],
        "conditional_frameworks": ["Ecodesign for Sustainable Products Regulation"],
        "notes": ["DPP relevance depends on product categories and supply-chain role."],
    },
    "Healthcare": {
        "relevant_frameworks": ["EU AI Act", "Medical Device Regulation", "GDPR"],
        "conditional_frameworks": ["Digital Product Passport"],
        "notes": ["AI obligations depend on clinical, HR or administrative use cases."],
    },
    "Forest & Paper": {
        "relevant_frameworks": ["Digital Product Passport", "CBAM", "EU Deforestation Regulation"],
        "conditional_frameworks": ["Net-Zero Industry Act", "EU AI Act"],
        "notes": ["Applicability depends on products, materials and market role."],
    },
    "Telecommunications": {
        "relevant_frameworks": ["NIS2", "EU AI Act", "GDPR"],
        "conditional_frameworks": ["Digital Product Passport"],
        "notes": ["Cyber/data obligations are usually stronger than product DPP unless hardware is in scope."],
    },
    "Construction & Real Estate": {
        "relevant_frameworks": ["Energy Performance of Buildings", "Digital Product Passport"],
        "conditional_frameworks": ["EU AI Act", "Net-Zero Industry Act"],
        "notes": ["DPP relevance often enters through materials/products, not all real-estate operations."],
    },
    "Media & Publishing": {
        "relevant_frameworks": ["EU AI Act", "Digital Services Act", "Copyright"],
        "conditional_frameworks": [],
        "notes": ["AI transparency may matter for generated content; industrial rules usually do not."],
    },
    "Other": {
        "relevant_frameworks": ["EU AI Act"],
        "conditional_frameworks": ["Digital Product Passport", "Net-Zero Industry Act"],
        "notes": ["Use company-specific evidence before asserting regulation-specific obligations."],
    },
}


def get_domain_regulatory_applicability(domain: str) -> dict:
    """Return conservative regulatory/foresight applicability metadata for a domain."""
    canonical = normalize_domain(domain)
    data = DOMAIN_REGULATORY_APPLICABILITY.get(canonical, DOMAIN_REGULATORY_APPLICABILITY["Other"]).copy()
    data["domain"] = canonical
    data["applicability_is_context_not_obligation"] = True
    return data


def should_assert_company_specific_obligation(domain: str, framework: str, company_specific_evidence: bool = False) -> bool:
    """Only allow deterministic obligation language with company-specific evidence."""
    return bool(company_specific_evidence)


def normalize_domain(raw: str) -> str:
    """Normalize a stored domain value to its canonical form.
    Returns the canonical domain or the original value if no match."""
    if not raw:
        return "Other"
    matched = _match_domain(raw)
    return matched if matched else raw


def _match_domain(raw: str) -> str | None:
    """Try to match a raw model response to a known domain.
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


def _call_classifier(model: str, prompt: str, company_name: str, timeout: int = 30) -> str | None:
    """Single classification attempt. Returns domain or None."""
    try:
        result, model_used = call_json_model_chain(prompt, (model,), max_tokens=40, timeout=timeout)
        result = (result or "").strip()
        matched = _match_domain(result)
        if matched and matched != "Other":
            print(f"   [Domain] Inferred: '{matched}' for {company_name} (model={model_used}, raw='{result}')")
            return matched
        if matched == "Other":
            print(f"   [Domain] Model explicitly returned 'Other' for {company_name}")
            return "Other"
        print(f"   [Domain] Unrecognised response '{result}' from {model}")
        return None
    except Exception as e:
        print(f"   [Domain] {model} failed: {e}")
        return None


def infer_domain(company_name: str, company_url: str, reality_results: dict, financial_results: dict, cached_domain: str = None) -> str:
    """
    Classify the company into one of KNOWN_DOMAINS using already-computed
    scan evidence. Only called when the finland_list lookup returned 'Other'.

    If cached_domain is a valid known domain (not 'Other'), returns it
    immediately — saves one model call per re-scan.

    Uses GPT-5.4 for deterministic classification and Sonnet 4.6 fallback.
    Non-fatal: returns 'Other' on any failure.
    """
    if cached_domain and cached_domain in KNOWN_DOMAINS and cached_domain != "Other":
        print(f"   [Domain] Using cached domain: '{cached_domain}' for {company_name}")
        return cached_domain

    try:
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

        # Attempt 1: GPT verifier model
        domain = _call_classifier("gpt-5.4", prompt, company_name, timeout=20)
        if domain:
            return domain

        # Attempt 2: independent provider fallback
        time.sleep(1)
        domain = _call_classifier("claude-sonnet-4-6", prompt, company_name, timeout=30)
        if domain:
            return domain

        print(f"   ⚠️ [Domain] All classification attempts failed for {company_name}")
        return "Other"
    except Exception as e:
        print(f"   [Domain] Classification failed: {e} — keeping 'Other'")
        return "Other"
