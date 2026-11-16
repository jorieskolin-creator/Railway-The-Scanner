"""Disposable PostgreSQL contracts for Card 3L-Jury-parity five-Jury cognition and model-policy binding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from model_policy import GPT_REASONING_MODEL, MODEL_POLICY_VERSION  # noqa: E402
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_checkpoint import build_diagnosis_input_projection  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import JuryStageExecutor  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_LANES  # noqa: E402
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    DiagnosisInputSnapshotIntegrityError,
    ScanRepository,
)


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "diagnosis_input_snapshots, jury_input_snapshots, jury_source_candidates, "
    "intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, "
    "scan_events, scan_steps, scan_runs"
)

_MIGRATIONS = (
    "001_scan_execution_state.sql",
    "002_scan_materials.sql",
    "003_scan_checkpoints.sql",
    "004_source_retrievals.sql",
    "005_intent_input_snapshots.sql",
    "006_jury_source_candidates.sql",
    "007_jury_input_snapshots.sql",
    "008_diagnosis_input_snapshots.sql",
    "012_scan_runs_save_to_database.sql",
)


@pytest.fixture(autouse=True)
def jury_cognition_postgres_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in _MIGRATIONS
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _prepare_leased_jury(repository: ScanRepository, *, now: datetime):
    ingress = prepare_scan_ingress(
        company_name="Nordic Grid Oy",
        company_url="https://nordicgrid.example",
        report_text="Nordic Grid Oy modernizes industrial APIs and cloud data telemetry.",
        save_to_database=False,
        uploads=[{"filename": "overview.txt", "content": b"API and telemetry architecture"}],
        idempotency_key=f"jury-parity-pg-{uuid4()}",
    )
    repository.create_run_with_materials(
        run_id=ingress.run_id,
        company_name=ingress.company_name,
        company_url=ingress.company_url,
        idempotency_key=ingress.idempotency_key,
        request_hash=ingress.request_hash,
        cognitive_contract_version=ingress.cognitive_contract_version,
        initial_stage=ingress.initial_stage,
        materials=list(ingress.materials),
        save_to_database=ingress.save_to_database,
    )
    mat_lease = repository.claim_next_step(
        lease_owner="pg-material", lease_seconds=30, now=now
    )
    assert mat_lease is not None
    repository.complete_step_with_checkpoint(
        mat_lease,
        checkpoint=build_material_ingestion_checkpoint(
            run_id=ingress.run_id,
            attempt=1,
            materials=repository.load_materials(run_id=ingress.run_id),
        ),
        next_stage="intent",
        now=now + timedelta(seconds=1),
    )

    intent_lease = repository.claim_next_step(
        lease_owner="pg-intent", lease_seconds=30, now=now + timedelta(seconds=2)
    )
    assert intent_lease is not None
    repository.persist_intent_input_snapshot(
        snapshot=build_intent_input_snapshot(
            run_id=ingress.run_id,
            attempt=1,
            materials=repository.load_materials(run_id=ingress.run_id),
            retrievals=[],
        )
    )
    intent_res = IntentStageExecutor(repository).execute(intent_lease)
    intent_cp = commit_intent_stage(
        repository, intent_lease, intent_res, now=now + timedelta(seconds=3)
    )

    candidate = prepare_jury_source_candidate(
        run_id=ingress.run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://nordicgrid.example/developers/apis",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text=(
            "Nordic Grid Oy operates an OpenAPI developer gateway and event-streaming "
            "telemetry platform across regional substations."
        ),
        metadata={"discovery_label": "Developer Gateway", "source_type": "technical_artifact"},
    )
    retrieval = prepare_source_retrieval(
        run_id=ingress.run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_live_acquisition",
        source_url="https://nordicgrid.example/ai-operations",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>AI operations</body></html>",
        extracted_text=(
            "Nordic Grid Oy deploys cloud data analytics and predictive maintenance "
            "machine learning models for grid reliability."
        ),
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    repository.persist_jury_source_candidates(candidates=[candidate])
    repository.persist_source_retrievals(retrievals=[retrieval])
    repository.persist_jury_input_snapshot(
        snapshot=build_jury_input_snapshot(
            run_id=ingress.run_id,
            attempt=1,
            intent_checkpoint=intent_cp,
            candidates=repository.load_jury_source_candidates(
                run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
            ),
            retrievals=repository.load_source_retrievals(
                run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
            ),
        )
    )
    jury_lease = repository.claim_next_step(
        lease_owner="pg-jury-parity", lease_seconds=30, now=now + timedelta(seconds=4)
    )
    assert jury_lease is not None
    return ingress, jury_lease, candidate, retrieval


def _deterministic_jury_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    assert "Nordic Grid Oy" in prompt
    label = kwargs.get("log_label", "")
    if "ARCHITECTURAL JURY" in label:
        return json.dumps(
            {
                "jury": "ARCHITECTURAL JURY (Engineering Reality)",
                "findings": [
                    {
                        "category": "ARCHITECTURAL JURY (Engineering Reality)",
                        "supported_claim": (
                            "Nordic Grid Oy operates an OpenAPI developer gateway "
                            "and event-streaming telemetry platform"
                        ),
                        "found": True,
                        "sentiment": "positive",
                        "source_quote": (
                            "Nordic Grid Oy operates an OpenAPI developer gateway and "
                            "event-streaming telemetry platform across regional substations."
                        ),
                        "title": "Developer Gateway",
                        "url": "https://nordicgrid.example/developers/apis",
                        "is_recent": True,
                        "source_type": "technical_artifact",
                        "evidence_package_id": "",
                        "proposition_ids": [],
                        "support_passage_ids": [],
                        "subdimensions": ["architecture"],
                        "entity_scope": "company",
                        "source_verdict": "accepted",
                        "support_level": "exact",
                        "confidence": 84,
                    }
                ],
                "rejected_evidence": [],
                "contradictions": [],
                "missing_coverage": [],
                "confidence_score": 80,
                "reality_score_adjustment": 4,
            }
        )
    for lane_name in JURY_CHECKPOINT_LANES:
        if lane_name in label:
            return json.dumps(
                {
                    "jury": lane_name,
                    "findings": [],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 0,
                    "reality_score_adjustment": 0,
                }
            )
    raise AssertionError(f"Unexpected Jury label: {label}")


def test_postgres_cognitive_parity_jury_commits_and_hydrates_diagnosis_and_guards_policy_version():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 11, 13, 13, 0, tzinfo=timezone.utc)
    ingress, jury_lease, candidate, _retrieval = _prepare_leased_jury(repository, now=now)

    executor = JuryStageExecutor.with_cognitive_parity(
        repository,
        openai_caller=_deterministic_jury_openai,
    )
    prepared = executor.execute(jury_lease)

    # Prepare-only invariant: nothing persisted yet.
    assert repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
    ) is None

    committed = commit_jury_stage(
        repository, jury_lease, prepared, now=now + timedelta(seconds=5)
    )
    reloaded = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
    )
    assert reloaded == committed
    assert reloaded is not None
    assert reloaded.snapshot["model_policy_version"] == MODEL_POLICY_VERSION

    # Replay with upgraded model_policy_version is rejected without mutating DB state.
    upgraded_executor = JuryStageExecutor.with_cognitive_parity(
        repository,
        openai_caller=_deterministic_jury_openai,
        model_policy_version="model-policy-v6-upgraded",
    )
    upgraded_prepared = upgraded_executor.execute(jury_lease)
    with pytest.raises(ValueError, match="different Jury checkpoint"):
        commit_jury_stage(
            repository, jury_lease, upgraded_prepared, now=now + timedelta(seconds=6)
        )

    # Downstream Diagnosis binds and projects the passage-verified five-Jury findings cleanly.
    diagnosis_snapshot = build_diagnosis_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        jury_checkpoint=reloaded,
    )
    repository.persist_diagnosis_input_snapshot(snapshot=diagnosis_snapshot)
    verified_diag_snapshot, hydrated_jury_cp = repository.load_verified_diagnosis_inputs(
        run_id=ingress.run_id, attempt=1
    )
    diag_projection = build_diagnosis_input_projection(
        verified_diag_snapshot, jury_checkpoint=hydrated_jury_cp
    )
    arch_lane = diag_projection.jury_result["juries"][0]
    assert len(arch_lane["findings"]) == 1
    assert arch_lane["findings"][0]["source_id"] == candidate.candidate_id
    assert arch_lane["findings"][0]["evidence_package_id"].startswith("PKG-")

    run = repository.load_run(run_id=ingress.run_id)
    assert run is not None and run.save_to_database is False

    # Mutating model_policy_version inside scan_checkpoints.snapshot fails closed on reload & Diagnosis hydration.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{model_policy_version}', '"tampered-policy"'::jsonb)
            WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
            """,
            (ingress.run_id,),
        )

    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        )
    with pytest.raises(
        DiagnosisInputSnapshotIntegrityError,
        match="does not match the verified durable inputs",
    ):
        repository.load_verified_diagnosis_inputs(run_id=ingress.run_id, attempt=1)
