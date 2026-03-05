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
import time

from gemini_backoff import call_with_503_backoff


def _build_person_prompt(person_name: str, person_role: str, company_name: str) -> str:
    """Build a focused search prompt for one stakeholder."""
    return f"""You are a research assistant. Your ONLY task is to find publicly available LinkedIn information about this person.

PERSON: {person_name}
ROLE: {person_role}
COMPANY: {company_name}

Search for this person on LinkedIn using Google. Look for:
1. Their LinkedIn profile headline and summary (if Google has indexed it)
2. Any LinkedIn posts or articles they have published
3. Their career history / previous roles visible in search snippets
4. Topics they publicly discuss or advocate for (digital transformation, technology, leadership, etc.)

### CRITICAL RULES — READ CAREFULLY
- ONLY report information you actually find in search results.
- If you find NOTHING about this person on LinkedIn, respond with the exact JSON below — do NOT make anything up.
- NEVER fabricate LinkedIn posts, quotes, career history, or profile descriptions.
- NEVER guess what someone "might" have shared or "likely" posted.
- NEVER invent connections, endorsements, skills, or follower counts.
- If search results are ambiguous (could be a different person with the same name), say so explicitly and do NOT attribute those results to this person.
- Partial information is fine — report only what you actually found.

### OUTPUT FORMAT
Respond with valid JSON only:
{{
  "found": true/false,
  "headline": "Their LinkedIn headline if found, or null",
  "summary": "A 1-3 sentence synthesis of what you found about this person's public LinkedIn presence. What do they focus on? What have they shared? If nothing found, say exactly: 'No public LinkedIn insights found for this person.'",
  "career_moves": ["Previous Role at Previous Company", ...] or [] if not found,
  "topics": ["topic1", "topic2", ...] or [] if not found,
  "notable_posts": ["Brief description of a real post/article if found"] or [] if not found
}}

If nothing was found, respond with:
{{"found": false, "headline": null, "summary": "No public LinkedIn insights found for this person.", "career_moves": [], "topics": [], "notable_posts": []}}"""


def enrich_single_person(person: dict, company_name: str, gemini_key: str) -> dict:
    """
    Enrich a single key_person dict with LinkedIn insights.
    Returns the person dict with an added 'linkedin_insights' field.
    """
    from google import genai
    from google.genai import types

    name = person.get("name", "")
    role = person.get("role", "")
    if not name:
        person["linkedin_insights"] = {
            "found": False,
            "summary": "No name provided — cannot search.",
        }
        return person

    prompt = _build_person_prompt(name, role, company_name)

    _safety = [
        {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_HATE_SPEECH', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'threshold': 'BLOCK_NONE'},
        {'category': 'HARM_CATEGORY_DANGEROUS_CONTENT', 'threshold': 'BLOCK_NONE'}
    ]

    response_text = ""
    model_used = ""

    # Primary: Gemini 3.1 Pro with Google Search grounding
    try:
        print(f"      🔍 [Stakeholder Intel] Searching for {name} ({role}) via gemini-3.1-pro-preview...")
        client = genai.Client(api_key=gemini_key)
        resp = call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[{'google_search': {}}],
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                    max_output_tokens=2000,
                    safety_settings=_safety,
                )
            ),
            label=f"Stakeholder Intel {name} gemini-3.1-pro-preview",
            model="gemini-3.1-pro-preview",
            timeout=45,
        )
        response_text = resp.text or ""
        model_used = "gemini-3.1-pro-preview"
    except Exception as g31_err:
        print(f"      ❌ [Stakeholder Intel] Gemini 3.1 Pro failed for {name}: {g31_err}")

    # Fallback: Gemini 2.5 Pro with Google Search
    if not response_text:
        try:
            print(f"      🔄 [Stakeholder Intel] Falling back to gemini-2.5-pro for {name}...")
            client = genai.Client(api_key=gemini_key)
            resp = call_with_503_backoff(
                lambda: client.models.generate_content(
                    model="gemini-2.5-pro",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        max_output_tokens=2000,
                        safety_settings=_safety,
                    )
                ),
                label=f"Stakeholder Intel {name} gemini-2.5-pro",
                model="gemini-2.5-pro",
                timeout=45,
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

    # Try to extract JSON
    try:
        # Handle markdown code blocks
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        parsed = json.loads(text)
        insights = {
            "found": parsed.get("found", False),
            "headline": parsed.get("headline"),
            "summary": parsed.get("summary", "No public LinkedIn insights found for this person."),
            "career_moves": parsed.get("career_moves", []),
            "topics": parsed.get("topics", []),
            "notable_posts": parsed.get("notable_posts", []),
            "model_used": model_used,
        }

        # Safety net: if found=true but summary is empty/generic, don't claim found
        if insights["found"] and not insights["summary"]:
            insights["found"] = False
            insights["summary"] = "No public LinkedIn insights found for this person."

        person["linkedin_insights"] = insights
        print(f"      {'✅' if insights['found'] else '⚪'} [Stakeholder Intel] {name}: {'Found insights' if insights['found'] else 'No public insights found'} ({model_used})")

    except (json.JSONDecodeError, KeyError) as e:
        print(f"      ⚠️ [Stakeholder Intel] Could not parse response for {name}: {e}")
        person["linkedin_insights"] = {
            "found": False,
            "summary": "Search completed but response could not be parsed.",
            "model_used": model_used,
        }

    return person


def enrich_key_people(company_name: str, key_people: list, gemini_key: str = None) -> list:
    """
    Enrich all key_people with LinkedIn insights.

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
    print(f"   🔍 [Stakeholder Intel] Enriching {len(key_people)} stakeholder(s) for {company_name}...")

    for person in key_people:
        enrich_single_person(person, company_name, gemini_key)

    ms = int((time.time() - start) * 1000)
    found_count = sum(1 for p in key_people if p.get("linkedin_insights", {}).get("found"))
    print(f"   ✅ [Stakeholder Intel] Complete for {company_name}: {found_count}/{len(key_people)} stakeholders with public insights ({ms}ms)")

    return key_people
