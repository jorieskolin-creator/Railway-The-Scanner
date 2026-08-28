"""Hermetic contracts for the Jury input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import (  # noqa: E402
    JURY_CHECKPOINT_CONTRACT,
    JURY_CHECKPOINT_LANES,
    JURY_PROJECTION_CONTRACT,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import JuryInputSnapshotIntegrityError, ScanCheckpoint  # noqa: E402


def _intent_checkpoint(*, run_id="RUN-JURY-001", status="passed"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "A" * 32,
        run_id=run_id,
        stage_name="intent",
        attempt=1,
        contract_version=INTENT_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": INTENT_CHECKPOINT_CONTRACT,
            "status": status,
            "reasons": [],
            "warnings": [],
        },
        snapshot={"contract": INTENT_CHECKPOINT_CONTRACT, "intent_result": {"score": 12}},
    )


def _candidate(*, run_id="RUN-JURY-001", **overrides):
    values = dict(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="HTTPS://Example.TEST:443/about/#section",
        recorded_at=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc),
        cached_extract_text="Cached about-page extract",
        parser_route="html/main-text/v1",
        metadata={"discovery_label": "company_site"},
    )
    values.update(overrides)
    return prepare_jury_source_candidate(**values)


def _jury_retrieval(*, run_id="RUN-JURY-001"):
    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_web_acquisition",
        source_url="https://news.example.test/coverage/",
        retrieved_at=datetime(2026, 8, 28, 11, 5, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Jury-stage fetch</body></html>",
        extracted_text="Jury-stage fetch",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )


def _snapshot(candidates=None, retrievals=None, checkpoint=None):
    candidates = (_candidate(),) if candidates is None else candidates
    retrievals = (_jury_retrieval(),) if retrievals is None else retrievals
    checkpoint = _intent_checkpoint() if checkpoint is None else checkpoint
    return build_jury_input_snapshot(
        run_id="RUN-JURY-001",
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )


def _empty_lane(jury_name: str) -> dict:
    return {
        "jury": jury_name,
        "findings": [],
        "rejected_evidence": [],
        "contradictions": [],
        "missing_coverage": [],
        "confidence_score": 0,
        "reality_score_adjustment": 0,
        "split_analysis": False,
    }


def _valid_finding(**overrides):
    finding = {
        "category": "ARCHITECTURAL JURY (Engineering Reality)",
        "supported_claim": "Example lists an API portal.",
        "found": True,
        "sentiment": "neutral",
        "source_quote": "API portal",
        "title": "About",
        "url": "https://example.test/about",
        "is_recent": True,
        "source_type": "technical_artifact",
        "evidence_package_id": "PKG-1",
        "proposition_ids": ["PROP-1"],
        "support_passage_ids": ["PASS-1"],
        "subdimensions": ["engineering"],
        "entity_scope": "company",
        "source_verdict": "accepted",
        "support_level": "partial",
        "confidence": 40,
    }
    finding.update(overrides)
    return finding


def _valid_jury_result(*, with_finding=False):
    lanes = []
    for name in JURY_CHECKPOINT_LANES:
        payload = _empty_lane(name)
        if with_finding and name == JURY_CHECKPOINT_LANES[0]:
            payload["findings"] = [_valid_finding()]
        lanes.append(payload)
    return {"juries": lanes}


def test_projection_is_deterministic_and_uses_only_snapshot_bound_inputs():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    snapshot = _snapshot(candidates, retrievals)
    checkpoint = _intent_checkpoint()

    first = build_jury_input_projection(
        snapshot,
        intent_checkpoint=checkpoint,
        candidates=tuple(reversed(candidates)),
        retrievals=tuple(reversed(retrievals)),
    )
    second = build_jury_input_projection(
        snapshot,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )

    assert first == second
    assert first.contract == JURY_PROJECTION_CONTRACT
    assert first.stage == "jury_analysis"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    assert first.intent_checkpoint_ref["checkpoint_id"] == checkpoint.checkpoint_id
    assert [reference["id"] for reference in first.input_refs] == [
        candidates[0].candidate_id,
        retrievals[0].retrieval_id,
    ]
    assert [packet["source_id"] for packet in first.packets] == [
        candidates[0].candidate_id,
        retrievals[0].retrieval_id,
    ]
    assert first.packets[0]["cached_excerpt"] == "Cached about-page extract"
    assert first.packets[1]["fetched_excerpt"] == "Jury-stage fetch"
    assert all(
        reference["kind"] in {"jury_source_candidate", "source_retrieval"}
        for reference in first.input_refs
    )


def test_projection_rejects_added_omitted_or_tampered_inputs():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    snapshot = _snapshot(candidates, retrievals)
    extra = _candidate(
        candidate_origin="previous_scan_source_cache",
        source_url="https://cache.example.test/prior/",
        cached_extract_text="Prior cached extract",
    )
    tampered = candidates[0].__class__(
        **{**candidates[0].__dict__, "cached_extract_text": "tampered extract"}
    )

    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match"):
        build_jury_input_projection(
            snapshot,
            intent_checkpoint=_intent_checkpoint(),
            candidates=(*candidates, extra),
            retrievals=retrievals,
        )
    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match"):
        build_jury_input_projection(
            snapshot,
            intent_checkpoint=_intent_checkpoint(),
            candidates=(),
            retrievals=retrievals,
        )
    with pytest.raises(JuryInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_jury_input_projection(
            snapshot,
            intent_checkpoint=_intent_checkpoint(),
            candidates=[tampered],
            retrievals=retrievals,
        )


def test_checkpoint_hashes_are_stable_and_omit_text_and_raw_bytes():
    projection = build_jury_input_projection(
        _snapshot(),
        intent_checkpoint=_intent_checkpoint(),
        candidates=(_candidate(),),
        retrievals=(_jury_retrieval(),),
    )
    result = _valid_jury_result(with_finding=True)

    first = build_jury_checkpoint(projection, jury_result=result)
    second = build_jury_checkpoint(projection, jury_result={"juries": list(result["juries"])})

    assert first == second
    assert first.contract_version == JURY_CHECKPOINT_CONTRACT
    assert first.stage_name == "jury_analysis"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["gate_results"][0]["status"] == "passed"
    assert first.snapshot["input_refs"] == [dict(reference) for reference in projection.input_refs]
    assert first.snapshot["intent_checkpoint_ref"]["output_hash"] == "b" * 64
    assert first.snapshot["jury_result"]["juries"][0]["findings"][0]["supported_claim"] == (
        "Example lists an API portal."
    )
    snapshot_text = str(first.snapshot)
    assert "Cached about-page extract" not in snapshot_text
    assert "Jury-stage fetch" not in snapshot_text
    assert "intent_result" not in snapshot_text
    assert "packets" not in first.snapshot
    assert "extracted_text" not in snapshot_text
    assert "response_content" not in snapshot_text
    assert "claim_support" not in snapshot_text
    assert "foresight_eligible" not in snapshot_text


def test_malformed_jury_result_cannot_produce_a_passed_gate():
    projection = build_jury_input_projection(
        _snapshot(),
        intent_checkpoint=_intent_checkpoint(),
        candidates=(_candidate(),),
        retrievals=(_jury_retrieval(),),
    )
    valid = _valid_jury_result()

    missing_juries = build_jury_checkpoint(projection, jury_result={"findings": []})
    extra_field = build_jury_checkpoint(
        projection,
        jury_result={**valid, "evidence_id": "EVD-1"},
    )
    wrong_order = build_jury_checkpoint(
        projection,
        jury_result={"juries": list(reversed(valid["juries"]))},
    )
    forbidden = _valid_jury_result(with_finding=True)
    forbidden["juries"][0]["findings"][0]["foresight_eligible"] = True
    forbidden_checkpoint = build_jury_checkpoint(projection, jury_result=forbidden)
    non_mapping = build_jury_checkpoint(projection, jury_result="not-a-result")

    assert missing_juries.gate_result["status"] == "validation_failed"
    assert extra_field.gate_result["status"] == "validation_failed"
    assert wrong_order.gate_result["status"] == "validation_failed"
    assert forbidden_checkpoint.gate_result["status"] == "validation_failed"
    assert non_mapping.gate_result["status"] == "validation_failed"
    assert missing_juries.snapshot["output_refs"] == []
    assert "jury_result" not in missing_juries.snapshot
    assert extra_field.gate_result["reasons"] == [{"code": "malformed_jury_result"}]


def test_empty_packets_can_still_pass_with_a_valid_result_shape():
    snapshot = _snapshot(candidates=(), retrievals=())
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=_intent_checkpoint(),
        candidates=(),
        retrievals=(),
    )

    checkpoint = build_jury_checkpoint(projection, jury_result=_valid_jury_result())

    assert projection.packets == ()
    assert checkpoint.gate_result["status"] == "passed"


def test_module_does_not_call_jury_cognition_or_acquire_content():
    source = Path(_BACKEND, "scan_jury_checkpoint.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "analyze_intent" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "from main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
