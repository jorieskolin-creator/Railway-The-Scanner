"""Prepare-only Diagnosis stage adapter.

This adapter hydrates a verified Diagnosis input snapshot, wraps the compact
Jury result into a Diagnosis proposal, and returns a checkpoint. It never
claims a lease, writes a state transition, calls a model, or registers with
FastAPI.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from model_policy import MODEL_POLICY_VERSION
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
        model_policy_version: str = MODEL_POLICY_VERSION,
    ) -> None:
        normalized_policy = (
            model_policy_version.strip() if isinstance(model_policy_version, str) else ""
        )
        if not normalized_policy:
            raise ValueError("model_policy_version is required.")
        self._input_loader = input_loader
        self._analyze_diagnosis = analyze_diagnosis or analyze_diagnosis_from_jury
        self._model_policy_version = normalized_policy

    @property
    def model_policy_version(self) -> str:
        return self._model_policy_version

    @classmethod
    def with_cognitive_parity(
        cls,
        input_loader: VerifiedDiagnosisInputLoader,
        *,
        company_name: str | None = None,
        intent_result: Mapping[str, Any] | None = None,
        financial_index: int | None = None,
        knowledge_context: str | None = None,
        anthropic_caller: Callable[..., Any] | None = None,
        openai_caller: Callable[..., Any] | None = None,
        model_policy: Mapping[str, Any] | None = None,
        model_policy_version: str = MODEL_POLICY_VERSION,
    ) -> "DiagnosisStageExecutor":
        """Build a prepare-only Diagnosis executor bound to the no-web AI Diagnosis pipeline."""

        from scan_diagnosis_cognition import build_no_web_diagnosis_analyzer

        context_loader = (
            input_loader
            if hasattr(input_loader, "load_run") or hasattr(input_loader, "load_checkpoint")
            else None
        )
        return cls(
            input_loader,
            analyze_diagnosis=build_no_web_diagnosis_analyzer(
                company_name=company_name,
                intent_result=intent_result,
                financial_index=financial_index,
                knowledge_context=knowledge_context,
                context_loader=context_loader,  # type: ignore[arg-type]
                anthropic_caller=anthropic_caller,
                openai_caller=openai_caller,
                model_policy=model_policy,
            ),
            model_policy_version=model_policy_version,
        )

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
            model_policy_version=self._model_policy_version,
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=FORESIGHT_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
