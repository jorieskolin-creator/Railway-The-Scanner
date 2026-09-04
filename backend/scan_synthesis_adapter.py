"""Prepare-only Strategic Synthesis stage adapter.

This adapter hydrates a verified Synthesis input snapshot, wraps the compact
Foresight result into a Synthesis proposal, and returns a checkpoint. It never
claims a lease, writes a state transition, calls a model, or registers with
FastAPI.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from scan_supervisor import (
    ScanCheckpoint,
    StageExecutionResult,
    StepLease,
    SynthesisInputSnapshot,
)
from scan_synthesis_checkpoint import (
    SynthesisInputProjection,
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
    empty_synthesis_result,
)
from scan_synthesis_snapshot import SYNTHESIS_STAGE


VERIFICATION_STAGE = "verification"

SynthesisAnalyzer = Callable[[SynthesisInputProjection], Mapping[str, object]]


class VerifiedSynthesisInputLoader(Protocol):
    def load_verified_synthesis_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[SynthesisInputSnapshot, ScanCheckpoint]: ...


def analyze_synthesis_from_foresight(projection: SynthesisInputProjection) -> dict[str, object]:
    """Wrap verified Foresight output into a Synthesis result without calling a model.

    The default body is an empty valid payload so hermetic tests can exercise
    the wrap. A later slice can inject the live Synthesis call without changing
    this adapter. Reading `foresight_result` is required so omitted Foresight
    output cannot silently become a Synthesis proposal.
    """

    if not isinstance(projection.foresight_result, Mapping) or not projection.foresight_result:
        raise ValueError("Synthesis adapter requires a compact Foresight result.")
    return empty_synthesis_result()


class SynthesisStageExecutor:
    """Prepare the Synthesis result without performing its commit."""

    def __init__(
        self,
        input_loader: VerifiedSynthesisInputLoader,
        *,
        analyze_synthesis: SynthesisAnalyzer | None = None,
    ) -> None:
        self._input_loader = input_loader
        self._analyze_synthesis = analyze_synthesis or analyze_synthesis_from_foresight

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != SYNTHESIS_STAGE:
            raise ValueError(f"Synthesis adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, foresight_checkpoint = self._input_loader.load_verified_synthesis_inputs(
            run_id=lease.run_id,
            attempt=lease.attempt,
        )
        projection = build_synthesis_input_projection(
            snapshot,
            foresight_checkpoint=foresight_checkpoint,
        )
        checkpoint = build_synthesis_checkpoint(
            projection,
            synthesis_result=self._analyze_synthesis(projection),
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=VERIFICATION_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
