"""Fenced commit for a prepared Intent checkpoint.

This helper persists a prepare-only Intent proposal with the existing
checkpoint transaction and queues `jury_analysis`. It does not register a
Supervisor loop, claim work on its own, or change POST /scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT
from scan_intent_snapshot import INTENT_STAGE
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease


JURY_ANALYSIS_STAGE = "jury_analysis"


class IntentCommitRepository(Protocol):
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


def commit_intent_stage(
    repository: IntentCommitRepository,
    lease: StepLease,
    result: StageExecutionResult,
    *,
    now: datetime | None = None,
) -> ScanCheckpoint:
    """Commit a passed Intent proposal, or accept an identical replay."""

    if lease.stage_name != INTENT_STAGE:
        raise ValueError(f"Intent commit cannot complete stage {lease.stage_name!r}.")
    checkpoint = result.checkpoint
    if checkpoint is None:
        raise ValueError("Intent commit requires a checkpoint proposal.")
    if (
        checkpoint.run_id != lease.run_id
        or checkpoint.stage_name != INTENT_STAGE
        or checkpoint.attempt != lease.attempt
    ):
        raise ValueError("Intent checkpoint identity does not match the leased step.")
    if checkpoint.contract_version != INTENT_CHECKPOINT_CONTRACT:
        raise ValueError("Intent checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("only a passed Intent gate may be committed.")

    existing = repository.load_checkpoint(
        run_id=lease.run_id,
        stage_name=INTENT_STAGE,
        attempt=lease.attempt,
    )
    if existing is not None:
        if (
            existing.checkpoint_id != checkpoint.checkpoint_id
            or existing.input_hash != checkpoint.input_hash
            or existing.output_hash != checkpoint.output_hash
        ):
            raise ValueError("a different Intent checkpoint already exists for this attempt.")
        return existing

    repository.complete_step_with_checkpoint(
        lease,
        checkpoint=checkpoint,
        next_stage=JURY_ANALYSIS_STAGE,
        now=now,
    )
    return checkpoint
