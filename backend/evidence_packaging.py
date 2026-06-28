"""Deterministic source parsing and jury evidence packaging.

The forensic acquisition models are scouts. This module turns fetched source
packets into bounded, jury-facing evidence packages before any reasoning call.
It deliberately does not promote facts; it describes what a source can safely
be used for and what it cannot prove.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from evidence_contracts import mark_jury_evidence_package


ARCHITECTURAL_JURY = "ARCHITECTURAL JURY (Engineering Reality)"
STRATEGIC_JURY = "STRATEGIC JURY (Business Model, Procurement & Customer Experience)"
CULTURAL_JURY = "CULTURAL JURY (Talent & Psychological Safety)"
AI_DATA_JURY = "AI & DATA JURY (Future Readiness)"
SERVICE_JURY = "SERVICE PORTFOLIO JURY (Delivery Reality)"


JURY_SUBDIMENSIONS = {
    ARCHITECTURAL_JURY: {
        "integration_architecture": ("integration", "api", "interface", "middleware", "erp", "system", "platform"),
        "engineering_delivery": ("engineering", "developer", "development", "delivery", "devops", "software"),
        "operating_model": ("operating model", "process", "workflow", "organization", "decision", "governance"),
        "decision_rights": ("decision", "ownership", "accountability", "business unit", "approval"),
        "platform_developer_capability": ("developer", "portal", "documentation", "sdk", "open api", "api"),
    },
    STRATEGIC_JURY: {
        "business_model_ownership": ("ownership", "transaction", "carve-out", "acquisition", "group", "strategy"),
        "procurement_partnerships": ("procurement", "supplier", "partner", "partnership", "tender", "framework"),
        "customer_experience": ("customer experience", "cx", "customer", "market", "brand", "channel"),
        "market_financial_change": ("revenue", "growth", "margin", "profit", "market", "investment"),
        "strategic_investment": ("investment", "capex", "factory", "program", "transformation", "roadmap"),
    },
    CULTURAL_JURY: {
        "employee_experience": ("employee", "people", "workplace", "culture", "experience", "engagement"),
        "workforce_movement": ("restructuring", "layoff", "hiring", "recruit", "workforce", "personnel"),
        "learning_reskilling": ("learning", "training", "reskilling", "skills", "academy", "competence"),
        "leadership_model": ("leadership", "management", "values", "psychological safety", "trust"),
        "workforce_friction": ("strike", "union", "turnover", "friction", "remote", "return to office"),
    },
    AI_DATA_JURY: {
        "ai_governance": ("ai governance", "responsible ai", "policy", "risk", "compliance", "model"),
        "ai_deployment": ("artificial intelligence", "ai", "automation", "machine learning", "genai"),
        "data_ownership": ("data owner", "data governance", "data platform", "master data", "analytics"),
        "ai_literacy": ("ai training", "literacy", "skills", "academy", "learning"),
        "data_platform": ("cloud", "data platform", "warehouse", "lakehouse", "analytics", "cyber"),
    },
    SERVICE_JURY: {
        "stated_services": ("service", "services", "solution", "solutions", "portfolio", "offering"),
        "delivery_proof": ("delivery", "implementation", "project", "deployment", "case", "reference"),
        "customer_references": ("customer", "client", "case study", "reference", "testimonial"),
        "lifecycle_capability": ("support", "maintenance", "lifecycle", "managed", "operations"),
        "measurable_outcomes": ("result", "impact", "savings", "efficiency", "availability", "uptime"),
    },
}


SOURCE_ROLE_KEYWORDS = {
    "outcome": ("result", "impact", "saved", "reduced", "increased", "improved", "roi", "uptime"),
    "implementation": ("implemented", "deployment", "launched", "built", "uses", "adopted", "project"),
    "contradiction": ("declined", "delay", "risk", "warning", "lawsuit", "layoff", "strike", "failed"),
    "intent": ("strategy", "plan", "aim", "target", "vision", "commitment", "ambition"),
    "independent_corroboration": ("registry", "analyst", "public procurement", "award", "database"),
}

WEAK_GENERIC_TERMS = {
    "brand",
    "culture",
    "customer",
    "market",
    "people",
    "strategy",
    "values",
}


SEMANTIC_PATTERN_FAMILIES = {
    STRATEGIC_JURY: {
        "ownership_change": {
            "subdimensions": ("business_model_ownership",),
            "indicators": (
                "acquisition",
                "carve-out",
                "divestment",
                "new owner",
                "ownership change",
                "private equity",
                "separate company",
                "spin-off",
                "transaction",
            ),
            "behavior": "ownership or transaction context",
        },
        "investment_thesis": {
            "subdimensions": ("business_model_ownership", "strategic_investment"),
            "indicators": (
                "growth platform",
                "investment thesis",
                "strategic investment",
                "value creation",
                "portfolio company",
                "growth company",
                "long-term owner",
            ),
            "behavior": "investor logic or value-creation thesis",
        },
        "growth_program": {
            "subdimensions": ("strategic_investment", "market_financial_change"),
            "indicators": (
                "growth program",
                "expand capacity",
                "capacity expansion",
                "market expansion",
                "new market",
                "growth strategy",
                "scale up",
                "international expansion",
            ),
            "behavior": "growth, scale, or market expansion direction",
        },
        "partnership_logic": {
            "subdimensions": ("procurement_partnerships",),
            "indicators": (
                "strategic partner",
                "partner ecosystem",
                "supplier partnership",
                "framework agreement",
                "procurement model",
                "sourcing model",
                "preferred supplier",
                "alliance",
            ),
            "behavior": "partner, supplier, or procurement logic",
        },
        "customer_segment_focus": {
            "subdimensions": ("customer_experience", "market_financial_change"),
            "indicators": (
                "customer segment",
                "target customers",
                "enterprise customers",
                "industrial customers",
                "customer base",
                "market-facing",
                "customer promise",
                "customer experience",
            ),
            "behavior": "customer or market-positioning focus",
        },
    },
    CULTURAL_JURY: {
        "decision_autonomy": {
            "subdimensions": ("leadership_model", "employee_experience"),
            "indicators": (
                "autonomous teams",
                "decision autonomy",
                "empowered teams",
                "low hierarchy",
                "make decisions",
                "freedom to decide",
                "self-organizing",
                "self-directed",
            ),
            "behavior": "decision autonomy or empowerment language",
        },
        "team_responsibility": {
            "subdimensions": ("employee_experience", "leadership_model"),
            "indicators": (
                "take responsibility",
                "team responsibility",
                "end-to-end responsibility",
                "accountable teams",
                "ownership mindset",
                "everyone takes responsibility",
                "responsible teams",
            ),
            "behavior": "team accountability or responsibility framing",
        },
        "safety_trust": {
            "subdimensions": ("leadership_model", "employee_experience"),
            "indicators": (
                "psychological safety",
                "safe to speak",
                "open feedback",
                "trust and transparency",
                "trusted environment",
                "speak up",
                "inclusive culture",
            ),
            "behavior": "trust, safety, or openness signal",
        },
        "learning_reskilling": {
            "subdimensions": ("learning_reskilling",),
            "indicators": (
                "reskilling",
                "upskilling",
                "learning culture",
                "continuous learning",
                "training program",
                "competence development",
                "career development",
                "academy",
            ),
            "behavior": "learning or capability development signal",
        },
        "workforce_change": {
            "subdimensions": ("workforce_movement", "workforce_friction"),
            "indicators": (
                "restructuring",
                "change negotiations",
                "layoffs",
                "hiring campaign",
                "recruitment drive",
                "workforce reduction",
                "new roles",
                "talent shortage",
            ),
            "behavior": "workforce change or friction signal",
        },
        "employee_promise": {
            "subdimensions": ("employee_experience",),
            "indicators": (
                "employee promise",
                "employer promise",
                "employee value proposition",
                "work-life balance",
                "flexible work",
                "meaningful work",
                "career path",
            ),
            "behavior": "employee promise or employer-positioning signal",
        },
    },
    ARCHITECTURAL_JURY: {
        "accountability_governance": {
            "subdimensions": ("operating_model", "decision_rights"),
            "indicators": (
                "accountability",
                "process owner",
                "business owner",
                "governance model",
                "decision rights",
                "approval process",
                "responsibility matrix",
            ),
            "behavior": "accountability, governance, or decision-rights signal",
        },
        "delivery_model": {
            "subdimensions": ("operating_model", "engineering_delivery"),
            "indicators": (
                "delivery model",
                "operating model",
                "end-to-end delivery",
                "cross-functional teams",
                "business units",
                "process ownership",
                "production scaling",
            ),
            "behavior": "operating or delivery-model signal",
        },
    },
}


def _clean_text(value: object, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def _canonical_url(url: str) -> str:
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{path}".lower()


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def source_text(record: dict, limit: int = 5000) -> str:
    text = _clean_text(
        record.get("fetched_excerpt")
        or record.get("cached_excerpt")
        or record.get("text_excerpt")
        or record.get("markdown_excerpt")
        or record.get("source_quote")
        or record.get("provider_quote_candidate")
        or "",
        limit,
    )
    if text:
        return text
    # Historical Scanner records sometimes only have `snippet`; fresh
    # acquisition records use it as a semantic routing hint, so keep it out of
    # evidence packages unless it came from a non-fresh source lifecycle.
    if record.get("source_status") != "fresh_discovery":
        return _clean_text(record.get("snippet") or "", limit)
    return ""


def _routing_haystack(record: dict, content_limit: int = 5000) -> str:
    return _clean_text(
        " ".join(
            str(record.get(field) or "")
            for field in (
                "title",
                "url",
                "source_type",
                "source_category",
                "semantic_snippet",
                "discovery_rationale",
                "source_intent",
                "fetched_excerpt",
                "cached_excerpt",
                "snippet",
            )
        )
        + " "
        + source_text(record, content_limit)
    )


def semantic_pattern_matches(jury_name: str, record: dict) -> list[dict]:
    """Return concrete semantic behavior patterns detected in a source.

    These patterns are intentionally stronger than broad keywords such as
    "strategy" or "culture": they represent business behaviors a Jury may
    cautiously reason about as scoped context.
    """
    haystack = _routing_haystack(record, 7000).lower()
    matches = []
    for family, spec in SEMANTIC_PATTERN_FAMILIES.get(jury_name, {}).items():
        hits = []
        score = 0
        for indicator in spec.get("indicators", ()):
            if indicator in haystack:
                hits.append(indicator)
                score += 2 if " " in indicator or "-" in indicator else 1
        if not hits:
            continue
        if score >= 4:
            confidence = "high"
        elif score >= 2:
            confidence = "medium"
        else:
            confidence = "low"
        matches.append(
            {
                "pattern_family": family,
                "behavior": spec.get("behavior") or family.replace("_", " "),
                "subdimensions": list(spec.get("subdimensions", ())),
                "matched_indicators": hits[:6],
                "pattern_score": score,
                "pattern_confidence": confidence,
            }
        )
    matches.sort(key=lambda item: item.get("pattern_score", 0), reverse=True)
    return matches


def _pattern_rejection_reason(jury_name: str, record: dict, patterns: list[dict]) -> str | None:
    if patterns:
        return None
    if jury_name not in {STRATEGIC_JURY, CULTURAL_JURY, ARCHITECTURAL_JURY}:
        return None
    haystack = _routing_haystack(record, 2500).lower()
    weak_hits = [term for term in WEAK_GENERIC_TERMS if term in haystack]
    if len(weak_hits) >= 2:
        return "generic_brand_language"
    if weak_hits:
        return "weak_pattern_match"
    return None


def route_subdimensions(jury_name: str, record: dict) -> list[str]:
    haystack = _routing_haystack(record, 5000).lower()
    matches = []
    pattern_scores = {}
    for pattern in semantic_pattern_matches(jury_name, record):
        if pattern.get("pattern_score", 0) < 2:
            continue
        for subdimension in pattern.get("subdimensions", []) or []:
            pattern_scores[subdimension] = max(
                pattern_scores.get(subdimension, 0),
                int(pattern.get("pattern_score", 0)) + 3,
            )
    for subdimension, terms in JURY_SUBDIMENSIONS.get(jury_name, {}).items():
        score = 0
        for term in terms:
            if term not in haystack:
                continue
            if jury_name in {STRATEGIC_JURY, CULTURAL_JURY} and term in WEAK_GENERIC_TERMS:
                score += 0.25
            else:
                score += 1
        score += pattern_scores.get(subdimension, 0)
        if score:
            matches.append((subdimension, score))
    matches.sort(key=lambda item: item[1], reverse=True)
    return [name for name, _ in matches[:3]]


def infer_source_role(record: dict) -> str:
    text = _clean_text(
        " ".join(
            str(record.get(field) or "")
            for field in (
                "title",
                "url",
                "source_type",
                "publisher_independence",
                "semantic_snippet",
                "fetched_excerpt",
                "cached_excerpt",
                "snippet",
            )
        ).lower()
    )
    if record.get("official_domain_scope_inferred") or "official" in text:
        if any(term in text for term in SOURCE_ROLE_KEYWORDS["implementation"]):
            return "implementation"
        return "official_context"
    for role, terms in SOURCE_ROLE_KEYWORDS.items():
        if any(term in text for term in terms):
            return role
    source_context = str(record.get("source_context_status") or record.get("source_use_status") or "").lower()
    if "scoped_context" in source_context or "valid_company_context" in source_context:
        return "scoped_context"
    return "scoped_context"


def _independence_label(record: dict) -> str:
    text = _clean_text(
        " ".join(
            str(record.get(field) or "")
            for field in (
                "publisher_independence",
                "source_type",
                "source_database",
                "forensic_provenance",
                "url",
            )
        ).lower()
    )
    if record.get("official_domain_scope_inferred") or "official" in text:
        return "company_stated"
    if any(term in text for term in ("registry", "public procurement", "analyst", "media", "research", "database")):
        return "independent_or_third_party"
    if any(term in text for term in ("partner", "vendor", "customer")):
        return "partner_or_vendor_context"
    return "unknown"


def _support_level(record: dict) -> str:
    level = str(record.get("support_level") or record.get("claim_support_status") or "").lower()
    if level in {"exact", "supported"}:
        return "exact"
    if level in {"partial", "partially_supported"}:
        return "partial"
    return "context_only"


def extract_source_propositions(record: dict, jury_name: str, max_props: int = 2) -> list[dict]:
    text = source_text(record, 3200)
    if not text:
        return []
    patterns = semantic_pattern_matches(jury_name, record)
    pattern_rejection = _pattern_rejection_reason(jury_name, record, patterns)
    subdimensions = route_subdimensions(jury_name, record) or ["current_state_context"]
    role = infer_source_role(record)
    support_level = _support_level(record)
    independence = _independence_label(record)
    title = _clean_text(record.get("title") or record.get("label") or _host(record.get("url") or ""), 160)
    passage = text[:900]
    if patterns:
        primary = patterns[0]
        behavior = primary.get("behavior") or primary.get("pattern_family", "bounded current-state context")
        claim = f"{title} can inform {behavior} for {', '.join(subdimensions[:2])}."
        source_role = "scoped_context" if role == "official_context" else role
        can_support = [
            "bounded diagnosis premise",
            "scoped current-state interpretation",
            f"{independence.replace('_', ' ')} context",
        ]
        cannot_support = [
            "independent proof of practiced behavior unless corroborated by third-party or outcome evidence",
            "precise hard facts not present in the fetched content",
            "company-wide maturity, autonomy, culture, or operating-model performance from brand language alone",
        ]
    else:
        claim = f"{title} provides {role.replace('_', ' ')} for {', '.join(subdimensions[:2])}."
        source_role = role
        can_support = [
            "bounded diagnosis premise",
            "scoped context",
            "current-state jury interpretation",
        ]
        cannot_support = [
            "independent proof of effectiveness unless the passage states outcomes",
            "precise hard facts not present in the fetched content",
            "company-wide deployment from a narrow service or subsidiary page",
        ]
        if pattern_rejection:
            cannot_support.append(pattern_rejection)
    propositions = [
        {
            "proposition_id": f"PROP-{hashlib.sha1((str(record.get('url')) + title + subdimensions[0]).encode('utf-8')).hexdigest()[:10]}",
            "claim": claim,
            "support_level": support_level,
            "source_passage": passage,
            "source_role": source_role,
            "source_independence": independence,
            "subdimensions": subdimensions[:2],
            "semantic_patterns": patterns[:3],
            "routing_basis": "semantic_pattern" if patterns else "keyword_or_metadata",
            "routing_warning": pattern_rejection,
            "can_support": can_support,
            "cannot_support": cannot_support,
        }
    ]
    if max_props > 1 and len(subdimensions) > 2:
        propositions.append(
            {
                "proposition_id": f"PROP-{hashlib.sha1((str(record.get('url')) + title + subdimensions[2]).encode('utf-8')).hexdigest()[:10]}",
                "claim": f"The same source may also inform {subdimensions[2]}.",
                "support_level": "context_only",
                "source_passage": passage[:700],
                "source_role": role,
                "source_independence": independence,
                "subdimensions": [subdimensions[2]],
                "semantic_patterns": patterns[:3],
                "routing_basis": "semantic_pattern" if patterns else "keyword_or_metadata",
                "routing_warning": pattern_rejection,
                "can_support": ["secondary context"],
                "cannot_support": ["standalone hard fact"],
            }
        )
    return propositions[:max_props]


def _package_score(record: dict, jury_name: str) -> int:
    text = source_text(record, 1000)
    patterns = semantic_pattern_matches(jury_name, record)
    score = 0
    if record.get("url_verified") or record.get("seed_url_verified"):
        score += 18
    if record.get("content_readable") or text:
        score += 18
    if record.get("entity_verified") or record.get("official_domain_scope_inferred"):
        score += 18
    if record.get("source_database") == "company_evidence_db":
        score += 14
    if record.get("cached_excerpt"):
        score += 8
    if record.get("source_use_status") == "scoped_context" or record.get("source_context_status") in {
        "scoped_context",
        "valid_company_context",
    }:
        score += 8
    if _host(record.get("url") or "") and record.get("official_domain_scope_inferred"):
        score += 8
    score += min(18, len(route_subdimensions(jury_name, record)) * 6)
    if patterns:
        score += min(18, sum(int(pattern.get("pattern_score", 0)) for pattern in patterns[:2]))
    elif _pattern_rejection_reason(jury_name, record, patterns):
        score -= 6
    if record.get("freshness") in {"fresh", "current"} or record.get("is_recent"):
        score += 4
    if "image/" in str(record.get("content_type") or "").lower():
        score -= 20
    return score


def build_jury_evidence_packages(jury_name: str, packets: list[dict], max_packages: int = 10) -> list[dict]:
    seen = set()
    candidates = []
    for idx, packet in enumerate(packets or []):
        if not isinstance(packet, dict) or not packet.get("url"):
            continue
        key = _canonical_url(packet.get("url") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        propositions = extract_source_propositions(packet, jury_name)
        if not propositions:
            continue
        subdimensions = sorted({dim for prop in propositions for dim in prop.get("subdimensions", [])})
        patterns = semantic_pattern_matches(jury_name, packet)
        routing_basis = "semantic_pattern" if patterns else "keyword_or_metadata"
        routing_warning = _pattern_rejection_reason(jury_name, packet, patterns)
        role = infer_source_role(packet)
        package = {
            "package_id": f"PKG-{hashlib.sha1((jury_name + key).encode('utf-8')).hexdigest()[:10]}",
            "source_url": packet.get("url"),
            "source_title": _clean_text(packet.get("title") or packet.get("label") or packet.get("url"), 220),
            "source_type": packet.get("source_type"),
            "source_database": packet.get("source_database"),
            "source_origin": packet.get("forensic_provenance") or packet.get("source_status") or packet.get("source_database"),
            "publisher_independence": packet.get("publisher_independence"),
            "entity_scope": packet.get("entity_scope"),
            "entity_verified": packet.get("entity_verified"),
            "url_verified": packet.get("url_verified") or packet.get("seed_url_verified"),
            "content_readable": packet.get("content_readable") or bool(source_text(packet, 200)),
            "freshness": packet.get("freshness") or ("current" if packet.get("is_recent") else "unknown"),
            "subdimensions": subdimensions,
            "evidence_role": role,
            "semantic_patterns": patterns[:4],
            "routing_basis": routing_basis,
            "routing_warning": routing_warning,
            "semantic_snippet": _clean_text(packet.get("semantic_snippet") or packet.get("snippet") or "", 700),
            "fetched_or_cached_content": source_text(packet, 2200),
            "propositions": propositions,
            "allowed_uses": sorted({item for prop in propositions for item in prop.get("can_support", [])}),
            "limitations": sorted({item for prop in propositions for item in prop.get("cannot_support", [])}),
            "portfolio_value": _package_score(packet, jury_name),
            "original_packet_index": idx,
        }
        candidates.append(mark_jury_evidence_package(package))

    candidates.sort(key=lambda item: item.get("portfolio_value", 0), reverse=True)
    selected = []
    selected_ids = set()
    covered_subdimensions = set()
    covered_roles = set()

    def add(package: dict) -> None:
        if package["package_id"] in selected_ids or len(selected) >= max_packages:
            return
        selected.append(package)
        selected_ids.add(package["package_id"])
        covered_subdimensions.update(package.get("subdimensions", []) or [])
        covered_roles.add(package.get("evidence_role"))

    # First pass: breadth across subdimensions and evidence roles.
    for package in candidates:
        if any(dim not in covered_subdimensions for dim in package.get("subdimensions", []) or []):
            add(package)
    for package in candidates:
        if package.get("evidence_role") not in covered_roles:
            add(package)
    for package in candidates:
        add(package)
    return selected


def build_subdimension_coverage(jury_name: str, packages: list[dict]) -> dict:
    counts = {name: 0 for name in JURY_SUBDIMENSIONS.get(jury_name, {})}
    role_counts = {}
    for package in packages or []:
        role = package.get("evidence_role") or "unknown"
        role_counts[role] = role_counts.get(role, 0) + 1
        for dim in package.get("subdimensions", []) or []:
            counts[dim] = counts.get(dim, 0) + 1
    status = {
        dim: "covered" if count >= 2 else "weak" if count == 1 else "missing"
        for dim, count in counts.items()
    }
    return {
        "status": status,
        "counts": counts,
        "roles": role_counts,
        "covered": [dim for dim, value in status.items() if value == "covered"],
        "weak": [dim for dim, value in status.items() if value == "weak"],
        "missing": [dim for dim, value in status.items() if value == "missing"],
    }


def render_packages_for_prompt(packages: list[dict], max_packages: int = 10) -> list[dict]:
    rendered = []
    for package in (packages or [])[:max_packages]:
        rendered.append(
            {
                "package_id": package.get("package_id"),
                "url": package.get("source_url"),
                "title": package.get("source_title"),
                "source_type": package.get("source_type"),
                "source_origin": package.get("source_origin"),
                "entity_scope": package.get("entity_scope"),
                "url_verified": package.get("url_verified"),
                "content_readable": package.get("content_readable"),
                "evidence_role": package.get("evidence_role"),
                "subdimensions": package.get("subdimensions"),
                "routing_basis": package.get("routing_basis"),
                "routing_warning": package.get("routing_warning"),
                "semantic_patterns": package.get("semantic_patterns"),
                "semantic_snippet_for_routing_only": package.get("semantic_snippet"),
                "source_content_excerpt": package.get("fetched_or_cached_content"),
                "source_supported_propositions": package.get("propositions"),
                "allowed_uses": package.get("allowed_uses"),
                "limitations": package.get("limitations"),
            }
        )
    return rendered
