"""Prepare-only Jury stage adapter.

This adapter hydrates a verified Jury input snapshot, wraps the existing
packet-priority and payload-merge helpers on those packets, and returns a
checkpoint proposal. It never claims a lease, writes a state transition,
scrapes content, or registers with FastAPI.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence

from model_policy import MODEL_POLICY_VERSION
from scan_jury_candidates import JURY_ANALYSIS_STAGE
from scan_jury_checkpoint import (
    JURY_CHECKPOINT_LANES,
    JuryInputProjection,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_supervisor import (
    JuryInputSnapshot,
    JurySourceCandidate,
    ScanCheckpoint,
    SourceRetrieval,
    StageExecutionResult,
    StepLease,
)
from scanner_two import FORENSIC_JURIES, _merge_jury_payloads, _prioritize_jury_packets


DIAGNOSIS_STAGE = "diagnosis"

JuryLaneAnalyzer = Callable[[str, Sequence[Mapping[str, object]]], Mapping[str, object]]
JuryAnalyzer = Callable[[JuryInputProjection], Mapping[str, object]]


class VerifiedJuryInputLoader(Protocol):
    def load_verified_jury_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[
        JuryInputSnapshot,
        ScanCheckpoint,
        list[JurySourceCandidate],
        list[SourceRetrieval],
    ]: ...


def analyze_jury_lanes(
    projection: JuryInputProjection,
    *,
    analyze_lane: JuryLaneAnalyzer | None = None,
) -> dict[str, object]:
    """Wrap existing Jury packet helpers without acquiring sources or calling models.

    Each lane is ranked with `_prioritize_jury_packets`. Lane payloads are then
    merged with `_merge_jury_payloads`. The default lane body is an empty valid
    payload so hermetic tests can exercise the wrap; a later slice can inject
    the live no-web Jury call without changing this adapter.
    """

    if tuple(FORENSIC_JURIES) != JURY_CHECKPOINT_LANES:
        raise ValueError("Existing Jury lane names do not match the Jury checkpoint contract.")
    lane_fn = analyze_lane or _empty_lane_payload
    juries = []
    for jury_name in FORENSIC_JURIES:
        packets = _prioritize_jury_packets(jury_name, list(projection.packets))
        merged = _merge_jury_payloads(jury_name, [dict(lane_fn(jury_name, packets))])
        if merged is None:
            merged = dict(_empty_lane_payload(jury_name, packets))
        juries.append(merged)
    return {"juries": juries}


def _empty_lane_payload(
    jury_name: str,
    packets: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    del packets
    return {
        "jury": jury_name,
        "findings": [],
        "rejected_evidence": [],
        "contradictions": [],
        "missing_coverage": [],
        "confidence_score": 0,
        "reality_score_adjustment": 0,
    }


class JuryStageExecutor:
    """Prepare the Jury result without performing its commit."""

    def __init__(
        self,
        input_loader: VerifiedJuryInputLoader,
        *,
        analyze_jury: JuryAnalyzer | None = None,
        model_policy_version: str = MODEL_POLICY_VERSION,
    ) -> None:
        normalized_policy = (
            model_policy_version.strip() if isinstance(model_policy_version, str) else ""
        )
        if not normalized_policy:
            raise ValueError("model_policy_version is required.")
        self._input_loader = input_loader
        self._analyze_jury = analyze_jury or analyze_jury_lanes
        self._model_policy_version = normalized_policy

    @property
    def model_policy_version(self) -> str:
        return self._model_policy_version

    @classmethod
    def with_cognitive_parity(
        cls,
        input_loader: VerifiedJuryInputLoader,
        *,
        company_name: str | None = None,
        company_url: str | None = None,
        openai_caller: Callable[..., Any] | None = None,
        anthropic_caller: Callable[..., Any] | None = None,
        model_policy: Mapping[str, Any] | None = None,
        model_policy_version: str = MODEL_POLICY_VERSION,
    ) -> "JuryStageExecutor":
        """Build a prepare-only Jury executor bound to the no-web five-Jury pipeline."""

        from scan_jury_cognition import build_no_web_jury_analyzer

        run_loader = input_loader if hasattr(input_loader, "load_run") else None
        return cls(
            input_loader,
            analyze_jury=build_no_web_jury_analyzer(
                company_name=company_name,
                company_url=company_url,
                run_loader=run_loader,  # type: ignore[arg-type]
                openai_caller=openai_caller,
                anthropic_caller=anthropic_caller,
                model_policy=model_policy,
            ),
            model_policy_version=model_policy_version,
        )

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != JURY_ANALYSIS_STAGE:
            raise ValueError(f"Jury adapter cannot execute stage {lease.stage_name!r}.")
        snapshot, intent_checkpoint, candidates, retrievals = (
            self._input_loader.load_verified_jury_inputs(
                run_id=lease.run_id,
                attempt=lease.attempt,
            )
        )
        projection = build_jury_input_projection(
            snapshot,
            intent_checkpoint=intent_checkpoint,
            candidates=candidates,
            retrievals=retrievals,
        )
        checkpoint = build_jury_checkpoint(
            projection,
            jury_result=self._analyze_jury(projection),
            model_policy_version=self._model_policy_version,
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=DIAGNOSIS_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
