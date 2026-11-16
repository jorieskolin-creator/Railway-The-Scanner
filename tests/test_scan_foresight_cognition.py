"""Hermetic contracts for Card 3L-Foresight-parity KB-first cognition and KB/registry/policy binding."""

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

import buying_potential  # noqa: E402
from buying_potential import _build_input_payload as build_buying_payload  # noqa: E402
from model_policy import (  # noqa: E402
    CLAUDE_SONNET_MODEL,
    GPT_REASONING_MODEL,
    MODEL_POLICY_VERSION,
)
from scan_checkpoint_integrity import validate_checkpoint_integrity  # noqa: E402
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
)
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_adapter import ForesightStageExecutor  # noqa: E402
from scan_foresight_checkpoint import (  # noqa: E402
    build_foresight_checkpoint,
    build_foresight_input_projection,
)
from scan_foresight_cognition import (  # noqa: E402
    ForesightCognitiveUnavailableError,
    analyze_foresight_projection_with_cognition,
    is_central_kb_signal_context_eligible,
    resolve_central_kb_binding,
)
from scan_foresight_commit import commit_foresight_stage  # noqa: E402
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
from scanner_one import analyze_intent  # noqa: E402
from strategic_foresight import extract_foresight_links  # noqa: E402


def _kb_signal(
    *,
    title: str,
    url: str,
    theme: str,
    description: str,
    confidence: str = "high",
    eligible: bool = True,
    company_specific: bool = False,
    audit_status: str = "accepted_active",
    signal_status: str = "active_context",
) -> dict:
    return {
        "title": title,
        "source_url": url,
        "fp_theme": theme,
        "_kb_theme": theme,
        "description": description,
        "signal_type": "Probable/Emerging",
        "timeframe": "12-24mo",
        "source_type": "industry_analyst",
        "source_credibility": "high",
        "confidence": confidence,
        "claim_type": "foresight_context",
        "company_specific": company_specific,
        "not_evidence_of_deployment": True,
        "requires_company_specific_corroboration": True,
        "modal_language_required": True,
        "foresight_context_eligible": eligible,
        "foresight_signal_status": signal_status,
        "source_audit_status": audit_status,
        "url_verified": True,
        "source_readable": True,
        "trend_supported": True,
        "theme_match": True,
        "trend_support_status": "supported",
        "foresight_evidence_version": "foresight-active-v3",
        "kb_dimensions_affected": ["AI Readiness", "Operating Model"],
        "industries_affected": ["Technology & Software"],
        "lifecycle_stage": "go_to_market",
        "trend_momentum": "accelerating",
        "supporting_sources": [
            {
                "url": f"{url}/corroboration",
                "source_type": "academic",
                "title": f"{title} Study",
            }
        ],
    }


def _sample_kb_by_theme() -> dict[str, list[dict]]:
    return {
        "Artificial Intelligence": [
            _kb_signal(
                title="Enterprise AI Governance Enforcement",
                url="https://analyst.example.org/ai-governance-2026",
                theme="Artificial Intelligence",
                description="EU AI Act transparency rules apply to high-risk enterprise tooling.",
            ),
            # Ineligible company-specific / rejected audit rows must be filtered out deterministically.
            _kb_signal(
                title="Contaminated Company Claim",
                url="https://bad.example.org/company-claim",
                theme="Artificial Intelligence",
                description="Company X deployed internal agents.",
                company_specific=True,
            ),
            _kb_signal(
                title="Rejected Dead Audit Signal",
                url="https://dead.example.org/404",
                theme="Artificial Intelligence",
                description="Dead signal",
                eligible=False,
                audit_status="source_dead",
                signal_status="rejected_audit",
            ),
        ],
        "Data & Digitalisation": [
            _kb_signal(
                title="Event-Driven Data Mesh Adoption",
                url="https://research.example.org/data-mesh-2026",
                theme="Data & Digitalisation",
                description="Industrial software vendors increasingly decouple telemetry APIs.",
                confidence="medium",
            ),
        ],
        "Business": [
            _kb_signal(
                title="Outcome-Based SaaS Procurement",
                url="https://market.example.org/saas-procurement-2026",
                theme="Business",
                description="Enterprise buyers increasingly evaluate verifiable delivery SLAs.",
                confidence="medium",
            ),
        ],
    }


def _build_upstream_chain(run_id: str = "RUN-3LF-001") -> tuple[ScanCheckpoint, ScanCheckpoint, ScanCheckpoint]:
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text=(
            "Nordic Cloud Oy builds industrial telemetry APIs, automated delivery, "
            "and governed machine learning pipelines."
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
        source_url="https://nordiccloud.example/developers/api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text="Nordic Cloud Oy operates an OpenAPI telemetry platform.",
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
                            "supported_claim": "Nordic Cloud Oy operates an OpenAPI telemetry platform.",
                            "found": True,
                            "sentiment": "positive",
                            "source_quote": "Nordic Cloud Oy operates an OpenAPI telemetry platform.",
                            "title": "API Portal",
                            "url": "https://nordiccloud.example/developers/api",
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
            "diagnostic_summary": "Nordic Cloud Oy operates modern API tooling with limited external audits.",
            "evidence_based_risks": ["Independent AI governance documentation is missing."],
            "reality_score": 62,
            "archetype": "Modernizing",
        },
    )
    return intent_cp, jury_cp, diag_cp


class _ForesightParityLoader:
    def __init__(self, *, run_id: str, company_name: str, intent_cp, jury_cp, diag_cp):
        self.run_id = run_id
        self.company_name = company_name
        self.intent_cp = intent_cp
        self.jury_cp = jury_cp
        self.diag_cp = diag_cp
        self.snapshot = build_foresight_input_snapshot(
            run_id=run_id, attempt=1, diagnosis_checkpoint=diag_cp
        )

    def load_run(self, *, run_id: str) -> ScanRun | None:
        if run_id != self.run_id:
            return None
        return ScanRun(
            run_id=self.run_id,
            company_name=self.company_name,
            company_url="https://nordiccloud.example",
            cognitive_contract_version="scanner-cognitive/v1",
            status="running",
            current_stage="foresight",
            idempotency_key="idemp-3lf",
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
        }.get(stage_name)

    def load_verified_foresight_inputs(self, *, run_id: str, attempt: int):
        assert (run_id, attempt) == (self.run_id, 1)
        return self.snapshot, self.diag_cp


def _lease(run_id: str = "RUN-3LF-001") -> StepLease:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    return StepLease(
        run_id=run_id,
        stage_name="foresight",
        attempt=1,
        lease_owner="foresight-parity-worker",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=60),
    )


def test_foresight_kb_parity_sanitizes_modality_binds_hashes_and_preserves_buying_compatibility(
    monkeypatch,
):
    monkeypatch.setattr(buying_potential, "_load_modernization_tactics", lambda: [])
    run_id = "RUN-3LF-PARITY"
    intent_cp, jury_cp, diag_cp = _build_upstream_chain(run_id)
    loader = _ForesightParityLoader(
        run_id=run_id,
        company_name="Nordic Cloud Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
    )
    kb_by_theme = _sample_kb_by_theme()
    expected_binding = resolve_central_kb_binding(
        domain="Technology & Software",
        kb_signals_by_theme=kb_by_theme,
    )

    recorded_calls: list[dict] = []

    def fake_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
        recorded_calls.append({"prompt": prompt, "model": model, **kwargs})
        assert kwargs.get("web_search") is False
        assert model == GPT_REASONING_MODEL
        assert "Nordic Cloud Oy" in prompt
        assert "Enterprise AI Governance Enforcement" in prompt
        assert "OpenAPI telemetry platform" in prompt
        assert "Contaminated Company Claim" not in prompt
        assert "Rejected Dead Audit Signal" not in prompt

        return json.dumps(
            {
                "dimension_forces": [
                    {
                        "trend": "Enterprise AI Governance Enforcement",
                        "type": "Probable/Emerging",
                        "timeframe": "12-24mo",
                        "kb_dimension": "AI Readiness",
                        "fp_theme": "Artificial Intelligence",
                        # Deliberately deterministic obligations to prove sanitizer rewrites them.
                        "impact_on_company": (
                            "Nordic Cloud Oy must comply and has deployed automated governance."
                        ),
                        "proposal_angle": "Nordic Cloud Oy will fall behind unless it acts by Q1 2025.",
                        "source_url": "https://analyst.example.org/ai-governance-2026",
                        "source_type": "industry_analyst",
                        "confidence": "high",
                        "lifecycle_stage": "go_to_market",
                        "trend_momentum": "accelerating",
                        "action_timing": "act_now",
                    },
                    {
                        "trend": "Event-Driven Data Mesh Adoption",
                        "type": "Probable/Emerging",
                        "timeframe": "12-24mo",
                        "kb_dimension": "Operating Model",
                        "fp_theme": "Data & Digitalisation",
                        "impact_on_company": "Nordic Cloud Oy forces teams to adopt streaming pipelines.",
                        "proposal_angle": "Evaluate streaming governance readiness.",
                        "source_url": "https://research.example.org/data-mesh-2026",
                        "source_type": "industry_analyst",
                        "confidence": "medium",
                    },
                    {
                        # Ungrounded fabricated URL must be dropped deterministically.
                        "trend": "Fabricated Hallucinated Force",
                        "type": "Probable/Emerging",
                        "timeframe": "12-24mo",
                        "kb_dimension": "Strategy",
                        "fp_theme": "Business",
                        "impact_on_company": "Hallucinated impact.",
                        "source_url": "https://invented-hallucination.example.net/fake",
                    },
                ],
                "cross_impact_chain": (
                    "AI governance pressure forces Nordic Cloud Oy to audit Operating Model pipelines."
                ),
                "scenarios": {
                    "acceleration": "Nordic Cloud Oy must expand governance controls.",
                    "status_quo": "Nordic Cloud Oy will fall behind if governance delays persist.",
                    "disruption": "A regulatory enforcement shift could disrupt buyers.",
                },
                "climate_exposure": {
                    "risk_level": "low",
                    "headline": "Compute workloads must improve efficiency.",
                },
            }
        )

    executor = ForesightStageExecutor.with_cognitive_parity(
        loader,
        domain="Technology & Software",
        kb_loader=lambda _themes: kb_by_theme,
        openai_caller=fake_openai,
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Anthropic fallback should not run when GPT primary succeeds")
        ),
        report_date=date(2026, 11, 13),
    )
    result = executor.execute(_lease(run_id))

    assert len(recorded_calls) == 1
    assert result.gate_result["status"] == "passed"
    assert result.next_stage == "strategic_synthesis"
    assert result.checkpoint is not None
    validate_checkpoint_integrity(result.checkpoint, require_passed=True)

    snapshot = result.checkpoint.snapshot
    assert snapshot["model_policy_version"] == MODEL_POLICY_VERSION
    assert snapshot["kb_version"] == expected_binding.kb_version
    assert snapshot["kb_registry_hash"] == expected_binding.kb_registry_hash
    snapshot_text = str(snapshot)
    assert "kb_versions" not in snapshot_text
    assert "Contaminated Company Claim" not in snapshot_text

    foresight_result = snapshot["foresight_result"]
    forces = foresight_result["dimension_forces"]
    assert len(forces) == 2
    assert all("invented-hallucination" not in str(f) for f in forces)
    first_force = forces[0]
    assert first_force["_from_kb"] is True
    assert first_force["claim_type"] == "foresight_context"
    assert first_force["company_specific"] is False
    assert first_force["not_evidence_of_deployment"] is True
    assert first_force["foresight_context_eligible"] is True
    assert "must" not in first_force["impact_on_company"].lower()
    assert "has deployed" not in first_force["impact_on_company"].lower()
    assert "will fall behind" not in first_force["proposal_angle"].lower()
    assert "must" not in foresight_result["scenarios"]["acceleration"].lower()
    assert "will fall behind" not in foresight_result["scenarios"]["status_quo"].lower()
    assert "must" not in foresight_result["climate_exposure"]["headline"].lower()

    # Card 2B compatibility: extract_foresight_links + Buying Insight read KB-first output cleanly.
    links = extract_foresight_links(
        {**foresight_result, "kb_first": True},
        company_name="Nordic Cloud Oy",
        domain="Technology & Software",
    )
    # 2 primary KB links + 2 corroborating supporting_sources links = 4 extracted links.
    assert len(links) == 4
    buying_payload = build_buying_payload(
        {
            "companyName": "Nordic Cloud Oy",
            "domain": "Technology & Software",
            "strategic_foresight": foresight_result,
            "foresight_links": links,
        },
        service_kb={
            "owner": "Vivicta",
            "services": [
                {
                    "name": "Industrial Data & AI Governance",
                    "category": "AI & Data Platform",
                    "buyer_personas": ["VP Engineering"],
                    "pain_triggers": ["AI governance"],
                    "evidence_patterns": ["telemetry"],
                    "proof_points": ["Delivered governance architecture."],
                    "safe_wording": ["Evaluate AI governance readiness."],
                    "do_not_claim": [],
                    "disqualifiers": [],
                }
            ],
        },
    )
    assert len(buying_payload["foresight_forces"]) == 2


def test_theme_fallback_anthropic_fallback_insufficient_kb_and_provider_unavailability():
    run_id = "RUN-3LF-FALLBACK"
    intent_cp, jury_cp, diag_cp = _build_upstream_chain(run_id)
    snapshot = build_foresight_input_snapshot(
        run_id=run_id, attempt=1, diagnosis_checkpoint=diag_cp
    )
    projection = build_foresight_input_projection(
        snapshot, diagnosis_checkpoint=diag_cp
    )

    # 1. Near-fit THEME_FALLBACK_MAP: "Data & Digitalisation" falls back to "Artificial Intelligence"
    # and "Services", and GPT timeout triggers CLAUDE_SONNET_MODEL no-web fallback.
    fallback_kb = {
        "Artificial Intelligence": [
            _kb_signal(
                title="AI Agent Standards",
                url="https://kb.example.org/ai-agents",
                theme="Artificial Intelligence",
                description="Interoperable agent standards are maturing.",
            ),
            _kb_signal(
                title="Model Audit Trails",
                url="https://kb.example.org/model-audits",
                theme="Artificial Intelligence",
                description="Enterprise regulators evaluate model lineage.",
            ),
        ],
        "Global Economy": [
            _kb_signal(
                title="Nordic Capex Moderation",
                url="https://kb.example.org/nordic-capex",
                theme="Global Economy",
                description="Corporate IT buyers prioritize measurable ROI.",
            ),
        ],
    }
    anthropic_calls = 0

    def anthropic_ok(prompt: str, model: str = CLAUDE_SONNET_MODEL, **kwargs):
        nonlocal anthropic_calls
        anthropic_calls += 1
        assert kwargs.get("web_search") is False
        assert model == CLAUDE_SONNET_MODEL
        return json.dumps(
            {
                "dimension_forces": [
                    {
                        "trend": "AI Agent Standards",
                        "type": "Probable/Emerging",
                        "timeframe": "12-24mo",
                        "kb_dimension": "AI Readiness",
                        "fp_theme": "Artificial Intelligence",
                        "impact_on_company": "Nordic Cloud Oy could evaluate standard interfaces.",
                        "source_url": "https://kb.example.org/ai-agents",
                    }
                ],
                "cross_impact_chain": "Standards -> APIs",
                "scenarios": {
                    "acceleration": "Faster partner adoption.",
                    "status_quo": "Gradual integration.",
                    "disruption": "Protocol fragmentation.",
                },
            }
        )

    compact_res, binding = analyze_foresight_projection_with_cognition(
        projection,
        company_name="Nordic Cloud Oy",
        domain="Other",
        kb_signals_by_theme=fallback_kb,
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt down")),
        anthropic_caller=anthropic_ok,
    )
    assert anthropic_calls == 1
    assert len(compact_res["dimension_forces"]) == 1
    assert len(binding.eligible_signals) == 3

    # 2. Insufficient (<3) eligible KB signals fails closed explicitly.
    with pytest.raises(ForesightCognitiveUnavailableError, match="Insufficient"):
        analyze_foresight_projection_with_cognition(
            projection,
            company_name="Nordic Cloud Oy",
            domain="Technology & Software",
            kb_signals=[
                _kb_signal(
                    title="Only One Signal",
                    url="https://kb.example.org/one",
                    theme="Artificial Intelligence",
                    description="Single signal.",
                )
            ],
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no call")),
        )

    # 3. Total provider unavailability fails closed explicitly (Rule #6).
    with pytest.raises(ForesightCognitiveUnavailableError, match="unavailable"):
        analyze_foresight_projection_with_cognition(
            projection,
            company_name="Nordic Cloud Oy",
            domain="Other",
            kb_signals_by_theme=fallback_kb,
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt down")),
            anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("sonnet down")),
        )

    # 4. Malformed model payload -> validation_failed and next_stage is None.
    loader = _ForesightParityLoader(
        run_id=run_id,
        company_name="Nordic Cloud Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
    )
    bad_exec = ForesightStageExecutor.with_cognitive_parity(
        loader,
        domain="Other",
        kb_signals_by_theme=fallback_kb,
        openai_caller=lambda *_a, **_kw: json.dumps(
            {
                "dimension_forces": [],
                "scenarios": {},
                "cross_impact_chain": "Malformed empty foresight forces and empty scenarios structure.",
            }
        ),
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Anthropic fallback should not run on parsed JSON object")
        ),
    )
    bad_res = bad_exec.execute(_lease(run_id))
    assert bad_res.gate_result["status"] == "validation_failed"
    assert bad_res.next_stage is None
    assert bad_res.checkpoint is not None
    assert "foresight_result" not in bad_res.checkpoint.snapshot


def test_kb_registry_and_policy_hashes_block_stale_replay_and_reload():
    run_id = "RUN-3LF-HASH-BIND"
    intent_cp, jury_cp, diag_cp = _build_upstream_chain(run_id)
    loader = _ForesightParityLoader(
        run_id=run_id,
        company_name="Nordic Cloud Oy",
        intent_cp=intent_cp,
        jury_cp=jury_cp,
        diag_cp=diag_cp,
    )
    kb_a = _sample_kb_by_theme()
    kb_b = _sample_kb_by_theme()
    kb_b["Business"][0]["description"] = "Updated central KB procurement signal text."

    binding_a = resolve_central_kb_binding(domain="Technology & Software", kb_signals_by_theme=kb_a)
    binding_b = resolve_central_kb_binding(domain="Technology & Software", kb_signals_by_theme=kb_b)
    binding_reg_changed = resolve_central_kb_binding(
        domain="Technology & Software",
        kb_signals_by_theme=kb_a,
        registry_override={"registry_version": "strategic-foresight-theme-registry-v2"},
    )
    assert binding_a.kb_registry_hash != binding_b.kb_registry_hash
    assert binding_a.kb_registry_hash != binding_reg_changed.kb_registry_hash
    assert binding_a.kb_version != binding_b.kb_version

    projection = build_foresight_input_projection(
        loader.snapshot, diagnosis_checkpoint=diag_cp
    )
    cp_a = build_foresight_checkpoint(
        projection,
        foresight_result={
            "dimension_forces": [],
            "cross_impact_chain": "",
            "scenarios": {},
            "climate_exposure": {},
        },
        model_policy_version="model-policy-v5-sonnet55",
        kb_version=binding_a.kb_version,
        kb_registry_hash=binding_a.kb_registry_hash,
    )
    cp_b = build_foresight_checkpoint(
        projection,
        foresight_result={
            "dimension_forces": [],
            "cross_impact_chain": "",
            "scenarios": {},
            "climate_exposure": {},
        },
        model_policy_version="model-policy-v5-sonnet55",
        kb_version=binding_b.kb_version,
        kb_registry_hash=binding_b.kb_registry_hash,
    )
    assert cp_a.output_hash != cp_b.output_hash
    assert cp_a.checkpoint_id != cp_b.checkpoint_id
    validate_checkpoint_integrity(cp_a, require_passed=True)
    validate_checkpoint_integrity(cp_b, require_passed=True)

    # commit_foresight_stage rejects replay when KB version/hash upgraded.
    class _ExistingRepo:
        def load_checkpoint(self, *, run_id, stage_name, attempt):
            return cp_a

        def complete_step_with_checkpoint(self, lease, *, checkpoint, next_stage, now=None):
            raise AssertionError("Should not complete on upgraded KB/policy replay")

    from scan_supervisor import StageExecutionResult  # noqa: E402

    with pytest.raises(ValueError, match="different Foresight checkpoint"):
        commit_foresight_stage(
            _ExistingRepo(),
            _lease(run_id),
            StageExecutionResult(
                output_hash=cp_b.output_hash,
                checkpoint_ref=cp_b.checkpoint_ref,
                gate_result=cp_b.gate_result,
                next_stage="strategic_synthesis",
                checkpoint=cp_b,
            ),
        )

    # Missing or malformed kb_registry_hash / kb_version / model_policy_version fails build & reload.
    with pytest.raises(ValueError, match="kb_registry_hash"):
        build_foresight_checkpoint(
            projection,
            foresight_result=cp_a.snapshot["foresight_result"],
            kb_registry_hash="not-a-sha256",
        )
    stripped = dict(cp_a.snapshot)
    del stripped["kb_registry_hash"]
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(replace(cp_a, snapshot=stripped), require_passed=True)

    # Eligibility helper rejects unverified / company_specific / wrong_entity records.
    assert is_central_kb_signal_context_eligible(kb_a["Artificial Intelligence"][0]) is True
    assert is_central_kb_signal_context_eligible(kb_a["Artificial Intelligence"][1]) is False
    assert is_central_kb_signal_context_eligible(kb_a["Artificial Intelligence"][2]) is False


def test_foresight_cognition_module_has_no_harvester_blob_live_scan_or_web_search_coupling():
    source = (_BACKEND / "scan_foresight_cognition.py").read_text(encoding="utf-8")

    assert "foresight_harvester" not in source
    assert "BlobStorage" not in source
    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "_verify_foresight_url" not in source
    assert "_generate_foresight_from_registry_sources" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "web_search=True" not in source
    assert "web_search=False" in source
