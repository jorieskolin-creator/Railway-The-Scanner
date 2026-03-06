"""
Reality Check — Hallucination Detection for AI-Generated Summaries

Two layers:
1. Heuristic pattern matching (zero API cost) — catches obvious fakes
2. AI-powered fact-check (Gemini 3.1 Pro / 3 Pro / 2.5 Pro backup + Google Search grounding)
   — verifies synthesis claims against live web data
   — if false claims found, also produces corrected summary in the same call

Returns a hallucination risk level: "low", "medium", "high"
and a list of specific flags explaining what was detected.
"""

import re
import json
import os
import time
from gemini_backoff import call_with_503_backoff

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
            - score: 0-100 hallucination suspicion score
            - summary_ok: bool (True if safe to save as-is)
    """
    if not summary_text or len(summary_text.strip()) < 20:
        return {
            "risk": "high",
            "flags": ["Summary is empty or too short"],
            "score": 90,
            "summary_ok": False,
        }

    flags = []
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

    # --- CHECK 7: Excessive length (AI rambling / hallucination drift) ---
    word_count = len(summary_text.split())
    if word_count > 350:
        flags.append(f"Summary unusually long ({word_count} words, expected 325-375)")
        score += 10

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
# Single-phase verification using Gemini 3.1 Pro (backup: 3 Pro / 2.5 Pro) with Google Search grounding.
# Gemini searches the web independently and fact-checks the synthesis against live data.
# This catches both evidence-sourced errors AND fabricated claims with no basis.
#
# Architecture rationale (replaces old two-phase Haiku contradiction + Flash fresh search):
#   - The core problem is EVIDENCE CONTAMINATION, not hallucination.
#     Checking the summary against stored evidence is circular — if the evidence
#     is wrong, the summary will be perfectly consistent with wrong evidence.
#   - Gemini 3.1 Pro with Google Search grounding is the right tool: strong reasoning
#     capability AND direct access to live web data to verify claims independently.
#   - If false claims found: Gemini 3.1 Pro also produces a corrected summary in the
#     same call (fact-check + fix in one step). Evidence is still purged in main.py.
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


def _gemini_fact_check(summary_text, company_name, evidence_links=None, pitch_text=None):
    """
    Two-stage fact-check pipeline:

      Stage 1 — Gemini 2.5 Pro + Google Search (detection):
        Finds false claims and attributes them to evidence sources.
        Backup: Gemini 3.1 Pro if 2.5 Pro fails/times out.

      Stage 2 — Gemini 3.1 Pro, no search (correction):
        Given the false claims from Stage 1, surgically rewrites the summary
        and pitch to remove them. Uses thinking budget for precise editing.
        Only runs when Stage 1 found at least one false claim.

    Returns source_indices and source_urls for each false claim so only the
    specific sources that a false claim traces back to get purged.
    """
    import concurrent.futures

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return {
            "false_claims": [],
            "corrected_summary": "",
            "corrected_pitch": "",
            "model": "skipped_no_gemini_key",
            "time_ms": 0,
        }

    # Build evidence reference list so Gemini can attribute false claims to sources
    verified_links = []
    evidence_ref = ""
    if evidence_links:
        verified_links = [l for l in evidence_links if l.get("verified", True)][:20]
        for i, link in enumerate(verified_links, 1):
            title = link.get("title", "Unknown")
            url = link.get("url", "")
            snippet = link.get("snippet", "")[:150]
            evidence_ref += f"[{i}] {title} | URL: {url} | Snippet: {snippet}\n"

    evidence_section = ""
    if evidence_ref:
        evidence_section = f"""
STORED EVIDENCE SOURCES (these were used to generate the summary):
{evidence_ref}
"""

    pitch_section = ""
    if pitch_text:
        pitch_section = f"""
AI-GENERATED PITCH TO VERIFY:
{pitch_text}
"""

    # ── Stage 1 prompt: detection only (no correction asked) ─────────────────
    stage1_prompt = f"""You are an uncompromising Data Auditor. Your job is to evaluate the faithfulness and factual accuracy of an AI-generated company summary. You must ruthlessly identify hallucinations, fabrications, and contradictions.

COMPANY: {company_name}

AI-GENERATED SUMMARY TO VERIFY:
{summary_text}
{pitch_section}{evidence_section}
INSTRUCTIONS:
You must evaluate the summary using the following step-by-step logic:

Step 1: Extract every definitive claim from the summary (including financial metrics, operational realities, "lack of evidence" claims, and strategic assessments).

Step 2: Cross-reference each claim strictly against the STORED EVIDENCE SOURCES above.

Step 3: Use Google Search to verify real-world accuracy if the claim cannot be proven or disproven by the stored sources alone.

FLAG A CLAIM AS FALSE IF IT MEETS ANY OF THESE CRITERIA:

A. [Fabrication/Unsupported]: The summary states a specific metric, event, or operational fact (e.g., "no digital investments," "10 million euros") that is NOT explicitly supported by the STORED EVIDENCE SOURCES. In an AI summary, adding new unsourced facts is a hallucination.

B. [Contradiction]: The summary makes a claim that directly contradicts the STORED EVIDENCE SOURCES or verifiable facts found via your Google Search.

C. [Misattribution/Arithmetic]: A number from a source is used in the wrong context. This is the MOST COMMON financial error — pay extra attention:
   - A year-over-year CHANGE (delta) cited as if it were the base figure. Example: a source says "profit decreased by €153.7M" but the summary says "from the €153.7M operating profit" — the €153.7M is the decrease, not the profit itself.
   - Revenue confused with profit, or operating profit confused with net profit.
   - A figure from one time period attributed to a different period.
   - A percentage change confused with an absolute figure, or vice versa.
   When you see a financial figure in the summary, verify not just that the number EXISTS in the source, but that it is attributed to the CORRECT METRIC and used in the CORRECT CONTEXT.

Do NOT ignore qualitative claims if they are based on fabricated underlying metrics.
Do NOT flag Knowledge Base terminology or archetype labels (e.g. "Digital Taylorism", "Thriving", "At Risk") — these are analytical framework terms, not factual claims.
Do NOT flag references to previous scan counts, historical score trajectories (e.g. arrow-notation scores like "72→76→75", phrases like "across X scans" or "across X assessments"), or any comparative claim about this scan versus prior assessments — these are sourced from internal scan database records, not externally verifiable data.

OUTPUT FORMAT — valid JSON only:

If summary is accurate (no false claims):
{{"false_claims": [], "summary_accurate": true}}

If false claims found:
{{"false_claims": [{{"claim": "Exact quote of the false claim from the summary", "error_type": "Fabrication OR Contradiction", "reasoning": "Briefly explain why this is false based on the sources or search results.", "source_indices": [2, 5]}}], "summary_accurate": false}}

Each false_claims object must have:
  - "claim": string — the exact quote or close paraphrase of the false claim from the summary
  - "error_type": string — either "Fabrication" or "Contradiction"
  - "reasoning": string — brief explanation of why this is false
  - "source_indices": array of integer indices referring to STORED EVIDENCE SOURCES that were contradicted or misrepresented. Use an EMPTY array [] if the claim is completely fabricated with no traceable source."""

    # ── Gemini client + configs ───────────────────────────────────────────────
    from google import genai
    client = genai.Client(api_key=gemini_key)

    from google.genai import types

    _safety = [
        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
    ]

    # Stage 1: Google Search enabled — 2.5 Pro (primary), 3.1 Pro (backup)
    # max_output_tokens raised from 4096 → 8000:
    #   gemini-2.5-pro defaults to thinking mode; thinking tokens share the
    #   same pool as output tokens. At 4096, ~2000 tokens consumed by thinking
    #   left only ~2096 for the JSON — tight when claims have long reasoning.
    #   thinking_budget=1024 caps internal reasoning, reserving ~7000 for output.
    #   gemini-3.1-pro-preview: now uses thinking_budget=3000 (mid-MEDIUM range: 2000-3500);
    #   8000 max_output_tokens still gives comfortable headroom for the JSON response.
    gemini_25_search_config = {
        'tools': [{'google_search': {}}],
        'temperature': 0.0,
        'max_output_tokens': 8000,
        'thinking_config': types.ThinkingConfig(thinking_budget=1024),
        'safety_settings': _safety,
    }
    gemini_31_search_config = {
        'tools': [{'google_search': {}}],
        'temperature': 0.5,
        'thinking_config': types.ThinkingConfig(thinking_budget=3000),  # MEDIUM = 2000-3500 tokens; explicit cap
        'max_output_tokens': 8000,
        'safety_settings': _safety,
    }
    # gemini-3-pro-preview removed: shares 3.x rate limits with 3.1-pro (useless
    # as fallback during overload) and is being deprecated by Google.

    # Stage 2: No search tool — pure text editing by 3.1 Pro.
    # gemini-3.1-pro-preview REQUIRES thinking (budget=0 → 400 INVALID_ARGUMENT).
    # With no thinking_config the model defaults to MEDIUM (~2000-3500 tokens),
    # which at the old max_output_tokens=4096 left too few tokens for the JSON
    # — truncating the corrected_summary mid-sentence.
    # Fix: cap thinking at 1024 (minimal) and raise max_output_tokens to 10000
    # so ~9000 tokens remain for the corrected JSON output.
    gemini_31_correction_config = {
        'temperature': 0.2,
        'max_output_tokens': 10000,
        'thinking_config': types.ThinkingConfig(thinking_budget=1024),
        'safety_settings': _safety,
    }

    stage1_models = [
        ("gemini-2.5-pro", 90, gemini_25_search_config),          # Primary: 2.5 Pro + Google Search, temp=0.0
        ("gemini-3.1-pro-preview", 90, gemini_31_search_config),  # Backup: 3.1 Pro + Google Search
    ]

    # ── Helper: single model call with timeout ────────────────────────────────
    def _call_model(model_name, config, prompt_text, timeout_sec):
        """Returns (response, elapsed_ms) or raises TimeoutError / Exception."""
        start = time.time()

        def _call():
            return call_with_503_backoff(
                lambda: client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=config,
                ),
                label=f"Fact-Check {model_name}",
            )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_call)
        try:
            response = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            elapsed_ms = int((time.time() - start) * 1000)
            raise TimeoutError(f"timed out after {timeout_sec}s ({elapsed_ms}ms)")
        pool.shutdown(wait=False)
        elapsed_ms = int((time.time() - start) * 1000)
        return response, elapsed_ms

    # ── Helper: extract text from response (response.text first, parts fallback)
    def _extract_text(response, label):
        try:
            _finish = getattr(response.candidates[0], 'finish_reason', 'unknown')
            text = response.text if response.text else ""
            if not text:
                parts = response.candidates[0].content.parts
                text = "".join(
                    p.text for p in parts
                    if p.text and not getattr(p, 'thought', False)
                ).strip()
            _part_sizes = []
            try:
                _part_sizes = [(getattr(p, 'thought', False), len(p.text) if p.text else 0)
                               for p in response.candidates[0].content.parts]
            except Exception:
                pass
            print(f"   🔍 [Fact-Check] {label}: finish={_finish}, text_len={len(text)}, parts={_part_sizes}")
            return text
        except Exception as _diag_e:
            text = response.text if response.text else ""
            print(f"   🔍 [Fact-Check] {label}: response access failed ({_diag_e}), response.text len={len(text)}")
            return text

    # ═══════════════════════════════════════════════════════════════════════════
    # STAGE 1 — Gemini 2.5 Pro (primary) + Google Search: detection
    # ═══════════════════════════════════════════════════════════════════════════
    stage1_false_claims = None
    stage1_model_name = None
    stage1_time_ms = 0

    for model_name, timeout_sec, model_config in stage1_models:
        try:
            print(f"   🔄 [Fact-Check S1] Trying {model_name}...")
            try:
                response, elapsed_ms = _call_model(model_name, model_config, stage1_prompt, timeout_sec)
            except TimeoutError as te:
                print(f"   ⏰ [Fact-Check S1] {model_name} {te}")
                continue

            text = _extract_text(response, model_name)
            if not text:
                print(f"   ⚠️ [Fact-Check S1] {model_name} returned empty response ({elapsed_ms}ms)")
                continue

            result = _robust_parse_json(text)
            if not result or "false_claims" not in result:
                print(f"   ⚠️ [Fact-Check S1] Could not parse {model_name} response: {text[:1000]}")
                continue

            # Respect summary_accurate override
            if result.get("summary_accurate") is True and result.get("false_claims"):
                print(f"   ✅ [Fact-Check S1] {model_name} explicitly said summary_accurate=true ({elapsed_ms}ms)")
                return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": model_name, "time_ms": elapsed_ms}

            raw_claims = result.get("false_claims", [])
            _not_real = ["no fabrication", "no false", "appears accurate", "all accurate",
                         "no issues", "could not find", "unable to verify", "summary is accurate",
                         "no contradiction", "no conflict", "all align", "nothing to flag"]
            false_claims = []
            for fc in raw_claims[:10]:
                if isinstance(fc, str):
                    if any(p in fc.lower() for p in _not_real):
                        print(f"   ⏭️ [Fact-Check S1] Skipping non-issue entry: {fc[:80]}")
                        continue
                    false_claims.append({"claim": fc, "error_type": "Unknown", "reasoning": "", "source_indices": [], "source_urls": []})
                elif isinstance(fc, dict):
                    claim_text = fc.get("claim", str(fc))
                    if any(p in claim_text.lower() for p in _not_real):
                        print(f"   ⏭️ [Fact-Check S1] Skipping non-issue entry: {claim_text[:80]}")
                        continue
                    error_type = fc.get("error_type", "Unknown")
                    reasoning = fc.get("reasoning", "")
                    indices = fc.get("source_indices", [])
                    source_urls = []
                    for idx in indices:
                        if isinstance(idx, int) and 1 <= idx <= len(verified_links):
                            url = verified_links[idx - 1].get("url", "")
                            if url:
                                source_urls.append(url)
                    false_claims.append({
                        "claim": claim_text,
                        "error_type": error_type,
                        "reasoning": reasoning,
                        "source_indices": indices,
                        "source_urls": source_urls,
                    })

            print(f"   {'⚠️' if false_claims else '✅'} [Fact-Check S1] {model_name}: {len(false_claims)} false claim(s) ({elapsed_ms}ms)")
            stage1_false_claims = false_claims
            stage1_model_name = model_name
            stage1_time_ms = elapsed_ms
            break

        except Exception as e:
            print(f"   ❌ [Fact-Check S1] {model_name} failed: {e}")
            continue

    if stage1_false_claims is None:
        print(f"   ❌ [Fact-Check] All Stage 1 models failed — skipping fact-check")
        return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": "all_models_failed", "time_ms": 0, "fact_check_skipped": True}

    # No false claims → done, Stage 2 not needed
    if not stage1_false_claims:
        return {"false_claims": [], "corrected_summary": "", "corrected_pitch": "", "model": stage1_model_name, "time_ms": stage1_time_ms}

    # ═══════════════════════════════════════════════════════════════════════════
    # STAGE 2 — Gemini 3.1 Pro, no search: surgical correction
    # Runs only because Stage 1 found false claims.
    # ═══════════════════════════════════════════════════════════════════════════
    claims_text = "\n".join(
        f"- [{fc['error_type']}] {fc['claim']}  (reason: {fc['reasoning']})"
        for fc in stage1_false_claims
    )

    pitch_correction_note = ""
    if pitch_text:
        pitch_correction_note = f"""
ORIGINAL PITCH TO ALSO CORRECT (fix only if it references a false claim):
{pitch_text}
"""

    stage2_prompt = f"""You are a precise editorial editor. The following AI-generated company summary has been fact-checked and specific false claims have been identified. Your sole job is to surgically remove or fix those false claims while preserving everything else exactly as written.

COMPANY: {company_name}

ORIGINAL SUMMARY:
{summary_text}
{pitch_correction_note}
IDENTIFIED FALSE CLAIMS TO REMOVE/FIX:
{claims_text}

CORRECTION RULES:
- Surgically remove or fix ONLY the sentences containing the identified false claims.
- Preserve the original text word-for-word for ALL sentences not affected by false claims.
- Do NOT add new information, analysis, or claims of your own.
- Do NOT change the archetype classification or Knowledge Base terminology.
- After removing a false claim sentence, fix only the immediately adjacent transitions so the text reads naturally.
- The corrected summary must remain close to the original length (within 80% of original word count).
- If the pitch references any of the false claims, fix those parts too; otherwise return the pitch unchanged.

OUTPUT FORMAT — valid JSON only:
{{"corrected_summary": "The surgically corrected summary...", "corrected_pitch": "The corrected pitch, or the original pitch if it had no false claims..."}}"""

    corrected_summary = ""
    corrected_pitch = ""
    elapsed2 = 0
    try:
        print(f"   🔄 [Fact-Check S2] Gemini 3.1 Pro correcting {len(stage1_false_claims)} false claim(s)...")
        try:
            response2, elapsed2 = _call_model("gemini-3.1-pro-preview", gemini_31_correction_config, stage2_prompt, 60)
        except TimeoutError as te:
            print(f"   ⏰ [Fact-Check S2] gemini-3.1-pro-preview {te} — returning claims without correction")
            return {
                "false_claims": stage1_false_claims,
                "corrected_summary": "",
                "corrected_pitch": "",
                "model": f"{stage1_model_name}+3.1pro(s2-timeout)",
                "time_ms": stage1_time_ms,
            }

        text2 = _extract_text(response2, "gemini-3.1-pro-preview (S2 correction)")
        if text2:
            result2 = _robust_parse_json(text2)
            if result2:
                corrected_summary = result2.get("corrected_summary", "")
                corrected_pitch = result2.get("corrected_pitch", "")
                if corrected_summary:
                    print(f"   ✏️ [Fact-Check S2] Correction produced ({len(corrected_summary.split())} words, {elapsed2}ms)")
                else:
                    print(f"   ⚠️ [Fact-Check S2] Parsed but no corrected_summary in response")
            else:
                print(f"   ⚠️ [Fact-Check S2] Could not parse Stage 2 response: {text2[:500]}")
        else:
            print(f"   ⚠️ [Fact-Check S2] Empty response from Stage 2")

    except Exception as e:
        print(f"   ❌ [Fact-Check S2] gemini-3.1-pro-preview failed: {e} — returning claims without correction")

    return {
        "false_claims": stage1_false_claims,
        "corrected_summary": corrected_summary,
        "corrected_pitch": corrected_pitch,
        "model": f"{stage1_model_name}+3.1pro",
        "time_ms": stage1_time_ms + elapsed2,
    }


def ai_fact_check_summary(summary_text, evidence_links, company_name, pitch_text=None):
    """
    AI-powered fact-check — two-stage pipeline:
      Stage 1: Gemini 2.5 Pro + Google Search detects false claims.
      Stage 2: Gemini 3.1 Pro (no search) surgically corrects them.

    Gemini 2.5 Pro independently verifies the synthesis claims against live
    web data. This catches both evidence-sourced errors (contaminated evidence)
    and fabricated claims that have no basis anywhere.

    If no false claims found → pipeline DONE, original synthesis stands.
    If false claims found → Gemini 3.1 Pro produces a corrected summary
    in a dedicated correction call. Evidence purging still
    happens in the caller (main.py).

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

    print(f"   🔍 [Fact-Check] Gemini 3.1 Pro fact-check against live web data...")
    result = _gemini_fact_check(summary_text, company_name, evidence_links=evidence_links, pitch_text=pitch_text)

    all_claims = []
    all_claims_detailed = []

    if result["false_claims"]:
        for fc in result["false_claims"]:
            claim_text = fc.get("claim", str(fc)) if isinstance(fc, dict) else str(fc)
            error_type = fc.get("error_type", "Unknown") if isinstance(fc, dict) else "Unknown"
            reasoning = fc.get("reasoning", "") if isinstance(fc, dict) else ""
            source_urls = fc.get("source_urls", []) if isinstance(fc, dict) else []
            source_indices = fc.get("source_indices", []) if isinstance(fc, dict) else []

            # Skip internal scan history false positives — score trajectories and
            # scan count references are based on internal DB records and will never
            # appear in external web sources, so source_indices is always empty for them.
            if not source_indices and _SCAN_HISTORY_RE.search(claim_text):
                print(f"      [Fact-Check] Skipping internal scan history claim (not externally verifiable): {claim_text[:70]}...")
                continue

            all_claims.append(claim_text)
            all_claims_detailed.append({
                "type": "false_claim",
                "claim": claim_text,
                "error_type": error_type,
                "reasoning": reasoning,
                "source_urls": source_urls,
                "source_indices": source_indices,
            })
            src_info = f" (sources: {source_urls})" if source_urls else " (no specific source identified)"
            error_info = f" [{error_type}]" if error_type != "Unknown" else ""
            print(f"      -{error_info} {claim_text}{src_info}")
            if reasoning:
                print(f"        Reasoning: {reasoning}")

    total_ms = int((time.time() - total_start) * 1000)

    corrected_summary = result.get("corrected_summary", "")
    corrected_pitch = result.get("corrected_pitch", "")
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
    """Build the cleaning prompt for admin recheck (Gemini-based)."""
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


def _clean_with_gemini(prompt):
    """Rewrite summary using Gemini 3.1 Pro Preview (higher quality — used in admin recheck).
    Falls back to Gemini 3 Pro → Gemini 2.5 Pro if Gemini 3.1 Pro fails."""
    import concurrent.futures
    from google import genai
    from google.genai import types

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        raise ValueError("No GEMINI_API_KEY configured")

    client = genai.Client(api_key=gemini_key)

    # --- Try Gemini 3.1 Pro Preview first (MEDIUM thinking) ---
    try:
        start = time.time()

        def _call():
            return call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                    config={
                        'temperature': 0.5,
                        'thinking_config': types.ThinkingConfig(thinking_budget=3000),  # MEDIUM = 2000-3500 tokens; explicit cap
                        'max_output_tokens': 6000,
                        'response_mime_type': 'application/json',
                    }
                ),
                label="Clean gemini-3.1-pro-preview",
            )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_call)
        try:
            response = future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            raise ValueError("Gemini 3.1 Pro Preview timed out after 60s")
        pool.shutdown(wait=False)

        elapsed_ms = int((time.time() - start) * 1000)
        response_text = response.text if response.text else ""
        if response_text:
            return response_text, elapsed_ms, "gemini-3.1-pro-preview"
        raise ValueError("Gemini 3.1 Pro Preview returned empty response")

    except Exception as g31_err:
        print(f"   ❌ [Clean] Gemini 3.1 Pro Preview failed: {g31_err}")

    # gemini-3-pro-preview fallback removed: shares 3.x rate limits with 3.1-pro
    # and is being deprecated by Google.

    # --- Fallback: Gemini 2.5 Pro (separate rate limits from 3.x family) ---
    print(f"   🔄 [Clean] Falling back to Gemini 2.5 Pro...")
    start = time.time()

    def _call_25():
        return call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config={
                    'temperature': 0.0,
                    'max_output_tokens': 2048,
                    'response_mime_type': 'application/json',
                }
            ),
            label="Clean gemini-2.5-pro",
        )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call_25)
    try:
        response = future.result(timeout=60)
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        raise ValueError("All Gemini models failed — Gemini 2.5 Pro timed out after 60s")
    pool.shutdown(wait=False)

    elapsed_ms = int((time.time() - start) * 1000)
    response_text = response.text if response.text else ""
    return response_text, elapsed_ms, "gemini-2.5-pro"


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
                   anthropic_key=None, use_gemini=False, use_opus=False):
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
        use_gemini: Unused (always uses Gemini now)
        use_opus: Unused (always uses Gemini now)

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
        print(f"   🔄 [Clean] Using Gemini for summary rewrite...")
        response_text, elapsed_ms, model_name = _clean_with_gemini(prompt)

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
