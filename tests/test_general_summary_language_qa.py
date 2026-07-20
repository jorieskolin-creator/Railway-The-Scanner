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


def test_general_summary_policy_uses_sonnet_primary_and_gpt_fallback():
    policy = general_summary.policy_for("general_summary")

    assert policy["primary"] == general_summary.CLAUDE_SONNET_MODEL
    assert policy["fallback"] == general_summary.GPT_REASONING_MODEL
    assert policy["independent_review"] == general_summary.GPT_VERIFIER_MODEL


def test_general_summary_generation_uses_sonnet_before_openai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(general_summary, "_build_input_payload", lambda *_args: {})
    monkeypatch.setattr(general_summary, "_build_prompt", lambda *_args: "prompt")
    monkeypatch.setattr(general_summary, "call_anthropic_text", lambda *_args, **_kwargs: "A factual summary.")

    def fail_openai(*_args, **_kwargs):
        raise AssertionError("OpenAI fallback must not run when Sonnet succeeds")

    monkeypatch.setattr(general_summary, "_call_openai_summary", fail_openai)

    text, model = general_summary.generate_general_summary({}, "Test Company", time_budget_seconds=40)

    assert text == "A factual summary."
    assert model == general_summary.CLAUDE_SONNET_MODEL
