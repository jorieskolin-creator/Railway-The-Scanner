"""
Reality Check — Hallucination Detection for AI-Generated Summaries

Two layers:
1. Heuristic pattern matching (zero API cost) — catches obvious fakes
2. AI-powered fact-check (GPT-5.4 with Claude Sonnet 4.6 fallback)
   — verifies synthesis claims against generation-eligible evidence
   — if false claims found, also produces corrected summary in a follow-up call

Returns a hallucination risk level: "low", "medium", "high"
and a list of specific flags explaining what was detected.
"""

import re
import json
import os
import time
from foresight_guardrails import sanitize_future_insight, validate_foresight_text, validate_future_deadlines


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


def _call_openai_text(prompt: str, model: str = "gpt-5.5", max_output_tokens: int = 10000, timeout: int = 90) -> str:
    api_key = _clean_env_var(os.environ.get("GPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise ValueError("No GPT_API_KEY configured")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=timeout)
    response = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )
    return _get_openai_text(response)

try:
    from source_quality import (
        annotate_evidence_links, filter_admissible_sources,
        is_boilerplate_snippet, is_title_echo, tier_snippet_bounds, describe_source,
        is_generation_eligible_source,
    )
except Exception:  # Keep reality_check import-safe in minimal deployments.
    annotate_evidence_links = None
    filter_admissible_sources = None
    is_boilerplate_snippet = None
    is_title_echo = None
    tier_snippet_bounds = None
    describe_source = None
    is_generation_eligible_source = None

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


def _is_supported_structured_financial_payload_claim(text: str, scan_payload: dict | None) -> bool:
    """Return true when a financial claim is directly represented in the compact scan payload."""
    if not text or not isinstance(scan_payload, dict):
        return False
    if not re.search(r'\b(?:revenue|sales|ebit|ebitda|profit|margin|cash flow|equity|financial|fy20\d{2}|€|\b\d+(?:\.\d+)?%|\bm\b|\bmillion)\b', text, re.IGNORECASE):
        return False
    payload_text = json.dumps(scan_payload.get("financial_payload") or {}, ensure_ascii=False).lower()
    if not payload_text or payload_text == "{}":
        return False
    claim_text = text.lower()
    values = re.findall(r'(?:€\s*)?\d+(?:[.,]\d+)?\s*(?:m|million|%|bn|billion)?', claim_text)
    values = [re.sub(r'\s+', '', v.replace(",", ".")) for v in values if len(v.strip()) >= 2]
    if values and all(v in re.sub(r'\s+', '', payload_text.replace(",", ".")) for v in values[:4]):
        return True
    words = [w for w in re.findall(r'[a-zåäöA-ZÅÄÖ]{4,}', claim_text) if w not in {"reported", "shows", "company", "annual", "financial"}]
    return len(words) >= 4 and sum(1 for w in words[:10] if w in payload_text) >= 4


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
# Two-stage verification: GPT-5.4 detects false claims, then GPT-5.5 or
# Claude Sonnet 4.6 surgically corrects them when needed.
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


def _provider_fact_check(summary_text, company_name, evidence_links=None, pitch_text=None, scan_payload=None):
    """Two-stage GPT/Claude fact-check pipeline."""
    verified_links = []
    evidence_ref = ""
    if evidence_links:
        for l in evidence_links:
            if is_generation_eligible_source and not is_generation_eligible_source(l):
                continue
            if not l.get("verified", True):
                continue
            title = (l.get("title") or "Unknown")[:80]
            snippet_raw = (l.get("source_quote") or l.get("snippet") or "").strip()
            tier = (l.get("claim_tier") or "D").upper()
            if tier_snippet_bounds is None:
                min_len, max_len = 0, 500
            else:
                min_len, max_len = tier_snippet_bounds(tier)
            if min_len and len(snippet_raw) < min_len:
                continue
            if is_title_echo and is_title_echo(snippet_raw, title):
                continue
            if is_boilerplate_snippet and is_boilerplate_snippet(snippet_raw):
                continue
            label = describe_source(l) if describe_source else "Source"
            verified_links.append({
                "title": title,
                "url": l.get("url", ""),
                "snippet": snippet_raw[:max_len],
                "tier": tier,
                "label": label,
            })
        verified_links = verified_links[:20]
        for i, link in enumerate(verified_links, 1):
            evidence_ref += (
                f"[{i}] [tier={link['tier']}] [{link['label']}] "
                f"{link['title']} | URL: {link['url']} | Snippet: {link['snippet']}\n"
            )

    evidence_section = f"""
STORED EVIDENCE SOURCES (these were used to generate the summary):
{evidence_ref}
""" if evidence_ref else ""
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

    stage1_prompt = f"""You are an uncompromising Data Auditor. Evaluate the faithfulness and factual accuracy of an AI-generated company summary.

COMPANY: {company_name}

AI-GENERATED SUMMARY TO VERIFY:
{summary_text}
{pitch_section}{evidence_section}{scan_payload_section}
INSTRUCTIONS:
1. Extract definitive claims from the summary and pitch.
2. Classify each claim before judging it:
   - EXTERNAL COMPANY FACT: company events, partnerships, products, customers, financial values, ownership, people, dates, operations, present-tense capabilities.
   - INTERNAL SCANNER OBSERVATION: statements about what the scan found or did not find, evidence counts, weak/no evidence by dimension, source quality, scores, confidence, archetype, evidence packet, or Scanner process state.
   - STRUCTURED FINANCIAL PAYLOAD FACT: financial index/sub-score, revenue/profitability/stability/investment finding, or exact financial value explicitly present in the scan payload's financial_payload.
3. For EXTERNAL COMPANY FACTS, cross-reference strictly against stored evidence. Use only verified, generation-eligible evidence as authoritative. Do not accept display-only, unsupported, wrong-entity, or weak evidence.
4. For STRUCTURED FINANCIAL PAYLOAD FACTS, verify against financial_payload in the SCAN PAYLOAD above. These facts may be valid Scanner financial-module facts even when they are not repeated in STORED EVIDENCE SOURCES. Flag them only if the value/detail is absent from or contradicts financial_payload.
5. For INTERNAL SCANNER OBSERVATIONS, verify against the SCAN PAYLOAD above, not external web sources. Do not flag an internal observation merely because no public webpage says it. Flag it only if it contradicts the scan payload or invents a Scanner result that is not present.
6. If stored evidence is insufficient for an external company fact, use cautious external verification through your own provider tools/knowledge, but do not invent corrections.
7. A company website is self-reported. It cannot by itself refute analytical observations about internal operating reality unless it contains concrete, specific operational metrics.
8. Foresight/regulatory context cannot prove present-tense company behavior.
9. Use "Internal Scan Mismatch" only for scanner-process statements that contradict the scan payload. Use Fabrication/Contradiction/Misattribution/Organizational Fabrication for external company facts.

OUTPUT FORMAT — valid JSON only:
If accurate: {{"false_claims": [], "summary_accurate": true}}
If false claims found: {{"false_claims": [{{"claim": "Exact quote or close paraphrase", "error_type": "Fabrication OR Contradiction OR Misattribution OR Organizational Fabrication OR Internal Scan Mismatch", "reasoning": "Brief reason", "correction": "Verified replacement or empty string", "wrong_value": "", "correct_value": "", "source_indices": [1]}}], "summary_accurate": false}}
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
                indices = fc.get("source_indices") or []
                source_urls = []
                for idx in indices:
                    if isinstance(idx, int) and 1 <= idx <= len(verified_links):
                        url = verified_links[idx - 1].get("url", "")
                        if url:
                            source_urls.append(url)
                parsed.append({
                    "claim": claim_text,
                    "error_type": fc.get("error_type") or "Unknown",
                    "reasoning": fc.get("reasoning") or "",
                    "correction": fc.get("correction") or "",
                    "wrong_value": fc.get("wrong_value") or "",
                    "correct_value": fc.get("correct_value") or "",
                    "source_indices": indices,
                    "source_urls": source_urls,
                })
        return parsed

    stage1_chain = ("gpt-5.4", "claude-sonnet-4-6")
    for model_name in stage1_chain:
        try:
            print(f"   🔄 [Fact-Check S1] Trying {model_name}...")
            start = time.time()
            if model_name.startswith("gpt-"):
                text = _call_openai_text(stage1_prompt, model=model_name, max_output_tokens=8000, timeout=90)
            else:
                import anthropic
                anthropic_key = _get_anthropic_key()
                if not anthropic_key:
                    raise ValueError("No ANTHROPIC_API_KEY configured")
                client = anthropic.Anthropic(api_key=anthropic_key)
                resp = client.messages.create(
                    model=model_name,
                    max_tokens=8000,
                    messages=[{"role": "user", "content": stage1_prompt}],
                )
                text = "".join(getattr(block, "text", "") for block in resp.content if getattr(block, "text", "")).strip()
            elapsed = int((time.time() - start) * 1000)
            result = _robust_parse_json(text)
            if not result or "false_claims" not in result:
                print(f"   ⚠️ [Fact-Check S1] Could not parse {model_name} response: {text[:500]}")
                continue
            if result.get("summary_accurate") is True and result.get("false_claims"):
                return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": model_name, "time_ms": elapsed}
            false_claims = _parse_false_claims(result)
            print(f"   {'⚠️' if false_claims else '✅'} [Fact-Check S1] {model_name}: {len(false_claims)} false claim(s) ({elapsed}ms)")
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

    claims_text = "\n".join(
        f"- [{fc.get('error_type', 'Unknown')}] {fc.get('claim', '')} (reason: {fc.get('reasoning', '')})\n  → VERIFIED CORRECTION: {fc.get('correction', '')}"
        for fc in stage1_false_claims
    )
    pitch_correction_note = f"""
ORIGINAL PITCH TO ALSO CORRECT (fix only if it references a false claim):
{pitch_text}
""" if pitch_text else ""
    stage2_prompt = f"""You are a precise editorial editor. Surgically correct only the false claims below while preserving every unaffected sentence.

COMPANY: {company_name}

ORIGINAL SUMMARY:
{summary_text}
{pitch_correction_note}
IDENTIFIED FALSE CLAIMS WITH VERIFIED CORRECTIONS:
{claims_text}

Rules:
- Apply provided verified corrections exactly.
- Delete fabricated sentences when no verified correction exists.
- Do not add new claims.
- Return valid JSON only: {{"corrected_summary": "...", "corrected_pitch": "..."}}
"""

    corrected_summary = ""
    corrected_pitch = ""
    elapsed2 = 0
    s2_model_name = None
    for model_name in ("gpt-5.5", "claude-sonnet-4-6"):
        try:
            print(f"   🔄 [Fact-Check S2] Trying {model_name} correction...")
            start = time.time()
            if model_name.startswith("gpt-"):
                text = _call_openai_text(stage2_prompt, model=model_name, max_output_tokens=10000, timeout=90)
            else:
                import anthropic
                anthropic_key = _get_anthropic_key()
                if not anthropic_key:
                    raise ValueError("No ANTHROPIC_API_KEY configured")
                client = anthropic.Anthropic(api_key=anthropic_key)
                resp = client.messages.create(
                    model=model_name,
                    max_tokens=10000,
                    messages=[{"role": "user", "content": stage2_prompt}],
                )
                text = "".join(getattr(block, "text", "") for block in resp.content if getattr(block, "text", "")).strip()
            elapsed2 = int((time.time() - start) * 1000)
            result = _robust_parse_json(text)
            if result and result.get("corrected_summary"):
                corrected_summary = result.get("corrected_summary", "")
                corrected_pitch = result.get("corrected_pitch", "")
                s2_model_name = model_name
                print(f"   ✏️ [Fact-Check S2] Correction produced ({len(corrected_summary.split())} words, {elapsed2}ms)")
                break
        except Exception as e:
            print(f"   ❌ [Fact-Check S2] {model_name} failed: {e}")

    return {
        "false_claims": stage1_false_claims,
        "corrected_summary": corrected_summary,
        "corrected_pitch": corrected_pitch,
        "model": f"{stage1_model_name}+{s2_model_name or 's2-failed'}",
        "time_ms": stage1_time_ms + elapsed2,
    }

def ai_fact_check_summary(summary_text, evidence_links, company_name, pitch_text=None, scan_payload=None):
    """
    AI-powered fact-check — two-stage pipeline:
      Stage 1: GPT-5.4 → Claude Sonnet 4.6 — detects false claims.
      Stage 2: GPT-5.5 → Claude Sonnet 4.6 — surgically corrects false claims.

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
            "false_claims": [],
            "false_claims_detailed": [],
            "corrected_summary": "",
            "corrected_pitch": "",
            "fact_check_model": "skipped",
            "fact_check_time_ms": 0,
        }

    total_start = time.time()

    print(f"   🔍 [Fact-Check] GPT/Claude fact-check against generation-eligible evidence...")
    result = _provider_fact_check(
        summary_text,
        company_name,
        evidence_links=evidence_links,
        pitch_text=pitch_text,
        scan_payload=scan_payload,
    )

    all_claims = []
    all_claims_detailed = []

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

            # Skip internal scan history false positives — score trajectories and
            # scan count references are based on internal DB records and will never
            # appear in external web sources, so source_indices is always empty for them.
            if not source_indices and _SCAN_HISTORY_RE.search(claim_text):
                print(f"      [Fact-Check] Skipping internal scan history claim (not externally verifiable): {claim_text[:70]}...")
                continue

            if not source_indices and _is_supported_structured_financial_payload_claim(claim_text, scan_payload):
                print(f"      [Fact-Check] Skipping structured financial payload claim: {claim_text[:70]}...")
                continue

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

            all_claims.append(claim_text)
            all_claims_detailed.append({
                "type": "false_claim",
                "claim": claim_text,
                "error_type": error_type,
                "reasoning": reasoning,
                "correction": correction,
                "wrong_value": wrong_value,
                "correct_value": correct_value,
                "source_urls": source_urls,
                "source_indices": source_indices,
            })
            src_info = f" (sources: {source_urls})" if source_urls else " (no specific source identified)"
            error_info = f" [{error_type}]" if error_type != "Unknown" else ""
            print(f"      -{error_info} {claim_text}{src_info}")
            if reasoning:
                print(f"        Reasoning: {reasoning}")

    total_ms = int((time.time() - total_start) * 1000)

    corrected_summary = result.get("corrected_summary") or ""
    corrected_pitch = result.get("corrected_pitch") or ""
    fact_check_skipped = result.get("fact_check_skipped", False)

    return {
        # verified=False when skipped: an unverified summary must not be treated
        # as clean. It stays unmodified but is flagged so callers can handle it.
        "verified": not fact_check_skipped and len(all_claims) == 0,
        "fact_check_skipped": fact_check_skipped,
        "false_claims": all_claims[:10],
        "false_claims_detailed": all_claims_detailed[:10],
        "corrected_summary": corrected_summary,
        "corrected_pitch": corrected_pitch,
        "fact_check_model": result["model"],
        "fact_check_time_ms": total_ms,
    }


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
    """Rewrite summary using GPT-5.5, with Claude Sonnet 4.6 fallback."""
    try:
        start = time.time()
        print(f"   🔄 [Clean] Attempting gpt-5.5 summary rewrite...")
        response_text = _call_openai_text(prompt, model="gpt-5.5", max_output_tokens=6000, timeout=90)
        elapsed_ms = int((time.time() - start) * 1000)
        if response_text:
            return response_text, elapsed_ms, "gpt-5.5"
        raise ValueError("gpt-5.5 returned empty response")
    except Exception as gpt_err:
        print(f"   ❌ [Clean] GPT 5.5 failed: {gpt_err}")

    anthropic_key = _get_anthropic_key()
    if anthropic_key:
        try:
            import anthropic
            print(f"   🔄 [Clean] Falling back to Claude Sonnet 4.6...")
            m_start = time.time()
            sonnet_client = anthropic.Anthropic(api_key=anthropic_key)
            sonnet_resp = sonnet_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
            m_text = "".join(
                block.text for block in sonnet_resp.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", "")
            ).strip()
            m_elapsed = int((time.time() - m_start) * 1000)
            if m_text:
                return m_text, m_elapsed, "claude-sonnet-4-6"
            print(f"   ⚠️ [Clean] Sonnet returned empty response")
        except Exception as sonnet_err:
            print(f"   ❌ [Clean] Claude Sonnet 4.6 fallback failed: {sonnet_err}")

    raise ValueError("All clean-summary models failed (gpt-5.5, claude-sonnet-4-6)")


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

    # Collect all source URLs that the AI explicitly attributed to false claims
    urls_to_purge = set()
    skipped_claims = 0
    claims_with_sources = 0

    if detailed_claims:
        for dc in detailed_claims:
            source_urls = dc.get("source_urls", [])
            claim_text = dc.get("claim", "")
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
