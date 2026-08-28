"""Phase 3 contracts for durable retrieval provenance."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_retrievals import canonicalize_source_url, prepare_source_retrieval  # noqa: E402


def _retrieval():
    return prepare_source_retrieval(
        run_id="RUN-001",
        stage_name="material_ingestion",
        attempt=1,
        retrieval_origin="intent_web_acquisition",
        source_url="HTTPS://Example.TEST:443/strategy/#fragment",
        retrieved_at=datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Acquired page</body></html>",
        extracted_text="Acquired page",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
        response_metadata={"provider": "test", "content_length": 39},
    )


def test_retrieval_is_deterministic_and_canonicalizes_transport_identity_only():
    first = _retrieval()
    second = _retrieval()

    assert first == second
    assert first.canonical_url == "https://example.test/strategy"
    assert len(first.response_sha256) == 64
    assert len(first.extracted_text_sha256) == 64
    assert not hasattr(first, "response_content")
    assert first.retrieval_outcome == "succeeded"


def test_canonical_url_rejects_non_http_source_and_preserves_query():
    with pytest.raises(ValueError, match="absolute HTTP"):
        canonicalize_source_url("file:///private/brief.pdf")

    assert canonicalize_source_url("https://EXAMPLE.test/path/?ref=campaign#part") == (
        "https://example.test/path?ref=campaign"
    )
