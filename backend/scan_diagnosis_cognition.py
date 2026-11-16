"""No-web AI Diagnosis cognitive parity and deterministic output normalization.

This module runs the Scanner AI Diagnosis synthesis path on verified durable
Jury (`DiagnosisInputProjection`) and upstream Intent outputs without touching
live web search, scraping, `main.py`, or Blob storage:

1. Formats verified five-Jury findings, contradictions, missing coverage, and
   upstream Intent metrics with offline Knowledge Base label-governance and
   claim-tier guardrails (`FALLBACK_FRAMEWORK`, `get_label_governance_prompt`,
   `build_shared_guardrails`).
2. Executes the `ai_diagnosis` high-effort provider sequence
   (`CLAUDE_PREMIUM_MODEL` primary -> `GPT_REASONING_MODEL` fallback ->
   conservative `CLAUDE_SONNET_MODEL` retry), hard-locking `web_search=False`.
3. Raises `DiagnosisCognitiveUnavailableError` when all providers fail, return
   unparseable output, or emit `"AI Summary Unavailable"` (Reliability Plan
   Rule #6: provider unavailability is never silently converted to `passed`).
4. Deterministically sanitizes proof-gap wording (`sanitize_inference_wording`),
   strips archetype labels, internal `TAC-xxx` / `DIM-xxx` codes, and leaked
   `[A]`–`[E]` tier prefixes from prose, and mechanically derives `archetype`
   from `(reality_score, financial_or_intent_score)`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping, Protocol

from knowledge_base import FALLBACK_FRAMEWORK, get_label_governance_prompt
from llm_clients import call_anthropic_text, call_openai_text
from model_policy import (
    CLAUDE_PREMIUM_MODEL,
    CLAUDE_SONNET_MODEL,
    GPT_REASONING_MODEL,
    policy_for,
)
from prompts import build_shared_guardrails
from reality_check import sanitize_inference_wording
from scan_diagnosis_checkpoint import DIAGNOSIS_ARCHETYPES, DiagnosisInputProjection
from scanner_two import extract_json_from_text


DIAGNOSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "diagnostic_summary": {"type": "string"},
        "evidence_based_risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "reality_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "archetype": {
            "type": "string",
            "enum": list(DIAGNOSIS_ARCHETYPES),
        },
    },
    "required": [
        "diagnostic_summary",
        "evidence_based_risks",
        "reality_score",
        "archetype",
    ],
}

_CONSERVATIVE_SONNET_SYSTEM_PREFIX = (
    "CRITICAL: You are a CONSERVATIVE analyst. When in doubt, say 'insufficient evidence.' "
    "Do NOT extrapolate. Do NOT infer organizational problems. Do NOT sound deeper than "
    "your evidence supports. A boring, accurate summary beats an impressive, fabricated one. "
    "NEVER invent leadership gaps, morale issues, succession problems, or strategic conclusions "
    "from minor details like tool names in job ads."
)

_ARCHETYPE_PROSE_REPLACEMENTS = {
    "Thriving": "financially and operationally strong profile",
    "Modernizing": "modernizing but financially constrained profile",
    "Cash Cow": "financially capable but operationally under-proven profile",
    "At Risk": "financially and operationally pressured profile",
}

_CLAIM_TIER_PREFIX_RE = re.compile(r"\[(?:A|B|C|D|E)\]\s*")
_INTERNAL_CODE_PAREN_RE = re.compile(r"\s*\(\s*(?:TAC|DIM)-\d{1,4}\s*\)")
_INTERNAL_CODE_RE = re.compile(r"\b(?:TAC|DIM)-\d{1,4}\b")


class DiagnosisCognitiveUnavailableError(RuntimeError):
    """Raised when AI Diagnosis cannot obtain a usable response from any provider."""


class DiagnosisContextLoader(Protocol):
    def load_run(self, *, run_id: str) -> Any: ...
    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> Any: ...


AnthropicNoWebCaller = Callable[..., str | Mapping[str, Any] | None]
OpenAINoWebCaller = Callable[..., str | Mapping[str, Any] | None]


def derive_diagnosis_archetype(
    *,
    reality_score: int,
    financial_or_intent_score: int,
) -> str:
    """Mechanically derive the audit archetype from `(reality_score, financial_or_intent_score)`."""

    if reality_score >= 50:
        return "Thriving" if financial_or_intent_score >= 50 else "Modernizing"
    return "Cash Cow" if financial_or_intent_score >= 50 else "At Risk"


def compute_jury_baseline_reality_score(jury_result: Mapping[str, Any]) -> int:
    """Compute neutral-anchored baseline reality score from judged five-Jury lanes."""

    juries = jury_result.get("juries")
    if not isinstance(juries, list):
        return 50
    adjustments = [
        int(lane.get("reality_score_adjustment", 0))
        for lane in juries
        if isinstance(lane, Mapping)
        and isinstance(lane.get("findings"), list)
        and len(lane["findings"]) > 0
        and type(lane.get("reality_score_adjustment")) is int
    ]
    avg_adjustment = round(sum(adjustments) / len(adjustments)) if adjustments else 0
    return max(0, min(100, 50 + avg_adjustment))


def sanitize_diagnosis_prose(value: str) -> str:
    """Idempotently sanitize proof-gap wording, archetype labels, tier tags, and TAC/DIM codes."""

    sanitized = str(sanitize_inference_wording(value or ""))
    for label, replacement in _ARCHETYPE_PROSE_REPLACEMENTS.items():
        sanitized = re.sub(rf"\b{re.escape(label)}\b", replacement, sanitized)
        sanitized = re.sub(rf"\b{re.escape(label.upper())}\b", replacement, sanitized)
    sanitized = _CLAIM_TIER_PREFIX_RE.sub("", sanitized)
    sanitized = _INTERNAL_CODE_PAREN_RE.sub("", sanitized)
    sanitized = _INTERNAL_CODE_RE.sub("", sanitized)
    sanitized = re.sub(r"\s*\(\s*\)", "", sanitized)
    return sanitized.strip()


def build_diagnosis_prompt(
    projection: DiagnosisInputProjection,
    *,
    company_name: str,
    intent_result: Mapping[str, Any] | None = None,
    financial_index: int | None = None,
    knowledge_context: str | None = None,
) -> str:
    """Construct the bounded no-web AI Diagnosis prompt from verified durable inputs."""

    framework = (
        knowledge_context.strip()
        if isinstance(knowledge_context, str) and knowledge_context.strip()
        else (FALLBACK_FRAMEWORK + get_label_governance_prompt()).strip()
    )
    guardrails = build_shared_guardrails(
        include_diagnostic=True,
        include_foresight=False,
        include_claim_tier=True,
    ).strip()

    intent_score = _extract_intent_score(intent_result)
    fin_for_archetype = (
        max(0, min(100, financial_index))
        if type(financial_index) is int
        else intent_score
    )
    fin_source_label = (
        "actual financial index"
        if type(financial_index) is int
        else "fallback: using intent score"
    )

    evidence_lines, contradictions, missing_coverage, avg_confidence = (
        _format_jury_evidence(projection.jury_result)
    )
    baseline_reality = compute_jury_baseline_reality_score(projection.jury_result)
    intent_block = (
        json.dumps(dict(intent_result), sort_keys=True, ensure_ascii=False)
        if isinstance(intent_result, Mapping)
        else f'{{"score": {intent_score}}}'
    )

    return f"""{guardrails}

### PRIMARY FRAMEWORK (KNOWLEDGE BASE — MANDATORY)
{framework}

### SCAN DATA
Target: {company_name}
Financial Health Index: {fin_for_archetype} ({fin_source_label})
Jury Baseline Reality Score: {baseline_reality}/100
Jury Evidence Confidence: {avg_confidence}/100
Intent Data: {intent_block}

Verified Jury Findings:
{chr(10).join(evidence_lines) if evidence_lines else "- No promoted Jury findings; score dimensions neutrally (10/20) and state evidence constraints."}

Contradictions Observed:
{json.dumps(contradictions, ensure_ascii=False)}

Missing Coverage Gaps:
{json.dumps(missing_coverage, ensure_ascii=False)}

### ANALYSIS PROTOCOL
1. Score each of the 5 Knowledge Base dimensions (0-20) from verified evidence only (no evidence = 10 neutral) and sum to `reality_score` (0-100).
2. Derive `archetype` mechanically from `reality_score` and Financial Health Index ({fin_for_archetype}):
   - reality_score >= 50 and Financial Health Index >= 50 -> "Thriving"
   - reality_score >= 50 and Financial Health Index < 50 -> "Modernizing"
   - reality_score < 50 and Financial Health Index >= 50 -> "Cash Cow"
   - reality_score < 50 and Financial Health Index < 50 -> "At Risk"
   NEVER write archetype labels (Thriving, Modernizing, Cash Cow, At Risk), [A]-[E] tier prefixes, or TAC/DIM codes in prose.
3. Write `diagnostic_summary` as 3-4 neutral current-reality paragraphs separated by \\n\\n. Do NOT include external market trends, sales language, or calls-to-action.
4. Generate 2-4 neutral, evidence-backed risk bullets in `evidence_based_risks` (or an empty array if no concrete risk is supported).

Respond with valid JSON only:
{{"diagnostic_summary": "...", "evidence_based_risks": ["..."], "reality_score": 50, "archetype": "At Risk"}}
"""


def normalize_diagnosis_payload(
    payload: Mapping[str, Any] | object,
    *,
    financial_or_intent_score: int = 50,
) -> dict[str, Any]:
    """Validate and deterministically sanitize a Diagnosis model payload.

    Structurally invalid payloads are returned without coercing invalid types so
    `build_diagnosis_checkpoint` marks the proposal `validation_failed`.
    """

    if not isinstance(payload, Mapping):
        return {"malformed": True}
    raw_summary = payload.get("diagnostic_summary")
    raw_risks = payload.get("evidence_based_risks")
    raw_score = payload.get("reality_score")
    raw_archetype = payload.get("archetype")

    if (
        not isinstance(raw_summary, str)
        or not raw_summary.strip()
        or raw_summary.strip().lower() == "ai summary unavailable"
        or not isinstance(raw_risks, list)
        or any(not isinstance(item, str) for item in raw_risks)
        or type(raw_score) is not int
        or not (0 <= raw_score <= 100)
        or (raw_archetype is not None and raw_archetype not in DIAGNOSIS_ARCHETYPES)
    ):
        return {"malformed_diagnosis_payload": True}

    sanitized_summary = sanitize_diagnosis_prose(raw_summary)
    if not sanitized_summary:
        return {"malformed_diagnosis_payload": True}

    sanitized_risks: list[str] = []
    for item in raw_risks:
        cleaned = sanitize_diagnosis_prose(item)
        if cleaned and cleaned not in sanitized_risks:
            sanitized_risks.append(cleaned)

    clamped_fin = max(0, min(100, int(financial_or_intent_score)))
    derived_archetype = derive_diagnosis_archetype(
        reality_score=raw_score,
        financial_or_intent_score=clamped_fin,
    )
    return {
        "diagnostic_summary": sanitized_summary,
        "evidence_based_risks": sanitized_risks,
        "reality_score": raw_score,
        "archetype": derived_archetype,
    }


def analyze_diagnosis_projection_with_cognition(
    projection: DiagnosisInputProjection,
    *,
    company_name: str,
    intent_result: Mapping[str, Any] | None = None,
    financial_index: int | None = None,
    knowledge_context: str | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the high-effort no-web AI Diagnosis chain on verified durable inputs."""

    normalized_company = str(company_name or "").strip()
    if not normalized_company:
        raise ValueError("company_name is required for Diagnosis cognitive analysis.")
    if not isinstance(projection.jury_result, Mapping) or not projection.jury_result:
        raise ValueError("Diagnosis cognition requires a compact Jury result.")

    diag_policy = dict(model_policy or policy_for("ai_diagnosis"))
    effort = str(diag_policy.get("effort") or "high").strip()
    max_tokens = int(diag_policy.get("max_output_tokens") or 8000)
    primary_claude = str(diag_policy.get("primary") or CLAUDE_PREMIUM_MODEL).strip()
    fallback_gpt = str(diag_policy.get("gpt_fallback") or GPT_REASONING_MODEL).strip()
    fallback_sonnet = str(diag_policy.get("sonnet_fallback") or CLAUDE_SONNET_MODEL).strip()

    anthropic_fn = anthropic_caller or call_anthropic_text
    openai_fn = openai_caller or call_openai_text

    prompt = build_diagnosis_prompt(
        projection,
        company_name=normalized_company,
        intent_result=intent_result,
        financial_index=financial_index,
        knowledge_context=knowledge_context,
    )

    intent_score = _extract_intent_score(intent_result)
    fin_for_archetype = (
        max(0, min(100, financial_index))
        if type(financial_index) is int
        else intent_score
    )

    errors: list[str] = []
    parsed_payload = _try_anthropic_diagnosis(
        anthropic_fn,
        prompt=prompt,
        model=primary_claude,
        max_tokens=max_tokens,
        timeout=70,
        effort=effort,
        schema_name="scanner_ai_diagnosis",
        log_label="Scanner Diagnosis Premium",
        errors=errors,
    )
    if parsed_payload is None:
        parsed_payload = _try_openai_diagnosis(
            openai_fn,
            prompt=prompt,
            model=fallback_gpt,
            max_output_tokens=min(7000, max_tokens),
            timeout=55,
            errors=errors,
        )
    if parsed_payload is None:
        parsed_payload = _try_anthropic_diagnosis(
            anthropic_fn,
            prompt=f"{_CONSERVATIVE_SONNET_SYSTEM_PREFIX}\n\n{prompt}",
            model=fallback_sonnet,
            max_tokens=min(5000, max_tokens),
            timeout=45,
            effort="medium",
            schema_name="scanner_ai_diagnosis_conservative",
            log_label="Scanner Diagnosis Sonnet Fallback",
            errors=errors,
        )

    if parsed_payload is None:
        detail = "; ".join(errors) if errors else "no parseable provider payload"
        raise DiagnosisCognitiveUnavailableError(
            f"No-web AI Diagnosis unavailable for {normalized_company}: {detail}"
        )

    raw_summary_check = parsed_payload.get("diagnostic_summary")
    if (
        isinstance(raw_summary_check, str)
        and raw_summary_check.strip().lower() == "ai summary unavailable"
    ):
        raise DiagnosisCognitiveUnavailableError(
            f"Provider returned placeholder unavailable summary for {normalized_company}."
        )

    return normalize_diagnosis_payload(
        parsed_payload,
        financial_or_intent_score=fin_for_archetype,
    )


def build_no_web_diagnosis_analyzer(
    *,
    company_name: str | None = None,
    intent_result: Mapping[str, Any] | None = None,
    financial_index: int | None = None,
    knowledge_context: str | None = None,
    context_loader: DiagnosisContextLoader | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
) -> Callable[[DiagnosisInputProjection], dict[str, Any]]:
    """Build an injectable `DiagnosisAnalyzer` resolving durable run & Intent context."""

    def _analyze(projection: DiagnosisInputProjection) -> dict[str, Any]:
        resolved_company = (company_name or "").strip()
        resolved_intent: Mapping[str, Any] | None = intent_result

        if context_loader is not None:
            if not resolved_company and hasattr(context_loader, "load_run"):
                loaded_run = context_loader.load_run(run_id=projection.run_id)
                if loaded_run is not None:
                    resolved_company = str(
                        getattr(loaded_run, "company_name", "") or ""
                    ).strip()
            if resolved_intent is None and hasattr(context_loader, "load_checkpoint"):
                resolved_intent = _load_upstream_intent_result(
                    context_loader,
                    run_id=projection.run_id,
                    jury_checkpoint_ref=projection.jury_checkpoint_ref,
                )

        if not resolved_company:
            raise ValueError(
                "company_name or a context_loader with a persisted ScanRun is required for Diagnosis cognition."
            )
        return analyze_diagnosis_projection_with_cognition(
            projection,
            company_name=resolved_company,
            intent_result=resolved_intent,
            financial_index=financial_index,
            knowledge_context=knowledge_context,
            anthropic_caller=anthropic_caller,
            openai_caller=openai_caller,
            model_policy=model_policy,
        )

    return _analyze


def _load_upstream_intent_result(
    context_loader: Any,
    *,
    run_id: str,
    jury_checkpoint_ref: Mapping[str, object],
) -> Mapping[str, Any] | None:
    jury_attempt = jury_checkpoint_ref.get("attempt")
    if type(jury_attempt) is not int or jury_attempt < 1:
        return None
    jury_cp = context_loader.load_checkpoint(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=jury_attempt,
    )
    if jury_cp is None:
        return None
    intent_ref = jury_cp.snapshot.get("intent_checkpoint_ref")
    if not isinstance(intent_ref, Mapping):
        return None
    intent_attempt = intent_ref.get("attempt")
    if type(intent_attempt) is not int or intent_attempt < 1:
        return None
    intent_cp = context_loader.load_checkpoint(
        run_id=run_id,
        stage_name="intent",
        attempt=intent_attempt,
    )
    if intent_cp is None:
        return None
    intent_payload = intent_cp.snapshot.get("intent_result")
    return dict(intent_payload) if isinstance(intent_payload, Mapping) else None


def _try_anthropic_diagnosis(
    anthropic_fn: AnthropicNoWebCaller,
    *,
    prompt: str,
    model: str,
    max_tokens: int,
    timeout: int,
    effort: str,
    schema_name: str,
    log_label: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        raw = anthropic_fn(
            prompt,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
            web_search=False,
            max_retries=0,
            reasoning_effort=effort,
            json_schema=DIAGNOSIS_JSON_SCHEMA,
            json_schema_name=schema_name,
            log_label=log_label,
        )
        parsed = _parse_json_object(raw)
        if parsed is not None:
            return parsed
        errors.append(f"{log_label}:{model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"{log_label}:{model}:{type(error).__name__}")
    return None


def _try_openai_diagnosis(
    openai_fn: OpenAINoWebCaller,
    *,
    prompt: str,
    model: str,
    max_output_tokens: int,
    timeout: int,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        raw = openai_fn(
            prompt,
            model=model,
            max_output_tokens=max_output_tokens,
            timeout=timeout,
            web_search=False,
            max_retries=0,
            reasoning_effort="medium",
            text_verbosity="medium",
            json_schema=DIAGNOSIS_JSON_SCHEMA,
            json_schema_name="scanner_ai_diagnosis",
            log_label="Scanner Diagnosis GPT Fallback",
        )
        parsed = _parse_json_object(raw)
        if parsed is not None:
            return parsed
        errors.append(f"Scanner Diagnosis GPT Fallback:{model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"Scanner Diagnosis GPT Fallback:{model}:{type(error).__name__}")
    return None


def _parse_json_object(raw_value: str | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, Mapping):
        return dict(raw_value)
    if not isinstance(raw_value, str):
        return None
    extracted = extract_json_from_text(raw_value)
    if not extracted:
        return None
    try:
        parsed = json.loads(extracted, strict=False)
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _extract_intent_score(intent_result: Mapping[str, Any] | None) -> int:
    if isinstance(intent_result, Mapping) and type(intent_result.get("score")) is int:
        return max(0, min(100, intent_result["score"]))
    return 50


def _format_jury_evidence(
    jury_result: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str], int]:
    juries = jury_result.get("juries")
    if not isinstance(juries, list):
        return [], [], [], 0
    lines: list[str] = []
    contradictions: list[str] = []
    missing_coverage: list[str] = []
    confidences: list[int] = []
    evd_idx = 1

    for lane in juries:
        if not isinstance(lane, Mapping):
            continue
        lane_name = str(lane.get("jury") or "")
        if type(lane.get("confidence_score")) is int and lane.get("findings"):
            confidences.append(lane["confidence_score"])
        for item in lane.get("contradictions") or []:
            if isinstance(item, str) and item.strip() and item.strip() not in contradictions:
                contradictions.append(item.strip())
        for gap in lane.get("missing_coverage") or []:
            if isinstance(gap, str) and gap.strip() and gap.strip() not in missing_coverage:
                missing_coverage.append(gap.strip())
        for finding in lane.get("findings") or []:
            if not isinstance(finding, Mapping):
                continue
            tier = _claim_tier_for_finding(finding)
            mode = (
                "scoped_context"
                if finding.get("support_level") == "context_only"
                else "hard_fact"
            )
            scope = str(finding.get("entity_scope") or "company")
            evd_id = str(
                finding.get("source_id")
                or finding.get("evidence_package_id")
                or f"EVD-{evd_idx:03d}"
            )
            evd_idx += 1
            claim = str(finding.get("supported_claim") or "").strip()
            quote = str(finding.get("source_quote") or "").strip()
            url = str(finding.get("url") or "").strip()
            lines.append(
                f"- [{tier}] [{evd_id}] (jury={lane_name}; mode={mode}; scope={scope}; "
                f"support={finding.get('support_level')}; url={url}): {claim} "
                f'| quote="{quote[:320]}"'
            )

    avg_conf = round(sum(confidences) / len(confidences)) if confidences else 0
    return lines, contradictions, missing_coverage, avg_conf


def _claim_tier_for_finding(finding: Mapping[str, Any]) -> str:
    source_type = str(finding.get("source_type") or "").lower()
    support_level = str(finding.get("support_level") or "").lower()
    if support_level == "context_only":
        return "D"
    if any(tok in source_type for tok in ("annual", "financial", "filing", "investor", "procurement")):
        return "B"
    if any(tok in source_type for tok in ("analyst", "third_party", "vendor_case", "regulator", "news")):
        return "A"
    if any(tok in source_type for tok in ("career", "job", "culture", "review")):
        return "D"
    return "C"
