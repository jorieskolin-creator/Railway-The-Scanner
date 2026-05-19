"""
stakeholder_intel.py — Enrich key_people with publicly available LinkedIn insights.

Uses Gemini with Google Search grounding to find what key stakeholders have
publicly shared on LinkedIn (profile summaries, posts, articles, career history).

CRITICAL GUARDRAILS:
  - Uses ONLY Google-indexed public content — no LinkedIn API or scraping.
  - If nothing is found for a person, returns "No public LinkedIn insights found."
  - NEVER fabricates insights, quotes, posts, or career history.
  - Each person is searched independently with a dedicated prompt.
"""

import json
import os
import re
import time

from gemini_backoff import call_with_503_backoff


def _extract_json(text: str) -> dict | None:
    """
    Robustly extract a JSON object from Gemini's response.

    Handles:
      - Pure JSON response
      - JSON inside ```json ... ``` code fences
      - JSON inside ``` ... ``` code fences (no language tag)
      - JSON embedded in prose (before/after text)
      - Thinking tokens mixed with output
    """
    if not text:
        return None

    text = text.strip()

    # 1. Try direct parse first (fastest path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract from markdown code fences: ```json\n{...}\n``` or ```\n{...}\n```
    fence_match = re.search(r'```(?:json)?\s*\n?\s*(\{.*?\})\s*\n?\s*```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Find the first { and last } — extract the JSON object from surrounding prose
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 4. Try progressively smaller substrings (nested braces)
        # Find all { positions and try each with the last }
        for i in range(len(candidate)):
            if candidate[i] == '{':
                try:
                    return json.loads(candidate[i:])
                except json.JSONDecodeError:
                    continue

    return None


def _build_batch_prompt(people: list[dict], company_name: str) -> str:
    """Build a single prompt to search for ALL stakeholders in one API call."""
    people_block = ""
    for i, p in enumerate(people):
        name = p.get("name", "Unknown")
        role = p.get("role", "Unknown")
        people_block += f"\n  Person {i+1}: {name} — Role (may be outdated): {role}"

    return f"""Search Google for publicly available professional information about ALL of these people at {company_name}.
{people_block}

For EACH person, search for:
1. "<person_name>" "{company_name}" LinkedIn
2. "<person_name>" "{company_name}" — press releases, news, interviews
3. "<person_name>" "<role>" "{company_name}"

### IDENTITY VERIFICATION — THIS IS THE MOST IMPORTANT RULE
You MUST verify that BOTH the person's name AND their connection to "{company_name}" match before reporting ANY finding.

- A LinkedIn profile that does NOT mention "{company_name}" → REJECT (could be a different person)
- A news article at a DIFFERENT company → REJECT (wrong person)
- Common names are especially risky — require STRICT company match
- When in doubt, set "found" to false. A false negative is always better than a wrong-person attribution.

### ROLE / POSITION VERIFICATION — CRITICAL
The roles we have on file may be OUTDATED. You MUST check for their CURRENT role:
- On LinkedIn: the CURRENT position is shown at the top of Experience with no end date.
- If their current title differs from our records, report the CURRENT title in "verified_role".
- If the person has LEFT {company_name}, set "still_at_company" to false and report where they moved.
- Do NOT assume the role we provided is correct — always verify.

### WHAT TO REPORT (only if identity is verified)
- Their CURRENT role/title at {company_name}
- LinkedIn profile headline, summary
- Career history (previous roles in "career_moves", CURRENT role in "verified_role")
- Press releases, news articles, or interviews mentioning them at {company_name}
- Conference talks, blog posts, or public speaking
- Topics they publicly discuss

### OTHER RULES
- ONLY report information you actually find in search results.
- If you find NOTHING for a person, set "found" to false for that person.
- NEVER fabricate quotes, posts, career history, or descriptions.
- Partial information is fine — report only what you actually found AND verified.

### OUTPUT FORMAT
Respond with ONLY a JSON object (no other text, no markdown, no explanation).
The JSON must have a "results" array with one entry per person, IN THE SAME ORDER as listed above:

{{
  "results": [
    {{
      "person_index": 1,
      "name": "<name>",
      "found": true,
      "headline": "Their current professional headline or null",
      "verified_role": "Their CURRENT role at {company_name} or null",
      "still_at_company": true,
      "summary": "1-3 sentence synthesis of findings.",
      "career_moves": ["Previous Role at Previous Company (dates if available)"],
      "topics": ["topic1", "topic2"],
      "notable_posts": ["Brief description of a real post/article/talk"]
    }},
    {{
      "person_index": 2,
      "name": "<name>",
      "found": false,
      "headline": null,
      "verified_role": null,
      "still_at_company": null,
      "summary": "No public professional insights found for this person.",
      "career_moves": [],
      "topics": [],
      "notable_posts": []
    }}
  ]
}}"""


def _build_person_prompt(person_name: str, person_role: str, company_name: str) -> str:
    """Build a focused search prompt for one stakeholder."""
    return f"""Search Google for publicly available professional information about this specific person.

TARGET PERSON:
  Name: {person_name}
  Role (from our records — may be outdated): {person_role}
  Company: {company_name}

Search for ALL of these (try each one):
1. "{person_name}" "{company_name}" LinkedIn
2. "{person_name}" "{company_name}" — press releases, news, interviews
3. "{person_name}" "{person_role}" "{company_name}"

### IDENTITY VERIFICATION — THIS IS THE MOST IMPORTANT RULE
You MUST verify that BOTH the person's name AND their connection to "{company_name}" match before reporting ANY finding.

- A LinkedIn profile for "{person_name}" that does NOT mention "{company_name}" → REJECT (could be a different person with the same name)
- A news article mentioning "{person_name}" at a DIFFERENT company → REJECT (wrong person)
- A conference speaker named "{person_name}" with no link to "{company_name}" → REJECT
- ONLY include findings where the source explicitly connects "{person_name}" to "{company_name}"
- Common names (e.g. "Tero Virtanen", "Manish Kumar") are especially risky — require STRICT company match
- When in doubt, set "found" to false. A false negative is always better than a wrong-person attribution.

### ROLE / POSITION VERIFICATION — CRITICAL
The role we have on file ("{person_role}") may be OUTDATED. You MUST check for their CURRENT role:

- On LinkedIn: the CURRENT position is the one shown at the top of the Experience section, with no end date.
  Older positions have an end date or are listed further down — those are PAST roles.
- If their current LinkedIn title differs from "{person_role}", report the CURRENT title in "verified_role".
- If the person has LEFT {company_name} entirely (their current position is at a different company),
  set "still_at_company" to false and report where they moved.
- Only report what LinkedIn or other sources explicitly show as the CURRENT position.
- Do NOT assume the role we provided is correct — always verify.

### WHAT TO REPORT (only if identity is verified)
- Their CURRENT role/title at {company_name} (not historical)
- LinkedIn profile headline, summary
- Career history (previous roles are reported in "career_moves", CURRENT role in "verified_role")
- Press releases, news articles, or interviews mentioning them at {company_name}
- Conference talks, blog posts, or public speaking where they represent {company_name}
- Topics they publicly discuss in connection with their role at {company_name}

### OTHER RULES
- ONLY report information you actually find in search results.
- If you find NOTHING that passes identity verification, set "found" to false.
- NEVER fabricate quotes, posts, career history, or descriptions.
- NEVER guess what someone "might" have shared or "likely" posted.
- Partial information is fine — report only what you actually found AND verified.

### OUTPUT FORMAT
Respond with ONLY a JSON object (no other text, no markdown, no explanation):
{{
  "found": true,
  "headline": "Their current professional headline or title if found, or null",
  "verified_role": "Their CURRENT role/title at {company_name} as shown on LinkedIn, or null if not found",
  "still_at_company": true,
  "summary": "A 1-3 sentence synthesis of what you found about this person at {company_name}.",
  "career_moves": ["Previous Role at Previous Company (dates if available)"],
  "topics": ["topic1", "topic2"],
  "notable_posts": ["Brief description of a real post, article, talk, or interview if found"]
}}

If NOTHING was found or identity could not be verified, respond with ONLY:
{{"found": false, "headline": null, "verified_role": null, "still_at_company": null, "summary": "No public professional insights found for this person.", "career_moves": [], "topics": [], "notable_posts": []}}"""


def enrich_single_person(person: dict, company_name: str, gemini_key: str) -> dict:
    """
    Enrich a single key_person dict with LinkedIn insights.
    Returns the person dict with an added 'linkedin_insights' field.
    """
    from google.genai import types
    from gemini_client import get_gemini_client

    name = person.get("name", "")
    role = person.get("role", "")
    if not name:
        person["linkedin_insights"] = {
            "found": False,
            "summary": "No name provided — cannot search.",
        }
        return person

    prompt = _build_person_prompt(name, role, company_name)

    response_text = ""
    model_used = ""

    # Primary: Gemini 3.1 Pro with Google Search grounding (better name accuracy)
    if not response_text:
        _safety = [
            {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
        ]
        try:
            print(f"      🔍 [Stakeholder Intel] Searching for {name} ({role}) via gemini-3.1-pro-preview...")
            client = get_gemini_client()
            resp = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        temperature=0.1,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        max_output_tokens=4000,
                        safety_settings=_safety,
                    )
                ),
                label=f"Stakeholder Intel {name} gemini-3.1-pro-preview",
                model="gemini-3.1-pro-preview",
                timeout=60,
            )
            response_text = resp.text or ""
            model_used = "gemini-3.1-pro-preview"
        except Exception as g31_err:
            print(f"      ❌ [Stakeholder Intel] Gemini 3.1 Pro failed for {name}: {g31_err}")

    # Fallback: Gemini 2.5 Pro with Google Search
    if not response_text:
        try:
            print(f"      🔄 [Stakeholder Intel] Falling back to gemini-2.5-pro for {name}...")
            client = get_gemini_client()
            resp = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        max_output_tokens=4000,
                        safety_settings=_safety,
                    )
                ),
                label=f"Stakeholder Intel {name} gemini-2.5-pro",
                model="gemini-2.5-pro",
                timeout=60,
            )
            response_text = resp.text or ""
            model_used = "gemini-2.5-pro"
        except Exception as g25_err:
            print(f"      ❌ [Stakeholder Intel] All models failed for {name}: {g25_err}")

    # Parse response
    if not response_text:
        person["linkedin_insights"] = {
            "found": False,
            "summary": "Search failed — no model available.",
            "model_used": None,
        }
        return person

    # Robust JSON extraction
    parsed = _extract_json(response_text)

    if parsed and isinstance(parsed, dict):
        insights = {
            "found": parsed.get("found", False),
            "headline": parsed.get("headline"),
            "verified_role": parsed.get("verified_role"),
            "still_at_company": parsed.get("still_at_company"),
            "summary": parsed.get("summary", "No public professional insights found for this person."),
            "career_moves": parsed.get("career_moves", []),
            "topics": parsed.get("topics", []),
            "notable_posts": parsed.get("notable_posts", []),
            "model_used": model_used,
        }

        # Safety net: if found=true but summary is empty/generic, don't claim found
        if insights["found"] and not insights["summary"]:
            insights["found"] = False
            insights["summary"] = "No public professional insights found for this person."

        # Update the person's role if a verified current role was found
        verified = insights.get("verified_role")
        if verified and insights["found"] and insights.get("still_at_company") is not False:
            original_role = person.get("role", "")
            if verified.lower() != original_role.lower():
                print(f"      🔄 [Stakeholder Intel] {name}: Role updated: \"{original_role}\" → \"{verified}\"")
                person["role"] = verified

        # Flag if person has left the company
        if insights.get("still_at_company") is False:
            print(f"      ⚠️ [Stakeholder Intel] {name}: No longer at {company_name}")

        person["linkedin_insights"] = insights
        print(f"      {'✅' if insights['found'] else '⚪'} [Stakeholder Intel] {name}: {'Found insights' if insights['found'] else 'No public insights found'} ({model_used})")

    else:
        # Last resort: the response is plain text (no JSON at all).
        # If it contains substantive content about the person, use it as-is.
        trimmed = response_text.strip()
        has_substance = len(trimmed) > 80 and name.split()[0].lower() in trimmed.lower()

        if has_substance:
            # Model returned prose instead of JSON — use it as a summary
            # Truncate to ~500 chars for display
            summary_text = trimmed[:500].rstrip()
            if len(trimmed) > 500:
                summary_text += "…"
            person["linkedin_insights"] = {
                "found": True,
                "headline": None,
                "summary": summary_text,
                "career_moves": [],
                "topics": [],
                "notable_posts": [],
                "model_used": model_used,
            }
            print(f"      ✅ [Stakeholder Intel] {name}: Found insights (plain text fallback, {model_used})")
        else:
            print(f"      ⚠️ [Stakeholder Intel] Could not parse response for {name} ({len(response_text)} chars, model: {model_used})")
            person["linkedin_insights"] = {
                "found": False,
                "summary": "Search completed but response could not be parsed.",
                "model_used": model_used,
            }

    return person


def _enrich_batch(people_with_names: list[dict], company_name: str, gemini_key: str) -> bool:
    """
    Enrich multiple people in a SINGLE Gemini API call (batched).

    Returns True if batch succeeded and all people were enriched,
    False if batch failed (caller should fall back to per-person calls).
    """
    from google.genai import types
    from gemini_client import get_gemini_client

    prompt = _build_batch_prompt(people_with_names, company_name)
    names_str = ", ".join(p.get("name", "?") for p in people_with_names)

    response_text = ""
    model_used = ""

    # Primary: Gemini 3.1 Pro with Google Search grounding (better name accuracy)
    if not response_text:
        _safety = [
            {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
            {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
        ]
        try:
            print(f"      🔍 [Stakeholder Intel] Batched search for {len(people_with_names)} people ({names_str}) via gemini-3.1-pro-preview...")
            client = get_gemini_client()
            resp = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        temperature=0.1,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        max_output_tokens=8000,
                        safety_settings=_safety,
                    )
                ),
                label=f"Stakeholder Intel batch gemini-3.1-pro-preview",
                model="gemini-3.1-pro-preview",
                timeout=90,
            )
            response_text = resp.text or ""
            model_used = "gemini-3.1-pro-preview"
        except Exception as g31_err:
            print(f"      ❌ [Stakeholder Intel] Batch Gemini 3.1 Pro failed: {g31_err}")

    # Fallback: Gemini 2.5 Pro
    if not response_text:
        try:
            print(f"      🔄 [Stakeholder Intel] Batch falling back to gemini-2.5-pro...")
            client = get_gemini_client()
            resp = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        max_output_tokens=8000,
                        safety_settings=_safety,
                    )
                ),
                label=f"Stakeholder Intel batch gemini-2.5-pro",
                model="gemini-2.5-pro",
                timeout=90,
            )
            response_text = resp.text or ""
            model_used = "gemini-2.5-pro"
        except Exception as g25_err:
            print(f"      ❌ [Stakeholder Intel] Batch all models failed: {g25_err}")

    if not response_text:
        return False

    # Parse the batch response
    parsed = _extract_json(response_text)
    if not parsed or not isinstance(parsed, dict):
        print(f"      ⚠️ [Stakeholder Intel] Batch response not valid JSON ({len(response_text)} chars)")
        return False

    results = parsed.get("results")
    if not results or not isinstance(results, list):
        print(f"      ⚠️ [Stakeholder Intel] Batch response missing 'results' array")
        return False

    if len(results) < len(people_with_names):
        print(f"      ⚠️ [Stakeholder Intel] Batch returned {len(results)} results for {len(people_with_names)} people — falling back")
        return False

    # Map results back to people (by index order)
    for i, person in enumerate(people_with_names):
        if i >= len(results):
            break

        r = results[i]
        name = person.get("name", "")

        insights = {
            "found": r.get("found", False),
            "headline": r.get("headline"),
            "verified_role": r.get("verified_role"),
            "still_at_company": r.get("still_at_company"),
            "summary": r.get("summary", "No public professional insights found for this person."),
            "career_moves": r.get("career_moves", []),
            "topics": r.get("topics", []),
            "notable_posts": r.get("notable_posts", []),
            "model_used": f"{model_used} (batched)",
        }

        # Safety net: if found=true but summary is empty/generic, don't claim found
        if insights["found"] and not insights["summary"]:
            insights["found"] = False
            insights["summary"] = "No public professional insights found for this person."

        # Update role if a verified current role was found
        verified = insights.get("verified_role")
        if verified and insights["found"] and insights.get("still_at_company") is not False:
            original_role = person.get("role", "")
            if verified.lower() != original_role.lower():
                print(f"      🔄 [Stakeholder Intel] {name}: Role updated: \"{original_role}\" → \"{verified}\"")
                person["role"] = verified

        # Flag if person has left the company
        if insights.get("still_at_company") is False:
            print(f"      ⚠️ [Stakeholder Intel] {name}: No longer at {company_name}")

        person["linkedin_insights"] = insights
        print(f"      {'✅' if insights['found'] else '⚪'} [Stakeholder Intel] {name}: {'Found insights' if insights['found'] else 'No public insights found'} ({model_used}, batched)")

    return True


def enrich_key_people(company_name: str, key_people: list, gemini_key: str = None) -> list:
    """
    Enrich all key_people with LinkedIn insights.

    Uses a BATCHED approach: all people are searched in a single Gemini API call
    instead of one call per person. Falls back to per-person calls if batch fails.

    Args:
        company_name: The company name for search context
        key_people: List of dicts with name, role, relevance
        gemini_key: Gemini API key (falls back to env vars if None)

    Returns:
        The same list with 'linkedin_insights' added to each person
    """
    if not key_people:
        return key_people

    if not gemini_key:
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")

    if not gemini_key:
        print(f"   ⚠️ [Stakeholder Intel] No Gemini API key — skipping LinkedIn enrichment")
        for person in key_people:
            person["linkedin_insights"] = {
                "found": False,
                "summary": "LinkedIn enrichment unavailable — no API key configured.",
            }
        return key_people

    start = time.time()

    # Split into people with names (searchable) and without (skip)
    _placeholder_names = {"unknown", "unnamed", "n/a", "tbd", "none", "not found",
                          "not available", "ei tiedossa", "tuntematon"}

    def _has_real_name(p):
        name = (p.get("name") or "").strip()
        return name and name.lower() not in _placeholder_names

    people_with_names = [p for p in key_people if _has_real_name(p)]
    people_without_names = [p for p in key_people if not _has_real_name(p)]

    for person in people_without_names:
        person["linkedin_insights"] = {
            "found": False,
            "summary": "No name provided — cannot search.",
        }

    if not people_with_names:
        return key_people

    print(f"   🔍 [Stakeholder Intel] Enriching {len(people_with_names)} stakeholder(s) for {company_name}...")

    # Try batched approach first (1 API call for all people)
    if len(people_with_names) >= 2:
        batch_ok = _enrich_batch(people_with_names, company_name, gemini_key)
        if batch_ok:
            ms = int((time.time() - start) * 1000)
            found_count = sum(1 for p in key_people if p.get("linkedin_insights", {}).get("found"))
            print(f"   ✅ [Stakeholder Intel] Complete for {company_name}: {found_count}/{len(people_with_names)} stakeholders with public insights ({ms}ms, batched — 1 API call)")
            return key_people
        else:
            print(f"   🔄 [Stakeholder Intel] Batch failed — falling back to per-person search")

    # Fallback: per-person calls (also used when only 1 person)
    for person in people_with_names:
        if not person.get("linkedin_insights"):
            enrich_single_person(person, company_name, gemini_key)

    ms = int((time.time() - start) * 1000)
    found_count = sum(1 for p in key_people if p.get("linkedin_insights", {}).get("found"))
    print(f"   ✅ [Stakeholder Intel] Complete for {company_name}: {found_count}/{len(people_with_names)} stakeholders with public insights ({ms}ms)")

    return key_people
