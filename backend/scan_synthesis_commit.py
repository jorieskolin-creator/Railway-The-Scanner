"""Fenced commit for a prepared Strategic Synthesis checkpoint.

This helper persists a prepare-only Synthesis proposal with the existing
checkpoint transaction and queues `verification`. It does not register a
Supervisor loop, claim work on its own, wrap Verification, or change POST /scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease
from scan_synthesis_checkpoint import SYNTHESIS_CHECKPOINT_CONTRACT
from scan_synthesis_snapshot import SYNTHESIS_STAGE


VERIFICATION_STAGE = "verification"


class SynthesisCommitRepository(Protocol):
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


def commit_synthesis_stage(
    repository: SynthesisCommitRepository,
    lease: StepLease,
    result: StageExecutionResult,
    *,
    now: datetime | None = None,
) -> ScanCheckpoint:
    """Commit a passed Synthesis proposal, or accept an identical replay."""

    if lease.stage_name != SYNTHESIS_STAGE:
        raise ValueError(f"Synthesis commit cannot complete stage {lease.stage_name!r}.")
    checkpoint = result.checkpoint
    if checkpoint is None:
        raise ValueError("Synthesis commit requires a checkpoint proposal.")
    if (
        checkpoint.run_id != lease.run_id
        or checkpoint.stage_name != SYNTHESIS_STAGE
        or checkpoint.attempt != lease.attempt
    ):
        raise ValueError("Synthesis checkpoint identity does not match the leased step.")
    if checkpoint.contract_version != SYNTHESIS_CHECKPOINT_CONTRACT:
        raise ValueError("Synthesis checkpoint contract is invalid.")
    if checkpoint.gate_result.get("status") != "passed":
        raise ValueError("only a passed Synthesis gate may be committed.")

    existing = repository.load_checkpoint(
        run_id=lease.run_id,
        stage_name=SYNTHESIS_STAGE,
        attempt=lease.attempt,
    )
    if existing is not None:
        if (
            existing.checkpoint_id != checkpoint.checkpoint_id
            or existing.input_hash != checkpoint.input_hash
            or existing.output_hash != checkpoint.output_hash
        ):
            raise ValueError("a different Synthesis checkpoint already exists for this attempt.")
        return existing

    repository.complete_step_with_checkpoint(
        lease,
        checkpoint=checkpoint,
        next_stage=VERIFICATION_STAGE,
        now=now,
    )
    return checkpoint
