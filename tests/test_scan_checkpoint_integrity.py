"""Hermetic contracts for Card 3K-integrity checkpoint reload verification and durable save_to_database."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scanner_one import analyze_intent  # noqa: E402
from scan_checkpoint_integrity import validate_checkpoint_integrity  # noqa: E402
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
)
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_checkpoint import (  # noqa: E402
    build_foresight_checkpoint,
    build_foresight_input_projection,
)
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_checkpoint import (  # noqa: E402
    build_intent_checkpoint,
    build_intent_input_projection,
)
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import analyze_jury_lanes  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import (  # noqa: E402
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ScanCheckpoint,
    ScanRepository,
    ScanRun,
    ScanRunIntegrityError,
    StepLease,
)
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
)
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scan_verification_checkpoint import (  # noqa: E402
    build_verification_checkpoint,
    build_verification_input_projection,
)
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _recompute_hashes_and_id(checkpoint: ScanCheckpoint, snapshot: dict) -> ScanCheckpoint:
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": checkpoint.run_id,
            "stage": checkpoint.stage_name,
            "attempt": checkpoint.attempt,
            "contract": checkpoint.contract_version,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return replace(
        checkpoint,
        checkpoint_id=checkpoint_id,
        output_hash=output_hash,
        snapshot=snapshot,
    )


def _full_stage_checkpoints(run_id: str = "RUN-3K-001") -> dict[str, ScanCheckpoint]:
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text="Customer autonomy, platform modernization, and cloud engineering reliability.",
        uploads=[{"filename": "architecture.txt", "content": b"API platform and data lineage"}],
    ).materials
    material_cp = build_material_ingestion_checkpoint(
        run_id=run_id,
        attempt=1,
        materials=materials,
    )

    intent_retrieval = prepare_source_retrieval(
        run_id=run_id,
        stage_name="intent",
        attempt=1,
        retrieval_origin="intent_web_acquisition",
        source_url="https://example.test/strategy",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Engineering strategy</body></html>",
        extracted_text="Engineering strategy and customer platform outcomes.",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    intent_snapshot = build_intent_input_snapshot(
        run_id=run_id,
        attempt=1,
        materials=materials,
        retrievals=[intent_retrieval],
    )
    intent_projection = build_intent_input_projection(
        intent_snapshot,
        materials=materials,
        retrievals=[intent_retrieval],
    )
    intent_cp = build_intent_checkpoint(
        intent_projection,
        intent_result=analyze_intent(intent_projection.concatenated_text),
    )

    jury_candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/engineering",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text="Cached engineering platform extract.",
        metadata={"discovery_label": "Engineering blog", "source_type": "official"},
    )
    jury_retrieval = prepare_source_retrieval(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_live_acquisition",
        source_url="https://example.test/investor-update",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Investor update</body></html>",
        extracted_text="Fetched investor update on platform delivery.",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    jury_snapshot = build_jury_input_snapshot(
        run_id=run_id,
        attempt=1,
        intent_checkpoint=intent_cp,
        candidates=[jury_candidate],
        retrievals=[jury_retrieval],
    )
    jury_projection = build_jury_input_projection(
        jury_snapshot,
        intent_checkpoint=intent_cp,
        candidates=[jury_candidate],
        retrievals=[jury_retrieval],
    )
    jury_cp = build_jury_checkpoint(
        jury_projection,
        jury_result=analyze_jury_lanes(jury_projection),
    )

    diagnosis_snapshot = build_diagnosis_input_snapshot(
        run_id=run_id,
        attempt=1,
        jury_checkpoint=jury_cp,
    )
    diagnosis_projection = build_diagnosis_input_projection(
        diagnosis_snapshot,
        jury_checkpoint=jury_cp,
    )
    diagnosis_cp = build_diagnosis_checkpoint(
        diagnosis_projection,
        diagnosis_result={
            "diagnostic_summary": "Platform modernization is underway with legacy bottlenecks.",
            "evidence_based_risks": ["Legacy ERP coupling"],
            "reality_score": 64,
            "archetype": "Modernizing",
        },
    )

    foresight_snapshot = build_foresight_input_snapshot(
        run_id=run_id,
        attempt=1,
        diagnosis_checkpoint=diagnosis_cp,
    )
    foresight_projection = build_foresight_input_projection(
        foresight_snapshot,
        diagnosis_checkpoint=diagnosis_cp,
    )
    foresight_cp = build_foresight_checkpoint(
        foresight_projection,
        foresight_result={
            "dimension_forces": [
                {
                    "fp_theme": "AI Governance & Regulation",
                    "kb_dimension": "Regulation",
                    "trend": "EU AI Act compliance timelines",
                }
            ],
            "cross_impact_chain": "Regulatory pressure accelerates data lineage investment.",
            "scenarios": {"baseline": "Gradual compliance rollout"},
            "climate_exposure": {"transition": "low"},
        },
    )

    synthesis_snapshot = build_synthesis_input_snapshot(
        run_id=run_id,
        attempt=1,
        foresight_checkpoint=foresight_cp,
    )
    synthesis_projection = build_synthesis_input_projection(
        synthesis_snapshot,
        foresight_checkpoint=foresight_cp,
    )
    synthesis_cp = build_synthesis_checkpoint(
        synthesis_projection,
        synthesis_result={
            "strategic_synthesis": "Prioritize API decoupling and auditable AI governance controls."
        },
    )

    verification_snapshot = build_verification_input_snapshot(
        run_id=run_id,
        attempt=1,
        synthesis_checkpoint=synthesis_cp,
    )
    verification_projection = build_verification_input_projection(
        verification_snapshot,
        synthesis_checkpoint=synthesis_cp,
    )
    verification_cp = build_verification_checkpoint(
        verification_projection,
        verification_result={"verification_record": "verified-compact-summary"},
    )

    return {
        "material_ingestion": material_cp,
        "intent": intent_cp,
        "jury_analysis": jury_cp,
        "diagnosis": diagnosis_cp,
        "foresight": foresight_cp,
        "strategic_synthesis": synthesis_cp,
        "verification": verification_cp,
    }


class _SingleRowCursor:
    def __init__(self, row=None, *, secondary_row=None, rowcount=1):
        self.rows = [row, secondary_row]
        self.rowcount = rowcount
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)


class _RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _repo_for_row(row, *, secondary_row=None):
    cursor = _SingleRowCursor(row, secondary_row=secondary_row)
    conn = _RecordingConnection(cursor)
    repo = ScanRepository(
        "postgresql://scanner:test@localhost/scanner",
        connection_factory=lambda _dsn: conn,
    )
    return repo, cursor, conn


def test_all_seven_stage_checkpoints_pass_reload_integrity_and_cursor_roundtrip():
    checkpoints = _full_stage_checkpoints()
    assert len(checkpoints) == 7

    for stage_name, checkpoint in checkpoints.items():
        validate_checkpoint_integrity(checkpoint, require_passed=True)

        # Mapping-valued JSONB row (standard psycopg dict_row behavior)
        repo_dict, _, _ = _repo_for_row(
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "run_id": checkpoint.run_id,
                "stage_name": checkpoint.stage_name,
                "attempt": checkpoint.attempt,
                "contract_version": checkpoint.contract_version,
                "input_hash": checkpoint.input_hash,
                "output_hash": checkpoint.output_hash,
                "gate_result": dict(checkpoint.gate_result),
                "snapshot": json.loads(json.dumps(checkpoint.snapshot)),
            }
        )
        reloaded_dict = repo_dict.load_checkpoint(
            run_id=checkpoint.run_id,
            stage_name=stage_name,
            attempt=checkpoint.attempt,
        )
        assert reloaded_dict == checkpoint

        # String-encoded JSON row (text-mode cursor fallback)
        repo_str, _, _ = _repo_for_row(
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "run_id": checkpoint.run_id,
                "stage_name": checkpoint.stage_name,
                "attempt": checkpoint.attempt,
                "contract_version": checkpoint.contract_version,
                "input_hash": checkpoint.input_hash,
                "output_hash": checkpoint.output_hash,
                "gate_result": json.dumps(checkpoint.gate_result),
                "snapshot": json.dumps(checkpoint.snapshot),
            }
        )
        reloaded_str = repo_str.load_checkpoint(
            run_id=checkpoint.run_id,
            stage_name=stage_name,
            attempt=checkpoint.attempt,
        )
        assert reloaded_str == checkpoint


def test_load_checkpoint_rejects_in_place_json_payload_mutation_and_id_mismatch():
    checkpoints = _full_stage_checkpoints()
    diagnosis_cp = checkpoints["diagnosis"]

    # 1. Mutate JSON payload inside snapshot while keeping output_hash & checkpoint_id unchanged.
    mutated_snapshot = json.loads(json.dumps(diagnosis_cp.snapshot))
    mutated_snapshot["diagnosis_result"]["reality_score"] = 99
    mutated_snapshot["output_refs"] = [
        {
            "id": "DRO-"
            + _hash(
                {
                    "run_id": diagnosis_cp.run_id,
                    "stage": "diagnosis",
                    "attempt": 1,
                    "sha256": _hash(mutated_snapshot["diagnosis_result"]),
                }
            )[:32].upper(),
            "sha256": _hash(mutated_snapshot["diagnosis_result"]),
            "kind": "diagnosis_result",
        }
    ]
    repo_stale_hash, _, _ = _repo_for_row(
        {
            "checkpoint_id": diagnosis_cp.checkpoint_id,
            "run_id": diagnosis_cp.run_id,
            "stage_name": diagnosis_cp.stage_name,
            "attempt": diagnosis_cp.attempt,
            "contract_version": diagnosis_cp.contract_version,
            "input_hash": diagnosis_cp.input_hash,
            "output_hash": diagnosis_cp.output_hash,
            "gate_result": dict(diagnosis_cp.gate_result),
            "snapshot": mutated_snapshot,
        }
    )
    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repo_stale_hash.load_checkpoint(
            run_id=diagnosis_cp.run_id,
            stage_name="diagnosis",
            attempt=1,
        )

    # 2. Mutate snapshot AND recompute output_hash without updating checkpoint_id.
    recomputed_output_hash = _hash(mutated_snapshot)
    repo_stale_id, _, _ = _repo_for_row(
        {
            "checkpoint_id": diagnosis_cp.checkpoint_id,
            "run_id": diagnosis_cp.run_id,
            "stage_name": diagnosis_cp.stage_name,
            "attempt": diagnosis_cp.attempt,
            "contract_version": diagnosis_cp.contract_version,
            "input_hash": diagnosis_cp.input_hash,
            "output_hash": recomputed_output_hash,
            "gate_result": dict(diagnosis_cp.gate_result),
            "snapshot": mutated_snapshot,
        }
    )
    with pytest.raises(CheckpointIntegrityError, match="checkpoint_id mismatch"):
        repo_stale_id.load_checkpoint(
            run_id=diagnosis_cp.run_id,
            stage_name="diagnosis",
            attempt=1,
        )


def test_embedded_gate_output_ref_input_ref_and_forbidden_key_mutations_fail_closed():
    checkpoints = _full_stage_checkpoints()

    # 1. Embedded gate_results mismatch even if output_hash & checkpoint_id are recomputed.
    foresight_cp = checkpoints["foresight"]
    tampered_gate_snapshot = json.loads(json.dumps(foresight_cp.snapshot))
    tampered_gate_snapshot["gate_results"][0]["warnings"] = [{"code": "injected_warning"}]
    forged_gate_cp = _recompute_hashes_and_id(foresight_cp, tampered_gate_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="embedded gate_results"):
        validate_checkpoint_integrity(forged_gate_cp)

    # 2. Output ref sha256 mismatch even if output_hash & checkpoint_id are recomputed.
    synthesis_cp = checkpoints["strategic_synthesis"]
    tampered_outref_snapshot = json.loads(json.dumps(synthesis_cp.snapshot))
    tampered_outref_snapshot["output_refs"][0]["sha256"] = "f" * 64
    forged_outref_cp = _recompute_hashes_and_id(synthesis_cp, tampered_outref_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="output_refs do not match"):
        validate_checkpoint_integrity(forged_outref_cp)

    # 3. Upstream checkpoint_ref vs input_refs mismatch on Verification.
    verification_cp = checkpoints["verification"]
    tampered_inref_snapshot = json.loads(json.dumps(verification_cp.snapshot))
    tampered_inref_snapshot["input_refs"][0]["sha256"] = "e" * 64
    forged_inref_cp = _recompute_hashes_and_id(verification_cp, tampered_inref_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="input_refs do not match"):
        validate_checkpoint_integrity(forged_inref_cp)

    # 4. Upstream checkpoint_ref mutated to match input_refs, breaking input_hash binding.
    tampered_upref_snapshot = json.loads(json.dumps(verification_cp.snapshot))
    tampered_upref_snapshot["synthesis_checkpoint_ref"]["output_hash"] = "e" * 64
    tampered_upref_snapshot["input_refs"][0]["sha256"] = "e" * 64
    forged_upref_cp = _recompute_hashes_and_id(verification_cp, tampered_upref_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="input_hash does not match"):
        validate_checkpoint_integrity(forged_upref_cp)

    # 5. Tampered input_snapshot_ref on Intent.
    intent_cp = checkpoints["intent"]
    tampered_snapref_snapshot = json.loads(json.dumps(intent_cp.snapshot))
    tampered_snapref_snapshot["input_snapshot_ref"] = "intent-input://scan/IIS-" + "0" * 32
    forged_snapref_cp = _recompute_hashes_and_id(intent_cp, tampered_snapref_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="input_snapshot_ref"):
        validate_checkpoint_integrity(forged_snapref_cp)

    # 6. Extra raw text / KB payload injected into snapshot.
    injected_snapshot = json.loads(json.dumps(intent_cp.snapshot))
    injected_snapshot["extracted_text"] = "raw text must never live on checkpoints"
    forged_injected_cp = _recompute_hashes_and_id(intent_cp, injected_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(forged_injected_cp)

    # 7. Material ingestion material_refs hash tamper (even if output_hash & checkpoint_id are recomputed).
    material_cp = checkpoints["material_ingestion"]
    tampered_mat_snapshot = json.loads(json.dumps(material_cp.snapshot))
    tampered_mat_snapshot["material_refs"][0]["extracted_text_sha256"] = "d" * 64
    forged_mat_cp = _recompute_hashes_and_id(material_cp, tampered_mat_snapshot)
    with pytest.raises(CheckpointIntegrityError, match="input_hash does not match material_refs"):
        validate_checkpoint_integrity(forged_mat_cp)


def test_non_passed_proposals_validate_when_unpersisted_but_cannot_pass_persisted_reload():
    run_id = "RUN-3K-BLOCKED"
    broken_materials = prepare_scan_materials(
        run_id=run_id,
        report_text="",
        uploads=[{"filename": "broken.pdf", "content": b"not-a-valid-pdf"}],
    ).materials
    blocked_material_cp = build_material_ingestion_checkpoint(
        run_id=run_id,
        attempt=1,
        materials=broken_materials,
    )
    validate_checkpoint_integrity(blocked_material_cp, require_passed=False)
    with pytest.raises(CheckpointIntegrityError, match="status='passed'"):
        validate_checkpoint_integrity(blocked_material_cp, require_passed=True)

    checkpoints = _full_stage_checkpoints()
    synthesis_cp = checkpoints["strategic_synthesis"]
    verification_snapshot = build_verification_input_snapshot(
        run_id=synthesis_cp.run_id,
        attempt=1,
        synthesis_checkpoint=synthesis_cp,
    )
    verification_projection = build_verification_input_projection(
        verification_snapshot,
        synthesis_checkpoint=synthesis_cp,
    )
    failed_verification_cp = build_verification_checkpoint(
        verification_projection,
        verification_result={"wrong_key": "malformed"},
    )
    validate_checkpoint_integrity(failed_verification_cp, require_passed=False)
    with pytest.raises(CheckpointIntegrityError, match="status='passed'"):
        validate_checkpoint_integrity(failed_verification_cp, require_passed=True)


def test_load_checkpoint_rejects_nan_invalid_json_and_complete_step_rejects_forged_hash():
    checkpoints = _full_stage_checkpoints()
    intent_cp = checkpoints["intent"]

    repo_nan, _, _ = _repo_for_row(
        {
            "checkpoint_id": intent_cp.checkpoint_id,
            "run_id": intent_cp.run_id,
            "stage_name": intent_cp.stage_name,
            "attempt": intent_cp.attempt,
            "contract_version": intent_cp.contract_version,
            "input_hash": intent_cp.input_hash,
            "output_hash": intent_cp.output_hash,
            "gate_result": '{"gate":"scanner.intent/v1","status":"passed","reasons":[],"warnings":[]}',
            "snapshot": '{"score": NaN}',
        }
    )
    with pytest.raises(CheckpointIntegrityError, match="non-finite constant"):
        repo_nan.load_checkpoint(run_id=intent_cp.run_id, stage_name="intent", attempt=1)

    repo_bool_attempt, _, _ = _repo_for_row(
        {
            "checkpoint_id": intent_cp.checkpoint_id,
            "run_id": intent_cp.run_id,
            "stage_name": intent_cp.stage_name,
            "attempt": True,
            "contract_version": intent_cp.contract_version,
            "input_hash": intent_cp.input_hash,
            "output_hash": intent_cp.output_hash,
            "gate_result": dict(intent_cp.gate_result),
            "snapshot": dict(intent_cp.snapshot),
        }
    )
    with pytest.raises(CheckpointIntegrityError, match="invalid types"):
        repo_bool_attempt.load_checkpoint(run_id=intent_cp.run_id, stage_name="intent", attempt=1)

    # Write-side guard: complete_step_with_checkpoint refuses a forged output_hash before SQL runs.
    repo_write, cursor, _ = _repo_for_row(None)
    lease = StepLease(
        run_id=intent_cp.run_id,
        stage_name="intent",
        attempt=1,
        lease_owner="worker-1",
        fencing_token=1,
        lease_expires_at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=60),
    )
    tampered_write_cp = replace(intent_cp, output_hash="a" * 64)
    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repo_write.complete_step_with_checkpoint(
            lease,
            checkpoint=tampered_write_cp,
            next_stage="jury_analysis",
        )
    assert cursor.executed == []


def test_durable_save_to_database_repository_and_ingress_contracts():
    ingress_true = prepare_scan_ingress(
        company_name="Example Oy",
        company_url="https://example.test",
        report_text="Context",
        save_to_database=True,
        uploads=[],
        idempotency_key="save-flag-001",
    )
    ingress_false = prepare_scan_ingress(
        company_name="Example Oy",
        company_url="https://example.test",
        report_text="Context",
        save_to_database=False,
        uploads=[],
        idempotency_key="save-flag-001",
    )
    assert ingress_true.save_to_database is True
    assert ingress_false.save_to_database is False
    assert ingress_true.request_hash != ingress_false.request_hash

    for invalid_flag in (None, "false", "true", 0, 1):
        with pytest.raises(ValueError, match="save_to_database must be a boolean"):
            prepare_scan_ingress(
                company_name="Example Oy",
                company_url="https://example.test",
                report_text="Context",
                save_to_database=invalid_flag,  # type: ignore[arg-type]
                uploads=[],
                idempotency_key="save-flag-invalid",
            )

    repo_insert, cursor_insert, conn_insert = _repo_for_row(
        {
            "run_id": ingress_false.run_id,
            "status": "queued",
            "current_stage": "material_ingestion",
            "request_hash": ingress_false.request_hash,
            "save_to_database": False,
        }
    )
    created = repo_insert.create_run(
        run_id=ingress_false.run_id,
        company_name=ingress_false.company_name,
        company_url=ingress_false.company_url,
        idempotency_key=ingress_false.idempotency_key,
        request_hash=ingress_false.request_hash,
        cognitive_contract_version=ingress_false.cognitive_contract_version,
        initial_stage=ingress_false.initial_stage,
        save_to_database=False,
    )
    assert created["save_to_database"] is False
    insert_sql, insert_params = cursor_insert.executed[0]
    assert "save_to_database" in insert_sql
    assert insert_params[-1] is False
    assert conn_insert.committed is True

    for invalid_flag in (None, "false", 1):
        with pytest.raises(ValueError, match="save_to_database must be a boolean"):
            repo_insert.create_run(
                run_id=ingress_false.run_id,
                company_name=ingress_false.company_name,
                company_url=ingress_false.company_url,
                idempotency_key=ingress_false.idempotency_key,
                request_hash=ingress_false.request_hash,
                cognitive_contract_version=ingress_false.cognitive_contract_version,
                initial_stage=ingress_false.initial_stage,
                save_to_database=invalid_flag,  # type: ignore[arg-type]
            )

    # Conflict path rejects flipped save_to_database even if caller supplies identical request_hash.
    repo_conflict, _, _ = _repo_for_row(
        None,
        secondary_row={
            "run_id": ingress_false.run_id,
            "status": "queued",
            "current_stage": "material_ingestion",
            "request_hash": ingress_false.request_hash,
            "save_to_database": False,
        },
    )
    with pytest.raises(ValueError, match="different scan input"):
        repo_conflict.create_run(
            run_id=ingress_false.run_id,
            company_name=ingress_false.company_name,
            company_url=ingress_false.company_url,
            idempotency_key=ingress_false.idempotency_key,
            request_hash=ingress_false.request_hash,
            cognitive_contract_version=ingress_false.cognitive_contract_version,
            initial_stage=ingress_false.initial_stage,
            save_to_database=True,
        )

    # load_run round-trips ScanRun and rejects non-bool save_to_database.
    repo_load, _, _ = _repo_for_row(
        {
            "run_id": ingress_false.run_id,
            "company_name": "Example Oy",
            "company_url": "https://example.test",
            "cognitive_contract_version": "scanner-cognitive/v1",
            "status": "running",
            "current_stage": "diagnosis",
            "idempotency_key": "save-flag-001",
            "request_hash": ingress_false.request_hash,
            "save_to_database": False,
            "final_artifact_ref": None,
        }
    )
    loaded_run = repo_load.load_run(run_id=ingress_false.run_id)
    assert loaded_run == ScanRun(
        run_id=ingress_false.run_id,
        company_name="Example Oy",
        company_url="https://example.test",
        cognitive_contract_version="scanner-cognitive/v1",
        status="running",
        current_stage="diagnosis",
        idempotency_key="save-flag-001",
        request_hash=ingress_false.request_hash,
        save_to_database=False,
        final_artifact_ref=None,
    )

    repo_corrupt_run, _, _ = _repo_for_row(
        {
            "run_id": ingress_false.run_id,
            "company_name": "Example Oy",
            "company_url": "https://example.test",
            "cognitive_contract_version": "scanner-cognitive/v1",
            "status": "running",
            "current_stage": "diagnosis",
            "idempotency_key": "save-flag-001",
            "request_hash": ingress_false.request_hash,
            "save_to_database": 1,
            "final_artifact_ref": None,
        }
    )
    with pytest.raises(ScanRunIntegrityError, match="malformed"):
        repo_corrupt_run.load_run(run_id=ingress_false.run_id)


def test_checkpoint_integrity_module_has_no_live_scan_blob_or_acquisition_coupling():
    source = (_BACKEND / "scan_checkpoint_integrity.py").read_text(encoding="utf-8")
    assert "from main" not in source
    assert "import main" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "BlobStorage" not in source
    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "generate_foresight" not in source
    assert "foresight_harvester" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
