"""Shared evidence lifecycle contracts.

These markers prevent metadata produced in one lifecycle from being interpreted
as a verdict in another. They do not change evidence scoring or add a model
stage; they make the existing transitions explicit and auditable.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy


COMPANY_EVIDENCE_CANDIDATE = "company_evidence_candidate/v1"
VERIFIED_COMPANY_EVIDENCE = "verified_company_evidence/v1"
CENTRAL_FORESIGHT_SIGNAL = "central_foresight_signal/v1"
COMPANY_FORESIGHT_INTERPRETATION = "company_foresight_interpretation/v1"
JURY_EVIDENCE_PACKAGE = "jury_evidence_package/v1"
PARSED_SOURCE = "parsed_source/v1"
SOURCE_PASSAGE = "source_passage/v1"
SOURCE_PROPOSITION = "source_proposition/v1"
JURY_FINDING = "jury_finding/v1"
SYNTHESIS_CLAIM = "synthesis_claim/v1"

ADAPTABILITY_ARCHETYPES = {"Thriving", "Modernizing", "Cash Cow", "At Risk"}


def _stable_id(prefix: str, *parts) -> str:
    key = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12].upper()}"


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item]
    return [value]


COMPANY_VERDICT_FIELDS = {
    "promotion_status",
    "generation_eligible",
    "analysis_verified",
    "claim_support_status",
    "support_level",
    "entity_match_status",
    "content_entity_match_status",
    "entity_scope",
    "verification_reason",
    "url_verified",
    "content_verified",
    "content_readable",
    "verified",
    "entity_verified",
    "wrong_entity",
    "wrong_entity_true",
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "buying_evidence_eligible",
    "source_status",
}


CENTRAL_FORESIGHT_FORBIDDEN_FIELDS = {
    "promotion_status",
    "generation_eligible",
    "analysis_verified",
    "claim_support_status",
    "support_level",
    "entity_match_status",
    "content_entity_match_status",
    "entity_scope",
    "wrong_entity",
    "wrong_entity_true",
    "allowed_as_fact",
    "allowed_as_hypothesis",
    "allowed_as_discovery_question",
    "buying_evidence_eligible",
    "source_status",
    "internal_only",
    "blocked",
    "target_entity_verified",
    "company_name",
    "company_slug",
    "source_database",
    "scanner_origin",
}


FRESH_COMPANY_VERIFICATION_FIELDS = (
    "verified",
    "url_verified",
    "content_readable",
    "content_verified",
    "entity_verified",
    "entity_match_status",
    "content_entity_match_status",
    "entity_scope",
    "entity_mentioned",
    "generic_context_page",
    "source_type",
    "source_database",
)


def mark_company_candidate(record: dict) -> dict:
    candidate = deepcopy(record) if isinstance(record, dict) else {}
    candidate["evidence_contract"] = COMPANY_EVIDENCE_CANDIDATE
    return candidate


def current_company_verification(record: dict) -> dict:
    """Return only current-run verification fields from a fetched packet."""
    if not isinstance(record, dict):
        return {}
    return {
        field: record.get(field)
        for field in FRESH_COMPANY_VERIFICATION_FIELDS
        if field in record
    }


def mark_verified_company_evidence(record: dict) -> dict:
    verified = deepcopy(record) if isinstance(record, dict) else {}
    verified["evidence_contract"] = VERIFIED_COMPANY_EVIDENCE
    return verified


def normalize_central_foresight_signal(record: dict) -> tuple[dict, list[str]]:
    """Strip company-verdict metadata from a central, company-neutral signal."""
    signal = deepcopy(record) if isinstance(record, dict) else {}
    removed = sorted(field for field in CENTRAL_FORESIGHT_FORBIDDEN_FIELDS if field in signal)
    for field in removed:
        signal.pop(field, None)
    signal["evidence_contract"] = CENTRAL_FORESIGHT_SIGNAL
    signal["claim_type"] = "foresight_context"
    signal["company_specific"] = False
    signal["requires_company_specific_corroboration"] = True
    signal["not_evidence_of_deployment"] = True
    return signal, removed


def mark_company_foresight_interpretation(record: dict) -> dict:
    interpretation = deepcopy(record) if isinstance(record, dict) else {}
    interpretation["evidence_contract"] = COMPANY_FORESIGHT_INTERPRETATION
    interpretation["claim_type"] = "foresight_context"
    interpretation["company_specific"] = False
    interpretation["requires_company_specific_corroboration"] = True
    interpretation["not_evidence_of_deployment"] = True
    return interpretation


def mark_jury_evidence_package(record: dict) -> dict:
    package = deepcopy(record) if isinstance(record, dict) else {}
    package["evidence_contract"] = JURY_EVIDENCE_PACKAGE
    return package


def mark_jury_finding(record: dict) -> dict:
    finding = deepcopy(record) if isinstance(record, dict) else {}
    finding["finding_contract"] = JURY_FINDING
    finding.setdefault("finding_id", stable_jury_finding_id(finding))
    finding.setdefault("claim_support_relationship", build_claim_support_relationship(finding))
    return finding


def stable_jury_finding_id(record: dict) -> str:
    """Create a deterministic finding ID from the current verified evidence path."""
    if not isinstance(record, dict):
        record = {}
    return _stable_id(
        "FND",
        record.get("url") or record.get("source_url"),
        record.get("category") or record.get("jury"),
        record.get("evidence_package_id"),
        ",".join(str(item) for item in _as_list(record.get("proposition_ids"))),
        record.get("supported_claim") or record.get("label") or record.get("title"),
    )


def build_claim_support_relationship(record: dict, claim_id: str | None = None) -> dict:
    """Normalize the final source-to-claim relation without changing legacy fields."""
    item = deepcopy(record) if isinstance(record, dict) else {}
    support_status = (
        item.get("claim_relation_status")
        or item.get("claim_support_status")
        or item.get("support_level")
        or "claim_support_pending"
    )
    source_context_status = (
        item.get("source_context_status")
        or item.get("source_use_status")
        or item.get("promotion_status")
        or "unknown"
    )
    if item.get("generation_eligible") is True:
        final_decision = "generation_eligible"
    elif source_context_status in {"scoped_context", "valid_company_context", "valid_context_unused"}:
        final_decision = "analysis_premise"
    elif source_context_status in {"wrong_entity", "dead_or_unreadable", "out_of_context"}:
        final_decision = "blocked_source"
    elif support_status in {"unsupported", "unsupported_for_this_claim", "contradicts"}:
        final_decision = "claim_relation_rejected"
    else:
        final_decision = item.get("promotion_status") or "pending"

    relation_key = (
        claim_id
        or item.get("claim_id")
        or item.get("supported_claim")
        or item.get("label")
        or item.get("title")
        or ""
    )
    relationship = {
        "relationship_id": _stable_id(
            "CSR",
            relation_key,
            item.get("finding_id"),
            item.get("source_id"),
            item.get("url") or item.get("source_url"),
            ",".join(str(value) for value in _as_list(item.get("proposition_ids"))),
        ),
        "claim_id": claim_id or item.get("claim_id"),
        "finding_id": item.get("finding_id"),
        "source_id": item.get("source_id"),
        "parsed_source_id": item.get("parsed_source_id"),
        "evidence_package_id": item.get("evidence_package_id"),
        "passage_ids": _as_list(item.get("support_passage_ids") or item.get("support_passage_id")),
        "proposition_ids": _as_list(item.get("proposition_ids")),
        "source_context_status": source_context_status,
        "support_status": support_status,
        "support_level": item.get("support_level"),
        "final_decision": final_decision,
    }
    return {key: value for key, value in relationship.items() if value not in (None, [], "")}


def build_classification_namespaces(
    *,
    adaptability_archetype: str | None = None,
    favi_vulnerability_segment: str | None = None,
    adaptation_capacity_segment: str | None = None,
    foresight_force_profile: dict | str | None = None,
) -> dict:
    """Keep company, vulnerability and foresight labels in separate families."""
    force_profile = dict(foresight_force_profile or {}) if isinstance(foresight_force_profile, dict) else {}
    if isinstance(foresight_force_profile, str):
        force_profile["label"] = foresight_force_profile
    force_label = force_profile.get("label")
    if force_label in ADAPTABILITY_ARCHETYPES:
        force_profile["blocked_label"] = force_label
        force_profile["label"] = None
        force_profile["namespace_conflict"] = True
        force_profile["namespace_reason"] = (
            "Adaptability archetype labels cannot be reused as foresight force profiles."
        )
    force_profile.setdefault("classification_family", "foresight_force_profile")
    force_profile.setdefault("company_fact_eligible", False)
    force_profile.setdefault("not_evidence_of_company_action", True)
    return {
        "adaptability_archetype": {
            "classification_family": "adaptability_archetype",
            "label": adaptability_archetype if adaptability_archetype in ADAPTABILITY_ARCHETYPES else None,
            "allowed_labels": sorted(ADAPTABILITY_ARCHETYPES),
            "source_contract": "current_company_reality_x_financial_health",
        },
        "favi_vulnerability_segment": {
            "classification_family": "favi_vulnerability_segment",
            "label": favi_vulnerability_segment,
            "source_contract": "market_intelligence_favi",
        },
        "adaptation_capacity_segment": {
            "classification_family": "adaptation_capacity_segment",
            "label": adaptation_capacity_segment,
            "source_contract": "foresight_vulnerability_capacity",
        },
        "foresight_force_profile": force_profile,
    }
