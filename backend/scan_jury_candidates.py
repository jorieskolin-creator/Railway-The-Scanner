"""Immutable Jury-stage source-candidate provenance.

Candidates record which URLs (and optional cached extracts) may later be
hydrated for five-Jury analysis. They are not Company Evidence, Foresight
eligibility, claim support, or publication authority.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from scan_retrievals import canonicalize_source_url
from scan_supervisor import JurySourceCandidate


JURY_ANALYSIS_STAGE = "jury_analysis"
ALLOWED_CANDIDATE_ORIGINS = {
    "company_evidence_db",
    "previous_scan_source_cache",
    "jury_live_acquisition_seed",
}
FORBIDDEN_METADATA_KEYS = {
    "claim_support",
    "generation_eligible",
    "analysis_verified",
    "allowed_as_fact",
    "promotion_status",
    "foresight_eligible",
    "entity_verified",
    "entity_match_status",
}


def prepare_jury_source_candidate(
    *,
    run_id: str,
    attempt: int,
    candidate_origin: str,
    source_url: str,
    recorded_at: datetime,
    cached_extract_text: str = "",
    parser_route: str = "none/v1",
    extraction_warnings: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> JurySourceCandidate:
    """Prepare one hash-bound Jury candidate without retaining raw bytes."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")
    if candidate_origin not in ALLOWED_CANDIDATE_ORIGINS:
        raise ValueError("candidate_origin is invalid.")
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware.")
    if not isinstance(cached_extract_text, str):
        raise TypeError("cached_extract_text must be text.")
    if not parser_route:
        raise ValueError("parser_route is required.")
    normalized_recorded_at = recorded_at.astimezone(timezone.utc)
    canonical_url = canonicalize_source_url(source_url)
    safe_metadata = dict(metadata or {})
    _reject_evidence_metadata(safe_metadata)
    _reject_raw_bytes(safe_metadata)
    extract_sha256 = _sha256(cached_extract_text.encode("utf-8"))
    candidate_id = "JSC-" + _sha256(
        "\0".join(
            (
                run_id,
                JURY_ANALYSIS_STAGE,
                str(attempt),
                candidate_origin,
                canonical_url,
                extract_sha256,
            )
        ).encode("utf-8")
    )[:32].upper()
    return JurySourceCandidate(
        candidate_id=candidate_id,
        run_id=run_id,
        stage_name=JURY_ANALYSIS_STAGE,
        attempt=attempt,
        candidate_origin=candidate_origin,
        source_url=source_url,
        canonical_url=canonical_url,
        recorded_at=normalized_recorded_at,
        parser_route=parser_route,
        cached_extract_sha256=extract_sha256,
        cached_extract_text=cached_extract_text,
        extraction_warnings=tuple(extraction_warnings),
        metadata=safe_metadata,
    )


def _reject_evidence_metadata(metadata: Mapping[str, Any]) -> None:
    blocked = sorted(key for key in metadata if key in FORBIDDEN_METADATA_KEYS)
    if blocked:
        raise ValueError(
            "Jury source candidate metadata cannot include evidence fields: "
            + ", ".join(blocked)
        )


def _reject_raw_bytes(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Jury source candidate metadata keys must be strings.")
            _reject_raw_bytes(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_raw_bytes(item)
        return
    raise ValueError("Jury source candidate metadata must be JSON-safe and contain no raw bytes.")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
