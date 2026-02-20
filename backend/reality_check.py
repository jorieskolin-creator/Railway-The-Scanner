"""
Reality Check — Hallucination Detection for AI-Generated Summaries

Two layers:
1. Heuristic pattern matching (zero API cost) — catches obvious fakes
2. AI-powered verification (Haiku) — cross-references specific claims against evidence snippets

Returns a hallucination risk level: "low", "medium", "high"
and a list of specific flags explaining what was detected.
"""

import re
import json
import os
import time

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


# --- AI-POWERED VERIFICATION (Layer 2) ---
# Two-phase verification:
#   Phase 1 (Contradiction Check): Haiku checks if summary CONTRADICTS evidence snippets
#           (e.g. snippet says "revenue €50M" but summary says "revenue €1M" → hallucination).
#           Missing data is NOT a flag — Opus may have sourced it from full page content.
#   Phase 2 (Fresh Search): Gemini + Google Search grounding independently verifies the summary
#           against live web data, catching fabricated claims that have no basis anywhere.

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


def _contradiction_check(summary_text, evidence_links, company_name, api_key):
    """
    Phase 1: Check if the summary CONTRADICTS any evidence snippet.
    Only flags conflicting data (e.g. different numbers for the same metric).
    Does NOT flag claims that are absent from snippets — that just means
    Opus sourced them from full page content the snippets didn't capture.
    """
    evidence_text = ""
    verified_links = [l for l in evidence_links if l.get("verified", True)]
    for i, link in enumerate(verified_links[:20], 1):
        title = link.get("title", "Unknown")
        snippet = link.get("snippet", "")
        source = link.get("source", "")
        evidence_text += f"[{i}] {title} ({source}): {snippet}\n"

    if not evidence_text.strip():
        return {
            "contradictions": [],
            "model": "skipped_no_evidence",
            "time_ms": 0,
        }

    prompt = f"""You are a contradiction detector. Your ONLY job is to find places where the SUMMARY says something that DIRECTLY CONFLICTS with the EVIDENCE SNIPPETS.

COMPANY: {company_name}

SUMMARY:
{summary_text}

EVIDENCE SNIPPETS:
{evidence_text}

RULES — READ CAREFULLY:
1. A CONTRADICTION is when the summary states a specific fact (number, date, name, percentage) and an evidence snippet states a DIFFERENT value for THE SAME thing.
   Example: Summary says "revenue €2.1B" but snippet says "revenue €1.5B" → CONTRADICTION.
   Example: Summary says "CEO John Smith" but snippet says "CEO Jane Doe" → CONTRADICTION.

2. If the summary mentions something that NO snippet covers at all, that is NOT a contradiction. The original AI had access to full web pages, not just snippets. Silence is not conflict.
   Example: Summary says "revenue €2.1B" and no snippet mentions revenue → NOT a contradiction, SKIP IT.

3. Do NOT flag qualitative statements, opinions, interpretations, or synthesis.
4. Do NOT flag the company name or general industry knowledge.
5. Only flag clear, specific data conflicts where both summary AND evidence mention the same topic with different facts.

Output valid JSON only:
{{"contradictions": ["Summary says X but evidence says Y", ...], "has_contradictions": true/false}}

If there are NO contradictions, return:
{{"contradictions": [], "has_contradictions": false}}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        start = time.time()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        elapsed_ms = int((time.time() - start) * 1000)

        response_text = response.content[0].text if response.content else ""

        json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            contradictions = result.get("contradictions", [])
            return {
                "contradictions": contradictions[:10],
                "model": "claude-haiku-4-5",
                "time_ms": elapsed_ms,
            }
        else:
            print(f"   ⚠️ [Contradiction Check] Could not parse Haiku response: {response_text[:200]}")
            return {"contradictions": [], "model": "haiku_parse_error", "time_ms": elapsed_ms}

    except Exception as e:
        print(f"   ❌ [Contradiction Check] Haiku failed: {e}")
        return {"contradictions": [], "model": f"error: {str(e)[:100]}", "time_ms": 0}


def _fresh_search_verify(summary_text, company_name):
    """
    Phase 2: Send the summary to Gemini with Google Search grounding.
    Gemini searches the web independently and checks if the summary claims are true.
    This catches fabricated claims that have no basis anywhere on the internet.
    """
    import concurrent.futures

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return {
            "fabrications": [],
            "model": "skipped_no_gemini_key",
            "time_ms": 0,
        }

    prompt = f"""You are a fact-checker with access to Google Search. Your job is to verify the following AI-generated company analysis by searching the web.

COMPANY: {company_name}

AI-GENERATED SUMMARY TO VERIFY:
{summary_text}

INSTRUCTIONS:
1. Search for the key factual claims in this summary: revenue figures, employee counts, partnerships, product names, executive names, specific events, dates, percentages.
2. For each specific claim, determine if it is TRUE, FALSE, or UNVERIFIABLE based on your search results.
3. ONLY flag claims that are clearly FALSE — where your search results show different facts.
4. Do NOT flag claims you simply cannot find — the company may be small or private. Absence of evidence is not evidence of fabrication.
5. Do NOT flag qualitative statements, opinions, or general industry observations.

Output ONLY valid JSON:
{{"fabrications": ["Claim X is false because web shows Y instead", ...], "summary_accurate": true/false}}

If the summary appears accurate or you found no clear falsehoods, return:
{{"fabrications": [], "summary_accurate": true}}"""

    try:
        from google import genai

        client = genai.Client(api_key=gemini_key)
        start = time.time()

        def _call():
            return client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    'tools': [{'google_search': {}}],
                    'temperature': 0.0,
                    'safety_settings': [
                        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
                        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
                    ]
                }
            )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_call)
        try:
            response = future.result(timeout=30)
        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            elapsed_ms = int((time.time() - start) * 1000)
            print(f"   ⏰ [Fresh Search] Gemini timed out after 30s ({elapsed_ms}ms)")
            return {"fabrications": [], "model": "gemini_timeout", "time_ms": elapsed_ms}
        pool.shutdown(wait=False)

        elapsed_ms = int((time.time() - start) * 1000)
        text = response.text if response.text else ""

        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            fabrications = result.get("fabrications", [])
            return {
                "fabrications": fabrications[:10],
                "model": "gemini-2.5-flash",
                "time_ms": elapsed_ms,
            }
        else:
            print(f"   ⚠️ [Fresh Search] Could not parse Gemini response: {text[:200]}")
            return {"fabrications": [], "model": "gemini_parse_error", "time_ms": elapsed_ms}

    except Exception as e:
        print(f"   ❌ [Fresh Search] Gemini verification failed: {e}")
        return {"fabrications": [], "model": f"error: {str(e)[:100]}", "time_ms": 0}


def ai_verify_summary(summary_text, evidence_links, company_name, anthropic_key=None):
    """
    AI-powered hallucination check — two phases:

    Phase 1 (Contradiction Check — Haiku):
        Compares summary against stored evidence snippets.
        Only flags CONTRADICTIONS (summary says X, evidence says Y).
        Does NOT flag missing data (silence ≠ hallucination).

    Phase 2 (Fresh Search — Gemini + Google Search):
        Independently verifies summary claims against live web data.
        Catches fabricated facts that contradict reality.

    A company is marked "unverified" ONLY if contradictions or fabrications are found.

    Returns:
        dict with:
            - verified: bool
            - unsupported_claims: list of contradictions + fabrications
            - verification_model: str
            - verification_time_ms: int
    """
    if not summary_text:
        return {
            "verified": True,
            "unsupported_claims": [],
            "verification_model": "skipped",
            "verification_time_ms": 0,
        }

    api_key = _get_anthropic_key(anthropic_key)
    total_start = time.time()
    all_issues = []
    models_used = []

    # --- Phase 1: Contradiction check (Haiku vs stored snippets) ---
    if evidence_links and api_key:
        print(f"   🔍 [Phase 1] Contradiction check against {len(evidence_links)} evidence snippets...")
        contradiction_result = _contradiction_check(summary_text, evidence_links, company_name, api_key)
        models_used.append(f"contradiction:{contradiction_result['model']}")

        if contradiction_result["contradictions"]:
            print(f"   ⚠️ [Phase 1] {len(contradiction_result['contradictions'])} contradiction(s) found")
            for c in contradiction_result["contradictions"]:
                all_issues.append(f"[Contradiction] {c}")
                print(f"      - {c}")
        else:
            print(f"   ✅ [Phase 1] No contradictions ({contradiction_result['time_ms']}ms)")

    # --- Phase 2: Fresh search verification (Gemini + Google Search) ---
    print(f"   🌐 [Phase 2] Fresh web search verification...")
    search_result = _fresh_search_verify(summary_text, company_name)
    models_used.append(f"search:{search_result['model']}")

    if search_result["fabrications"]:
        print(f"   ⚠️ [Phase 2] {len(search_result['fabrications'])} fabrication(s) found")
        for f in search_result["fabrications"]:
            all_issues.append(f"[Fabrication] {f}")
            print(f"      - {f}")
    else:
        print(f"   ✅ [Phase 2] Summary checks out ({search_result['time_ms']}ms)")

    total_ms = int((time.time() - total_start) * 1000)

    return {
        "verified": len(all_issues) == 0,
        "unsupported_claims": all_issues[:10],
        "verification_model": " + ".join(models_used),
        "verification_time_ms": total_ms,
    }


# --- SUMMARY CLEANING (Layer 3) ---
# When AI verification finds false claims, rewrite the summary without them
# instead of requiring a full (expensive) rescan.
#
# Two modes:
#   - In-pipeline (use_gemini=False): Haiku rewrites during normal scan (fast, cheap)
#   - Admin recheck (use_gemini=True): Gemini 3 Pro rewrites standalone (higher quality)

def _build_clean_prompt(summary_text, pitch_text, unsupported_claims, company_name):
    """Build the shared cleaning prompt for both Haiku and Gemini."""
    claims_text = "\n".join(f"- {claim}" for claim in unsupported_claims)

    return f"""You are a precision editor. Your job is to rewrite an AI-generated company summary to REMOVE specific false claims that were identified by a fact-checking system.

COMPANY: {company_name}

ORIGINAL SUMMARY (180-220 words):
{summary_text}

ORIGINAL PITCH (90-110 words):
{pitch_text or "N/A"}

FALSE CLAIMS TO REMOVE:
{claims_text}

RULES — READ CAREFULLY:
1. Remove ONLY the sentences or phrases that contain the false claims listed above.
2. Do NOT add any new information or claims. You are only REMOVING, never ADDING.
3. Keep all other content EXACTLY as-is — same tone, same structure, same archetype classification.
4. Smooth out the text so it reads naturally after removal (fix transitions, but don't add content).
5. The cleaned summary should be 150-220 words. If removing false claims makes it too short, that's fine — a shorter honest summary is better than a padded one.
6. The cleaned pitch should be 70-110 words. Same rules: only remove, don't add.
7. If the pitch references any of the false claims, remove those parts too.
8. Preserve the company name, archetype label, and any KB terminology that isn't part of a false claim.

Output ONLY valid JSON:
{{"cleaned_summary": "...", "cleaned_pitch": "...", "claims_removed": <number of false claims you addressed>}}"""


def _clean_with_haiku(prompt, api_key):
    """Rewrite summary using Haiku (fast, cheap — used as fallback)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    start = time.time()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed_ms = int((time.time() - start) * 1000)
    response_text = response.content[0].text if response.content else ""
    return response_text, elapsed_ms, "claude-haiku-4-5"


def _clean_with_opus(prompt, api_key):
    """Rewrite summary using Opus (highest quality — used in scan pipeline).
    Opus created the summary, so it's the best model to fix its own mistakes."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    start = time.time()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    elapsed_ms = int((time.time() - start) * 1000)
    response_text = response.content[0].text if response.content else ""
    return response_text, elapsed_ms, "claude-opus-4-6"


def _clean_with_gemini(prompt):
    """Rewrite summary using Gemini 3 Pro (higher quality — used in admin recheck)."""
    import concurrent.futures
    from google import genai

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        raise ValueError("No GEMINI_API_KEY configured")

    client = genai.Client(api_key=gemini_key)
    start = time.time()

    def _call():
        return client.models.generate_content(
            model="gemini-3-pro",
            contents=prompt,
            config={
                'temperature': 0.3,
                'max_output_tokens': 1024,
            }
        )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        response = future.result(timeout=30)
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        raise ValueError("Gemini 3 Pro timed out after 30s")
    pool.shutdown(wait=False)

    elapsed_ms = int((time.time() - start) * 1000)
    response_text = response.text if response.text else ""
    return response_text, elapsed_ms, "gemini-3-pro"


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
    Rewrites a summary and pitch to remove specific false claims found by AI verification.

    Three modes (pick one):
      - use_opus=True: Opus rewrites — used in scan pipeline (Opus fixes its own mistakes)
      - use_gemini=True: Gemini 3 Pro rewrites — used in admin recheck (high quality standalone)
      - Both False: Haiku rewrites — cheap fallback

    Args:
        summary_text: The current summary containing false claims
        pitch_text: The current pitch (may reference false claims too)
        unsupported_claims: List of "[Contradiction] ..." / "[Fabrication] ..." strings
        company_name: The target company name
        anthropic_key: Optional Anthropic API key (for Opus/Haiku mode)
        use_gemini: If True, use Gemini 3 Pro for rewriting
        use_opus: If True, use Opus for rewriting (highest quality, Opus fixes its own work)

    Returns:
        dict with:
            - cleaned_summary: str (rewritten summary without false claims)
            - cleaned_pitch: str (rewritten pitch without false claims)
            - claims_removed: int (number of false claims addressed)
            - model: str
            - time_ms: int
            - success: bool
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
        if use_opus:
            # In-pipeline mode: Opus fixes its own summary (highest quality)
            api_key = _get_anthropic_key(anthropic_key)
            if not api_key:
                return {
                    "cleaned_summary": summary_text,
                    "cleaned_pitch": pitch_text,
                    "claims_removed": 0,
                    "model": "skipped_no_api_key",
                    "time_ms": 0,
                    "success": False,
                }
            print(f"   🔄 [Clean] Using Opus to rewrite summary (fixing its own mistakes)...")
            response_text, elapsed_ms, model_name = _clean_with_opus(prompt, api_key)
        elif use_gemini:
            # Admin recheck mode: Gemini 3 Pro for high-quality standalone rewriting
            print(f"   🔄 [Clean] Using Gemini 3 Pro for summary rewrite...")
            response_text, elapsed_ms, model_name = _clean_with_gemini(prompt)
        else:
            # Fallback: Haiku for fast, cheap rewriting
            api_key = _get_anthropic_key(anthropic_key)
            if not api_key:
                return {
                    "cleaned_summary": summary_text,
                    "cleaned_pitch": pitch_text,
                    "claims_removed": 0,
                    "model": "skipped_no_api_key",
                    "time_ms": 0,
                    "success": False,
                }
            print(f"   🔄 [Clean] Using Haiku for summary rewrite...")
            response_text, elapsed_ms, model_name = _clean_with_haiku(prompt, api_key)

        # Parse JSON from response
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return _validate_clean_result(result, summary_text, pitch_text, model_name, elapsed_ms)
        else:
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

def purge_false_evidence(data, unsupported_claims):
    """
    Removes evidence_links and reality_checklist items that match false claims.

    When AI verification finds contradictions/fabrications, the underlying evidence
    that contributed to those false claims must be removed from the stored data.
    Otherwise, the next scan's 'previous scan context' will include the false data
    and potentially regenerate the same bad summary.

    Args:
        data: The full company data dict (modified in-place)
        unsupported_claims: List of "[Contradiction] ..." / "[Fabrication] ..." strings

    Returns:
        dict with:
            - evidence_removed: int (number of evidence links removed)
            - checklist_removed: int (number of checklist items removed)
            - purged_items: list of removed items (for audit trail)
    """
    if not unsupported_claims:
        return {"evidence_removed": 0, "checklist_removed": 0, "purged_items": []}

    # Extract key terms from false claims for fuzzy matching
    # e.g. "[Contradiction] Summary says revenue €2.1B but evidence says €1.5B"
    #   → extract: "revenue", "€2.1B", "€1.5B"
    claim_keywords = set()
    for claim in unsupported_claims:
        # Strip the [Contradiction] / [Fabrication] prefix
        clean_claim = re.sub(r'^\[(Contradiction|Fabrication)\]\s*', '', claim)
        # Extract significant words (numbers, proper nouns, specific terms)
        words = re.findall(r'[€$£]?\d[\d.,]*[BMKbmk]?|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b\w{5,}\b', clean_claim)
        for w in words:
            claim_keywords.add(w.lower())

    if not claim_keywords:
        return {"evidence_removed": 0, "checklist_removed": 0, "purged_items": []}

    purged_items = []

    # --- Purge evidence_links ---
    evidence_links = data.get("evidence_links", [])
    if evidence_links:
        clean_links = []
        for link in evidence_links:
            snippet = (link.get("snippet", "") or "").lower()
            title = (link.get("title", "") or "").lower()
            combined = f"{snippet} {title}"

            # Check if this evidence link matches any false claim keywords
            match_count = sum(1 for kw in claim_keywords if kw in combined)
            if match_count >= 2:
                # Multiple keyword matches — this evidence likely contributed to the false claim
                purged_items.append({
                    "type": "evidence_link",
                    "title": link.get("title", ""),
                    "reason": f"Matched {match_count} false-claim keywords",
                })
                print(f"   🗑️ [Purge] Removing evidence: '{link.get('title', '')[:60]}' (matched {match_count} keywords)")
            else:
                clean_links.append(link)

        evidence_removed = len(evidence_links) - len(clean_links)
        data["evidence_links"] = clean_links
    else:
        evidence_removed = 0

    # --- Purge reality_checklist items ---
    checklist = data.get("reality_checklist", [])
    if checklist:
        clean_checklist = []
        for item in checklist:
            item_text = ""
            if isinstance(item, dict):
                item_text = (item.get("finding", "") or item.get("text", "") or "").lower()
            elif isinstance(item, str):
                item_text = item.lower()

            match_count = sum(1 for kw in claim_keywords if kw in item_text)
            if match_count >= 2:
                purged_items.append({
                    "type": "checklist_item",
                    "text": item_text[:100],
                    "reason": f"Matched {match_count} false-claim keywords",
                })
                print(f"   🗑️ [Purge] Removing checklist item: '{item_text[:60]}' (matched {match_count} keywords)")
            else:
                clean_checklist.append(item)

        checklist_removed = len(checklist) - len(clean_checklist)
        data["reality_checklist"] = clean_checklist
    else:
        checklist_removed = 0

    total = evidence_removed + checklist_removed
    if total > 0:
        print(f"   ✅ [Purge] Removed {evidence_removed} evidence links + {checklist_removed} checklist items")
    else:
        print(f"   ℹ️ [Purge] No matching evidence found to remove (false claims may have been hallucinated without evidence basis)")

    return {
        "evidence_removed": evidence_removed,
        "checklist_removed": checklist_removed,
        "purged_items": purged_items,
    }
