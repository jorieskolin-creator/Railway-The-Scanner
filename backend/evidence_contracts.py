"""Shared evidence lifecycle contracts.

These markers prevent metadata produced in one lifecycle from being interpreted
as a verdict in another. They do not change evidence scoring or add a model
stage; they make the existing transitions explicit and auditable.
"""

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
    return finding
