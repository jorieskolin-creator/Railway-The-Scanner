"""Prepare-only Foresight stage adapter.

This adapter hydrates a verified Foresight input snapshot, wraps the compact
Diagnosis result into a Foresight proposal, and returns a checkpoint. It never
claims a lease, writes a state transition, calls a model, or registers with
FastAPI.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from scan_foresight_checkpoint import (
    ForesightInputProjection,
    build_foresight_checkpoint,
    build_foresight_input_projection,
    empty_foresight_result,
)
from scan_foresight_snapshot import FORESIGHT_STAGE
from scan_supervisor import (
    ForesightInputSnapshot,
    ScanCheckpoint,
    StageExecutionResult,
    StepLease,
)


SYNTHESIS_STAGE = "strategic_synthesis"

ForesightAnalyzer = Callable[[ForesightInputProjection], Mapping[str, object]]


class VerifiedForesightInputLoader(Protocol):
    def load_verified_foresight_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[ForesightInputSnapshot, ScanCheckpoint]: ...


def analyze_foresight_from_diagnosis(projection: ForesightInputProjection) -> dict[str, object]:
    """Wrap verified Diagnosis output into a Foresight result without calling a model.

    The default body is an empty valid payload so hermetic tests can exercise
    the wrap. A later slice can inject the live Foresight call without changing
    this adapter. Reading `diagnosis_result` is required so omitted Diagnosis
    output cannot silently become a Foresight proposal.
    """

    if not isinstance(projection.diagnosis_result, Mapping) or not projection.diagnosis_result:
        raise ValueError("Foresight adapter requires a compact Diagnosis result.")
    return empty_foresight_result()


class ForesightStageExecutor:
    """Prepare the Foresight result without performing its commit."""

    def __init__(
        self,
        input_loader: VerifiedForesightInputLoader,
        *,
        analyze_foresight: ForesightAnalyzer | None = None,
    ) -> None:
        self._input_loader = input_loader
        self._analyze_foresight = analyze_foresight or analyze_foresight_from_diagnosis

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != FORESIGHT_STAGE:
            raise ValueError(f"Foresight adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, diagnosis_checkpoint = self._input_loader.load_verified_foresight_inputs(
            run_id=lease.run_id,
            attempt=lease.attempt,
        )
        projection = build_foresight_input_projection(
            snapshot,
            diagnosis_checkpoint=diagnosis_checkpoint,
        )
        checkpoint = build_foresight_checkpoint(
            projection,
            foresight_result=self._analyze_foresight(projection),
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=SYNTHESIS_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
