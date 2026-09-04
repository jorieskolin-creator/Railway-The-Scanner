"""Immutable, evidence-neutral input manifests for the future Synthesis adapter.

A Strategic Synthesis input snapshot binds one strategic_synthesis attempt to a
passed Foresight checkpoint. It is operational lineage only: it does not make a
finding true, eligible, or publishable, and it does not invoke Synthesis
generation or copy Foresight Knowledge Base content.
"""

from __future__ import annotations

import hashlib
import json

from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT
from scan_foresight_snapshot import FORESIGHT_STAGE
from scan_supervisor import (
    ScanCheckpoint,
    SynthesisInputSnapshot,
    SynthesisInputSnapshotIntegrityError,
)


SYNTHESIS_INPUT_SNAPSHOT_CONTRACT = "scanner.synthesis-input/v1"
SYNTHESIS_STAGE = "strategic_synthesis"


def build_synthesis_input_snapshot(
    *,
    run_id: str,
    attempt: int,
    foresight_checkpoint: ScanCheckpoint,
) -> SynthesisInputSnapshot:
    """Build a deterministic manifest from a passed Foresight checkpoint."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    checkpoint_ref = _foresight_checkpoint_ref(foresight_checkpoint, run_id=run_id)
    payload = {
        "contract": SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
        "run_id": run_id,
        "stage": SYNTHESIS_STAGE,
        "attempt": attempt,
        "foresight_checkpoint_ref": checkpoint_ref,
    }
    input_hash = _hash(payload)
    manifest = {**payload, "input_hash": input_hash}
    snapshot_id = "SIS-" + _hash(
        {
            "run_id": run_id,
            "stage": SYNTHESIS_STAGE,
            "attempt": attempt,
            "contract": SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
            "input_hash": input_hash,
        }
    )[:32].upper()
    return SynthesisInputSnapshot(
        snapshot_id=snapshot_id,
        run_id=run_id,
        stage_name=SYNTHESIS_STAGE,
        attempt=attempt,
        contract_version=SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
        input_hash=input_hash,
        manifest=manifest,
    )


def validate_synthesis_input_snapshot_manifest(snapshot: SynthesisInputSnapshot) -> None:
    """Validate the deterministic manifest shape and identity without hydrating text."""

    manifest = snapshot.manifest
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "foresight_checkpoint_ref",
        "input_hash",
    }
    if set(manifest) != expected_keys:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input manifest has unexpected fields."
        )
    if (
        manifest.get("contract") != snapshot.contract_version
        or manifest.get("run_id") != snapshot.run_id
        or manifest.get("stage") != snapshot.stage_name
        or manifest.get("attempt") != snapshot.attempt
        or manifest.get("input_hash") != snapshot.input_hash
    ):
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input snapshot does not match its deterministic manifest."
        )
    checkpoint_ref = manifest.get("foresight_checkpoint_ref")
    if not isinstance(checkpoint_ref, dict):
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input manifest Foresight checkpoint ref is invalid."
        )
    expected_hash = _hash(
        {
            "contract": snapshot.contract_version,
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "foresight_checkpoint_ref": checkpoint_ref,
        }
    )
    if expected_hash != snapshot.input_hash:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input snapshot does not match its deterministic manifest."
        )
    expected_id = "SIS-" + _hash(
        {
            "run_id": snapshot.run_id,
            "stage": snapshot.stage_name,
            "attempt": snapshot.attempt,
            "contract": snapshot.contract_version,
            "input_hash": snapshot.input_hash,
        }
    )[:32].upper()
    if snapshot.snapshot_id != expected_id:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input snapshot identity does not match its immutable hash."
        )


def assert_synthesis_input_snapshot_matches(
    snapshot: SynthesisInputSnapshot,
    *,
    foresight_checkpoint: ScanCheckpoint,
) -> None:
    """Reject a manifest that no longer binds the exact verified Foresight checkpoint."""

    try:
        expected = build_synthesis_input_snapshot(
            run_id=snapshot.run_id,
            attempt=snapshot.attempt,
            foresight_checkpoint=foresight_checkpoint,
        )
    except (TypeError, ValueError) as error:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input manifest cannot be reconstructed from durable inputs."
        ) from error
    if expected != snapshot:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input manifest does not match the verified durable inputs."
        )


def _foresight_checkpoint_ref(checkpoint: ScanCheckpoint, *, run_id: str) -> dict[str, object]:
    if checkpoint.run_id != run_id:
        raise ValueError("Foresight checkpoint does not match the Synthesis run.")
    if checkpoint.stage_name != FORESIGHT_STAGE:
        raise ValueError("Synthesis input requires a passed Foresight checkpoint.")
    if checkpoint.attempt < 1:
        raise ValueError("Foresight checkpoint attempt must be positive.")
    if checkpoint.contract_version != FORESIGHT_CHECKPOINT_CONTRACT:
        raise ValueError("Foresight checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("Synthesis input requires a passed Foresight checkpoint.")
    _require_sha256(checkpoint.input_hash, "Foresight checkpoint input")
    _require_sha256(checkpoint.output_hash, "Foresight checkpoint output")
    if not checkpoint.checkpoint_id:
        raise ValueError("Foresight checkpoint identity is required.")
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "stage": FORESIGHT_STAGE,
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
