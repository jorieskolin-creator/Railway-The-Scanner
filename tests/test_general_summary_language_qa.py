"""Focused regression checks for post-correction General Summary hygiene."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import general_summary  # noqa: E402


def test_punctuation_seams_are_repaired_after_claim_replacement():
    text = "The company makes trucks., with production in Finland. Another claim., alongside services"

    polished = general_summary._polish_general_summary_text(text)

    assert "trucks, with" in polished
    assert "claim, alongside" in polished
    assert polished.endswith(".")


def test_duplicate_verifier_claim_prefers_actionable_replacement():
    claims = [
        {"claim": "The unsupported claim.", "reason": "First", "replacement": ""},
        {"claim": "The unsupported claim", "reason": "Second", "replacement": "A narrower fact."},
    ]

    deduped = general_summary._dedupe_general_summary_claims(claims)

    assert len(deduped) == 1
    assert deduped[0]["replacement"] == "A narrower fact."
