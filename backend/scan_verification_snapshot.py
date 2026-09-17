"""Immutable, evidence-neutral input manifests for the future Verification adapter.

A Verification input snapshot binds one verification attempt to a passed
Strategic Synthesis checkpoint. It is operational lineage only: it does not
make a finding true, eligible, or publishable, and it does not invoke
Verification generation or copy Foresight Knowledge Base content.
"""

from __future__ import annotations

import hashlib
import json

from scan_supervisor import (
    ScanCheckpoint,
    VerificationInputSnapshot,
    VerificationInputSnapshotIntegrityError,
)
from scan_synthesis_checkpoint import SYNTHESIS_CHECKPOINT_CONTRACT
from scan_synthesis_snapshot import SYNTHESIS_STAGE


VERIFICATION_INPUT_SNAPSHOT_CONTRACT = "scanner.verification-input/v1"
VERIFICATION_STAGE = "verification"


def build_verification_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    synthesis_checkpoint: ScanCheckpoint,
) -> VerificationInputSnapshot:
    """Build a deterministic manifest from a passed Synthesis checkpoint."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    checkpoint_ref = _synthesis_checkpoint_ref(synthesis_checkpoint, run_id=run_id)
    payload = {
        "contract": VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": VERIFICATION_STAGE,
        "attempt": attempt,
        "synthesis_checkpoint_ref": checkpoint_ref,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "VIS-" + _hash(
        {
            "run_id": run_id,
            "stage": VERIFICATION_STAGE,
            "attempt": attempt,
            "contract": VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return VerificationInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=VERIFICATION_STAGE,
        attempt=attempt,
        contract_version=VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )


def validate_verification_input_snapshot_manifest(snapshot: VerificationInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "synthesis_checkpoint_ref",
        "input_hash",
    }
    if set(manifest) != expected_keys:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input manifest has unexpected fields."
        )
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise VerificationInputSnapshotIntegrityError(
            "Verification input snapshot does not match its deterministic manifest."
        )
    checkpoint_ref = manifest.get("synthesis_checkpoint_ref")
    if not isinstance(checkpoint_ref, dict):
        raise VerificationInputSnapshotIntegrityError(
            "Verification input manifest Synthesis checkpoint ref is invalid."
        )
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "synthesis_checkpoint_ref": checkpoint_ref,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input snapshot does not match its deterministic manifest."
        )
    expected_id = "VIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input snapshot identity does not match its immutable hash."
        )


def assert_verification_input_snapshot_matches(
    snapshot: VerificationInputSnapshot,
    *,
    synthesis_checkpoint: ScanCheckpoint,
) -> None:
    """Reject a manifest that no longer binds the exact verified Synthesis checkpoint."""

    try:
        expected = build_verification_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            synthesis_checkpoint=synthesis_checkpoint,
        )
    except (TypeError, ValueError) as error:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input manifest does not match the verified durable inputs."
        )


def _synthesis_checkpoint_ref(checkpoint: ScanCheckpoint, *, run_id: str) -> dict[str, object]:
    if checkpoint.run_id != run_id:
        raise ValueError("Synthesis checkpoint does not match the Verification run.")
    if checkpoint.stage_name != SYNTHESIS_STAGE:
        raise ValueError("Verification input requires a passed Synthesis checkpoint.")
    if checkpoint.attempt < 1:
        raise ValueError("Synthesis checkpoint attempt must be positive.")
    if checkpoint.contract_version != SYNTHESIS_CHECKPOINT_CONTRACT:
        raise ValueError("Synthesis checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("Verification input requires a passed Synthesis checkpoint.")
    _require_sha256(checkpoint.input_hash, "Synthesis checkpoint input")
    _require_sha256(checkpoint.output_hash, "Synthesis checkpoint output")
    if not checkpoint.checkpoint_id:
        raise ValueError("Synthesis checkpoint identity is required.")
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "stage": SYNTHESIS_STAGE,
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
