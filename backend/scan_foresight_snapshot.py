"""Immutable, evidence-neutral input manifests for the future Foresight adapter.

A Foresight input snapshot binds one foresight attempt to a passed Diagnosis
checkpoint. It is operational lineage only: it does not make a finding true,
eligible, or publishable, and it does not invoke Foresight generation or copy
Foresight Knowledge Base content.
"""

from __future__ import annotations

import hashlib
import json

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT
from scan_diagnosis_snapshot import DIAGNOSIS_STAGE
from scan_supervisor import (
    ForesightInputSnapshot,
    ForesightInputSnapshotIntegrityError,
    ScanCheckpoint,
)


FORESIGHT_INPUT_SNAPSHOT_CONTRACT = "scanner.foresight-input/v1"
FORESIGHT_STAGE = "foresight"


def build_foresight_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    diagnosis_checkpoint: ScanCheckpoint,
) -> ForesightInputSnapshot:
    """Build a deterministic manifest from a passed Diagnosis checkpoint."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    checkpoint_ref = _diagnosis_checkpoint_ref(diagnosis_checkpoint, run_id=run_id)
    payload = {
        "contract": FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": FORESIGHT_STAGE,
        "attempt": attempt,
        "diagnosis_checkpoint_ref": checkpoint_ref,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "FIS-" + _hash(
        {
            "run_id": run_id,
            "stage": FORESIGHT_STAGE,
            "attempt": attempt,
            "contract": FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return ForesightInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=FORESIGHT_STAGE,
        attempt=attempt,
        contract_version=FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )


def validate_foresight_input_snapshot_manifest(snapshot: ForesightInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "diagnosis_checkpoint_ref",
        "input_hash",
    }
    if set(manifest) != expected_keys:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input manifest has unexpected fields."
        )
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input snapshot does not match its deterministic manifest."
        )
    checkpoint_ref = manifest.get("diagnosis_checkpoint_ref")
    if not isinstance(checkpoint_ref, dict):
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input manifest Diagnosis checkpoint ref is invalid."
        )
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "diagnosis_checkpoint_ref": checkpoint_ref,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input snapshot does not match its deterministic manifest."
        )
    expected_id = "FIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input snapshot identity does not match its immutable hash."
        )


def assert_foresight_input_snapshot_matches(
    snapshot: ForesightInputSnapshot,
    *,
    diagnosis_checkpoint: ScanCheckpoint,
) -> None:
    """Reject a manifest that no longer binds the exact verified Diagnosis checkpoint."""

    try:
        expected = build_foresight_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            diagnosis_checkpoint=diagnosis_checkpoint,
        )
    except (TypeError, ValueError) as error:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input manifest does not match the verified durable inputs."
        )


def _diagnosis_checkpoint_ref(checkpoint: ScanCheckpoint, *, run_id: str) -> dict[str, object]:
    if checkpoint.run_id != run_id:
        raise ValueError("Diagnosis checkpoint does not match the Foresight run.")
    if checkpoint.stage_name != DIAGNOSIS_STAGE:
        raise ValueError("Foresight input requires a passed Diagnosis checkpoint.")
    if checkpoint.attempt < 1:
        raise ValueError("Diagnosis checkpoint attempt must be positive.")
    if checkpoint.contract_version != DIAGNOSIS_CHECKPOINT_CONTRACT:
        raise ValueError("Diagnosis checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("Foresight input requires a passed Diagnosis checkpoint.")
    _require_sha256(checkpoint.input_hash, "Diagnosis checkpoint input")
    _require_sha256(checkpoint.output_hash, "Diagnosis checkpoint output")
    if not checkpoint.checkpoint_id:
        raise ValueError("Diagnosis checkpoint identity is required.")
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "stage": DIAGNOSIS_STAGE,
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
