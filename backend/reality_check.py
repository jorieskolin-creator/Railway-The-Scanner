"""
Reality Check — Hallucination Detection for AI-Generated Summaries

Two layers:
1. Heuristic pattern matching (zero API cost) — catches obvious fakes
2. AI-powered fact-check (Gemini 3 Pro / 2.5 Pro backup + Google Search grounding)
   — verifies synthesis claims against live web data

Returns a hallucination risk level: "low", "medium", "high"
and a list of specific flags explaining what was detected.
"""

import re
import json
import os
import time


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
    except json.JSONDecodeError:
        pass

    # 5. Auto-repair common LLM JSON errors
    repaired = candidate
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
    except json.JSONDecodeError:
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
                repaired = repaired[:pos - 1] + repaired[pos:]
            else:
                break

    # 7. Final attempt
    try:
        return json.loads(repaired, strict=False)
    except json.JSONDecodeError:
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
        flags.append(f"Summary unusually long ({word_count} words, expected 180-220)")
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
# Single-phase verification using Gemini 3 Pro (backup: 2.5 Pro) with Google Search grounding.
# Gemini searches the web independently and fact-checks the synthesis against live data.
# This catches both evidence-sourced errors AND fabricated claims with no basis.
#
# Architecture rationale (replaces old two-phase Haiku contradiction + Flash fresh search):
#   - The core problem is EVIDENCE CONTAMINATION, not hallucination.
#     Checking the summary against stored evidence is circular — if the evidence
#     is wrong, the summary will be perfectly consistent with wrong evidence.
#   - Gemini 3 Pro with Google Search grounding is the right tool: strong reasoning
#     capability AND direct access to live web data to verify claims independently.
#   - After fact-check, if false claims found: contaminated evidence is purged and
#     Opus does a FULL re-synthesis from clean evidence (in main.py).
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


def _gemini_fact_check(summary_text, company_name, evidence_links=None):
    """
    Fact-check the synthesis using Gemini 3 Pro Preview (backup: 2.5 Pro) with
    Google Search grounding. Gemini searches the web independently and checks
    if the summary claims are true.

    This replaces both the old contradiction check (Haiku) and fresh search (2.5 Flash).
    Gemini 3 Pro is a strong model with reasoning capability + live web access — the
    right tool for catching both evidence-sourced errors and outright fabrications.

    Returns source_indices and source_urls for each false claim so only the specific
    sources that a false claim traces back to get purged.
    """
    import concurrent.futures

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return {
            "false_claims": [],
            "model": "skipped_no_gemini_key",
            "time_ms": 0,
        }

    # Build evidence reference list so Gemini can attribute false claims to sources
    evidence_ref = ""
    if evidence_links:
        verified_links = [l for l in evidence_links if l.get("verified", True)]
        for i, link in enumerate(verified_links[:20], 1):
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

    prompt = f"""You are a fact-checker with access to Google Search. Your job is to verify the following AI-generated company analysis by searching the web for the truth.

COMPANY: {company_name}

AI-GENERATED SUMMARY TO VERIFY:
{summary_text}
{evidence_section}
INSTRUCTIONS:
1. Search for the key factual claims in this summary: revenue figures, employee counts, partnerships, product names, executive names, specific events, dates, percentages.
2. For each specific claim, determine if it is TRUE, FALSE, or UNVERIFIABLE based on your search results.
3. ONLY flag claims that are clearly FALSE — where your search results show different facts.
   This includes both:
   - Claims that contradict your web search results (e.g. summary says "revenue €2.1B" but web shows €1.5B)
   - Claims that appear completely fabricated (no basis found anywhere on the web)
4. Do NOT flag claims you simply cannot find — the company may be small or private. Absence of evidence is not evidence of fabrication.
5. Do NOT flag qualitative statements, opinions, or general industry observations.
6. For each false claim, check the STORED EVIDENCE SOURCES above. If you can identify which stored source(s) [by index number] likely contributed to the false claim (e.g. the source itself contains wrong data that led to the error), include those indices. If you cannot trace the false claim to any stored source, return an empty array for source_indices.

Output ONLY valid JSON:
{{"false_claims": [{{"claim": "Claim X is false because web shows Y instead", "source_indices": [2, 5]}}], "summary_accurate": true/false}}

Each false_claims object must have:
  - "claim": string describing what is false and why
  - "source_indices": array of integer indices referring to STORED EVIDENCE SOURCES that likely contributed to this false claim. Use an EMPTY array [] if you cannot trace the claim to any specific stored source.

If the summary appears accurate or you found no clear falsehoods, return:
{{"false_claims": [], "summary_accurate": true}}"""

    # --- Try Gemini 3 Pro Preview first, then 2.5 Pro as backup ---
    from google import genai
    client = genai.Client(api_key=gemini_key)
    verified_links = [l for l in (evidence_links or []) if l.get("verified", True)][:20]

    from google.genai import types

    gemini_config = {
        'tools': [{'google_search': {}}],
        'temperature': 1.0,  # Gemini 3 requires temp=1.0 — lower values cause looping/timeouts
        'thinking_config': types.ThinkingConfig(thinking_level="LOW"),  # LOW for fast fact-check
        'safety_settings': [
            {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
        ]
    }

    # Config for Gemini 2.5 Pro fallback (no thinking_config needed)
    gemini_25_config = {
        'tools': [{'google_search': {}}],
        'temperature': 0.0,
        'safety_settings': gemini_config['safety_settings'],
    }

    models_to_try = [
        ("gemini-3-pro-preview", 90, gemini_config),      # Primary: Gemini 3 Pro, thinking_level=LOW
        ("gemini-2.5-pro", 45, gemini_25_config),          # Backup: Gemini 2.5 Pro
    ]

    for model_name, timeout_sec, model_config in models_to_try:
        try:
            print(f"   🔄 [Fact-Check] Trying {model_name}...")
            start = time.time()

            def _call(m=model_name, cfg=model_config):
                return client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=cfg,
                )

            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(_call)
            try:
                response = future.result(timeout=timeout_sec)
            except concurrent.futures.TimeoutError:
                pool.shutdown(wait=False)
                elapsed_ms = int((time.time() - start) * 1000)
                print(f"   ⏰ [Fact-Check] {model_name} timed out after {timeout_sec}s ({elapsed_ms}ms)")
                continue  # Try next model
            pool.shutdown(wait=False)

            elapsed_ms = int((time.time() - start) * 1000)
            text = response.text if response.text else ""

            if not text:
                print(f"   ⚠️ [Fact-Check] {model_name} returned empty response ({elapsed_ms}ms)")
                continue  # Try next model

            result = _robust_parse_json(text)
            if result and "false_claims" in result:
                # Respect the summary_accurate flag — if the LLM says true,
                # treat the list as empty even if it contains explanatory entries
                if result.get("summary_accurate") is True and result.get("false_claims"):
                    print(f"   ✅ [Fact-Check] {model_name} explicitly said summary_accurate=true ({elapsed_ms}ms)")
                    return {"false_claims": [], "model": model_name, "time_ms": elapsed_ms}

                raw_claims = result.get("false_claims", [])
                # Filter out bogus entries where the LLM put "no issue" text
                _not_real = ["no fabrication", "no false", "appears accurate", "all accurate",
                             "no issues", "could not find", "unable to verify", "summary is accurate",
                             "no contradiction", "no conflict", "all align", "nothing to flag"]
                false_claims = []
                for fc in raw_claims[:10]:
                    if isinstance(fc, str):
                        if any(p in fc.lower() for p in _not_real):
                            print(f"   ⏭️ [Fact-Check] Skipping non-issue entry: {fc[:80]}")
                            continue
                        false_claims.append({"claim": fc, "source_indices": [], "source_urls": []})
                    elif isinstance(fc, dict):
                        claim_text = fc.get("claim", str(fc))
                        if any(p in claim_text.lower() for p in _not_real):
                            print(f"   ⏭️ [Fact-Check] Skipping non-issue entry: {claim_text[:80]}")
                            continue
                        indices = fc.get("source_indices", [])
                        source_urls = []
                        for idx in indices:
                            if isinstance(idx, int) and 1 <= idx <= len(verified_links):
                                url = verified_links[idx - 1].get("url", "")
                                if url:
                                    source_urls.append(url)
                        false_claims.append({
                            "claim": claim_text,
                            "source_indices": indices,
                            "source_urls": source_urls,
                        })

                print(f"   {'⚠️' if false_claims else '✅'} [Fact-Check] {model_name}: {len(false_claims)} false claim(s) ({elapsed_ms}ms)")
                return {
                    "false_claims": false_claims,
                    "model": model_name,
                    "time_ms": elapsed_ms,
                }
            else:
                print(f"   ⚠️ [Fact-Check] Could not parse {model_name} response: {text[:200]}")
                continue  # Try next model

        except Exception as e:
            print(f"   ❌ [Fact-Check] {model_name} failed: {e}")
            continue  # Try next model

    # All models failed
    print(f"   ❌ [Fact-Check] All Gemini models failed — skipping fact-check")
    return {"false_claims": [], "model": "all_models_failed", "time_ms": 0}


def ai_fact_check_summary(summary_text, evidence_links, company_name):
    """
    AI-powered fact-check — single phase using Gemini 3 Pro (backup: 2.5 Pro)
    with Google Search grounding.

    Gemini independently verifies the synthesis claims against live web data.
    This catches both evidence-sourced errors (contaminated evidence) and
    fabricated claims that have no basis anywhere.

    If no false claims found → pipeline DONE, original synthesis stands.
    If false claims found → caller purges evidence + triggers full Opus re-synthesis.

    Returns:
        dict with:
            - verified: bool (True if no false claims found)
            - false_claims: list of false claim strings (for display/logging)
            - false_claims_detailed: list of structured dicts with source attribution
            - fact_check_model: str (which model was used)
            - fact_check_time_ms: int
    """
    if not summary_text:
        return {
            "verified": True,
            "false_claims": [],
            "false_claims_detailed": [],
            "fact_check_model": "skipped",
            "fact_check_time_ms": 0,
        }

    total_start = time.time()

    print(f"   🔍 [Fact-Check] Gemini fact-check against live web data...")
    result = _gemini_fact_check(summary_text, company_name, evidence_links=evidence_links)

    all_claims = []
    all_claims_detailed = []

    if result["false_claims"]:
        for fc in result["false_claims"]:
            claim_text = fc.get("claim", str(fc)) if isinstance(fc, dict) else str(fc)
            source_urls = fc.get("source_urls", []) if isinstance(fc, dict) else []
            source_indices = fc.get("source_indices", []) if isinstance(fc, dict) else []
            all_claims.append(claim_text)
            all_claims_detailed.append({
                "type": "false_claim",
                "claim": claim_text,
                "source_urls": source_urls,
                "source_indices": source_indices,
            })
            src_info = f" (sources: {source_urls})" if source_urls else " (no specific source identified)"
            print(f"      - {claim_text}{src_info}")

    total_ms = int((time.time() - total_start) * 1000)

    return {
        "verified": len(all_claims) == 0,
        "false_claims": all_claims[:10],
        "false_claims_detailed": all_claims_detailed[:10],
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
4. The archetype classification MUST remain EXACTLY as it appears in the original — do not change, re-evaluate, or remove it under any circumstances.
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
    """Rewrite summary using Gemini 3 Pro Preview (higher quality — used in admin recheck).
    Falls back to Gemini 2.5 Pro if Gemini 3 Pro fails."""
    import concurrent.futures
    from google import genai
    from google.genai import types

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        raise ValueError("No GEMINI_API_KEY configured")

    client = genai.Client(api_key=gemini_key)

    # --- Try Gemini 3 Pro Preview first ---
    try:
        start = time.time()

        def _call():
            return client.models.generate_content(
                model="gemini-3-pro-preview",
                contents=prompt,
                config={
                    'temperature': 1.0,  # Gemini 3 requires temp=1.0 — lower values cause looping
                    'thinking_config': types.ThinkingConfig(thinking_level="LOW"),
                    'max_output_tokens': 2048,
                    'response_mime_type': 'application/json',
                }
            )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_call)
        try:
            response = future.result(timeout=60)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            raise ValueError("Gemini 3 Pro Preview timed out after 60s")
        pool.shutdown(wait=False)

        elapsed_ms = int((time.time() - start) * 1000)
        response_text = response.text if response.text else ""
        if response_text:
            return response_text, elapsed_ms, "gemini-3-pro-preview"
        raise ValueError("Gemini 3 Pro Preview returned empty response")

    except Exception as g3_err:
        print(f"   ❌ [Clean] Gemini 3 Pro Preview failed: {g3_err}")

    # --- Fallback: Gemini 2.5 Pro ---
    print(f"   🔄 [Clean] Falling back to Gemini 2.5 Pro...")
    start = time.time()

    def _call_25():
        return client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config={
                'temperature': 0.3,
                'max_output_tokens': 2048,
                'response_mime_type': 'application/json',
            }
        )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call_25)
    try:
        response = future.result(timeout=60)
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        raise ValueError("Gemini 2.5 Pro timed out after 60s")
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
