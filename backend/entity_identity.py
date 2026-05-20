"""
entity_identity.py - canonical company identity and source-scope helpers.

The scanner often sees evidence for legal entities, former brand names, owners,
or subsidiaries. Those sources can be useful, but they are not interchangeable
with the canonical company being scanned. This module keeps that distinction
deterministic so prompts and validators do not have to infer it from prose.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import Dict, Iterable, List


ENTITY_SCOPE_CANONICAL_GROUP = "canonical_group"
ENTITY_SCOPE_LOCAL_LEGAL_ENTITY = "local_legal_entity"
ENTITY_SCOPE_FORMER_NAME = "former_name"
ENTITY_SCOPE_SUBSIDIARY = "subsidiary"
ENTITY_SCOPE_PARENT_OWNER = "parent_owner"
ENTITY_SCOPE_WRONG_ENTITY = "wrong_entity"
ENTITY_SCOPE_UNKNOWN = "unknown"


_LEGAL_SUFFIX_RE = re.compile(
    r"\b(oyj|oy|abp|ab|as|asa|ltd|limited|plc|inc|corp|corporation|gmbh|s\.r\.o\.|sro|bv|nv)\b\.?",
    re.IGNORECASE,
)


def _norm(value: str | None) -> str:
    value = value or ""
    value = value.lower()
    value = re.sub(r"[^\w\s&.-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _strip_legal_suffix(value: str | None) -> str:
    value = _LEGAL_SUFFIX_RE.sub("", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = _norm(phrase)
    if not phrase:
        return False
    return phrase in text


def normalize_source_host(value: str | None) -> str:
    """Return a lowercase host without www, or empty string for non-host values."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if " " in raw and "." not in raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        host = parsed.netloc or parsed.path
    except Exception:
        host = raw
    host = host.split("@")[-1].split(":")[0].strip(".")
    if host.startswith("www."):
        host = host[4:]
    if "." not in host:
        return ""
    return host


def _host_matches(source_host: str, target_host: str) -> bool:
    if not source_host or not target_host:
        return False
    return (
        source_host == target_host
        or source_host.endswith(f".{target_host}")
        or target_host.endswith(f".{source_host}")
    )


def _host_slug(host: str) -> str:
    if not host:
        return ""
    root = host.split(".")[0]
    return re.sub(r"[^a-z0-9]+", "", root.lower())


def _significant_tokens(value: str | None) -> list[str]:
    legal = {
        "oy", "oyj", "ab", "abp", "as", "asa", "ltd", "limited", "plc", "inc",
        "corp", "corporation", "group", "company", "consulting", "consultancy",
        "services", "service", "solutions", "technologies", "technology",
    }
    tokens = re.findall(r"[a-z0-9åäö]{3,}", _norm(value))
    return [t for t in tokens if t not in legal]


def _names(items: Iterable[dict | str]) -> List[str]:
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if name:
            names.append(str(name))
    return names


ENTITY_REGISTRY: Dict[str, dict] = {
    "vivicta": {
        "canonical_name": "Vivicta",
        "aliases": ["Vivicta", "Vivicta Group"],
        "former_names": ["Tietoevry Tech Services", "Tietoevry Tech Services business"],
        "legal_entities": [
            {
                "name": "Vivicta Oy",
                "country": "FI",
                "scope": ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
                "claim_policy": "May support Finnish legal-entity financial facts only unless corroborated by group-level evidence.",
            },
            {
                "name": "Tietoevry Tech Services Finland Oy",
                "country": "FI",
                "scope": ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
                "claim_policy": "Legacy Finnish legal entity; historical/local scope unless explicitly tied to group operations.",
            },
        ],
        "subsidiaries": [
            {"name": "Tietoevry Tech Services Finland Oy", "country": "FI"},
            {"name": "Tietoevry Tech Services Sweden AB", "country": "SE"},
            {"name": "Tietoevry Tech Services Norway AS", "country": "NO"},
            {"name": "Tietoevry Tech Services Lithuania", "country": "LT"},
        ],
        "parent_or_owner": [
            {
                "name": "Agilitas",
                "relationship": "owner/acquirer",
                "claim_policy": "Ownership and transaction context only unless the source explicitly discusses Vivicta operations.",
            },
            {
                "name": "Agilitas Private Equity",
                "relationship": "owner/acquirer",
                "claim_policy": "Ownership and transaction context only unless the source explicitly discusses Vivicta operations.",
            },
        ],
        "transition_events": [
            {
                "date": "2025-09-02",
                "event": "Tietoevry completed the divestment of Tech Services; Vivicta began operating as a standalone company.",
            },
            {
                "date": "2025-10-31",
                "event": "Vivicta launched its new brand identity after operating as Tietoevry Tech Services.",
            },
        ],
        "entity_scope_policy": (
            "Use Vivicta for canonical/group claims. Use Vivicta Oy only for local Finnish legal-entity "
            "financial facts. Use Tietoevry Tech Services for predecessor/history. Use Agilitas for owner/acquirer context."
        ),
    }
}


def get_company_identity(company_name: str | None) -> dict:
    """Return canonical identity metadata for a company, or a safe default."""
    clean = _norm(_strip_legal_suffix(company_name or ""))
    direct = ENTITY_REGISTRY.get(clean)
    if direct:
        return direct

    for identity in ENTITY_REGISTRY.values():
        candidates = [identity.get("canonical_name", "")]
        candidates += identity.get("aliases", [])
        candidates += identity.get("former_names", [])
        candidates += _names(identity.get("legal_entities", []))
        if clean in {_norm(_strip_legal_suffix(c)) for c in candidates if c}:
            return identity

    return {
        "canonical_name": company_name or "",
        "aliases": [company_name] if company_name else [],
        "former_names": [],
        "legal_entities": [],
        "subsidiaries": [],
        "parent_or_owner": [],
        "transition_events": [],
        "entity_scope_policy": "",
    }


def assess_entity_match(
    link: dict,
    company_name: str | None = "",
    company_domain: str | None = "",
    *,
    page_text: str | None = "",
) -> dict:
    """Classify whether a source describes the scan target.

    URL liveness and claim support are not enough: a source can support a
    statement about a different same-name company.  This helper is deliberately
    deterministic so scan-time, Admin cleaning, and rendering all use the same
    entity gate.
    """
    if not isinstance(link, dict):
        return {
            "entity_match_status": "unknown",
            "entity_match_reason": "not_a_dict",
            "canonical_source_host": "",
        }

    source_url = str(link.get("url") or link.get("source_url") or "")
    source_host = normalize_source_host(source_url)
    target_host = normalize_source_host(company_domain)

    if link.get("wrong_entity") is True or link.get("entity_match") == "wrong":
        return {
            "entity_match_status": "wrong",
            "entity_match_reason": "pre_marked_wrong_entity",
            "canonical_source_host": source_host,
            "entity_scope": ENTITY_SCOPE_WRONG_ENTITY,
        }

    if source_host and target_host and _host_matches(source_host, target_host):
        return {
            "entity_match_status": "exact",
            "entity_match_reason": "source_host_matches_target_domain",
            "canonical_source_host": source_host,
            "entity_scope": ENTITY_SCOPE_CANONICAL_GROUP,
        }

    identity = get_company_identity(company_name)
    text = _norm(" ".join([
        str(link.get("title") or link.get("source_title") or ""),
        str(link.get("snippet") or link.get("evidence_snippet") or ""),
        str(link.get("source_says") or link.get("source_quote") or ""),
        str(link.get("description") or link.get("summary") or ""),
        str(source_url),
        str(page_text or ""),
    ]))

    canonical_names = []
    for name in [identity.get("canonical_name") or company_name or "", *(identity.get("aliases") or [])]:
        if name and name not in canonical_names:
            canonical_names.append(str(name))

    for name in canonical_names:
        if _contains_phrase(text, name):
            return {
                "entity_match_status": "exact",
                "entity_match_reason": f"mentions_target:{name}",
                "canonical_source_host": source_host,
                "entity_scope": ENTITY_SCOPE_CANONICAL_GROUP,
            }

    scoped_groups = [
        (identity.get("legal_entities", []), ENTITY_SCOPE_LOCAL_LEGAL_ENTITY, "mentions_legal_entity"),
        (identity.get("subsidiaries", []), ENTITY_SCOPE_SUBSIDIARY, "mentions_subsidiary"),
        (identity.get("former_names", []), ENTITY_SCOPE_FORMER_NAME, "mentions_former_name"),
        (identity.get("parent_or_owner", []), ENTITY_SCOPE_PARENT_OWNER, "mentions_parent_owner"),
    ]
    for items, scope, reason in scoped_groups:
        for name in _names(items):
            if _contains_phrase(text, name):
                return {
                    "entity_match_status": "scoped",
                    "entity_match_reason": f"{reason}:{name}",
                    "canonical_source_host": source_host,
                    "entity_scope": scope,
                }

    stripped = _strip_legal_suffix(company_name or "")
    if stripped and stripped != (company_name or "") and _contains_phrase(text, stripped):
        return {
            "entity_match_status": "exact",
            "entity_match_reason": f"mentions_target_without_legal_suffix:{stripped}",
            "canonical_source_host": source_host,
            "entity_scope": ENTITY_SCOPE_CANONICAL_GROUP,
        }

    tokens = _significant_tokens(company_name)
    primary = tokens[0] if tokens else ""
    target_full_seen = any(_contains_phrase(text, name) for name in canonical_names if name)
    source_slug = _host_slug(source_host)
    target_slug = _host_slug(target_host)

    if primary and primary in text and not target_full_seen:
        alternate_entity_markers = (
            f"{primary} inc", f"{primary} llc", f"{primary} ltd", f"{primary} limited",
            f"{primary} corporation", f"{primary} consultancy", f"{primary} nyc",
        )
        if any(marker in text for marker in alternate_entity_markers):
            return {
                "entity_match_status": "wrong",
                "entity_match_reason": "same_name_different_entity_in_page_text",
                "canonical_source_host": source_host,
                "entity_scope": ENTITY_SCOPE_WRONG_ENTITY,
            }
        if source_host and target_host and not _host_matches(source_host, target_host):
            if primary in source_slug and source_slug != target_slug:
                return {
                    "entity_match_status": "wrong",
                    "entity_match_reason": "same_name_different_source_domain",
                    "canonical_source_host": source_host,
                    "entity_scope": ENTITY_SCOPE_WRONG_ENTITY,
                }
        return {
            "entity_match_status": "weak",
            "entity_match_reason": "partial_name_match_only",
            "canonical_source_host": source_host,
        }

    return {
        "entity_match_status": "unknown",
        "entity_match_reason": "target_entity_not_detected",
        "canonical_source_host": source_host,
    }


def entity_search_terms(company_name: str | None) -> List[str]:
    """Names worth searching for the canonical target."""
    identity = get_company_identity(company_name)
    terms = []
    for group in (
        [identity.get("canonical_name", "")],
        identity.get("aliases", []),
        identity.get("former_names", []),
        _names(identity.get("legal_entities", [])),
        _names(identity.get("subsidiaries", [])),
    ):
        for term in group:
            if term and term not in terms:
                terms.append(term)
    return terms


def build_entity_context_block(company_name: str | None) -> str:
    """Prompt block for entity-aware scanning."""
    identity = get_company_identity(company_name)
    if not identity.get("entity_scope_policy"):
        return ""

    aliases = ", ".join(identity.get("aliases", []) or ["None"])
    former = ", ".join(identity.get("former_names", []) or ["None"])
    legal = ", ".join(_names(identity.get("legal_entities", [])) or ["None"])
    subsidiaries = ", ".join(_names(identity.get("subsidiaries", [])) or ["None"])
    owners = ", ".join(_names(identity.get("parent_or_owner", [])) or ["None"])
    events = "; ".join(
        f"{e.get('date')}: {e.get('event')}" for e in identity.get("transition_events", []) if e.get("event")
    ) or "None"

    return f"""
    ENTITY IDENTITY AWARENESS (CRITICAL):
    Canonical target: {identity.get("canonical_name") or company_name}
    Aliases/search names: {aliases}
    Former names/predecessors: {former}
    Local legal entities: {legal}
    Subsidiaries/operating entities: {subsidiaries}
    Parent/owner/acquirer context: {owners}
    Transition events: {events}

    Entity-scope policy: {identity.get("entity_scope_policy")}

    You MUST tag each evidence item with entity_scope:
    - "canonical_group" for the canonical target / group-level operating company.
    - "local_legal_entity" for country-specific legal entities such as an Oy/AB/AS/Ltd record.
    - "former_name" for predecessor or pre-carve-out sources.
    - "subsidiary" for subsidiary-specific evidence.
    - "parent_owner" for owner/acquirer/investor evidence.
    - "wrong_entity" if the source describes an unrelated company.
    Use local/subsidiary/parent evidence only with that scope; do not promote it into unqualified group-wide claims.
    """


def classify_entity_scope(link: dict, company_name: str | None = "") -> str:
    """Classify which entity a source appears to describe."""
    if not isinstance(link, dict):
        return ENTITY_SCOPE_UNKNOWN
    if link.get("wrong_entity") is True or link.get("entity_match") == "wrong":
        return ENTITY_SCOPE_WRONG_ENTITY

    identity = get_company_identity(company_name)
    text = _norm(" ".join([
        str(link.get("title") or ""),
        str(link.get("snippet") or link.get("evidence_snippet") or link.get("source_says") or ""),
        str(link.get("url") or link.get("source_url") or ""),
    ]))

    canonical = identity.get("canonical_name") or company_name or ""
    canonical_in_text = _contains_phrase(text, canonical) if canonical else False

    for name in _names(identity.get("parent_or_owner", [])):
        if _contains_phrase(text, name):
            owner_context = any(
                token in text for token in (
                    "acquir", "buy-out", "buyout", "owner", "backed by", "private equity",
                    "funds advised", "divest", "transaction", "purchase price"
                )
            )
            if owner_context or not canonical_in_text:
                return ENTITY_SCOPE_PARENT_OWNER

    for name in _names(identity.get("legal_entities", [])):
        if _contains_phrase(text, name):
            return ENTITY_SCOPE_LOCAL_LEGAL_ENTITY

    for name in _names(identity.get("subsidiaries", [])):
        if _contains_phrase(text, name):
            return ENTITY_SCOPE_SUBSIDIARY

    for name in identity.get("former_names", []) or []:
        if _contains_phrase(text, name):
            return ENTITY_SCOPE_FORMER_NAME

    if canonical_in_text:
        return ENTITY_SCOPE_CANONICAL_GROUP

    # Generic fallback for companies with no curated registry metadata.
    target = _norm(company_name or "")
    stripped_target = _norm(_strip_legal_suffix(company_name or ""))
    if target and target in text:
        return ENTITY_SCOPE_LOCAL_LEGAL_ENTITY if _LEGAL_SUFFIX_RE.search(company_name or "") else ENTITY_SCOPE_CANONICAL_GROUP
    if stripped_target and stripped_target in text:
        local_markers = (" oy", " ab", " as", " ltd", " limited", " finland", " suomi", " sweden", " norway")
        if any(marker in text for marker in local_markers):
            return ENTITY_SCOPE_LOCAL_LEGAL_ENTITY
        return ENTITY_SCOPE_CANONICAL_GROUP

    return ENTITY_SCOPE_UNKNOWN


def apply_entity_scope(link: dict, company_name: str | None = "") -> dict:
    """Mutate a link with deterministic entity-scope metadata."""
    if not isinstance(link, dict):
        return link
    scope = classify_entity_scope(link, company_name)
    existing = link.get("entity_scope")
    legacy_scope_map = {
        "group": ENTITY_SCOPE_CANONICAL_GROUP,
        "local": ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
    }
    if isinstance(existing, str) and existing in legacy_scope_map:
        link["entity_scope"] = legacy_scope_map[existing]
    elif not existing or existing == "unknown":
        link["entity_scope"] = scope
    else:
        link["entity_scope_inferred"] = scope

    identity = get_company_identity(company_name)
    if identity.get("canonical_name"):
        link.setdefault("canonical_company", identity["canonical_name"])
    if link.get("entity_scope") == ENTITY_SCOPE_WRONG_ENTITY:
        link["entity_match"] = "wrong"
    if link.get("entity_scope") in {
        ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
        ENTITY_SCOPE_SUBSIDIARY,
        ENTITY_SCOPE_PARENT_OWNER,
        ENTITY_SCOPE_FORMER_NAME,
    }:
        link["scope_warning"] = (
            f"Evidence scope is {link.get('entity_scope')}; do not use as unqualified "
            f"{identity.get('canonical_name') or company_name} group-wide evidence."
        )
    return link
