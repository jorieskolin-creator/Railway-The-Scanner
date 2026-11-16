"""No-web Strategic Synthesis cognitive parity and deterministic output normalization.

This module runs the Strategic Synthesis stage on verified durable Foresight
(`SynthesisInputProjection`) and upstream Diagnosis, five-Jury, and Intent
outputs without live web search, `main.py` orchestration, or Vercel Blob storage:

1. Rebuilds the deterministic generation manifest (`attach_evidence_graph`,
   `evidence_prompt_packet`, `build_classification_namespaces`,
   `extract_foresight_links`, and `format_foresight_for_synthesis`) from
   verified durable Jury findings, Diagnosis summary/risks, and Foresight
   forces using offline `FALLBACK_FRAMEWORK + get_label_governance_prompt()`.
2. Executes `policy_for("activation_strategic_synthesis")` (`GPT_PRO_MODEL`
   with `reasoning_mode="pro"`, `reasoning_effort="high"`,
   `max_output_tokens=16000` -> `CLAUDE_SONNET_MODEL` high-effort fallback),
   hard-locking `web_search=False`.
3. Raises explicit `SynthesisCognitiveUnavailableError` when all providers fail,
   return unparseable output, or emit an empty `strategic_synthesis` string
   (Reliability Plan Rule #6: provider unavailability is never silently
   converted to `passed`).
4. Deterministically sanitizes proof-gap wording (`sanitize_inference_wording`),
   strips archetype labels (`Thriving`, `Modernizing`, `Cash Cow`, `At Risk`),
   internal `TAC-x`/`DIM-x` codes, and leaked `[A]`–`[E]` claim-tier tags,
   and applies modal foresight wording (`sanitize_future_insight`) so macro
   pressure never asserts company action.
"""

from __future__ import annotations

from datetime import date
import json
import re
from typing import Any, Callable, Mapping, Protocol

from evidence_contracts import build_classification_namespaces
from evidence_graph import attach_evidence_graph, evidence_prompt_packet
from finland_list import get_company_metadata
from foresight_guardrails import sanitize_future_insight
from knowledge_base import FALLBACK_FRAMEWORK, get_label_governance_prompt
from llm_clients import call_anthropic_text, call_openai_text
from model_policy import (
    CLAUDE_SONNET_MODEL,
    GPT_PRO_MODEL,
    GPT_PRO_REASONING_MODE,
    policy_for,
)
from reality_check import sanitize_inference_wording
from scan_synthesis_checkpoint import SynthesisInputProjection
from scanner_two import extract_json_from_text
from strategic_foresight import (
    extract_foresight_links,
    format_foresight_for_synthesis,
)


STRATEGIC_SYNTHESIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"strategic_synthesis": {"type": "string"}},
    "required": ["strategic_synthesis"],
    "additionalProperties": False,
}

_ARCHETYPE_PROSE_REPLACEMENTS = {
    "Thriving": "financially and operationally strong profile",
    "Modernizing": "modernizing but financially constrained profile",
    "Cash Cow": "financially capable but operationally under-proven profile",
    "At Risk": "financially and operationally pressured profile",
}

_CLAIM_TIER_PREFIX_RE = re.compile(r"\[(?:A|B|C|D|E)\]\s*")
_INTERNAL_CODE_PAREN_RE = re.compile(r"\s*\(\s*(?:TAC|DIM)-\d{1,4}\s*\)")
_INTERNAL_CODE_RE = re.compile(r"\b(?:TAC|DIM)-\d{1,4}\b")


class SynthesisCognitiveUnavailableError(RuntimeError):
    """Raised when Strategic Synthesis cannot obtain a usable response from any provider."""


class SynthesisContextLoader(Protocol):
    def load_run(self, *, run_id: str) -> Any: ...
    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> Any: ...


OpenAINoWebCaller = Callable[..., str | Mapping[str, Any] | None]
AnthropicNoWebCaller = Callable[..., str | Mapping[str, Any] | None]


def sanitize_synthesis_prose(
    value: str,
    *,
    report_date: date | None = None,
) -> str:
    """Idempotently sanitize Strategic Synthesis prose while preserving modal discipline."""

    text = str(sanitize_inference_wording(value or ""))
    text, _ = sanitize_future_insight(text, report_date)
    for label, replacement in _ARCHETYPE_PROSE_REPLACEMENTS.items():
        text = re.sub(rf"\b{re.escape(label)}\b", replacement, text)
        text = re.sub(rf"\b{re.escape(label.upper())}\b", replacement, text)
    text = _CLAIM_TIER_PREFIX_RE.sub("", text)
    text = _INTERNAL_CODE_PAREN_RE.sub("", text)
    text = _INTERNAL_CODE_RE.sub("", text)
    text = re.sub(r"\s*\(\s*\)", "", text)
    return text.strip()


def build_synthesis_prompt(
    projection: SynthesisInputProjection,
    *,
    company_name: str,
    domain: str = "Other",
    diagnosis_result: Mapping[str, Any] | None = None,
    jury_result: Mapping[str, Any] | None = None,
    intent_result: Mapping[str, Any] | None = None,
    knowledge_context: str | None = None,
) -> str:
    """Build the bounded Strategic Synthesis prompt from verified durable outputs."""

    diag = dict(diagnosis_result or {})
    reality_score = (
        int(diag["reality_score"])
        if type(diag.get("reality_score")) is int
        else 50
    )
    intent_score = (
        int(intent_result["score"])
        if isinstance(intent_result, Mapping) and type(intent_result.get("score")) is int
        else 50
    )
    archetype = str(diag.get("archetype") or "").strip() or None
    summary = str(diag.get("diagnostic_summary") or "").strip()
    risks = [
        str(item)
        for item in (diag.get("evidence_based_risks") or [])
        if isinstance(item, str) and item.strip()
    ]
    diagnosis_status = "generated" if summary else "unavailable"

    evidence_links = _evidence_links_from_jury(jury_result)
    foresight_data = dict(projection.foresight_result)
    foresight_links = extract_foresight_links(
        {**foresight_data, "kb_first": True},
        company_name=company_name,
        domain=domain,
    )
    graph_input: dict[str, Any] = {
        "evidence_links": evidence_links,
        "foresight_links": foresight_links,
        "strategic_foresight": foresight_data,
        "evidence_absence_signals": [],
    }
    attach_evidence_graph(graph_input)
    generation_manifest = graph_input.get("evidence_manifest", [])
    activation_evidence_packet = evidence_prompt_packet(generation_manifest, limit=18)

    classification_context = build_classification_namespaces(
        adaptability_archetype=archetype,
        foresight_force_profile={
            "source_contract": "strategic_foresight_macro_context",
            "evidence_kind": "foresight_context",
            "company_fact_eligible": False,
            "not_evidence_of_company_action": True,
        },
    )
    foresight_context = format_foresight_for_synthesis(
        foresight_data,
        company_name,
        domain,
    )
    framework = (
        knowledge_context.strip()
        if isinstance(knowledge_context, str) and knowledge_context.strip()
        else (FALLBACK_FRAMEWORK + get_label_governance_prompt()).strip()
    )

    activation_context = f"""COMPANY: {company_name}

### GOVERNED CURRENT CONTEXT
Reality score: {reality_score}
Intent score: {intent_score}
Diagnosis status: {diagnosis_status}
Diagnosis: {summary if diagnosis_status == "generated" else "Unavailable; use governed evidence directly."}
Evidence-based risks: {json.dumps(risks, ensure_ascii=False)}

### KNOWLEDGE BASE BASELINE — MANDATORY
{framework}

### CLASSIFICATION NAMESPACES
{json.dumps(classification_context, ensure_ascii=False)}

### IMMUTABLE EVIDENCE MANIFEST
{activation_evidence_packet}
{foresight_context}
"""

    return activation_context + """
### TASK: STRATEGIC SYNTHESIS ONLY
Write 300-500 words as exactly 4-5 short paragraphs separated by \\n\\n inside the JSON string.
Paragraph 1: current adaptation thesis. Paragraph 2: 2-3 strategic tensions.
Paragraph 3: at least two external pressure/readiness links. Paragraph 4: financial
capacity and at least two time horizons. Optional paragraph 5: invalidation condition.
Use evidence IDs already present. Macro pressure never proves company action. Treat
absence as a proof gap, not proof of absence. Do not write archetype labels, sales pitch,
WHY/WHAT, section headings, Scanner comparisons, or new facts.
Return only the schema-constrained strategic_synthesis object.
"""


def normalize_synthesis_payload(
    payload: Mapping[str, Any] | object,
    *,
    report_date: date | None = None,
) -> dict[str, Any]:
    """Validate and deterministically sanitize a Strategic Synthesis model payload."""

    if not isinstance(payload, Mapping):
        return {"malformed_synthesis_payload": True}
    raw_text = payload.get("strategic_synthesis")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return {"malformed_synthesis_payload": True}
    cleaned = sanitize_synthesis_prose(raw_text, report_date=report_date)
    if not cleaned:
        return {"malformed_synthesis_payload": True}
    return {"strategic_synthesis": cleaned}


def analyze_synthesis_projection_with_cognition(
    projection: SynthesisInputProjection,
    *,
    company_name: str,
    domain: str = "Other",
    diagnosis_result: Mapping[str, Any] | None = None,
    jury_result: Mapping[str, Any] | None = None,
    intent_result: Mapping[str, Any] | None = None,
    knowledge_context: str | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
    report_date: date | None = None,
) -> dict[str, Any]:
    """Execute the high-effort no-web Strategic Synthesis chain on verified durable inputs."""

    normalized_company = str(company_name or "").strip()
    if not normalized_company:
        raise ValueError("company_name is required for Strategic Synthesis cognitive analysis.")
    if not isinstance(projection.foresight_result, Mapping) or not projection.foresight_result:
        raise ValueError("Strategic Synthesis cognition requires a compact Foresight result.")

    synth_policy = dict(model_policy or policy_for("activation_strategic_synthesis"))
    primary_model = str(synth_policy.get("primary") or GPT_PRO_MODEL).strip()
    reasoning_mode = str(synth_policy.get("reasoning_mode") or GPT_PRO_REASONING_MODE).strip()
    effort = str(synth_policy.get("effort") or "high").strip()
    verbosity = str(synth_policy.get("verbosity") or "medium").strip()
    max_output_tokens = int(synth_policy.get("max_output_tokens") or 16000)
    fallback_model = str(synth_policy.get("fallback") or CLAUDE_SONNET_MODEL).strip()

    openai_fn = openai_caller or call_openai_text
    anthropic_fn = anthropic_caller or call_anthropic_text

    prompt = build_synthesis_prompt(
        projection,
        company_name=normalized_company,
        domain=domain,
        diagnosis_result=diagnosis_result,
        jury_result=jury_result,
        intent_result=intent_result,
        knowledge_context=knowledge_context,
    )

    errors: list[str] = []
    parsed_payload = _try_openai_synthesis(
        openai_fn,
        prompt=prompt,
        model=primary_model,
        max_output_tokens=max_output_tokens,
        reasoning_mode=reasoning_mode,
        effort=effort,
        verbosity=verbosity,
        errors=errors,
    )
    if parsed_payload is None:
        parsed_payload = _try_anthropic_synthesis(
            anthropic_fn,
            prompt=prompt,
            model=fallback_model,
            max_tokens=5000,
            effort="high",
            errors=errors,
        )

    if parsed_payload is None:
        detail = "; ".join(errors) if errors else "no parseable provider payload"
        raise SynthesisCognitiveUnavailableError(
            f"No-web Strategic Synthesis unavailable for {normalized_company}: {detail}"
        )

    return normalize_synthesis_payload(parsed_payload, report_date=report_date)


def build_no_web_synthesis_analyzer(
    *,
    company_name: str | None = None,
    domain: str | None = None,
    diagnosis_result: Mapping[str, Any] | None = None,
    jury_result: Mapping[str, Any] | None = None,
    intent_result: Mapping[str, Any] | None = None,
    knowledge_context: str | None = None,
    context_loader: SynthesisContextLoader | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
    report_date: date | None = None,
) -> Callable[[SynthesisInputProjection], dict[str, Any]]:
    """Build an injectable `SynthesisAnalyzer` resolving durable upstream context."""

    def _analyze(projection: SynthesisInputProjection) -> dict[str, Any]:
        resolved_company = (company_name or "").strip()
        resolved_domain = (domain or "").strip()
        resolved_diag = diagnosis_result
        resolved_jury = jury_result
        resolved_intent = intent_result

        if context_loader is not None:
            if not resolved_company and hasattr(context_loader, "load_run"):
                loaded_run = context_loader.load_run(run_id=projection.run_id)
                if loaded_run is not None:
                    resolved_company = str(
                        getattr(loaded_run, "company_name", "") or ""
                    ).strip()
            if (
                resolved_diag is None or resolved_jury is None or resolved_intent is None
            ) and hasattr(context_loader, "load_checkpoint"):
                loaded_diag, loaded_jury, loaded_intent = _load_upstream_chain_results(
                    context_loader,
                    run_id=projection.run_id,
                    foresight_checkpoint_ref=projection.foresight_checkpoint_ref,
                )
                if resolved_diag is None:
                    resolved_diag = loaded_diag
                if resolved_jury is None:
                    resolved_jury = loaded_jury
                if resolved_intent is None:
                    resolved_intent = loaded_intent

        if not resolved_company:
            raise ValueError(
                "company_name or a context_loader with a persisted ScanRun is required for Synthesis cognition."
            )
        if not resolved_domain:
            meta = get_company_metadata(resolved_company) or {}
            resolved_domain = str(meta.get("domain") or "Other").strip() or "Other"

        return analyze_synthesis_projection_with_cognition(
            projection,
            company_name=resolved_company,
            domain=resolved_domain,
            diagnosis_result=resolved_diag,
            jury_result=resolved_jury,
            intent_result=resolved_intent,
            knowledge_context=knowledge_context,
            openai_caller=openai_caller,
            anthropic_caller=anthropic_caller,
            model_policy=model_policy,
            report_date=report_date,
        )

    return _analyze


def _load_upstream_chain_results(
    context_loader: Any,
    *,
    run_id: str,
    foresight_checkpoint_ref: Mapping[str, object],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    diag_result: Mapping[str, Any] | None = None
    jury_result: Mapping[str, Any] | None = None
    intent_result: Mapping[str, Any] | None = None

    fs_attempt = foresight_checkpoint_ref.get("attempt")
    if type(fs_attempt) is not int or fs_attempt < 1:
        return diag_result, jury_result, intent_result
    fs_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="foresight", attempt=fs_attempt
    )
    if fs_cp is None:
        return diag_result, jury_result, intent_result

    diag_ref = fs_cp.snapshot.get("diagnosis_checkpoint_ref")
    if not isinstance(diag_ref, Mapping) or type(diag_ref.get("attempt")) is not int:
        return diag_result, jury_result, intent_result
    diag_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="diagnosis", attempt=diag_ref["attempt"]
    )
    if diag_cp is None:
        return diag_result, jury_result, intent_result
    if isinstance(diag_cp.snapshot.get("diagnosis_result"), Mapping):
        diag_result = dict(diag_cp.snapshot["diagnosis_result"])

    jury_ref = diag_cp.snapshot.get("jury_checkpoint_ref")
    if not isinstance(jury_ref, Mapping) or type(jury_ref.get("attempt")) is not int:
        return diag_result, jury_result, intent_result
    jury_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="jury_analysis", attempt=jury_ref["attempt"]
    )
    if jury_cp is None:
        return diag_result, jury_result, intent_result
    if isinstance(jury_cp.snapshot.get("jury_result"), Mapping):
        jury_result = dict(jury_cp.snapshot["jury_result"])

    intent_ref = jury_cp.snapshot.get("intent_checkpoint_ref")
    if not isinstance(intent_ref, Mapping) or type(intent_ref.get("attempt")) is not int:
        return diag_result, jury_result, intent_result
    intent_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="intent", attempt=intent_ref["attempt"]
    )
    if intent_cp is not None and isinstance(intent_cp.snapshot.get("intent_result"), Mapping):
        intent_result = dict(intent_cp.snapshot["intent_result"])

    return diag_result, jury_result, intent_result


def _evidence_links_from_jury(jury_result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(jury_result, Mapping):
        return []
    links: list[dict[str, Any]] = []
    for lane in jury_result.get("juries") or []:
        if not isinstance(lane, Mapping):
            continue
        for finding in lane.get("findings") or []:
            if not isinstance(finding, Mapping):
                continue
            item = dict(finding)
            support_level = str(item.get("support_level") or "").lower()
            item.setdefault("verified", True)
            item.setdefault("url_verified", bool(item.get("url")))
            item.setdefault("content_verified", bool(item.get("source_quote")))
            item.setdefault("analysis_verified", support_level == "exact")
            item.setdefault("generation_eligible", support_level == "exact")
            item.setdefault(
                "promotion_status",
                "generation_eligible" if support_level == "exact" else "scoped_context",
            )
            item.setdefault(
                "source_use_status",
                "supported_fact" if support_level == "exact" else "scoped_context",
            )
            links.append(item)
    return links


def _try_openai_synthesis(
    openai_fn: OpenAINoWebCaller,
    *,
    prompt: str,
    model: str,
    max_output_tokens: int,
    reasoning_mode: str,
    effort: str,
    verbosity: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        raw = openai_fn(
            prompt,
            model=model,
            max_output_tokens=max_output_tokens,
            timeout=110,
            web_search=False,
            max_retries=0,
            reasoning_mode=reasoning_mode,
            reasoning_effort=effort,
            text_verbosity=verbosity,
            json_schema=STRATEGIC_SYNTHESIS_JSON_SCHEMA,
            json_schema_name="scanner_strategic_synthesis",
            log_label="Strategic Synthesis",
        )
        parsed = _parse_json_object(raw)
        if parsed is not None:
            return parsed
        errors.append(f"Strategic Synthesis:{model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"Strategic Synthesis:{model}:{type(error).__name__}")
    return None


def _try_anthropic_synthesis(
    anthropic_fn: AnthropicNoWebCaller,
    *,
    prompt: str,
    model: str,
    max_tokens: int,
    effort: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        raw = anthropic_fn(
            prompt,
            model=model,
            max_tokens=max_tokens,
            timeout=45,
            web_search=False,
            max_retries=0,
            reasoning_effort=effort,
            json_schema=STRATEGIC_SYNTHESIS_JSON_SCHEMA,
            json_schema_name="scanner_strategic_synthesis_fallback",
            log_label="Strategic Synthesis Sonnet Fallback",
        )
        parsed = _parse_json_object(raw)
        if parsed is not None:
            return parsed
        errors.append(f"Strategic Synthesis Sonnet Fallback:{model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"Strategic Synthesis Sonnet Fallback:{model}:{type(error).__name__}")
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
