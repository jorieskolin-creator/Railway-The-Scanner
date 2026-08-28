"""Contracts for immutable, evidence-neutral Intent input manifests."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_snapshot import (  # noqa: E402
    INTENT_INPUT_SNAPSHOT_CONTRACT,
    assert_intent_input_snapshot_matches,
    build_intent_input_snapshot,
)
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import IntentInputSnapshotIntegrityError  # noqa: E402


def _materials():
    return prepare_scan_materials(
        run_id="RUN-INTENT-001",
        report_text="Submitted company context.",
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
    ).materials


def _retrievals():
    return (
        prepare_source_retrieval(
            run_id="RUN-INTENT-001",
            stage_name="intent",
            attempt=1,
            retrieval_origin="intent_web_acquisition",
            source_url="https://example.test/strategy/",
            retrieved_at=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
            http_status=200,
            content_type="text/html",
            response_content=b"<html><body>Acquired page</body></html>",
            extracted_text="Acquired page",
            parser_route="html/main-text/v1",
            readability_status="readable",
            retrieval_outcome="succeeded",
        ),
    )


def test_intent_input_manifest_is_deterministic_and_contains_references_not_text():
    materials = _materials()
    retrievals = _retrievals()

    first = build_intent_input_snapshot(
        run_id="RUN-INTENT-001", attempt=1, materials=materials, retrievals=retrievals
    )
    second = build_intent_input_snapshot(
        run_id="RUN-INTENT-001",
        attempt=1,
        materials=tuple(reversed(materials)),
        retrievals=tuple(reversed(retrievals)),
    )

    assert first == second
    assert first.contract_version == INTENT_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"intent-input://scan/{first.snapshot_id}"
    assert len(first.input_hash) == 64
    assert "extracted_text" not in str(first.manifest)
    assert "response_content" not in str(first.manifest)
    assert "claim_support" not in str(first.manifest)
    assert first.manifest["retrieval_refs"][0]["retrieval_outcome"] == "succeeded"


def test_intent_input_manifest_rejects_cross_attempt_and_tampered_hydration():
    materials = _materials()
    retrieval = _retrievals()[0]

    with pytest.raises(ValueError, match="does not match the Intent attempt"):
        build_intent_input_snapshot(
            run_id="RUN-INTENT-001", attempt=2, materials=materials, retrievals=[retrieval]
        )

    snapshot = build_intent_input_snapshot(
        run_id="RUN-INTENT-001", attempt=1, materials=materials, retrievals=[retrieval]
    )
    tampered = retrieval.__class__(**{**retrieval.__dict__, "extracted_text": "tampered"})
    with pytest.raises(IntentInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_intent_input_snapshot_matches(
            snapshot, materials=materials, retrievals=[tampered]
        )
