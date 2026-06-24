"""Canonical evidence and claim graph shared by generation and verification.

The graph deliberately separates company evidence from macro foresight.  It is
stored alongside the legacy report fields so older renderers remain compatible.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


GRAPH_VERSION = "1.1"
_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_EVIDENCE_ID_RE = re.compile(r"\b(?:EVD|FOR)-[A-F0-9]{12}\b")


def canonicalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        query = [
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
        ]
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except Exception:
        return value.split("#", 1)[0].rstrip("/")


def _stable_id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12].upper()}"


def _content(item: dict) -> str:
    for key in (
        "fetched_excerpt", "normalized_content", "content_excerpt", "extracted_text",
        "cached_excerpt", "source_quote", "semantic_snippet", "snippet", "description",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _truth_score(item: dict, kind: str) -> int:
    score = 0
    score += 8 if item.get("url_verified") is True or item.get("verified") is True else 0
    score += 7 if item.get("content_verified") is True or item.get("source_readable") is True else 0
    score += 8 if item.get("entity_verified") is True or item.get("entity_match_status") in {"exact", "verified"} else 0
    score += 8 if item.get("claim_support_status") in {"exact", "supported", "partial"} else 0
    score += 10 if item.get("generation_eligible") is True else 0
    score += 6 if item.get("promotion_status") == "scoped_context" else 0
    score += 10 if kind == "foresight" and item.get("foresight_context_eligible") is True else 0
    score += min(len(_content(item)) // 250, 5)
    score -= 20 if item.get("wrong_entity") is True or item.get("entity_match_status") == "wrong" else 0
    score -= 15 if item.get("page_not_found") is True or item.get("source_audit_status") == "source_dead" else 0
    score -= 6 if item.get("url_verified") is False or item.get("verified") is False else 0
    return score


def _retrieval_event(item: dict) -> dict:
    return {
        "checked_at": item.get("last_verified_at") or item.get("fetched_at") or item.get("verified_at"),
        "url_verified": item.get("url_verified", item.get("verified")),
        "content_verified": item.get("content_verified", item.get("source_readable")),
        "entity_match_status": item.get("entity_match_status"),
        "claim_support_status": item.get("claim_support_status"),
        "audit_status": item.get("source_audit_status"),
    }


def _merge_duplicate(current: dict, candidate: dict, kind: str) -> dict:
    """Keep the strongest current verdict while retaining failed retrieval history."""
    winner, loser = (candidate, current) if _truth_score(candidate, kind) > _truth_score(current, kind) else (current, candidate)
    merged = dict(winner)
    history = list(current.get("retrieval_history") or [])
    history.extend(candidate.get("retrieval_history") or [])
    history.extend([_retrieval_event(current), _retrieval_event(candidate)])
    seen = set()
    merged["retrieval_history"] = []
    for event in history:
        key = repr(sorted((event or {}).items()))
        if key not in seen:
            seen.add(key)
            merged["retrieval_history"].append(event)
    merged["duplicate_verdicts_resolved"] = True
    merged["weaker_duplicate_status"] = _retrieval_event(loser)
    return merged


def _source_record(item: dict, kind: str) -> dict:
    url = canonicalize_url(item.get("url") or item.get("source_url") or "")
    prefix = "FOR" if kind == "foresight" else "EVD"
    evidence_id = item.get("evidence_id") if str(item.get("evidence_id") or "").startswith(prefix + "-") else _stable_id(prefix, url or repr(sorted(item.items())))
    record = dict(item)
    record.update({
        "evidence_id": evidence_id,
        "evidence_kind": "foresight_context" if kind == "foresight" else "company_evidence",
        "canonical_url": url,
        "url": item.get("url") or item.get("source_url") or url,
        "exact_passage": _content(item),
        "company_specific": kind != "foresight",
        "claim_type": "foresight_context" if kind == "foresight" else (item.get("claim_type") or "company_fact"),
    })
    if kind == "foresight":
        record["requires_company_specific_corroboration"] = True
        record["not_evidence_of_deployment"] = True
    record.setdefault("retrieval_history", [_retrieval_event(item)])
    return record


def build_evidence_manifest(company_links=None, foresight_links=None) -> list[dict]:
    by_key = {}
    for kind, links in (("company", company_links or []), ("foresight", foresight_links or [])):
        for item in links:
            if not isinstance(item, dict):
                continue
            record = _source_record(item, kind)
            key = (record["evidence_kind"], record["canonical_url"] or record["evidence_id"])
            by_key[key] = _merge_duplicate(by_key[key], record, kind) if key in by_key else record
    records = sorted(by_key.values(), key=lambda row: (row["evidence_kind"] == "foresight_context", row["canonical_url"], row["evidence_id"]))
    for index, record in enumerate(records, 1):
        record["report_ref"] = index
        record["citation"] = f"[{index}]"
    return records


def extract_evidence_ids(text: str, manifest: list[dict] | None = None) -> list[str]:
    """Return stable evidence IDs explicitly cited by generated text."""
    allowed = {row.get("evidence_id") for row in (manifest or [])}
    ids = list(dict.fromkeys(_EVIDENCE_ID_RE.findall(text or "")))
    return [value for value in ids if not allowed or value in allowed]


def evidence_prompt_packet(manifest: list[dict], kind: str | None = None, limit: int = 30) -> str:
    """Compact immutable packet for generation prompts.

    Models cite stable IDs. Numeric report references are a rendering concern.
    """
    rows = [row for row in manifest if not kind or row.get("evidence_kind") == kind]
    lines = []
    for row in rows[:limit]:
        passage = (row.get("exact_passage") or "").strip().replace("\n", " ")
        mode = row.get("analysis_support_mode") or row.get("source_use_status") or row.get("promotion_status") or ""
        context = row.get("source_context_status") or ""
        support = row.get("claim_support_status") or ""
        meta = " | ".join(
            part for part in (
                f"mode={mode}" if mode else "",
                f"source_context={context}" if context else "",
                f"claim_support={support}" if support else "",
            )
            if part
        )
        lines.append(
            f"[{row['evidence_id']}] {row.get('title') or row.get('name') or 'Source'} | "
            f"{row.get('canonical_url') or row.get('url') or ''} | {meta} | {passage[:1200]}"
        )
    return "\n".join(lines)


def _claim_record(text: str, phase: str, evidence_ids: list[str], claim_type: str, index: int, **extra) -> dict:
    text = (text or "").strip()
    record = {
        "claim_id": _stable_id("CLM", f"{phase}|{index}|{text}"),
        "claim": text,
        "phase": phase,
        "claim_type": claim_type,
        "evidence_ids": list(dict.fromkeys(evidence_ids or [])),
        "premise_ids": list(dict.fromkeys(evidence_ids or [])),
        "support": extra.pop("support", "pending_inference_audit"),
        "confidence": extra.pop("confidence", None),
        "scope": extra.pop("scope", "company"),
        "uncertainty": extra.pop("uncertainty", ""),
        "reasoning": extra.pop("reasoning", ""),
    }
    record.update(extra)
    return record


def _narrative_claims(text: str) -> list[str]:
    """Split prose into auditable claims without pretending headings are claims."""
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) >= 24 and not chunk.lstrip().startswith("#")]


def build_claim_graph(data: dict, manifest: list[dict]) -> tuple[list[dict], list[dict]]:
    url_to_id = {row.get("canonical_url"): row["evidence_id"] for row in manifest}
    claims, contradictions = [], []
    for index, finding in enumerate(data.get("reality_checklist") or [], 1):
        if not isinstance(finding, dict) or finding.get("found") is False:
            continue
        text = finding.get("finding") or finding.get("query") or finding.get("title") or ""
        if not text:
            continue
        url = canonicalize_url(finding.get("url") or finding.get("source_url") or "")
        evidence_ids = [url_to_id[url]] if url in url_to_id else []
        claims.append(_claim_record(text, "jury_reality", evidence_ids, "company_fact", index, support=finding.get("claim_support_status") or "pending"))
    strategic = data.get("strategic_foresight") or {}
    forces = list(strategic.get("forces") or []) + list(strategic.get("dimension_forces") or [])
    for index, force in enumerate(forces, 1):
        if not isinstance(force, dict):
            continue
        text = force.get("description") or force.get("summary") or force.get("title") or force.get("theme") or ""
        ids = [value for value in (force.get("source_ids") or force.get("evidence_ids") or []) if str(value).startswith("FOR-")]
        for source in force.get("sources") or force.get("evidence") or []:
            if isinstance(source, dict):
                source_id = url_to_id.get(canonicalize_url(source.get("url") or source.get("source_url") or ""))
                if source_id and source_id.startswith("FOR-"):
                    ids.append(source_id)
        claims.append(_claim_record(text, "foresight", ids, "foresight_context", index, scope="macro", support="context"))
    for index, signal in enumerate(data.get("evidence_absence_signals") or [], 1):
        if not isinstance(signal, dict):
            continue
        text = signal.get("safe_wording") or signal.get("description") or ""
        if not text:
            continue
        claims.append(_claim_record(
            text,
            "evidence_absence",
            [],
            "absence_of_promoted_evidence",
            index,
            support="deterministic_evidence_state",
            scope="scanner_evidence_state",
            confidence=signal.get("severity_hint"),
            reasoning=(
                "Promoted evidence count is a deterministic Scanner source-register state. "
                "It may support proof-gap reasoning but never proves the capability is absent."
            ),
            signal_id=signal.get("signal_id"),
            dimension=signal.get("dimension"),
            promoted_evidence_count=signal.get("promoted_evidence_count"),
            candidate_source_count=signal.get("candidate_source_count"),
            verification_contract=signal.get("verification_contract"),
            citation_status="not_source_claim",
        ))
    narrative_fields = (
        ("summary_text", "diagnosis", "diagnostic_inference"),
        ("strategic_synthesis", "strategic_synthesis", "strategic_synthesis"),
        ("general_summary", "general_summary", "company_fact"),
        ("detailed_report", "detailed_report", "diagnostic_inference"),
    )
    for field, phase, claim_type in narrative_fields:
        text = data.get(field)
        if not isinstance(text, str) or not text.strip():
            continue
        for atomic_text in _narrative_claims(text):
            ids = extract_evidence_ids(atomic_text, manifest)
            claims.append(_claim_record(
                atomic_text,
                phase,
                ids,
                claim_type,
                len(claims) + 1,
                support="pending_inference_audit" if claim_type != "company_fact" else "pending_fact_check",
                scope="mixed" if phase in {"strategic_synthesis", "detailed_report"} else "company",
                citation_status="explicit" if ids else "missing",
            ))
    for index, item in enumerate(data.get("contradictions") or [], 1):
        text = item.get("description") if isinstance(item, dict) else str(item)
        contradictions.append({
            "contradiction_id": _stable_id("CON", f"{index}|{text}"),
            "description": text,
            "claim_ids": item.get("claim_ids", []) if isinstance(item, dict) else [],
            "status": "unresolved",
        })
    return claims, contradictions


def build_foresight_health(manifest: list[dict]) -> dict:
    rows = [row for row in manifest if row.get("evidence_kind") == "foresight_context"]
    eligible = [row for row in rows if row.get("foresight_context_eligible") is True]
    domains = {urlsplit(row.get("canonical_url") or "").netloc for row in eligible if row.get("canonical_url")}
    unreadable = sum(1 for row in rows if row.get("source_readable") is False or row.get("content_verified") is False)
    freshness = [row.get("freshness_status") for row in eligible]
    weak = len(eligible) < 3 or len(domains) < 2 or unreadable > max(2, len(rows) // 3)
    themes = {}
    for row in rows:
        theme = row.get("theme") or row.get("foresight_theme") or "Unclassified"
        bucket = themes.setdefault(theme, {"active_signals": 0, "total_signals": 0, "domains": set(), "unreadable_sources": 0, "fresh_or_current": 0, "last_harvest": ""})
        bucket["total_signals"] += 1
        if row.get("source_readable") is False or row.get("content_verified") is False:
            bucket["unreadable_sources"] += 1
        if row.get("foresight_context_eligible") is True:
            bucket["active_signals"] += 1
            domain = urlsplit(row.get("canonical_url") or "").netloc
            if domain:
                bucket["domains"].add(domain)
            if row.get("freshness_status") in {"fresh", "current", "evergreen_context"}:
                bucket["fresh_or_current"] += 1
        bucket["last_harvest"] = max(bucket["last_harvest"], row.get("harvested_at") or "")
    theme_health = {}
    for theme, bucket in themes.items():
        diversity = len(bucket.pop("domains"))
        bucket["source_diversity"] = diversity
        bucket["refresh_recommended"] = bucket["active_signals"] < 3 or diversity < 2 or bucket["unreadable_sources"] > 2
        theme_health[theme] = bucket
    return {
        "active_signals": len(eligible),
        "total_signals": len(rows),
        "source_diversity": len(domains),
        "unreadable_sources": unreadable,
        "fresh_or_current": sum(1 for value in freshness if value in {"fresh", "current", "evergreen_context"}),
        "last_harvest": max((row.get("harvested_at") or "" for row in rows), default=""),
        "refresh_recommended": weak,
        "themes": theme_health,
    }


def attach_evidence_graph(data: dict) -> dict:
    foresight_links = list(data.get("foresight_links") or [])
    strategic = data.get("strategic_foresight") or {}
    # The synthesizer may use the structured foresight evidence directly even
    # when a legacy foresight_links copy contains a failed duplicate fetch.
    # Put both into one canonical manifest; duplicate resolution keeps the
    # strongest current verification and retains all retrieval history.
    for item in strategic.get("foresight_evidence") or []:
        if isinstance(item, dict):
            foresight_links.append(item)
    for force in list(strategic.get("forces") or []) + list(strategic.get("dimension_forces") or []):
        if not isinstance(force, dict):
            continue
        for item in force.get("sources") or force.get("evidence") or []:
            if isinstance(item, dict):
                foresight_links.append(item)
    manifest = build_evidence_manifest(data.get("evidence_links"), foresight_links)
    claims, contradictions = build_claim_graph(data, manifest)
    data["evidence_graph_version"] = GRAPH_VERSION
    data["evidence_manifest"] = manifest
    data["claim_graph"] = claims
    data["contradiction_graph"] = contradictions
    data["foresight_health"] = build_foresight_health(manifest)
    claims_by_evidence = {}
    for claim in claims:
        for evidence_id in claim.get("evidence_ids") or []:
            claims_by_evidence.setdefault(evidence_id, []).append(claim["claim_id"])
    for record in manifest:
        record["associated_claim_ids"] = claims_by_evidence.get(record["evidence_id"], [])
    refs = {row.get("canonical_url"): row for row in manifest}
    for field in ("evidence_links", "foresight_links"):
        for link in data.get(field) or []:
            if not isinstance(link, dict):
                continue
            match = refs.get(canonicalize_url(link.get("url") or link.get("source_url") or ""))
            if match:
                link["evidence_id"] = match["evidence_id"]
                link["report_ref"] = match["report_ref"]
                link["citation"] = match["citation"]
    data["evidence_graph_created_at"] = datetime.now(timezone.utc).isoformat()
    return data
