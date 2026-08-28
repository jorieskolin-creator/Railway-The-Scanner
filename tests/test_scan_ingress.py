"""Phase 3 contracts for inactive, transactional Scan ingress."""

from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_ingress import prepare_scan_ingress  # noqa: E402


def test_same_idempotency_key_replays_identical_run_material_and_request_hash():
    kwargs = {
        "company_name": "Example Oy",
        "company_url": "https://example.test",
        "report_text": "Company-provided operating context.",
        "save_to_database": True,
        "uploads": [{"filename": "brief.txt", "content": b"Operating brief"}],
        "idempotency_key": "client-request-001",
    }

    first = prepare_scan_ingress(**kwargs)
    second = prepare_scan_ingress(**kwargs)

    assert first == second
    assert first.run_id.startswith("RUN-")
    assert len(first.request_hash) == 64
    assert first.initial_stage == "material_ingestion"
    assert len(first.materials) == 2


def test_request_hash_is_input_based_not_idempotency_key_based():
    common = {
        "company_name": "Example Oy",
        "company_url": "https://example.test",
        "report_text": "Company-provided operating context.",
        "save_to_database": True,
        "uploads": [{"filename": "brief.txt", "content": b"Operating brief"}],
    }

    first = prepare_scan_ingress(**common, idempotency_key="client-request-001")
    second = prepare_scan_ingress(**common, idempotency_key="client-request-002")

    assert first.run_id != second.run_id
    assert first.request_hash == second.request_hash


def test_changed_input_with_same_idempotency_key_has_a_different_request_hash():
    common = {
        "company_name": "Example Oy",
        "company_url": "https://example.test",
        "save_to_database": True,
        "uploads": [],
        "idempotency_key": "client-request-001",
    }

    first = prepare_scan_ingress(**common, report_text="first submission")
    changed = prepare_scan_ingress(**common, report_text="changed submission")

    assert first.run_id == changed.run_id
    assert first.request_hash != changed.request_hash


def test_ingress_requires_company_name_and_idempotency_key():
    with pytest.raises(ValueError, match="company_name"):
        prepare_scan_ingress(
            company_name="",
            company_url=None,
            report_text="",
            save_to_database=True,
            uploads=[],
            idempotency_key="client-request-001",
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        prepare_scan_ingress(
            company_name="Example Oy",
            company_url=None,
            report_text="",
            save_to_database=True,
            uploads=[],
            idempotency_key="",
        )
