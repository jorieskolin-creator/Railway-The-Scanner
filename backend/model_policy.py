"""Central model policy defaults for Scanner.

The policy names approved production models, keeps expensive Pro usage explicit,
and prevents distrusted lightweight models from becoming silent fallbacks.
"""

from __future__ import annotations

import os
from typing import Any


MODEL_POLICY_VERSION = os.environ.get("SCANNER_MODEL_POLICY_VERSION", "model-policy-v2-gpt56-sol").strip() or "model-policy-v2-gpt56-sol"

GPT_REASONING_MODEL = os.environ.get("SCANNER_DEFAULT_FRONTIER_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
# GPT-5.6 Pro is a reasoning mode on the Sol model, not a separate model slug.
GPT_PRO_MODEL = os.environ.get("SCANNER_PREMIUM_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
GPT_PRO_REASONING_MODE = os.environ.get("SCANNER_PREMIUM_REASONING_MODE", "pro").strip() or "pro"
GPT_VERIFIER_MODEL = os.environ.get("SCANNER_VALIDATOR_MODEL", "gpt-5.4").strip() or "gpt-5.4"
GPT_EXPERIMENTAL_MODEL = os.environ.get("SCANNER_GPT56_SOL_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
ENABLE_GPT56_SOL_TEST = os.environ.get("SCANNER_ENABLE_GPT56_SOL_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
BUYING_GENERATION_MODEL_OVERRIDE = (
    os.environ.get("SCANNER_BUYING_GENERATION_MODEL", "").strip()
    or (GPT_EXPERIMENTAL_MODEL if ENABLE_GPT56_SOL_TEST else "")
)
DETAILED_COMPLETION_MODEL = (
    os.environ.get("SCANNER_DETAILED_COMPLETION_MODEL", "").strip()
    or GPT_REASONING_MODEL
)

CLAUDE_OPUS_MODEL = os.environ.get("CLAUDE_OPUS_MODEL", "claude-opus-4-8").strip() or "claude-opus-4-8"
CLAUDE_SONNET_MODEL = os.environ.get("CLAUDE_SONNET_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"

GEMINI_GROUNDED_MODEL = os.environ.get("SCANNER_GROUNDED_RESEARCH_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
GEMINI_HARVEST_MODEL = os.environ.get("GEMINI_HARVEST_MODEL", GEMINI_GROUNDED_MODEL).strip() or GEMINI_GROUNDED_MODEL
GEMINI_FORENSIC_MODEL = os.environ.get("GEMINI_FORENSIC_MODEL", GEMINI_GROUNDED_MODEL).strip() or GEMINI_GROUNDED_MODEL
GEMINI_FAST_FALLBACK_MODEL = os.environ.get("GEMINI_FAST_FALLBACK_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"

BUYING_MODEL_MODE = os.environ.get("SCANNER_BUYING_MODEL_MODE", "quality").strip().lower() or "quality"


def dedupe_chain(*models: str | None) -> tuple[str, ...]:
    seen: set[str] = set()
    chain: list[str] = []
    for model in models:
        model = str(model or "").strip().strip('"').strip("'")
        if not model or model in seen:
            continue
        seen.add(model)
        chain.append(model)
    return tuple(chain)


def buying_generation_chain(mode: str | None = None) -> tuple[str, ...]:
    mode = (mode or BUYING_MODEL_MODE or "quality").strip().lower()
    override = BUYING_GENERATION_MODEL_OVERRIDE
    if mode == "economy":
        return dedupe_chain(override, GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)
    if mode == "balanced":
        return dedupe_chain(override, CLAUDE_SONNET_MODEL, GPT_REASONING_MODEL)
    if mode == "board_grade":
        return dedupe_chain(override, GPT_REASONING_MODEL, CLAUDE_OPUS_MODEL, CLAUDE_SONNET_MODEL)
    return dedupe_chain(override, GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL, CLAUDE_OPUS_MODEL)


def buying_gate_chain(_mode: str | None = None) -> tuple[str, ...]:
    return dedupe_chain(GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)


def json_repair_chain(_mode: str | None = None) -> tuple[str, ...]:
    return dedupe_chain(GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)


def source_claim_chain() -> tuple[str, ...]:
    return dedupe_chain(GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)


MODEL_POLICIES: dict[str, dict[str, Any]] = {
    "grounded_acquisition": {
        "primary": GEMINI_GROUNDED_MODEL,
        "thinking_level": "low",
        "availability_fallback": (GPT_REASONING_MODEL, GEMINI_FAST_FALLBACK_MODEL),
        "gpt_reasoning_effort": "low",
        "gpt_verbosity": "low",
    },
    "json_repair": {
        "chain": json_repair_chain(),
        "effort": "low",
        "verbosity": "low",
        "terminal": "deterministic_fallback",
    },
    "source_claim_validation": {
        "chain": source_claim_chain(),
        "effort": "low",
        "verbosity": "low",
        "terminal": "manual_or_deterministic_review",
    },
    "forensic_jury": {
        "primary": GPT_REASONING_MODEL,
        "effort": "medium",
        "verbosity": "medium",
        "fallback": CLAUDE_SONNET_MODEL,
        "terminal": "compact_deterministic_rescue",
    },
    "gpt_web_repair": {
        "primary": GPT_REASONING_MODEL,
        "effort": "low",
        "verbosity": "low",
        "fallback": GEMINI_FAST_FALLBACK_MODEL,
    },
    "buying_insight_generation": {
        "mode": BUYING_MODEL_MODE,
        "chain": buying_generation_chain(BUYING_MODEL_MODE),
        "qualification": {"effort": "high", "verbosity": "medium", "max_output_tokens": 9000},
        "per_signal": {"effort": "medium", "verbosity": "medium", "max_output_tokens": 6000},
        "cleanup": {"effort": "low", "verbosity": "low", "max_output_tokens": 5000},
    },
    "buying_insight_gate": {
        "chain": buying_gate_chain(),
        "effort": "low",
        "verbosity": "low",
        "max_output_tokens": 5000,
        "timeout_seconds": 90,
    },
    "buying_insight_gate_decision": {
        "chain": buying_gate_chain(),
        "effort": "low",
        "verbosity": "low",
        "max_output_tokens": 5000,
        "timeout_seconds": 90,
    },
    "buying_insight_gate_repair": {
        "chain": buying_gate_chain(),
        "effort": "medium",
        "verbosity": "low",
        "max_output_tokens": 6000,
        "timeout_seconds": 90,
    },
    "buying_insight_gate_reanalysis": {
        "chain": dedupe_chain(GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL),
        "effort": "medium",
        "verbosity": "medium",
        "max_output_tokens": 7000,
        "timeout_seconds": 120,
    },
    "general_summary": {
        "primary": GPT_REASONING_MODEL,
        "effort": "medium",
        "verbosity": "medium",
        "independent_review": CLAUDE_SONNET_MODEL,
    },
    "detailed_report": {
        "standard": CLAUDE_SONNET_MODEL,
        "complex": GPT_REASONING_MODEL,
        "premium": CLAUDE_OPUS_MODEL,
        "completion_repair": DETAILED_COMPLETION_MODEL,
        "completion_effort": "medium",
        "completion_verbosity": "high",
        "completion_max_output_tokens": 16000,
    },
    "activation_strategic_synthesis": {
        "primary": GPT_PRO_MODEL,
        "reasoning_mode": GPT_PRO_REASONING_MODE,
        "effort": "high",
        "verbosity": "medium",
        "max_output_tokens": 24000,
        "fallback": CLAUDE_SONNET_MODEL,
    },
    "cross_impact_interpretation": {
        "primary": GPT_PRO_MODEL,
        "reasoning_mode": GPT_PRO_REASONING_MODE,
        "effort": "high",
        "verbosity": "high",
        "max_output_tokens": 24000,
        "guardrails": ("numeric", "structure", "public_output_hygiene"),
    },
    "observatory": {
        "l1": GPT_PRO_MODEL,
        "l2": GPT_PRO_MODEL,
        "reasoning_mode": GPT_PRO_REASONING_MODE,
        "effort": "high",
        "verbosity": "high",
        "max_output_tokens": 24000,
        "l3": CLAUDE_OPUS_MODEL,
        "repair": json_repair_chain(),
    },
}


def policy_for(task: str) -> dict[str, Any]:
    return dict(MODEL_POLICIES.get(task, {}))
