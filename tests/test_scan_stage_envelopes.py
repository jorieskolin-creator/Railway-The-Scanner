"""Hermetic Card 4A envelope and gate contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from scan_stage_envelopes import (
    StageEnvelope, StageEnvelopeValidationError, StageGate, StageRef,
    build_persisted_gate_result, run_gated_stage_step, validate_persisted_gate_result,
    validate_stage_envelope,
)
from scan_supervisor import GateResultIntegrityError, StepLease

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


def _ingress():
    return StageEnvelope(
        run_id="RUN-EXAMPLE", stage="intent", attempt=1, input_hash="a" * 64,
        input_refs=(StageRef("IIS-EXAMPLE", "a" * 64, "input_snapshot"),),
        gate_results=(StageGate("stage_ingress", "passed"),), started_at=NOW.isoformat(),
    )


@pytest.mark.parametrize("change", [
    {"contract": "scanner.stage/v2"}, {"attempt": True}, {"stage": "publication"},
    {"input_hash": None}, {"input_hash": "x" * 64}, {"started_at": "2026-11-26"},
    {"started_at": "0001-01-01T00:00:00+01:00"},
    {"completed_at": NOW.isoformat()}, {"output_hash": "b" * 64},
    {"input_refs": [{"id": "REF", "sha256": "bad", "kind": "input_snapshot"}]},
    {"input_refs": [{"id": "REF", "sha256": "a" * 64, "kind": "snapshot", "text": "not lineage"}]},
    {"gate_results": []}, {"gate_results": [{"gate": "stage_ingress", "status": "passed", "reasons": ["blocked"], "warnings": []}]},
    {"source_text": "not lineage"},
])
def test_envelope_rejects_malformed_and_contradictory_data(change):
    with pytest.raises(StageEnvelopeValidationError):
        validate_stage_envelope({**_ingress().to_dict(), **change})


@pytest.mark.parametrize("status", ["passed", "failed", "blocked", "insufficient_evidence", "validation_failed", "approval_required"])
def test_gate_identity_is_semantic_but_hash_binds_timestamp_and_fields(status):
    original = replace(_ingress(), gate_results=(StageGate(
        "stage_ingress", status, () if status == "passed" else ("test_rejection",),
    ),))
    later = NOW + timedelta(seconds=1)
    first = build_persisted_gate_result(original, checked_at=NOW)
    replay = build_persisted_gate_result(replace(original, started_at=later.isoformat()), checked_at=later)
    assert original == validate_stage_envelope(first.envelope)
    assert first.gate_result_id == replay.gate_result_id
    assert first.record_sha256 != replay.record_sha256
    validate_persisted_gate_result(first)
    with pytest.raises(GateResultIntegrityError):
        validate_persisted_gate_result(replace(first, input_hash="f" * 64))
    with pytest.raises(GateResultIntegrityError):
        validate_persisted_gate_result(replace(first, checked_at=later))


def test_operational_error_is_not_translated_into_a_gate():
    class Repository:
        def load_materials(self, *, run_id):
            return []

    class Executor:
        def execute(self, lease):
            raise TimeoutError("provider unavailable")

    lease = StepLease("RUN-EXAMPLE", "material_ingestion", 1, "owner", 1, NOW + timedelta(seconds=30))
    with pytest.raises(TimeoutError, match="provider unavailable"):
        run_gated_stage_step(Repository(), lease, Executor(), now=NOW)
