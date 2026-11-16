"""Disposable PostgreSQL contracts for Card 3L-Foresight-parity KB-first cognition and KB/policy binding."""

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

from model_policy import CLAUDE_PREMIUM_MODEL, GPT_REASONING_MODEL, MODEL_POLICY_VERSION  # noqa: E402
from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_adapter import DiagnosisStageExecutor  # noqa: E402
from scan_diagnosis_commit import commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_adapter import ForesightStageExecutor  # noqa: E402
from scan_foresight_cognition import resolve_central_kb_binding  # noqa: E402
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
    SynthesisInputSnapshotIntegrityError,
)
from scan_synthesis_checkpoint import build_synthesis_input_projection  # noqa: E402
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "synthesis_input_snapshots, foresight_input_snapshots, diagnosis_input_snapshots, "
    "jury_input_snapshots, jury_source_candidates, intent_input_snapshots, "
    "source_retrievals, scan_checkpoints, scan_materials, scan_events, "
    "scan_steps, scan_runs"
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
    "012_scan_runs_save_to_database.sql",
)


@pytest.fixture(autouse=True)
def foresight_cognition_postgres_schema():
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
    assert "Nordic Pulse Oy" in prompt
    assert "EU AI Governance Rules" in prompt
    return json.dumps(
        {
            "dimension_forces": [
                {
                    "trend": "EU AI Governance Rules",
                    "type": "Probable/Emerging",
                    "timeframe": "12-24mo",
                    "kb_dimension": "AI Readiness",
                    "fp_theme": "Artificial Intelligence",
                    "impact_on_company": "Nordic Pulse Oy must evaluate governance auditability.",
                    "proposal_angle": "Nordic Pulse Oy will fall behind without early readiness review.",
                    "source_url": "https://kb.example.org/eu-ai-governance",
                    "source_type": "industry_analyst",
                    "confidence": "high",
                }
            ],
            "cross_impact_chain": "AI Governance -> Operating Model",
            "scenarios": {
                "acceleration": "Nordic Pulse Oy must strengthen compliance telemetry.",
                "status_quo": "Gradual readiness.",
                "disruption": "Tighter EU enforcement.",
            },
        }
    )


def _prepare_leased_foresight(repository: ScanRepository, *, now: datetime):
    ingress = prepare_scan_ingress(
        company_name="Nordic Pulse Oy",
        company_url="https://nordicpulse.example",
        report_text="Nordic Pulse Oy provides industrial APIs and cloud telemetry.",
        save_to_database=False,
        uploads=[{"filename": "brief.txt", "content": b"OpenAPI telemetry architecture"}],
        idempotency_key=f"foresight-parity-pg-{uuid4()}",
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
        lease_owner="pg-foresight", lease_seconds=30, now=now + timedelta(seconds=8)
    )
    assert fs_lease is not None
    return ingress, fs_lease


def test_postgres_cognitive_parity_foresight_binds_kb_and_hydrates_synthesis_and_guards_tamper():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
    ingress, fs_lease = _prepare_leased_foresight(repository, now=now)
    kb_by_theme = _kb_signals()
    expected_binding = resolve_central_kb_binding(
        domain="Technology & Software", kb_signals_by_theme=kb_by_theme
    )

    executor = ForesightStageExecutor.with_cognitive_parity(
        repository,
        domain="Technology & Software",
        kb_loader=lambda _themes: kb_by_theme,
        openai_caller=_deterministic_foresight_openai,
        report_date=date(2026, 11, 13),
    )
    prepared = executor.execute(fs_lease)

    # Prepare-only invariant: no Foresight checkpoint row before commit.
    assert repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="foresight", attempt=1
    ) is None

    committed = commit_foresight_stage(
        repository, fs_lease, prepared, now=now + timedelta(seconds=9)
    )
    reloaded = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="foresight", attempt=1
    )
    assert reloaded == committed
    assert reloaded is not None
    assert reloaded.snapshot["model_policy_version"] == MODEL_POLICY_VERSION
    assert reloaded.snapshot["kb_version"] == expected_binding.kb_version
    assert reloaded.snapshot["kb_registry_hash"] == expected_binding.kb_registry_hash

    # Replay across an upgraded central KB signal store is rejected without mutating DB state.
    upgraded_kb = _kb_signals()
    upgraded_kb["Business"][0]["description"] = "Updated central KB procurement force."
    upgraded_prepared = ForesightStageExecutor.with_cognitive_parity(
        repository,
        domain="Technology & Software",
        kb_loader=lambda _themes: upgraded_kb,
        openai_caller=_deterministic_foresight_openai,
        report_date=date(2026, 11, 13),
    ).execute(fs_lease)
    with pytest.raises(ValueError, match="different Foresight checkpoint"):
        commit_foresight_stage(
            repository, fs_lease, upgraded_prepared, now=now + timedelta(seconds=10)
        )

    # Downstream Strategic Synthesis binds and projects sanitized Foresight output cleanly.
    synth_snapshot = build_synthesis_input_snapshot(
        run_id=ingress.run_id, attempt=1, foresight_checkpoint=reloaded
    )
    repository.persist_synthesis_input_snapshot(snapshot=synth_snapshot)
    verified_synth_snapshot, hydrated_fs_cp = repository.load_verified_synthesis_inputs(
        run_id=ingress.run_id, attempt=1
    )
    synth_projection = build_synthesis_input_projection(
        verified_synth_snapshot, foresight_checkpoint=hydrated_fs_cp
    )
    force = synth_projection.foresight_result["dimension_forces"][0]
    assert force["_from_kb"] is True
    assert "must" not in force["impact_on_company"].lower()
    assert "will fall behind" not in force["proposal_angle"].lower()

    # Mutating kb_registry_hash inside scan_checkpoints.snapshot fails closed on reload & Synthesis hydration.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{kb_registry_hash}', %s::jsonb)
            WHERE run_id = %s AND stage_name = 'foresight' AND attempt = 1
            """,
            (json.dumps("0" * 64), ingress.run_id),
        )

    with pytest.raises(CheckpointIntegrityError, match="output_hash mismatch"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="foresight", attempt=1
        )
    with pytest.raises(
        SynthesisInputSnapshotIntegrityError,
        match="does not match the verified durable inputs",
    ):
        repository.load_verified_synthesis_inputs(run_id=ingress.run_id, attempt=1)
