"""Prepare-only material-ingestion stage adapter.

This adapter reads hash-verified durable material and constructs the immutable
checkpoint required for the first stage.  It never invokes Intent, starts a
worker, claims a lease, or writes a state transition; the future Supervisor
must fence and commit the returned checkpoint separately.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from scan_checkpoints import MATERIAL_INGESTION_STAGE, build_material_ingestion_checkpoint
from scan_supervisor import ScanMaterial, StageExecutionResult, StepLease


INTENT_STAGE = "intent"


class MaterialLoader(Protocol):
    def load_materials(self, *, run_id: str) -> list[ScanMaterial]: ...


class MaterialIngestionStageExecutor:
    """Prepare the material-ingestion result without performing its commit."""

    def __init__(self, material_loader: MaterialLoader) -> None:
        self._material_loader = material_loader

    def execute(self, lease: StepLease) -> StageExecutionResult:
        if lease.stage_name != MATERIAL_INGESTION_STAGE:
            raise ValueError(
                f"Material ingestion adapter cannot execute stage {lease.stage_name!r}."
            )
        materials = self._material_loader.load_materials(run_id=lease.run_id)
        checkpoint = build_material_ingestion_checkpoint(
            run_id=lease.run_id,
            attempt=lease.attempt,
            materials=materials,
        )
        return StageExecutionResult(
            output_hash=checkpoint.output_hash,
            checkpoint_ref=checkpoint.checkpoint_ref,
            gate_result=checkpoint.gate_result,
            next_stage=INTENT_STAGE if checkpoint.gate_result["status"] == "passed" else None,
            checkpoint=checkpoint,
        )
