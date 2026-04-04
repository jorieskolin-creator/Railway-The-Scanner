"""
Foresight Observatory — Three-Layer Strategic Analysis Engine
=============================================================
Synthesizes existing Scanner data into macro-level strategic intelligence:
  1. Global Force Landscape — futurologist's briefing on converging forces
  2. Finnish Economy Force Map — how forces reshape Finnish business
  3. Domain Force Dossiers — per-domain analysis with timescale mapping

Model pipeline:
  - Sonnet 4.6: Primary generation (narrative synthesis)
  - Gemini 3.1 Pro: Verification (fact-check, framing compliance, structure)

All data comes from existing DB — no new scanning or web search required.
"""

import json
import os
import re
import time
import statistics
from collections import Counter, defaultdict
from datetime import datetime

import anthropic
from google.genai import types as genai_types

from gemini_client import get_gemini_client
from gemini_backoff import call_with_503_backoff
from storage import BlobStorage
from market_intelligence import (
    compute_market_intelligence, THEME_CATEGORIES, ALL_CATEGORIES, KB_DIMENSIONS,
)
from analyze_scores import run_analysis, classify_archetype


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — Shared across all three outputs
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_ROLE = """You are a synthesis of three expert perspectives: a futurologist who reads weak signals, a market analyst who grounds insight in evidence, and a strategic designer who frames actionable futures.

CRITICAL FRAMING RULE — Technology is context, never conclusion:
- Frame every force through the lens of organizational thinking, adaptability, and future-readiness.
- NEVER say "companies need AI/cloud/digital/technology" — instead describe what thinking capabilities, organizational structures, or strategic postures enable companies to create their own race.
- The ultimate strategic question is not "how do we respond to these forces?" but "how do we create conditions where we define the game rather than react to it?"
- Focus on: thinking and adaptability, future-drivenness, innovative thinking, organizational learning, what companies need to STOP doing, creating "Own Race" vs. participating reactively.

EVIDENCE RULE — Every claim must trace to the data provided. Include specific numbers when available (e.g., "60% of Finnish industrial companies sit in Cash Cow archetype"). Do not invent statistics."""


# ═══════════════════════════════════════════════════════════════════════════════
# DATA AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def _aggregate_kb_signals(all_kb: dict) -> dict:
    """Aggregate all KB signals by category, lifecycle, timeframe, and force type."""
    by_category = defaultdict(list)
    by_timeframe = defaultdict(int)
    by_force_type = defaultdict(int)
    total = 0

    for theme_slug, theme_data in all_kb.items():
        signals = theme_data.get("signals", [])
        for sig in signals:
            fp_theme = sig.get("fp_theme", "")
            category = THEME_CATEGORIES.get(fp_theme, "Unknown")
            by_category[category].append({
                "theme": fp_theme,
                "dimension": sig.get("kb_dimension", ""),
                "title": sig.get("title", ""),
                "description": (sig.get("description", "") or "")[:200],
                "force_type": sig.get("force_type", ""),
                "timeframe": sig.get("timeframe", ""),
                "credibility": sig.get("source_credibility", "medium"),
            })
            by_timeframe[sig.get("timeframe", "unknown")] += 1
            by_force_type[sig.get("force_type", "unknown")] += 1
            total += 1

    # Count accelerating vs decelerating
    accelerating = by_force_type.get("Probable/Emerging", 0) + by_force_type.get("Plausible/Maturing", 0)
    decelerating = by_force_type.get("Weakening/Legacy", 0)

    return {
        "total_signals": total,
        "by_category": dict(by_category),
        "by_timeframe": dict(by_timeframe),
        "by_force_type": dict(by_force_type),
        "accelerating": accelerating,
        "decelerating": decelerating,
        "theme_count": len(all_kb),
    }


def _load_all_data(storage: BlobStorage):
    """Load all data needed for observatory generation."""
    print("   [Observatory] Loading all data...")

    # 1. KB signals
    all_kb = storage.get_all_foresight_kb()
    kb_aggregate = _aggregate_kb_signals(all_kb)
    print(f"   [Observatory] KB: {kb_aggregate['total_signals']} signals across {kb_aggregate['theme_count']} themes")

    # 2. Dashboard + company details
    dashboard_data = storage.retrieve_latest_data("index/summary_list") or []
    company_details = []
    for item in dashboard_data:
        detail = storage.get_company_data(item.get("companyName", ""))
        if detail:
            company_details.append(detail)
    print(f"   [Observatory] Companies: {len(dashboard_data)} summary, {len(company_details)} detailed")

    # 3. Market intelligence
    analysis_result = run_analysis(dashboard_data) if dashboard_data else {}
    mi = compute_market_intelligence(dashboard_data, company_details, analysis_result) if dashboard_data else {}

    return {
        "all_kb": all_kb,
        "kb_aggregate": kb_aggregate,
        "dashboard_data": dashboard_data,
        "company_details": company_details,
        "market_intel": mi,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_global_prompt(kb_aggregate: dict, cross_impact: list, top_foresights: list) -> str:
    """Build prompt for Output 1: Global Force Landscape."""
    # Summarize KB by category
    cat_summary = []
    for cat in ALL_CATEGORIES:
        signals = kb_aggregate["by_category"].get(cat, [])
        if signals:
            themes = set(s["theme"] for s in signals)
            cat_summary.append(f"- {cat}: {len(signals)} signals across themes: {', '.join(sorted(themes))}")

    # Top themes by cross-impact density
    cross_summary = ""
    if cross_impact:
        theme_counts = Counter()
        for cell in cross_impact:
            theme_counts[cell["theme"]] += cell["count"]
        top_cross = theme_counts.most_common(8)
        cross_summary = "\n".join(f"- {t}: {c} cross-dimensional signals" for t, c in top_cross)

    # Top foresights
    foresight_summary = ""
    if top_foresights:
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies affected ({f['pct']}%)"
            for f in top_foresights[:10]
        )

    return f"""{SYSTEM_ROLE}

TASK: Write a Global Force Landscape briefing — a futurologist's synthesis of the forces reshaping business globally.

DATA PROVIDED:

Signal Overview:
- Total validated signals: {kb_aggregate['total_signals']}
- Themes covered: {kb_aggregate['theme_count']} of 30
- Accelerating forces: {kb_aggregate['accelerating']}
- Decelerating forces: {kb_aggregate['decelerating']}

Timeframe distribution:
{json.dumps(kb_aggregate['by_timeframe'], indent=2)}

Force type distribution:
{json.dumps(kb_aggregate['by_force_type'], indent=2)}

Signals by category:
{chr(10).join(cat_summary)}

Cross-impact density (theme × dimension interactions):
{cross_summary}

Top foresight themes affecting companies:
{foresight_summary}

ANALYTICAL STRUCTURE (Popper's Foresight Framework):

Section 1 — ANTICIPATION: What forces are converging? Which weak signals are strengthening? What established patterns are decelerating? Synthesize across categories — don't list themes, weave them into a narrative about interconnected forces.

Section 2 — PARTICIPATION: Where are organizations already adapting? What patterns emerge from how companies engage with these forces? What does the cross-impact data reveal about which organizational dimensions are most under pressure?

Section 3 — FUTURES LITERACY (Three Scenarios):
- Acceleration: Forces converge faster than expected — what happens to organizations that haven't adapted their thinking?
- Drift (Status Quo): Inertia continues — what gradually deteriorates?
- Disruption: Black swan convergence — what combinations create sudden system shifts?

OUTPUT FORMAT:
- 800-1000 words
- Three sections with headers (## Anticipation, ## Participation, ## Futures Literacy)
- Analytical essay prose — NO bullet points
- Written as a futurologist would present to a strategy board
- End with implications for organizational thinking, never technology adoption"""


def _build_finland_prompt(global_analysis: str, mi: dict) -> str:
    """Build prompt for Output 2: Finnish Economy Force Map."""
    # Market health summary
    health_summary = ""
    if mi.get("market_health"):
        health_summary = "\n".join(
            f"- {h['domain']}: health={h['score']}/100, reality={h['avg_reality']}, financial={h['avg_financial']}, gap={h['avg_gap']}, {h['company_count']} companies"
            for h in mi["market_health"]
        )

    # Archetype distribution
    arch_summary = ""
    if mi.get("archetype_flow"):
        arch_summary = "\n".join(
            f"- {a['domain']}: Thriving={a['thriving']}, Modernizing={a['modernizing']}, Cash Cow={a['cash_cow']}, At Risk={a['at_risk']} (total={a['total']})"
            for a in mi["archetype_flow"]
        )

    # FAVI segments
    favi_summary = json.dumps(mi.get("favi_segments", {}), indent=2)

    # Gap patterns
    gap_summary = ""
    if mi.get("gap_patterns"):
        gap_summary = "\n".join(
            f"- {g['dimension']}: avg_intent={g['avg_intent']}, avg_reality={g['avg_reality']}, gap={g['avg_gap']} ({g['company_count']} companies)"
            for g in mi["gap_patterns"]
        )

    # Top foresights
    foresight_summary = ""
    if mi.get("top_foresights"):
        foresight_summary = "\n".join(
            f"- {f['theme']} ({f['category']}): {f['count']} companies ({f['pct']}%)"
            for f in mi["top_foresights"][:10]
        )

    return f"""{SYSTEM_ROLE}

TASK: Write a Finnish Economy Force Map — how global forces specifically reshape the Finnish business landscape.

GLOBAL CONTEXT (the analysis you are building upon):
{global_analysis}

FINNISH DATA:

Market Health by Domain:
{health_summary}

Archetype Distribution by Domain:
{arch_summary}

FAVI Segment Distribution (across all companies):
{favi_summary}

Gap Patterns (Intent vs Reality per KB Dimension):
{gap_summary}

Top Foresight Themes Affecting Finnish Companies:
{foresight_summary}

Total companies analyzed: {mi.get('company_count', 0)}
Companies with detailed data: {mi.get('detailed_count', 0)}

ANALYTICAL STRUCTURE:

Section 1 — THE FINNISH POSITION: Where does Finnish industry stand in the global force landscape? Use the market health scores and archetype distributions to identify strengths and blind spots. Ground every claim in the numbers above.

Section 2 — DOMAIN CONVERGENCE: Which forces cut across multiple Finnish domains? What systemic patterns emerge from the cross-domain data? Where are the unexpected connections?

Section 3 — THE ADAPTABILITY GAP: Use the gap patterns (intent vs reality) to analyze where Finnish companies claim one thing but do another. What does this reveal about organizational thinking capacity? Are Finnish companies running someone else's race?

Section 4 — THREE HORIZONS FOR FINLAND:
- Now (6-12 months): What must Finnish industry address immediately based on the data?
- Prepare (12-24 months): What capabilities need building?
- Position (24-36 months): What "Own Race" opportunities exist?

OUTPUT FORMAT:
- 1000-1200 words
- Four sections with headers
- Analytical essay prose — NO bullet points
- Grounded in actual company data patterns (cite specific numbers)"""


def _build_domain_prompt(
    domain: str, domain_config: dict, domain_companies: list,
    kb_signals: list, market_health: dict,
    global_analysis: str, finland_analysis: str,
) -> str:
    """Build prompt for Output 3: Domain Force Dossier."""
    # Company stats
    n = len(domain_companies)
    archetypes = Counter()
    favi_segments = Counter()
    reality_scores = []
    financial_scores = []

    for c in domain_companies:
        reality = c.get("reality_score")
        financial = c.get("financial_index")
        if reality is not None:
            reality_scores.append(float(reality))
            arch = classify_archetype(
                float(reality),
                float(financial) if financial is not None else float(c.get("intent_score", 0))
            )
            archetypes[arch] += 1
        if financial is not None:
            financial_scores.append(float(financial))

    avg_reality = round(statistics.mean(reality_scores), 1) if reality_scores else 0
    avg_financial = round(statistics.mean(financial_scores), 1) if financial_scores else 0

    arch_str = ", ".join(f"{k}={v}" for k, v in sorted(archetypes.items()))

    # Foresight links from domain companies
    domain_forces = []
    for c in domain_companies:
        for link in (c.get("foresight_links") or []):
            domain_forces.append({
                "theme": link.get("fp_theme", ""),
                "dimension": link.get("kb_dimension", ""),
                "timeframe": link.get("timeframe", ""),
                "force_type": link.get("force_type", ""),
                "title": link.get("title", ""),
            })

    # KB signals for this domain's themes
    kb_summary = []
    for sig in kb_signals[:30]:
        kb_summary.append(f"- [{sig.get('timeframe', '?')}] {sig.get('title', '?')} ({sig.get('force_type', '?')})")

    # Forces by timeframe
    forces_by_tf = defaultdict(list)
    for f in domain_forces:
        forces_by_tf[f["timeframe"]].append(f"{f['theme']}: {f['title']}")

    health_score = market_health.get("score", "N/A") if market_health else "N/A"

    return f"""{SYSTEM_ROLE}

TASK: Write a Domain Force Dossier for {domain}.

BROADER CONTEXT (reference but don't repeat):
Global Force Landscape and Finnish Economy Force Map have already been produced. Build on their conclusions.

DOMAIN DATA:

Companies: {n}
Archetype distribution: {arch_str}
Average reality score: {avg_reality}/100
Average financial index: {avg_financial}/100
Market health score: {health_score}/100
FP themes mapped: {', '.join(domain_config.get('fp_themes', []))}
Priority dimensions: {', '.join(domain_config.get('priority_dimensions', []))}

Forces identified in domain companies ({len(domain_forces)} total):
- Near-term (6-12mo): {len(forces_by_tf.get('6-12mo', []))} forces
- Medium-term (12-24mo): {len(forces_by_tf.get('12-24mo', []))} forces
- Long-term (24-36mo): {len(forces_by_tf.get('24-36mo', []))} forces

KB signals for this domain's themes:
{chr(10).join(kb_summary[:20])}

ANALYTICAL STRUCTURE:

Section 1 — DOMAIN REALITY CHECK: Current state summary. What does the archetype distribution and average scores reveal about this domain's collective thinking capacity? Be specific with numbers.

Section 2 — FORCES BY TIMESCALE:
- Next 12 months (Act Now): Forces already in motion. What organizational thinking shifts are urgent? What must companies STOP doing immediately?
- 12-36 months (Prepare): Forces building momentum. What capabilities need to be built? What assumptions need challenging?
- 24-60 months (Position): Emerging forces that will reshape the domain. What "Own Race" opportunities exist?

Section 3 — THE OWN RACE OPPORTUNITY: What would it mean for companies in {domain} to define their own race rather than react? Specific strategic scenarios.

OUTPUT FORMAT:
- 600-800 words
- Three sections with headers
- Analytical essay prose — NO bullet points
- Reference actual data from above"""


# ═══════════════════════════════════════════════════════════════════════════════
# LLM GENERATION + VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_sonnet(prompt: str, max_tokens: int = 4000) -> tuple[str, int]:
    """Generate text via Sonnet 4.6. Returns (text, word_count)."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=anthropic_key)
    t0 = time.time()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        thinking={"type": "enabled", "budget_tokens": 3000},
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
    text = "\n".join(text_parts).strip()

    # Strip markdown links (keep text only)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)

    wc = len(text.split())
    elapsed = int((time.time() - t0) * 1000)
    print(f"   [Observatory] Sonnet 4.6: {wc} words ({elapsed}ms)")
    return text, wc


def _verify_gemini(essay: str, source_data_summary: str, output_type: str) -> dict:
    """Verify essay via Gemini 3.1 Pro. Returns verification result."""
    prompt = f"""You are a quality assurance reviewer for strategic foresight analysis.

TASK: Verify the following {output_type} essay against its source data.

CHECK LIST:
1. DATA ACCURACY: Do any numbers or statistics cited in the essay contradict the source data? List specific mismatches.
2. ANTI-TECHNOLOGY FRAMING: Does the essay frame technology as the key message anywhere? Flag any sentences that say "companies need AI/cloud/digital/technology" rather than focusing on thinking, adaptability, and organizational capability.
3. STRUCTURE: Are all required sections present? Estimate the word count.
4. EVIDENCE GROUNDING: Does the essay make claims not supported by the provided data?

SOURCE DATA:
{source_data_summary[:4000]}

ESSAY TO VERIFY:
{essay}

RESPOND IN JSON FORMAT ONLY:
{{"passed": true/false, "word_count_estimate": N, "issues": ["issue 1", ...], "tech_framing_violations": ["sentence 1", ...], "data_mismatches": ["mismatch 1", ...]}}"""

    try:
        client = get_gemini_client()
        response = call_with_503_backoff(
            lambda: client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=2000,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
                ),
            ),
            label=f"Observatory Verify {output_type}",
            timeout=60,
            model="gemini-3.1-pro-preview",
        )

        # Extract text from response
        text = ""
        if hasattr(response, "candidates") and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        # Parse JSON from response
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())

        return {"passed": True, "issues": [], "note": "Could not parse verification response"}

    except Exception as e:
        print(f"   [Observatory] Verification failed (non-blocking): {e}")
        return {"passed": True, "issues": [], "error": str(e)}


def _generate_with_verification(prompt: str, source_summary: str, output_type: str) -> tuple[str, dict]:
    """Generate via Sonnet 4.6, then verify via Gemini. Returns (text, verification)."""
    text, wc = _generate_sonnet(prompt)

    if wc < 50:
        raise ValueError(f"Generation too short: {wc} words")

    verification = _verify_gemini(text, source_summary, output_type)

    # If verification finds critical issues and text is regeneratable, try once more
    if not verification.get("passed", True) and verification.get("tech_framing_violations"):
        violations = verification["tech_framing_violations"]
        print(f"   [Observatory] Verification found {len(violations)} tech-framing violations — regenerating...")
        correction = f"\n\nCRITICAL CORRECTION: Your previous attempt contained technology-centric framing. These specific sentences violated the anti-technology rule: {json.dumps(violations)}. Rewrite to focus on organizational thinking, adaptability, and future-readiness instead."
        text, wc = _generate_sonnet(prompt + correction)
        verification = _verify_gemini(text, source_summary, output_type)

    return text, verification


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_observatory(progress_callback=None):
    """
    Generate the full Foresight Observatory (three layers).
    Returns the complete observatory data structure.

    progress_callback(step_name, current_index, total_steps) is called after each step.
    """
    storage = BlobStorage()

    def progress(step, idx, total):
        print(f"   [Observatory] Step {idx+1}/{total}: {step}")
        if progress_callback:
            progress_callback(step, idx, total)

    # --- Load all data ---
    data = _load_all_data(storage)
    kb_agg = data["kb_aggregate"]
    mi = data["market_intel"]

    # Import domain config
    from strategic_foresight import DOMAIN_TO_FORESIGHT

    # Count active domains (domains with companies)
    domain_companies = defaultdict(list)
    for c in data["company_details"]:
        domain = c.get("domain") or "Unknown"
        if domain != "Unknown":
            domain_companies[domain].append(c)

    active_domains = [d for d in sorted(domain_companies.keys()) if len(domain_companies[d]) >= 2]
    total_steps = 2 + len(active_domains)  # Global + Finland + N domains

    # --- Output 1: Global Force Landscape ---
    progress("Global Force Landscape", 0, total_steps)
    global_prompt = _build_global_prompt(
        kb_agg,
        mi.get("cross_impact", []),
        mi.get("top_foresights", []),
    )
    global_source = f"Total signals: {kb_agg['total_signals']}, Themes: {kb_agg['theme_count']}, Accelerating: {kb_agg['accelerating']}, Decelerating: {kb_agg['decelerating']}"
    global_text, global_verify = _generate_with_verification(global_prompt, global_source, "Global Force Landscape")

    # Extract key convergences from the text (first 3 sentences that mention convergence/force)
    key_convergences = []
    for sentence in re.split(r'[.!?]+', global_text):
        sentence = sentence.strip()
        if any(word in sentence.lower() for word in ["converg", "amplif", "intersect", "compound"]):
            if len(sentence) > 20:
                key_convergences.append(sentence[:150])
        if len(key_convergences) >= 3:
            break

    global_analysis = {
        "text": global_text,
        "force_count": kb_agg["total_signals"],
        "accelerating_forces": kb_agg["accelerating"],
        "key_convergences": key_convergences,
        "verification": global_verify,
    }

    # --- Output 2: Finnish Economy Force Map ---
    progress("Finnish Economy Force Map", 1, total_steps)
    finland_prompt = _build_finland_prompt(global_text, mi)
    finland_source = f"Companies: {mi.get('company_count', 0)}, Domains: {len(mi.get('market_health', []))}, FAVI segments: {json.dumps(mi.get('favi_segments', {}))}"
    finland_text, finland_verify = _generate_with_verification(finland_prompt, finland_source, "Finnish Economy Force Map")

    # Extract top adaptability gaps
    top_gaps = []
    for g in mi.get("gap_patterns", [])[:3]:
        top_gaps.append(f"{g['dimension']}: gap={g['avg_gap']}")

    finland_analysis = {
        "text": finland_text,
        "domains_analyzed": len(mi.get("market_health", [])),
        "favi_distribution": mi.get("favi_segments", {}),
        "top_adaptability_gaps": top_gaps,
        "verification": finland_verify,
    }

    # --- Output 3: Domain Force Dossiers ---
    domain_dossiers = []
    for i, domain in enumerate(active_domains):
        progress(f"Domain: {domain}", 2 + i, total_steps)

        config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT.get("Other", {}))
        companies = domain_companies[domain]

        # Get KB signals for this domain's themes
        kb_signals = []
        for theme in config.get("fp_themes", []):
            for theme_slug, theme_data in data["all_kb"].items():
                for sig in theme_data.get("signals", []):
                    if sig.get("fp_theme") == theme:
                        kb_signals.append(sig)

        # Find market health for this domain
        mh = None
        for h in mi.get("market_health", []):
            if h["domain"] == domain:
                mh = h
                break

        domain_prompt = _build_domain_prompt(
            domain, config, companies, kb_signals, mh,
            global_text, finland_text,
        )
        source = f"Domain: {domain}, Companies: {len(companies)}, KB signals: {len(kb_signals)}"
        text, verify = _generate_with_verification(domain_prompt, source, f"Domain Dossier: {domain}")

        # Extract forces by timescale
        forces_by_ts = {"act_now": [], "prepare": [], "position": []}
        for sig in kb_signals:
            tf = sig.get("timeframe", "")
            title = sig.get("title", "")[:80]
            if "6-12" in tf:
                forces_by_ts["act_now"].append(title)
            elif "12-24" in tf:
                forces_by_ts["prepare"].append(title)
            elif "24-36" in tf:
                forces_by_ts["position"].append(title)

        # Limit to top 5 per timescale
        for k in forces_by_ts:
            forces_by_ts[k] = forces_by_ts[k][:5]

        dossier = {
            "domain": domain,
            "text": text,
            "company_count": len(companies),
            "archetype_distribution": dict(Counter(
                classify_archetype(
                    float(c["reality_score"]),
                    float(c.get("financial_index") or c.get("intent_score", 0))
                )
                for c in companies if c.get("reality_score") is not None
            )),
            "forces_by_timescale": forces_by_ts,
            "verification": verify,
        }
        domain_dossiers.append(dossier)

    # --- Assemble final result ---
    result = {
        "generated_at": datetime.utcnow().isoformat(),
        "model": "claude-sonnet-4-6",
        "verification_model": "gemini-3.1-pro-preview",
        "company_count": len(data["dashboard_data"]),
        "domain_count": len(active_domains),
        "global_analysis": global_analysis,
        "finland_analysis": finland_analysis,
        "domain_dossiers": domain_dossiers,
    }

    # Save to storage
    url = storage.save_observatory(result)
    print(f"   [Observatory] Saved observatory to storage: {url}")

    return result
