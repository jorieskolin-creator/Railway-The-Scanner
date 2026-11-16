"""Hermetic contracts for Card 3L-Jury-parity five-Jury no-web cognition and model-policy binding."""

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

from evidence_packaging import build_jury_evidence_packages  # noqa: E402
from model_policy import (  # noqa: E402
    CLAUDE_SONNET_MODEL,
    GPT_REASONING_MODEL,
    MODEL_POLICY_VERSION,
)
from scan_checkpoint_integrity import validate_checkpoint_integrity  # noqa: E402
from scan_intent_checkpoint import (  # noqa: E402
    build_intent_checkpoint,
    build_intent_input_projection,
)
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import JuryStageExecutor  # noqa: E402
from scan_jury_candidates import (  # noqa: E402
    FORBIDDEN_METADATA_KEYS,
    prepare_jury_source_candidate,
)
from scan_jury_checkpoint import (  # noqa: E402
    JURY_CHECKPOINT_LANES,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_cognition import (  # noqa: E402
    JuryCognitiveUnavailableError,
    analyze_jury_projection_with_cognition,
    normalize_jury_lane_payload,
)
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ScanCheckpoint,
    ScanRun,
    StepLease,
)
from scanner_one import analyze_intent  # noqa: E402
from scanner_two import (  # noqa: E402
    SERVICE_PORTFOLIO_JURY_NAME,
    _FORENSIC_JURY_JSON_SCHEMA,
)


def _real_intent_checkpoint(run_id: str = "RUN-3L-001") -> ScanCheckpoint:
    materials = prepare_scan_materials(
        run_id=run_id,
        report_text="Example Platform Oy operates API integration and cloud data engineering.",
        uploads=[],
    ).materials
    snapshot = build_intent_input_snapshot(
        run_id=run_id,
        attempt=1,
        materials=materials,
        retrievals=[],
    )
    projection = build_intent_input_projection(
        snapshot,
        materials=materials,
        retrievals=[],
    )
    return build_intent_checkpoint(
        projection,
        intent_result=analyze_intent(projection.concatenated_text),
    )


class _ParityLoader:
    def __init__(self, *, run_id: str, company_name: str, intent_cp: ScanCheckpoint, candidates, retrievals):
        self.run_id = run_id
        self.company_name = company_name
        self.intent_cp = intent_cp
        self.candidates = list(candidates)
        self.retrievals = list(retrievals)
        self.snapshot = build_jury_input_snapshot(
            run_id=run_id,
            attempt=1,
            intent_checkpoint=intent_cp,
            candidates=self.candidates,
            retrievals=self.retrievals,
        )

    def load_run(self, *, run_id: str) -> ScanRun | None:
        if run_id != self.run_id:
            return None
        return ScanRun(
            run_id=self.run_id,
            company_name=self.company_name,
            company_url="https://example.test",
            cognitive_contract_version="scanner-cognitive/v1",
            status="running",
            current_stage="jury_analysis",
            idempotency_key="idemp-3l",
            request_hash="a" * 64,
            save_to_database=False,
            final_artifact_ref=None,
        )

    def load_verified_jury_inputs(self, *, run_id: str, attempt: int):
        assert (run_id, attempt) == (self.run_id, 1)
        return (
            self.snapshot,
            self.intent_cp,
            list(self.candidates),
            list(self.retrievals),
        )


def _lease(run_id: str = "RUN-3L-001") -> StepLease:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    return StepLease(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        lease_owner="jury-parity-worker",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=60),
    )


def _contains_any_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        if any(key in forbidden for key in value):
            return True
        return any(_contains_any_key(item, forbidden) for item in value.values())
    if isinstance(value, list):
        return any(_contains_any_key(item, forbidden) for item in value)
    return False


def test_five_jury_no_web_cognitive_parity_binds_packages_passages_and_model_policy():
    run_id = "RUN-3L-PARITY"
    now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    intent_cp = _real_intent_checkpoint(run_id)

    arch_text = (
        "Example Platform Oy provides a public REST API developer portal and event-driven "
        "integration platform for industrial telemetry."
    )
    ai_text = (
        "Example Platform Oy runs governed machine learning pipelines and cloud data analytics "
        "across customer operations."
    )
    candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/developers/api-platform",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text=arch_text,
        metadata={"discovery_label": "Developer API Portal", "source_type": "technical_artifact"},
    )
    retrieval = prepare_source_retrieval(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_live_acquisition",
        source_url="https://example.test/ai-data-platform",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>AI data platform</body></html>",
        extracted_text=ai_text,
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    loader = _ParityLoader(
        run_id=run_id,
        company_name="Example Platform Oy",
        intent_cp=intent_cp,
        candidates=[candidate],
        retrievals=[retrieval],
    )

    recorded_calls: list[dict] = []

    def fake_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
        recorded_calls.append({"prompt": prompt, "model": model, **kwargs})
        assert kwargs.get("web_search") is False
        assert kwargs.get("json_schema") == _FORENSIC_JURY_JSON_SCHEMA
        assert "Example Platform Oy" in prompt
        assert "PKG-" in prompt

        label = kwargs.get("log_label", "")
        if "ARCHITECTURAL JURY" in label:
            return json.dumps(
                {
                    "jury": "ARCHITECTURAL JURY (Engineering Reality)",
                    "findings": [
                        {
                            "category": "ARCHITECTURAL JURY (Engineering Reality)",
                            "supported_claim": (
                                "Example Platform Oy provides a public REST API developer portal "
                                "and event-driven integration platform"
                            ),
                            "found": True,
                            "sentiment": "positive",
                            "source_quote": (
                                "Example Platform Oy provides a public REST API developer portal "
                                "and event-driven integration platform for industrial telemetry."
                            ),
                            "title": "Developer API Portal",
                            "url": "https://example.test/developers/api-platform",
                            "is_recent": True,
                            "source_type": "technical_artifact",
                            "evidence_package_id": "PKG-HALLUCINATED",
                            "proposition_ids": ["PROP-HALLUCINATED"],
                            "support_passage_ids": ["PASS-HALLUCINATED"],
                            "subdimensions": ["architecture"],
                            "entity_scope": "company",
                            "source_verdict": "accepted",
                            "support_level": "exact",
                            "confidence": 82,
                        }
                    ],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": ["No public SLO latency benchmark"],
                    "confidence_score": 78,
                    "reality_score_adjustment": 4,
                }
            )
        if "AI & DATA JURY" in label:
            return "```json\n" + json.dumps(
                {
                    "jury": "AI & DATA JURY (Future Readiness)",
                    "findings": [
                        {
                            "category": "AI & DATA JURY (Future Readiness)",
                            "supported_claim": (
                                "Example Platform Oy runs governed machine learning pipelines "
                                "and cloud data analytics"
                            ),
                            "found": True,
                            "sentiment": "positive",
                            "source_quote": ai_text,
                            "title": "AI Data Platform",
                            "url": "https://example.test/ai-data-platform",
                            "is_recent": True,
                            "source_type": "official_ai_data_context",
                            "evidence_package_id": "",
                            "proposition_ids": [],
                            "support_passage_ids": [],
                            "subdimensions": ["data_foundation"],
                            "entity_scope": "company",
                            "source_verdict": "accepted",
                            "support_level": "exact",
                            "confidence": 75,
                        }
                    ],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 72,
                    "reality_score_adjustment": 3,
                }
            ) + "\n```"
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
        raise AssertionError(f"Unexpected log_label: {label}")

    executor = JuryStageExecutor.with_cognitive_parity(
        loader,
        openai_caller=fake_openai,
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Anthropic fallback should not run when primary succeeds")
        ),
    )
    result = executor.execute(_lease(run_id))

    assert result.gate_result["status"] == "passed"
    assert result.next_stage == "diagnosis"
    assert result.checkpoint is not None
    validate_checkpoint_integrity(result.checkpoint, require_passed=True)
    assert result.checkpoint.snapshot["model_policy_version"] == MODEL_POLICY_VERSION
    assert all(call["web_search"] is False for call in recorded_calls)
    assert all(call["model"] == GPT_REASONING_MODEL for call in recorded_calls)

    juries_by_name = {
        lane["jury"]: lane
        for lane in result.checkpoint.snapshot["jury_result"]["juries"]
    }
    arch_lane = juries_by_name["ARCHITECTURAL JURY (Engineering Reality)"]
    assert len(arch_lane["findings"]) == 1
    arch_finding = arch_lane["findings"][0]
    # Hallucinated PKG/PROP/PASS IDs were replaced with deterministic package lineage IDs!
    assert arch_finding["evidence_package_id"].startswith("PKG-")
    assert arch_finding["evidence_package_id"] != "PKG-HALLUCINATED"
    assert all(pid.startswith("PROP-") and pid != "PROP-HALLUCINATED" for pid in arch_finding["proposition_ids"])
    assert all(pid.startswith("PASS-") and pid != "PASS-HALLUCINATED" for pid in arch_finding["support_passage_ids"])
    assert arch_finding["source_id"] == candidate.candidate_id
    assert arch_lane["confidence_score"] == 78

    ai_lane = juries_by_name["AI & DATA JURY (Future Readiness)"]
    assert len(ai_lane["findings"]) == 1
    assert ai_lane["findings"][0]["source_id"] == retrieval.retrieval_id
    assert ai_lane["findings"][0]["evidence_package_id"].startswith("PKG-")
    assert not _contains_any_key(result.checkpoint.snapshot, set(FORBIDDEN_METADATA_KEYS))


def test_off_packet_urls_are_rejected_and_overreaching_claims_narrow_or_become_context_only():
    arch_jury = "ARCHITECTURAL JURY (Engineering Reality)"
    cultural_jury = "CULTURAL JURY (Talent & Psychological Safety)"

    arch_packets = [
        {
            "source_id": "JSC-ARCH-1",
            "url": "https://example.test/architecture",
            "title": "Platform Architecture",
            "source_type": "technical_artifact",
            "cached_excerpt": (
                "Example Oy exposes versioned GraphQL and REST APIs for partner integration."
            ),
            "fetched_excerpt": "",
            "content_readable": True,
        }
    ]
    raw_arch_payload = {
        "jury": arch_jury,
        "findings": [
            {
                "category": arch_jury,
                "supported_claim": "Off-packet hallucinated claim",
                "found": True,
                "sentiment": "positive",
                "source_quote": "invented quote",
                "title": "Unseen URL",
                "url": "https://unseen.example.org/blog",
                "is_recent": True,
                "source_type": "blog",
                "evidence_package_id": "",
                "proposition_ids": [],
                "support_passage_ids": [],
                "subdimensions": [],
                "entity_scope": "company",
                "source_verdict": "accepted",
                "support_level": "exact",
                "confidence": 90,
            },
            {
                "category": arch_jury,
                # Bundled multi-sentence overreaching claim triggers _derive_narrow_claim_from_source.
                "supported_claim": (
                    "Example Oy transformed every legacy mainframe in Europe. "
                    "It also achieved 99.999% global uptime."
                ),
                "found": True,
                "sentiment": "positive",
                "source_quote": "completely fabricated quote not in packet",
                "title": "Platform Architecture",
                "url": "https://example.test/architecture",
                "is_recent": True,
                "source_type": "technical_artifact",
                "evidence_package_id": "",
                "proposition_ids": [],
                "support_passage_ids": [],
                "subdimensions": [],
                "entity_scope": "company",
                "source_verdict": "accepted",
                "support_level": "exact",
                "confidence": 65,
            },
        ],
        "rejected_evidence": [],
        "contradictions": [],
        "missing_coverage": [],
        "confidence_score": 65,
        "reality_score_adjustment": 2,
    }
    normalized_arch = normalize_jury_lane_payload(
        arch_jury,
        raw_arch_payload,
        arch_packets,
        company_name="Example Oy",
        company_url="https://example.test",
    )
    assert any(
        item["url"] == "https://unseen.example.org/blog"
        and "not present in its supplied source packets" in item["reason"]
        for item in normalized_arch["rejected_evidence"]
    )
    assert len(normalized_arch["findings"]) == 1
    narrowed = normalized_arch["findings"][0]
    assert narrowed["support_level"] == "exact"
    assert "GraphQL and REST APIs" in narrowed["source_quote"]
    assert "99.999%" not in narrowed["supported_claim"]
    assert narrowed["source_id"] == "JSC-ARCH-1"

    # Short overview packet (<45 chars so no sentence qualifies for narrow claim extraction)
    # downgrades overreach to context_only, whereas a wrong same-name entity ("Nordic Inc")
    # is rejected outright by assess_entity_match.
    generic_packets = [
        {
            "source_id": "JSC-GEN-1",
            "url": "https://nordicsystems.example/overview",
            "title": "Company Overview",
            "source_type": "official",
            "cached_excerpt": "Nordic Systems Oy Helsinki office.",
            "fetched_excerpt": "",
            "content_readable": True,
        },
        {
            "source_id": "JSC-WRONG-1",
            "url": "https://otherfirm.org/about",
            "title": "Nordic Inc NYC",
            "source_type": "blog",
            "cached_excerpt": (
                "Nordic Inc is a Manhattan staffing agency founded in 2019."
            ),
            "fetched_excerpt": "",
            "content_readable": True,
        },
    ]
    raw_cultural_payload = {
        "jury": cultural_jury,
        "findings": [
            {
                "category": cultural_jury,
                "supported_claim": "Nordic Systems Oy has top-decile psychological safety scores.",
                "found": True,
                "sentiment": "positive",
                "source_quote": "top-decile psychological safety",
                "title": "Company Overview",
                "url": "https://nordicsystems.example/overview",
                "is_recent": False,
                "source_type": "official",
                "evidence_package_id": "",
                "proposition_ids": [],
                "support_passage_ids": [],
                "subdimensions": [],
                "entity_scope": "company",
                "source_verdict": "accepted",
                "support_level": "exact",
                "confidence": 50,
            },
            {
                "category": cultural_jury,
                "supported_claim": "Nordic Inc is a Manhattan staffing agency founded in 2019.",
                "found": True,
                "sentiment": "neutral",
                "source_quote": "Nordic Inc is a Manhattan staffing agency founded in 2019.",
                "title": "Nordic Inc NYC",
                "url": "https://otherfirm.org/about",
                "is_recent": True,
                "source_type": "blog",
                "evidence_package_id": "",
                "proposition_ids": [],
                "support_passage_ids": [],
                "subdimensions": [],
                "entity_scope": "company",
                "source_verdict": "accepted",
                "support_level": "exact",
                "confidence": 80,
            },
        ],
        "rejected_evidence": [],
        "contradictions": [],
        "missing_coverage": [],
        "confidence_score": 50,
        "reality_score_adjustment": 0,
    }
    normalized_cultural = normalize_jury_lane_payload(
        cultural_jury,
        raw_cultural_payload,
        generic_packets,
        company_name="Nordic Systems Oy",
        company_url="https://nordicsystems.example",
    )
    assert any(
        "retained as scoped context" in item["reason"]
        for item in normalized_cultural["rejected_evidence"]
    )
    assert any(
        item["url"] == "https://otherfirm.org/about"
        and "wrong same-name or unrelated entity" in item["reason"]
        for item in normalized_cultural["rejected_evidence"]
    )
    assert len(normalized_cultural["findings"]) == 1
    assert normalized_cultural["findings"][0]["support_level"] == "context_only"
    assert normalized_cultural["findings"][0]["source_verdict"] == "scoped_context"


def test_split_packet_chunking_sonnet_fallback_ai_rescue_and_unavailable_fail_closed():
    run_id = "RUN-3L-FALLBACK"
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    intent_cp = _real_intent_checkpoint(run_id)

    # Create 10 distinct packets so SERVICE_PORTFOLIO_JURY_NAME (>8 threshold) splits into chunks.
    candidates = [
        prepare_jury_source_candidate(
            run_id=run_id,
            attempt=1,
            candidate_origin="company_evidence_db",
            source_url=f"https://example.test/services/offering-{idx}",
            recorded_at=now,
            parser_route="html/main-text/v1",
            cached_extract_text=(
                f"Example Oy delivers managed cloud migration service offering {idx} "
                f"with enterprise SLA architecture and AI data analytics."
            ),
            metadata={"discovery_label": f"Service Offering {idx}", "source_type": "official"},
        )
        for idx in range(10)
    ]
    snapshot = build_jury_input_snapshot(
        run_id=run_id,
        attempt=1,
        intent_checkpoint=intent_cp,
        candidates=candidates,
        retrievals=[],
    )
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=intent_cp,
        candidates=candidates,
        retrievals=[],
    )

    sonnet_lanes: list[str] = []
    rescue_lanes: list[str] = []
    service_primary_calls = 0

    def fake_openai(prompt: str, model: str = GPT_REASONING_MODEL, **kwargs):
        nonlocal service_primary_calls
        assert kwargs.get("web_search") is False
        schema_name = kwargs.get("json_schema_name")
        label = kwargs.get("log_label", "")
        if schema_name == "forensic_jury_rescue":
            rescue_lanes.append(label)
            if "AI & DATA JURY" in label:
                return {
                    "jury": "AI & DATA JURY (Future Readiness)",
                    "findings": [
                        {
                            "category": "AI & DATA JURY (Future Readiness)",
                            "supported_claim": (
                                "Example Oy delivers managed cloud migration service offering 0 "
                                "with enterprise SLA architecture and AI data analytics"
                            ),
                            "found": True,
                            "sentiment": "neutral",
                            "source_quote": (
                                "Example Oy delivers managed cloud migration service offering 0 "
                                "with enterprise SLA architecture and AI data analytics."
                            ),
                            "title": "Service Offering 0",
                            "url": "https://example.test/services/offering-0",
                            "is_recent": True,
                            "source_type": "official",
                            "evidence_package_id": "",
                            "proposition_ids": [],
                            "support_passage_ids": [],
                            "subdimensions": [],
                            "entity_scope": "company",
                            "source_verdict": "accepted",
                            "support_level": "exact",
                            "confidence": 60,
                        }
                    ],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 55,
                    "reality_score_adjustment": 0,
                }
            return {
                "jury": label.replace("Forensic Jury Rescue ", ""),
                "findings": [],
                "rejected_evidence": [],
                "contradictions": [],
                "missing_coverage": [],
                "confidence_score": 0,
                "reality_score_adjustment": 0,
            }

        if "ARCHITECTURAL JURY" in label:
            raise TimeoutError("simulated primary GPT timeout")
        if "AI & DATA JURY" in label:
            raise TimeoutError("simulated primary GPT timeout on AI & Data Jury")
        if SERVICE_PORTFOLIO_JURY_NAME in label:
            service_primary_calls += 1
        for lane_name in JURY_CHECKPOINT_LANES:
            if lane_name in label:
                return {
                    "jury": lane_name,
                    "findings": [],
                    "rejected_evidence": [],
                    "contradictions": [],
                    "missing_coverage": [],
                    "confidence_score": 0,
                    "reality_score_adjustment": 0,
                }
        raise AssertionError(f"Unexpected call: {label}")

    def fake_anthropic(prompt: str, model: str = CLAUDE_SONNET_MODEL, **kwargs):
        assert kwargs.get("web_search") is False
        assert model == CLAUDE_SONNET_MODEL
        label = kwargs.get("log_label", "")
        sonnet_lanes.append(label)
        return {
            "jury": "ARCHITECTURAL JURY (Engineering Reality)",
            "findings": [
                {
                    "category": "ARCHITECTURAL JURY (Engineering Reality)",
                    "supported_claim": (
                        "Example Oy delivers managed cloud migration service offering 1 "
                        "with enterprise SLA architecture and AI data analytics"
                    ),
                    "found": True,
                    "sentiment": "positive",
                    "source_quote": (
                        "Example Oy delivers managed cloud migration service offering 1 "
                        "with enterprise SLA architecture and AI data analytics."
                    ),
                    "title": "Service Offering 1",
                    "url": "https://example.test/services/offering-1",
                    "is_recent": True,
                    "source_type": "official",
                    "evidence_package_id": "",
                    "proposition_ids": [],
                    "support_passage_ids": [],
                    "subdimensions": [],
                    "entity_scope": "company",
                    "source_verdict": "accepted",
                    "support_level": "exact",
                    "confidence": 70,
                }
            ],
            "rejected_evidence": [],
            "contradictions": [],
            "missing_coverage": [],
            "confidence_score": 68,
            "reality_score_adjustment": 1,
        }

    result = analyze_jury_projection_with_cognition(
        projection,
        company_name="Example Oy",
        openai_caller=fake_openai,
        anthropic_caller=fake_anthropic,
    )
    juries_by_name = {lane["jury"]: lane for lane in result["juries"]}

    # Service Portfolio Jury (>8 packets) split into multiple chunks.
    assert service_primary_calls >= 2
    assert juries_by_name[SERVICE_PORTFOLIO_JURY_NAME]["split_analysis"] is True
    # Architectural Jury fell back to Sonnet; AI & Data Jury skipped Sonnet and used GPT rescue.
    assert any("ARCHITECTURAL JURY" in item for item in sonnet_lanes)
    assert not any("AI & DATA JURY" in item for item in sonnet_lanes)
    assert any("AI & DATA JURY" in item for item in rescue_lanes)
    assert len(juries_by_name["ARCHITECTURAL JURY (Engineering Reality)"]["findings"]) == 1
    assert len(juries_by_name["AI & DATA JURY (Future Readiness)"]["findings"]) == 1

    # Reliability Plan Rule #6: total provider unavailability on non-empty packages raises explicitly.
    assert build_jury_evidence_packages("ARCHITECTURAL JURY (Engineering Reality)", [dict(p) for p in projection.packets])
    with pytest.raises(JuryCognitiveUnavailableError, match="unavailable"):
        analyze_jury_projection_with_cognition(
            projection,
            company_name="Example Oy",
            openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("gpt down")),
            anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("sonnet down")),
        )


def test_empty_packets_skip_model_calls_and_malformed_model_payload_fails_validation():
    run_id = "RUN-3L-EMPTY"
    intent_cp = _real_intent_checkpoint(run_id)
    empty_loader = _ParityLoader(
        run_id=run_id,
        company_name="Empty Packet Oy",
        intent_cp=intent_cp,
        candidates=[],
        retrievals=[],
    )
    empty_exec = JuryStageExecutor.with_cognitive_parity(
        empty_loader,
        openai_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Empty packet set must not invoke provider")
        ),
        anthropic_caller=lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("Empty packet set must not invoke provider")
        ),
    )
    empty_res = empty_exec.execute(_lease(run_id))
    assert empty_res.gate_result["status"] == "passed"
    assert empty_res.checkpoint is not None
    validate_checkpoint_integrity(empty_res.checkpoint, require_passed=True)

    # Malformed provider object (non-int confidence_score) fails checkpoint gate validation.
    now = datetime(2026, 11, 13, 11, 0, tzinfo=timezone.utc)
    candidate = prepare_jury_source_candidate(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/api",
        recorded_at=now,
        parser_route="html/main-text/v1",
        cached_extract_text="Example Oy operates an enterprise API integration platform.",
        metadata={"discovery_label": "API", "source_type": "technical_artifact"},
    )
    bad_loader = _ParityLoader(
        run_id=run_id,
        company_name="Malformed Output Oy",
        intent_cp=intent_cp,
        candidates=[candidate],
        retrievals=[],
    )
    bad_exec = JuryStageExecutor.with_cognitive_parity(
        bad_loader,
        openai_caller=lambda *_a, **_kw: {
            "jury": "ARCHITECTURAL JURY (Engineering Reality)",
            "findings": [],
            "rejected_evidence": [],
            "contradictions": [],
            "missing_coverage": [],
            "confidence_score": "high",
            "reality_score_adjustment": 0,
        },
        anthropic_caller=lambda *_a, **_kw: None,
    )
    bad_res = bad_exec.execute(_lease(run_id))
    assert bad_res.gate_result["status"] == "validation_failed"
    assert bad_res.next_stage is None
    assert bad_res.checkpoint is not None
    assert "jury_result" not in bad_res.checkpoint.snapshot


def test_model_policy_version_binding_changes_hashes_and_blocks_stale_policy_replay():
    run_id = "RUN-3L-POLICY"
    intent_cp = _real_intent_checkpoint(run_id)
    loader = _ParityLoader(
        run_id=run_id,
        company_name="Policy Binding Oy",
        intent_cp=intent_cp,
        candidates=[],
        retrievals=[],
    )
    res_v5 = JuryStageExecutor(
        loader, model_policy_version="model-policy-v5-sonnet55"
    ).execute(_lease(run_id))
    res_v6 = JuryStageExecutor(
        loader, model_policy_version="model-policy-v6-future"
    ).execute(_lease(run_id))

    assert res_v5.checkpoint is not None and res_v6.checkpoint is not None
    assert res_v5.checkpoint.output_hash != res_v6.checkpoint.output_hash
    assert res_v5.checkpoint.checkpoint_id != res_v6.checkpoint.checkpoint_id
    validate_checkpoint_integrity(res_v5.checkpoint, require_passed=True)
    validate_checkpoint_integrity(res_v6.checkpoint, require_passed=True)

    # Replay across a model_policy_version upgrade is rejected by commit_jury_stage.
    class _ExistingRepo:
        def load_checkpoint(self, *, run_id, stage_name, attempt):
            return res_v5.checkpoint

        def complete_step_with_checkpoint(self, lease, *, checkpoint, next_stage, now=None):
            raise AssertionError("Should not complete when existing checkpoint mismatches")

    with pytest.raises(ValueError, match="different Jury checkpoint"):
        commit_jury_stage(_ExistingRepo(), _lease(run_id), res_v6)

    # Empty or missing model_policy_version is rejected at build and reload boundaries.
    projection = build_jury_input_projection(
        loader.snapshot,
        intent_checkpoint=intent_cp,
        candidates=[],
        retrievals=[],
    )
    with pytest.raises(ValueError, match="model_policy_version"):
        build_jury_checkpoint(
            projection,
            jury_result=res_v5.checkpoint.snapshot["jury_result"],
            model_policy_version="   ",
        )

    stripped_snapshot = dict(res_v5.checkpoint.snapshot)
    del stripped_snapshot["model_policy_version"]
    with pytest.raises(CheckpointIntegrityError, match="unexpected or missing keys"):
        validate_checkpoint_integrity(
            replace(res_v5.checkpoint, snapshot=stripped_snapshot),
            require_passed=True,
        )


def test_jury_cognition_module_has_no_web_acquisition_live_scan_or_blob_coupling():
    source = (_BACKEND / "scan_jury_cognition.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
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
