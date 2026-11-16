"""No-web KB-first Strategic Foresight cognitive parity and central KB/registry hash binding.

This module executes the KB-first Strategic Foresight synthesis path on
verified durable Diagnosis + upstream company inputs and supplied central
Foresight KB/registry artifacts without running live web harvesting, touching
Vercel Blob storage, modifying `POST /scan`, or copying central KB records onto the
durable `scanner.foresight/v1` checkpoint:

1. Computes a deterministic `CentralForesightKBBinding` (`kb_version`,
   `kb_registry_hash`) over the central Strategic Foresight Theme Registry
   (`FP_THEMES_30`, `FP_THEME_CATEGORIES`, `FP_THEME_SOURCES`,
   `FORESIGHT_SOURCE_QUALITY_TIERS`, `DOMAIN_TO_FORESIGHT`, `THEME_FALLBACK_MAP`)
   and the supplied central KB signal store.
2. Filters central KB signals through the dual-status company-neutral context
   eligibility gate (rejecting `company_specific=True`, `wrong_entity=True`,
   unverified URLs, dead/unreadable/audit/stale signals) and applies
   `THEME_FALLBACK_MAP` near-fit fallback deterministically offline.
3. Raises `ForesightCognitiveUnavailableError` when fewer than
   `_MIN_KB_SIGNALS_FOR_SYNTHESIS` eligible central KB signals exist or when
   both `GPT_REASONING_MODEL` and `CLAUDE_SONNET_MODEL` fail or return
   unparseable output (Reliability Plan Rule #6: insufficient KB evidence or
   provider unavailability is never silently converted to `passed`).
4. Builds `_build_kb_synthesis_prompt(...)`, executes `GPT_REASONING_MODEL`
   primary -> `CLAUDE_SONNET_MODEL` fallback with `web_search=False`
   hard-locked, drops any ungrounded primary `source_url` not in the verified
   central KB input set, attaches KB context metadata (`_from_kb`,
   `claim_type="foresight_context"`, `company_specific=False`,
   `not_evidence_of_deployment=True`, `requires_company_specific_corroboration=True`,
   `foresight_context_eligible`, `_kb_description`), sanitizes every field with
   `_sanitize_foresight_data(...)` and `sanitize_future_insight(...)`, and
   emits the compact 4-key `scanner.foresight/v1` payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from typing import Any, Callable, Mapping, Protocol, Sequence

from finland_list import get_company_metadata
from foresight_guardrails import sanitize_future_insight
from llm_clients import call_anthropic_text, call_openai_text
from model_policy import CLAUDE_SONNET_MODEL, GPT_REASONING_MODEL
from scan_foresight_checkpoint import ForesightInputProjection
from strategic_foresight import (
    DOMAIN_TO_FORESIGHT,
    FORESIGHT_SOURCE_QUALITY_TIERS,
    FP_THEME_CATEGORIES,
    FP_THEME_SOURCES,
    FP_THEMES_30,
    THEME_FALLBACK_MAP,
    _MIN_KB_SIGNALS_FOR_SYNTHESIS,
    _build_kb_synthesis_prompt,
    _extract_json_from_text,
    _sanitize_foresight_data,
)


_ACTIVE_KB_STATUSES = frozenset({"active_context", "context_eligible"})
_ACCEPTED_AUDIT_STATUSES = frozenset({"accepted_active", "accepted"})
_VALID_FORESIGHT_SIGNAL_MODES = frozenset({"foresight_context", "regulatory_context"})
_VALID_FORESIGHT_SOURCE_TYPES = frozenset(
    {"foresight", "foresight_report", "trend_report", "regulatory_context"}
)


class ForesightCognitiveUnavailableError(RuntimeError):
    """Raised when central KB inputs are insufficient or all no-web models fail."""


@dataclass(frozen=True)
class CentralForesightKBBinding:
    """Deterministic version/hash identity for the central KB & Theme Registry."""

    kb_version: str
    kb_registry_hash: str
    registry_hash: str
    kb_signals_hash: str
    domain: str
    fp_themes: tuple[str, ...]
    eligible_signals: tuple[Mapping[str, Any], ...]


class ForesightContextLoader(Protocol):
    def load_run(self, *, run_id: str) -> Any: ...
    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> Any: ...


KBLoader = Callable[
    [Sequence[str]],
    Sequence[Mapping[str, Any]] | Mapping[str, Sequence[Mapping[str, Any]]],
]
OpenAINoWebCaller = Callable[..., str | Mapping[str, Any] | None]
AnthropicNoWebCaller = Callable[..., str | Mapping[str, Any] | None]


def is_central_kb_signal_context_eligible(signal: object) -> bool:
    """Deterministic company-neutral central KB context eligibility check."""

    if not isinstance(signal, Mapping):
        return False
    if signal.get("company_specific") is True or signal.get("wrong_entity") is True:
        return False
    claim_type = str(signal.get("claim_type") or "").strip().lower()
    source_type = str(signal.get("source_type") or signal.get("origin") or "").strip().lower()
    context_safe = (
        claim_type in _VALID_FORESIGHT_SIGNAL_MODES
        or signal.get("not_evidence_of_deployment") is True
        or source_type in _VALID_FORESIGHT_SOURCE_TYPES
    )
    if not context_safe:
        return False
    if signal.get("foresight_context_eligible") is not True:
        return False
    if signal.get("foresight_signal_status") not in _ACTIVE_KB_STATUSES:
        return False
    if signal.get("source_audit_status") not in _ACCEPTED_AUDIT_STATUSES:
        return False
    if signal.get("url_verified") is not True and signal.get("verified") is not True:
        return False
    if (
        signal.get("source_readable") is False
        and signal.get("content_verified") is not True
        and not bool(signal.get("source_quote"))
    ):
        return False
    return True


def canonical_central_registry_payload() -> dict[str, Any]:
    """Deterministic central Strategic Foresight Theme Registry payload (no wall-clock timestamps)."""

    return {
        "registry_version": "strategic-foresight-theme-registry-v1",
        "fp_themes_30": list(FP_THEMES_30),
        "fp_theme_categories": {
            category: list(themes)
            for category, themes in sorted(FP_THEME_CATEGORIES.items())
        },
        "fp_theme_sources": {
            theme: list(queries)
            for theme, queries in sorted(FP_THEME_SOURCES.items())
        },
        "source_quality_tiers": {
            tier: {
                key: sorted(value) if isinstance(value, (set, list, tuple)) else value
                for key, value in sorted(spec.items())
            }
            for tier, spec in sorted(FORESIGHT_SOURCE_QUALITY_TIERS.items())
        },
        "domain_to_foresight": {
            domain_name: {
                key: list(value) if isinstance(value, (list, tuple)) else value
                for key, value in sorted(cfg.items())
            }
            for domain_name, cfg in sorted(DOMAIN_TO_FORESIGHT.items())
        },
        "theme_fallback_map": {
            theme: list(fallbacks)
            for theme, fallbacks in sorted(THEME_FALLBACK_MAP.items())
        },
    }


def resolve_central_kb_binding(
    *,
    domain: str = "Other",
    kb_signals: Sequence[Mapping[str, Any]] | None = None,
    kb_signals_by_theme: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    registry_override: Mapping[str, Any] | None = None,
    kb_version_override: str | None = None,
) -> CentralForesightKBBinding:
    """Resolve eligible company-neutral KB signals and compute central KB/registry hashes."""

    resolved_domain = domain if domain in DOMAIN_TO_FORESIGHT else "Other"
    domain_config = DOMAIN_TO_FORESIGHT.get(resolved_domain) or DOMAIN_TO_FORESIGHT["Other"]
    fp_themes = tuple(domain_config.get("fp_themes") or ())

    eligible = _select_eligible_kb_signals(
        fp_themes=fp_themes,
        kb_signals=kb_signals,
        kb_signals_by_theme=kb_signals_by_theme,
    )

    registry_payload = (
        _json_safe_value(dict(registry_override))
        if registry_override is not None
        else canonical_central_registry_payload()
    )
    registry_hash = _hash(registry_payload)

    kb_digest_material = {
        "kb_signals_by_theme": (
            {
                str(theme): [
                    _json_safe_value(dict(item))
                    for item in items
                    if isinstance(item, Mapping)
                ]
                for theme, items in sorted(kb_signals_by_theme.items())
            }
            if kb_signals_by_theme is not None
            else None
        ),
        "kb_signals": (
            [
                _json_safe_value(dict(item))
                for item in kb_signals
                if isinstance(item, Mapping)
            ]
            if kb_signals is not None
            else None
        ),
    }
    kb_signals_hash = _hash(kb_digest_material)
    kb_registry_hash = _hash(
        {
            "registry_hash": registry_hash,
            "kb_signals_hash": kb_signals_hash,
        }
    )
    resolved_kb_version = (
        kb_version_override.strip()
        if isinstance(kb_version_override, str) and kb_version_override.strip()
        else f"foresight-kb-registry/v1:{kb_registry_hash[:16]}"
    )

    return CentralForesightKBBinding(
        kb_version=resolved_kb_version,
        kb_registry_hash=kb_registry_hash,
        registry_hash=registry_hash,
        kb_signals_hash=kb_signals_hash,
        domain=resolved_domain,
        fp_themes=fp_themes,
        eligible_signals=tuple(eligible),
    )


def analyze_foresight_projection_with_cognition(
    projection: ForesightInputProjection,
    *,
    company_name: str,
    domain: str = "Other",
    financial_index: int | None = None,
    top_findings: Sequence[Mapping[str, Any]] | None = None,
    kb_signals: Sequence[Mapping[str, Any]] | None = None,
    kb_signals_by_theme: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    registry_override: Mapping[str, Any] | None = None,
    kb_version_override: str | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    report_date: date | None = None,
) -> tuple[dict[str, Any], CentralForesightKBBinding]:
    """Run no-web KB-first Strategic Foresight synthesis on verified durable inputs."""

    normalized_company = str(company_name or "").strip()
    if not normalized_company:
        raise ValueError("company_name is required for Foresight cognitive analysis.")
    if not isinstance(projection.diagnosis_result, Mapping) or not projection.diagnosis_result:
        raise ValueError("Foresight cognition requires a compact Diagnosis result.")

    binding = resolve_central_kb_binding(
        domain=domain,
        kb_signals=kb_signals,
        kb_signals_by_theme=kb_signals_by_theme,
        registry_override=registry_override,
        kb_version_override=kb_version_override,
    )
    if len(binding.eligible_signals) < _MIN_KB_SIGNALS_FOR_SYNTHESIS:
        raise ForesightCognitiveUnavailableError(
            "Insufficient foresight-context eligible central KB signals for "
            f"{normalized_company} ({len(binding.eligible_signals)}/{_MIN_KB_SIGNALS_FOR_SYNTHESIS})."
        )

    domain_config = DOMAIN_TO_FORESIGHT.get(binding.domain) or DOMAIN_TO_FORESIGHT["Other"]
    reality_score = int(projection.diagnosis_result.get("reality_score") or 0)
    archetype = str(projection.diagnosis_result.get("archetype") or "At Risk")
    selected_signals = [dict(sig) for sig in binding.eligible_signals[:12]]
    findings_list = [dict(item) for item in (top_findings or ()) if isinstance(item, Mapping)]

    prompt = _build_kb_synthesis_prompt(
        normalized_company,
        binding.domain,
        archetype,
        reality_score,
        financial_index,
        findings_list,
        domain_config,
        selected_signals,
    )

    openai_fn = openai_caller or call_openai_text
    anthropic_fn = anthropic_caller or call_anthropic_text
    errors: list[str] = []

    raw_data: dict[str, Any] | None = None
    for model_name, provider_kind in (
        (GPT_REASONING_MODEL, "openai"),
        (CLAUDE_SONNET_MODEL, "anthropic"),
    ):
        raw_text = _call_kb_synthesis_model(
            prompt=prompt,
            model_name=model_name,
            provider_kind=provider_kind,
            openai_fn=openai_fn,
            anthropic_fn=anthropic_fn,
            errors=errors,
        )
        if not raw_text or len(raw_text) < 100:
            if raw_text is not None:
                errors.append(f"{model_name}:insufficient_text")
            continue
        json_str = _extract_json_from_text(raw_text)
        if not json_str:
            errors.append(f"{model_name}:no_json")
            continue
        try:
            parsed = json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            errors.append(f"{model_name}:invalid_json")
            continue
        if not isinstance(parsed, Mapping):
            errors.append(f"{model_name}:non_mapping")
            continue
        raw_data = dict(parsed)
        break

    if raw_data is None:
        detail = "; ".join(errors) if errors else "no parseable provider payload"
        raise ForesightCognitiveUnavailableError(
            f"No-web KB Foresight synthesis unavailable for {normalized_company}: {detail}"
        )

    compact_result = normalize_foresight_payload(
        raw_data,
        kb_signals=selected_signals,
        domain=binding.domain,
        company_name=normalized_company,
        report_date=report_date,
    )
    return compact_result, binding


def normalize_foresight_payload(
    raw_data: Mapping[str, Any] | object,
    *,
    kb_signals: Sequence[Mapping[str, Any]],
    domain: str,
    company_name: str,
    report_date: date | None = None,
) -> dict[str, Any]:
    """Attach verified KB context metadata, drop ungrounded URLs, and sanitize modal language."""

    if not isinstance(raw_data, Mapping):
        return {"malformed_foresight_payload": True}

    forces_raw = raw_data.get("dimension_forces")
    scenarios_raw = raw_data.get("scenarios")
    if not isinstance(forces_raw, list) or not isinstance(scenarios_raw, Mapping) or not scenarios_raw:
        return dict(raw_data)
    if any(not isinstance(item, Mapping) for item in forces_raw):
        return dict(raw_data)

    allowed_urls = {
        str(sig.get("source_url") or "").strip()
        for sig in kb_signals
        if isinstance(sig, Mapping) and str(sig.get("source_url") or "").strip()
    }
    enriched_forces: list[dict[str, Any]] = []
    for raw_force in forces_raw:
        force = dict(raw_force)
        source_url = str(force.get("source_url") or "").strip()
        force_title = str(force.get("trend") or "").strip().lower()
        matched_sig = None
        for sig in kb_signals:
            if source_url and str(sig.get("source_url") or "").strip() == source_url:
                matched_sig = sig
                break
        if matched_sig is None and force_title:
            for sig in kb_signals:
                sig_title = str(sig.get("title") or "").strip().lower()
                if sig_title and (sig_title in force_title or force_title in sig_title):
                    matched_sig = sig
                    if sig.get("source_url"):
                        source_url = str(sig["source_url"]).strip()
                        force["source_url"] = source_url
                    break

        if source_url and source_url not in allowed_urls:
            # Grounded KB-first mode rejects fabricated or unverified source URLs.
            continue

        force["_from_kb"] = True
        force["validated"] = True
        force["url_verified"] = bool(source_url and source_url in allowed_urls)
        force.setdefault("source_type", "foresight")
        force["claim_type"] = "foresight_context"
        force["company_specific"] = False
        force["not_evidence_of_deployment"] = True
        force["requires_company_specific_corroboration"] = True
        force["modal_language_required"] = True

        if matched_sig is not None:
            kb_supporting = [
                dict(ss)
                for ss in (matched_sig.get("supporting_sources") or [])
                if isinstance(ss, Mapping)
            ]
            if kb_supporting and not force.get("supporting_sources"):
                force["supporting_sources"] = kb_supporting
            force["verified_supporting_sources"] = [
                {**ss, "url_verified": bool(str(ss.get("url") or "").strip())}
                for ss in (force.get("supporting_sources") or kb_supporting)
                if isinstance(ss, Mapping)
            ]
            force["source_credibility"] = matched_sig.get("source_credibility", "medium")
            force["source_count"] = 1 + len(force["verified_supporting_sources"])
            force["source_readable"] = matched_sig.get("source_readable") is True
            force["trend_supported"] = matched_sig.get("trend_supported") is True
            force["theme_match"] = matched_sig.get("theme_match") is not False
            force["foresight_context_eligible"] = (
                matched_sig.get("foresight_context_eligible") is True
            )
            force["foresight_signal_status"] = matched_sig.get(
                "foresight_signal_status", ""
            )
            force["source_audit_status"] = matched_sig.get("source_audit_status", "")
            force["trend_support_status"] = matched_sig.get("trend_support_status", "")
            force["foresight_evidence_version"] = matched_sig.get(
                "foresight_evidence_version"
            )
            force["fp_theme"] = matched_sig.get("fp_theme") or force.get("fp_theme")
            if matched_sig.get("description"):
                force["_kb_description"] = matched_sig["description"]
            for lc_field in ("lifecycle_stage", "trend_momentum", "action_timing"):
                if matched_sig.get(lc_field) and not force.get(lc_field):
                    force[lc_field] = matched_sig[lc_field]

        enriched_forces.append(force)

    if not enriched_forces:
        return {"malformed_foresight_payload": True}

    staged = {
        "dimension_forces": enriched_forces,
        "cross_impact_chain": str(raw_data.get("cross_impact_chain") or ""),
        "scenarios": dict(scenarios_raw),
        "climate_exposure": (
            dict(raw_data["climate_exposure"])
            if isinstance(raw_data.get("climate_exposure"), Mapping)
            else {}
        ),
    }
    sanitized = _sanitize_foresight_data(
        staged, domain=domain, company_name=company_name
    ) or staged

    effective_date = report_date or date.today()
    sanitized_scenarios = _sanitize_nested_text(
        sanitized.get("scenarios") or {}, effective_date
    )
    sanitized_climate = _sanitize_nested_text(
        sanitized.get("climate_exposure") or {}, effective_date
    )
    chain_text, _ = sanitize_future_insight(
        str(sanitized.get("cross_impact_chain") or ""),
        effective_date,
    )

    return {
        "dimension_forces": list(sanitized.get("dimension_forces") or []),
        "cross_impact_chain": chain_text,
        "scenarios": sanitized_scenarios,
        "climate_exposure": sanitized_climate,
    }


def build_no_web_foresight_runner(
    *,
    company_name: str | None = None,
    domain: str | None = None,
    financial_index: int | None = None,
    top_findings: Sequence[Mapping[str, Any]] | None = None,
    kb_signals: Sequence[Mapping[str, Any]] | None = None,
    kb_signals_by_theme: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    kb_loader: KBLoader | None = None,
    registry_override: Mapping[str, Any] | None = None,
    kb_version_override: str | None = None,
    context_loader: ForesightContextLoader | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    report_date: date | None = None,
) -> Callable[[ForesightInputProjection], tuple[dict[str, Any], str, str]]:
    """Build a pure runner returning `(foresight_result, kb_version, kb_registry_hash)`."""

    def _run(projection: ForesightInputProjection) -> tuple[dict[str, Any], str, str]:
        resolved_company = (company_name or "").strip()
        resolved_domain = (domain or "").strip()
        resolved_fin = financial_index
        resolved_findings: list[Mapping[str, Any]] = [
            dict(item) for item in (top_findings or ()) if isinstance(item, Mapping)
        ]

        if context_loader is not None:
            if not resolved_company and hasattr(context_loader, "load_run"):
                loaded_run = context_loader.load_run(run_id=projection.run_id)
                if loaded_run is not None:
                    resolved_company = str(
                        getattr(loaded_run, "company_name", "") or ""
                    ).strip()
            if (not resolved_findings or resolved_fin is None) and hasattr(
                context_loader, "load_checkpoint"
            ):
                jury_findings, intent_score = _load_upstream_context(
                    context_loader,
                    run_id=projection.run_id,
                    diagnosis_checkpoint_ref=projection.diagnosis_checkpoint_ref,
                )
                if not resolved_findings:
                    resolved_findings = jury_findings
                if resolved_fin is None and intent_score is not None:
                    resolved_fin = intent_score

        if not resolved_company:
            raise ValueError(
                "company_name or a context_loader with a persisted ScanRun is required for Foresight cognition."
            )

        if not resolved_domain:
            meta = get_company_metadata(resolved_company) or {}
            resolved_domain = str(meta.get("domain") or "Other").strip() or "Other"
        if resolved_domain not in DOMAIN_TO_FORESIGHT:
            resolved_domain = "Other"

        domain_themes = list(
            (DOMAIN_TO_FORESIGHT.get(resolved_domain) or DOMAIN_TO_FORESIGHT["Other"]).get(
                "fp_themes", ()
            )
        )
        loaded_signals = kb_signals
        loaded_by_theme = kb_signals_by_theme
        if kb_loader is not None and loaded_signals is None and loaded_by_theme is None:
            loaded = kb_loader(domain_themes)
            if isinstance(loaded, Mapping):
                loaded_by_theme = loaded
            else:
                loaded_signals = list(loaded)

        if not resolved_findings:
            for risk in projection.diagnosis_result.get("evidence_based_risks") or []:
                if isinstance(risk, str) and risk.strip():
                    resolved_findings.append(
                        {
                            "category": "Diagnosis Risk",
                            "label": risk.strip(),
                            "sentiment": "negative",
                            "snippet": "",
                        }
                    )

        compact_result, binding = analyze_foresight_projection_with_cognition(
            projection,
            company_name=resolved_company,
            domain=resolved_domain,
            financial_index=resolved_fin,
            top_findings=resolved_findings,
            kb_signals=loaded_signals,
            kb_signals_by_theme=loaded_by_theme,
            registry_override=registry_override,
            kb_version_override=kb_version_override,
            openai_caller=openai_caller,
            anthropic_caller=anthropic_caller,
            report_date=report_date,
        )
        return compact_result, binding.kb_version, binding.kb_registry_hash

    return _run


def _select_eligible_kb_signals(
    *,
    fp_themes: Sequence[str],
    kb_signals: Sequence[Mapping[str, Any]] | None,
    kb_signals_by_theme: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    if kb_signals_by_theme is not None:
        for theme in fp_themes:
            theme_found: list[dict[str, Any]] = []
            for raw in kb_signals_by_theme.get(theme) or ():
                if is_central_kb_signal_context_eligible(raw):
                    sig = dict(raw)
                    sig["_from_kb"] = True
                    sig["_kb_theme"] = theme
                    sig.setdefault("fp_theme", theme)
                    theme_found.append(sig)
            if not theme_found:
                for fallback_theme in THEME_FALLBACK_MAP.get(theme, ()):
                    for raw in kb_signals_by_theme.get(fallback_theme) or ():
                        if is_central_kb_signal_context_eligible(raw):
                            sig = dict(raw)
                            sig["_from_kb"] = True
                            sig["_kb_theme"] = fallback_theme
                            sig.setdefault("fp_theme", fallback_theme)
                            theme_found.append(sig)
                    if theme_found:
                        break
            for sig in theme_found:
                key = str(sig.get("source_url") or sig.get("title") or "").strip().lower()
                if key and key in seen_urls:
                    continue
                if key:
                    seen_urls.add(key)
                collected.append(sig)
    elif kb_signals is not None:
        allowed_themes = set(fp_themes)
        for theme in fp_themes:
            allowed_themes.update(THEME_FALLBACK_MAP.get(theme, ()))
        for raw in kb_signals:
            if not is_central_kb_signal_context_eligible(raw):
                continue
            sig_theme = str(raw.get("_kb_theme") or raw.get("fp_theme") or "").strip()
            if sig_theme and allowed_themes and sig_theme not in allowed_themes:
                continue
            sig = dict(raw)
            sig["_from_kb"] = True
            if sig_theme:
                sig.setdefault("_kb_theme", sig_theme)
                sig.setdefault("fp_theme", sig_theme)
            key = str(sig.get("source_url") or sig.get("title") or "").strip().lower()
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            collected.append(sig)

    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    collected.sort(
        key=lambda item: (
            confidence_rank.get(str(item.get("confidence") or "").lower(), 3),
            str(item.get("source_url") or ""),
            str(item.get("title") or ""),
        )
    )
    return collected


def _load_upstream_context(
    context_loader: Any,
    *,
    run_id: str,
    diagnosis_checkpoint_ref: Mapping[str, object],
) -> tuple[list[dict[str, Any]], int | None]:
    diag_attempt = diagnosis_checkpoint_ref.get("attempt")
    if type(diag_attempt) is not int or diag_attempt < 1:
        return [], None
    diag_cp = context_loader.load_checkpoint(
        run_id=run_id,
        stage_name="diagnosis",
        attempt=diag_attempt,
    )
    if diag_cp is None:
        return [], None
    jury_ref = diag_cp.snapshot.get("jury_checkpoint_ref")
    if not isinstance(jury_ref, Mapping):
        return [], None
    jury_attempt = jury_ref.get("attempt")
    if type(jury_attempt) is not int or jury_attempt < 1:
        return [], None
    jury_cp = context_loader.load_checkpoint(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=jury_attempt,
    )
    if jury_cp is None:
        return [], None

    findings: list[dict[str, Any]] = []
    jury_result = jury_cp.snapshot.get("jury_result")
    if isinstance(jury_result, Mapping):
        for lane in jury_result.get("juries") or []:
            if not isinstance(lane, Mapping):
                continue
            for item in lane.get("findings") or []:
                if not isinstance(item, Mapping):
                    continue
                findings.append(
                    {
                        "category": item.get("category") or lane.get("jury") or "",
                        "label": item.get("supported_claim") or "",
                        "sentiment": item.get("sentiment") or "neutral",
                        "snippet": item.get("source_quote") or "",
                    }
                )

    intent_score: int | None = None
    intent_ref = jury_cp.snapshot.get("intent_checkpoint_ref")
    if isinstance(intent_ref, Mapping):
        intent_attempt = intent_ref.get("attempt")
        if type(intent_attempt) is int and intent_attempt >= 1:
            intent_cp = context_loader.load_checkpoint(
                run_id=run_id,
                stage_name="intent",
                attempt=intent_attempt,
            )
            if intent_cp is not None:
                intent_payload = intent_cp.snapshot.get("intent_result")
                if isinstance(intent_payload, Mapping) and type(intent_payload.get("score")) is int:
                    intent_score = intent_payload["score"]

    return findings, intent_score


def _call_kb_synthesis_model(
    *,
    prompt: str,
    model_name: str,
    provider_kind: str,
    openai_fn: OpenAINoWebCaller,
    anthropic_fn: AnthropicNoWebCaller,
    errors: list[str],
) -> str | None:
    try:
        if provider_kind == "openai":
            raw = openai_fn(
                prompt,
                model=model_name,
                max_output_tokens=9000,
                timeout=120,
                web_search=False,
                max_retries=0,
                log_label="Foresight KB Synthesis OpenAI",
            )
        else:
            raw = anthropic_fn(
                prompt,
                model=model_name,
                max_tokens=9000,
                timeout=120,
                web_search=False,
                max_retries=0,
                log_label="Foresight KB Synthesis Anthropic",
            )
    except Exception as error:
        errors.append(f"{model_name}:{type(error).__name__}")
        return None
    if isinstance(raw, Mapping):
        return json.dumps(dict(raw))
    if isinstance(raw, str):
        return raw
    errors.append(f"{model_name}:unexpected_response_type")
    return None


def _sanitize_nested_text(value: Mapping[str, Any], effective_date: date) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            sanitized, _ = sanitize_future_insight(item, effective_date)
            cleaned[key] = sanitized
        elif isinstance(item, Mapping):
            cleaned[key] = _sanitize_nested_text(item, effective_date)
        else:
            cleaned[key] = item
    return cleaned


def _json_safe_value(value: Any) -> Any:
    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
