"""Prepare-only Foresight stage adapter.

This adapter hydrates a verified Foresight input snapshot, wraps the compact
Diagnosis result into a Foresight proposal, and returns a checkpoint. It never
claims a lease, writes a state transition, calls a model, or registers with
FastAPI.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping, Protocol, Sequence

from model_policy import MODEL_POLICY_VERSION
from scan_foresight_checkpoint import (
    DEFAULT_FORESIGHT_KB_VERSION,
    DEFAULT_FORESIGHT_REGISTRY_HASH,
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
ForesightCognitiveRunner = Callable[
    [ForesightInputProjection], tuple[Mapping[str, object], str, str]
]


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
        model_policy_version: str = MODEL_POLICY_VERSION,
        kb_version: str = DEFAULT_FORESIGHT_KB_VERSION,
        kb_registry_hash: str = DEFAULT_FORESIGHT_REGISTRY_HASH,
        _cognitive_runner: ForesightCognitiveRunner | None = None,
    ) -> None:
        normalized_policy = (
            model_policy_version.strip() if isinstance(model_policy_version, str) else ""
        )
        if not normalized_policy:
            raise ValueError("model_policy_version is required.")
        normalized_kb_version = (
            kb_version.strip() if isinstance(kb_version, str) else ""
        )
        if not normalized_kb_version:
            raise ValueError("kb_version is required.")
        self._input_loader = input_loader
        self._analyze_foresight = analyze_foresight or analyze_foresight_from_diagnosis
        self._model_policy_version = normalized_policy
        self._kb_version = normalized_kb_version
        self._kb_registry_hash = str(kb_registry_hash)
        self._cognitive_runner = _cognitive_runner

    @property
    def model_policy_version(self) -> str:
        return self._model_policy_version

    @classmethod
    def with_cognitive_parity(
        cls,
        input_loader: VerifiedForesightInputLoader,
        *,
        company_name: str | None = None,
        domain: str | None = None,
        financial_index: int | None = None,
        top_findings: Sequence[Mapping[str, Any]] | None = None,
        kb_signals: Sequence[Mapping[str, Any]] | None = None,
        kb_signals_by_theme: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        kb_loader: Callable[..., Any] | None = None,
        registry_override: Mapping[str, Any] | None = None,
        kb_version_override: str | None = None,
        openai_caller: Callable[..., Any] | None = None,
        anthropic_caller: Callable[..., Any] | None = None,
        model_policy_version: str = MODEL_POLICY_VERSION,
        report_date: date | None = None,
    ) -> "ForesightStageExecutor":
        """Build a prepare-only Foresight executor bound to no-web KB-first cognition."""

        from scan_foresight_cognition import build_no_web_foresight_runner

        context_loader = (
            input_loader
            if hasattr(input_loader, "load_run") or hasattr(input_loader, "load_checkpoint")
            else None
        )
        runner = build_no_web_foresight_runner(
            company_name=company_name,
            domain=domain,
            financial_index=financial_index,
            top_findings=top_findings,
            kb_signals=kb_signals,
            kb_signals_by_theme=kb_signals_by_theme,
            kb_loader=kb_loader,
            registry_override=registry_override,
            kb_version_override=kb_version_override,
            context_loader=context_loader,  # type: ignore[arg-type]
            openai_caller=openai_caller,
            anthropic_caller=anthropic_caller,
            report_date=report_date,
        )
        return cls(
            input_loader,
            model_policy_version=model_policy_version,
            _cognitive_runner=runner,
        )

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
        if self._cognitive_runner is not None:
            foresight_result, kb_version, kb_registry_hash = self._cognitive_runner(
                projection
            )
        else:
            foresight_result = self._analyze_foresight(projection)
            kb_version = self._kb_version
            kb_registry_hash = self._kb_registry_hash
        checkpoint = build_foresight_checkpoint(
            projection,
            foresight_result=foresight_result,
            model_policy_version=self._model_policy_version,
            kb_version=kb_version,
            kb_registry_hash=kb_registry_hash,
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=SYNTHESIS_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
