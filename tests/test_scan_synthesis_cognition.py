"""Hermetic contracts for Card 3L-Synthesis-parity Strategic Synthesis cognition and model-policy binding."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from model_policy import (  # noqa: E402
    CLAUDE_SONNET_MODEL,
    GPT_PRO_MODEL,
    GPT_PRO_REASONING_MODE,
    MODEL_POLICY_VERSION,
)
from scan_checkpoint_integrity import validate_checkpoint_integrity  # noqa: E402
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
from scan_synthesis_adapter import SynthesisStageExecutor  # noqa: E402
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
)
from scan_synthesis_cognition import (  # noqa: E402
    STRATEGIC_SYNTHESIS_JSON_SCHEMA,
    SynthesisCognitiveUnavailableError,
    analyze_synthesis_projection_with_cognition,
    normalize_synthesis_payload,
    sanitize_synthesis_prose,
)
from scan_synthesis_commit import commit_synthesis_stage  # noqa: E402
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scanner_one import analyze_intent  # noqa: E402


def _build_upstream_chain(run_id: str = "RUN-3LS-001"):
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text=(
            "Nordic Telemetry Oy builds industrial telemetry APIs, automated delivery, "
            "and governed cloud data pipelines."
        ),
        uploads=[],
    ).materials
    intent_snapshot = build_intent_input_snapshot(
        run_id=run_id, attempt=1, materials=materials, retrievals=[]
    )
    intent_projection = build_intent_input_projection(
        intent_snapshot, materials=materials, retrievals=[]
    )
    intent_cp = build_intent_checkpoint(
        intent_projection, intent_result=analyze_intent(intent_projection.concatenated_text)
    )

    candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://nordictelemetry.example/developers/api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text="Nordic Telemetry Oy operates an OpenAPI telemetry platform.",
        metadata={"discovery_label": "API Portal", "source_type": "technical_artifact"},
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
                            "supported_claim": "Nordic Telemetry Oy operates an OpenAPI telemetry platform.",
                            "found": True,
                            "sentiment": "positive",
                            "source_quote": "Nordic Telemetry Oy operates an OpenAPI telemetry platform.",
                            "title": "API Portal",
                            "url": "https://nordictelemetry.example/developers/api",
                            "is_recent": True,
                            "source_type": "technical_artifact",
                            "evidence_package_id": "PKG-arch01",
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
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 80,
                    "reality_score_adjustment": 5,
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

    diag_snapshot = build_diagnosis_input_snapshot(
        run_id=run_id, attempt=1, jury_checkpoint=jury_cp
    )
    diag_projection = build_diagnosis_input_projection(
        diag_snapshot, jury_checkpoint=jury_cp
    )
    diag_cp = build_diagnosis_checkpoint(
        diag_projection,
        diagnosis_result={
            "diagnostic_summary": "Nordic Telemetry Oy operates modern API tooling with limited external audits.",
            "evidence_based_risks": ["Independent AI governance documentation is missing."],
            "reality_score": 62,
            "archetype": "Modernizing",
        },
    )

    fs_snapshot = build_foresight_input_snapshot(
        run_id=run_id, attempt=1, diagnosis_checkpoint=diag_cp
    )
    fs_projection = build_foresight_input_projection(
        fs_snapshot, diagnosis_checkpoint=diag_cp
    )
    fs_cp = build_foresight_checkpoint(
        fs_projection,
        foresight_result={
            "dimension_forces": [
                {
                    "trend": "EU AI Governance Enforcement",
                    "type": "Probable/Emerging",
                    "timeframe": "12-24mo",
                    "kb_dimension": "AI Readiness",
                    "fp_theme": "Artificial Intelligence",
                    "impact_on_company": "Nordic Telemetry Oy should evaluate audit controls.",
                    "source_url": "https://analyst.example.org/ai-governance",
                    "source_type": "industry_analyst",
                    "confidence": "high",
                    "validated": True,
                    "url_verified": True,
                    "source_readable": True,
                    "trend_supported": True,
                    "theme_match": True,
                    "foresight_context_eligible": True,
                    "foresight_signal_status": "active_context",
                    "source_audit_status": "accepted_active",
                    "_from_kb": True,
                    "claim_type": "foresight_context",
                    "company_specific": False,
                    "not_evidence_of_deployment": True,
                    "requires_company_specific_corroboration": True,
                    "_kb_description": "EU AI transparency rules apply to high-risk enterprise tooling.",
                }
            ],
            "cross_impact_chain": "AI governance -> Operating Model",
            "scenarios": {
                "acceleration": "Faster compliance telemetry adoption.",
                "status_quo": "Gradual readiness.",
                "disruption": "Stricter enforcement timelines.",
            },
            "climate_exposure": {},
        },
    )
    return intent_cp, jury_cp, diag_cp, fs_cp


class _SynthesisParityLoader:
    def __init__(self, *, run_id: str, company_name: str, intent_cp, jury_cp, diag_cp, fs_cp):
        self.run_id = run_id
        self.company_name = company_name
        self.intent_cp = intent_cp
        self.jury_cp = jury_cp
        self.diag_cp = diag_cp
        self.fs_cp = fs_cp
        self.snapshot = build_synthesis_input_snapshot(
            run_id=run_id, attempt=1, foresight_checkpoint=fs_cp
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
            current_stage="strategic_synthesis",
            idempotency_key="idemp-3ls",
            request_hash="a" * 64,
            save_to_database=False,
            final_artifact_ref=None,
        )

    def load_checkpoint(self, *, run_id: str, stage_name: str, attempt: int) -> ScanCheckpoint | None:
        if (run_id, attempt) != (self.run_id, 1):
            return None
        return {
            "intent": self.intent_cp,
            "jury_analysis": self.jury_cp,
            "diagnosis": self.diag_cp,
            "foresight": self.fs_cp,
        }.get(stage_name)

    def load_verified_synthesis_inputs(self, *, run_id: str, attempt: int):
        assert (run_id, attempt) == (self.run_id, 1)
        return self.snapshot, self.fs_cp


def _lease(run_id: str = "RUN-3LS-001") -> StepLease:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    return StepLease(
        run_id=run_id,
        stage_name="strategic_synthesis",
        attempt=1,
        lease_owner="synthesis-parity-worker",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=60),
    )


def test_synthesis_cognitive_parity_sanitizes_prose_uses_pro_policy_and_binds_version():
    run_id = "RUN-3LS-PARITY"
    intent_cp, jury_cp, diag_cp, fs_cp = _build_upstream_chain(run_id)
    loader = _SynthesisParityLoader(
        run_id=run_id,
        company_name="Nordic Telemetry Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
        fs_cp=fs_cp,
    )

    recorded_calls: list[dict] = []

    def fake_openai(prompt: str, model: str = GPT_PRO_MODEL, **kwargs):
        recorded_calls.append({"prompt": prompt, "model": model, **kwargs})
        assert kwargs.get("web_search") is False
        assert model == GPT_PRO_MODEL
        assert kwargs.get("reasoning_mode") == GPT_PRO_REASONING_MODE
        assert kwargs.get("reasoning_effort") == "high"
        assert kwargs.get("max_output_tokens") == 16000
        assert kwargs.get("json_schema") == STRATEGIC_SYNTHESIS_JSON_SCHEMA
        assert "Nordic Telemetry Oy" in prompt
        assert "OpenAPI telemetry platform" in prompt
        assert "EU AI Governance Enforcement" in prompt
        assert "Independent AI governance documentation is missing." in prompt

        return json.dumps(
            {
                "strategic_synthesis": (
                    "[C] Nordic Telemetry Oy operates an OpenAPI telemetry platform (TAC-104) "
                    "consistent with a Modernizing position.\n\n"
                    "The absence of public evidence proves that independent AI governance controls "
                    "are missing across units (DIM-02).\n\n"
                    "EU AI Governance Enforcement must force immediate compliance by Q1 2025.\n\n"
                    "Financial capacity across 12-24mo and 24-36mo horizons remains bounded by "
                    "invalidation checks."
                )
            }
        )

    executor = SynthesisStageExecutor.with_cognitive_parity(
        loader,
        domain="Technology & Software",
        openai_caller=fake_openai,
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Anthropic fallback should not run when Pro primary succeeds")
        ),
        report_date=date(2026, 11, 13),
    )
    result = executor.execute(_lease(run_id))

    assert len(recorded_calls) == 1
    assert result.gate_result["status"] == "passed"
    assert result.next_stage == "verification"
    assert result.checkpoint is not None
    validate_checkpoint_integrity(result.checkpoint, require_passed=True)
    assert result.checkpoint.snapshot["model_policy_version"] == MODEL_POLICY_VERSION

    synthesis_text = result.checkpoint.snapshot["synthesis_result"]["strategic_synthesis"]
    assert "Modernizing" not in synthesis_text
    assert "TAC-104" not in synthesis_text
    assert "DIM-02" not in synthesis_text
    assert "[C]" not in synthesis_text
    assert "must" not in synthesis_text.lower()
    assert "the unresolved question is whether independent AI governance controls are missing" in synthesis_text
    assert sanitize_synthesis_prose(synthesis_text, report_date=date(2026, 11, 13)) == synthesis_text


def test_synthesis_sonnet_fallback_unavailable_and_malformed_validation():
    run_id = "RUN-3LS-FALLBACK"
    intent_cp, jury_cp, diag_cp, fs_cp = _build_upstream_chain(run_id)
    snapshot = build_synthesis_input_snapshot(
        run_id=run_id, attempt=1, foresight_checkpoint=fs_cp
    )
    projection = build_synthesis_input_projection(snapshot, foresight_checkpoint=fs_cp)

    # 1. Primary GPT Pro fails -> Sonnet high-effort fallback succeeds.
    sonnet_calls = 0

    def sonnet_ok(prompt: str, model: str = CLAUDE_SONNET_MODEL, **kwargs):
        nonlocal sonnet_calls
        sonnet_calls += 1
        assert kwargs.get("web_search") is False
        assert model == CLAUDE_SONNET_MODEL
        assert kwargs.get("reasoning_effort") == "high"
        assert kwargs.get("json_schema") == STRATEGIC_SYNTHESIS_JSON_SCHEMA
        return "```json\n" + json.dumps(
            {
                "strategic_synthesis": (
                    "Paragraph 1: adaptation thesis.\n\n"
                    "Paragraph 2: strategic tension.\n\n"
                    "Paragraph 3: external pressure links.\n\n"
                    "Paragraph 4: financial capacity and horizons."
                )
            }
        ) + "\n```"

    res_fallback = analyze_synthesis_projection_with_cognition(
        projection,
        company_name="Nordic Telemetry Oy",
        domain="Technology & Software",
        diagnosis_result=diag_cp.snapshot["diagnosis_result"],
        jury_result=jury_cp.snapshot["jury_result"],
        intent_result=intent_cp.snapshot["intent_result"],
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt pro timeout")),
        anthropic_caller=sonnet_ok,
    )
    assert sonnet_calls == 1
    assert "adaptation thesis" in res_fallback["strategic_synthesis"]

    # 2. Total provider unavailability fails closed explicitly (Rule #6).
    with pytest.raises(SynthesisCognitiveUnavailableError, match="unavailable"):
        analyze_synthesis_projection_with_cognition(
            projection,
            company_name="Nordic Telemetry Oy",
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt down")),
            anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("sonnet down")),
        )

    # 3. Empty/whitespace strategic_synthesis from provider triggers validation_failed -> next_stage=None.
    loader = _SynthesisParityLoader(
        run_id=run_id,
        company_name="Nordic Telemetry Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
        fs_cp=fs_cp,
    )
    assert "malformed_synthesis_payload" in normalize_synthesis_payload({"strategic_synthesis": "   "})
    bad_exec = SynthesisStageExecutor.with_cognitive_parity(
        loader,
        openai_caller=lambda *_a, **_kw: json.dumps({"strategic_synthesis": "   "}),
    )
    bad_res = bad_exec.execute(_lease(run_id))
    assert bad_res.gate_result["status"] == "validation_failed"
    assert bad_res.next_stage is None
    assert bad_res.checkpoint is not None
    assert "synthesis_result" not in bad_res.checkpoint.snapshot


def test_synthesis_model_policy_version_binding_changes_hashes_and_blocks_stale_replay():
    run_id = "RUN-3LS-POLICY"
    intent_cp, jury_cp, diag_cp, fs_cp = _build_upstream_chain(run_id)
    loader = _SynthesisParityLoader(
        run_id=run_id,
        company_name="Policy Binding Synthesis Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
        fs_cp=fs_cp,
    )
    res_v5 = SynthesisStageExecutor(
        loader, model_policy_version="model-policy-v5-sonnet55"
    ).execute(_lease(run_id))
    res_v6 = SynthesisStageExecutor(
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

    with pytest.raises(ValueError, match="different Synthesis checkpoint"):
        commit_synthesis_stage(_ExistingRepo(), _lease(run_id), res_v6)

    projection = build_synthesis_input_projection(
        loader.snapshot, foresight_checkpoint=fs_cp
    )
    with pytest.raises(ValueError, match="model_policy_version"):
        build_synthesis_checkpoint(
            projection,
            synthesis_result=res_v5.checkpoint.snapshot["synthesis_result"],
            model_policy_version="",
        )

    stripped_snapshot = dict(res_v5.checkpoint.snapshot)
    del stripped_snapshot["model_policy_version"]
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(
            replace(res_v5.checkpoint, snapshot=stripped_snapshot),
            require_passed=True,
        )


def test_synthesis_cognition_module_has_no_web_acquisition_live_scan_or_blob_coupling():
    source = (_BACKEND / "scan_synthesis_cognition.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "foresight_harvester" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "BlobStorage" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "web_search=True" not in source
    assert "web_search=False" in source
