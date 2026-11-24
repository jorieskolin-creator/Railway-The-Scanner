"""Disposable PostgreSQL contracts for Card 3L-Verification-parity across the full 7-stage cognitive chain."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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

from model_policy import (  # noqa: E402
    CLAUDE_PREMIUM_MODEL,
    GPT_PRO_MODEL,
    GPT_REASONING_MODEL,
    GPT_VERIFIER_MODEL,
    MODEL_POLICY_VERSION,
)
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_adapter import DiagnosisStageExecutor  # noqa: E402
from scan_diagnosis_commit import commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_durable_loop import DURABLE_STAGE_ORDER  # noqa: E402
from scan_foresight_adapter import ForesightStageExecutor  # noqa: E402
from scan_foresight_commit import commit_foresight_stage  # noqa: E402
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
    ScanRepository,
)
from scan_synthesis_adapter import SynthesisStageExecutor  # noqa: E402
from scan_synthesis_commit import commit_synthesis_stage  # noqa: E402
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scan_verification_adapter import VerificationStageExecutor  # noqa: E402
from scan_verification_cognition import parse_verification_record  # noqa: E402
from scan_verification_commit import commit_verification_stage  # noqa: E402
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "verification_input_snapshots, synthesis_input_snapshots, foresight_input_snapshots, "
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
    "009_foresight_input_snapshots.sql",
    "010_synthesis_input_snapshots.sql",
    "011_verification_input_snapshots.sql",
    "012_scan_runs_save_to_database.sql",
)


@pytest.fixture(autouse=True)
def verification_cognition_postgres_schema():
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


def _kb_signals() -> dict[str, list[dict]]:
    def sig(title: str, url: str, theme: str, desc: str) -> dict:
        return {
            "title": title,
            "source_url": url,
            "fp_theme": theme,
            "_kb_theme": theme,
            "description": desc,
            "signal_type": "Probable/Emerging",
            "timeframe": "12-24mo",
            "source_type": "industry_analyst",
            "source_credibility": "high",
            "confidence": "high",
            "claim_type": "foresight_context",
            "company_specific": False,
            "not_evidence_of_deployment": True,
            "requires_company_specific_corroboration": True,
            "modal_language_required": True,
            "foresight_context_eligible": True,
            "foresight_signal_status": "active_context",
            "source_audit_status": "accepted_active",
            "url_verified": True,
            "source_readable": True,
            "trend_supported": True,
            "theme_match": True,
            "foresight_evidence_version": "foresight-active-v3",
            "supporting_sources": [],
        }

    return {
        "Artificial Intelligence": [
            sig(
                "EU AI Governance Rules",
                "https://kb.example.org/eu-ai-governance",
                "Artificial Intelligence",
                "Enterprise AI providers evaluate auditability and transparency controls.",
            ),
        ],
        "Data & Digitalisation": [
            sig(
                "Industrial Telemetry Mesh",
                "https://kb.example.org/telemetry-mesh",
                "Data & Digitalisation",
                "Industrial SaaS platforms decouple telemetry ingestion pipelines.",
            ),
        ],
        "Business": [
            sig(
                "SLA-Backed Procurement",
                "https://kb.example.org/sla-procurement",
                "Business",
                "Enterprise procurement requires measurable service reliability.",
            ),
        ],
    }


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
                            "Nordic Pulse Oy provides an OpenAPI telemetry platform "
                            "for industrial equipment."
                        ),
                        "found": True,
                        "sentiment": "positive",
                        "source_quote": (
                            "Nordic Pulse Oy provides an OpenAPI telemetry platform "
                            "for industrial equipment."
                        ),
                        "title": "Telemetry API Docs",
                        "url": "https://nordicpulse.example/developers/api",
                        "is_recent": True,
                        "source_type": "technical_artifact",
                        "evidence_package_id": "",
                        "proposition_ids": [],
                        "support_passage_ids": [],
                        "subdimensions": ["architecture"],
                        "entity_scope": "company",
                        "source_verdict": "accepted",
                        "support_level": "exact",
                        "confidence": 82,
                    }
                ],
                "rejected_evidence": [],
                "contradictions": [],
                "missing_coverage": [],
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


def _deterministic_diagnosis_anthropic(prompt: str, model: str = CLAUDE_PREMIUM_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    return json.dumps(
        {
            "diagnostic_summary": "Nordic Pulse Oy operates an OpenAPI telemetry platform.",
            "evidence_based_risks": ["Independent AI governance evidence is limited."],
            "reality_score": 60,
            "archetype": "Thriving",
        }
    )


def _deterministic_foresight_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    return json.dumps(
        {
            "dimension_forces": [
                {
                    "trend": "EU AI Governance Rules",
                    "type": "Probable/Emerging",
                    "timeframe": "12-24mo",
                    "kb_dimension": "AI Readiness",
                    "fp_theme": "Artificial Intelligence",
                    "impact_on_company": "Nordic Pulse Oy could evaluate governance auditability.",
                    "source_url": "https://kb.example.org/eu-ai-governance",
                    "source_type": "industry_analyst",
                    "confidence": "high",
                }
            ],
            "cross_impact_chain": "AI Governance -> Operating Model",
            "scenarios": {
                "acceleration": "Strengthen compliance telemetry.",
                "status_quo": "Gradual readiness.",
                "disruption": "Tighter EU enforcement.",
            },
        }
    )


def _deterministic_synthesis_openai(prompt: str, model: str = GPT_PRO_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    return json.dumps(
        {
            "strategic_synthesis": (
                "Nordic Pulse Oy anchors its industrial telemetry platform on OpenAPI capabilities "
                "while evaluating EU AI governance readiness over 12-24mo horizons."
            )
        }
    )


def _deterministic_verification_openai(prompt: str, model: str = GPT_VERIFIER_MODEL, **kwargs):
    assert kwargs.get("web_search") is False
    assert "Nordic Pulse Oy" in prompt
    assert "OpenAPI telemetry platform" in prompt
    return json.dumps({"summary_accurate": True, "false_claims": []})


def test_postgres_full_seven_stage_cognitive_parity_completes_without_publication():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc)

    ingress = prepare_scan_ingress(
        company_name="Nordic Pulse Oy",
        company_url="https://nordicpulse.example",
        report_text="Nordic Pulse Oy provides industrial APIs and cloud telemetry.",
        save_to_database=False,
        uploads=[{"filename": "brief.txt", "content": b"OpenAPI telemetry architecture"}],
        idempotency_key=f"verification-parity-pg-{uuid4()}",
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

    mat_lease = repository.claim_next_step(lease_owner="pg-mat", lease_seconds=30, now=now)
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
    intent_cp = commit_intent_stage(
        repository,
        intent_lease,
        IntentStageExecutor(repository).execute(intent_lease),
        now=now + timedelta(seconds=3),
    )

    candidate = prepare_jury_source_candidate(
        run_id=ingress.run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://nordicpulse.example/developers/api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text="Nordic Pulse Oy provides an OpenAPI telemetry platform for industrial equipment.",
        metadata={"discovery_label": "Telemetry API Docs", "source_type": "technical_artifact"},
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
    jury_cp = commit_jury_stage(
        repository,
        jury_lease,
        JuryStageExecutor.with_cognitive_parity(
            repository, openai_caller=_deterministic_jury_openai
        ).execute(jury_lease),
        now=now + timedelta(seconds=5),
    )

    repository.persist_diagnosis_input_snapshot(
        snapshot=build_diagnosis_input_snapshot(
            run_id=ingress.run_id, attempt=1, jury_checkpoint=jury_cp
        )
    )
    diag_lease = repository.claim_next_step(
        lease_owner="pg-diag", lease_seconds=30, now=now + timedelta(seconds=6)
    )
    assert diag_lease is not None
    diag_cp = commit_diagnosis_stage(
        repository,
        diag_lease,
        DiagnosisStageExecutor.with_cognitive_parity(
            repository,
            financial_index=60,
            anthropic_caller=_deterministic_diagnosis_anthropic,
        ).execute(diag_lease),
        now=now + timedelta(seconds=7),
    )

    repository.persist_foresight_input_snapshot(
        snapshot=build_foresight_input_snapshot(
            run_id=ingress.run_id, attempt=1, diagnosis_checkpoint=diag_cp
        )
    )
    fs_lease = repository.claim_next_step(
        lease_owner="pg-fs", lease_seconds=30, now=now + timedelta(seconds=8)
    )
    assert fs_lease is not None
    fs_cp = commit_foresight_stage(
        repository,
        fs_lease,
        ForesightStageExecutor.with_cognitive_parity(
            repository,
            domain="Technology & Software",
            kb_loader=lambda _themes: _kb_signals(),
            openai_caller=_deterministic_foresight_openai,
            report_date=date(2026, 11, 13),
        ).execute(fs_lease),
        now=now + timedelta(seconds=9),
    )

    repository.persist_synthesis_input_snapshot(
        snapshot=build_synthesis_input_snapshot(
            run_id=ingress.run_id, attempt=1, foresight_checkpoint=fs_cp
        )
    )
    synth_lease = repository.claim_next_step(
        lease_owner="pg-synth", lease_seconds=30, now=now + timedelta(seconds=10)
    )
    assert synth_lease is not None
    synth_cp = commit_synthesis_stage(
        repository,
        synth_lease,
        SynthesisStageExecutor.with_cognitive_parity(
            repository,
            domain="Technology & Software",
            openai_caller=_deterministic_synthesis_openai,
            report_date=date(2026, 11, 13),
        ).execute(synth_lease),
        now=now + timedelta(seconds=11),
    )

    repository.persist_verification_input_snapshot(
        snapshot=build_verification_input_snapshot(
            run_id=ingress.run_id, attempt=1, synthesis_checkpoint=synth_cp
        )
    )
    ver_lease = repository.claim_next_step(
        lease_owner="pg-ver", lease_seconds=30, now=now + timedelta(seconds=12)
    )
    assert ver_lease is not None

    executor = VerificationStageExecutor.with_cognitive_parity(
        repository,
        domain="Technology & Software",
        openai_caller=_deterministic_verification_openai,
    )
    prepared = executor.execute(ver_lease)
    assert prepared.next_stage is None
    assert repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="verification", attempt=1
    ) is None

    committed = commit_verification_stage(
        repository, ver_lease, prepared, now=now + timedelta(seconds=13)
    )
    reloaded = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="verification", attempt=1
    )
    assert reloaded == committed
    assert reloaded is not None
    assert reloaded.snapshot["model_policy_version"] == MODEL_POLICY_VERSION
    parsed = parse_verification_record(
        reloaded.snapshot["verification_result"]["verification_record"]
    )
    assert parsed is not None
    assert parsed["verified"] is True
    assert parsed["verification_status"] == "verified"

    # Policy-upgrade replay rejected without mutating DB state.
    upgraded_prepared = VerificationStageExecutor.with_cognitive_parity(
        repository,
        domain="Technology & Software",
        openai_caller=_deterministic_verification_openai,
        model_policy_version="model-policy-v6-upgraded",
    ).execute(ver_lease)
    with pytest.raises(ValueError, match="different Verification checkpoint"):
        commit_verification_stage(
            repository, ver_lease, upgraded_prepared, now=now + timedelta(seconds=14)
        )

    completed_run = repository.load_run(run_id=ingress.run_id)
    assert completed_run is not None
    assert completed_run.status == "completed"
    assert completed_run.current_stage is None
    assert completed_run.save_to_database is False
    assert completed_run.final_artifact_ref is None

    with psycopg.connect(DATABASE_URL) as connection:
        step_stages = [
            row[0]
            for row in connection.execute(
                "SELECT stage_name FROM scan_steps WHERE run_id = %s ORDER BY created_at, stage_name",
                (ingress.run_id,),
            ).fetchall()
        ]
        artifact_tables = connection.execute(
            "SELECT to_regclass('public.artifacts'), to_regclass('public.scan_artifacts')"
        ).fetchone()
    assert step_stages == list(DURABLE_STAGE_ORDER)
    assert artifact_tables == (None, None)

    # Mutating model_policy_version inside scan_checkpoints.snapshot fails closed on reload.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{model_policy_version}', '"tampered-policy"'::jsonb)
            WHERE run_id = %s AND stage_name = 'verification' AND attempt = 1
            """,
            (ingress.run_id,),
        )
    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="verification", attempt=1
        )
