"""Disposable PostgreSQL contracts for Card 3L-Diagnosis-parity AI Diagnosis cognition and model-policy binding."""

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

from model_policy import CLAUDE_PREMIUM_MODEL, GPT_REASONING_MODEL, MODEL_POLICY_VERSION  # noqa: E402
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_adapter import DiagnosisStageExecutor  # noqa: E402
from scan_diagnosis_commit import commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_checkpoint import build_foresight_input_projection  # noqa: E402
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import JuryStageExecutor  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_LANES  # noqa: E402
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ForesightInputSnapshotIntegrityError,
    ScanRepository,
)


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "foresight_input_snapshots, diagnosis_input_snapshots, jury_input_snapshots, "
    "jury_source_candidates, intent_input_snapshots, source_retrievals, "
    "scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs"
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
    "009_foresight_input_snapshots.sql",
    "012_scan_runs_save_to_database.sql",
)


@pytest.fixture(autouse=True)
def diagnosis_cognition_postgres_schema():
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


def _deterministic_jury_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    label = kwargs.get("log_label", "")
    if "ARCHITECTURAL JURY" in label:
        return json.dumps(
            {
                "jury": "ARCHITECTURAL JURY (Engineering Reality)",
                "findings": [
                    {
                        "category": "ARCHITECTURAL JURY (Engineering Reality)",
                        "supported_claim": (
                            "Aurora Robotics Oy provides an event-driven fleet API "
                            "and edge telemetry platform"
                        ),
                        "found": True,
                        "sentiment": "positive",
                        "source_quote": (
                            "Aurora Robotics Oy provides an event-driven fleet API and "
                            "edge telemetry platform for warehouse automation."
                        ),
                        "title": "Fleet API Docs",
                        "url": "https://aurorarobotics.example/developers/fleet-api",
                        "is_recent": True,
                        "source_type": "technical_artifact",
                        "evidence_package_id": "",
                        "proposition_ids": [],
                        "support_passage_ids": [],
                        "subdimensions": ["architecture"],
                        "entity_scope": "company",
                        "source_verdict": "accepted",
                        "support_level": "exact",
                        "confidence": 85,
                    }
                ],
                "rejected_evidence": [],
                "contradictions": [],
                "missing_coverage": ["No third-party security audit"],
                "confidence_score": 80,
                "reality_score_adjustment": 5,
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


def _prepare_leased_diagnosis(repository: ScanRepository, *, now: datetime):
    ingress = prepare_scan_ingress(
        company_name="Aurora Robotics Oy",
        company_url="https://aurorarobotics.example",
        report_text=(
            "Aurora Robotics Oy builds autonomous warehouse platforms with continuous "
            "delivery, customer value streams, and psychological safety."
        ),
        save_to_database=False,
        uploads=[{"filename": "platform.txt", "content": b"Event-driven fleet API architecture"}],
        idempotency_key=f"diagnosis-parity-pg-{uuid4()}",
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
        source_url="https://aurorarobotics.example/developers/fleet-api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text=(
            "Aurora Robotics Oy provides an event-driven fleet API and "
            "edge telemetry platform for warehouse automation."
        ),
        metadata={"discovery_label": "Fleet API Docs", "source_type": "technical_artifact"},
    )
    repository.persist_jury_source_candidates(candidates=[candidate])
    repository.persist_jury_input_snapshot(
        snapshot=build_jury_input_snapshot(
            run_id=ingress.run_id,
            attempt=1,
            intent_checkpoint=intent_cp,
            candidates=repository.load_jury_source_candidates(
                run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
            ),
            retrievals=[],
        )
    )
    jury_lease = repository.claim_next_step(
        lease_owner="pg-jury", lease_seconds=30, now=now + timedelta(seconds=4)
    )
    assert jury_lease is not None
    jury_res = JuryStageExecutor.with_cognitive_parity(
        repository, openai_caller=_deterministic_jury_openai
    ).execute(jury_lease)
    jury_cp = commit_jury_stage(
        repository, jury_lease, jury_res, now=now + timedelta(seconds=5)
    )

    repository.persist_diagnosis_input_snapshot(
        snapshot=build_diagnosis_input_snapshot(
            run_id=ingress.run_id,
            attempt=1,
            jury_checkpoint=jury_cp,
        )
    )
    diag_lease = repository.claim_next_step(
        lease_owner="pg-diagnosis-parity",
        lease_seconds=30,
        now=now + timedelta(seconds=6),
    )
    assert diag_lease is not None
    return ingress, diag_lease


def _deterministic_diagnosis_anthropic(prompt: str, model: str = CLAUDE_PREMIUM_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    assert kwargs.get("reasoning_effort") == "high"
    assert "Aurora Robotics Oy" in prompt
    assert "event-driven fleet API" in prompt
    return json.dumps(
        {
            "diagnostic_summary": (
                "[C] Aurora Robotics Oy states it operates an event-driven fleet API "
                "platform (TAC-101) with Thriving technical foundations.\n\n"
                "However, the lack of verified evidence proves that independent security "
                "audits are missing."
            ),
            "evidence_based_risks": [
                "No third-party security audit is publicly documented.",
            ],
            "reality_score": 66,
            "archetype": "At Risk",
        }
    )


def test_postgres_cognitive_parity_diagnosis_commits_hydrates_foresight_and_guards_policy_version():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
    ingress, diag_lease = _prepare_leased_diagnosis(repository, now=now)

    executor = DiagnosisStageExecutor.with_cognitive_parity(
        repository,
        financial_index=65,
        anthropic_caller=_deterministic_diagnosis_anthropic,
    )
    prepared = executor.execute(diag_lease)

    # Prepare-only invariant: no Diagnosis checkpoint row before commit.
    assert repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="diagnosis", attempt=1
    ) is None

    committed = commit_diagnosis_stage(
        repository, diag_lease, prepared, now=now + timedelta(seconds=7)
    )
    reloaded = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="diagnosis", attempt=1
    )
    assert reloaded == committed
    assert reloaded is not None
    assert reloaded.snapshot["model_policy_version"] == MODEL_POLICY_VERSION

    # Replay across a model_policy_version upgrade is rejected.
    upgraded_prepared = DiagnosisStageExecutor.with_cognitive_parity(
        repository,
        financial_index=65,
        anthropic_caller=_deterministic_diagnosis_anthropic,
        model_policy_version="model-policy-v6-upgraded",
    ).execute(diag_lease)
    with pytest.raises(ValueError, match="different Diagnosis checkpoint"):
        commit_diagnosis_stage(
            repository, diag_lease, upgraded_prepared, now=now + timedelta(seconds=8)
        )

    # Downstream Foresight binds and projects the sanitized AI Diagnosis result cleanly.
    foresight_snapshot = build_foresight_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        diagnosis_checkpoint=reloaded,
    )
    repository.persist_foresight_input_snapshot(snapshot=foresight_snapshot)
    verified_fs_snapshot, hydrated_diag_cp = repository.load_verified_foresight_inputs(
        run_id=ingress.run_id, attempt=1
    )
    fs_projection = build_foresight_input_projection(
        verified_fs_snapshot, diagnosis_checkpoint=hydrated_diag_cp
    )
    assert fs_projection.diagnosis_result["reality_score"] == 66
    assert fs_projection.diagnosis_result["archetype"] == "Thriving"
    assert "Thriving" not in fs_projection.diagnosis_result["diagnostic_summary"]
    assert "TAC-101" not in fs_projection.diagnosis_result["diagnostic_summary"]
    assert (
        "the unresolved question is whether independent security audits are missing"
        in fs_projection.diagnosis_result["diagnostic_summary"]
    )

    run = repository.load_run(run_id=ingress.run_id)
    assert run is not None and run.save_to_database is False

    # Mutating model_policy_version inside scan_checkpoints.snapshot fails closed on reload & Foresight hydration.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{model_policy_version}', '"tampered-policy"'::jsonb)
            WHERE run_id = %s AND stage_name = 'diagnosis' AND attempt = 1
            """,
            (ingress.run_id,),
        )

    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="diagnosis", attempt=1
        )
    with pytest.raises(
        ForesightInputSnapshotIntegrityError,
        match="does not match the verified durable inputs",
    ):
        repository.load_verified_foresight_inputs(run_id=ingress.run_id, attempt=1)
