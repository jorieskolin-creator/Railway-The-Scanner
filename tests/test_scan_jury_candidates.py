"""Phase 3 contracts for Jury-stage source-candidate provenance."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_retrievals import canonicalize_source_url  # noqa: E402


def _candidate(**overrides):
    values = dict(
        run_id="RUN-JURY-001",
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="HTTPS://Example.TEST:443/strategy/#fragment",
        recorded_at=datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc),
        cached_extract_text="Cached official-site extract",
        parser_route="html/main-text/v1",
        metadata={"discovery_label": "company_site"},
    )
    values.update(overrides)
    return prepare_jury_source_candidate(**values)


def test_candidate_is_deterministic_and_canonicalizes_transport_identity_only():
    first = _candidate()
    second = _candidate()

    assert first == second
    assert first.stage_name == "jury_analysis"
    assert first.canonical_url == "https://example.test/strategy"
    assert first.canonical_url == canonicalize_source_url(first.source_url)
    assert len(first.cached_extract_sha256) == 64
    assert first.candidate_id.startswith("JSC-")
    assert not hasattr(first, "response_content")
    assert not hasattr(first, "raw_content")
    assert first.metadata == {"discovery_label": "company_site"}


def test_empty_cached_extract_is_allowed_and_hash_bound():
    empty = _candidate(
        candidate_origin="jury_live_acquisition_seed",
        cached_extract_text="",
        parser_route="none/v1",
        metadata={"discovery_label": "seed_url"},
    )

    assert empty.cached_extract_text == ""
    assert empty.cached_extract_sha256 == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert empty.candidate_origin == "jury_live_acquisition_seed"


def test_forbidden_evidence_metadata_keys_are_rejected():
    with pytest.raises(ValueError, match="evidence fields: claim_support"):
        _candidate(metadata={"claim_support": True, "discovery_label": "company_site"})

    with pytest.raises(ValueError, match="foresight_eligible"):
        _candidate(metadata={"foresight_eligible": False})

    with pytest.raises(ValueError, match="entity_match_status"):
        _candidate(metadata={"entity_match_status": "matched"})


def test_raw_bytes_and_non_http_sources_are_rejected():
    with pytest.raises(ValueError, match="no raw bytes"):
        _candidate(metadata={"preview": b"<html>"})

    with pytest.raises(ValueError, match="absolute HTTP"):
        _candidate(source_url="file:///private/brief.pdf")
