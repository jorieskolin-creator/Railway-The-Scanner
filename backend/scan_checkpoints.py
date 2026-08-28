"""Immutable material-ingestion checkpoint construction.

A checkpoint is operational lineage, not evidence.  It contains material
references and verified hashes, never raw files or extracted text.
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from scan_supervisor import ScanCheckpoint, ScanMaterial


MATERIAL_INGESTION_CHECKPOINT_CONTRACT = "scanner.material-ingestion/v1"
MATERIAL_INGESTION_STAGE = "material_ingestion"


def build_material_ingestion_checkpoint(
    *,
    run_id: str,
    attempt: int,
    materials: Sequence[ScanMaterial],
) -> ScanCheckpoint:
    """Build a deterministic, immutable manifest checkpoint from loaded material."""

    if not run_id:
        raise ValueError("run_id is required.")
    if attempt < 1:
        raise ValueError("attempt must be positive.")

    material_refs = [
        {
            "material_id": material.material_id,
            "origin": material.origin,
            "filename": material.filename,
            "media_type": material.media_type,
            "byte_size": material.byte_size,
            "content_sha256": material.content_sha256,
            "extracted_text_sha256": material.extracted_text_sha256,
            "parser_version": material.parser_version,
            "extraction_status": material.extraction_status,
            "allowed_use_classification": material.allowed_use_classification,
        }
        for material in sorted(materials, key=lambda item: item.material_id)
    ]
    failed_material_ids = [
        reference["material_id"]
        for reference in material_refs
        if reference["extraction_status"] == "failed"
    ]
    empty_material_ids = [
        reference["material_id"]
        for reference in material_refs
        if reference["extraction_status"] == "empty"
    ]
    gate_result = {
        "gate": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
        "status": "blocked" if failed_material_ids else "passed",
        "reasons": (
            [{"code": "material_extraction_failed", "material_ids": failed_material_ids}]
            if failed_material_ids
            else []
        ),
        "warnings": (
            [{"code": "no_submitted_material"}]
            if not material_refs
            else (
                [{"code": "material_extraction_empty", "material_ids": empty_material_ids}]
                if empty_material_ids
                else []
            )
        ),
    }
    input_hash = _hash(
        {
            "contract": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
            "run_id": run_id,
            "stage": MATERIAL_INGESTION_STAGE,
            "attempt": attempt,
            "material_refs": material_refs,
        }
    )
    snapshot = {
        "contract": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
        "run_id": run_id,
        "stage": MATERIAL_INGESTION_STAGE,
        "attempt": attempt,
        "input_hash": input_hash,
        "material_refs": material_refs,
        "gate_result": gate_result,
    }
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": run_id,
            "stage": MATERIAL_INGESTION_STAGE,
            "attempt": attempt,
            "contract": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        stage_name=MATERIAL_INGESTION_STAGE,
        attempt=attempt,
        contract_version=MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
        input_hash=input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
