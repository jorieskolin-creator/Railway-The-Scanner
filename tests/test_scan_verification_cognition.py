"""Hermetic contracts for Card 3L-Verification-parity no-web verification and model-policy binding."""

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
    CLAUDE_SONNET_MODEL,
    GPT_VERIFIER_MODEL,
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
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
)
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scan_verification_adapter import VerificationStageExecutor  # noqa: E402
from scan_verification_checkpoint import (  # noqa: E402
    build_verification_checkpoint,
    build_verification_input_projection,
)
from scan_verification_cognition import (  # noqa: E402
    VerificationCognitiveUnavailableError,
    analyze_verification_projection_with_cognition,
    parse_verification_record,
)
from scan_verification_commit import commit_verification_stage  # noqa: E402
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402
from scanner_one import analyze_intent  # noqa: E402


def _build_upstream_chain(run_id: str = "RUN-3LV-001"):
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
            "diagnostic_summary": "Nordic Telemetry Oy operates an OpenAPI telemetry platform.",
            "evidence_based_risks": ["Independent AI governance documentation is not publicly verified."],
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
                    "impact_on_company": "Nordic Telemetry Oy could evaluate audit controls.",
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
                "acceleration": "Evaluate compliance telemetry.",
                "status_quo": "Gradual readiness.",
                "disruption": "Stricter enforcement timelines.",
            },
            "climate_exposure": {},
        },
    )

    synth_snapshot = build_synthesis_input_snapshot(
        run_id=run_id, attempt=1, foresight_checkpoint=fs_cp
    )
    synth_projection = build_synthesis_input_projection(
        synth_snapshot, foresight_checkpoint=fs_cp
    )
    synth_cp = build_synthesis_checkpoint(
        synth_projection,
        synthesis_result={
            "strategic_synthesis": (
                "Nordic Telemetry Oy anchors its telemetry platform on OpenAPI capabilities "
                "while evaluating EU AI governance readiness over 12-24mo horizons."
            )
        },
    )
    return intent_cp, jury_cp, diag_cp, fs_cp, synth_cp


class _VerificationParityLoader:
    def __init__(self, *, run_id: str, company_name: str, intent_cp, jury_cp, diag_cp, fs_cp, synth_cp):
        self.run_id = run_id
        self.company_name = company_name
        self.intent_cp = intent_cp
        self.jury_cp = jury_cp
        self.diag_cp = diag_cp
        self.fs_cp = fs_cp
        self.synth_cp = synth_cp
        self.snapshot = build_verification_input_snapshot(
            run_id=run_id, attempt=1, synthesis_checkpoint=synth_cp
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
            current_stage="verification",
            idempotency_key="idemp-3lv",
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
            "strategic_synthesis": self.synth_cp,
        }.get(stage_name)

    def load_verified_verification_inputs(self, *, run_id: str, attempt: int):
        assert (run_id, attempt) == (self.run_id, 1)
        return self.snapshot, self.synth_cp


def _lease(run_id: str = "RUN-3LV-001") -> StepLease:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    return StepLease(
        run_id=run_id,
        stage_name="verification",
        attempt=1,
        lease_owner="verification-parity-worker",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=60),
    )


def test_verification_cognitive_parity_verifies_clean_and_flags_false_claims_without_successor():
    run_id = "RUN-3LV-PARITY"
    intent_cp, jury_cp, diag_cp, fs_cp, synth_cp = _build_upstream_chain(run_id)
    loader = _VerificationParityLoader(
        run_id=run_id,
        company_name="Nordic Telemetry Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
        fs_cp=fs_cp,
        synth_cp=synth_cp,
    )

    recorded_calls: list[dict] = []

    def fake_openai_verified(prompt: str, model: str = GPT_VERIFIER_MODEL, **kwargs):
        recorded_calls.append({"prompt": prompt, "model": model, **kwargs})
        assert kwargs.get("web_search") is False
        assert model == GPT_VERIFIER_MODEL
        assert "Nordic Telemetry Oy" in prompt
        assert "OpenAPI telemetry platform" in prompt
        return json.dumps({"summary_accurate": True, "false_claims": []})

    executor = VerificationStageExecutor.with_cognitive_parity(
        loader,
        domain="Technology & Software",
        openai_caller=fake_openai_verified,
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Anthropic fallback should not run when GPT verifier succeeds")
        ),
    )
    result = executor.execute(_lease(run_id))

    assert len(recorded_calls) == 1
    assert result.gate_result["status"] == "passed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    validate_checkpoint_integrity(result.checkpoint, require_passed=True)
    assert result.checkpoint.snapshot["model_policy_version"] == MODEL_POLICY_VERSION

    parsed_record = parse_verification_record(
        result.checkpoint.snapshot["verification_result"]["verification_record"]
    )
    assert parsed_record is not None
    assert parsed_record["verified"] is True
    assert parsed_record["verification_status"] == "verified"
    assert parsed_record["fact_check_model"] == GPT_VERIFIER_MODEL
    assert parsed_record["false_claims"] == []

    # Now exercise a blocking false-claim detection -> verification_status="failed", verified=False.
    def fake_openai_false_claim(prompt: str, model: str = GPT_VERIFIER_MODEL, **kwargs):
        assert kwargs.get("web_search") is False
        return json.dumps(
            {
                "summary_accurate": False,
                "false_claims": [
                    {
                        "section": "Strategic Synthesis",
                        "claim": "Nordic Telemetry Oy serves 50,000 enterprise customers.",
                        "claim_class": "unsupported_numeric_claim",
                        "error_type": "Unsupported Fact",
                        "support_level": "unsupported",
                        "missing_or_contradicted_premise": "No customer count exists in evidence.",
                        "reasoning": "Claimed customer count is absent from verified packets.",
                        "correction": "Remove the unsupported customer count.",
                    }
                ],
            }
        )

    flagged_result = VerificationStageExecutor.with_cognitive_parity(
        loader,
        domain="Technology & Software",
        openai_caller=fake_openai_false_claim,
    ).execute(_lease(run_id))

    assert flagged_result.next_stage is None
    assert flagged_result.checkpoint is not None
    validate_checkpoint_integrity(flagged_result.checkpoint, require_passed=True)
    flagged_record = parse_verification_record(
        flagged_result.checkpoint.snapshot["verification_result"]["verification_record"]
    )
    assert flagged_record is not None
    assert flagged_record["verified"] is False
    assert flagged_record["verification_status"] == "failed"
    assert flagged_record["false_claims"] == [
        "Nordic Telemetry Oy serves 50,000 enterprise customers."
    ]


def test_verification_sonnet_fallback_and_unavailable_fail_closed():
    run_id = "RUN-3LV-FALLBACK"
    intent_cp, jury_cp, diag_cp, fs_cp, synth_cp = _build_upstream_chain(run_id)
    snapshot = build_verification_input_snapshot(
        run_id=run_id, attempt=1, synthesis_checkpoint=synth_cp
    )
    projection = build_verification_input_projection(
        snapshot, synthesis_checkpoint=synth_cp
    )

    sonnet_calls = 0

    def sonnet_ok(prompt: str, model: str = CLAUDE_SONNET_MODEL, **kwargs):
        nonlocal sonnet_calls
        sonnet_calls += 1
        assert kwargs.get("web_search") is False
        assert model == CLAUDE_SONNET_MODEL
        return json.dumps({"summary_accurate": True, "false_claims": []})

    res_fallback = analyze_verification_projection_with_cognition(
        projection,
        company_name="Nordic Telemetry Oy",
        domain="Technology & Software",
        diagnosis_result=diag_cp.snapshot["diagnosis_result"],
        foresight_result=fs_cp.snapshot["foresight_result"],
        jury_result=jury_cp.snapshot["jury_result"],
        intent_result=intent_cp.snapshot["intent_result"],
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt verifier timeout")),
        anthropic_caller=sonnet_ok,
    )
    assert sonnet_calls == 1
    parsed = parse_verification_record(res_fallback["verification_record"])
    assert parsed is not None
    assert parsed["verified"] is True
    assert parsed["fact_check_model"] == CLAUDE_SONNET_MODEL

    # Reliability Plan Rule #6: total provider failure raises VerificationCognitiveUnavailableError.
    with pytest.raises(VerificationCognitiveUnavailableError, match="unavailable"):
        analyze_verification_projection_with_cognition(
            projection,
            company_name="Nordic Telemetry Oy",
            diagnosis_result=diag_cp.snapshot["diagnosis_result"],
            foresight_result=fs_cp.snapshot["foresight_result"],
            jury_result=jury_cp.snapshot["jury_result"],
            intent_result=intent_cp.snapshot["intent_result"],
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt down")),
            anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("sonnet down")),
        )


def test_verification_model_policy_version_binding_changes_hashes_and_blocks_stale_replay():
    run_id = "RUN-3LV-POLICY"
    intent_cp, jury_cp, diag_cp, fs_cp, synth_cp = _build_upstream_chain(run_id)
    loader = _VerificationParityLoader(
        run_id=run_id,
        company_name="Policy Binding Verification Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
        fs_cp=fs_cp,
        synth_cp=synth_cp,
    )
    res_v5 = VerificationStageExecutor(
        loader, model_policy_version="model-policy-v5-sonnet55"
    ).execute(_lease(run_id))
    res_v6 = VerificationStageExecutor(
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

    with pytest.raises(ValueError, match="different Verification checkpoint"):
        commit_verification_stage(_ExistingRepo(), _lease(run_id), res_v6)

    projection = build_verification_input_projection(
        loader.snapshot, synthesis_checkpoint=synth_cp
    )
    with pytest.raises(ValueError, match="model_policy_version"):
        build_verification_checkpoint(
            projection,
            verification_result=res_v5.checkpoint.snapshot["verification_result"],
            model_policy_version="",
        )

    stripped_snapshot = dict(res_v5.checkpoint.snapshot)
    del stripped_snapshot["model_policy_version"]
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(
            replace(res_v5.checkpoint, snapshot=stripped_snapshot),
            require_passed=True,
        )


def test_verification_cognition_module_has_no_web_acquisition_live_scan_or_blob_coupling():
    source = (_BACKEND / "scan_verification_cognition.py").read_text(encoding="utf-8")

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
