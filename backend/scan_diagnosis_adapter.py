"""Prepare-only Diagnosis stage adapter.

This adapter hydrates a verified Diagnosis input snapshot, wraps the compact
Jury result into a Diagnosis proposal, and returns a checkpoint. It never
claims a lease, writes a state transition, calls a model, or registers with
FastAPI.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from scan_diagnosis_checkpoint import (
    DiagnosisInputProjection,
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
    empty_diagnosis_result,
)
from scan_diagnosis_snapshot import DIAGNOSIS_STAGE
from scan_supervisor import (
    DiagnosisInputSnapshot,
    ScanCheckpoint,
    StageExecutionResult,
    StepLease,
)


FORESIGHT_STAGE = "foresight"

DiagnosisAnalyzer = Callable[[DiagnosisInputProjection], Mapping[str, object]]


class VerifiedDiagnosisInputLoader(Protocol):
    def load_verified_diagnosis_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[DiagnosisInputSnapshot, ScanCheckpoint]: ...


def analyze_diagnosis_from_jury(projection: DiagnosisInputProjection) -> dict[str, object]:
    """Wrap verified Jury output into a Diagnosis result without calling a model.

    The default body is an empty valid payload so hermetic tests can exercise
    the wrap. A later slice can inject the live Diagnosis call without changing
    this adapter. Reading `jury_result` is required so omitted Jury output
    cannot silently become a Diagnosis proposal.
    """

    if not isinstance(projection.jury_result, Mapping) or not projection.jury_result:
        raise ValueError("Diagnosis adapter requires a compact Jury result.")
    return empty_diagnosis_result()


class DiagnosisStageExecutor:
    """Prepare the Diagnosis result without performing its commit."""

    def __init__(
        self,
        input_loader: VerifiedDiagnosisInputLoader,
        *,
        analyze_diagnosis: DiagnosisAnalyzer | None = None,
    ) -> None:
        self._input_loader = input_loader
        self._analyze_diagnosis = analyze_diagnosis or analyze_diagnosis_from_jury

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != DIAGNOSIS_STAGE:
            raise ValueError(f"Diagnosis adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, jury_checkpoint = self._input_loader.load_verified_diagnosis_inputs(
            run_id=lease.run_id,
            attempt=lease.attempt,
        )
        projection = build_diagnosis_input_projection(
            snapshot,
            jury_checkpoint=jury_checkpoint,
        )
        checkpoint = build_diagnosis_checkpoint(
            projection,
            diagnosis_result=self._analyze_diagnosis(projection),
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=FORESIGHT_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
