"""Central model policy defaults for Scanner.

The policy names approved production models, keeps expensive Pro usage explicit,
and prevents distrusted lightweight models from becoming silent fallbacks.
"""

from __future__ import annotations

import os
from typing import Any


_DEFAULT_POLICY_VERSION = "model-policy-v5-sonnet55"
MODEL_POLICY_VERSION = os.environ.get("SCANNER_MODEL_POLICY_VERSION", "").strip() or _DEFAULT_POLICY_VERSION

_DEFAULT_REASONING_MODEL = "gpt-6.1-sol"
_DEFAULT_VERIFIER_MODEL = "gpt-6.1-terra"
_DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
_DEFAULT_GROUNDED_MODEL = "gemini-3.8-flash"
_MODEL_REPLACEMENTS = {
    "gpt-5.6-sol": _DEFAULT_REASONING_MODEL,
    "gpt-5.4": _DEFAULT_VERIFIER_MODEL,
    "claude-opus-5": _DEFAULT_CLAUDE_MODEL,
    "claude-sonnet-5": _DEFAULT_CLAUDE_MODEL,
    "gemini-3.5-flash": _DEFAULT_GROUNDED_MODEL,
}


def resolve_model_id(model: str) -> str:
    """Resolve old model pins to their configured replacements."""
    model = model.strip().strip('"').strip("'")
    return _MODEL_REPLACEMENTS.get(model, model)


def _configured_model(env_var: str, default: str = "") -> str:
    return resolve_model_id(os.environ.get(env_var, "")) or default


GPT_REASONING_MODEL = _configured_model("SCANNER_DEFAULT_FRONTIER_MODEL", _DEFAULT_REASONING_MODEL)
# The premium OpenAI route uses Pro reasoning mode on Sol.
GPT_PRO_MODEL = _configured_model("SCANNER_PREMIUM_MODEL", _DEFAULT_REASONING_MODEL)
GPT_PRO_REASONING_MODE = os.environ.get("SCANNER_PREMIUM_REASONING_MODE", "pro").strip() or "pro"
GPT_VERIFIER_MODEL = _configured_model("SCANNER_VALIDATOR_MODEL", _DEFAULT_VERIFIER_MODEL)
GPT_EXPERIMENTAL_MODEL = _configured_model("SCANNER_GPT56_SOL_MODEL", _DEFAULT_REASONING_MODEL)
ENABLE_GPT56_SOL_TEST = os.environ.get("SCANNER_ENABLE_GPT56_SOL_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
BUYING_GENERATION_MODEL_OVERRIDE = (
    _configured_model("SCANNER_BUYING_GENERATION_MODEL")
    or (GPT_EXPERIMENTAL_MODEL if ENABLE_GPT56_SOL_TEST else "")
)
DETAILED_COMPLETION_MODEL = _configured_model("SCANNER_DETAILED_COMPLETION_MODEL", GPT_REASONING_MODEL)

CLAUDE_PREMIUM_MODEL = (
    _configured_model("CLAUDE_PREMIUM_MODEL")
    or _configured_model("CLAUDE_OPUS_MODEL", _DEFAULT_CLAUDE_MODEL)
)
CLAUDE_SONNET_MODEL = _configured_model("CLAUDE_SONNET_MODEL", _DEFAULT_CLAUDE_MODEL)

GEMINI_GROUNDED_MODEL = _configured_model("SCANNER_GROUNDED_RESEARCH_MODEL", _DEFAULT_GROUNDED_MODEL)
GEMINI_HARVEST_MODEL = _configured_model("GEMINI_HARVEST_MODEL", GEMINI_GROUNDED_MODEL)
GEMINI_FORENSIC_MODEL = _configured_model("GEMINI_FORENSIC_MODEL", GEMINI_GROUNDED_MODEL)
GEMINI_FAST_FALLBACK_MODEL = _configured_model("GEMINI_FAST_FALLBACK_MODEL", _DEFAULT_GROUNDED_MODEL)

BUYING_MODEL_MODE = os.environ.get("SCANNER_BUYING_MODEL_MODE", "quality").strip().lower() or "quality"


def dedupe_chain(*models: str | None) -> tuple[str, ...]:
    seen: set[str] = set()
    chain: list[str] = []
    for model in models:
        model = resolve_model_id(str(model or ""))
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
        return dedupe_chain(override, GPT_REASONING_MODEL, CLAUDE_PREMIUM_MODEL, CLAUDE_SONNET_MODEL)
    return dedupe_chain(override, GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL, CLAUDE_PREMIUM_MODEL)


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
        "availability_fallback": dedupe_chain(GEMINI_GROUNDED_MODEL, GPT_REASONING_MODEL, GEMINI_FAST_FALLBACK_MODEL)[1:],
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
    "ai_diagnosis": {
        "effort": "high",
        "max_output_tokens": 8000,
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
        "chain": dedupe_chain(CLAUDE_SONNET_MODEL, GPT_VERIFIER_MODEL),
        "effort": "medium",
        "verbosity": "low",
        "max_output_tokens": 4000,
        "timeout_seconds": 90,
        "max_batch_items": 5,
        "max_batch_chars": 24000,
    },
    "buying_insight_gate_reanalysis": {
        "chain": dedupe_chain(GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL),
        "effort": "medium",
        "verbosity": "medium",
        "max_output_tokens": 7000,
        "timeout_seconds": 120,
    },
    "buying_insight_language_qa": {
        "chain": dedupe_chain(CLAUDE_SONNET_MODEL, GPT_VERIFIER_MODEL),
        "effort": "medium",
        "verbosity": "low",
        "max_output_tokens": 3500,
        "timeout_seconds": 75,
        "max_batch_items": 8,
        "max_batch_chars": 20000,
    },
    "general_summary": {
        "primary": CLAUDE_SONNET_MODEL,
        "fallback": GPT_REASONING_MODEL,
        "effort": "medium",
        "verbosity": "medium",
        "independent_review": GPT_VERIFIER_MODEL,
    },
    "detailed_report": {
        "standard": CLAUDE_SONNET_MODEL,
        "complex": GPT_REASONING_MODEL,
        "premium": CLAUDE_PREMIUM_MODEL,
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
        "max_output_tokens": 16000,
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
        "l3": CLAUDE_PREMIUM_MODEL,
        "repair": json_repair_chain(),
    },
}


def policy_for(task: str) -> dict[str, Any]:
    return dict(MODEL_POLICIES.get(task, {}))
