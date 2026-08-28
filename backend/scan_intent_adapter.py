"""Prepare-only Intent stage adapter.

This adapter hydrates a verified Intent input snapshot, calls the existing
Intent scoring function on that concatenated text, and returns a checkpoint
proposal. It never claims a lease, writes a state transition, scrapes content,
or registers with FastAPI.
"""

from __future__ import annotations

from typing import Protocol

from scan_intent_checkpoint import build_intent_checkpoint, build_intent_input_projection
from scan_intent_snapshot import INTENT_STAGE
from scan_supervisor import (
    IntentInputSnapshot,
    ScanMaterial,
    SourceRetrieval,
    StageExecutionResult,
    StepLease,
)
from scanner_one import analyze_intent


JURY_ANALYSIS_STAGE = "jury_analysis"


class VerifiedIntentInputLoader(Protocol):
    def load_verified_intent_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[IntentInputSnapshot, list[ScanMaterial], list[SourceRetrieval]]: ...


class IntentStageExecutor:
    """Prepare the Intent result without performing its commit."""

    def __init__(self, input_loader: VerifiedIntentInputLoader) -> None:
        self._input_loader = input_loader

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != INTENT_STAGE:
            raise ValueError(f"Intent adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, materials, retrievals = self._input_loader.load_verified_intent_inputs(
            run_id=lease.run_id,
            attempt=lease.attempt,
        )
        projection = build_intent_input_projection(
            snapshot,
            materials=materials,
            retrievals=retrievals,
        )
        checkpoint = build_intent_checkpoint(
            projection,
            intent_result=analyze_intent(projection.concatenated_text),
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=JURY_ANALYSIS_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
