"""No-web Verification cognitive parity and deterministic verification-record normalization.

This module executes the Scanner Verification stage on verified durable
Strategic Synthesis (`VerificationInputProjection`) and upstream Foresight,
Diagnosis, five-Jury, and Intent outputs without live web search, `main.py`
orchestration, or Vercel Blob publication:

1. Rebuilds the canonical bibliography and evidence graph (`attach_evidence_graph`,
   `extract_foresight_links`) from verified durable upstream checkpoints and
   `ScanRun.company_name`.
2. Runs deterministic heuristic validation (`validate_summary`) and the
   independent provider-separated fact-checker (`ai_fact_check_summary` with
   `verification_only=True` and `(GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)`).
3. Raises explicit `VerificationCognitiveUnavailableError` when `ai_fact_check_summary`
   reports `fact_check_skipped=True` or `verification_status="unavailable"`
   (Reliability Plan Rule #6: provider unavailability is never silently
   converted to `passed`).
4. Emits a deterministic canonical JSON `verification_record` string under the
   compact `scanner.verification/v1` contract (`{"verification_record": "..."}`),
   keeping raw texts, source extracts, and publication authority off the
   checkpoint.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Mapping, Protocol, Sequence

from evidence_graph import attach_evidence_graph
from finland_list import get_company_metadata
from model_policy import CLAUDE_SONNET_MODEL, GPT_VERIFIER_MODEL
import reality_check
from reality_check import ai_fact_check_summary, validate_summary
from scan_verification_checkpoint import VerificationInputProjection
from strategic_foresight import extract_foresight_links


_PROVIDER_SWAP_LOCK = threading.Lock()
_VERIFICATION_RECORD_KEYS = frozenset(
    {
        "verification_status",
        "verified",
        "fact_check_model",
        "hallucination_risk",
        "hallucination_score",
        "hallucination_flags",
        "false_claims",
        "audit_notes",
        "wording_adjustments",
        "evidence_handoff_failures_count",
        "premise_reference_failures_count",
        "corrected_claim_ids",
    }
)


class VerificationCognitiveUnavailableError(RuntimeError):
    """Raised when independent Verification cannot complete due to provider unavailability."""


class VerificationContextLoader(Protocol):
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


def normalize_verification_record(
    *,
    summary_check: Mapping[str, Any],
    fact_check: Mapping[str, Any],
) -> dict[str, str]:
    """Serialize deterministic + AI verification verdicts into `verification_record`."""

    payload = {
        "verification_status": str(fact_check.get("verification_status") or "verified"),
        "verified": bool(fact_check.get("verified")),
        "fact_check_model": str(fact_check.get("fact_check_model") or "skipped"),
        "hallucination_risk": str(summary_check.get("risk") or "low"),
        "hallucination_score": int(summary_check.get("score") or 0),
        "hallucination_flags": [
            str(item)
            for item in (summary_check.get("flags") or [])
            if isinstance(item, str)
        ],
        "false_claims": [
            str(item)
            for item in (fact_check.get("false_claims") or [])
            if isinstance(item, str)
        ],
        "audit_notes": [
            str(item)
            for item in (fact_check.get("audit_notes") or [])
            if isinstance(item, str)
        ],
        "wording_adjustments": [
            str(item)
            for item in (fact_check.get("wording_adjustments") or [])
            if isinstance(item, str)
        ],
        "evidence_handoff_failures_count": len(
            fact_check.get("evidence_handoff_failures") or []
        ),
        "premise_reference_failures_count": len(
            fact_check.get("premise_reference_failures") or []
        ),
        "corrected_claim_ids": [
            str(item)
            for item in (fact_check.get("corrected_claim_ids") or [])
            if isinstance(item, str)
        ],
    }
    return {
        "verification_record": json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    }


def parse_verification_record(record: object) -> dict[str, Any] | None:
    """Parse a structured `verification_record` JSON string, or return None if invalid."""

    if not isinstance(record, str) or not record.strip():
        return None
    try:
        parsed = json.loads(record)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping) or set(parsed) != _VERIFICATION_RECORD_KEYS:
        return None
    return dict(parsed)


def analyze_verification_projection_with_cognition(
    projection: VerificationInputProjection,
    *,
    company_name: str,
    domain: str = "Other",
    diagnosis_result: Mapping[str, Any] | None = None,
    foresight_result: Mapping[str, Any] | None = None,
    jury_result: Mapping[str, Any] | None = None,
    intent_result: Mapping[str, Any] | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    provider_chain: Sequence[str] | None = None,
) -> dict[str, str]:
    """Execute deterministic + AI Verification on verified durable stage outputs."""

    normalized_company = str(company_name or "").strip()
    if not normalized_company:
        raise ValueError("company_name is required for Verification cognitive analysis.")
    if not isinstance(projection.synthesis_result, Mapping) or not projection.synthesis_result:
        raise ValueError("Verification cognition requires a compact Synthesis result.")

    diag = dict(diagnosis_result or {})
    fs = dict(foresight_result or {})
    synthesis_text = str(projection.synthesis_result.get("strategic_synthesis") or "").strip()
    diagnosis_summary = str(diag.get("diagnostic_summary") or "").strip()
    evidence_based_risks = [
        str(item)
        for item in (diag.get("evidence_based_risks") or [])
        if isinstance(item, str) and item.strip()
    ]

    evidence_links = _evidence_links_from_jury(jury_result)
    foresight_links = (
        extract_foresight_links(
            {**fs, "kb_first": True},
            company_name=normalized_company,
            domain=domain,
        )
        if fs
        else []
    )
    graph_container: dict[str, Any] = {
        "evidence_links": evidence_links,
        "foresight_links": foresight_links,
        "strategic_foresight": fs,
        "evidence_absence_signals": [],
    }
    attach_evidence_graph(graph_container)
    evidence_manifest = list(graph_container.get("evidence_manifest") or [])
    claim_graph = list(graph_container.get("claim_graph") or [])

    avg_conf = _average_jury_confidence(jury_result)
    effective_summary_text = diagnosis_summary or synthesis_text

    summary_check = validate_summary(
        summary_text=effective_summary_text,
        company_name=normalized_company,
        key_people=[],
        evidence_links=evidence_links,
        confidence_score=avg_conf,
    )

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
    scan_payload = {
        "reality_score": reality_score,
        "intent_score": intent_score,
        "confidence_score": avg_conf,
        "archetype": str(diag.get("archetype") or ""),
        "washing_flag": False,
        "washing_type": None,
        "evidence_absence_signals": [],
    }
    analysis_sections: dict[str, Any] = {
        "evidence_based_risks": evidence_based_risks,
        "strategic_synthesis": synthesis_text,
        "activation_pairs": [],
        "why_proposals": [],
        "what_proposals": [],
        "what_progress_proposals": [],
    }
    chain = tuple(provider_chain) if provider_chain else (GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL)

    fact_check = _run_fact_check_with_optional_callers(
        summary_text=effective_summary_text,
        evidence_links=evidence_links,
        company_name=normalized_company,
        scan_payload=scan_payload,
        analysis_sections=analysis_sections,
        foresight_links=foresight_links,
        evidence_manifest=evidence_manifest,
        claim_graph=claim_graph,
        provider_chain=chain,
        openai_caller=openai_caller,
        anthropic_caller=anthropic_caller,
    )

    if (
        fact_check.get("fact_check_skipped") is True
        or fact_check.get("verification_status") == "unavailable"
    ):
        raise VerificationCognitiveUnavailableError(
            f"No-web Verification unavailable for {normalized_company}: "
            f"{fact_check.get('fact_check_model', 'all_models_failed')}"
        )

    return normalize_verification_record(
        summary_check=summary_check,
        fact_check=fact_check,
    )


def build_no_web_verification_analyzer(
    *,
    company_name: str | None = None,
    domain: str | None = None,
    diagnosis_result: Mapping[str, Any] | None = None,
    foresight_result: Mapping[str, Any] | None = None,
    jury_result: Mapping[str, Any] | None = None,
    intent_result: Mapping[str, Any] | None = None,
    context_loader: VerificationContextLoader | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    provider_chain: Sequence[str] | None = None,
) -> Callable[[VerificationInputProjection], dict[str, str]]:
    """Build an injectable `VerificationAnalyzer` resolving durable upstream context."""

    def _analyze(projection: VerificationInputProjection) -> dict[str, str]:
        resolved_company = (company_name or "").strip()
        resolved_domain = (domain or "").strip()
        resolved_fs = foresight_result
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
                resolved_fs is None
                or resolved_diag is None
                or resolved_jury is None
                or resolved_intent is None
            ) and hasattr(context_loader, "load_checkpoint"):
                loaded_fs, loaded_diag, loaded_jury, loaded_intent = (
                    _load_upstream_chain_results(
                        context_loader,
                        run_id=projection.run_id,
                        synthesis_checkpoint_ref=projection.synthesis_checkpoint_ref,
                    )
                )
                if resolved_fs is None:
                    resolved_fs = loaded_fs
                if resolved_diag is None:
                    resolved_diag = loaded_diag
                if resolved_jury is None:
                    resolved_jury = loaded_jury
                if resolved_intent is None:
                    resolved_intent = loaded_intent

        if not resolved_company:
            raise ValueError(
                "company_name or a context_loader with a persisted ScanRun is required for Verification cognition."
            )
        if not resolved_domain:
            meta = get_company_metadata(resolved_company) or {}
            resolved_domain = str(meta.get("domain") or "Other").strip() or "Other"

        return analyze_verification_projection_with_cognition(
            projection,
            company_name=resolved_company,
            domain=resolved_domain,
            diagnosis_result=resolved_diag,
            foresight_result=resolved_fs,
            jury_result=resolved_jury,
            intent_result=resolved_intent,
            openai_caller=openai_caller,
            anthropic_caller=anthropic_caller,
            provider_chain=provider_chain,
        )

    return _analyze


def _run_fact_check_with_optional_callers(
    *,
    summary_text: str,
    evidence_links: list[dict[str, Any]],
    company_name: str,
    scan_payload: dict[str, Any],
    analysis_sections: dict[str, Any],
    foresight_links: list[dict[str, Any]],
    evidence_manifest: list[dict[str, Any]],
    claim_graph: list[dict[str, Any]],
    provider_chain: tuple[str, ...],
    openai_caller: OpenAINoWebCaller | None,
    anthropic_caller: AnthropicNoWebCaller | None,
) -> dict[str, Any]:
    if openai_caller is None and anthropic_caller is None:
        return ai_fact_check_summary(
            summary_text=summary_text,
            evidence_links=evidence_links,
            company_name=company_name,
            pitch_text="",
            scan_payload=scan_payload,
            analysis_sections=analysis_sections,
            document_label="AI Diagnosis, Evidence-Based Risks, and Strategic Synthesis",
            foresight_links=foresight_links,
            evidence_manifest=evidence_manifest,
            claim_graph=claim_graph,
            verification_only=True,
            provider_chain=provider_chain,
        )

    with _PROVIDER_SWAP_LOCK:
        orig_openai = reality_check._call_openai_text
        orig_anthropic = reality_check.call_anthropic_text
        try:
            if openai_caller is not None:

                def _wrapped_openai(
                    prompt: str,
                    model: str = GPT_VERIFIER_MODEL,
                    max_output_tokens: int = 10000,
                    timeout: int = 90,
                    json_schema: dict | None = None,
                    json_schema_name: str = "scanner_verifier",
                ) -> str:
                    out = openai_caller(
                        prompt,
                        model=model,
                        max_output_tokens=max_output_tokens,
                        timeout=timeout,
                        web_search=False,
                        json_schema=json_schema,
                        json_schema_name=json_schema_name,
                    )
                    if isinstance(out, Mapping):
                        return json.dumps(dict(out))
                    return "" if out is None else str(out)

                reality_check._call_openai_text = _wrapped_openai
            if anthropic_caller is not None:

                def _wrapped_anthropic(
                    prompt: str,
                    model: str = CLAUDE_SONNET_MODEL,
                    **kwargs: Any,
                ) -> str:
                    out = anthropic_caller(
                        prompt,
                        model=model,
                        web_search=False,
                        **{k: v for k, v in kwargs.items() if k != "web_search"},
                    )
                    if isinstance(out, Mapping):
                        return json.dumps(dict(out))
                    return "" if out is None else str(out)

                reality_check.call_anthropic_text = _wrapped_anthropic

            return ai_fact_check_summary(
                summary_text=summary_text,
                evidence_links=evidence_links,
                company_name=company_name,
                pitch_text="",
                scan_payload=scan_payload,
                analysis_sections=analysis_sections,
                document_label="AI Diagnosis, Evidence-Based Risks, and Strategic Synthesis",
                foresight_links=foresight_links,
                evidence_manifest=evidence_manifest,
                claim_graph=claim_graph,
                verification_only=True,
                provider_chain=provider_chain,
            )
        finally:
            reality_check._call_openai_text = orig_openai
            reality_check.call_anthropic_text = orig_anthropic


def _load_upstream_chain_results(
    context_loader: Any,
    *,
    run_id: str,
    synthesis_checkpoint_ref: Mapping[str, object],
) -> tuple[
    Mapping[str, Any] | None,
    Mapping[str, Any] | None,
    Mapping[str, Any] | None,
    Mapping[str, Any] | None,
]:
    fs_result: Mapping[str, Any] | None = None
    diag_result: Mapping[str, Any] | None = None
    jury_result: Mapping[str, Any] | None = None
    intent_result: Mapping[str, Any] | None = None

    synth_attempt = synthesis_checkpoint_ref.get("attempt")
    if type(synth_attempt) is not int or synth_attempt < 1:
        return fs_result, diag_result, jury_result, intent_result
    synth_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="strategic_synthesis", attempt=synth_attempt
    )
    if synth_cp is None:
        return fs_result, diag_result, jury_result, intent_result

    fs_ref = synth_cp.snapshot.get("foresight_checkpoint_ref")
    if not isinstance(fs_ref, Mapping) or type(fs_ref.get("attempt")) is not int:
        return fs_result, diag_result, jury_result, intent_result
    fs_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="foresight", attempt=fs_ref["attempt"]
    )
    if fs_cp is None:
        return fs_result, diag_result, jury_result, intent_result
    if isinstance(fs_cp.snapshot.get("foresight_result"), Mapping):
        fs_result = dict(fs_cp.snapshot["foresight_result"])

    diag_ref = fs_cp.snapshot.get("diagnosis_checkpoint_ref")
    if not isinstance(diag_ref, Mapping) or type(diag_ref.get("attempt")) is not int:
        return fs_result, diag_result, jury_result, intent_result
    diag_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="diagnosis", attempt=diag_ref["attempt"]
    )
    if diag_cp is None:
        return fs_result, diag_result, jury_result, intent_result
    if isinstance(diag_cp.snapshot.get("diagnosis_result"), Mapping):
        diag_result = dict(diag_cp.snapshot["diagnosis_result"])

    jury_ref = diag_cp.snapshot.get("jury_checkpoint_ref")
    if not isinstance(jury_ref, Mapping) or type(jury_ref.get("attempt")) is not int:
        return fs_result, diag_result, jury_result, intent_result
    jury_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="jury_analysis", attempt=jury_ref["attempt"]
    )
    if jury_cp is None:
        return fs_result, diag_result, jury_result, intent_result
    if isinstance(jury_cp.snapshot.get("jury_result"), Mapping):
        jury_result = dict(jury_cp.snapshot["jury_result"])

    intent_ref = jury_cp.snapshot.get("intent_checkpoint_ref")
    if not isinstance(intent_ref, Mapping) or type(intent_ref.get("attempt")) is not int:
        return fs_result, diag_result, jury_result, intent_result
    intent_cp = context_loader.load_checkpoint(
        run_id=run_id, stage_name="intent", attempt=intent_ref["attempt"]
    )
    if intent_cp is not None and isinstance(intent_cp.snapshot.get("intent_result"), Mapping):
        intent_result = dict(intent_cp.snapshot["intent_result"])

    return fs_result, diag_result, jury_result, intent_result


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


def _average_jury_confidence(jury_result: Mapping[str, Any] | None) -> int:
    if not isinstance(jury_result, Mapping):
        return 0
    scores = [
        int(lane["confidence_score"])
        for lane in (jury_result.get("juries") or [])
        if isinstance(lane, Mapping)
        and lane.get("findings")
        and type(lane.get("confidence_score")) is int
    ]
    return round(sum(scores) / len(scores)) if scores else 0
