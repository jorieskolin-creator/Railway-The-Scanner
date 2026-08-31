"""Fenced commit for a prepared Diagnosis checkpoint.

This helper persists a prepare-only Diagnosis proposal with the existing
checkpoint transaction and queues `foresight`. It does not register a
Supervisor loop, claim work on its own, or change POST /scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT
from scan_diagnosis_snapshot import DIAGNOSIS_STAGE
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease


FORESIGHT_STAGE = "foresight"


class DiagnosisCommitRepository(Protocol):
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


def commit_diagnosis_stage(
    repository: DiagnosisCommitRepository,
    lease: StepLease,
    result: StageExecutionResult,
    *,
    now: datetime | None = None,
) -> ScanCheckpoint:
    """Commit a passed Diagnosis proposal, or accept an identical replay."""

    if lease.stage_name != DIAGNOSIS_STAGE:
        raise ValueError(f"Diagnosis commit cannot complete stage {lease.stage_name!r}.")
    checkpoint = result.checkpoint
    if checkpoint is None:
        raise ValueError("Diagnosis commit requires a checkpoint proposal.")
    if (
        checkpoint.run_id != lease.run_id
        or checkpoint.stage_name != DIAGNOSIS_STAGE
        or checkpoint.attempt != lease.attempt
    ):
        raise ValueError("Diagnosis checkpoint identity does not match the leased step.")
    if checkpoint.contract_version != DIAGNOSIS_CHECKPOINT_CONTRACT:
        raise ValueError("Diagnosis checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("only a passed Diagnosis gate may be committed.")

    existing = repository.load_checkpoint(
        run_id=lease.run_id,
        stage_name=DIAGNOSIS_STAGE,
        attempt=lease.attempt,
    )
    if existing is not None:
        if (
            existing.checkpoint_id != checkpoint.checkpoint_id
            or existing.input_hash != checkpoint.input_hash
            or existing.output_hash != checkpoint.output_hash
        ):
            raise ValueError("a different Diagnosis checkpoint already exists for this attempt.")
        return existing

    repository.complete_step_with_checkpoint(
        lease,
        checkpoint=checkpoint,
        next_stage=FORESIGHT_STAGE,
        now=now,
    )
    return checkpoint
