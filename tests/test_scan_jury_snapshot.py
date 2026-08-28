"""Contracts for immutable, evidence-neutral Jury input manifests."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_snapshot import (  # noqa: E402
    JURY_INPUT_SNAPSHOT_CONTRACT,
    assert_jury_input_snapshot_matches,
    build_jury_input_snapshot,
)
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


def _intent_retrieval(*, run_id="RUN-JURY-001"):
    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="intent",
        attempt=1,
        retrieval_origin="intent_web_acquisition",
        source_url="https://example.test/strategy/",
        retrieved_at=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Acquired page</body></html>",
        extracted_text="Acquired page",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )


def test_jury_input_manifest_is_deterministic_and_contains_references_not_text():
    checkpoint = _intent_checkpoint()
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)

    first = build_jury_input_snapshot(
        run_id="RUN-JURY-001",
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    second = build_jury_input_snapshot(
        run_id="RUN-JURY-001",
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=tuple(reversed(candidates)),
        retrievals=tuple(reversed(retrievals)),
    )

    assert first == second
    assert first.contract_version == JURY_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"jury-input://scan/{first.snapshot_id}"
    assert first.snapshot_id.startswith("JIS-")
    assert len(first.input_hash) == 64
    assert first.manifest["intent_checkpoint_ref"]["output_hash"] == checkpoint.output_hash
    manifest_text = str(first.manifest)
    assert "Cached about-page extract" not in manifest_text
    assert "Jury-stage fetch" not in manifest_text
    assert "intent_result" not in manifest_text
    assert "claim_support" not in manifest_text
    assert "foresight_eligible" not in manifest_text
    assert "response_content" not in manifest_text
    assert "extracted_text" not in first.manifest["candidate_refs"][0]
    assert "extracted_text" not in first.manifest["retrieval_refs"][0]
    assert "material_refs" not in first.manifest
    assert first.manifest["retrieval_refs"][0]["retrieval_outcome"] == "succeeded"


def test_jury_input_manifest_rejects_non_passed_checkpoint_and_intent_stage_retrievals():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)

    with pytest.raises(ValueError, match="passed Intent checkpoint"):
        build_jury_input_snapshot(
            run_id="RUN-JURY-001",
            attempt=1,
            intent_checkpoint=_intent_checkpoint(status="validation_failed"),
            candidates=candidates,
            retrievals=retrievals,
        )

    with pytest.raises(ValueError, match="does not match the Jury run"):
        build_jury_input_snapshot(
            run_id="RUN-JURY-001",
            attempt=1,
            intent_checkpoint=_intent_checkpoint(run_id="RUN-OTHER"),
            candidates=candidates,
            retrievals=retrievals,
        )

    with pytest.raises(ValueError, match="does not match the Jury attempt"):
        build_jury_input_snapshot(
            run_id="RUN-JURY-001",
            attempt=1,
            intent_checkpoint=_intent_checkpoint(),
            candidates=candidates,
            retrievals=[_intent_retrieval()],
        )


def test_added_omitted_or_tampered_inputs_cannot_match_the_manifest():
    checkpoint = _intent_checkpoint()
    candidate = _candidate()
    retrieval = _jury_retrieval()
    snapshot = build_jury_input_snapshot(
        run_id="RUN-JURY-001",
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=[candidate],
        retrievals=[retrieval],
    )
    extra = _candidate(
        candidate_origin="jury_live_acquisition_seed",
        source_url="https://news.example.test/coverage/",
        cached_extract_text="",
        parser_route="none/v1",
    )
    tampered = candidate.__class__(**{**candidate.__dict__, "cached_extract_text": "tampered"})

    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match the verified durable inputs"):
        assert_jury_input_snapshot_matches(
            snapshot,
            intent_checkpoint=checkpoint,
            candidates=[candidate, extra],
            retrievals=[retrieval],
        )
    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match the verified durable inputs"):
        assert_jury_input_snapshot_matches(
            snapshot,
            intent_checkpoint=checkpoint,
            candidates=[],
            retrievals=[retrieval],
        )
    with pytest.raises(JuryInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_jury_input_snapshot_matches(
            snapshot,
            intent_checkpoint=checkpoint,
            candidates=[tampered],
            retrievals=[retrieval],
        )
