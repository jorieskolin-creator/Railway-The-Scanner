"""
Reality Check — Hallucination Detection for AI-Generated Summaries

Two layers:
1. Heuristic pattern matching (zero API cost) — catches obvious fakes
2. AI-powered fact-check (GPT verifier with Claude Sonnet fallback)
   — verifies synthesis claims against generation-eligible evidence
   — if false claims found, also produces corrected summary in a follow-up call

Returns a hallucination risk level: "low", "medium", "high"
and a list of specific flags explaining what was detected.
"""

from __future__ import annotations

import re
import json
import os
import time
import hashlib
from foresight_guardrails import sanitize_future_insight, validate_foresight_text, validate_future_deadlines
from model_policy import CLAUDE_SONNET_MODEL, GPT_REASONING_MODEL, GPT_VERIFIER_MODEL
from llm_clients import call_anthropic_text, call_openai_text


def _clean_env_var(value: str) -> str:
    if not value:
        return ""
    if "=" in value:
        try:
            value = value.split("=", 1)[1]
        except Exception:
            pass
    return value.strip().strip('"').strip("'")


def _get_openai_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    parts = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_text = getattr(content, "text", None)
            if content_text:
                parts.append(content_text)
    return "\n".join(parts).strip()


def _call_openai_text(
    prompt: str,
    model: str = GPT_REASONING_MODEL,
    max_output_tokens: int = 10000,
    timeout: int = 90,
    json_schema: dict | None = None,
    json_schema_name: str = "scanner_verifier",
) -> str:
    verifier_route = model == GPT_VERIFIER_MODEL
    return call_openai_text(
        prompt,
        model=model,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        max_retries=0,
        reasoning_effort="low" if verifier_route else "medium",
        text_verbosity="low" if verifier_route else "medium",
        json_schema=json_schema,
        json_schema_name=json_schema_name,
        log_label="Reality Verifier" if verifier_route else "Scanner Reasoning",
    )


_FACT_CHECK_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary_accurate": {"type": "boolean"},
        "false_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "claim": {"type": "string"},
                    "claim_class": {"type": "string"},
                    "error_type": {"type": "string"},
                    "support_level": {"type": "string"},
                    "missing_or_contradicted_premise": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "correction": {"type": "string"},
                    "wrong_value": {"type": "string"},
                    "correct_value": {"type": "string"},
                    "source_indices": {"type": "array", "items": {"type": "integer"}},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "section", "claim", "claim_class", "error_type", "support_level",
                    "missing_or_contradicted_premise", "reasoning", "correction",
                    "wrong_value", "correct_value", "source_indices", "source_ids",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary_accurate", "false_claims"],
    "additionalProperties": False,
}

try:
    from source_quality import (
        annotate_evidence_links, filter_admissible_sources,
        is_boilerplate_snippet, is_title_echo, tier_snippet_bounds, describe_source,
        is_generation_eligible_source, is_analysis_premise_source,
        is_foresight_context_eligible_source,
    )
except Exception:  # Keep reality_check import-safe in minimal deployments.
    annotate_evidence_links = None
    filter_admissible_sources = None
    is_boilerplate_snippet = None
    is_title_echo = None
    tier_snippet_bounds = None
    describe_source = None
    is_generation_eligible_source = None
    is_analysis_premise_source = None
    is_foresight_context_eligible_source = None

# Regex for detecting scan history references in fact-check claims.
# These come from internal DB records (scan_history), not external web sources,
# so the fact-checker will always label them "Fabrication" with source_indices=[].
_SCAN_HISTORY_RE = re.compile(
    r'\d+\s*→\s*\d+'              # arrow-notation scores: 72→76→75
    r'|across\s+\w+\s+scans?'     # "across four scans"
    r'|across\s+\w+\s+assessments?'  # "across four assessments"
    r'|score\s+trajectory'         # "score trajectory"
    r'|previous\s+scans?|prior\s+scans?',  # "previous scans / prior scans"
    re.IGNORECASE,
)


_INTERNAL_SCAN_OBSERVATION_RE = re.compile(
    r'\b(?:scan|scanner|evidence\s+packet|stored\s+evidence|source\s+manager|source\s+gate'
    r'|reality\s+check|intent\s+score|reality\s+score|confidence\s+score|summary\s+confidence|archetype'
    r'|dimension\s+(?:returns?|shows?|has|lacks?|contains?)'
    r'|no\s+company-specific\s+evidence|single\s+weak\s+signal|weak\s+(?:signal|evidence)'
    r'|public\s+evidence\s+is\s+limited|limited\s+public\s+evidence|evidence\s+is\s+sparse'
    r'|generation-eligible|analysis-verified|display-only|wrong-entity)\b',
    re.IGNORECASE,
)


def _is_internal_scanner_observation(text: str) -> bool:
    """Return true for claims about Scanner outputs rather than external company facts."""
    return bool(text and _INTERNAL_SCAN_OBSERVATION_RE.search(text))


def _canonical_fact_url(value: str) -> str:
    """Return a stable URL key without weakening source identity."""
    normalized = str(value or "").strip().lower()
    normalized = re.sub(r"^http://", "https://", normalized)
    return normalized.rstrip("/")


def _financial_value_tokens(value: str) -> list[str]:
    tokens = re.findall(
        r'(?:€\s*)?\d+(?:[.,]\d+)?\s*(?:m|million|%|bn|billion)?',
        str(value or "").lower(),
    )
    output = []
    for token in tokens:
        normalized = re.sub(r'\s+', '', token.replace(",", "."))
        normalized = re.sub(r"(?:million|milj\.?)(?:euros?)?", "m", normalized)
        normalized = re.sub(r"billion", "bn", normalized)
        normalized = normalized.replace("€", "")
        bare_number = re.sub(r'[^0-9.]', '', normalized)
        # Reporting years belong to the governed period key, not to the value
        # pool that may be combined across records.
        if re.fullmatch(r"20\d{2}", bare_number):
            continue
        if len(normalized) >= 2:
            output.append(normalized)
    return list(dict.fromkeys(output))


def _normalize_financial_support_text(value: str) -> str:
    normalized = str(value or "").lower().replace(",", ".")
    normalized = re.sub(r"(?:million|milj\.?)(?:\s*euros?)?", "m", normalized)
    normalized = re.sub(r"billion", "bn", normalized)
    normalized = normalized.replace("€", "")
    return re.sub(r"\s+", "", normalized)


def _financial_group_key(record: dict) -> tuple[str, str, tuple[str, ...]]:
    source_url = _canonical_fact_url(
        record.get("source_url") or record.get("canonical_url") or record.get("url")
    )
    entity = str(
        record.get("legal_entity")
        or record.get("entity_name")
        or record.get("entity_scope")
        or "unknown"
    ).strip().lower()
    periods = record.get("periods") or []
    if isinstance(periods, str):
        periods = re.findall(r"\b(?:FY\s*)?20\d{2}\b", periods, flags=re.IGNORECASE)
    if not isinstance(periods, list):
        periods = []
    period_values = {
        re.sub(r"^FY", "", re.sub(r"\s+", "", str(value or "").upper()))
        for value in periods
        if str(value or "").strip()
    }
    if not period_values:
        record_text = " ".join(str(record.get(key) or "") for key in (
            "assessment", "finding", "evidence_snippet", "metric", "value",
        ))
        period_values = {
            re.sub(r"^FY", "", re.sub(r"\s+", "", value.upper()))
            for value in re.findall(r"\b(?:FY\s*)?20\d{2}\b", record_text, flags=re.IGNORECASE)
        }
    return source_url, entity, tuple(sorted(period_values))


def _structured_financial_claim_support(text: str, scan_payload: dict | None) -> dict:
    """Resolve exact values against one canonical source/entity/period group."""
    empty = {"supported": False, "financial_fact_ids": [], "support_group": {}}
    if not text or not isinstance(scan_payload, dict):
        return empty
    if not re.search(r'\b(?:revenue|sales|ebit|ebitda|profit|margin|cash flow|equity|financial|fy20\d{2}|€|\b\d+(?:\.\d+)?%|\bm\b|\bmillion)\b', text, re.IGNORECASE):
        return empty
    financial = scan_payload.get("financial_payload") or {}
    records = [
        item for item in (financial.get("financial_fact_manifest") or [])
        if isinstance(item, dict)
    ]
    if not records:
        legacy_text = json.dumps({
            str(key): value for key, value in financial.items()
            if isinstance(value, (str, int, float, bool))
        }, ensure_ascii=False)
        values = _financial_value_tokens(text)
        if values and all(value in _normalize_financial_support_text(legacy_text) for value in values):
            return {
                "supported": True,
                "financial_fact_ids": [],
                "support_group": {"legacy_structured_payload": True},
            }
        return empty
    values = _financial_value_tokens(text)
    claim_periods = {
        re.sub(r"^FY", "", re.sub(r"\s+", "", value.upper()))
        for value in re.findall(r"\b(?:FY\s*)?20\d{2}\b", text, flags=re.IGNORECASE)
    }
    groups: dict[tuple[str, str, tuple[str, ...]], list[dict]] = {}
    for record_index, record in enumerate(records):
        key = _financial_group_key(record)
        if key[1] == "unknown" or not key[2]:
            continue
        if not key[0]:
            # Legacy governed manifests may lack URL lineage. Keep such a
            # record independently usable, but never combine it with another
            # record merely because both omit a URL.
            key = (f"record:{record.get('fact_id') or record_index}", key[1], key[2])
        groups.setdefault(key, []).append(record)

    claim_words = [
        word for word in re.findall(r'[a-zåäöA-ZÅÄÖ]{4,}', text.lower())
        if word not in {"reported", "shows", "company", "annual", "financial"}
    ]
    for (source_url, entity, periods), group_records in groups.items():
        if claim_periods and not claim_periods.issubset(set(periods)):
            continue
        group_text = " ".join(
            " ".join(str(record.get(key) or "") for key in (
                "dimension", "assessment", "finding", "metric", "value",
                "evidence_snippet", "periods", "entity_scope", "legal_entity",
            ))
            for record in group_records
        ).lower()
        normalized_group = _normalize_financial_support_text(group_text)
        values_supported = bool(values) and all(value in normalized_group for value in values[:8])
        words_supported = (
            not values
            and len(claim_words) >= 4
            and sum(1 for word in claim_words[:12] if word in group_text) >= 4
        )
        if values_supported or words_supported:
            return {
                "supported": True,
                "financial_fact_ids": [
                    str(record.get("fact_id") or "")
                    for record in group_records
                    if str(record.get("fact_id") or "")
                ],
                "support_group": {
                    "canonical_source_url": source_url,
                    "legal_entity": entity,
                    "reporting_periods": list(periods),
                },
            }
    return empty


def _is_supported_structured_financial_payload_claim(text: str, scan_payload: dict | None) -> bool:
    """Return true when one governed source/entity/period group supports the claim."""
    return bool(_structured_financial_claim_support(text, scan_payload).get("supported"))


_AUDIT_NOTE_LABELS = {
    "wording_precision_note": "Wording precision",
    "citation_scope_note": "Citation scope",
    "structured_payload_supported": "Structured payload supported",
    "inference_scope_note": "Inference scope",
    "scanner_evidence_state_note": "Scanner evidence state",
    "unsafe_inference_wording": "Inference wording",
    "evidence_handoff_failure": "Evidence handoff",
}


def sanitize_inference_wording(value):
    """Rewrite over-assertive proof-gap wording without changing hard facts.

    Absence/proof-gap signals are useful diagnostic premises, but they should
    never be phrased as proof that a company has acted or that a capability is
    absent. This sanitizer keeps the inference, but turns the unsafe proof
    wording into a bounded question.
    """
    if isinstance(value, list):
        return [sanitize_inference_wording(v) for v in value]
    if isinstance(value, dict):
        return {k: sanitize_inference_wording(v) for k, v in value.items()}
    if not isinstance(value, str) or not value:
        return value

    text = value

    def _question(match):
        premise = (match.group(1) or "").strip()
        premise = re.sub(r'\s+', ' ', premise).rstrip(" .;:")
        if not premise:
            return match.group(0)
        return f"the unresolved question is whether {premise}"

    text = re.sub(
        r'\b(?:the\s+)?(?:evidence\s+|proof\s+)?gap\s+(?:is|serves\s+as|acts\s+as)\s+proof\s+that\s+([^.;\n]+)',
        _question,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'\b(?:lack|absence)\s+of\s+(?:public\s+|promoted\s+|verified\s+)?evidence\s+(?:proves|shows|demonstrates)\s+that\s+([^.;\n]+)',
        _question,
        text,
        flags=re.IGNORECASE,
    )
    proof_gap_rewrites = (
        (
            r'\b(?:the\s+)?(?:current\s+)?promoted evidence set contains no source verifying\s+([^.;\n]+)',
            r'the current promoted evidence set does not yet provide enough direct support to confirm \1',
        ),
        (
            r'\bno promoted source directly verifies\s+([^.;\n]+)',
            r'the promoted source set does not yet provide enough direct support to confirm \1',
        ),
        (
            r'\bno source verifying\s+([^.;\n]+)',
            r'the available source set does not yet provide enough support to confirm \1',
        ),
        (
            r'\bdoes not verify broader\s+([^.;\n]+)',
            r'does not yet independently confirm broader \1',
        ),
        (
            r'\bdoes not verify the breadth of\s+([^.;\n]+)',
            r'does not yet independently confirm the breadth of \1',
        ),
    )
    for pattern, replacement in proof_gap_rewrites:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'\s+([.;,])', r'\1', text)
    return text


def _is_actual_scanner_process_observation(text: str, reasoning: str = "") -> bool:
    """Return true only for Scanner process/telemetry claims, not ordinary diagnostic inferences."""
    combined = f"{text or ''} {reasoning or ''}".lower()
    if _SCAN_HISTORY_RE.search(combined):
        return True
    process_markers = (
        "scanner", "scan payload", "source register", "source gate", "reality check",
        "evidence packet", "generation-eligible", "analysis-verified", "display-only",
        "wrong-entity", "intent score", "reality score", "confidence score",
        "ambition score", "source manager", "forensic scan", "jury coverage",
        "previous scan", "prior scan", "across scans", "score trajectory",
    )
    return any(marker in combined for marker in process_markers)


def _classify_fact_check_issue(fc: dict, scan_payload: dict | None) -> tuple[str, bool]:
    """
    Convert model fact-check labels into materiality-aware issue types.

    Returns (issue_type, is_blocking). Only blocking issues should feed
    correction/removal logic; the rest are audit notes.
    """
    claim_text = str(fc.get("claim") or "")
    error_type = str(fc.get("error_type") or "").strip().lower()
    claim_class = str(fc.get("claim_class") or "").strip().lower()
    support_level = str(fc.get("support_level") or "unsupported").strip().lower()
    missing_premise = str(fc.get("missing_or_contradicted_premise") or "").strip()
    reasoning = str(fc.get("reasoning") or "")
    wrong_value = str(fc.get("wrong_value") or "").strip()
    correct_value = str(fc.get("correct_value") or "").strip()
    combined = f"{claim_text} {reasoning} {missing_premise}".lower()

    if error_type == "evidence handoff failure":
        return "evidence_handoff_failure", False

    if _is_supported_structured_financial_payload_claim(claim_text, scan_payload):
        return "structured_payload_supported", False

    if _SCAN_HISTORY_RE.search(claim_text):
        return "blocking_false_claim", True

    if error_type == "prohibited scanner observation":
        if _is_actual_scanner_process_observation(claim_text, reasoning):
            return "blocking_scanner_process_language", True
        return "inference_scope_note", False

    if claim_class == "structured_financial_payload_fact":
        if support_level in {"exact", "partial"}:
            return "structured_payload_supported", False
        return "blocking_false_claim", True

    # Harmless lexical precision should be visible but not make a good report
    # fail. This catches cases like enterprise application/applications.
    if (
        any(marker in combined for marker in (
            "singular", "plural", "wording", "phrasing", "label wording",
            "enumerated wording", "exact wording", "terminology",
            "should match the verified page", "source lists",
        ))
        and not wrong_value
        and not correct_value
    ):
        return "wording_precision_note", False

    # A verifier may mistakenly label a sentence containing a concrete company
    # event or present capability as strategic inference. Material facts remain
    # blocking regardless of that model-provided class.
    concrete_company_fact = bool(
        wrong_value
        or correct_value
        or any(marker in combined for marker in (
            "wrong entity", "wrong date", "wrong value", "fabricated event",
            "did not happen", "contradicts the source", "contradicts the evidence",
        ))
        or re.search(
            r"\b(?:has|have|had)\s+(?:deployed|implemented|signed|secured|launched|"
            r"acquired|selected|adopted|appointed|opened|closed)\b"
            r"|\bcurrently\s+(?:uses|operates|deploys|employs)\b"
            r"|\breported\s+(?:revenue|profit|loss|margin|growth)\s+of\b",
            claim_text,
            re.IGNORECASE,
        )
    )
    if support_level == "unsupported" and concrete_company_fact:
        return "blocking_false_claim", True

    if re.search(
        r'\b(?:evidence\s+|proof\s+)?gap\s+(?:is|serves\s+as|acts\s+as)\s+proof\s+that\b'
        r'|\b(?:lack|absence)\s+of\s+(?:public\s+|promoted\s+|verified\s+)?evidence\s+'
        r'(?:proves|shows|demonstrates)\s+that\b',
        combined,
        re.IGNORECASE,
    ):
        return "wording_calibration", False

    # Unsupported causal or comparative interpretations must be neutralized,
    # but they are not the same as a false company fact. Keep them out of the
    # blocking/removal path and require a narrower reader-facing replacement.
    causal_markers = (
        "driven by", "caused by", "because of", "as a result of", "resulting from",
        "the driver", "primary driver", "main driver", "attributable to",
    )
    calibration_markers = causal_markers + (
        "overstates", "overstated", "too strong", "stronger than", "not established",
        "not supported", "not evidenced", "comparative claim", "concentrated",
        "concentration", "financing mechanism", "scope is broader", "scope mismatch",
    )
    if error_type in {"overstrong interpretation", "inference calibration"}:
        return "wording_calibration", False
    if (
        claim_class in {"strategic_inference", "macro_foresight_context"}
        and support_level in {"partial", "context_only", "unsupported"}
        and (
            any(marker in combined for marker in calibration_markers)
            or (
                "causal" in combined
                and any(marker in combined for marker in ("unsupported", "not supported", "not established"))
            )
        )
    ):
        return "wording_calibration", False

    # A source supporting a narrower claim is an audit/scope note unless the
    # generated statement has a wrong value/entity/date or material contradiction.
    if support_level in {"partial", "context_only"} and any(marker in combined for marker in (
        "narrower", "partially support", "partially supported", "generally right",
        "context", "scope", "overstates", "overstated", "not cleanly as a direct quote",
    )):
        if wrong_value or correct_value or "wrong entity" in combined:
            return "blocking_false_claim", True
        return "wording_calibration", False

    if claim_class in {"macro_foresight_context", "strategic_inference"}:
        if support_level in {"exact", "partial", "context_only"}:
            return "inference_scope_note", False
        if "absent from the payload" in combined or "not present in the scan payload" in combined:
            return "inference_scope_note", False
        # Unsupported analytical language should be narrowed to what the
        # supported premises permit. It becomes blocking only when it embeds a
        # wrong company fact, value, date, entity, or present-tense capability.
        if not any(marker in combined for marker in (
            "wrong entity", "wrong date", "wrong value", "contradicts", "fabricated event",
            "did not happen", "has deployed", "currently uses", "already implemented",
        )):
            return "wording_calibration", False

    # Diagnostic statements about limited evidence/proof gaps are synthesis
    # notes, not external facts, unless they smuggle in a concrete unsupported
    # value/entity/date.
    if re.search(
        r'public evidence is limited|limited public evidence|remains? unverified|proof gap|'
        r'evidence gap|available evidence|not independently verified|no measurable|'
        r'insufficient public evidence|no public evidence',
        combined,
        re.IGNORECASE,
    ):
        if wrong_value or correct_value:
            return "blocking_false_claim", True
        return "inference_scope_note", False

    return "blocking_false_claim", True


def _format_audit_note(issue_type: str, claim_text: str, reasoning: str = "") -> str:
    label = _AUDIT_NOTE_LABELS.get(issue_type, issue_type.replace("_", " ").title())
    note = f"[{label}] {claim_text}".strip()
    if reasoning:
        note += f" — {reasoning}"
    return note


def _strip_code_fences(text):
    """Strip markdown code fences (```json ... ```) from LLM responses."""
    if not text:
        return text
    # First try to extract content from a complete code block
    block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if block_match:
        return block_match.group(1).strip()
    # Fallback: remove all code fence markers
    stripped = re.sub(r'```(?:json)?\s*', '', text).strip()
    return stripped


def _balance_brackets(text):
    """
    Close unclosed [ brackets that appear before } in JSON text.
    Walks the string respecting JSON string literals so we don't
    modify content inside quoted values.
    """
    result = list(text)
    stack = []  # track ( [ { nesting
    in_string = False
    escape = False
    i = 0
    insertions = []  # (position, char_to_insert)
    for i, ch in enumerate(result):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            stack.append(ch)
        elif ch == ']':
            # Pop matching [
            if stack and stack[-1] == '[':
                stack.pop()
        elif ch == '}':
            # Close any unclosed [ before this }
            while stack and stack[-1] == '[':
                insertions.append((i, ']'))
                stack.pop()
            if stack and stack[-1] == '{':
                stack.pop()
    # Apply insertions in reverse order so positions stay valid
    for pos, char in reversed(insertions):
        result.insert(pos, char)
    # Close anything still open at the end
    while stack:
        bracket = stack.pop()
        result.append(']' if bracket == '[' else '}')
    return ''.join(result)


def _robust_parse_json(text):
    """
    Robustly parse JSON from LLM response text.
    Handles code fences, truncated responses, trailing commas, missing commas,
    and other common LLM JSON errors. Returns parsed dict or None.
    """
    if not text:
        return None

    text = _strip_code_fences(text)

    # 1. Try finding a markdown block first (in case strip didn't catch it)
    # Use greedy match for inner content to handle nested braces correctly
    markdown_match = re.search(r'```json\s*(\{.*\})\s*```', text, re.DOTALL)
    candidate = markdown_match.group(1) if markdown_match else None

    # 2. Fallback: Find the first outer { to last }
    if not candidate:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            candidate = match.group(0)

    # 3. If no closing brace found, try to complete truncated JSON
    if not candidate and '{' in text:
        # Extract from first { to end, then try adding closing braces
        start_idx = text.index('{')
        candidate = text[start_idx:]
        # Count unclosed braces/brackets and close them
        open_braces = candidate.count('{') - candidate.count('}')
        open_brackets = candidate.count('[') - candidate.count(']')
        # If the candidate ends mid-string, close the string first
        in_string = candidate.count('"') % 2 == 1
        if in_string:
            candidate += '..."'
        candidate += ']' * max(0, open_brackets)
        candidate += '}' * max(0, open_braces)

    if not candidate:
        return None

    # 4. Fast path: try parsing as-is
    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError as e:
        print(f"   🐛 [JSON debug] Fast-path failed: {e} | candidate[{max(0,e.pos-30)}:{e.pos+30}] = ...{candidate[max(0,e.pos-30):e.pos+30]}...")
        pass

    # 5. Auto-repair common LLM JSON errors
    repaired = candidate
    # Fix missing value after colon: "key":} or "key":]  →  "key":null} or "key":null]
    repaired = re.sub(r':\s*([}\]])', r': null\1', repaired)
    # Fix unclosed brackets/arrays inside the candidate
    # Walk the string (outside of JSON strings) and close any unclosed [ before }
    repaired = _balance_brackets(repaired)
    # Remove trailing commas before } or ]
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)
    # Fix missing commas between }{ or }" or ]" patterns
    repaired = re.sub(r'(\})\s*(\{)', r'\1,\2', repaired)
    repaired = re.sub(r'(\})\s*(")', r'\1,\2', repaired)
    repaired = re.sub(r'(\])\s*(")', r'\1,\2', repaired)
    # Fix missing commas after true/false/null before next key
    repaired = re.sub(r'(true|false|null)\s*\n\s*(")', r'\1,\2', repaired)
    # Fix missing commas after numbers before next key
    repaired = re.sub(r'(\d)\s*\n\s*(")', r'\1,\2', repaired)
    # Remove control characters that break JSON
    repaired = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', repaired)

    try:
        return json.loads(repaired, strict=False)
    except json.JSONDecodeError as e:
        print(f"   🐛 [JSON debug] Auto-repair failed: {e} | repaired[{max(0,e.pos-30)}:{e.pos+30}] = ...{repaired[max(0,e.pos-30):e.pos+30]}...")
        pass

    # 6. Iterative position-based repair (handles deeply nested errors)
    for _attempt in range(30):
        try:
            return json.loads(repaired, strict=False)
        except json.JSONDecodeError as e:
            msg = str(e)
            pos = e.pos
            if pos is None or pos >= len(repaired):
                break
            if "Expecting ',' delimiter" in msg:
                repaired = repaired[:pos] + ',' + repaired[pos:]
            elif "Expecting ':' delimiter" in msg:
                repaired = repaired[:pos] + ':' + repaired[pos:]
            elif "Expecting value" in msg and pos > 0 and repaired[pos - 1] == ',':
                # Trailing comma before closer — remove the comma
                repaired = repaired[:pos - 1] + repaired[pos:]
            elif "Expecting value" in msg and pos < len(repaired) and repaired[pos] in ('}', ']'):
                # Missing value before } or ] — insert null
                repaired = repaired[:pos] + 'null' + repaired[pos:]
            else:
                break

    # 7. Final attempt
    try:
        return json.loads(repaired, strict=False)
    except json.JSONDecodeError as e:
        print(f"   🐛 [JSON debug] All repairs failed: {e} | pos={e.pos} | full candidate ({len(repaired)} chars): {repr(repaired[:500])}")
        return None

# --- HEURISTIC PATTERNS ---
# These patterns indicate the AI is fabricating events rather than synthesizing evidence

BREAKING_NEWS_PATTERNS = [
    r"\b(?:just|recently)\s+announced\b",
    r"\b(?:today|yesterday|this morning|this afternoon)\b",
    r"\bbreaking\s*(?:news|:)\b",
    r"\b(?:has just|have just)\s+(?:been|appointed|fired|resigned|acquired)\b",
    r"\bmoments?\s+ago\b",
    r"\bearlier\s+today\b",
]

# Claims about government/political positions (extremely unlikely in corporate analysis)
POLITICAL_HALLUCINATION_PATTERNS = [
    r"\b(?:minister|prime\s+minister|president\s+of\s+(?:finland|the\s+republic))\b",
    r"\b(?:foreign\s+minister|defense\s+minister|finance\s+minister)\b",
    r"\b(?:nominated|appointed|elected)\s+(?:as|to\s+be)\s+(?:minister|president|senator|governor)\b",
    r"\b(?:parliament|congressional|senate)\s+(?:hearing|testimony|investigation)\b",
    r"\b(?:ambassador|diplomat|consul)\b",
]

# Extremely dramatic claims that require strong evidence
DRAMATIC_CLAIMS_PATTERNS = [
    r"\b(?:arrested|indicted|convicted|sentenced|imprisoned|jailed)\b",
    r"\b(?:bankrupt(?:cy)?|insolvent|liquidat(?:ed|ion))\b",
    r"\b(?:fraud(?:ulent)?|embezzl(?:ed|ement)|money\s+laundering)\b",
    r"\b(?:scandal|whistleblow|cover[\s-]?up)\b",
    r"\b(?:collapsed|imploded|destroyed|catastroph)\b",
    r"\b(?:fired|terminated|ousted|expelled)\s+(?:the\s+)?(?:CEO|CTO|CFO|board)\b",
]

# Patterns suggesting the AI is inventing specific people
FABRICATED_PERSON_PATTERNS = [
    r"(?:new|incoming|former)\s+CEO\s+(\w+\s+\w+)",
    r"(?:appointed|hired|named)\s+(\w+\s+\w+)\s+as\s+(?:CEO|CTO|CFO|COO|President)",
    r"CEO\s+(\w+\s+\w+)\s+(?:has\s+)?(?:resigned|stepped\s+down|been\s+replaced)",
]

# Temporal impossibility (summary shouldn't reference real-time events)
TEMPORAL_HALLUCINATION_PATTERNS = [
    r"\b(?:live|real[\s-]?time|unfolding|developing\s+story)\b",
    r"\b(?:confirmed\s+(?:minutes|hours)\s+ago)\b",
    r"\b(?:stocks?\s+(?:crashed|plummeted|surged)\s+(?:today|this\s+morning))\b",
]


# --- CLAIM-SAFETY VALIDATORS (Phase 1) ---
_COMPANY_WIDE_TERMS_RE = re.compile(
    r"\b(company-wide|enterprise|organisation|organization|across\s+the\s+(?:company|firm|enterprise)|the\s+company\s+(?:has|is|uses|lacks|struggles|deploys))\b",
    re.IGNORECASE,
)
_SCOPE_QUALIFIER_RE = re.compile(
    r"\b(in\s+HR|HR\s+personnel|survey\s+of|study\s+of|case\s+study|pilot|one\s+team|specific\s+team|department|respondents|sample)\b",
    re.IGNORECASE,
)
_FORESIGHT_FACT_RE = re.compile(
    r"\b(has\s+deployed|have\s+deployed|is\s+deploying|are\s+deploying|uses\s+multi-agent|broad\s+deployment|currently\s+uses|must\s+realign|requires\s+.+\bto\b|is\s+forced\s+to)\b",
    re.IGNORECASE,
)
_FORESIGHT_CONTEXT_RE = re.compile(
    r"\b(foresight|trend|forecast|scenario|by\s+20\d{2}|expected\s+to|could|may|net-zero\s+industry\s+act|digital\s+product\s+passport|agentic\s+AI|multi-agent)\b",
    re.IGNORECASE,
)


def _split_sentences_for_validation(text):
    if not text:
        return []
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]


def validate_scope_inflation(summary_text, evidence_links=None):
    """Flag company-wide language when the available evidence is narrow-only."""
    flags = []
    if not evidence_links:
        return flags
    narrow_markers = []
    for link in evidence_links:
        if not isinstance(link, dict):
            continue
        scope = (link.get("scope") or "").lower()
        stype = (link.get("source_type") or "").lower()
        text = f"{link.get('title','')} {link.get('snippet','')} {link.get('url','')}".lower()
        if scope == "narrow" or stype in {"academic_thesis", "thesis", "survey", "employee_review", "job_posting", "github"} or any(t in text for t in ["master's thesis", "respondents", "survey of", "case study", "pilot"]):
            narrow_markers.append(link)
    if not narrow_markers:
        return flags
    for sent in _split_sentences_for_validation(summary_text):
        if _COMPANY_WIDE_TERMS_RE.search(sent) and not _SCOPE_QUALIFIER_RE.search(sent):
            low = sent.lower()
            if any(term in low for term in ["ai readiness", "adoption", "proficiency", "silo", "operating model", "maturity", "lags", "fragmented"]):
                flags.append(f"Possible scope inflation from narrow evidence: '{sent[:180]}'")
    return flags[:5]


def validate_foresight_presented_as_fact(summary_text, evidence_links=None):
    """Flag trend/regulatory language stated as current company fact or deterministic obligation."""
    flags = []
    foresight_available = False
    for link in evidence_links or []:
        if not isinstance(link, dict):
            continue
        stype = (link.get("source_type") or "").lower()
        claim_types = link.get("allowed_claim_types") or []
        if stype in {"foresight", "foresight_report", "trend_report", "regulation", "regulatory_context"} or "foresight_context" in claim_types or "regulatory_context" in claim_types:
            foresight_available = True
            break
    # Even if evidence metadata is absent, obvious foresight terms + present-tense deployment verbs are risky.
    for sent in _split_sentences_for_validation(summary_text):
        if _FORESIGHT_CONTEXT_RE.search(sent) and _FORESIGHT_FACT_RE.search(sent):
            if foresight_available or any(t in sent.lower() for t in ["agentic", "multi-agent", "net-zero", "digital product passport", "nzia"]):
                flags.append(f"Possible foresight/regulatory context presented as fact: '{sent[:180]}'")
    return flags[:5]


def validate_evidence_admissibility(evidence_links=None):
    """Flag excluded evidence still present in the payload."""
    flags = []
    if not evidence_links or not annotate_evidence_links:
        return flags
    try:
        annotated = annotate_evidence_links(evidence_links, "")
        excluded = [l for l in annotated if l.get("admissible") is False]
        if excluded:
            reasons = {}
            for l in excluded:
                reason = l.get("exclusion_reason") or "excluded"
                reasons[reason] = reasons.get(reason, 0) + 1
            flags.append(f"Evidence payload contains inadmissible sources: {reasons}")
    except Exception as e:
        flags.append(f"Evidence admissibility validation failed: {e}")
    return flags


def validate_forward_looking_language(text: str, report_date=None, section: str = "Generated narrative"):
    """Validate deterministic foresight language and past deadlines in generated narratives."""
    flags = []
    if not text:
        return flags
    try:
        for warning in validate_foresight_text(text, report_date):
            flags.append(f"{section}: {warning}")
    except Exception as e:
        flags.append(f"{section}: foresight language validation failed: {e}")
    return flags


def sanitize_future_insight_language(text: str, report_date=None):
    """Rewrite deterministic foresight language and past deadlines."""
    cleaned, issues = sanitize_future_insight(text or "", report_date)
    return cleaned, issues


# Backward-compatible alias for any older maintenance scripts importing the
# previous helper name. New scan paths call validate_forward_looking_language.
validate_future_insight_language = validate_forward_looking_language


def validate_summary(summary_text, company_name, key_people=None, evidence_links=None, confidence_score=None):
    """
    Validates an AI-generated summary for hallucination indicators.

    Args:
        summary_text: The generated summary/synthesis text
        company_name: The target company name
        key_people: List of key_people dicts from Scanner 2 evidence
        evidence_links: List of evidence_link dicts from Scanner 2
        confidence_score: Scanner 2's confidence score (0-100)

    Returns:
        dict with:
            - risk: "low" | "medium" | "high"
            - flags: list of specific issues found
            - quality_notes: non-evidence format/style notes
            - score: 0-100 hallucination suspicion score
            - summary_ok: bool (True if safe to save as-is)
    """
    if not summary_text or len(summary_text.strip()) < 20:
        return {
            "risk": "high",
            "flags": ["Summary is empty or too short"],
            "quality_notes": [],
            "score": 90,
            "summary_ok": False,
        }

    flags = []
    quality_notes = []
    score = 0
    text_lower = summary_text.lower()

    # --- CHECK 1: Breaking news / temporal hallucination ---
    for pattern in BREAKING_NEWS_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            flags.append(f"Breaking news language detected: '{match.group()}'")
            score += 20

    for pattern in TEMPORAL_HALLUCINATION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            flags.append(f"Real-time event language: '{match.group()}'")
            score += 25

    # --- CHECK 2: Political/government hallucination ---
    for pattern in POLITICAL_HALLUCINATION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            flags.append(f"Political/government claim in corporate analysis: '{match.group()}'")
            score += 30

    # --- CHECK 3: Dramatic unsubstantiated claims ---
    for pattern in DRAMATIC_CLAIMS_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            # Only flag if confidence is low or no evidence supports it
            claim = match.group()
            has_evidence = False
            if evidence_links:
                for link in evidence_links:
                    snippet = (link.get("snippet", "") or "").lower()
                    title = (link.get("title", "") or "").lower()
                    if claim in snippet or claim in title:
                        has_evidence = True
                        break
            if not has_evidence:
                flags.append(f"Dramatic claim without supporting evidence: '{claim}'")
                score += 15

    # --- CHECK 4: Fabricated person names ---
    known_names = set()
    if key_people:
        for person in key_people:
            name = (person.get("name", "") or "").lower().strip()
            if name:
                known_names.add(name)
                # Also add parts of the name for partial matching
                parts = name.split()
                if len(parts) >= 2:
                    known_names.add(parts[-1])  # Last name

    for pattern in FABRICATED_PERSON_PATTERNS:
        matches = re.finditer(pattern, summary_text, re.IGNORECASE)
        for match in matches:
            person_name = match.group(1).strip().lower() if match.lastindex else ""
            if person_name and not any(known in person_name or person_name in known for known in known_names):
                flags.append(f"Person mentioned in summary not found in evidence: '{match.group(1).strip()}'")
                score += 20

    # --- CHECK 5: Company name sanity ---
    if company_name and company_name.lower() not in text_lower:
        # Summary doesn't even mention the company — very suspicious
        # Check partial match (e.g., "Nokia" in "Nokia Oyj")
        name_parts = company_name.lower().split()
        if not any(part in text_lower for part in name_parts if len(part) > 2):
            flags.append(f"Summary does not mention the target company '{company_name}'")
            score += 15

    # --- CHECK 6: Confidence-based suspicion ---
    if confidence_score is not None and confidence_score < 30:
        # Very low confidence + dramatic claims = high hallucination risk
        dramatic_count = sum(1 for f in flags if "Dramatic" in f or "Political" in f or "Breaking" in f)
        if dramatic_count > 0:
            flags.append(f"Low confidence ({confidence_score}/100) combined with dramatic claims")
            score += 15

    # --- CHECK 7: Format length note (not evidence/hallucination signal) ---
    word_count = len(summary_text.split())
    if word_count > 475:
        quality_notes.append(f"Summary length outside target range ({word_count} words, expected 325-475)")

    # --- CHECK 8: Repetitive patterns (hallucination loops) ---
    sentences = [s.strip() for s in re.split(r'[.!?]', summary_text) if len(s.strip()) > 20]
    if len(sentences) >= 3:
        seen = set()
        duplicates = 0
        for s in sentences:
            # Normalize for comparison
            normalized = re.sub(r'\s+', ' ', s.lower().strip())
            if normalized in seen:
                duplicates += 1
            seen.add(normalized)
        if duplicates >= 2:
            flags.append(f"Repetitive content detected ({duplicates} duplicate sentences)")
            score += 15

    # --- CHECK 9: Evidence governance checks ---
    # These catch the failure modes seen in company summaries: narrow-source
    # extrapolation, wrong-entity evidence leakage, and foresight/regulation
    # being written as current company fact.
    governance_flags = []
    governance_flags.extend(validate_evidence_admissibility(evidence_links))
    governance_flags.extend(validate_scope_inflation(summary_text, evidence_links))
    governance_flags.extend(validate_foresight_presented_as_fact(summary_text, evidence_links))
    governance_flags.extend(validate_forward_looking_language(summary_text, section="Generated narrative"))
    if governance_flags:
        flags.extend(governance_flags)
        score += min(35, 10 + 5 * len(governance_flags))

    # --- CALCULATE RISK LEVEL ---
    score = min(score, 100)
    if score >= 50:
        risk = "high"
    elif score >= 25:
        risk = "medium"
    else:
        risk = "low"

    return {
        "risk": risk,
        "flags": flags,
        "quality_notes": quality_notes,
        "score": score,
        "summary_ok": score < 50,
    }


def validate_pitch(pitch_text, company_name):
    """
    Light validation for the pitch text.
    Same heuristic checks but with lower thresholds since pitch is shorter.
    """
    if not pitch_text or len(pitch_text.strip()) < 20:
        return {"risk": "low", "flags": [], "score": 0, "pitch_ok": True}

    flags = []
    score = 0
    text_lower = pitch_text.lower()

    # Check for political/government hallucinations in pitch too
    for pattern in POLITICAL_HALLUCINATION_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            flags.append(f"Political claim in pitch: '{match.group()}'")
            score += 30

    for pattern in BREAKING_NEWS_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            flags.append(f"Breaking news language in pitch: '{match.group()}'")
            score += 20

    score = min(score, 100)
    risk = "high" if score >= 50 else "medium" if score >= 25 else "low"

    return {
        "risk": risk,
        "flags": flags,
        "score": score,
        "pitch_ok": score < 50,
    }


# --- AI-POWERED FACT-CHECK (Layer 2) ---
# Two-stage verification: GPT verifier detects false claims, then GPT frontier
# or Claude Sonnet surgically corrects them when needed.
# This catches both evidence-sourced errors AND fabricated claims with no basis.
#
# Architecture rationale:
#   - The core problem is EVIDENCE CONTAMINATION, not hallucination.
#     Checking the summary against stored evidence is circular — if the evidence
#     is wrong, the summary will be perfectly consistent with wrong evidence.
#   - The verifier uses only generation-eligible evidence and a separate provider
#     family from the generation model whenever possible.
#   - If false claims are found, correction happens in a second call. Evidence is
#     still purged in main.py.
#   - If no false claims found: pipeline is DONE, original synthesis stands.

def _get_anthropic_key(anthropic_key=None):
    """Resolve Anthropic API key from argument or environment."""
    api_key = anthropic_key
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and "=" in api_key:
            try: api_key = api_key.split("=", 1)[1]
            except: pass
        api_key = (api_key or "").strip().strip('"').strip("'")
    return api_key


def _fact_check_source_content(link):
    return (
        link.get("fetched_excerpt") or link.get("normalized_content")
        or link.get("content_excerpt") or link.get("extracted_text")
        or link.get("cached_excerpt") or link.get("semantic_snippet")
        or link.get("source_quote") or link.get("snippet") or link.get("description") or ""
    ).strip()


def _build_fact_check_source_pack(evidence_links=None, foresight_links=None, evidence_manifest=None):
    """Build separately governed company and macro-context source packets."""
    if evidence_manifest:
        packets = []
        for record in evidence_manifest:
            if not isinstance(record, dict) or not record.get("evidence_id"):
                continue
            content = _fact_check_source_content(record)
            if not content:
                content = record.get("exact_passage") or ""
            packets.append({
                "evidence_id": record["evidence_id"],
                "report_ref": record.get("report_ref"),
                "evidence_kind": record.get("evidence_kind") or "company_evidence",
                "title": (record.get("title") or record.get("source_title") or "Unknown")[:120],
                "url": record.get("url") or record.get("canonical_url") or "",
                "semantic_hint": (record.get("semantic_hint") or record.get("semantic_snippet") or "")[:500],
                "snippet": content[:2400],
                "tier": str(record.get("source_tier") or record.get("claim_tier") or "unknown").upper(),
                "label": describe_source(record) if describe_source else "Source",
                "claim_type": record.get("claim_type") or "company_fact",
                "theme": record.get("theme") or record.get("dimension") or "",
                "company_specific": record.get("company_specific", True),
                "analysis_support_mode": record.get("analysis_support_mode"),
                "source_context_status": record.get("source_context_status"),
                "verification_state": {
                    "url_verified": record.get("url_verified", record.get("verified")),
                    "content_verified": record.get("content_verified", record.get("source_readable")),
                    "entity_match_status": record.get("entity_match_status"),
                    "claim_support_status": record.get("claim_support_status"),
                    "source_context_status": record.get("source_context_status"),
                    "analysis_support_mode": record.get("analysis_support_mode"),
                    "foresight_context_eligible": record.get("foresight_context_eligible"),
                },
            })
        return packets
    packets = []
    seen_urls = set()

    def add_source(link, evidence_kind):
        if not isinstance(link, dict):
            return
        url = (link.get("url") or link.get("source_url") or "").strip()
        if not url or url in seen_urls:
            return
        if link.get("verified") is False or link.get("url_verified") is False:
            return
        title = (link.get("title") or link.get("source_title") or "Unknown")[:120]
        content = _fact_check_source_content(link)
        if not content:
            return
        if evidence_kind == "company_evidence":
            if is_analysis_premise_source and not is_analysis_premise_source(link):
                return
            if not is_analysis_premise_source and is_generation_eligible_source and not is_generation_eligible_source(link):
                return
            tier = (link.get("claim_tier") or "D").upper()
            if tier_snippet_bounds is None:
                min_len, max_len = 0, 500
            else:
                min_len, max_len = tier_snippet_bounds(tier)
            if min_len and len(content) < min_len:
                return
            if is_title_echo and is_title_echo(content, title):
                return
            if is_boilerplate_snippet and is_boilerplate_snippet(content):
                return
        else:
            if is_foresight_context_eligible_source and not is_foresight_context_eligible_source(link):
                return
            tier = str(link.get("source_tier") or link.get("claim_tier") or "foresight").upper()
            max_len = 1800
        seen_urls.add(url)
        packets.append({
            "evidence_id": f"{'C' if evidence_kind == 'company_evidence' else 'F'}{1 + sum(1 for p in packets if p['evidence_kind'] == evidence_kind)}",
            "evidence_kind": evidence_kind,
            "title": title,
            "url": url,
            "semantic_hint": (link.get("semantic_hint") or link.get("semantic_snippet") or "").strip()[:500],
            "snippet": content[:max(1800, max_len)],
            "tier": tier,
            "label": describe_source(link) if describe_source else "Source",
            "claim_type": "foresight_context" if evidence_kind == "foresight_context" else (link.get("claim_type") or "company_evidence"),
            "theme": link.get("theme") or link.get("dimension") or "",
            "company_specific": evidence_kind == "company_evidence",
        })

    for link in evidence_links or []:
        add_source(link, "company_evidence")
        if sum(1 for p in packets if p["evidence_kind"] == "company_evidence") >= 20:
            break
    for link in foresight_links or []:
        add_source(link, "foresight_context")
        if sum(1 for p in packets if p["evidence_kind"] == "foresight_context") >= 10:
            break
    return packets


def _build_fact_check_premise_pack(scan_payload=None):
    """Return deterministic Scanner premises separately from source evidence."""
    payload = scan_payload if isinstance(scan_payload, dict) else {}
    # The generation-time manifest is authoritative for resolving ABS IDs. The
    # final evidence-state signals are additive context only because evidence
    # management may recompute them after synthesis.
    candidates = list(payload.get("generation_premise_manifest") or [])
    candidates.extend(payload.get("evidence_absence_signals") or [])
    dimension_notes = payload.get("dimension_evidence_notes") or {}
    if isinstance(dimension_notes, dict):
        candidates.extend(dimension_notes.get("absence_signals") or [])

    premises = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        signal_id = str(item.get("signal_id") or "").strip()
        if not signal_id.startswith("ABS-") or signal_id in seen:
            continue
        seen.add(signal_id)
        premises.append({
            "premise_id": signal_id,
            "premise_kind": "absence_of_promoted_evidence",
            "dimension": str(item.get("dimension") or "").strip(),
            "gap_status": str(item.get("gap_status") or "").strip(),
            "safe_wording": str(item.get("safe_wording") or "").strip()[:900],
            "do_not_say": str(item.get("do_not_say") or "").strip()[:500],
            "verification_contract": str(item.get("verification_contract") or "evidence_state_not_company_fact"),
        })
    return premises


def _canonicalize_premise_id(source_id, claim_text, reasoning, premise_by_id):
    """Resolve a generated ABS reference to a current deterministic premise."""
    source_id = str(source_id or "").strip()
    if source_id in premise_by_id:
        return source_id
    if not source_id.startswith("ABS-") or not premise_by_id:
        return ""

    haystack = f"{claim_text or ''} {reasoning or ''}".lower()
    ranked = []
    for premise_id, premise in premise_by_id.items():
        dimension = str(premise.get("dimension") or "").strip().lower()
        terms = {term for term in re.findall(r"[a-z0-9]+", dimension) if len(term) >= 3}
        safe_terms = {
            term for term in re.findall(r"[a-z0-9]+", str(premise.get("safe_wording") or "").lower())
            if len(term) >= 5
        }
        score = sum(4 for term in terms if term in haystack)
        score += min(4, sum(1 for term in safe_terms if term in haystack))
        ranked.append((score, premise_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked and ranked[0][0] >= 4 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1]
    return ""


def _fact_check_claim_id(section, claim):
    normalized = re.sub(r"\s+", " ", f"{section or ''}|{claim or ''}".strip().lower())
    return "FC-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()


_EDITORIAL_CORRECTION_RE = re.compile(
    r"^(?:remove|omit|delete|qualify|rewrite|replace|exclude|do\s+not\s+(?:use|say|include)|"
    r"mark\s+as|treat\s+as)\b",
    flags=re.IGNORECASE,
)


def _reader_facing_fact_check_correction(value) -> str:
    """Reject verifier instructions that are not publishable company prose."""
    correction = re.sub(r"\s+", " ", str(value or "")).strip()
    if not correction or _EDITORIAL_CORRECTION_RE.search(correction):
        return ""
    return correction


def _fact_check_field_paths(claim, summary_text, pitch_text, analysis_sections):
    """Locate exact reader-facing occurrences without trusting model labels."""
    needle = re.sub(r"\s+", " ", str(claim or "")).strip().lower()
    if not needle:
        return []
    roots = {
        "summary_text": summary_text,
        "pitch_text": pitch_text or "",
        **(analysis_sections or {}),
    }
    matches = []

    def visit(value, path):
        if isinstance(value, str):
            if needle in re.sub(r"\s+", " ", value).lower():
                matches.append(path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}")

    for root, value in roots.items():
        visit(value, root)
    return matches


def _provider_fact_check(
    summary_text,
    company_name,
    evidence_links=None,
    foresight_links=None,
    pitch_text=None,
    scan_payload=None,
    analysis_sections=None,
    document_label="AI Diagnosis",
    evidence_manifest=None,
    claim_graph=None,
    time_budget_seconds=None,
    verification_only=False,
    provider_chain=None,
    provider_timeout_seconds=None,
):
    """Two-stage GPT/Claude fact-check pipeline."""
    verified_links = _build_fact_check_source_pack(evidence_links, foresight_links, evidence_manifest=evidence_manifest)
    deadline = time.time() + max(15, int(time_budget_seconds or 120))

    def remaining_seconds():
        return max(0, int(deadline - time.time()))
    premise_pack = _build_fact_check_premise_pack(scan_payload)
    premise_by_id = {item["premise_id"]: item for item in premise_pack}
    company_ref = ""
    foresight_ref = ""
    for i, link in enumerate(verified_links, 1):
        row = (
            f"[{link.get('report_ref') or i}] [id={link['evidence_id']}] [tier={link['tier']}] [{link['label']}] "
            f"{link['title']} | URL: {link['url']} | Claim type: {link['claim_type']} | "
            f"Theme: {link['theme']} | Semantic hint (routing only): {link['semantic_hint']} | "
            f"Verification: {json.dumps(link.get('verification_state') or {}, ensure_ascii=False)} | "
            f"Current source content: {link['snippet']}\n"
        )
        if link["evidence_kind"] == "foresight_context":
            foresight_ref += row
        else:
            company_ref += row

    evidence_section = ""
    if company_ref:
        evidence_section += f"""
VERIFIED COMPANY EVIDENCE SOURCES:
{company_ref}
"""
    if foresight_ref:
        evidence_section += f"""
VERIFIED MACRO FORESIGHT CONTEXT SOURCES (not proof of company action or deployment):
{foresight_ref}
"""
    if premise_pack:
        evidence_section += f"""
SCANNER PREMISE MANIFEST (deterministic evidence-state observations, not source records):
{json.dumps(premise_pack, ensure_ascii=False, indent=2)}
"""
    pitch_section = f"""
AI-GENERATED PITCH TO VERIFY:
{pitch_text}
""" if pitch_text else ""
    scan_payload_section = ""
    if scan_payload:
        try:
            payload_text = json.dumps(scan_payload, ensure_ascii=False, indent=2)
        except Exception:
            payload_text = str(scan_payload)
        scan_payload_section = f"""
SCAN PAYLOAD FOR INTERNAL SCANNER OBSERVATIONS AND STRUCTURED FINANCIAL FACTS:
{payload_text[:14000]}
"""
    sections_section = ""
    if analysis_sections:
        sections_section = f"""
ADDITIONAL GENERATED SECTIONS TO VERIFY INDEPENDENTLY:
{json.dumps(analysis_sections, ensure_ascii=False, indent=2)}
"""
    claim_graph_section = ""
    if claim_graph:
        claim_graph_section = f"""
CANONICAL CLAIM GRAPH (claims cite stable evidence IDs):
{json.dumps(claim_graph, ensure_ascii=False, indent=2)[:18000]}
"""

    document_lower = str(document_label or "").lower()
    if "general summary" in document_lower:
        section_policy = (
            "This is a facts-only General Summary. Scanner evidence-state observations and strategic "
            "inferences are not permitted in its reader-facing prose."
        )
    elif "detailed forensic report" in document_lower:
        section_policy = (
            "This report may state cautious evidence limitations and clearly marked analytical implications. "
            "It must not convert a proof gap into proof that a capability is absent."
        )
    else:
        section_policy = (
            "AI Diagnosis and Evidence-Based Risks may state clearly scoped evidence-state observations. "
            "Strategic Synthesis, WHY/WHAT and Pitch may use cautious proof-gap premises from the ABS manifest. "
            "These observations must describe what the promoted evidence does not currently confirm, never "
            "assert that the company capability itself is absent."
        )

    stage1_prompt = f"""You are an uncompromising Data Auditor. Evaluate the faithfulness and factual accuracy of this {document_label} section by section.

COMPANY: {company_name}

AI-GENERATED SUMMARY TO VERIFY:
{summary_text}
{pitch_section}{sections_section}{evidence_section}{claim_graph_section}{scan_payload_section}
INSTRUCTIONS:
1. Extract definitive claims from the summary and pitch.
2. Classify each claim before judging it:
   - EXTERNAL COMPANY FACT: company events, partnerships, products, customers, financial values, ownership, people, dates, operations, present-tense capabilities.
   - INTERNAL SCANNER OBSERVATION: statements about what the scan found or did not find, evidence counts, weak/no evidence by dimension, source quality, scores, confidence, archetype, evidence packet, or Scanner process state.
   - STRUCTURED FINANCIAL PAYLOAD FACT: financial index/sub-score, revenue/profitability/stability/investment finding, or exact financial value explicitly present in the scan payload's financial_payload.
   - MACRO FORESIGHT CONTEXT: an external trend, regulation, market pressure, technology trajectory, or time-horizon claim that is not asserted as present company behavior.
   - STRATEGIC INFERENCE: a new analytical conclusion connecting one or more supported company premises to supported macro context, expressed as a pressure, exposure, implication, opportunity, risk, or question.
3. For EXTERNAL COMPANY FACTS, cross-reference strictly against stored evidence. Use only verified, generation-eligible evidence as authoritative. Do not accept display-only, unsupported, wrong-entity, or weak evidence.
4. For STRUCTURED FINANCIAL PAYLOAD FACTS, verify against financial_payload in the SCAN PAYLOAD above. These facts may be valid Scanner financial-module facts even when they are not repeated in STORED EVIDENCE SOURCES. Flag them only if the value/detail is absent from or contradicts financial_payload.
5. Apply this section policy: {section_policy}
   Scanner process telemetry and comparisons between previous scans remain prohibited. Do not classify a
   properly scoped ABS-backed proof-gap statement as prohibited merely because it describes evidence state.
6. If stored evidence is insufficient for an external company fact, use cautious external verification through your own provider tools/knowledge, but do not invent corrections.
7. A company website is self-reported. It cannot by itself refute analytical observations about internal operating reality unless it contains concrete, specific operational metrics.
8. Validate MACRO FORESIGHT CONTEXT against VERIFIED MACRO FORESIGHT CONTEXT SOURCES. The source does not need to mention the company. Never reject macro context merely because it is absent from the company scan payload.
9. A STRATEGIC INFERENCE is newly created analysis and is not expected to appear verbatim in any database or source. Do not flag it merely because "the payload does not contain the conclusion." Validate its premises instead: at least one company premise must be supported by company evidence, structured financial data, or an internal Scanner observation, and any macro premise must be supported by foresight context. It must remain modal and must not claim that the company has already acted, deployed, selected a vendor, or incurred a quantified impact unless company evidence proves that fact.
10. For a STRATEGIC INFERENCE, classify support as exact when all premises are directly supported, partial when the inference is reasonable but one non-essential premise is context-only, context_only when it is suitable only as a cautious scenario/question, and unsupported only when a required premise is absent or contradicted.
11. Semantic hints and historical snippets are discovery aids only. Judge support from CURRENT SOURCE CONTENT. Exact quotation mismatch is not by itself a failure when the current passage supports the same claim; classify support as exact, partial, context_only, or unsupported.
12. Financial evidence may support only financial condition, capacity and investment claims. It cannot substitute for missing operational, architectural, strategic, AI/data, service-delivery or cultural evidence.
13. Use "Prohibited Scanner Observation" for scanner-process or cross-scan performance statements. Use Fabrication/Contradiction/Misattribution/Organizational Fabrication for external company facts. For macro context or strategic inference, identify the missing or contradicted premise rather than saying only that the conclusion is absent from the payload.
14. The evidence manifest above is the exact source bibliography supplied to generation. ABS-* identifiers belong to the separate Scanner Premise Manifest and must never be treated as missing source evidence. If a non-ABS source ID is absent, classify it as Evidence Handoff Failure. If an ABS reference cannot be resolved, classify only the reference as Premise Reference Mismatch and continue judging the claim from its available premises.
15. Return each materially unsupported claim once. If a sentence combines supported and unsupported clauses, isolate the unsupported span and provide a correction that preserves the supported clause. Do not invalidate an entire sentence merely because one separable clause is unsupported.
16. Distinguish false facts from overstrong interpretation:
   - Use Fabrication, Contradiction, Misattribution, or Organizational Fabrication only when a company fact, entity, date, value, event, present capability, or cited source relation is genuinely unsupported or wrong.
   - Use Overstrong Interpretation when the underlying facts are supported but the prose makes an unproved causal, comparative, concentration, financing, certainty, or scope claim.
   - Overstrong Interpretation always requires a complete neutralized correction preserving the supported analytical point. It is a wording-calibration action, not grounds to reject the report or trigger full reanalysis.

OUTPUT FORMAT — valid JSON only:
If accurate: {{"false_claims": [], "summary_accurate": true}}
If issues are found: {{"false_claims": [{{"section": "section heading", "claim": "Exact quote or close paraphrase", "claim_class": "external_company_fact OR internal_scanner_observation OR structured_financial_payload_fact OR macro_foresight_context OR strategic_inference", "error_type": "Fabrication OR Contradiction OR Misattribution OR Organizational Fabrication OR Overstrong Interpretation OR Prohibited Scanner Observation OR Evidence Handoff Failure OR Premise Reference Mismatch", "support_level": "exact OR partial OR context_only OR unsupported", "missing_or_contradicted_premise": "Specific premise or empty string", "reasoning": "Brief reason", "correction": "Complete ordinary reader-facing replacement sentence or clause, or empty string to remove it. Never return an editorial command such as Remove, Omit, Qualify, Rewrite, Replace, or Do not use", "wrong_value": "", "correct_value": "", "source_indices": [1], "source_ids": ["EVD-...", "FOR-...", "ABS-..."]}}], "summary_accurate": false}}
"""

    stage1_false_claims = None
    stage1_model_name = None
    stage1_time_ms = 0
    _not_real = ["no fabrication", "no false", "appears accurate", "all accurate", "no issues", "summary is accurate", "no contradiction"]

    def _parse_false_claims(result):
        raw_claims = result.get("false_claims") or []
        parsed = []
        for fc in raw_claims[:10]:
            if isinstance(fc, str):
                if any(p in fc.lower() for p in _not_real):
                    continue
                parsed.append({"claim": fc, "error_type": "Unknown", "reasoning": "", "source_indices": [], "source_urls": []})
            elif isinstance(fc, dict):
                claim_text = fc.get("claim") or str(fc)
                if any(p in claim_text.lower() for p in _not_real):
                    continue
                claim_class = (fc.get("claim_class") or "").strip().lower()
                support_level = (fc.get("support_level") or "unsupported").strip().lower()
                error_type_lower = str(fc.get("error_type") or "").strip().lower()
                missing_premise = (fc.get("missing_or_contradicted_premise") or "").strip()
                reasoning_lower = (fc.get("reasoning") or "").lower()
                # A supported inference is analysis, not a false factual claim. Filter it
                # before Stage 2 so valid diagnosis prose is never silently deleted.
                if (
                    claim_class in {"macro_foresight_context", "strategic_inference"}
                    and support_level in {"exact", "partial", "context_only"}
                    and not missing_premise
                    and error_type_lower not in {"overstrong interpretation", "inference calibration"}
                ):
                    continue
                if (
                    claim_class in {"macro_foresight_context", "strategic_inference"}
                    and "not present in the scan payload" in reasoning_lower
                    and "contradict" not in reasoning_lower
                    and not missing_premise
                ):
                    continue
                indices = fc.get("source_indices") or []
                source_ids = [str(item or "").strip() for item in (fc.get("source_ids") or []) if str(item or "").strip()]
                original_source_ids = list(source_ids)
                source_urls = []
                by_id = {link.get("evidence_id"): link for link in verified_links}
                canonical_source_ids = []
                premise_reference_failures = []
                for source_id in source_ids:
                    if source_id.startswith("ABS-"):
                        canonical_id = _canonicalize_premise_id(
                            source_id,
                            claim_text,
                            fc.get("reasoning") or "",
                            premise_by_id,
                        )
                        if canonical_id:
                            canonical_source_ids.append(canonical_id)
                        else:
                            premise_reference_failures.append(source_id)
                    else:
                        canonical_source_ids.append(source_id)
                source_ids = list(dict.fromkeys(canonical_source_ids))
                missing_source_ids = [sid for sid in source_ids if not sid.startswith("ABS-") and sid not in by_id]
                if missing_source_ids:
                    fc["error_type"] = "Evidence Handoff Failure"
                    fc["missing_or_contradicted_premise"] = ", ".join(missing_source_ids)
                    missing_premise = fc["missing_or_contradicted_premise"]
                elif premise_reference_failures:
                    fc["error_type"] = "Premise Reference Mismatch"
                    fc["missing_or_contradicted_premise"] = ", ".join(premise_reference_failures)
                    missing_premise = fc["missing_or_contradicted_premise"]
                elif (
                    original_source_ids
                    and all(source_id.startswith("ABS-") for source_id in original_source_ids)
                    and str(fc.get("error_type") or "").strip().lower() == "evidence handoff failure"
                ):
                    # The verifier confused a resolvable Scanner premise with a
                    # missing source record. The reference is valid, so this is
                    # not a claim-level failure and must not enter correction.
                    continue
                for source_id in source_ids:
                    url = (by_id.get(source_id) or {}).get("url", "")
                    if url:
                        source_urls.append(url)
                for idx in indices:
                    if isinstance(idx, int) and 1 <= idx <= len(verified_links):
                        url = verified_links[idx - 1].get("url", "")
                        if url:
                            source_urls.append(url)
                parsed.append({
                    "claim": claim_text,
                    "section": fc.get("section") or document_label,
                    "error_type": fc.get("error_type") or "Unknown",
                    "claim_class": claim_class,
                    "support_level": support_level,
                    "missing_or_contradicted_premise": missing_premise,
                    "reasoning": fc.get("reasoning") or "",
                    "correction": _reader_facing_fact_check_correction(fc.get("correction")),
                    "wrong_value": fc.get("wrong_value") or "",
                    "correct_value": fc.get("correct_value") or "",
                    "source_indices": indices,
                    "source_ids": source_ids,
                    "source_urls": source_urls,
                    "premise_reference_failures": premise_reference_failures,
                    "claim_id": _fact_check_claim_id(fc.get("section") or document_label, claim_text),
                    "field_paths": _fact_check_field_paths(
                        claim_text, summary_text, pitch_text, analysis_sections
                    ),
                    "current_text_claim_present": (
                        re.sub(r"\s+", " ", claim_text).strip().lower().rstrip(".?!")
                        in re.sub(r"\s+", " ", summary_text).strip().lower().rstrip(".?!")
                    ),
                })
        deduped = {}
        order = []
        for item in parsed:
            normalized_claim = re.sub(r"\s+", " ", str(item.get("claim") or "")).strip().lower().rstrip(".?!")
            key = (str(item.get("section") or "").strip().lower(), normalized_claim)
            if key not in deduped:
                order.append(key)
                deduped[key] = item
            elif not str(deduped[key].get("correction") or "").strip() and str(item.get("correction") or "").strip():
                deduped[key] = item
        return [deduped[key] for key in order]

    stage1_chain = tuple(provider_chain or (GPT_VERIFIER_MODEL, CLAUDE_SONNET_MODEL))
    strict_fact_check_output = "detailed forensic report" in document_lower
    for model_index, model_name in enumerate(stage1_chain):
        if remaining_seconds() < 12:
            break
        try:
            print(f"   🔄 [Fact-Check S1] Trying {model_name}...")
            start = time.time()
            # A final decision-only recheck must receive its complete allocation;
            # ordinary checks retain correction headroom.
            reserve_for_correction = 2 if verification_only else 10
            providers_after = len(stage1_chain) - model_index - 1
            reserve_for_fallback = 15 if providers_after else 0
            provider_timeout = max(10, int(provider_timeout_seconds or 45))
            call_timeout = min(
                provider_timeout,
                max(10, remaining_seconds() - reserve_for_correction - reserve_for_fallback),
            )
            if model_name.startswith("gpt-"):
                text = _call_openai_text(
                    stage1_prompt,
                    model=model_name,
                    max_output_tokens=6000,
                    timeout=call_timeout,
                    json_schema=_FACT_CHECK_RESULT_SCHEMA if strict_fact_check_output else None,
                    json_schema_name="scanner_detailed_report_fact_check",
                )
            else:
                text = call_anthropic_text(
                    stage1_prompt,
                    model=model_name,
                    max_tokens=8000,
                    timeout=call_timeout,
                    max_retries=0,
                    reasoning_effort="low",
                    json_schema=_FACT_CHECK_RESULT_SCHEMA if strict_fact_check_output else None,
                    json_schema_name="scanner_detailed_report_fact_check_fallback",
                    log_label="Reality Verifier Fallback",
                )
            elapsed = int((time.time() - start) * 1000)
            result = _robust_parse_json(text)
            if not result or "false_claims" not in result:
                print(f"   ⚠️ [Fact-Check S1] Could not parse {model_name} response: {text[:500]}")
                continue
            if result.get("summary_accurate") is True and result.get("false_claims"):
                return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": model_name, "time_ms": elapsed}
            false_claims = _parse_false_claims(result)
            print(
                f"   {'⚠️' if false_claims else '✅'} [Fact-Check S1] {model_name}: "
                f"{len(false_claims)} review finding(s) ({elapsed}ms)"
            )
            stage1_false_claims = false_claims
            stage1_model_name = model_name
            stage1_time_ms = elapsed
            break
        except Exception as e:
            print(f"   ❌ [Fact-Check S1] {model_name} failed: {e}")

    if stage1_false_claims is None:
        return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": "all_models_failed", "time_ms": 0, "fact_check_skipped": True}
    if not stage1_false_claims:
        return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": stage1_model_name, "time_ms": stage1_time_ms}

    handoff_failures = [
        fc for fc in stage1_false_claims
        if str(fc.get("error_type") or "").strip().lower() == "evidence handoff failure"
    ]
    premise_mismatches = [
        fc for fc in stage1_false_claims
        if str(fc.get("error_type") or "").strip().lower() == "premise reference mismatch"
    ]
    classified_claims = [
        (fc, *_classify_fact_check_issue(fc, scan_payload))
        for fc in stage1_false_claims
        if fc not in handoff_failures and fc not in premise_mismatches
    ]
    correctable_claims = [fc for fc, _issue_type, is_blocking in classified_claims if is_blocking]
    audit_only_claims = [fc for fc, _issue_type, is_blocking in classified_claims if not is_blocking]
    if not correctable_claims:
        return {
            "false_claims": audit_only_claims + handoff_failures + premise_mismatches,
            "corrected_summary": "",
            "corrected_pitch": "",
            "corrected_sections": {},
            "model": stage1_model_name,
            "time_ms": stage1_time_ms,
            "evidence_handoff_failure": bool(handoff_failures),
        }

    # Stage 1 already supplies bounded, evidence-aware replacements in the
    # common case. Let the caller apply those exact sentence corrections across
    # every field path instead of spending the stage on a whole-document rewrite.
    if verification_only or all(str(fc.get("correction") or "").strip() for fc in correctable_claims):
        return {
            "false_claims": correctable_claims + audit_only_claims + handoff_failures + premise_mismatches,
            "corrected_summary": "",
            "corrected_pitch": "",
            "corrected_sections": {},
            "model": f"{stage1_model_name}+direct-sentence-actions",
            "time_ms": stage1_time_ms,
            "evidence_handoff_failure": bool(handoff_failures),
        }

    claims_text = "\n".join(
        f"- [{fc.get('error_type', 'Unknown')}] {fc.get('claim', '')} (reason: {fc.get('reasoning', '')})\n  → VERIFIED CORRECTION: {fc.get('correction', '')}"
        for fc in correctable_claims
    )
    pitch_correction_note = f"""
ORIGINAL PITCH TO ALSO CORRECT (fix only if it references a false claim):
{pitch_text}
""" if pitch_text else ""
    sections_correction_note = f"""
ORIGINAL ADDITIONAL SECTIONS TO CORRECT:
{json.dumps(analysis_sections, ensure_ascii=False, indent=2)}
""" if analysis_sections else ""
    stage2_prompt = f"""You are a precise editorial editor. Surgically correct only the false claims below while preserving every unaffected sentence.

COMPANY: {company_name}

ORIGINAL SUMMARY:
{summary_text}
{pitch_correction_note}{sections_correction_note}
IDENTIFIED FALSE CLAIMS WITH VERIFIED CORRECTIONS:
{claims_text}

Rules:
- Apply provided verified corrections exactly.
- Delete fabricated sentences when no verified correction exists.
- Do not add new claims.
- Preserve each additional section's JSON type: strings remain strings and lists remain lists.
- Return valid JSON only: {{"corrected_summary": "...", "corrected_pitch": "...", "corrected_sections": {{"section_key": "... or original JSON type"}}}}
"""

    corrected_summary = ""
    corrected_pitch = ""
    corrected_sections = {}
    elapsed2 = 0
    s2_model_name = None
    for model_name in (GPT_REASONING_MODEL, CLAUDE_SONNET_MODEL):
        if remaining_seconds() < 10:
            break
        try:
            print(f"   🔄 [Fact-Check S2] Trying {model_name} correction...")
            start = time.time()
            call_timeout = min(35, max(8, remaining_seconds()))
            if model_name.startswith("gpt-"):
                text = _call_openai_text(stage2_prompt, model=model_name, max_output_tokens=7000, timeout=call_timeout)
            else:
                text = call_anthropic_text(
                    stage2_prompt,
                    model=model_name,
                    max_tokens=7000,
                    timeout=call_timeout,
                    reasoning_effort="medium",
                    log_label="Reality Correction Fallback",
                )
            elapsed2 = int((time.time() - start) * 1000)
            result = _robust_parse_json(text)
            if result and result.get("corrected_summary"):
                corrected_summary = result.get("corrected_summary", "")
                corrected_pitch = result.get("corrected_pitch", "")
                corrected_sections = result.get("corrected_sections") or {}
                s2_model_name = model_name
                print(f"   ✏️ [Fact-Check S2] Correction produced ({len(corrected_summary.split())} words, {elapsed2}ms)")
                break
        except Exception as e:
            print(f"   ❌ [Fact-Check S2] {model_name} failed: {e}")

    return {
        "false_claims": correctable_claims + audit_only_claims + handoff_failures + premise_mismatches,
        "corrected_summary": corrected_summary,
        "corrected_pitch": corrected_pitch,
        "corrected_sections": corrected_sections,
        "model": f"{stage1_model_name}+{s2_model_name or 's2-failed'}",
        "time_ms": stage1_time_ms + elapsed2,
    }

def ai_fact_check_summary(
    summary_text,
    evidence_links,
    company_name,
    pitch_text=None,
    scan_payload=None,
    analysis_sections=None,
    document_label="AI Diagnosis",
    foresight_links=None,
    evidence_manifest=None,
    claim_graph=None,
    time_budget_seconds=None,
    verification_only=False,
    provider_chain=None,
    provider_timeout_seconds=None,
):
    """
    AI-powered fact-check — two-stage pipeline:
      Stage 1: GPT verifier → Claude Sonnet — detects false claims.
      Stage 2: GPT frontier → Claude Sonnet — surgically corrects false claims.

    The fact-checker independently verifies the synthesis claims against live
    web data. This catches both evidence-sourced errors (contaminated evidence)
    and fabricated claims that have no basis anywhere.

    If no false claims found → pipeline DONE, original synthesis stands.
    If false claims are found, correction happens in a dedicated second call.
    Evidence purging still happens in the caller (main.py).

    Returns:
        dict with:
            - verified: bool (True if no false claims found)
            - false_claims: list of false claim strings (for display/logging)
            - false_claims_detailed: list of structured dicts with source attribution
            - corrected_summary: str (corrected summary if false claims found, else "")
            - corrected_pitch: str (corrected pitch if false claims found, else "")
            - fact_check_model: str (which model was used)
            - fact_check_time_ms: int
    """
    if not summary_text:
        return {
            "verified": True,
            "verification_status": "verified",
            "false_claims": [],
            "false_claims_detailed": [],
            "audit_notes": [],
            "audit_notes_detailed": [],
            "wording_adjustments": [],
            "wording_adjustments_detailed": [],
            "evidence_handoff_failures": [],
            "premise_reference_failures": [],
            "corrected_claim_ids": [],
            "unresolved_handoff_ids": [],
            "corrected_summary": "",
            "corrected_pitch": "",
            "corrected_sections": {},
            "fact_check_model": "skipped",
            "fact_check_time_ms": 0,
        }

    total_start = time.time()

    print(f"   🔍 [Evidence & Reasoning Audit] Checking source support, premises and analytical logic...")
    result = _provider_fact_check(
        summary_text,
        company_name,
        evidence_links=evidence_links,
        foresight_links=foresight_links,
        pitch_text=pitch_text,
        scan_payload=scan_payload,
        analysis_sections=analysis_sections,
        document_label=document_label,
        evidence_manifest=evidence_manifest,
        claim_graph=claim_graph,
        time_budget_seconds=time_budget_seconds,
        verification_only=verification_only,
        provider_chain=provider_chain,
        provider_timeout_seconds=provider_timeout_seconds,
    )

    all_claims = []
    all_claims_detailed = []
    audit_notes = []
    audit_notes_detailed = []
    wording_adjustments = []
    wording_adjustments_detailed = []
    handoff_failures = []
    premise_reference_failures = []

    # Build company domain pattern for self-referential detection
    _company_name_lower = (company_name or "").strip().lower()
    # Extract likely domain fragments from company name (e.g., "Vivicta" → "vivicta")
    _company_name_words = [w for w in re.split(r'\s+', _company_name_lower) if len(w) > 2]

    if result.get("false_claims"):
        for fc in result["false_claims"]:
            claim_text = (fc.get("claim") or str(fc)) if isinstance(fc, dict) else str(fc)
            error_type = (fc.get("error_type") or "Unknown") if isinstance(fc, dict) else "Unknown"
            reasoning = (fc.get("reasoning") or "") if isinstance(fc, dict) else ""
            source_urls = (fc.get("source_urls") or []) if isinstance(fc, dict) else []
            source_indices = (fc.get("source_indices") or []) if isinstance(fc, dict) else []
            claim_class = (fc.get("claim_class") or "") if isinstance(fc, dict) else ""
            support_level = (fc.get("support_level") or "unsupported") if isinstance(fc, dict) else "unsupported"
            missing_premise = (fc.get("missing_or_contradicted_premise") or "") if isinstance(fc, dict) else ""
            claim_id = (fc.get("claim_id") or _fact_check_claim_id(fc.get("section") if isinstance(fc, dict) else document_label, claim_text)) if isinstance(fc, dict) else _fact_check_claim_id(document_label, claim_text)

            for premise_id in (fc.get("premise_reference_failures") or []) if isinstance(fc, dict) else []:
                premise_reference_failures.append({
                    "claim_id": claim_id,
                    "claim": claim_text,
                    "missing_premise_id": premise_id,
                    "reasoning": reasoning,
                })

            if error_type == "Premise Reference Mismatch":
                if not any(item.get("claim_id") == claim_id for item in premise_reference_failures):
                    premise_reference_failures.append({
                        "claim_id": claim_id,
                        "claim": claim_text,
                        "missing_premise_id": missing_premise,
                        "reasoning": reasoning,
                    })
                audit_notes.append(_format_audit_note("premise_reference_mismatch", claim_text, reasoning))
                audit_notes_detailed.append({
                    "type": "premise_reference_mismatch",
                    "claim_id": claim_id,
                    "claim": claim_text,
                    "issue_type": "premise_reference_mismatch",
                    "error_type": error_type,
                    "claim_class": claim_class,
                    "support_level": support_level,
                    "missing_or_contradicted_premise": missing_premise,
                    "reasoning": reasoning,
                    "source_urls": source_urls,
                    "source_indices": source_indices,
                    "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                    "field_paths": (fc.get("field_paths") or []) if isinstance(fc, dict) else [],
                })
                print(f"      [Fact-Check] Premise reference mismatch; preserving as an audit warning: {claim_text[:80]}...")
                continue

            # The governed financial manifest is authoritative for exact
            # values. Resolve it even when the verifier selected an incomplete
            # EVD excerpt: a generic source projection must not override a
            # matching FINFACT source/entity/period group.
            financial_support = _structured_financial_claim_support(claim_text, scan_payload)
            if financial_support.get("supported"):
                audit_notes.append(_format_audit_note("structured_payload_supported", claim_text, reasoning))
                audit_notes_detailed.append({
                    "type": "audit_note",
                    "claim_id": claim_id,
                    "issue_type": "structured_payload_supported",
                    "claim": claim_text,
                    "error_type": error_type,
                    "claim_class": claim_class or "structured_financial_payload_fact",
                    "support_level": "exact",
                    "missing_or_contradicted_premise": "",
                    "reasoning": reasoning or "Supported by a governed period-specific financial fact.",
                    "source_urls": source_urls,
                    "source_indices": source_indices,
                    "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                    "field_paths": (fc.get("field_paths") or []) if isinstance(fc, dict) else [],
                    "financial_fact_ids": financial_support.get("financial_fact_ids", []),
                    "support_group": financial_support.get("support_group", {}),
                    "packet_omission_repaired": True,
                })
                print(f"      [Fact-Check] Governed financial fact supports claim: {claim_text[:70]}...")
                continue

            if error_type == "Evidence Handoff Failure":
                handoff_failures.append({
                    "claim_id": claim_id,
                    "claim": claim_text,
                    "missing_evidence_ids": missing_premise,
                    "reasoning": reasoning,
                    "field_paths": (fc.get("field_paths") or []) if isinstance(fc, dict) else [],
                })
                audit_notes.append(_format_audit_note("evidence_handoff_failure", claim_text, reasoning))
                audit_notes_detailed.append({
                    "type": "evidence_handoff_failure",
                    "claim_id": claim_id,
                    "claim": claim_text,
                    "issue_type": "evidence_handoff_failure",
                    "error_type": error_type,
                    "claim_class": claim_class,
                    "support_level": support_level,
                    "missing_or_contradicted_premise": missing_premise,
                    "reasoning": reasoning,
                    "source_urls": source_urls,
                    "source_indices": source_indices,
                    "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                    "field_paths": (fc.get("field_paths") or []) if isinstance(fc, dict) else [],
                })
                print(f"      [Fact-Check] Evidence handoff failure; preserving analysis for re-audit: {claim_text[:80]}...")
                continue

            # Cross-scan Scanner outputs are not company trends. Keep these in
            # the correction set so they are removed from company-facing prose.
            if _SCAN_HISTORY_RE.search(claim_text):
                error_type = "Prohibited Scanner Observation"
                claim_class = "internal_scanner_observation"

            # Scanner-process observations are checked against the internal
            # scan payload, not public web sources. If the model labels one as
            # a generic fabrication with no payload mismatch, suppress it so
            # audit/process language does not become a misleading external
            # "false company claim" warning.
            if not source_indices and _is_internal_scanner_observation(claim_text):
                _error_lower = error_type.lower()
                _reason_lower = reasoning.lower()
                _is_payload_mismatch = (
                    "internal" in _error_lower
                    or "payload" in _error_lower
                    or "scan mismatch" in _error_lower
                    or "contradicts the scan payload" in _reason_lower
                    or "not present in the scan payload" in _reason_lower
                )
                if not _is_payload_mismatch:
                    print(f"      [Fact-Check] Skipping scanner-internal observation (checked against payload, not web): {claim_text[:70]}...")
                    continue

            # ── Self-referential source filter ────────────────────────────
            # Detect when the fact-checker used only the company's own website
            # to override an analytical observation. This catches cases where
            # the LLM ignores the prompt rule about self-referential sources.
            if error_type == "Contradiction" and reasoning:
                reasoning_lower = reasoning.lower()
                # Check if reasoning references the company's own website as evidence
                _is_self_referential = False
                _self_ref_indicators = [
                    f"{w}'s own website" for w in _company_name_words
                ] + [
                    f"{w}'s website" for w in _company_name_words
                ] + [
                    f"on {w}'s" for w in _company_name_words
                ] + [
                    f"{w}.com" for w in _company_name_words
                ] + [
                    f"{w}.fi" for w in _company_name_words
                ] + [
                    f"{w}.io" for w in _company_name_words
                ]
                for indicator in _self_ref_indicators:
                    if indicator in reasoning_lower:
                        _is_self_referential = True
                        break

                # Also check: is the claim an analytical "lack of evidence" observation?
                _is_analytical_observation = bool(re.search(
                    r'lack[s]?\s+(of\s+)?(?:visible|evidence|demonstrated|clear)'
                    r'|no\s+evidence\s+of'
                    r'|remains?\s+invisible'
                    r'|not\s+(?:visible|evident|demonstrated)'
                    r'|reads?\s+as\s+narrative'
                    r'|positioning\s+rather\s+than'
                    r'|intent\s+rather\s+than',
                    claim_text, re.IGNORECASE
                ))

                # Check if reasoning mentions any independent third-party source
                _has_independent_source = bool(re.search(
                    r'gartner|forrester|idc|mckinsey|reuters|bloomberg'
                    r'|independent\s+(?:report|analysis|audit|review|assessment)'
                    r'|third.party|analyst\s+report|case\s+study\s+(?:by|from|published)'
                    r'|glassdoor|employee\s+review',
                    reasoning_lower
                ))

                if _is_self_referential and _is_analytical_observation and not _has_independent_source:
                    print(f"      [Fact-Check] BLOCKED self-referential override: company's own website used to contradict analytical observation: {claim_text[:80]}...")
                    print(f"        Reasoning (blocked): {reasoning[:200]}")
                    continue

                # Broader catch: if reasoning is based purely on company's own
                # website/blog/marketing and the claim is analytical, skip it
                if _is_self_referential and not _has_independent_source:
                    # Check for analytical language patterns in the claim
                    _analytical_patterns = re.search(
                        r'(?:organizational|execution|governance|operational|strategic)\s+'
                        r'(?:design|mechanic|maturity|reality|framework|capability)'
                        r'|internally\b|internal\s+(?:structure|practice|adoption)',
                        claim_text, re.IGNORECASE
                    )
                    if _analytical_patterns:
                        print(f"      [Fact-Check] BLOCKED self-referential override: company's own marketing cannot refute internal practice claims: {claim_text[:80]}...")
                        print(f"        Reasoning (blocked): {reasoning[:200]}")
                        continue
            # ── End self-referential source filter ────────────────────────

            correction = (fc.get("correction") or "") if isinstance(fc, dict) else ""
            wrong_value = (fc.get("wrong_value") or "") if isinstance(fc, dict) else ""
            correct_value = (fc.get("correct_value") or "") if isinstance(fc, dict) else ""

            issue_type, is_blocking = _classify_fact_check_issue({
                "claim": claim_text,
                "error_type": error_type,
                "claim_class": claim_class,
                "support_level": support_level,
                "missing_or_contradicted_premise": missing_premise,
                "reasoning": reasoning,
                "wrong_value": wrong_value,
                "correct_value": correct_value,
            }, scan_payload)

            field_paths = (fc.get("field_paths") or []) if isinstance(fc, dict) else []
            pitch_scoped = any(str(path).startswith("pitch_text") for path in field_paths)
            if pitch_scoped and support_level in {"partial", "context_only", "unsupported"}:
                # Pitch is customer-facing. A scoped overstatement that is acceptable
                # as an internal synthesis audit note still needs correction or removal
                # before it can remain in the Pitch.
                issue_type = "pitch_customer_safe_overstatement"
                is_blocking = True

            if not is_blocking:
                if issue_type == "wording_calibration":
                    wording_adjustments.append(claim_text)
                    wording_adjustments_detailed.append({
                        "type": "wording_calibration",
                        "claim_id": claim_id,
                        "issue_type": issue_type,
                        "claim": claim_text,
                        "error_type": error_type,
                        "claim_class": claim_class,
                        "support_level": support_level,
                        "missing_or_contradicted_premise": missing_premise,
                        "reasoning": reasoning,
                        "correction": correction,
                        "source_urls": source_urls,
                        "source_indices": source_indices,
                        "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                        "field_paths": field_paths,
                        "current_text_claim_present": bool(
                            fc.get("current_text_claim_present", True)
                        ) if isinstance(fc, dict) else True,
                    })
                audit_notes.append(_format_audit_note(issue_type, claim_text, reasoning))
                audit_notes_detailed.append({
                    "type": "audit_note",
                    "issue_type": issue_type,
                    "claim": claim_text,
                    "error_type": error_type,
                    "claim_class": claim_class,
                    "support_level": support_level,
                    "missing_or_contradicted_premise": missing_premise,
                    "reasoning": reasoning,
                    "correction": correction,
                    "source_urls": source_urls,
                    "source_indices": source_indices,
                    "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                    "field_paths": field_paths,
                    "current_text_claim_present": bool(
                        fc.get("current_text_claim_present", True)
                    ) if isinstance(fc, dict) else True,
                })
                print(f"      [Fact-Check] Non-blocking {issue_type}: {claim_text[:80]}...")
                if reasoning:
                    print(f"        Reasoning: {reasoning}")
                continue

            all_claims.append(claim_text)
            all_claims_detailed.append({
                "type": "false_claim",
                "claim_id": claim_id,
                "issue_type": "blocking_false_claim",
                "claim": claim_text,
                "error_type": error_type,
                "claim_class": claim_class,
                "support_level": support_level,
                "missing_or_contradicted_premise": missing_premise,
                "reasoning": reasoning,
                "correction": correction,
                "wrong_value": wrong_value,
                "correct_value": correct_value,
                "source_urls": source_urls,
                "source_indices": source_indices,
                "source_ids": (fc.get("source_ids") or []) if isinstance(fc, dict) else [],
                "field_paths": (fc.get("field_paths") or []) if isinstance(fc, dict) else [],
                "current_text_claim_present": bool(
                    fc.get("current_text_claim_present", True)
                ) if isinstance(fc, dict) else True,
            })
            src_info = f" (sources: {source_urls})" if source_urls else " (no specific source identified)"
            error_info = f" [{error_type}]" if error_type != "Unknown" else ""
            print(f"      -{error_info} {claim_text}{src_info}")
            if reasoning:
                print(f"        Reasoning: {reasoning}")

    total_ms = int((time.time() - total_start) * 1000)

    corrected_summary = result.get("corrected_summary") or ""
    corrected_pitch = result.get("corrected_pitch") or ""
    corrected_sections = result.get("corrected_sections") or {}
    fact_check_skipped = result.get("fact_check_skipped", False)
    corrected_claim_ids = [
        item.get("claim_id") for item in all_claims_detailed
        if item.get("claim_id") and (corrected_summary or corrected_pitch or corrected_sections or item.get("correction"))
    ]
    if fact_check_skipped:
        verification_status = "unavailable"
    elif handoff_failures and all_claims and (corrected_summary or corrected_pitch or corrected_sections):
        verification_status = "corrected_with_handoff_warnings"
    elif handoff_failures:
        verification_status = "failed_handoff"
    elif all_claims:
        verification_status = "failed"
    elif wording_adjustments:
        verification_status = "wording_calibration_required"
    elif audit_notes:
        verification_status = "verified_with_audit_notes"
    else:
        verification_status = "verified"

    return {
        # verified=False when skipped: an unverified summary must not be treated
        # as clean. It stays unmodified but is flagged so callers can handle it.
        "verified": verification_status in {"verified", "verified_with_audit_notes"},
        "verification_status": verification_status,
        "fact_check_skipped": fact_check_skipped,
        "false_claims": all_claims[:10],
        "false_claims_detailed": all_claims_detailed[:10],
        "audit_notes": audit_notes[:20],
        "audit_notes_detailed": audit_notes_detailed[:20],
        "wording_adjustments": wording_adjustments[:20],
        "wording_adjustments_detailed": wording_adjustments_detailed[:20],
        "evidence_handoff_failures": handoff_failures[:10],
        "premise_reference_failures": premise_reference_failures[:10],
        "corrected_claim_ids": list(dict.fromkeys(corrected_claim_ids))[:20],
        "unresolved_handoff_ids": list(dict.fromkeys(
            str(item.get("missing_evidence_ids") or "") for item in handoff_failures
            if str(item.get("missing_evidence_ids") or "")
        ))[:20],
        "corrected_summary": corrected_summary,
        "corrected_pitch": corrected_pitch,
        "corrected_sections": corrected_sections,
        "fact_check_model": result["model"],
        "fact_check_time_ms": total_ms,
    }


def _fact_check_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", str(value or "").lower())
        if token not in {
            "that", "this", "with", "from", "have", "into", "their", "company",
            "report", "evidence", "section", "would", "could", "should",
        }
    }


def _project_fact_check_record(item: dict) -> dict:
    """Project a source/premise into the bounded verifier wire shape."""
    if not isinstance(item, dict):
        return {}
    projected = {}
    scalar_keys = (
        "evidence_id", "source_id", "premise_id", "signal_id", "fact_id", "report_ref",
        "evidence_kind", "title", "source_title", "url", "canonical_url",
        "source_url", "legal_entity", "entity_name", "entity_scope",
        "claim", "finding", "theme", "dimension", "category", "description",
        "summary", "assessment", "metric", "value", "safe_wording",
        "semantic_hint", "semantic_snippet", "evidence_snippet",
        "source_quote", "exact_passage", "snippet", "fetched_excerpt",
        "claim_type", "source_tier", "claim_tier", "support_level",
        "analysis_support_mode", "source_context_status", "promotion_status",
        "source_use_status", "jury_status", "entity_match_status",
        "claim_support_status", "time_horizon", "company_relevance",
        "generation_eligible", "analysis_verified", "company_specific",
        "verified", "url_verified", "content_verified", "source_readable",
        "foresight_context_eligible", "found", "sentiment", "source_type",
    )
    excerpt_keys = {
        "source_quote": 1200, "exact_passage": 1200,
        "snippet": 1200, "fetched_excerpt": 1200,
        "semantic_hint": 400, "semantic_snippet": 600,
        "claim": 900, "finding": 900, "description": 900,
        "summary": 900, "assessment": 1800, "evidence_snippet": 1200,
        "safe_wording": 900,
    }
    for key in scalar_keys:
        value = item.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            projected[key] = value[:excerpt_keys.get(key, 500)]
        elif isinstance(value, (bool, int, float)):
            projected[key] = value
        elif key == "report_ref":
            projected[key] = value
    for key in ("source_ids", "evidence_ids", "allowed_claim_types", "periods"):
        values = item.get(key)
        if isinstance(values, list):
            projected[key] = [str(value)[:100] for value in values[:12]]
    return projected


def _compact_fact_check_records(records: list, section_text: str, limit: int) -> list:
    """Select stable, relevant source/premise records for one report batch."""
    section_terms = _fact_check_terms(section_text)
    explicit_ids = set(re.findall(r"\b(?:EVD|FOR|ABS|FINFACT|FIN|SRC|CED|LEDGER)-[A-Z0-9]+\b", section_text))
    explicit_refs = {
        int(value) for value in re.findall(r"\[(\d{1,3})\]", section_text)
    }
    ranked = []
    for index, item in enumerate(records or []):
        if not isinstance(item, dict):
            continue
        searchable = " ".join(str(item.get(key) or "") for key in (
            "evidence_id", "source_id", "fact_id", "title", "source_title",
            "claim", "finding", "assessment", "metric", "value", "periods",
            "source_url", "snippet", "evidence_snippet", "source_quote", "exact_passage",
            "semantic_snippet", "theme", "dimension", "description", "summary",
            "safe_wording",
        ))
        item_terms = _fact_check_terms(searchable)
        overlap = len(section_terms & item_terms)
        item_ids = {
            str(item.get(key) or "").strip()
            for key in ("evidence_id", "source_id", "signal_id", "premise_id", "fact_id")
            if str(item.get(key) or "").strip()
        }
        report_ref = item.get("report_ref")
        explicit_id = bool(item_ids & explicit_ids) or (
            isinstance(report_ref, int) and report_ref in explicit_refs
        ) or (
            str(report_ref or "").isdigit() and int(report_ref) in explicit_refs
        )
        score = overlap + (20 if explicit_id else 0)
        ranked.append((explicit_id, score, -index, item))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    positive = [item for explicit, score, _index, item in ranked if explicit or score > 0]
    if len(positive) < min(4, limit):
        selected_ids = {id(item) for item in positive}
        positive.extend(
            item for _explicit, _score, _index, item in ranked
            if id(item) not in selected_ids
        )
    return [_project_fact_check_record(item) for item in positive[:limit]]


def _fact_check_claim_units(section_text: str) -> list[str]:
    prose = re.sub(r"(?m)^##\s+.+$", " ", str(section_text or ""))
    units = re.split(r"(?<=[.!?])\s+|\n+", prose)
    return [
        re.sub(r"\s+", " ", unit).strip()
        for unit in units
        if len(re.findall(r"\b[\w€%.-]+\b", unit)) >= 6
    ]


def _claim_first_fact_check_records(records: list, section_text: str, limit: int) -> tuple[list, dict]:
    """Reserve direct support per claim before adding broad section context."""
    selected = []
    seen = set()
    covered = 0

    def record_key(item: dict) -> str:
        return str(
            item.get("fact_id") or item.get("evidence_id") or item.get("source_id")
            or item.get("premise_id") or item.get("signal_id") or item.get("report_ref")
            or json.dumps(item, ensure_ascii=False, sort_keys=True)[:300]
        ).strip()

    claims = _fact_check_claim_units(section_text)
    for claim in claims:
        candidate = _compact_fact_check_records(records, claim, 1)
        if not candidate:
            continue
        item = candidate[0]
        # A fallback with no lexical or explicit relation does not count as
        # claim coverage merely because it was the first source in the list.
        searchable = " ".join(str(item.get(key) or "") for key in (
            "claim", "finding", "assessment", "metric", "value", "periods",
            "evidence_snippet", "source_quote", "exact_passage", "snippet",
            "description", "summary", "safe_wording", "source_url", "url",
        ))
        explicit = bool(set(re.findall(
            r"\b(?:EVD|FOR|ABS|FINFACT|FIN|SRC|CED|LEDGER)-[A-Z0-9]+\b", claim
        )) & {
            str(item.get(key) or "")
            for key in ("fact_id", "evidence_id", "source_id", "premise_id", "signal_id")
        })
        overlap = len(_fact_check_terms(claim) & _fact_check_terms(searchable))
        if not explicit and overlap < 2:
            continue
        covered += 1
        key = record_key(item)
        if key and key not in seen and len(selected) < limit:
            selected.append(item)
            seen.add(key)

    for item in _compact_fact_check_records(records, section_text, limit):
        key = record_key(item)
        if key and key not in seen and len(selected) < limit:
            selected.append(item)
            seen.add(key)
    return selected, {
        "candidate_claim_count": len(claims),
        "claims_with_reserved_support": covered,
        "claims_without_reserved_support": max(0, len(claims) - covered),
    }


def _compact_financial_fact_manifest(records: list, section_text: str, limit: int = 12) -> list:
    """Keep all records in selected source/entity/period groups together."""
    selected, _coverage = _claim_first_fact_check_records(records, section_text, limit)
    selected_group_keys = {
        _financial_group_key(item) for item in selected if isinstance(item, dict)
    }
    output = []
    seen = set()
    for item in list(selected) + [
        _project_fact_check_record(record)
        for record in (records or [])
        if isinstance(record, dict) and _financial_group_key(record) in selected_group_keys
    ]:
        key = str(item.get("fact_id") or item.get("evidence_id") or json.dumps(item, sort_keys=True))
        if key in seen or len(output) >= limit:
            continue
        seen.add(key)
        output.append(item)
    return output


def _repair_explicit_packet_omissions(
    result: dict,
    *,
    full_records: list,
    compact_records: list,
) -> list[dict]:
    """Resolve source handoff warnings caused only by compact packet selection."""
    full_by_id = {}
    for record in full_records or []:
        if not isinstance(record, dict):
            continue
        for key in ("fact_id", "evidence_id", "source_id", "premise_id", "signal_id"):
            value = str(record.get(key) or "").strip()
            if value:
                full_by_id[value] = record
    compact_ids = {
        str(record.get(key) or "").strip()
        for record in (compact_records or []) if isinstance(record, dict)
        for key in ("fact_id", "evidence_id", "source_id", "premise_id", "signal_id")
        if str(record.get(key) or "").strip()
    }
    repaired = []
    remaining = []
    for failure in result.get("evidence_handoff_failures") or []:
        raw_ids = str(failure.get("missing_evidence_ids") or "") if isinstance(failure, dict) else ""
        missing_ids = re.findall(
            r"\b(?:EVD|FOR|ABS|FINFACT|FIN|SRC|CED|LEDGER)-[A-Z0-9][A-Z0-9-]*\b",
            raw_ids,
            flags=re.IGNORECASE,
        )
        recovered_ids = [value for value in missing_ids if value in full_by_id and value not in compact_ids]
        if missing_ids and len(recovered_ids) == len(missing_ids):
            repaired.append({
                "claim_id": failure.get("claim_id", ""),
                "claim": failure.get("claim", ""),
                "recovered_source_ids": recovered_ids,
                "status": "packet_omission_repaired",
            })
        else:
            remaining.append(failure)
    if repaired:
        result["evidence_handoff_failures"] = remaining
        result.setdefault("packet_omission_repaired", []).extend(repaired)
        result.setdefault("audit_notes_detailed", []).extend({
            "type": "audit_note",
            "issue_type": "packet_omission_repaired",
            **item,
        } for item in repaired)
    return repaired


def _semantic_packet_omission_records(
    failures: list,
    *,
    full_records: list,
    compact_records: list,
    limit_per_claim: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Recover strongly matching current-run records when a verifier omitted IDs.

    This does not approve the claim. It only rebuilds a bounded packet for a
    focused no-web recheck, which remains the authority for support and scope.
    """
    compact_keys = {
        str(record.get(key) or "").strip()
        for record in (compact_records or []) if isinstance(record, dict)
        for key in ("fact_id", "evidence_id", "source_id", "premise_id", "signal_id")
        if str(record.get(key) or "").strip()
    }
    recovered_records: list[dict] = []
    recovered_failures: list[dict] = []
    seen = set()
    for failure in failures or []:
        if not isinstance(failure, dict):
            continue
        claim = re.sub(r"\s+", " ", str(failure.get("claim") or "")).strip()
        reasoning = re.sub(r"\s+", " ", str(failure.get("reasoning") or "")).strip()
        if not claim:
            continue
        candidates, _coverage = _claim_first_fact_check_records(
            full_records or [],
            f"{claim} {reasoning}",
            max(2, int(limit_per_claim or 4)),
        )
        claim_terms = _fact_check_terms(claim)
        accepted = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(
                candidate.get("fact_id") or candidate.get("evidence_id")
                or candidate.get("source_id") or candidate.get("premise_id")
                or candidate.get("signal_id") or ""
            ).strip()
            if candidate_id and candidate_id in compact_keys:
                continue
            if candidate.get("generation_eligible") is False:
                continue
            if candidate.get("content_verified") is False or candidate.get("entity_match_status") == "wrong_entity":
                continue
            searchable = " ".join(str(candidate.get(key) or "") for key in (
                "claim", "finding", "assessment", "evidence_snippet", "source_quote",
                "exact_passage", "snippet", "description", "summary", "safe_wording",
                "metric", "value", "periods",
            ))
            candidate_terms = _fact_check_terms(searchable)
            overlap = len(claim_terms & candidate_terms)
            union = len(claim_terms | candidate_terms) or 1
            # Require a strong semantic bridge. Broad topical overlap is not
            # enough to repair an evidence handoff.
            if overlap < 4 or (overlap / union) < 0.20:
                continue
            key = candidate_id or json.dumps(candidate, ensure_ascii=False, sort_keys=True)[:400]
            if key in seen:
                continue
            seen.add(key)
            accepted.append(candidate)
            recovered_records.append(candidate)
        if accepted:
            recovered_failures.append({
                **failure,
                "recovered_source_ids": [
                    str(item.get("fact_id") or item.get("evidence_id") or item.get("source_id") or "")
                    for item in accepted
                    if str(item.get("fact_id") or item.get("evidence_id") or item.get("source_id") or "")
                ],
                "recovery_method": "semantic_current_run_match",
            })
    return recovered_records, recovered_failures


def _split_fact_check_sections(report_text: str) -> list[dict]:
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", str(report_text or "")))
    if not matches:
        return [{"heading": "Detailed Report", "text": str(report_text or "").strip()}]
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report_text)
        sections.append({
            "heading": match.group(1).strip(),
            "text": report_text[match.start():end].strip(),
        })
    return sections


def _fact_check_candidate_claim_count(text: str) -> int:
    """Estimate verification work from complete sentence-like claim units."""
    prose = re.sub(r"(?m)^##\s+.+$", " ", str(text or ""))
    units = re.split(r"(?<=[.!?])\s+|\n+", prose)
    candidates = [
        unit for unit in units
        if len(re.findall(r"\b[\w€%.-]+\b", unit)) >= 6
    ]
    return max(1, len(candidates))


def _plan_detailed_fact_check_batches(sections: list[dict]) -> list[list[dict]]:
    """Keep contiguous sections together without hiding dense claim packets."""
    batches = []
    current = []
    for section in sections:
        proposed = current + [section]
        proposed_text = "\n\n".join(item["text"] for item in proposed)
        proposed_words = len(proposed_text.split())
        proposed_claims = _fact_check_candidate_claim_count(proposed_text)
        if current and (
            len(proposed) > 3
            or proposed_words > 520
            or proposed_claims > 22
        ):
            batches.append(current)
            current = [section]
        else:
            current = proposed
    if current:
        batches.append(current)
    return batches


def _detailed_fact_check_load(text: str, estimated_input_tokens: int) -> dict:
    claim_count = _fact_check_candidate_claim_count(text)
    word_count = len(str(text or "").split())
    input_tokens = max(1, int(estimated_input_tokens or 0))
    primary_seconds = round(
        24
        + min(18, claim_count * 0.9)
        + min(12, (input_tokens / 1000) * 1.2)
    )
    fallback_seconds = round(
        20
        + min(14, claim_count * 0.7)
        + min(11, input_tokens / 1000)
    )
    return {
        "candidate_claim_count": claim_count,
        "word_count": word_count,
        "estimated_input_tokens": input_tokens,
        "primary_seconds": max(35, min(55, primary_seconds)),
        "fallback_seconds": max(30, min(45, fallback_seconds)),
    }


def detailed_report_fact_check_budget(
    report_text: str,
    only_section_names: list[str] | None = None,
    *,
    minimum_seconds: int = 90,
    maximum_seconds: int = 150,
) -> dict:
    """Return a bounded budget that covers all primary batches and one fallback."""
    sections = _split_fact_check_sections(report_text)
    selected_names = {
        str(name).strip().lower()
        for name in (only_section_names or [])
        if str(name).strip()
    }
    if selected_names:
        selected_sections = [
            item for item in sections
            if item["heading"].lower() in selected_names
        ]
        if selected_sections:
            sections = selected_sections
    batches = _plan_detailed_fact_check_batches(sections)
    loads = []
    for batch in batches:
        batch_text = "\n\n".join(item["text"] for item in batch)
        # Evidence records are selected later. Reserve a conservative compact
        # packet allowance so the budget does not depend on prose length alone.
        estimated_tokens = max(7000, (len(batch_text) // 4) + 7000)
        loads.append(_detailed_fact_check_load(batch_text, estimated_tokens))
    recommended = sum(item["primary_seconds"] for item in loads)
    if loads:
        recommended += max(item["fallback_seconds"] for item in loads)
    minimum = max(30, int(minimum_seconds or 30))
    maximum = max(minimum, int(maximum_seconds or minimum))
    return {
        "recommended_seconds": max(minimum, min(maximum, recommended)),
        "batch_count": len(batches),
        "candidate_claim_count": sum(item["candidate_claim_count"] for item in loads),
        "word_count": sum(item["word_count"] for item in loads),
    }


def _compact_fact_check_payload(scan_payload: dict | None, section_text: str) -> dict:
    payload = dict(scan_payload or {})

    def bounded(value, depth: int = 0):
        if depth >= 3:
            return str(value)[:500]
        if isinstance(value, str):
            return value[:1200]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, list):
            return [bounded(item, depth + 1) for item in value[:12]]
        if isinstance(value, dict):
            return {
                str(key)[:100]: bounded(item, depth + 1)
                for key, item in list(value.items())[:30]
            }
        return str(value)[:500]

    compact = {
        key: bounded(payload.get(key))
        for key in (
            "companyName", "intent_score", "reality_score", "reality_gap", "confidence_score",
            "archetype", "evidence_counts", "dimension_evidence_counts",
            "evidence_absence_contract", "evidence_graph_version", "temporal_contract",
            "prohibited_reasoning",
        )
        if key in payload
    }
    compact["generation_premise_manifest"] = _claim_first_fact_check_records(
        payload.get("generation_premise_manifest") or [], section_text, 8
    )[0]
    compact["evidence_absence_signals"] = _claim_first_fact_check_records(
        payload.get("evidence_absence_signals") or [], section_text, 8
    )[0]
    compact["evidence_based_risks"] = _claim_first_fact_check_records(
        payload.get("evidence_based_risks") or [], section_text, 6
    )[0]
    compact["sample_reality_findings"] = _claim_first_fact_check_records(
        payload.get("sample_reality_findings") or [], section_text, 12
    )[0]
    financial = dict(payload.get("financial_payload") or {})
    compact["financial_payload"] = {
        "financial_index": financial.get("financial_index"),
        "financial_confidence": financial.get("financial_confidence"),
        "financial_sub_scores": {
            str(key): value for key, value in (financial.get("financial_sub_scores") or {}).items()
            if isinstance(value, (str, int, float, bool))
        },
        "financial_findings": _claim_first_fact_check_records(
            financial.get("financial_findings") or [], section_text, 8
        )[0],
        "financial_fact_manifest": _compact_financial_fact_manifest(
            financial.get("financial_fact_manifest") or [], section_text, 12
        ),
    }
    raw_foresight = dict(payload.get("foresight_context") or {})
    foresight = {
        "source_counts": raw_foresight.get("source_counts") or {},
        "verification_rule": str(raw_foresight.get("verification_rule") or "")[:500],
        "forces": _claim_first_fact_check_records(
            raw_foresight.get("forces") or [], section_text, 6
        )[0],
    }
    compact["foresight_context"] = foresight
    return compact


def ai_fact_check_detailed_report(
    report_text: str,
    evidence_links: list,
    company_name: str,
    *,
    scan_payload: dict | None = None,
    foresight_links: list | None = None,
    evidence_manifest: list | None = None,
    claim_graph: list | None = None,
    time_budget_seconds: int = 85,
    only_section_names: list[str] | None = None,
) -> dict:
    """Verify a Detailed Report in compact, independently recoverable batches."""
    started = time.time()
    deadline = started + max(30, int(time_budget_seconds or 85))
    sections = _split_fact_check_sections(report_text)
    selected_names = {str(name).strip().lower() for name in (only_section_names or []) if str(name).strip()}
    if selected_names:
        sections = [item for item in sections if item["heading"].lower() in selected_names]
    if not sections:
        sections = _split_fact_check_sections(report_text)

    batches = _plan_detailed_fact_check_batches(sections)
    planned_loads = []
    for batch in batches:
        batch_text = "\n\n".join(item["text"] for item in batch)
        planned_loads.append(_detailed_fact_check_load(
            batch_text,
            max(7000, (len(batch_text) // 4) + 7000),
        ))
    aggregate = {
        "false_claims": [],
        "false_claims_detailed": [],
        "wording_adjustments": [],
        "wording_adjustments_detailed": [],
        "audit_notes": [],
        "audit_notes_detailed": [],
        "evidence_handoff_failures": [],
        "premise_reference_failures": [],
        "packet_omission_repaired": [],
    }
    batch_results = []
    unavailable_sections = []
    models = []

    for batch_index, batch in enumerate(batches):
        remaining = max(0, int(deadline - time.time()))
        future_primary_floor = sum(
            item["primary_seconds"] for item in planned_loads[batch_index + 1:]
        )
        if remaining < 25:
            unavailable_sections.extend(item["heading"] for item in batch)
            batch_results.append({
                "sections": [item["heading"] for item in batch],
                "status": "unavailable_budget",
                "model": "not_attempted",
            })
            continue
        batch_text = "\n\n".join(item["text"] for item in batch)
        compact_evidence, evidence_coverage = _claim_first_fact_check_records(
            evidence_links or [], batch_text, 18
        )
        compact_foresight, foresight_coverage = _claim_first_fact_check_records(
            foresight_links or [], batch_text, 8
        )
        selected_source_ids = {
            str(item.get("evidence_id") or item.get("source_id") or "").strip()
            for item in compact_evidence + compact_foresight
            if isinstance(item, dict)
        }
        explicit_refs = {
            int(value) for value in re.findall(r"\[(\d{1,3})\]", batch_text)
        }
        compact_manifest = [
            _project_fact_check_record(item) for item in (evidence_manifest or [])
            if isinstance(item, dict)
            and (
                str(item.get("evidence_id") or item.get("source_id") or "").strip() in selected_source_ids
                or (
                    str(item.get("report_ref") or "").isdigit()
                    and int(item.get("report_ref")) in explicit_refs
                )
            )
        ][:20]
        manifest_seen = {
            str(item.get("evidence_id") or item.get("source_id") or item.get("report_ref") or "").strip()
            for item in compact_manifest
        }
        claim_manifest, manifest_coverage = _claim_first_fact_check_records(
            evidence_manifest or [], batch_text, 16
        )
        for item in claim_manifest:
            item_key = str(
                item.get("evidence_id") or item.get("source_id") or item.get("report_ref") or ""
            ).strip()
            if item_key and item_key not in manifest_seen:
                compact_manifest.append(item)
                manifest_seen.add(item_key)
            if len(compact_manifest) >= 20:
                break
        compact_claim_graph, claim_graph_coverage = _claim_first_fact_check_records(
            claim_graph or [], batch_text, 16
        )
        compact_payload = _compact_fact_check_payload(scan_payload, batch_text)
        packet_records = compact_evidence + compact_foresight + compact_manifest + compact_claim_graph
        estimated_chars = len(batch_text) + sum(
            len(json.dumps(item, ensure_ascii=False)) for item in packet_records
        ) + len(json.dumps(compact_payload, ensure_ascii=False))
        # Include the stable verifier instructions and JSON schema, which are
        # not represented in the compact evidence character count above.
        estimated_input_tokens = max(1, ((estimated_chars + 3) // 4) + 3000)
        if estimated_input_tokens > 24000:
            # Keep explicit/citation-selected records, but trim broad context
            # before allowing packet size to distort scheduling or timeouts.
            compact_evidence = compact_evidence[:14]
            compact_foresight = compact_foresight[:5]
            compact_manifest = compact_manifest[:14]
            compact_claim_graph = compact_claim_graph[:10]
            packet_records = compact_evidence + compact_foresight + compact_manifest + compact_claim_graph
            estimated_chars = len(batch_text) + sum(
                len(json.dumps(item, ensure_ascii=False)) for item in packet_records
            ) + len(json.dumps(compact_payload, ensure_ascii=False))
            estimated_input_tokens = max(1, ((estimated_chars + 3) // 4) + 3000)
        load = _detailed_fact_check_load(batch_text, estimated_input_tokens)
        primary_capacity = max(0, remaining - future_primary_floor)
        primary_allocation = min(load["primary_seconds"], primary_capacity)
        headings = [item["heading"] for item in batch]
        if primary_allocation < 25:
            unavailable_sections.extend(headings)
            batch_results.append({
                "sections": headings,
                "status": "unavailable_budget",
                "model": "not_attempted",
                **load,
                "evidence_record_count": len(compact_evidence) + len(compact_foresight),
                "allocated_seconds": 0,
                "attempts": [],
            })
            continue
        print(
            "   🔎 [Detailed Report Fact-Check] "
            f"batch={batch_index + 1}/{len(batches)} sections={headings} "
            f"claims={load['candidate_claim_count']} words={load['word_count']} "
            f"tokens≈{estimated_input_tokens} evidence={len(compact_evidence) + len(compact_foresight)} "
            f"primary={primary_allocation}s fallback_target={load['fallback_seconds']}s"
        )
        attempts = []

        def run_provider(provider: str, allocation: int) -> dict:
            attempt_started = time.time()
            provider_result = ai_fact_check_summary(
                summary_text=batch_text,
                evidence_links=compact_evidence,
                company_name=company_name,
                scan_payload=compact_payload,
                document_label=f"Detailed Forensic Report sections: {', '.join(headings)}",
                foresight_links=compact_foresight,
                evidence_manifest=compact_manifest,
                claim_graph=compact_claim_graph,
                time_budget_seconds=allocation,
                verification_only=True,
                provider_chain=(provider,),
                provider_timeout_seconds=max(10, allocation - 2),
            )
            attempts.append({
                "provider": provider,
                "allocated_seconds": allocation,
                "elapsed_ms": int((time.time() - attempt_started) * 1000),
                "status": provider_result.get("verification_status", "unavailable"),
                "parse_or_provider_failed": bool(provider_result.get("fact_check_skipped")),
            })
            return provider_result

        result = run_provider(GPT_VERIFIER_MODEL, primary_allocation)
        if result.get("fact_check_skipped"):
            remaining_after_primary = max(0, int(deadline - time.time()))
            fallback_capacity = max(0, remaining_after_primary - future_primary_floor)
            fallback_allocation = min(load["fallback_seconds"], fallback_capacity)
            if fallback_allocation >= 25:
                print(
                    "   ↪️ [Detailed Report Fact-Check] "
                    f"retrying failed batch with {CLAUDE_SONNET_MODEL} "
                    f"({fallback_allocation}s independent allocation)"
                )
                result = run_provider(CLAUDE_SONNET_MODEL, fallback_allocation)
            else:
                print(
                    "   ⚠️ [Detailed Report Fact-Check] "
                    f"failed batch has no viable Sonnet slot; available={fallback_capacity}s"
                )
        full_financial_records = list(
            ((scan_payload or {}).get("financial_payload") or {}).get("financial_fact_manifest") or []
        )
        compact_financial_records = list(
            (compact_payload.get("financial_payload") or {}).get("financial_fact_manifest") or []
        )
        full_packet_records = (
            list(evidence_links or [])
            + list(foresight_links or [])
            + list(evidence_manifest or [])
            + list(claim_graph or [])
            + full_financial_records
        )
        compact_packet_records = (
            compact_evidence + compact_foresight + compact_manifest
            + compact_claim_graph + compact_financial_records
        )
        packet_repairs = _repair_explicit_packet_omissions(
            result,
            full_records=full_packet_records,
            compact_records=compact_packet_records,
        )
        semantic_records, semantic_failures = _semantic_packet_omission_records(
            result.get("evidence_handoff_failures") or [],
            full_records=full_packet_records,
            compact_records=compact_packet_records,
        )
        semantic_recheck = None
        semantic_capacity = max(
            0,
            int(deadline - time.time()) - future_primary_floor,
        )
        if semantic_records and semantic_failures and semantic_capacity >= 18:
            semantic_claim_text = "\n".join(
                f"- {item.get('claim')}" for item in semantic_failures if item.get("claim")
            )
            semantic_allocation = min(30, semantic_capacity)
            print(
                "   🔁 [Detailed Report Fact-Check] rebuilding semantic handoff packet "
                f"for {len(semantic_failures)} claim(s) ({semantic_allocation}s)"
            )
            semantic_recheck = ai_fact_check_summary(
                summary_text=semantic_claim_text,
                evidence_links=semantic_records,
                company_name=company_name,
                scan_payload=compact_payload,
                document_label=f"Detailed Report semantic handoff recheck: {', '.join(headings)}",
                foresight_links=[],
                evidence_manifest=semantic_records,
                claim_graph=[],
                time_budget_seconds=semantic_allocation,
                verification_only=True,
                provider_chain=(GPT_VERIFIER_MODEL,),
                provider_timeout_seconds=max(12, semantic_allocation - 2),
            )
            if not semantic_recheck.get("fact_check_skipped"):
                repaired_claim_ids = {
                    str(item.get("claim_id") or "") for item in semantic_failures
                }
                result["evidence_handoff_failures"] = [
                    item for item in (result.get("evidence_handoff_failures") or [])
                    if str(item.get("claim_id") or "") not in repaired_claim_ids
                ]
                for key in (
                    "false_claims", "false_claims_detailed", "wording_adjustments",
                    "wording_adjustments_detailed", "audit_notes", "audit_notes_detailed",
                    "premise_reference_failures", "evidence_handoff_failures",
                ):
                    result.setdefault(key, []).extend(semantic_recheck.get(key) or [])
                for item in semantic_failures:
                    repair = {
                        "claim_id": item.get("claim_id", ""),
                        "claim": item.get("claim", ""),
                        "recovered_source_ids": item.get("recovered_source_ids", []),
                        "status": "packet_omission_repaired",
                        "recovery_method": "semantic_recheck",
                    }
                    packet_repairs.append(repair)
                    result.setdefault("packet_omission_repaired", []).append(repair)
                    result.setdefault("audit_notes_detailed", []).append({
                        "type": "audit_note",
                        "issue_type": "packet_omission_repaired",
                        **repair,
                    })
        model = str(result.get("fact_check_model") or "")
        if model and model != "all_models_failed":
            models.append(model)
        unavailable = bool(result.get("fact_check_skipped"))
        if unavailable:
            unavailable_sections.extend(headings)
        batch_results.append({
            "sections": headings,
            "status": (
                "packet_rebuilt"
                if packet_repairs and result.get("verification_status") in {
                    "verified", "verified_with_audit_notes"
                }
                else result.get("verification_status", "unavailable")
            ),
            "model": model or "all_models_failed",
            "blocking_claim_count": len(result.get("false_claims") or []),
            "wording_adjustment_count": len(result.get("wording_adjustments") or []),
            **load,
            "evidence_record_count": len(compact_evidence) + len(compact_foresight),
            "packet_coverage": {
                "evidence": evidence_coverage,
                "foresight": foresight_coverage,
                "manifest": manifest_coverage,
                "claim_graph": claim_graph_coverage,
            },
            "allocated_seconds": sum(item["allocated_seconds"] for item in attempts),
            "elapsed_ms": sum(item["elapsed_ms"] for item in attempts),
            "attempts": attempts,
            "packet_omission_repaired": packet_repairs,
            "semantic_recheck_status": (
                semantic_recheck.get("verification_status")
                if isinstance(semantic_recheck, dict) else "not_needed"
            ),
        })
        for key in aggregate:
            aggregate[key].extend(result.get(key) or [])

    def _dedupe(items: list, key_name: str = "claim") -> list:
        output = []
        seen = set()
        for item in items:
            value = item.get(key_name) if isinstance(item, dict) else item
            key = re.sub(r"\s+", " ", str(value or "")).strip().lower().rstrip(".?!")
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output

    for key in aggregate:
        aggregate[key] = _dedupe(aggregate[key])
    unavailable_sections = list(dict.fromkeys(unavailable_sections))
    blocking_count = len(aggregate["false_claims"])
    calibration_count = len(aggregate["wording_adjustments"])
    handoff_count = len(aggregate["evidence_handoff_failures"])
    if unavailable_sections:
        status = "partially_unavailable" if len(unavailable_sections) < len(sections) else "unavailable"
    elif blocking_count:
        status = "changes_required"
    elif handoff_count:
        status = "handoff_recheck_required"
    elif calibration_count:
        status = "wording_calibration_required"
    elif aggregate["audit_notes"]:
        status = "verified_with_audit_notes"
    else:
        status = "verified"
    aggregate.update({
        "verified": status in {"verified", "verified_with_audit_notes"},
        "verification_status": status,
        "fact_check_skipped": status == "unavailable",
        "verification_partial": status == "partially_unavailable",
        "unavailable_sections": unavailable_sections,
        "batch_results": batch_results,
        "fact_check_model": "+".join(dict.fromkeys(models)) or "all_models_failed",
        "fact_check_time_ms": int((time.time() - started) * 1000),
        "financial_fact_ids": list(dict.fromkeys(
            fact_id
            for item in aggregate.get("audit_notes_detailed", [])
            if isinstance(item, dict)
            for fact_id in (item.get("financial_fact_ids") or [])
            if str(fact_id or "")
        )),
        "support_groups": [
            item.get("support_group")
            for item in aggregate.get("audit_notes_detailed", [])
            if isinstance(item, dict) and item.get("support_group")
        ],
        "packet_coverage": [
            {
                "sections": list(item.get("sections") or []),
                **dict(item.get("packet_coverage") or {}),
            }
            for item in batch_results
            if isinstance(item, dict) and item.get("packet_coverage")
        ],
    })
    return aggregate


# --- SUMMARY CLEANING (Layer 3) — Admin Recheck Only ---
# In the scan pipeline, surgical cleaning is replaced by full Opus re-synthesis (main.py).
# This clean_summary function is retained ONLY for the admin recheck endpoint,
# which operates standalone outside the scan pipeline.

def _build_clean_prompt(summary_text, pitch_text, unsupported_claims, company_name):
    """Build the cleaning prompt for admin recheck."""
    claims_text = "\n".join(f"- {claim}" for claim in unsupported_claims)
    summary_word_count = len(summary_text.split())
    pitch_word_count = len((pitch_text or "").split()) if pitch_text else 0

    return f"""You are a precision editor. Your ONLY job is to surgically remove specific false claims from an AI-generated company summary. You must preserve the original text as much as possible.

COMPANY: {company_name}

ORIGINAL SUMMARY ({summary_word_count} words):
{summary_text}

ORIGINAL PITCH ({pitch_word_count} words):
{pitch_text or "N/A"}

FALSE CLAIMS TO REMOVE:
{claims_text}

IMPORTANT: The false claims above are DESCRIPTIONS of what is wrong, not exact quotes from the summary. Each one explains what the summary got wrong (e.g. "[Contradiction] Summary says revenue €2.1B but evidence says €1.5B"). You must find the corresponding sentence(s) in the summary that contain the incorrect information described and remove them. Match by MEANING, not by exact wording.

RULES — READ CAREFULLY:
1. PRESERVE the original text word-for-word except for sentences that contain the false claims described above.
2. Remove ONLY the sentences or phrases that contain the false claims. Do NOT rephrase, reword, or restructure sentences that are not affected by false claims.
3. Do NOT add any new information, claims, or analysis. You are only REMOVING, never ADDING or REWRITING.
4. Do NOT change, re-evaluate, or remove the archetype classification yourself. Any necessary archetype correction will be handled separately after cleaning.
5. After removing false claim sentences, fix only the immediately adjacent transitions so the text reads naturally. Do NOT rewrite surrounding paragraphs.
6. If a false claim is embedded within an otherwise true sentence, remove only the false part and keep the true part intact.
7. If the pitch references any of the false claims, remove those parts too.
8. Preserve the company name, KB terminology, and all other factual content exactly as written.
9. You MUST set claims_removed to the number of false claims from the list above that you found and removed from the summary. If you genuinely cannot locate ANY of the described issues in the summary text after careful semantic matching, set claims_removed to 0 — but this should be rare since these claims were identified by a prior verification step.
10. LENGTH PRESERVATION: The cleaned summary MUST remain close to its original length ({summary_word_count} words). You are removing individual false claim sentences, NOT rewriting or condensing the summary. Your output should be at least 65% of the original word count. Every sentence that is NOT related to a false claim must appear in your output UNCHANGED. Do NOT summarize, compress, or paraphrase the remaining text.

CRITICAL: Your output must be as close to the original as possible. A diff between the original and your output should show ONLY the removed false claims and minor transition fixes — nothing else. If the original is {summary_word_count} words and you remove 2-3 false claim sentences, your output should still be roughly {summary_word_count - 30}-{summary_word_count} words.

Output ONLY valid JSON:
{{"cleaned_summary": "...", "cleaned_pitch": "...", "claims_removed": <number of false claims you actually found and removed>}}"""


def _clean_with_models(prompt):
    """Rewrite summary using GPT frontier model, with Claude Sonnet fallback."""
    try:
        start = time.time()
        print(f"   🔄 [Clean] Attempting {GPT_REASONING_MODEL} summary rewrite...")
        response_text = _call_openai_text(prompt, model=GPT_REASONING_MODEL, max_output_tokens=6000, timeout=90)
        elapsed_ms = int((time.time() - start) * 1000)
        if response_text:
            return response_text, elapsed_ms, GPT_REASONING_MODEL
        raise ValueError(f"{GPT_REASONING_MODEL} returned empty response")
    except Exception as gpt_err:
        print(f"   ❌ [Clean] {GPT_REASONING_MODEL} failed: {gpt_err}")

    anthropic_key = _get_anthropic_key()
    if anthropic_key:
        try:
            import anthropic
            print(f"   🔄 [Clean] Falling back to {CLAUDE_SONNET_MODEL}...")
            m_start = time.time()
            sonnet_client = anthropic.Anthropic(api_key=anthropic_key)
            sonnet_resp = sonnet_client.messages.create(
                model=CLAUDE_SONNET_MODEL,
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
            m_text = "".join(
                block.text for block in sonnet_resp.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", "")
            ).strip()
            m_elapsed = int((time.time() - m_start) * 1000)
            if m_text:
                return m_text, m_elapsed, CLAUDE_SONNET_MODEL
            print(f"   ⚠️ [Clean] Sonnet returned empty response")
        except Exception as sonnet_err:
            print(f"   ❌ [Clean] {CLAUDE_SONNET_MODEL} fallback failed: {sonnet_err}")

    raise ValueError(f"All clean-summary models failed ({GPT_REASONING_MODEL}, {CLAUDE_SONNET_MODEL})")


def _validate_clean_result(result, summary_text, pitch_text, model_name, elapsed_ms):
    """Validate and return the cleaned result, with safety checks."""
    cleaned_summary = result.get("cleaned_summary", summary_text)
    cleaned_pitch = result.get("cleaned_pitch", pitch_text)
    claims_removed = result.get("claims_removed", 0)

    # Sanity check: cleaned version shouldn't be longer than original
    if len(cleaned_summary.split()) > len(summary_text.split()) + 10:
        print(f"   ⚠️ [Clean] Cleaned summary is longer than original — LLM may have added content. Rejecting.")
        return {
            "cleaned_summary": summary_text,
            "cleaned_pitch": pitch_text,
            "claims_removed": 0,
            "model": "rejected_longer_output",
            "time_ms": elapsed_ms,
            "success": False,
        }

    # Sanity check: cleaned version shouldn't be drastically shorter than original.
    # Removing a few false claim sentences should not lose more than ~30% of the text.
    # If the model compressed the summary instead of surgically removing, reject it.
    original_word_count = len(summary_text.split())
    cleaned_word_count = len(cleaned_summary.split())
    if original_word_count > 0 and cleaned_word_count < original_word_count * 0.65:
        pct = int(cleaned_word_count / original_word_count * 100)
        print(f"   ⚠️ [Clean] Cleaned summary is only {pct}% of original length ({cleaned_word_count} vs {original_word_count} words) — LLM compressed instead of surgically removing. Rejecting.")
        return {
            "cleaned_summary": summary_text,
            "cleaned_pitch": pitch_text,
            "claims_removed": 0,
            "model": f"rejected_too_short_{pct}pct",
            "time_ms": elapsed_ms,
            "success": False,
        }

    print(f"   ✅ [Clean] Removed {claims_removed} false claim(s) from summary via {model_name} ({elapsed_ms}ms)")
    return {
        "cleaned_summary": cleaned_summary,
        "cleaned_pitch": cleaned_pitch,
        "claims_removed": claims_removed,
        "model": model_name,
        "time_ms": elapsed_ms,
        "success": True,
    }


def clean_summary(summary_text, pitch_text, unsupported_claims, company_name,
                   anthropic_key=None, legacy_model_flag=False, use_opus=False):
    """
    Rewrites a summary and pitch to remove specific false claims.
    Used by admin recheck endpoint (standalone, outside scan pipeline).

    In the scan pipeline, this is replaced by full Opus re-synthesis (main.py).

    Args:
        summary_text: The current summary containing false claims
        pitch_text: The current pitch (may reference false claims too)
        unsupported_claims: List of false claim description strings
        company_name: The target company name
        anthropic_key: Unused (retained for backward compat)
        legacy_model_flag: Deprecated compatibility flag; ignored.
        use_opus: Deprecated compatibility flag; ignored.

    Returns:
        dict with cleaned_summary, cleaned_pitch, claims_removed, model, time_ms, success
    """
    if not summary_text or not unsupported_claims:
        return {
            "cleaned_summary": summary_text,
            "cleaned_pitch": pitch_text,
            "claims_removed": 0,
            "model": "skipped_nothing_to_clean",
            "time_ms": 0,
            "success": False,
        }

    prompt = _build_clean_prompt(summary_text, pitch_text, unsupported_claims, company_name)

    try:
        print(f"   🔄 [Clean] Using GPT/Claude for summary rewrite...")
        response_text, elapsed_ms, model_name = _clean_with_models(prompt)

        # Parse JSON from response (robust parsing with auto-repair)
        result = _robust_parse_json(response_text)
        if result and "cleaned_summary" in result:
            return _validate_clean_result(result, summary_text, pitch_text, model_name, elapsed_ms)
        else:
            # Fallback: if model says "no contradiction" / nothing to fix, treat as 0 claims removed
            lower_resp = (response_text or "").lower()
            no_fix_phrases = ["no contradiction", "no false claim", "nothing to remove",
                              "no issues found", "all align", "cannot locate any"]
            if any(phrase in lower_resp for phrase in no_fix_phrases):
                print(f"   ℹ️ [Clean] {model_name} found no claims to remove — returning original text")
                return {
                    "cleaned_summary": summary_text,
                    "cleaned_pitch": pitch_text,
                    "claims_removed": 0,
                    "model": f"{model_name}_no_claims_found",
                    "time_ms": elapsed_ms,
                    "success": True,
                }
            print(f"   ⚠️ [Clean] Could not parse {model_name} response: {response_text[:200]}")
            return {
                "cleaned_summary": summary_text,
                "cleaned_pitch": pitch_text,
                "claims_removed": 0,
                "model": f"{model_name}_parse_error",
                "time_ms": elapsed_ms,
                "success": False,
            }

    except Exception as e:
        print(f"   ❌ [Clean] Summary cleaning failed: {e}")
        return {
            "cleaned_summary": summary_text,
            "cleaned_pitch": pitch_text,
            "claims_removed": 0,
            "model": f"error: {str(e)[:100]}",
            "time_ms": 0,
            "success": False,
        }


# --- EVIDENCE PURGING (Layer 4) ---
# When false claims are found, remove the contaminated evidence from the database
# so it doesn't poison future scans (Scanner 2 uses previous scan data as context).

def purge_false_evidence(data, unsupported_claims, detailed_claims=None):
    """
    Removes evidence_links and reality_checklist items that the AI fact-check
    step specifically traced back to false claims via source URLs.

    KEY SAFETY PRINCIPLE: If the AI could not identify the specific source(s)
    behind a false claim, NOTHING gets purged for that claim. We only remove
    evidence that is explicitly linked to a false claim by the fact-check AI.
    This prevents over-aggressive purging from keyword guesswork.

    Args:
        data: The full company data dict (modified in-place)
        unsupported_claims: List of false claim strings (for logging)
        detailed_claims: List of structured dicts from ai_fact_check_summary's false_claims_detailed,
                         each with: {"type", "claim", "source_urls", "source_indices"}

    Returns:
        dict with:
            - evidence_removed: int (number of evidence links removed)
            - checklist_removed: int (number of checklist items removed)
            - purged_items: list of removed items (for audit trail)
            - skipped_claims: int (claims where no source was identified — nothing purged)
    """
    if not unsupported_claims and not detailed_claims:
        return {"evidence_removed": 0, "checklist_removed": 0, "purged_items": [], "skipped_claims": 0}

    # Collect only URLs whose source itself genuinely failed. A valid source is
    # never purged merely because an inference built from it overreached.
    urls_to_purge = set()
    skipped_claims = 0
    claims_with_sources = 0

    if detailed_claims:
        for dc in detailed_claims:
            source_urls = dc.get("source_urls", [])
            claim_text = dc.get("claim", "")
            error_type = str(dc.get("error_type") or "").strip().lower()
            claim_class = str(dc.get("claim_class") or "").strip().lower()
            reasoning = str(dc.get("reasoning") or "").lower()
            source_failure = (
                error_type in {"misattribution", "organizational fabrication"}
                or any(marker in reasoning for marker in (
                    "dead url", "source is dead", "soft-404", "soft 404",
                    "wrong entity", "materially misrepresent", "unreadable source",
                ))
            )
            protected_reasoning = (
                error_type in {"evidence handoff failure", "prohibited scanner observation"}
                or claim_class in {
                    "strategic_inference", "macro_foresight_context",
                    "structured_financial_payload_fact", "internal_scanner_observation",
                }
            )
            if protected_reasoning or not source_failure:
                skipped_claims += 1
                print(
                    f"   ⏭️ [Purge] Keeping source; claim correction is not a source failure: "
                    f"'{claim_text[:80]}'"
                )
                continue
            if source_urls:
                claims_with_sources += 1
                for url in source_urls:
                    urls_to_purge.add(url)
                print(f"   🔗 [Purge] Claim traced to {len(source_urls)} source(s): '{claim_text[:80]}'")
                for url in source_urls:
                    print(f"      → {url}")
            else:
                skipped_claims += 1
                print(f"   ⏭️ [Purge] No source identified for claim — skipping purge: '{claim_text[:80]}'")

    if not urls_to_purge:
        print(f"   ℹ️ [Purge] No specific source URLs identified for any false claim — nothing purged (safe mode)")
        return {
            "evidence_removed": 0,
            "checklist_removed": 0,
            "purged_items": [],
            "skipped_claims": skipped_claims,
        }

    print(f"   🎯 [Purge] {len(urls_to_purge)} unique source URL(s) to purge from {claims_with_sources} claim(s)")

    purged_items = []

    # --- Purge evidence_links by exact URL match ---
    evidence_links = data.get("evidence_links", [])
    if evidence_links:
        clean_links = []
        for link in evidence_links:
            link_url = (link.get("url", "") or "").strip()
            if link_url and link_url in urls_to_purge:
                purged_items.append({
                    "type": "evidence_link",
                    "title": link.get("title", ""),
                    "url": link_url,
                    "reason": "AI verification traced false claim to this source",
                })
                print(f"   🗑️ [Purge] Removing evidence: '{link.get('title', '')[:60]}' ({link_url})")
            else:
                clean_links.append(link)

        evidence_removed = len(evidence_links) - len(clean_links)
        data["evidence_links"] = clean_links
    else:
        evidence_removed = 0

    # --- Purge reality_checklist items that reference purged URLs ---
    checklist = data.get("reality_checklist", [])
    if checklist:
        clean_checklist = []
        for item in checklist:
            item_text = ""
            if isinstance(item, dict):
                item_text = (item.get("finding", "") or item.get("text", "") or "")
                # Also check if the checklist item has a source/url field
                item_url = (item.get("url", "") or item.get("source_url", "") or "").strip()
                if item_url and item_url in urls_to_purge:
                    purged_items.append({
                        "type": "checklist_item",
                        "text": item_text[:100],
                        "url": item_url,
                        "reason": "AI verification traced false claim to this source",
                    })
                    print(f"   🗑️ [Purge] Removing checklist item: '{item_text[:60]}' ({item_url})")
                    continue
            # Check if any purged URL appears in the item text (catches inline references)
            item_text_lower = item_text.lower() if item_text else ""
            url_found_in_text = any(url.lower() in item_text_lower for url in urls_to_purge if url)
            if url_found_in_text:
                purged_items.append({
                    "type": "checklist_item",
                    "text": item_text[:100],
                    "reason": "Contains reference to purged source URL",
                })
                print(f"   🗑️ [Purge] Removing checklist item (URL ref): '{item_text[:60]}'")
            else:
                clean_checklist.append(item)

        checklist_removed = len(checklist) - len(clean_checklist)
        data["reality_checklist"] = clean_checklist
    else:
        checklist_removed = 0

    total = evidence_removed + checklist_removed
    if total > 0:
        print(f"   ✅ [Purge] Removed {evidence_removed} evidence links + {checklist_removed} checklist items (from {len(urls_to_purge)} traced URLs)")
    else:
        print(f"   ℹ️ [Purge] URLs identified but no matching evidence found in stored data (sources may already have been removed)")

    if skipped_claims > 0:
        print(f"   🛡️ [Purge] {skipped_claims} claim(s) had no traceable source — their evidence was preserved (safe mode)")

    return {
        "evidence_removed": evidence_removed,
        "checklist_removed": checklist_removed,
        "purged_items": purged_items,
        "skipped_claims": skipped_claims,
    }
