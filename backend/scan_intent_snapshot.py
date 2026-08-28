"""Immutable, evidence-neutral input manifests for the future Intent adapter.

An Intent input snapshot binds exactly the material and source-retrieval records
that a later adapter may hydrate. It is operational lineage only: it does not
make a source trusted, company-scoped, eligible, or supportive of a claim.
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from scan_supervisor import (
    IntentInputSnapshot,
    IntentInputSnapshotIntegrityError,
    ScanMaterial,
    SourceRetrieval,
)


INTENT_INPUT_SNAPSHOT_CONTRACT = "scanner.intent-input/v1"
INTENT_STAGE = "intent"


def build_intent_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    materials: Sequence[ScanMaterial],
    retrievals: Sequence[SourceRetrieval],
) -> IntentInputSnapshot:
    """Build a deterministic manifest from already hash-verified durable input."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    material_refs = [_material_ref(material) for material in sorted(materials, key=lambda item: item.material_id)]
    retrieval_refs = [_retrieval_ref(retrieval, run_id=run_id, attempt=attempt) for retrieval in sorted(retrievals, key=lambda item: item.retrieval_id)]
    if len({reference["material_id"] for reference in material_refs}) != len(material_refs):
        raise ValueError("material IDs must be unique in an Intent input snapshot.")
    if len({reference["retrieval_id"] for reference in retrieval_refs}) != len(retrieval_refs):
        raise ValueError("retrieval IDs must be unique in an Intent input snapshot.")
    failed_material_ids = [
        reference["material_id"]
        for reference in material_refs
        if reference["extraction_status"] == "failed"
    ]
    if failed_material_ids:
        raise ValueError(
            "Intent input cannot be built from failed material: " + ", ".join(failed_material_ids)
        )

    payload = {
        "contract": INTENT_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": INTENT_STAGE,
        "attempt": attempt,
        "material_refs": material_refs,
        "retrieval_refs": retrieval_refs,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "IIS-" + _hash(
        {
            "run_id": run_id,
            "stage": INTENT_STAGE,
            "attempt": attempt,
            "contract": INTENT_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return IntentInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=INTENT_STAGE,
        attempt=attempt,
        contract_version=INTENT_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )



def validate_intent_input_snapshot_manifest(snapshot: IntentInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract", "run_id", "stage", "attempt", "material_refs",
        "retrieval_refs", "input_hash",
    }
    if set(manifest) != expected_keys:
        raise IntentInputSnapshotIntegrityError("Intent input manifest has unexpected fields.")
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise IntentInputSnapshotIntegrityError(
            "Intent input snapshot does not match its deterministic manifest."
        )
    material_refs = manifest.get("material_refs")
    retrieval_refs = manifest.get("retrieval_refs")
    if not isinstance(material_refs, list) or not isinstance(retrieval_refs, list):
        raise IntentInputSnapshotIntegrityError("Intent input manifest references must be lists.")
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "material_refs": material_refs,
            "retrieval_refs": retrieval_refs,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise IntentInputSnapshotIntegrityError(
            "Intent input snapshot does not match its deterministic manifest."
        )
    expected_id = "IIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise IntentInputSnapshotIntegrityError(
            "Intent input snapshot identity does not match its immutable hash."
        )

def assert_intent_input_snapshot_matches(
    snapshot: IntentInputSnapshot,
    *,
    materials: Sequence[ScanMaterial],
    retrievals: Sequence[SourceRetrieval],
) -> None:
    """Reject a manifest that no longer binds the exact verified durable inputs."""

    try:
        expected = build_intent_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            materials=materials,
            retrievals=retrievals,
        )
    except (TypeError, ValueError) as error:
        raise IntentInputSnapshotIntegrityError(
            "Intent input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise IntentInputSnapshotIntegrityError(
            "Intent input manifest does not match the verified durable inputs."
        )


def _material_ref(material: ScanMaterial) -> dict[str, object]:
    _require_sha256(material.content_sha256, "material content")
    _require_sha256(material.extracted_text_sha256, "material extracted text")
    if _sha256_text(material.extracted_text) != material.extracted_text_sha256:
        raise ValueError(f"Material extracted text hash mismatch: {material.material_id}.")
    if material.extraction_status not in {"extracted", "empty", "failed"}:
        raise ValueError(f"Material extraction status is invalid: {material.material_id}.")
    return {
        "material_id": material.material_id,
        "origin": material.origin,
        "filename": material.filename,
        "media_type": material.media_type,
        "byte_size": material.byte_size,
        "content_sha256": material.content_sha256,
        "extracted_text_sha256": material.extracted_text_sha256,
        "parser_version": material.parser_version,
        "extraction_status": material.extraction_status,
        "allowed_use_classification": material.allowed_use_classification,
    }


def _retrieval_ref(
    retrieval: SourceRetrieval,
    *,
    run_id: str,
    attempt: int,
) -> dict[str, object]:
    if (
        retrieval.run_id != run_id
        or retrieval.stage_name != INTENT_STAGE
        or retrieval.attempt != attempt
    ):
        raise ValueError("Source retrieval identity does not match the Intent attempt.")
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
