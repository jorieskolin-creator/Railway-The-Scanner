"""Phase 3 contracts for immutable material-ingestion checkpoints."""

from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402


def _materials():
    return prepare_scan_materials(
        run_id="RUN-001",
        report_text="Submitted company context.",
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
    ).materials


def test_checkpoint_is_deterministic_across_material_order_and_has_no_extracted_text():
    materials = _materials()

    first = build_material_ingestion_checkpoint(
        run_id="RUN-001",
        attempt=1,
        materials=materials,
    )
    second = build_material_ingestion_checkpoint(
        run_id="RUN-001",
        attempt=1,
        materials=tuple(reversed(materials)),
    )

    assert first == second
    assert first.gate_result["status"] == "passed"
    assert first.checkpoint_ref == f"checkpoint://scan/{first.checkpoint_id}"
    assert len(first.input_hash) == 64
    assert len(first.output_hash) == 64
    assert all(
        "extracted_text" not in reference
        for reference in first.snapshot["material_refs"]
    )
    assert {reference["material_id"] for reference in first.snapshot["material_refs"]} == {
        material.material_id for material in materials
    }


def test_failed_extraction_creates_a_blocked_checkpoint_gate():
    materials = prepare_scan_materials(
        run_id="RUN-001",
        report_text="",
        uploads=[{"filename": "broken.pdf", "content": b"not a PDF"}],
    ).materials

    checkpoint = build_material_ingestion_checkpoint(
        run_id="RUN-001",
        attempt=1,
        materials=materials,
    )

    assert checkpoint.gate_result["status"] == "blocked"
    assert checkpoint.gate_result["reasons"] == [
        {"code": "material_extraction_failed", "material_ids": [materials[0].material_id]}
    ]
