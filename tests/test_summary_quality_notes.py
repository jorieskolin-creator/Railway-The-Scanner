"""
Regression tests for summary length validation.

Run with: python -m pytest tests/test_summary_quality_notes.py -v
"""

from __future__ import annotations

import os
import sys
import types

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

if "gemini_backoff" not in sys.modules:
    gemini_backoff_stub = types.ModuleType("gemini_backoff")
    gemini_backoff_stub.call_with_503_backoff = None
    sys.modules["gemini_backoff"] = gemini_backoff_stub

from reality_check import validate_summary  # noqa: E402


def _summary(word_count: int) -> str:
    return " ".join(["ExampleCo"] + ["evidence"] * (word_count - 1))


def test_summary_inside_target_range_has_no_length_warning():
    result = validate_summary(_summary(359), "ExampleCo")

    assert not any("Summary" in flag and "words" in flag for flag in result["flags"])
    assert result.get("quality_notes", []) == []
    assert result["risk"] == "low"


def test_summary_above_target_range_is_quality_note_not_unsupported_flag():
    result = validate_summary(_summary(490), "ExampleCo")

    expected = "Summary length outside target range (490 words, expected 325-475)"
    assert expected in result.get("quality_notes", [])
    assert expected not in result["flags"]
    assert result["risk"] == "low"


def test_actual_hallucination_signals_remain_unsupported_flags():
    result = validate_summary(
        "ExampleCo today announced that its CEO was arrested in a bankruptcy scandal.",
        "ExampleCo",
    )

    assert result["flags"]
    assert any("Breaking news language" in flag for flag in result["flags"])
    assert result.get("quality_notes", []) == []
