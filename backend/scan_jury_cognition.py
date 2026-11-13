"""No-web five-Jury cognitive parity and deterministic finding normalization.

This module runs the existing five-Jury packet analysis pipeline on verified
durable Jury packets (`JuryInputProjection`) without live web acquisition:

1. Splits and prioritizes durable packets per Jury lane (`_split_jury_packets`).
2. Builds deterministic `PKG-...` / `PASS-...` / `PROP-...` evidence packages
   (`build_jury_evidence_packages`) and renders `_build_jury_analysis_prompt`.
3. Executes the `forensic_jury` model policy (`GPT_REASONING_MODEL` primary ->
   `CLAUDE_SONNET_MODEL` fallback where enabled -> compact no-web rescue
   `_build_jury_rescue_prompt` when findings are empty), hard-locking
   `web_search=False`.
4. Raises `JuryCognitiveUnavailableError` if a lane has judgeable evidence
   packages and every provider call fails or returns no parseable JSON (per
   Reliability Plan Rule #6: provider unavailability is never silently
   converted to `passed`).
5. Deterministically verifies off-packet URLs, source-passage support, atomic
   claims, overreach narrowing (`_derive_narrow_claim_from_source`), and
   `context_only` fallback, emitting only compact `scanner.jury/v1` fields.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Protocol, Sequence

from entity_identity import assess_entity_match
from evidence_packaging import build_jury_evidence_packages
from llm_clients import call_anthropic_text, call_openai_text
from model_policy import CLAUDE_SONNET_MODEL, GPT_REASONING_MODEL, policy_for
from scan_jury_checkpoint import JURY_CHECKPOINT_LANES, JuryInputProjection
from scanner_two import (
    FORENSIC_JURIES,
    FORENSIC_JURY_GPT_TIMEOUT_SECONDS,
    FORENSIC_JURY_GPT_TIMEOUTS,
    FORENSIC_JURY_RESCUE_TIMEOUT_SECONDS,
    FORENSIC_JURY_SONNET_TIMEOUT_SECONDS,
    FORENSIC_JURY_SONNET_TIMEOUTS,
    FORENSIC_SKIP_SONNET_ANALYSIS_FALLBACK_JURIES,
    SERVICE_PORTFOLIO_JURY_NAME,
    STRATEGIC_JURY_NAME,
    _FORENSIC_JURY_JSON_SCHEMA,
    _build_jury_analysis_prompt,
    _build_jury_rescue_prompt,
    _canonical_evidence_url,
    _claim_supported_by_source_text,
    _derive_narrow_claim_from_source,
    _is_atomic_supported_claim,
    _merge_jury_payloads,
    _relevant_passage,
    _split_jury_packets,
    extract_json_from_text,
)


class JuryCognitiveUnavailableError(RuntimeError):
    """Raised when judgeable Jury packages exist but all no-web model calls fail."""


class RunCompanyLoader(Protocol):
    def load_run(self, *, run_id: str) -> Any: ...


OpenAINoWebCaller = Callable[..., str | Mapping[str, Any] | None]
AnthropicNoWebCaller = Callable[..., str | Mapping[str, Any] | None]


def build_no_web_jury_analyzer(
    *,
    company_name: str | None = None,
    company_url: str | None = None,
    run_loader: RunCompanyLoader | None = None,
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
) -> Callable[[JuryInputProjection], dict[str, Any]]:
    """Build an injectable `JuryAnalyzer` executing five-Jury no-web cognition."""

    def _analyze(projection: JuryInputProjection) -> dict[str, Any]:
        resolved_company = (company_name or "").strip()
        resolved_url = (company_url or "").strip()
        if (not resolved_company or not resolved_url) and run_loader is not None:
            loaded_run = run_loader.load_run(run_id=projection.run_id)
            if loaded_run is not None:
                if not resolved_company:
                    resolved_company = str(
                        getattr(loaded_run, "company_name", "") or ""
                    ).strip()
                if not resolved_url:
                    resolved_url = str(
                        getattr(loaded_run, "company_url", "") or ""
                    ).strip()
        if not resolved_company:
            raise ValueError(
                "company_name or a run_loader with a persisted ScanRun is required for Jury cognition."
            )
        return analyze_jury_projection_with_cognition(
            projection,
            company_name=resolved_company,
            company_url=resolved_url,
            openai_caller=openai_caller,
            anthropic_caller=anthropic_caller,
            model_policy=model_policy,
        )

    return _analyze


def analyze_jury_projection_with_cognition(
    projection: JuryInputProjection,
    *,
    company_name: str,
    company_url: str = "",
    openai_caller: OpenAINoWebCaller | None = None,
    anthropic_caller: AnthropicNoWebCaller | None = None,
    model_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run no-web five-Jury analysis and deterministic normalization on durable packets."""

    normalized_company = str(company_name or "").strip()
    if not normalized_company:
        raise ValueError("company_name is required for Jury cognitive analysis.")
    if tuple(FORENSIC_JURIES) != JURY_CHECKPOINT_LANES:
        raise ValueError("Existing Jury lane names do not match the Jury checkpoint contract.")

    jury_policy = dict(model_policy or policy_for("forensic_jury"))
    primary_model = str(jury_policy.get("primary") or GPT_REASONING_MODEL).strip()
    fallback_model = str(jury_policy.get("fallback") or CLAUDE_SONNET_MODEL).strip()
    effort = str(jury_policy.get("effort") or "medium").strip()
    verbosity = str(jury_policy.get("verbosity") or "medium").strip()

    openai_fn = openai_caller or call_openai_text
    anthropic_fn = anthropic_caller or call_anthropic_text

    packet_dicts = [
        _enrich_transient_packet(
            packet,
            company_name=normalized_company,
            company_url=company_url,
        )
        for packet in projection.packets
    ]
    juries: list[dict[str, Any]] = []
    for jury_name in FORENSIC_JURIES:
        lane_payload = _analyze_single_jury_lane(
            company_name=normalized_company,
            jury_name=jury_name,
            packets=packet_dicts,
            primary_model=primary_model,
            fallback_model=fallback_model,
            effort=effort,
            verbosity=verbosity,
            openai_fn=openai_fn,
            anthropic_fn=anthropic_fn,
        )
        juries.append(
            normalize_jury_lane_payload(
                jury_name,
                lane_payload,
                packet_dicts,
                company_name=normalized_company,
                company_url=company_url,
            )
        )
    return {"juries": juries}


def normalize_jury_lane_payload(
    jury_name: str,
    payload: Mapping[str, Any] | object,
    packets: Sequence[Mapping[str, Any]],
    *,
    company_name: str = "",
    company_url: str = "",
) -> dict[str, Any]:
    """Deterministically verify URLs, source passages, and package lineage for one Jury lane.

    Structurally malformed payloads are returned as dicts without coercing bad
    types so `build_jury_checkpoint` marks the proposal `validation_failed`.
    """

    if not isinstance(payload, Mapping):
        return {"jury": jury_name, "malformed": True}
    raw_findings = payload.get("findings")
    raw_rejected = payload.get("rejected_evidence", [])
    raw_contradictions = payload.get("contradictions", [])
    raw_missing = payload.get("missing_coverage", [])
    raw_confidence = payload.get("confidence_score")
    raw_adjustment = payload.get("reality_score_adjustment")
    if (
        not isinstance(raw_findings, list)
        or any(not isinstance(item, Mapping) for item in raw_findings)
        or not isinstance(raw_rejected, list)
        or not isinstance(raw_contradictions, list)
        or any(not isinstance(item, str) for item in raw_contradictions)
        or not isinstance(raw_missing, list)
        or any(not isinstance(item, str) for item in raw_missing)
        or type(raw_confidence) is not int
        or not (0 <= raw_confidence <= 100)
        or type(raw_adjustment) is not int
        or not (-100 <= raw_adjustment <= 100)
    ):
        return dict(payload)

    packet_list = [
        _enrich_transient_packet(
            packet,
            company_name=company_name,
            company_url=company_url,
        )
        for packet in packets
        if isinstance(packet, Mapping)
    ]
    packet_by_url: dict[str, dict[str, Any]] = {}
    for packet in packet_list:
        canon = _canonical_evidence_url(str(packet.get("url") or ""))
        if canon and canon not in packet_by_url:
            packet_by_url[canon] = packet
    packet_urls = set(packet_by_url)

    package_by_id: dict[str, dict[str, Any]] = {}
    package_by_url: dict[str, dict[str, Any]] = {}
    for package in build_jury_evidence_packages(jury_name, packet_list, max_packages=24):
        pkg_id = str(package.get("package_id") or "").strip()
        pkg_url = _canonical_evidence_url(str(package.get("source_url") or ""))
        if pkg_id:
            package_by_id[pkg_id] = package
        if pkg_url and pkg_url not in package_by_url:
            package_by_url[pkg_url] = package

    rejected_out: list[dict[str, str]] = []
    for item in raw_rejected:
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("url"), str)
            and isinstance(item.get("reason"), str)
        ):
            rejected_out.append({"url": item["url"], "reason": item["reason"]})

    accepted_findings: list[dict[str, Any]] = []
    seen_finding_keys: set[tuple[str, str]] = set()
    for item in raw_findings:
        raw_url = str(item.get("url") or "").strip()
        if not raw_url:
            rejected_out.append(
                {"url": "", "reason": "Jury finding omitted source URL."}
            )
            continue
        canon_url = _canonical_evidence_url(raw_url)
        if not canon_url or canon_url not in packet_urls:
            rejected_out.append(
                {
                    "url": raw_url,
                    "reason": "Jury returned a URL that was not present in its supplied source packets.",
                }
            )
            continue

        packet = packet_by_url[canon_url]
        packet_text = str(
            packet.get("fetched_excerpt")
            or packet.get("snippet")
            or packet.get("cached_excerpt")
            or ""
        )
        supported_claim = str(
            item.get("supported_claim") or item.get("label") or ""
        ).strip()
        source_quote = str(item.get("source_quote") or item.get("snippet") or "").strip()
        support_level = str(item.get("support_level") or "unsupported").strip().lower()
        if support_level not in {"exact", "partial", "context_only", "unsupported"}:
            support_level = "unsupported"
        if support_level == "unsupported":
            rejected_out.append(
                {"url": raw_url, "reason": "Jury marked claim unsupported."}
            )
            continue

        packet_entity_status = str(
            packet.get("content_entity_match_status")
            or packet.get("entity_match_status")
            or ""
        ).lower()
        if packet.get("wrong_entity") is True or packet_entity_status == "wrong":
            rejected_out.append(
                {
                    "url": raw_url,
                    "reason": "Jury finding matched a wrong same-name or unrelated entity.",
                }
            )
            continue

        claim_supported, supporting_passage, _method = _claim_supported_by_source_text(
            supported_claim,
            source_quote,
            packet_text,
            support_level,
        )
        if (
            not supported_claim
            or not _is_atomic_supported_claim(supported_claim)
            or not claim_supported
        ):
            narrowed = _derive_narrow_claim_from_source(packet, jury_name)
            if narrowed:
                supported_claim, supporting_passage, _ = narrowed
                source_quote = supporting_passage
                support_level = "exact"
                claim_supported = True
            elif (
                bool(packet_text.strip())
                and packet.get("wrong_entity") is not True
                and (
                    packet_entity_status in {"exact", "scoped"}
                    or not (company_name or company_url)
                )
            ):
                rejected_out.append(
                    {
                        "url": raw_url,
                        "reason": "Jury claim overreached; source retained as scoped context.",
                    }
                )
                title = str(
                    packet.get("title") or item.get("title") or "Scoped source"
                ).strip()
                supported_claim = f"Scoped company context for {jury_name}: {title}"[:300]
                supporting_passage = (
                    _relevant_passage(title, packet_text)
                    or packet_text[:1200]
                    or source_quote
                )
                source_quote = supporting_passage
                support_level = "context_only"
                claim_supported = True
            else:
                rejected_out.append(
                    {
                        "url": raw_url,
                        "reason": "Jury claim was bundled or materially exceeded the fetched source content.",
                    }
                )
                continue

        if not claim_supported:
            rejected_out.append(
                {
                    "url": raw_url,
                    "reason": "Jury claim was not supported by fetched source content.",
                }
            )
            continue

        if packet.get("generic_context_page") and support_level == "exact":
            support_level = "context_only"

        claimed_pkg_id = str(item.get("evidence_package_id") or "").strip()
        candidate_pkg = package_by_id.get(claimed_pkg_id)
        if (
            candidate_pkg is not None
            and _canonical_evidence_url(str(candidate_pkg.get("source_url") or ""))
            == canon_url
        ):
            package = candidate_pkg
        else:
            package = package_by_url.get(canon_url) or {}

        evidence_package_id = str(
            package.get("package_id") or claimed_pkg_id or ""
        ).strip()
        proposition_ids = _resolve_lineage_ids(
            item.get("proposition_ids"),
            package.get("proposition_ids"),
        )
        support_passage_ids = _resolve_lineage_ids(
            item.get("support_passage_ids"),
            package.get("support_passage_ids"),
        )
        subdimensions = _dedupe_strings(
            item.get("subdimensions") or package.get("subdimensions") or []
        )

        raw_sentiment = str(item.get("sentiment") or "neutral").strip().lower()
        sentiment = (
            raw_sentiment
            if raw_sentiment in {"positive", "neutral", "negative"}
            else "neutral"
        )
        raw_item_conf = item.get("confidence", 0)
        item_confidence = (
            max(0, min(100, raw_item_conf)) if type(raw_item_conf) is int else 0
        )
        resolved_url = str(packet.get("url") or raw_url)
        dedupe_key = (_canonical_evidence_url(resolved_url), supported_claim.casefold())
        if dedupe_key in seen_finding_keys:
            continue
        seen_finding_keys.add(dedupe_key)

        compact_finding: dict[str, Any] = {
            "category": jury_name,
            "supported_claim": supported_claim,
            "found": bool(item.get("found", True)),
            "sentiment": sentiment,
            "source_quote": str(supporting_passage or source_quote)[:1200],
            "title": str(item.get("title") or packet.get("title") or "").strip(),
            "url": resolved_url,
            "is_recent": bool(item.get("is_recent", False)),
            "source_type": str(
                item.get("source_type") or packet.get("source_type") or ""
            ).strip(),
            "evidence_package_id": evidence_package_id,
            "proposition_ids": proposition_ids,
            "support_passage_ids": support_passage_ids,
            "subdimensions": subdimensions,
            "entity_scope": str(item.get("entity_scope") or "company").strip()
            or "company",
            "source_verdict": (
                "accepted" if support_level in {"exact", "partial"} else "scoped_context"
            ),
            "support_level": support_level,
            "confidence": item_confidence,
        }
        source_id = str(
            packet.get("source_id")
            or package.get("source_id")
            or item.get("source_id")
            or ""
        ).strip()
        if source_id:
            compact_finding["source_id"] = source_id
        accepted_findings.append(compact_finding)

    normalized_lane: dict[str, Any] = {
        "jury": jury_name,
        "findings": accepted_findings,
        "rejected_evidence": rejected_out,
        "contradictions": [s.strip() for s in raw_contradictions if s.strip()],
        "missing_coverage": [s.strip() for s in raw_missing if s.strip()],
        "confidence_score": raw_confidence if accepted_findings else 0,
        "reality_score_adjustment": raw_adjustment if accepted_findings else 0,
    }
    if "split_analysis" in payload and type(payload.get("split_analysis")) is bool:
        normalized_lane["split_analysis"] = payload["split_analysis"]
    return normalized_lane


def _analyze_single_jury_lane(
    *,
    company_name: str,
    jury_name: str,
    packets: list[dict[str, Any]],
    primary_model: str,
    fallback_model: str,
    effort: str,
    verbosity: str,
    openai_fn: OpenAINoWebCaller,
    anthropic_fn: AnthropicNoWebCaller,
) -> Mapping[str, Any]:
    if not packets:
        return _empty_lane(jury_name)
    preview_packages = build_jury_evidence_packages(jury_name, packets, max_packages=10)
    if not preview_packages:
        return _empty_lane(jury_name)

    errors: list[str] = []
    split_chunks = _split_jury_packets(jury_name, packets)
    chunk_payloads: list[dict[str, Any]] = []
    structural_failure: Mapping[str, Any] | None = None

    for chunk_label, chunk_packets in split_chunks:
        if not build_jury_evidence_packages(jury_name, chunk_packets, max_packages=10):
            continue
        chunk_payload = _run_chunk_with_fallback(
            company_name=company_name,
            jury_name=jury_name,
            chunk_label=chunk_label if len(split_chunks) > 1 else "",
            chunk_packets=chunk_packets,
            primary_model=primary_model,
            fallback_model=fallback_model,
            effort=effort,
            verbosity=verbosity,
            openai_fn=openai_fn,
            anthropic_fn=anthropic_fn,
            errors=errors,
        )
        if chunk_payload is not None:
            if not _has_valid_lane_types(chunk_payload):
                structural_failure = chunk_payload
            else:
                chunk_payloads.append(chunk_payload)

    if structural_failure is not None and not chunk_payloads:
        return structural_failure

    merged: dict[str, Any] | None = (
        _merge_jury_payloads(jury_name, chunk_payloads) if chunk_payloads else None
    )
    if merged is not None and len(split_chunks) == 1:
        merged["split_analysis"] = False

    if not (merged or {}).get("findings"):
        rescue_payload = _run_rescue_call(
            company_name=company_name,
            jury_name=jury_name,
            packets=packets,
            primary_model=primary_model,
            effort=effort,
            openai_fn=openai_fn,
            errors=errors,
        )
        if rescue_payload is not None:
            if not _has_valid_lane_types(rescue_payload):
                if merged is None:
                    return rescue_payload
            else:
                merged = (
                    _merge_jury_payloads(jury_name, [merged, rescue_payload])
                    if merged is not None
                    else _merge_jury_payloads(jury_name, [rescue_payload])
                )
                if merged is not None and len(split_chunks) <= 1:
                    merged["split_analysis"] = False

    if merged is None:
        detail = "; ".join(errors) if errors else "no parseable provider payload"
        raise JuryCognitiveUnavailableError(
            f"No-web Jury cognition unavailable for {jury_name}: {detail}"
        )
    return merged


def _run_chunk_with_fallback(
    *,
    company_name: str,
    jury_name: str,
    chunk_label: str,
    chunk_packets: list[dict[str, Any]],
    primary_model: str,
    fallback_model: str,
    effort: str,
    verbosity: str,
    openai_fn: OpenAINoWebCaller,
    anthropic_fn: AnthropicNoWebCaller,
    errors: list[str],
) -> dict[str, Any] | None:
    prompt = _build_jury_analysis_prompt(company_name, jury_name, chunk_packets)
    gpt_timeout = FORENSIC_JURY_GPT_TIMEOUTS.get(
        jury_name, FORENSIC_JURY_GPT_TIMEOUT_SECONDS
    )
    if chunk_label:
        if jury_name == SERVICE_PORTFOLIO_JURY_NAME:
            gpt_timeout = min(gpt_timeout, 120)
        elif jury_name == STRATEGIC_JURY_NAME:
            gpt_timeout = min(gpt_timeout, 105)
        else:
            gpt_timeout = min(gpt_timeout, 75)

    try:
        raw_primary = openai_fn(
            prompt,
            model=primary_model,
            max_output_tokens=6500,
            timeout=max(1, gpt_timeout - 5),
            web_search=False,
            reasoning_effort=effort,
            text_verbosity=verbosity,
            max_retries=0,
            json_schema=_FORENSIC_JURY_JSON_SCHEMA,
            json_schema_name="forensic_jury",
            log_label=f"Forensic Jury {jury_name}",
        )
        parsed = _parse_json_object(raw_primary)
        if parsed is not None:
            return parsed
        errors.append(f"{primary_model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"{primary_model}:{type(error).__name__}")

    if jury_name in FORENSIC_SKIP_SONNET_ANALYSIS_FALLBACK_JURIES:
        return None

    sonnet_timeout = FORENSIC_JURY_SONNET_TIMEOUTS.get(
        jury_name, FORENSIC_JURY_SONNET_TIMEOUT_SECONDS
    )
    if chunk_label:
        if jury_name in {STRATEGIC_JURY_NAME, SERVICE_PORTFOLIO_JURY_NAME}:
            sonnet_timeout = min(sonnet_timeout, 60)
        else:
            sonnet_timeout = min(sonnet_timeout, 45)

    try:
        raw_fallback = anthropic_fn(
            prompt,
            model=fallback_model,
            max_tokens=6500,
            timeout=max(1, sonnet_timeout - 5),
            web_search=False,
            max_retries=0,
            reasoning_effort=effort,
            json_schema=_FORENSIC_JURY_JSON_SCHEMA,
            json_schema_name="forensic_jury",
            log_label=f"Forensic Jury {jury_name}",
        )
        parsed_fallback = _parse_json_object(raw_fallback)
        if parsed_fallback is not None:
            return parsed_fallback
        errors.append(f"{fallback_model}:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"{fallback_model}:{type(error).__name__}")

    return None


def _run_rescue_call(
    *,
    company_name: str,
    jury_name: str,
    packets: list[dict[str, Any]],
    primary_model: str,
    effort: str,
    openai_fn: OpenAINoWebCaller,
    errors: list[str],
) -> dict[str, Any] | None:
    prompt = _build_jury_rescue_prompt(company_name, jury_name, packets)
    try:
        raw_rescue = openai_fn(
            prompt,
            model=primary_model,
            max_output_tokens=3500,
            timeout=max(1, FORENSIC_JURY_RESCUE_TIMEOUT_SECONDS - 5),
            web_search=False,
            reasoning_effort=effort,
            text_verbosity="low",
            max_retries=0,
            json_schema=_FORENSIC_JURY_JSON_SCHEMA,
            json_schema_name="forensic_jury_rescue",
            log_label=f"Forensic Jury Rescue {jury_name}",
        )
        parsed = _parse_json_object(raw_rescue)
        if parsed is not None:
            return parsed
        errors.append(f"{primary_model}-rescue:empty_or_invalid_json")
    except Exception as error:
        errors.append(f"{primary_model}-rescue:{type(error).__name__}")
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


def _has_valid_lane_types(payload: Mapping[str, Any]) -> bool:
    findings = payload.get("findings")
    rejected = payload.get("rejected_evidence", [])
    contradictions = payload.get("contradictions", [])
    missing = payload.get("missing_coverage", [])
    confidence = payload.get("confidence_score")
    adjustment = payload.get("reality_score_adjustment")
    return (
        isinstance(findings, list)
        and all(isinstance(item, Mapping) for item in findings)
        and isinstance(rejected, list)
        and isinstance(contradictions, list)
        and all(isinstance(item, str) for item in contradictions)
        and isinstance(missing, list)
        and all(isinstance(item, str) for item in missing)
        and type(confidence) is int
        and 0 <= confidence <= 100
        and type(adjustment) is int
        and -100 <= adjustment <= 100
    )


def _resolve_lineage_ids(claimed: object, package_ids: object) -> list[str]:
    valid_pkg_ids = _dedupe_strings(package_ids)
    raw_claimed = _dedupe_strings(claimed)
    if valid_pkg_ids:
        matched = [item for item in raw_claimed if item in valid_pkg_ids]
        return matched if matched else valid_pkg_ids
    return raw_claimed


def _dedupe_strings(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _enrich_transient_packet(
    packet: Mapping[str, Any],
    *,
    company_name: str,
    company_url: str,
) -> dict[str, Any]:
    """Attach transient entity/readability verdicts for in-memory Jury normalization only.

    These verdicts never mutate durable `JurySourceCandidate` rows and are
    stripped before emitting compact `scanner.jury/v1` checkpoint findings.
    """

    enriched = dict(packet)
    packet_text = str(
        enriched.get("fetched_excerpt")
        or enriched.get("cached_excerpt")
        or enriched.get("snippet")
        or ""
    ).strip()
    has_text = bool(packet_text)
    if "content_readable" not in enriched:
        enriched["content_readable"] = has_text
    if "url_verified" not in enriched and enriched.get("url") and enriched.get("content_readable"):
        enriched["url_verified"] = True

    if (company_name or company_url) and "entity_match_status" not in enriched:
        verdict = assess_entity_match(
            enriched,
            company_name=company_name,
            company_domain=company_url,
            page_text=packet_text,
        )
        match_status = str(verdict.get("entity_match_status") or "unknown").lower()
        enriched["entity_match_status"] = match_status
        enriched["content_entity_match_status"] = match_status
        enriched["entity_verified"] = match_status in {"exact", "scoped"}
        if match_status == "wrong":
            enriched["wrong_entity"] = True
            enriched["wrong_entity_true"] = True
        if not enriched.get("entity_scope") and verdict.get("entity_scope"):
            enriched["entity_scope"] = verdict["entity_scope"]
    return enriched


def _empty_lane(jury_name: str) -> dict[str, Any]:
    return {
        "jury": jury_name,
        "findings": [],
        "rejected_evidence": [],
        "contradictions": [],
        "missing_coverage": [],
        "confidence_score": 0,
        "reality_score_adjustment": 0,
        "split_analysis": False,
    }
