"""Versioned Jury input projection and output checkpoint contract.

These builders bind a verified Jury input snapshot to adapter packets and a
separately versioned checkpoint proposal. They do not invoke five-Jury
cognition, persist state, or grant evidence, eligibility, or publication
meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from scan_jury_candidates import FORBIDDEN_METADATA_KEYS, JURY_ANALYSIS_STAGE
from scan_jury_snapshot import assert_jury_input_snapshot_matches
from scan_supervisor import (
    JuryInputSnapshot,
    JuryInputSnapshotIntegrityError,
    JurySourceCandidate,
    ScanCheckpoint,
    SourceRetrieval,
)


JURY_PROJECTION_CONTRACT = "scanner.jury-projection/v1"
JURY_CHECKPOINT_CONTRACT = "scanner.jury/v1"
JURY_CHECKPOINT_LANES = (
    "ARCHITECTURAL JURY (Engineering Reality)",
    "STRATEGIC JURY (Business Model, Procurement & Customer Experience)",
    "CULTURAL JURY (Talent & Psychological Safety)",
    "AI & DATA JURY (Future Readiness)",
    "SERVICE PORTFOLIO JURY (Delivery Reality)",
)
_JURY_RESULT_KEYS = {"juries"}
_JURY_PAYLOAD_REQUIRED_KEYS = {
    "jury",
    "findings",
    "rejected_evidence",
    "contradictions",
    "missing_coverage",
    "confidence_score",
    "reality_score_adjustment",
}
_JURY_PAYLOAD_OPTIONAL_KEYS = {"split_analysis"}
_FINDING_REQUIRED_KEYS = {
    "category",
    "supported_claim",
    "found",
    "sentiment",
    "source_quote",
    "title",
    "url",
    "is_recent",
    "source_type",
    "evidence_package_id",
    "proposition_ids",
    "support_passage_ids",
    "subdimensions",
    "entity_scope",
    "source_verdict",
    "support_level",
    "confidence",
}
_FINDING_OPTIONAL_KEYS = {"source_id"}
_REJECTED_EVIDENCE_KEYS = {"url", "reason"}


@dataclass(frozen=True)
class JuryInputProjection:
    """Exact adapter-facing view of one verified Jury input snapshot.

    `packets` is the only payload a later adapter may pass to existing Jury
    packet helpers. Packet excerpts are not stored on the checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    intent_checkpoint_ref: Mapping[str, object]
    payload_sha256: str
    packets: tuple[Mapping[str, Any], ...]


def build_jury_input_projection(
    snapshot: JuryInputSnapshot,
    *,
    intent_checkpoint: ScanCheckpoint,
    candidates: Sequence[JurySourceCandidate],
    retrievals: Sequence[SourceRetrieval],
) -> JuryInputProjection:
    """Project verified durable inputs into versioned Jury packets."""

    try:
        assert_jury_input_snapshot_matches(
            snapshot,
            intent_checkpoint=intent_checkpoint,
            candidates=candidates,
            retrievals=retrievals,
        )
    except JuryInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise JuryInputSnapshotIntegrityError(
            "Jury input projection cannot be built from durable inputs."
        ) from error

    ordered_candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    ordered_retrievals = tuple(sorted(retrievals, key=lambda item: item.retrieval_id))
    packets = tuple(
        [_packet_from_candidate(candidate) for candidate in ordered_candidates]
        + [_packet_from_retrieval(retrieval) for retrieval in ordered_retrievals]
    )
    input_refs = tuple(
        [_candidate_input_ref(candidate) for candidate in ordered_candidates]
        + [_retrieval_input_ref(retrieval) for retrieval in ordered_retrievals]
    )
    return JuryInputProjection(
        contract=JURY_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=JURY_ANALYSIS_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        intent_checkpoint_ref=dict(snapshot.manifest["intent_checkpoint_ref"]),
        payload_sha256=_hash([_packet_digest_payload(packet) for packet in packets]),
        packets=packets,
    )


def build_jury_checkpoint(
    projection: JuryInputProjection,
    *,
    jury_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build a Jury output checkpoint from a projection and a supplied result.

    The caller supplies the five-Jury result mapping. This function never
    acquires sources, never calls a model, and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_jury_result(jury_result)
    if compact_result is None:
        gate_result = {
            "gate": JURY_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_jury_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        jury_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": JURY_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "JRO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": JURY_ANALYSIS_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "jury_result",
            }
        ]
        jury_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": JURY_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": JURY_ANALYSIS_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "intent_checkpoint_ref": dict(projection.intent_checkpoint_ref),
        "payload_sha256": projection.payload_sha256,
    }
    if jury_payload is not None:
        snapshot["jury_result"] = jury_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": JURY_ANALYSIS_STAGE,
            "attempt": projection.attempt,
            "contract": JURY_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=JURY_ANALYSIS_STAGE,
        attempt=projection.attempt,
        contract_version=JURY_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def _validate_projection(projection: JuryInputProjection) -> None:
    if projection.contract != JURY_PROJECTION_CONTRACT:
        raise JuryInputSnapshotIntegrityError("Jury projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != JURY_ANALYSIS_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise JuryInputSnapshotIntegrityError("Jury projection identity is invalid.")
    expected_payload_hash = _hash(
        [_packet_digest_payload(packet) for packet in projection.packets]
    )
    if expected_payload_hash != projection.payload_sha256:
        raise JuryInputSnapshotIntegrityError(
            "Jury projection payload hash does not match packets."
        )
    for packet in projection.packets:
        excerpt = _packet_excerpt(packet)
        if _sha256_text(excerpt) != packet.get("extract_sha256"):
            raise JuryInputSnapshotIntegrityError(
                "Jury projection packet extract hash does not match packet text."
            )


def _packet_from_candidate(candidate: JurySourceCandidate) -> dict[str, Any]:
    metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
    title = metadata.get("discovery_label")
    source_type = metadata.get("source_type")
    return {
        "source_id": candidate.candidate_id,
        "url": candidate.canonical_url,
        "title": title if isinstance(title, str) else "",
        "source_type": source_type if isinstance(source_type, str) else "",
        "snippet": "",
        "cached_excerpt": candidate.cached_extract_text,
        "fetched_excerpt": "",
        "source_database": (
            "company_evidence_db"
            if candidate.candidate_origin == "company_evidence_db"
            else ""
        ),
        "forensic_provenance": (
            "fresh_acquisition"
            if candidate.candidate_origin == "jury_live_acquisition_seed"
            else ""
        ),
        "packet_kind": "jury_source_candidate",
        "extract_sha256": candidate.cached_extract_sha256,
    }


def _packet_from_retrieval(retrieval: SourceRetrieval) -> dict[str, Any]:
    return {
        "source_id": retrieval.retrieval_id,
        "url": retrieval.canonical_url,
        "title": "",
        "source_type": "",
        "snippet": "",
        "cached_excerpt": "",
        "fetched_excerpt": retrieval.extracted_text,
        "content_readable": retrieval.readability_status == "readable",
        "forensic_provenance": "fresh_acquisition",
        "packet_kind": "source_retrieval",
        "extract_sha256": retrieval.extracted_text_sha256,
    }


def _packet_excerpt(packet: Mapping[str, Any]) -> str:
    kind = packet.get("packet_kind")
    if kind == "jury_source_candidate":
        return str(packet.get("cached_excerpt") or "")
    if kind == "source_retrieval":
        return str(packet.get("fetched_excerpt") or "")
    return ""


def _packet_digest_payload(packet: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(packet.get("source_id") or ""),
        "kind": str(packet.get("packet_kind") or ""),
        "url": str(packet.get("url") or ""),
        "cached_excerpt": str(packet.get("cached_excerpt") or ""),
        "fetched_excerpt": str(packet.get("fetched_excerpt") or ""),
        "extract_sha256": str(packet.get("extract_sha256") or ""),
    }


def _candidate_input_ref(candidate: JurySourceCandidate) -> dict[str, str]:
    return {
        "id": candidate.candidate_id,
        "sha256": candidate.cached_extract_sha256,
        "kind": "jury_source_candidate",
    }


def _retrieval_input_ref(retrieval: SourceRetrieval) -> dict[str, str]:
    return {
        "id": retrieval.retrieval_id,
        "sha256": retrieval.extracted_text_sha256,
        "kind": "source_retrieval",
    }


def _compact_jury_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _JURY_RESULT_KEYS:
        return None
    payloads = value.get("juries")
    if not isinstance(payloads, list) or len(payloads) != len(JURY_CHECKPOINT_LANES):
        return None
    compact_payloads = [_compact_jury_payload(item) for item in payloads]
    if any(item is None for item in compact_payloads):
        return None
    names = tuple(item["jury"] for item in compact_payloads)
    if names != JURY_CHECKPOINT_LANES:
        return None
    return {"juries": compact_payloads}


def _compact_jury_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    keys = set(value)
    if not _JURY_PAYLOAD_REQUIRED_KEYS <= keys:
        return None
    if keys - _JURY_PAYLOAD_REQUIRED_KEYS - _JURY_PAYLOAD_OPTIONAL_KEYS:
        return None
    if not isinstance(value.get("jury"), str) or value["jury"] not in JURY_CHECKPOINT_LANES:
        return None
    if type(value.get("confidence_score")) is not int:
        return None
    if type(value.get("reality_score_adjustment")) is not int:
        return None
    findings = _compact_findings(value.get("findings"))
    rejected = _compact_rejected_evidence(value.get("rejected_evidence"))
    contradictions = _compact_string_list(value.get("contradictions"))
    missing_coverage = _compact_string_list(value.get("missing_coverage"))
    if None in (findings, rejected, contradictions, missing_coverage):
        return None
    compact: dict[str, Any] = {
        "jury": value["jury"],
        "findings": findings,
        "rejected_evidence": rejected,
        "contradictions": contradictions,
        "missing_coverage": missing_coverage,
        "confidence_score": value["confidence_score"],
        "reality_score_adjustment": value["reality_score_adjustment"],
    }
    if "split_analysis" in value:
        if type(value.get("split_analysis")) is not bool:
            return None
        compact["split_analysis"] = value["split_analysis"]
    if _contains_forbidden_keys(compact):
        return None
    return compact


def _compact_findings(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    compact_items = []
    for item in value:
        compact = _compact_finding(item)
        if compact is None:
            return None
        compact_items.append(compact)
    return compact_items


def _compact_finding(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    keys = set(value)
    if not _FINDING_REQUIRED_KEYS <= keys:
        return None
    if keys - _FINDING_REQUIRED_KEYS - _FINDING_OPTIONAL_KEYS:
        return None
    compact = _json_safe_mapping(value)
    if compact is None or _contains_forbidden_keys(compact):
        return None
    return compact


def _compact_rejected_evidence(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    compact_items = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _REJECTED_EVIDENCE_KEYS:
            return None
        if not isinstance(item.get("url"), str) or not isinstance(item.get("reason"), str):
            return None
        compact_items.append({"url": item["url"], "reason": item["reason"]})
    return compact_items


def _compact_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) for item in value):
        return None
    return list(value)


def _contains_forbidden_keys(value: object) -> bool:
    if isinstance(value, Mapping):
        if any(key in FORBIDDEN_METADATA_KEYS for key in value):
            return True
        return any(_contains_forbidden_keys(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_keys(item) for item in value)
    return False


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(
            json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
    except (TypeError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
