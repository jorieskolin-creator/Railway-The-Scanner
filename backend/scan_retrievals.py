"""Deterministic preparation of durable source-retrieval records.

Retrieval records preserve what happened at an acquisition boundary.  They do
not make a source live, entity-scoped, eligible for analysis, or supportive of
a Company claim; those are independent later verdicts.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from scan_supervisor import SourceRetrieval


def prepare_source_retrieval(
    *,
    run_id: str,
    stage_name: str,
    attempt: int,
    retrieval_origin: str,
    source_url: str,
    retrieved_at: datetime,
    http_status: int | None,
    content_type: str,
    response_content: bytes,
    extracted_text: str,
    parser_route: str,
    readability_status: str,
    retrieval_outcome: str,
    extraction_warnings: tuple[str, ...] = (),
    response_metadata: Mapping[str, Any] | None = None,
) -> SourceRetrieval:
    """Prepare one raw-body-free, hash-bound retrieval record."""

    canonical_url = canonicalize_source_url(source_url)
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware.")
    normalized_retrieved_at = retrieved_at.astimezone(timezone.utc)
    if not isinstance(response_content, bytes):
        raise TypeError("response_content must be bytes.")
    if not isinstance(extracted_text, str):
        raise TypeError("extracted_text must be text.")
    metadata = dict(response_metadata or {})
    response_sha256 = _sha256(response_content)
    extracted_text_sha256 = _sha256(extracted_text.encode("utf-8"))
    retrieval_id = "RET-" + _sha256(
        "\0".join(
            (
                run_id,
                stage_name,
                str(attempt),
                retrieval_origin,
                canonical_url,
                normalized_retrieved_at.isoformat(),
                response_sha256,
            )
        ).encode("utf-8")
    )[:32].upper()
    return SourceRetrieval(
        retrieval_id=retrieval_id,
        run_id=run_id,
        stage_name=stage_name,
        attempt=attempt,
        retrieval_origin=retrieval_origin,
        source_url=source_url,
        canonical_url=canonical_url,
        retrieved_at=normalized_retrieved_at,
        http_status=http_status,
        content_type=content_type.strip().lower(),
        response_sha256=response_sha256,
        extracted_text_sha256=extracted_text_sha256,
        parser_route=parser_route,
        readability_status=readability_status,
        retrieval_outcome=retrieval_outcome,
        extraction_warnings=tuple(extraction_warnings),
        response_metadata=metadata,
        extracted_text=extracted_text,
    )


def canonicalize_source_url(source_url: str) -> str:
    """Normalize transport identity only; this is not an entity or trust verdict."""

    parsed = urlsplit(str(source_url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must be an absolute HTTP or HTTPS URL.")
    hostname = parsed.hostname.lower()
    port = parsed.port
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
