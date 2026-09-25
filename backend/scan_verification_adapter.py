"""Prepare-only Verification stage adapter.

This adapter hydrates a verified Verification input snapshot, wraps the compact
Synthesis result into a Verification proposal, and returns a checkpoint. It
never claims a lease, writes a state transition, calls a model, or registers
with FastAPI. Verification is the last cognitive stage, so a passed proposal
does not name a successor stage.
"""

from __future__ import annotations

from typing import Callable, Mapping, Protocol

from scan_supervisor import (
    ScanCheckpoint,
    StageExecutionResult,
    StepLease,
    VerificationInputSnapshot,
)
from scan_verification_checkpoint import (
    VerificationInputProjection,
    build_verification_checkpoint,
    build_verification_input_projection,
    empty_verification_result,
)
from scan_verification_snapshot import VERIFICATION_STAGE


VerificationAnalyzer = Callable[[VerificationInputProjection], Mapping[str, object]]


class VerifiedVerificationInputLoader(Protocol):
    def load_verified_verification_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[VerificationInputSnapshot, ScanCheckpoint]: ...


def analyze_verification_from_synthesis(
    projection: VerificationInputProjection,
) -> dict[str, object]:
    """Wrap verified Synthesis output into a Verification result without a model.

    The default body is an empty valid payload so hermetic tests can exercise
    the wrap. A later slice can inject the live Verification call without
    changing this adapter. Reading `synthesis_result` is required so omitted
    Synthesis output cannot silently become a Verification proposal.
    """

    if not isinstance(projection.synthesis_result, Mapping) or not projection.synthesis_result:
        raise ValueError("Verification adapter requires a compact Synthesis result.")
    return empty_verification_result()


class VerificationStageExecutor:
    """Prepare the Verification result without performing its commit."""

    def __init__(
        self,
        input_loader: VerifiedVerificationInputLoader,
        *,
        analyze_verification: VerificationAnalyzer | None = None,
    ) -> None:
        self._input_loader = input_loader
        self._analyze_verification = analyze_verification or analyze_verification_from_synthesis

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != VERIFICATION_STAGE:
            raise ValueError(f"Verification adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, synthesis_checkpoint = self._input_loader.load_verified_verification_inputs(
            run_id=lease.run_id,
            attempt=lease.attempt,
        )
        projection = build_verification_input_projection(
            snapshot,
            synthesis_checkpoint=synthesis_checkpoint,
        )
        checkpoint = build_verification_checkpoint(
            projection,
            verification_result=self._analyze_verification(projection),
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=None,
            checkpoint=checkpoint,
        )
