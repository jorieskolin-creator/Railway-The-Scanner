"""Hermetic contracts for Card 3L-Diagnosis-parity AI Diagnosis cognition and model-policy binding."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from model_policy import (  # noqa: E402
    CLAUDE_PREMIUM_MODEL,
    CLAUDE_SONNET_MODEL,
    GPT_REASONING_MODEL,
    MODEL_POLICY_VERSION,
)
from scan_checkpoint_integrity import validate_checkpoint_integrity  # noqa: E402
from scan_diagnosis_adapter import DiagnosisStageExecutor  # noqa: E402
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
)
from scan_diagnosis_cognition import (  # noqa: E402
    DIAGNOSIS_JSON_SCHEMA,
    DiagnosisCognitiveUnavailableError,
    analyze_diagnosis_projection_with_cognition,
    derive_diagnosis_archetype,
    normalize_diagnosis_payload,
    sanitize_diagnosis_prose,
)
from scan_diagnosis_commit import commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_intent_checkpoint import (  # noqa: E402
    build_intent_checkpoint,
    build_intent_input_projection,
)
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import (  # noqa: E402
    JURY_CHECKPOINT_LANES,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ScanCheckpoint,
    ScanRun,
    StepLease,
)
from scanner_one import analyze_intent  # noqa: E402


def _build_upstream_intent_and_jury(
    run_id: str = "RUN-3LD-001",
) -> tuple[ScanCheckpoint, ScanCheckpoint]:
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text=(
            "Nordic Telemetry Oy empowers autonomous product teams, continuous delivery, "
            "psychological safety, cloud platform APIs, and customer value streams."
        ),
        uploads=[],
    ).materials
    intent_snapshot = build_intent_input_snapshot(
        run_id=run_id,
        attempt=1,
        materials=materials,
        retrievals=[],
    )
    intent_projection = build_intent_input_projection(
        intent_snapshot,
        materials=materials,
        retrievals=[],
    )
    intent_cp = build_intent_checkpoint(
        intent_projection,
        intent_result=analyze_intent(intent_projection.concatenated_text),
    )

    candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://nordictelemetry.example/developers/api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text=(
            "Nordic Telemetry Oy exposes an OpenAPI gateway and event-driven cloud platform."
        ),
        metadata={"discovery_label": "API Gateway", "source_type": "technical_artifact"},
    )
    jury_snapshot = build_jury_input_snapshot(
        run_id=run_id,
        attempt=1,
        intent_checkpoint=intent_cp,
        candidates=[candidate],
        retrievals=[],
    )
    jury_projection = build_jury_input_projection(
        jury_snapshot,
        intent_checkpoint=intent_cp,
        candidates=[candidate],
        retrievals=[],
    )
    lanes = []
    for name in JURY_CHECKPOINT_LANES:
        if name == "ARCHITECTURAL JURY (Engineering Reality)":
            lanes.append(
                {
                    "jury": name,
                    "findings": [
                        {
                            "category": name,
                            "supported_claim": (
                                "Nordic Telemetry Oy exposes an OpenAPI gateway and "
                                "event-driven cloud platform."
                            ),
                            "found": True,
                            "sentiment": "positive",
                            "source_quote": (
                                "Nordic Telemetry Oy exposes an OpenAPI gateway and "
                                "event-driven cloud platform."
                            ),
                            "title": "API Gateway",
                            "url": "https://nordictelemetry.example/developers/api",
                            "is_recent": True,
                            "source_type": "technical_artifact",
                            "evidence_package_id": "PKG-arch0001",
                            "proposition_ids": ["PROP-1"],
                            "support_passage_ids": ["PASS-1"],
                            "subdimensions": ["architecture"],
                            "entity_scope": "company",
                            "source_verdict": "accepted",
                            "support_level": "exact",
                            "confidence": 82,
                            "source_id": candidate.candidate_id,
                        }
                    ],
                    "rejected_evidence": [],
                    "contradictions": ["Self-reported cloud scale contrasts with single regional gateway"],
                    "missing_coverage": ["No independent uptime audit"],
                    "confidence_score": 80,
                    "reality_score_adjustment": 6,
                }
            )
        else:
            lanes.append(
                {
                    "jury": name,
                    "findings": [],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 0,
                    "reality_score_adjustment": 0,
                }
            )
    jury_cp = build_jury_checkpoint(jury_projection, jury_result={"juries": lanes})
    return intent_cp, jury_cp


class _DiagnosisParityLoader:
    def __init__(self, *, run_id: str, company_name: str, intent_cp: ScanCheckpoint, jury_cp: ScanCheckpoint):
        self.run_id = run_id
        self.company_name = company_name
        self.intent_cp = intent_cp
        self.jury_cp = jury_cp
        self.snapshot = build_diagnosis_input_snapshot(
            run_id=run_id,
            attempt=1,
            jury_checkpoint=jury_cp,
        )

    def load_run(self, *, run_id: str) -> ScanRun | None:
        if run_id != self.run_id:
            return None
        return ScanRun(
            run_id=self.run_id,
            company_name=self.company_name,
            company_url="https://nordictelemetry.example",
            cognitive_contract_version="scanner-cognitive/v1",
            status="running",
            current_stage="diagnosis",
            idempotency_key="idemp-3ld",
            request_hash="a" * 64,
            save_to_database=False,
            final_artifact_ref=None,
        )

    def load_checkpoint(self, *, run_id: str, stage_name: str, attempt: int) -> ScanCheckpoint | None:
        if (run_id, attempt) != (self.run_id, 1):
            return None
        if stage_name == "jury_analysis":
            return self.jury_cp
        if stage_name == "intent":
            return self.intent_cp
        return None

    def load_verified_diagnosis_inputs(self, *, run_id: str, attempt: int):
        assert (run_id, attempt) == (self.run_id, 1)
        return self.snapshot, self.jury_cp


def _lease(run_id: str = "RUN-3LD-001") -> StepLease:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    return StepLease(
        run_id=run_id,
        stage_name="diagnosis",
        attempt=1,
        lease_owner="diagnosis-parity-worker",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=60),
    )


def test_diagnosis_cognitive_parity_sanitizes_prose_derives_mechanical_archetype_and_binds_policy():
    run_id = "RUN-3LD-PARITY"
    intent_cp, jury_cp = _build_upstream_intent_and_jury(run_id)
    loader = _DiagnosisParityLoader(
        run_id=run_id,
        company_name="Nordic Telemetry Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
    )
    intent_score = intent_cp.snapshot["intent_result"]["score"]
    assert 0 < intent_score < 50

    recorded_calls: list[dict] = []

    def fake_anthropic(prompt: str, model: str = CLAUDE_PREMIUM_MODEL, **kwargs):
        recorded_calls.append({"prompt": prompt, "model": model, **kwargs})
        assert kwargs.get("web_search") is False
        assert kwargs.get("reasoning_effort") == "high"
        assert kwargs.get("max_tokens") == 8000
        assert kwargs.get("json_schema") == DIAGNOSIS_JSON_SCHEMA
        assert "Nordic Telemetry Oy" in prompt
        assert "OpenAPI gateway" in prompt
        assert "Self-reported cloud scale contrasts" in prompt
        assert f'"score": {intent_score}' in prompt

        return json.dumps(
            {
                "diagnostic_summary": (
                    "[C] Nordic Telemetry Oy states it exposes an OpenAPI gateway (TAC-104) "
                    "and operates like a Cash Cow in core telemetry.\n\n"
                    "However, the absence of public evidence proves that DevOps deployment "
                    "frequency is lagging across business units (DIM-02)."
                ),
                "evidence_based_risks": [
                    "[D] Lack of verified evidence demonstrates that multi-region failover is absent (TAC-205).",
                    "Single regional gateway creates concentration risk in Modernizing operations.",
                ],
                "reality_score": 64,
                # Deliberately drifting model archetype ("At Risk") while (64 >= 50, 38 < 50) dictates "Modernizing".
                "archetype": "At Risk",
            }
        )

    executor = DiagnosisStageExecutor.with_cognitive_parity(
        loader,
        anthropic_caller=fake_anthropic,
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("GPT fallback should not run when primary succeeds")
        ),
    )
    result = executor.execute(_lease(run_id))

    assert len(recorded_calls) == 1
    assert recorded_calls[0]["model"] == CLAUDE_PREMIUM_MODEL
    assert result.gate_result["status"] == "passed"
    assert result.next_stage == "foresight"
    assert result.checkpoint is not None
    validate_checkpoint_integrity(result.checkpoint, require_passed=True)
    assert result.checkpoint.snapshot["model_policy_version"] == MODEL_POLICY_VERSION

    diag_payload = result.checkpoint.snapshot["diagnosis_result"]
    # Mechanical archetype overrides drifting model label.
    assert diag_payload["reality_score"] == 64
    assert diag_payload["archetype"] == "Modernizing"
    # Archetype labels, TAC/DIM codes, [C]/[D] tier prefixes, and unsafe proof-gap phrasing are sanitized.
    summary = diag_payload["diagnostic_summary"]
    assert "Cash Cow" not in summary
    assert "TAC-104" not in summary
    assert "DIM-02" not in summary
    assert "[C]" not in summary
    assert "the unresolved question is whether DevOps deployment frequency is lagging" in summary
    # Idempotency check on second pass.
    assert sanitize_diagnosis_prose(summary) == summary

    risks = diag_payload["evidence_based_risks"]
    assert len(risks) == 2
    assert all("Modernizing" not in item and "TAC-205" not in item and "[D]" not in item for item in risks)
    assert "the unresolved question is whether multi-region failover is absent" in risks[0]
    assert all(sanitize_diagnosis_prose(item) == item for item in risks)


def test_provider_fallback_chain_and_unavailable_fail_closed():
    run_id = "RUN-3LD-FALLBACK"
    intent_cp, jury_cp = _build_upstream_intent_and_jury(run_id)
    snapshot = build_diagnosis_input_snapshot(
        run_id=run_id, attempt=1, jury_checkpoint=jury_cp
    )
    projection = build_diagnosis_input_projection(snapshot, jury_checkpoint=jury_cp)

    # 1. Primary Claude fails -> GPT frontier fallback succeeds.
    gpt_calls = 0

    def gpt_ok(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
        nonlocal gpt_calls
        gpt_calls += 1
        assert kwargs.get("web_search") is False
        assert model == GPT_REASONING_MODEL
        return {
            "diagnostic_summary": "Neutral assessment from GPT frontier fallback.\n\nSecond paragraph.",
            "evidence_based_risks": ["Single gateway dependency."],
            "reality_score": 55,
            "archetype": "Modernizing",
        }

    res_gpt = analyze_diagnosis_projection_with_cognition(
        projection,
        company_name="Nordic Telemetry Oy",
        financial_index=40,
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("claude timeout")),
        openai_caller=gpt_ok,
    )
    assert gpt_calls == 1
    assert res_gpt["reality_score"] == 55
    assert res_gpt["archetype"] == "Modernizing"

    # 2. Primary Claude AND GPT fail -> Conservative Sonnet retry succeeds.
    anthropic_attempts: list[str] = []

    def claude_fail_then_sonnet_recover(prompt: str, model: str = CLAUDE_SONNET_MODEL, **kwargs):
        assert kwargs.get("web_search") is False
        label = kwargs.get("log_label", "")
        anthropic_attempts.append(label)
        if label == "Scanner Diagnosis Premium":
            raise TimeoutError("premium timeout")
        assert label == "Scanner Diagnosis Sonnet Fallback"
        assert "CRITICAL: You are a CONSERVATIVE analyst." in prompt
        assert kwargs.get("reasoning_effort") == "medium"
        return "```json\n" + json.dumps(
            {
                "diagnostic_summary": "Conservative assessment.\n\nLimited public evidence constrains this assessment.",
                "evidence_based_risks": [],
                "reality_score": 45,
                "archetype": "Cash Cow",
            }
        ) + "\n```"

    res_sonnet = analyze_diagnosis_projection_with_cognition(
        projection,
        company_name="Nordic Telemetry Oy",
        financial_index=60,
        anthropic_caller=claude_fail_then_sonnet_recover,
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt timeout")),
    )
    assert anthropic_attempts == [
        "Scanner Diagnosis Premium",
        "Scanner Diagnosis Sonnet Fallback",
    ]
    assert res_sonnet["archetype"] == "Cash Cow"

    # 3. Reliability Plan Rule #6: all providers failing or returning "AI Summary Unavailable" raises explicitly.
    with pytest.raises(DiagnosisCognitiveUnavailableError, match="unavailable"):
        analyze_diagnosis_projection_with_cognition(
            projection,
            company_name="Nordic Telemetry Oy",
            anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("down")),
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("down")),
        )

    with pytest.raises(DiagnosisCognitiveUnavailableError, match="unavailable"):
        analyze_diagnosis_projection_with_cognition(
            projection,
            company_name="Nordic Telemetry Oy",
            anthropic_caller=lambda *_a, **_kw: {
                "diagnostic_summary": "AI Summary Unavailable",
                "evidence_based_risks": [],
                "reality_score": 50,
                "archetype": "Thriving",
            },
        )


def test_four_quadrant_mechanical_archetypes_and_malformed_validation_failure():
    assert derive_diagnosis_archetype(reality_score=50, financial_or_intent_score=50) == "Thriving"
    assert derive_diagnosis_archetype(reality_score=75, financial_or_intent_score=49) == "Modernizing"
    assert derive_diagnosis_archetype(reality_score=49, financial_or_intent_score=80) == "Cash Cow"
    assert derive_diagnosis_archetype(reality_score=20, financial_or_intent_score=20) == "At Risk"

    run_id = "RUN-3LD-MALFORMED"
    intent_cp, jury_cp = _build_upstream_intent_and_jury(run_id)
    loader = _DiagnosisParityLoader(
        run_id=run_id,
        company_name="Malformed Diagnosis Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
    )

    for bad_payload in (
        {"diagnostic_summary": "   ", "evidence_based_risks": [], "reality_score": 50, "archetype": "Thriving"},
        {"diagnostic_summary": "Valid text", "evidence_based_risks": "not-a-list", "reality_score": 50, "archetype": "Thriving"},
        {"diagnostic_summary": "Valid text", "evidence_based_risks": [], "reality_score": "fifty", "archetype": "Thriving"},
        {"diagnostic_summary": "Valid text", "evidence_based_risks": [], "reality_score": 120, "archetype": "Thriving"},
        {"diagnostic_summary": "Valid text", "evidence_based_risks": [], "reality_score": 50, "archetype": "InvalidArchetype"},
    ):
        assert "malformed_diagnosis_payload" in normalize_diagnosis_payload(bad_payload)
        bad_exec = DiagnosisStageExecutor.with_cognitive_parity(
            loader,
            anthropic_caller=lambda *_a, payload=bad_payload, **_kw: payload,
        )
        bad_res = bad_exec.execute(_lease(run_id))
        assert bad_res.gate_result["status"] == "validation_failed"
        assert bad_res.next_stage is None
        assert bad_res.checkpoint is not None
        assert "diagnosis_result" not in bad_res.checkpoint.snapshot


def test_diagnosis_model_policy_version_binding_changes_hashes_and_blocks_stale_replay():
    run_id = "RUN-3LD-POLICY"
    intent_cp, jury_cp = _build_upstream_intent_and_jury(run_id)
    loader = _DiagnosisParityLoader(
        run_id=run_id,
        company_name="Policy Binding Diagnosis Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
    )
    res_v5 = DiagnosisStageExecutor(
        loader, model_policy_version="model-policy-v5-sonnet55"
    ).execute(_lease(run_id))
    res_v6 = DiagnosisStageExecutor(
        loader, model_policy_version="model-policy-v6-future"
    ).execute(_lease(run_id))

    assert res_v5.checkpoint is not None and res_v6.checkpoint is not None
    assert res_v5.checkpoint.output_hash != res_v6.checkpoint.output_hash
    assert res_v5.checkpoint.checkpoint_id != res_v6.checkpoint.checkpoint_id
    validate_checkpoint_integrity(res_v5.checkpoint, require_passed=True)
    validate_checkpoint_integrity(res_v6.checkpoint, require_passed=True)

    class _ExistingRepo:
        def load_checkpoint(self, *, run_id, stage_name, attempt):
            return res_v5.checkpoint

        def complete_step_with_checkpoint(self, lease, *, checkpoint, next_stage, now=None):
            raise AssertionError("Should not complete when existing checkpoint mismatches")

    with pytest.raises(ValueError, match="different Diagnosis checkpoint"):
        commit_diagnosis_stage(_ExistingRepo(), _lease(run_id), res_v6)

    projection = build_diagnosis_input_projection(
        loader.snapshot, jury_checkpoint=jury_cp
    )
    with pytest.raises(ValueError, match="model_policy_version"):
        build_diagnosis_checkpoint(
            projection,
            diagnosis_result=res_v5.checkpoint.snapshot["diagnosis_result"],
            model_policy_version="",
        )

    stripped_snapshot = dict(res_v5.checkpoint.snapshot)
    del stripped_snapshot["model_policy_version"]
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(
            replace(res_v5.checkpoint, snapshot=stripped_snapshot),
            require_passed=True,
        )


def test_diagnosis_cognition_module_has_no_web_acquisition_live_scan_or_blob_coupling():
    source = (_BACKEND / "scan_diagnosis_cognition.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "generate_foresight" not in source
    assert "foresight_harvester" not in source
    assert "strategic_synthesis" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "BlobStorage" not in source
    assert "get_evolving_framework" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "web_search=True" not in source
    assert "web_search=False" in source
