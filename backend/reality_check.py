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
# Uses Haiku to cross-reference specific claims in the summary against evidence snippets.
# Catches fabricated numbers, names, dates, and claims that appear in no snippet.

def ai_verify_summary(summary_text, evidence_links, company_name, anthropic_key=None):
    """
    AI-powered hallucination check: sends summary + evidence snippets to Haiku
    and asks it to identify specific claims NOT supported by any evidence.

    Args:
        summary_text: The AI-generated summary
        evidence_links: List of evidence_link dicts (with title, snippet, url)
        company_name: Target company name
        anthropic_key: Anthropic API key (if None, tries env var)

    Returns:
        dict with:
            - verified: bool (True if no unsupported claims found)
            - unsupported_claims: list of specific unsupported claims
            - verification_model: str (model used)
            - verification_time_ms: int
    """
    if not summary_text or not evidence_links:
        return {
            "verified": True,
            "unsupported_claims": [],
            "verification_model": "skipped",
            "verification_time_ms": 0,
        }

    api_key = anthropic_key
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key and "=" in api_key:
            try: api_key = api_key.split("=", 1)[1]
            except: pass
        api_key = (api_key or "").strip().strip('"').strip("'")

    if not api_key:
        return {
            "verified": True,
            "unsupported_claims": [],
            "verification_model": "skipped_no_key",
            "verification_time_ms": 0,
        }

    # Build evidence context from snippets
    evidence_text = ""
    verified_links = [l for l in evidence_links if l.get("verified", True)]
    for i, link in enumerate(verified_links[:20], 1):
        title = link.get("title", "Unknown")
        snippet = link.get("snippet", "")
        source = link.get("source", "")
        evidence_text += f"[{i}] {title} ({source}): {snippet}\n"

    if not evidence_text.strip():
        return {
            "verified": True,
            "unsupported_claims": [],
            "verification_model": "skipped_no_evidence",
            "verification_time_ms": 0,
        }

    prompt = f"""You are a fact-checker. Your job is to find specific claims in the SUMMARY that are NOT supported by ANY of the EVIDENCE SNIPPETS.

COMPANY: {company_name}

SUMMARY:
{summary_text}

EVIDENCE SNIPPETS:
{evidence_text}

INSTRUCTIONS:
- Focus ONLY on specific, verifiable claims: numbers, percentages, revenue figures, dates, named partnerships, named people, specific product names, specific events.
- Do NOT flag general qualitative statements like "the company is modernizing" or "shows strong digital ambition" — these are synthesis/interpretation, not factual claims.
- Do NOT flag the company name or common knowledge about the company.
- A claim is "unsupported" ONLY if it contains a specific data point (number, name, date) that appears in ZERO snippets.
- Be strict: if a snippet mentions the same topic but with different numbers, that IS a flag.

Output valid JSON only:
{{"unsupported_claims": ["claim 1 text", "claim 2 text"], "all_claims_supported": true/false}}

If ALL specific claims trace back to at least one snippet, return:
{{"unsupported_claims": [], "all_claims_supported": true}}"""

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

        # Parse JSON from response
        json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            claims = result.get("unsupported_claims", [])
            all_ok = result.get("all_claims_supported", len(claims) == 0)
            return {
                "verified": all_ok and len(claims) == 0,
                "unsupported_claims": claims[:10],  # Cap at 10
                "verification_model": "claude-haiku-4-5",
                "verification_time_ms": elapsed_ms,
            }
        else:
            print(f"   ⚠️ [AI Verify] Could not parse Haiku response: {response_text[:200]}")
            return {
                "verified": True,
                "unsupported_claims": [],
                "verification_model": "haiku_parse_error",
                "verification_time_ms": elapsed_ms,
            }

    except Exception as e:
        print(f"   ❌ [AI Verify] Haiku verification failed: {e}")
        return {
            "verified": True,
            "unsupported_claims": [],
            "verification_model": f"error: {str(e)[:100]}",
            "verification_time_ms": 0,
        }
