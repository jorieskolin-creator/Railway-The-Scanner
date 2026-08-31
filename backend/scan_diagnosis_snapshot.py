"""Immutable, evidence-neutral input manifests for the future Diagnosis adapter.

A Diagnosis input snapshot binds one diagnosis attempt to a passed Jury
checkpoint. It is operational lineage only: it does not make a finding true,
eligible, or publishable, and it does not invoke Diagnosis generation.
"""

from __future__ import annotations

import hashlib
import json

from scan_jury_candidates import JURY_ANALYSIS_STAGE
from scan_jury_checkpoint import JURY_CHECKPOINT_CONTRACT
from scan_supervisor import (
    DiagnosisInputSnapshot,
    DiagnosisInputSnapshotIntegrityError,
    ScanCheckpoint,
)


DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT = "scanner.diagnosis-input/v1"
DIAGNOSIS_STAGE = "diagnosis"


def build_diagnosis_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    jury_checkpoint: ScanCheckpoint,
) -> DiagnosisInputSnapshot:
    """Build a deterministic manifest from a passed Jury checkpoint."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    checkpoint_ref = _jury_checkpoint_ref(jury_checkpoint, run_id=run_id)
    payload = {
        "contract": DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": DIAGNOSIS_STAGE,
        "attempt": attempt,
        "jury_checkpoint_ref": checkpoint_ref,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "DIS-" + _hash(
        {
            "run_id": run_id,
            "stage": DIAGNOSIS_STAGE,
            "attempt": attempt,
            "contract": DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return DiagnosisInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=DIAGNOSIS_STAGE,
        attempt=attempt,
        contract_version=DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )


def validate_diagnosis_input_snapshot_manifest(snapshot: DiagnosisInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "jury_checkpoint_ref",
        "input_hash",
    }
    if set(manifest) != expected_keys:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input manifest has unexpected fields."
        )
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input snapshot does not match its deterministic manifest."
        )
    checkpoint_ref = manifest.get("jury_checkpoint_ref")
    if not isinstance(checkpoint_ref, dict):
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input manifest Jury checkpoint ref is invalid."
        )
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "jury_checkpoint_ref": checkpoint_ref,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input snapshot does not match its deterministic manifest."
        )
    expected_id = "DIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input snapshot identity does not match its immutable hash."
        )


def assert_diagnosis_input_snapshot_matches(
    snapshot: DiagnosisInputSnapshot,
    *,
    jury_checkpoint: ScanCheckpoint,
) -> None:
    """Reject a manifest that no longer binds the exact verified Jury checkpoint."""

    try:
        expected = build_diagnosis_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            jury_checkpoint=jury_checkpoint,
        )
    except (TypeError, ValueError) as error:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input manifest does not match the verified durable inputs."
        )


def _jury_checkpoint_ref(checkpoint: ScanCheckpoint, *, run_id: str) -> dict[str, object]:
    if checkpoint.run_id != run_id:
        raise ValueError("Jury checkpoint does not match the Diagnosis run.")
    if checkpoint.stage_name != JURY_ANALYSIS_STAGE:
        raise ValueError("Diagnosis input requires a passed Jury checkpoint.")
    if checkpoint.attempt < 1:
        raise ValueError("Jury checkpoint attempt must be positive.")
    if checkpoint.contract_version != JURY_CHECKPOINT_CONTRACT:
        raise ValueError("Jury checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("Diagnosis input requires a passed Jury checkpoint.")
    _require_sha256(checkpoint.input_hash, "Jury checkpoint input")
    _require_sha256(checkpoint.output_hash, "Jury checkpoint output")
    if not checkpoint.checkpoint_id:
        raise ValueError("Jury checkpoint identity is required.")
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "stage": JURY_ANALYSIS_STAGE,
        "attempt": checkpoint.attempt,
        "contract_version": checkpoint.contract_version,
        "input_hash": checkpoint.input_hash,
        "output_hash": checkpoint.output_hash,
    }


def _require_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{label} hash must be a SHA-256 digest.")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
