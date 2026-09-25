"""Fenced commit for a prepared Verification checkpoint.

This helper persists a prepare-only Verification proposal with the existing
checkpoint transaction. Verification is the last cognitive stage, so it queues
no successor. It does not publish an artifact, register a Supervisor loop,
claim work on its own, or change POST /scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease
from scan_verification_checkpoint import VERIFICATION_CHECKPOINT_CONTRACT
from scan_verification_snapshot import VERIFICATION_STAGE


class VerificationCommitRepository(Protocol):
    def complete_step_with_checkpoint(
        self,
        lease: StepLease,
        *,
        checkpoint: ScanCheckpoint,
        next_stage: str | None,
        now: datetime | None = None,
    ) -> None: ...

    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> ScanCheckpoint | None: ...


def commit_verification_stage(
    repository: VerificationCommitRepository,
    lease: StepLease,
    result: StageExecutionResult,
    *,
    now: datetime | None = None,
) -> ScanCheckpoint:
    """Commit a passed Verification proposal, or accept an identical replay."""

    if lease.stage_name != VERIFICATION_STAGE:
        raise ValueError(f"Verification commit cannot complete stage {lease.stage_name!r}.")
    checkpoint = result.checkpoint
    if checkpoint is None:
        raise ValueError("Verification commit requires a checkpoint proposal.")
    if (
        checkpoint.run_id != lease.run_id
        or checkpoint.stage_name != VERIFICATION_STAGE
        or checkpoint.attempt != lease.attempt
    ):
        raise ValueError("Verification checkpoint identity does not match the leased step.")
    if checkpoint.contract_version != VERIFICATION_CHECKPOINT_CONTRACT:
        raise ValueError("Verification checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("only a passed Verification gate may be committed.")
    if result.next_stage is not None:
        raise ValueError("Verification commit cannot queue a successor stage.")

    existing = repository.load_checkpoint(
        run_id=lease.run_id,
        stage_name=VERIFICATION_STAGE,
        attempt=lease.attempt,
    )
    if existing is not None:
        if (
            existing.checkpoint_id != checkpoint.checkpoint_id
            or existing.input_hash != checkpoint.input_hash
            or existing.output_hash != checkpoint.output_hash
        ):
            raise ValueError("a different Verification checkpoint already exists for this attempt.")
        return existing

    repository.complete_step_with_checkpoint(
        lease,
        checkpoint=checkpoint,
        next_stage=None,
        now=now,
    )
    return checkpoint
