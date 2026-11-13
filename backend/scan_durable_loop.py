"""Test-only driver for the completed durable stage chain.

This module proves that existing loaders, prepare-only adapters, and fenced
commit helpers can move one run from material ingestion through Verification.
It does not register with FastAPI, start the production Supervisor, call a
model, write Blob objects, or publish an artifact. Empty adapter payloads are
orchestration fixtures, not cognitive proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from scan_checkpoint_integrity import validate_checkpoint_integrity
from scan_checkpoints import MATERIAL_INGESTION_STAGE
from scan_diagnosis_adapter import DiagnosisStageExecutor
from scan_diagnosis_commit import commit_diagnosis_stage
from scan_diagnosis_snapshot import DIAGNOSIS_STAGE, build_diagnosis_input_snapshot
from scan_foresight_adapter import ForesightStageExecutor
from scan_foresight_commit import commit_foresight_stage
from scan_foresight_snapshot import FORESIGHT_STAGE, build_foresight_input_snapshot
from scan_intent_adapter import IntentStageExecutor
from scan_intent_commit import JURY_ANALYSIS_STAGE, commit_intent_stage
from scan_intent_snapshot import INTENT_STAGE, build_intent_input_snapshot
from scan_jury_adapter import JuryStageExecutor
from scan_jury_commit import commit_jury_stage
from scan_jury_snapshot import build_jury_input_snapshot
from scan_stage_executor import MaterialIngestionStageExecutor
from scan_supervisor import (
    CheckpointIntegrityError,
    ScanCheckpoint,
    StageExecutionResult,
    StepLease,
)
from scan_synthesis_adapter import SynthesisStageExecutor
from scan_synthesis_commit import commit_synthesis_stage
from scan_synthesis_snapshot import SYNTHESIS_STAGE, build_synthesis_input_snapshot
from scan_verification_adapter import VerificationStageExecutor
from scan_verification_commit import commit_verification_stage
from scan_verification_snapshot import VERIFICATION_STAGE, build_verification_input_snapshot


DURABLE_STAGE_ORDER = (
    MATERIAL_INGESTION_STAGE,
    INTENT_STAGE,
    JURY_ANALYSIS_STAGE,
    DIAGNOSIS_STAGE,
    FORESIGHT_STAGE,
    SYNTHESIS_STAGE,
    VERIFICATION_STAGE,
)


@dataclass(frozen=True)
class DurableChainResult:
    """Orchestration outcome. Checkpoint hashes are not cognitive verdicts."""

    run_id: str
    stages: tuple[str, ...]
    reclaimed_stage: str | None
    reclaimed_owner: str | None


@dataclass(frozen=True)
class DurableResumeResult:
    """Test-only resume outcome across one or more durable stage boundaries."""

    run_id: str
    resumed_from_stage: str
    verified_upstream_stages: tuple[str, ...]
    completed_stages: tuple[str, ...]
    checkpoints: Mapping[str, ScanCheckpoint]


_UPSTREAM_CHECKPOINT_REF_FIELDS: Mapping[str, tuple[str, str]] = {
    JURY_ANALYSIS_STAGE: ("intent_checkpoint_ref", INTENT_STAGE),
    DIAGNOSIS_STAGE: ("jury_checkpoint_ref", JURY_ANALYSIS_STAGE),
    FORESIGHT_STAGE: ("diagnosis_checkpoint_ref", DIAGNOSIS_STAGE),
    SYNTHESIS_STAGE: ("foresight_checkpoint_ref", FORESIGHT_STAGE),
    VERIFICATION_STAGE: ("synthesis_checkpoint_ref", SYNTHESIS_STAGE),
}


def drive_durable_chain(
    repository,
    *,
    lease_owner: str,
    now: datetime,
    lease_seconds: int = 30,
    reclaim_stage: str | None = None,
    replacement_owner: str | None = None,
) -> DurableChainResult:
    """Claim, prepare, and fence-commit each existing stage once.

    `reclaim_stage` expires the first lease for that stage and resumes with
    `replacement_owner` before execute/commit. It is one reclaim, not the
    full restart matrix.
    """

    if not lease_owner:
        raise ValueError("lease_owner is required.")
    if reclaim_stage is not None and reclaim_stage not in DURABLE_STAGE_ORDER:
        raise ValueError(f"reclaim stage {reclaim_stage!r} is not in the durable chain.")
    if reclaim_stage is not None and not replacement_owner:
        raise ValueError("replacement_owner is required when reclaim_stage is set.")

    completed: list[str] = []
    reclaimed_owner: str | None = None
    current_time = now
    run_id: str | None = None
    for expected_stage in DURABLE_STAGE_ORDER:
        lease = repository.claim_next_step(
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            now=current_time,
        )
        if lease is None or lease.stage_name != expected_stage:
            raise ValueError(f"durable chain expected stage {expected_stage!r}.")
        run_id = lease.run_id
        if reclaim_stage == expected_stage:
            current_time = current_time + timedelta(seconds=lease_seconds + 1)
            lease = repository.claim_next_step(
                lease_owner=replacement_owner,
                lease_seconds=lease_seconds,
                now=current_time,
            )
            if lease is None or lease.stage_name != expected_stage:
                raise ValueError(f"reclaim did not resume stage {expected_stage!r}.")
            reclaimed_owner = replacement_owner
        _bind_stage_inputs(repository, lease)
        result = _execute(repository, lease)
        if result.next_stage is not None and expected_stage == VERIFICATION_STAGE:
            raise ValueError("Verification cannot name a successor stage.")
        if result.gate_result.get("status") != "passed":
            raise ValueError(f"stage {expected_stage!r} did not pass its gate.")
        _commit(repository, lease, result, now=current_time)
        completed.append(expected_stage)
    if tuple(completed) != DURABLE_STAGE_ORDER or run_id is None:
        raise ValueError("durable chain did not complete every stage.")
    return DurableChainResult(
        run_id=run_id,
        stages=tuple(completed),
        reclaimed_stage=reclaim_stage,
        reclaimed_owner=reclaimed_owner,
    )


def verify_completed_stage_prefix(
    repository,
    *,
    run_id: str,
    completed_stages: Sequence[str],
) -> dict[str, ScanCheckpoint]:
    """Reload and hash-verify an exact completed prefix of `DURABLE_STAGE_ORDER`."""

    if not run_id:
        raise ValueError("run_id is required.")
    prefix = tuple(completed_stages)
    if prefix != DURABLE_STAGE_ORDER[: len(prefix)]:
        raise ValueError(
            "completed_stages must be an exact prefix of DURABLE_STAGE_ORDER."
        )
    if hasattr(repository, "load_run"):
        run = repository.load_run(run_id=run_id)
        if run is None:
            raise ValueError(f"unknown durable scan run {run_id!r}.")

    verified: dict[str, ScanCheckpoint] = {}
    for stage_name in prefix:
        checkpoint = repository.load_checkpoint(
            run_id=run_id,
            stage_name=stage_name,
            attempt=1,
        )
        if checkpoint is None:
            raise CheckpointIntegrityError(
                f"Missing passed checkpoint for completed stage {stage_name!r}."
            )
        validate_checkpoint_integrity(checkpoint, require_passed=True)
        upstream_binding = _UPSTREAM_CHECKPOINT_REF_FIELDS.get(stage_name)
        if upstream_binding is not None:
            ref_key, upstream_stage = upstream_binding
            upstream = verified[upstream_stage]
            embedded_ref = checkpoint.snapshot.get(ref_key)
            expected_ref = {
                "checkpoint_id": upstream.checkpoint_id,
                "checkpoint_ref": upstream.checkpoint_ref,
                "stage": upstream_stage,
                "attempt": upstream.attempt,
                "contract_version": upstream.contract_version,
                "input_hash": upstream.input_hash,
                "output_hash": upstream.output_hash,
            }
            if embedded_ref != expected_ref:
                raise CheckpointIntegrityError(
                    f"Checkpoint {stage_name!r} {ref_key} does not match reloaded {upstream_stage!r} checkpoint."
                )
        verified[stage_name] = checkpoint

    for pending_stage in DURABLE_STAGE_ORDER[len(prefix) :]:
        unexpected = repository.load_checkpoint(
            run_id=run_id,
            stage_name=pending_stage,
            attempt=1,
        )
        if unexpected is not None:
            raise CheckpointIntegrityError(
                f"Unexpected downstream checkpoint already exists for uncompleted stage {pending_stage!r}."
            )
    return verified


def prepare_leased_stage(repository, lease: StepLease) -> StageExecutionResult:
    """Bind durable inputs and run the prepare-only executor without committing."""

    if lease.stage_name not in DURABLE_STAGE_ORDER:
        raise ValueError(f"durable chain has no stage {lease.stage_name!r}.")
    _bind_stage_inputs(repository, lease)
    result = _execute(repository, lease)
    if lease.stage_name == VERIFICATION_STAGE and result.next_stage is not None:
        raise ValueError("Verification cannot name a successor stage.")
    if result.gate_result.get("status") != "passed":
        raise ValueError(f"stage {lease.stage_name!r} did not pass its gate.")
    return result


def commit_prepared_stage(
    repository,
    lease: StepLease,
    result: StageExecutionResult,
    *,
    now: datetime,
) -> ScanCheckpoint:
    """Commit a prepared stage result under the caller's lease."""

    if lease.stage_name not in DURABLE_STAGE_ORDER:
        raise ValueError(f"durable chain has no stage {lease.stage_name!r}.")
    if lease.stage_name == VERIFICATION_STAGE and result.next_stage is not None:
        raise ValueError("Verification cannot name a successor stage.")
    if result.gate_result.get("status") != "passed":
        raise ValueError(f"stage {lease.stage_name!r} did not pass its gate.")
    return _commit(repository, lease, result, now=now)


def resume_durable_chain(
    repository,
    *,
    lease_owner: str,
    now: datetime,
    lease_seconds: int = 30,
    stop_after_stage: str | None = None,
    on_stage_leased: Callable[[Any, StepLease], None] | None = None,
) -> DurableResumeResult:
    """Resume a durable run from its next claimable step after verifying upstream checkpoints."""

    if not lease_owner:
        raise ValueError("lease_owner is required.")
    if stop_after_stage is not None and stop_after_stage not in DURABLE_STAGE_ORDER:
        raise ValueError(
            f"stop_after_stage {stop_after_stage!r} is not in the durable chain."
        )

    current_time = now
    first_lease = repository.claim_next_step(
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        now=current_time,
    )
    if first_lease is None:
        raise ValueError("no claimable durable step available to resume.")
    if first_lease.stage_name not in DURABLE_STAGE_ORDER:
        raise ValueError(
            f"claimed stage {first_lease.stage_name!r} is not in the durable chain."
        )

    start_index = DURABLE_STAGE_ORDER.index(first_lease.stage_name)
    if (
        stop_after_stage is not None
        and DURABLE_STAGE_ORDER.index(stop_after_stage) < start_index
    ):
        raise ValueError(
            f"stop_after_stage {stop_after_stage!r} precedes resumed stage {first_lease.stage_name!r}."
        )

    upstream_stages = DURABLE_STAGE_ORDER[:start_index]
    verify_completed_stage_prefix(
        repository,
        run_id=first_lease.run_id,
        completed_stages=upstream_stages,
    )

    completed_now: list[str] = []
    committed_checkpoints: dict[str, ScanCheckpoint] = {}
    for offset, expected_stage in enumerate(DURABLE_STAGE_ORDER[start_index:]):
        if offset == 0:
            lease: StepLease | None = first_lease
        else:
            lease = repository.claim_next_step(
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
                now=current_time,
            )
        if (
            lease is None
            or lease.run_id != first_lease.run_id
            or lease.stage_name != expected_stage
        ):
            raise ValueError(
                f"durable resume expected stage {expected_stage!r} for run {first_lease.run_id!r}."
            )
        if on_stage_leased is not None:
            on_stage_leased(repository, lease)
        result = prepare_leased_stage(repository, lease)
        checkpoint = commit_prepared_stage(
            repository,
            lease,
            result,
            now=current_time,
        )
        completed_now.append(expected_stage)
        committed_checkpoints[expected_stage] = checkpoint
        if expected_stage == stop_after_stage or expected_stage == VERIFICATION_STAGE:
            break
        current_time = current_time + timedelta(seconds=1)

    return DurableResumeResult(
        run_id=first_lease.run_id,
        resumed_from_stage=first_lease.stage_name,
        verified_upstream_stages=upstream_stages,
        completed_stages=tuple(completed_now),
        checkpoints=committed_checkpoints,
    )


def _bind_stage_inputs(repository, lease: StepLease) -> None:
    if lease.stage_name == MATERIAL_INGESTION_STAGE:
        return
    if lease.stage_name == INTENT_STAGE:
        materials = repository.load_materials(run_id=lease.run_id)
        retrievals = repository.load_source_retrievals(
            run_id=lease.run_id,
            stage_name=INTENT_STAGE,
            attempt=lease.attempt,
        )
        repository.persist_intent_input_snapshot(
            snapshot=build_intent_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                materials=materials,
                retrievals=retrievals,
            )
        )
        return
    if lease.stage_name == JURY_ANALYSIS_STAGE:
        intent_checkpoint = _require_checkpoint(repository, lease.run_id, INTENT_STAGE)
        candidates = repository.load_jury_source_candidates(
            run_id=lease.run_id,
            stage_name=JURY_ANALYSIS_STAGE,
            attempt=lease.attempt,
        )
        retrievals = repository.load_source_retrievals(
            run_id=lease.run_id,
            stage_name=JURY_ANALYSIS_STAGE,
            attempt=lease.attempt,
        )
        repository.persist_jury_input_snapshot(
            snapshot=build_jury_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                intent_checkpoint=intent_checkpoint,
                candidates=candidates,
                retrievals=retrievals,
            )
        )
        return
    if lease.stage_name == DIAGNOSIS_STAGE:
        repository.persist_diagnosis_input_snapshot(
            snapshot=build_diagnosis_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                jury_checkpoint=_require_checkpoint(repository, lease.run_id, JURY_ANALYSIS_STAGE),
            )
        )
        return
    if lease.stage_name == FORESIGHT_STAGE:
        repository.persist_foresight_input_snapshot(
            snapshot=build_foresight_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                diagnosis_checkpoint=_require_checkpoint(repository, lease.run_id, DIAGNOSIS_STAGE),
            )
        )
        return
    if lease.stage_name == SYNTHESIS_STAGE:
        repository.persist_synthesis_input_snapshot(
            snapshot=build_synthesis_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                foresight_checkpoint=_require_checkpoint(repository, lease.run_id, FORESIGHT_STAGE),
            )
        )
        return
    if lease.stage_name == VERIFICATION_STAGE:
        repository.persist_verification_input_snapshot(
            snapshot=build_verification_input_snapshot(
                run_id=lease.run_id,
                attempt=lease.attempt,
                synthesis_checkpoint=_require_checkpoint(repository, lease.run_id, SYNTHESIS_STAGE),
            )
        )
        return
    raise ValueError(f"durable chain cannot bind stage {lease.stage_name!r}.")


def _execute(repository, lease: StepLease) -> StageExecutionResult:
    executors = {
        MATERIAL_INGESTION_STAGE: MaterialIngestionStageExecutor(repository),
        INTENT_STAGE: IntentStageExecutor(repository),
        JURY_ANALYSIS_STAGE: JuryStageExecutor(repository),
        DIAGNOSIS_STAGE: DiagnosisStageExecutor(repository),
        FORESIGHT_STAGE: ForesightStageExecutor(repository),
        SYNTHESIS_STAGE: SynthesisStageExecutor(repository),
        VERIFICATION_STAGE: VerificationStageExecutor(repository),
    }
    executor = executors.get(lease.stage_name)
    if executor is None:
        raise ValueError(f"durable chain has no adapter for stage {lease.stage_name!r}.")
    return executor.execute(lease)


def _commit(repository, lease: StepLease, result: StageExecutionResult, *, now: datetime) -> ScanCheckpoint:
    if lease.stage_name == MATERIAL_INGESTION_STAGE:
        checkpoint = result.checkpoint
        if checkpoint is None or result.next_stage != INTENT_STAGE:
            raise ValueError("material ingestion must propose the intent stage.")
        repository.complete_step_with_checkpoint(
            lease,
            checkpoint=checkpoint,
            next_stage=INTENT_STAGE,
            now=now,
        )
        return checkpoint
    commits = {
        INTENT_STAGE: commit_intent_stage,
        JURY_ANALYSIS_STAGE: commit_jury_stage,
        DIAGNOSIS_STAGE: commit_diagnosis_stage,
        FORESIGHT_STAGE: commit_foresight_stage,
        SYNTHESIS_STAGE: commit_synthesis_stage,
        VERIFICATION_STAGE: commit_verification_stage,
    }
    commit = commits.get(lease.stage_name)
    if commit is None:
        raise ValueError(f"durable chain has no commit helper for stage {lease.stage_name!r}.")
    return commit(repository, lease, result, now=now)


def _require_checkpoint(repository, run_id: str, stage_name: str) -> ScanCheckpoint:
    checkpoint = repository.load_checkpoint(run_id=run_id, stage_name=stage_name, attempt=1)
    if checkpoint is None:
        raise ValueError(f"missing passed checkpoint for stage {stage_name!r}.")
    return checkpoint
