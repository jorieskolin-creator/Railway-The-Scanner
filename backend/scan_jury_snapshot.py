"""Immutable, evidence-neutral input manifests for the future Jury adapter.

A Jury input snapshot binds one jury_analysis attempt to a passed Intent
checkpoint plus that attempt's Jury-stage candidates and retrievals. It is
operational lineage only: it does not make a source trusted, company-scoped,
eligible, or supportive of a claim.
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT
from scan_intent_snapshot import INTENT_STAGE
from scan_jury_candidates import ALLOWED_CANDIDATE_ORIGINS, JURY_ANALYSIS_STAGE
from scan_supervisor import (
    JuryInputSnapshot,
    JuryInputSnapshotIntegrityError,
    JurySourceCandidate,
    ScanCheckpoint,
    SourceRetrieval,
)


JURY_INPUT_SNAPSHOT_CONTRACT = "scanner.jury-input/v1"


def build_jury_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    intent_checkpoint: ScanCheckpoint,
    candidates: Sequence[JurySourceCandidate],
    retrievals: Sequence[SourceRetrieval],
) -> JuryInputSnapshot:
    """Build a deterministic manifest from a passed Intent checkpoint and Jury inputs."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    checkpoint_ref = _intent_checkpoint_ref(intent_checkpoint, run_id=run_id)
    candidate_refs = [
        _candidate_ref(candidate, run_id=run_id, attempt=attempt)
        for candidate in sorted(candidates, key=lambda item: item.candidate_id)
    ]
    retrieval_refs = [
        _retrieval_ref(retrieval, run_id=run_id, attempt=attempt)
        for retrieval in sorted(retrievals, key=lambda item: item.retrieval_id)
    ]
    if len({reference["candidate_id"] for reference in candidate_refs}) != len(candidate_refs):
        raise ValueError("candidate IDs must be unique in a Jury input snapshot.")
    if len({reference["retrieval_id"] for reference in retrieval_refs}) != len(retrieval_refs):
        raise ValueError("retrieval IDs must be unique in a Jury input snapshot.")

    payload = {
        "contract": JURY_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": JURY_ANALYSIS_STAGE,
        "attempt": attempt,
        "intent_checkpoint_ref": checkpoint_ref,
        "candidate_refs": candidate_refs,
        "retrieval_refs": retrieval_refs,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "JIS-" + _hash(
        {
            "run_id": run_id,
            "stage": JURY_ANALYSIS_STAGE,
            "attempt": attempt,
            "contract": JURY_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return JuryInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=JURY_ANALYSIS_STAGE,
        attempt=attempt,
        contract_version=JURY_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )


def validate_jury_input_snapshot_manifest(snapshot: JuryInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "intent_checkpoint_ref",
        "candidate_refs",
        "retrieval_refs",
        "input_hash",
    }
    if set(manifest) != expected_keys:
        raise JuryInputSnapshotIntegrityError("Jury input manifest has unexpected fields.")
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise JuryInputSnapshotIntegrityError(
            "Jury input snapshot does not match its deterministic manifest."
        )
    candidate_refs = manifest.get("candidate_refs")
    retrieval_refs = manifest.get("retrieval_refs")
    checkpoint_ref = manifest.get("intent_checkpoint_ref")
    if not isinstance(candidate_refs, list) or not isinstance(retrieval_refs, list):
        raise JuryInputSnapshotIntegrityError("Jury input manifest references must be lists.")
    if not isinstance(checkpoint_ref, dict):
        raise JuryInputSnapshotIntegrityError("Jury input manifest Intent checkpoint ref is invalid.")
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "intent_checkpoint_ref": checkpoint_ref,
            "candidate_refs": candidate_refs,
            "retrieval_refs": retrieval_refs,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise JuryInputSnapshotIntegrityError(
            "Jury input snapshot does not match its deterministic manifest."
        )
    expected_id = "JIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise JuryInputSnapshotIntegrityError(
            "Jury input snapshot identity does not match its immutable hash."
        )


def assert_jury_input_snapshot_matches(
    snapshot: JuryInputSnapshot,
    *,
    intent_checkpoint: ScanCheckpoint,
    candidates: Sequence[JurySourceCandidate],
    retrievals: Sequence[SourceRetrieval],
) -> None:
    """Reject a manifest that no longer binds the exact verified durable inputs."""

    try:
        expected = build_jury_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            intent_checkpoint=intent_checkpoint,
            candidates=candidates,
            retrievals=retrievals,
        )
    except (TypeError, ValueError) as error:
        raise JuryInputSnapshotIntegrityError(
            "Jury input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise JuryInputSnapshotIntegrityError(
            "Jury input manifest does not match the verified durable inputs."
        )


def _intent_checkpoint_ref(checkpoint: ScanCheckpoint, *, run_id: str) -> dict[str, object]:
    if checkpoint.run_id != run_id:
        raise ValueError("Intent checkpoint does not match the Jury run.")
    if checkpoint.stage_name != INTENT_STAGE:
        raise ValueError("Jury input requires a passed Intent checkpoint.")
    if checkpoint.attempt < 1:
        raise ValueError("Intent checkpoint attempt must be positive.")
    if checkpoint.contract_version != INTENT_CHECKPOINT_CONTRACT:
        raise ValueError("Intent checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("Jury input requires a passed Intent checkpoint.")
    _require_sha256(checkpoint.input_hash, "Intent checkpoint input")
    _require_sha256(checkpoint.output_hash, "Intent checkpoint output")
    if not checkpoint.checkpoint_id:
        raise ValueError("Intent checkpoint identity is required.")
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "stage": INTENT_STAGE,
        "attempt": checkpoint.attempt,
        "contract_version": checkpoint.contract_version,
        "input_hash": checkpoint.input_hash,
        "output_hash": checkpoint.output_hash,
    }


def _candidate_ref(
    candidate: JurySourceCandidate,
    *,
    run_id: str,
    attempt: int,
) -> dict[str, object]:
    if (
        candidate.run_id != run_id
        or candidate.stage_name != JURY_ANALYSIS_STAGE
        or candidate.attempt != attempt
    ):
        raise ValueError("Jury source candidate identity does not match the Jury attempt.")
    if candidate.candidate_origin not in ALLOWED_CANDIDATE_ORIGINS:
        raise ValueError(f"Jury source candidate origin is invalid: {candidate.candidate_id}.")
    _require_sha256(candidate.cached_extract_sha256, "candidate cached extract")
    if _sha256_text(candidate.cached_extract_text) != candidate.cached_extract_sha256:
        raise ValueError(f"Candidate cached extract hash mismatch: {candidate.candidate_id}.")
    return {
        "candidate_id": candidate.candidate_id,
        "candidate_origin": candidate.candidate_origin,
        "source_url": candidate.source_url,
        "canonical_url": candidate.canonical_url,
        "recorded_at": candidate.recorded_at.isoformat(),
        "parser_route": candidate.parser_route,
        "cached_extract_sha256": candidate.cached_extract_sha256,
    }


def _retrieval_ref(
    retrieval: SourceRetrieval,
    *,
    run_id: str,
    attempt: int,
) -> dict[str, object]:
    if (
        retrieval.run_id != run_id
        or retrieval.stage_name != JURY_ANALYSIS_STAGE
        or retrieval.attempt != attempt
    ):
        raise ValueError("Source retrieval identity does not match the Jury attempt.")
    _require_sha256(retrieval.response_sha256, "retrieval response")
    _require_sha256(retrieval.extracted_text_sha256, "retrieval extracted text")
    if _sha256_text(retrieval.extracted_text) != retrieval.extracted_text_sha256:
        raise ValueError(f"Retrieval extracted text hash mismatch: {retrieval.retrieval_id}.")
    if retrieval.readability_status not in {"readable", "unreadable", "unknown"}:
        raise ValueError(f"Retrieval readability status is invalid: {retrieval.retrieval_id}.")
    if retrieval.retrieval_outcome not in {"succeeded", "not_found", "blocked", "failed"}:
        raise ValueError(f"Retrieval outcome is invalid: {retrieval.retrieval_id}.")
    return {
        "retrieval_id": retrieval.retrieval_id,
        "retrieval_origin": retrieval.retrieval_origin,
        "source_url": retrieval.source_url,
        "canonical_url": retrieval.canonical_url,
        "retrieved_at": retrieval.retrieved_at.isoformat(),
        "response_sha256": retrieval.response_sha256,
        "extracted_text_sha256": retrieval.extracted_text_sha256,
        "parser_route": retrieval.parser_route,
        "readability_status": retrieval.readability_status,
        "retrieval_outcome": retrieval.retrieval_outcome,
    }


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{label} hash must be a SHA-256 digest.")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
