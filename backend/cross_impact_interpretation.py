"""
Cross-Impact Matrix Interpretation
==================================
Generates a structured prose interpretation of the Theme x Dimension Cross-Impact
Matrix for the Market Intelligence: Future Demand Analysis view.

Produces three views in section-marker format:
  - VERTICAL: dominant forces (Pareto top 3 themes)
  - HORIZONTAL: convergence points (top 3 highest-density cells)
  - FOCUSED: structural pattern (cross-analysis + conspicuous absences)

Reuses the LLM + Gemini verification pipeline from foresight_observatory.
Adds a deterministic post-hoc numeric verifier that checks every cited density
against the matrix payload to catch fabricated numbers.
"""

import json
import re
from datetime import datetime

from market_intelligence import (
    KB_DIMENSIONS,
    compute_cross_impact,
    compute_gap_patterns,
    compute_archetype_flow,
    shape_cross_impact_for_prompt,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DIMENSION DESCRIPTIONS — passed to the prompt for accurate paraphrasing
# ═══════════════════════════════════════════════════════════════════════════════

DIMENSION_DESCRIPTIONS = {
    "Operating Model": "How the organization is structured, how teams work, governance, agility, and process design.",
    "Service Portfolio": "What the organization sells: products, services, platforms, monetization, offering mix.",
    "Values": "Culture, ethics, sustainability posture, inclusion, and the principles guiding decisions.",
    "Strategy": "Direction, market positioning, competitive thesis, growth ambition, transformation agenda.",
    "AI Readiness": "Capability to absorb AI-driven change: data foundations, talent, automation, technology stack.",
}


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_prompt(
    matrix_data: list,
    theme_descriptions: dict,
    available_sources: list,
    intent_reality_gap: list = None,
    stakeholder_attention: dict = None,
    archetype_distribution: list = None,
) -> str:
    """Build the v1 Cross-Impact Matrix Interpretation prompt.

    matrix_data: output of shape_cross_impact_for_prompt — each item has
      {theme, total_density, dimensions, apply_shape_label}.
    theme_descriptions: map of theme name -> short description.
    available_sources: list of source labels actually injected into this prompt.
    Optional inputs degrade gracefully when None/empty.
    """
    # Build the matrix table the model reads. Note vocabulary: "density".
    matrix_lines = []
    for item in matrix_data:
        dim_str = ", ".join(f"{d}={item['dimensions'].get(d, 0)}" for d in KB_DIMENSIONS)
        shape_note = "" if item.get("apply_shape_label", True) else " [SMALL_N: do not apply shape label; describe distribution numerically only]"
        matrix_lines.append(
            f"- {item['theme']} | total_density={item['total_density']} | {dim_str}{shape_note}"
        )
    matrix_block = "\n".join(matrix_lines) if matrix_lines else "(no themes available)"

    # Theme descriptions (only for themes present in the matrix)
    matrix_themes = {item["theme"] for item in matrix_data}
    desc_lines = [
        f"- {theme}: {theme_descriptions.get(theme, '(no description provided)')}"
        for theme in matrix_themes
    ]
    desc_block = "\n".join(desc_lines) if desc_lines else "(no theme descriptions provided)"

    # Dimension descriptions
    dim_lines = [f"- {d}: {DIMENSION_DESCRIPTIONS[d]}" for d in KB_DIMENSIONS]
    dim_block = "\n".join(dim_lines)

    # Optional cross-reference blocks
    if intent_reality_gap:
        gap_block = "\n".join(
            f"- {g.get('dimension')}: intent={g.get('avg_intent')}, reality={g.get('avg_reality')}, gap={g.get('avg_gap')}"
            for g in intent_reality_gap
        )
    else:
        gap_block = "(not provided — omit the intent-reality cross-reference)"

    if stakeholder_attention and stakeholder_attention.get("matrix_text"):
        stake_block = stakeholder_attention["matrix_text"]
        unchampioned = stakeholder_attention.get("unchampioned_dimensions") or []
        if unchampioned:
            stake_block += f"\nDimensions without senior champions: {', '.join(unchampioned)}"
    else:
        stake_block = "(not provided — omit the stakeholder cross-reference)"

    if archetype_distribution:
        arch_lines = [
            f"- {a.get('domain')}: thriving={a.get('thriving')}, modernizing={a.get('modernizing')}, cash_cow={a.get('cash_cow')}, at_risk={a.get('at_risk')}, total={a.get('total')}"
            for a in archetype_distribution
        ]
        arch_block = "\n".join(arch_lines)
    else:
        arch_block = "(not provided — omit the archetype cross-reference)"

    sources_block = ", ".join(available_sources) if available_sources else ""

    return f"""You are an analyst producing an interpretive summary of the Theme x Dimension
Cross-Impact Matrix for a Market Intelligence report. Your reader is a business
user who understands the report at a high level but is opening this interpretation
to make sense of what the matrix is telling them.

The matrix is a density map of evidence convergence. Each cell shows how densely
a foresight theme links to a strategic dimension across the scanned company
portfolio. High density means the analysis pipeline repeatedly and independently
identified the same theme-dimension pairing as a meaningful external force. Read
densities relative to one another; do not treat any single number as absolute.

Your task is to produce a structured interpretation in three views:
1. VERTICAL VIEW — the three highest-volume themes (Pareto top 3 by row total)
2. HORIZONTAL VIEW — the three highest-density cells in the matrix
3. FOCUSED VIEW — cross-analysis of how the top themes propagate

Write in calm, formal, analytical prose. Use full sentences and paragraphs, not
bullets. Avoid hype and avoid hedging. State findings directly, but never overstate
what the data shows.

-----

INPUTS

Matrix data (top themes, with per-dimension density):
{matrix_block}

Theme descriptions (use these for accurate paraphrasing — do not invent themes):
{desc_block}

Dimension descriptions (use these for accurate paraphrasing):
{dim_block}

Available sources for attribution (list verbatim in the SOURCES section):
{sources_block}

Optional cross-reference data:
Intent-Reality Gap:
{gap_block}

Stakeholder Attention Map:
{stake_block}

Archetype Distribution:
{arch_block}

-----

ANALYTICAL STEPS — perform these silently before writing.

Step 1 — Identify the Pareto top 3 themes by total_density.
For each, compute the full dimension distribution and the share absorbed by
the top dimension and by the top two combined.

Step 2 — Characterize each top theme's distribution shape.
- CONCENTRATED: top dimension >= 35% of total_density AND top two combined >= 60%.
- BIMODAL: top two combined >= 60% but top single below 35%.
- DIFFUSE: no single dimension >= 30% AND top two combined < 55%.
If a theme has the flag SMALL_N in its row, do NOT apply the CONCENTRATED /
BIMODAL / DIFFUSE label. Describe its distribution numerically and explain
that the row total is too small for shape classification.

Step 3 — Identify the top 3 highest-density cells in the matrix (any rows).

Step 4 — Identify conspicuous absences. For each top theme, flag any cell that
holds less than 10% of that theme's total_density when other cells in the same
row hold much more. These absences are diagnostically meaningful.

Step 5 — Cross-analysis: compare distribution shapes of the top themes
(different routing, compounding pressure, contrast between concentrated and
diffuse).

Step 6 — Optional cross-reference. If any of Intent-Reality Gap, Stakeholder
Attention, or Archetype Distribution is provided (not "(not provided ...)"),
look for misalignment between where matrix pressure lands and where leadership
attention or self-assessment sits.

-----

OUTPUT STRUCTURE — produce these section markers verbatim, one per line, in this
exact order. The rendering layer parses these markers; do not deviate.

[SECTION: INTRODUCTION]
One short paragraph (2-3 sentences) that orients the reader. State that the
interpretation has three views: dominant forces (vertical), convergence points
(horizontal), and the structural pattern (focused).

[SECTION: VERTICAL — DOMINANT FORCES]
One opening sentence framing what the vertical view answers.
Then, for each of the top 3 Pareto themes, produce a compact subsection in this
exact format:
[THEME: <name>] [TOTAL: <total_density>] [SHAPE: <CONCENTRATED|BIMODAL|DIFFUSE|SMALL_N>]
Two paragraphs. Paragraph 1: state the theme's total_density, its full
distribution, and characterize its shape (or note SMALL_N if applicable).
Use specific numbers. Paragraph 2: explain what the shape means — where the
pressure lands, or why diffuseness/small-N is itself the finding. Reference
the theme description so the reader knows what kinds of signals are inside
the theme.

[SECTION: HORIZONTAL — CONVERGENCE POINTS]
One opening sentence framing what the horizontal view answers.
Then, for each of the top 3 highest-density cells, produce in this exact format:
[CELL: <theme> x <dimension>] [DENSITY: <value>] [RANK: 1|2|3]
Two paragraphs. Paragraph 1: what the cell tells us — name the theme, the
dimension, and the convergence specifically. Paragraph 2: why this matters —
what kind of organizational response the convergence implies.

[SECTION: FOCUSED — STRUCTURAL PATTERN]
Three to four paragraphs. No sub-headers in this section.
Paragraph 1: Compare the distribution shapes of the top Pareto themes. Use
specific numbers.
Paragraph 2: Explain the conspicuous absences flagged in Step 4.
Paragraph 3: State the structural finding — the one or two sentences that
summarize what the three views together reveal.
Paragraph 4 (only if any optional cross-reference data was provided): Connect
the matrix pattern to the intent-reality gap, stakeholder attention, or
archetype distribution. Cite specific numbers from the cross-reference data.

[SECTION: HOW TO USE THIS INTERPRETATION]
Two short paragraphs. Paragraph 1: Read densities relative to one another, not
as absolute counts. Notice absences as well as presences. Paragraph 2: The
matrix establishes where structural pressure is consolidating; the rest of the
report — gap analysis, stakeholder map, archetype distribution, forensic
diagnostic — refines those findings into actionable terrain.

[SECTION: SOURCES]
One sentence stating that this interpretation draws on the following sources,
then list verbatim from "Available sources for attribution" above, separated by
commas. List ONLY items present in that input. If no sources are listed there,
state: "Source attribution unavailable for this interpretation."

-----

HARD CONSTRAINTS

1. Use only data present in the inputs. Do not invent density values, gap
   numbers, theme descriptions, or sources.
2. Treat density as a relational quantity. Do not describe density as "X
   signals" or "X URLs" or "X findings". Use phrases like "the cell shows a
   density of <value>", "the theme's total density is <value>", "the
   highest-density cell in the row", or simply "the value <number>".
3. Use specific numbers in the analysis. Vague phrases like "high", "very low",
   or "much higher" are insufficient on their own — they must be supported by
   actual values from the matrix in the same or immediately preceding sentence.
4. Do not use bullet points, numbered lists, or visual emphasis (bold, italics)
   in the prose. Section markers and metadata tags ([THEME: ...], [CELL: ...])
   are the only structural markup the model produces.
5. Maintain a calm, formal tone throughout. Do not use sales language, do not
   use exclamations, and do not address the reader as "you" except in the
   closing "How to Use" section.
6. Length budget: 700-1100 words total across all sections. The Focused section
   should be the longest; Sources should be one short sentence plus the list.
7. Do not output any preamble or postscript outside the [SECTION: ...] markers.
   The first character of the output is the first section marker; the last
   character is the end of the Sources section.

Output only the structured interpretation. Nothing else."""


# ═══════════════════════════════════════════════════════════════════════════════
# POST-HOC NUMERIC VERIFIER
# ═══════════════════════════════════════════════════════════════════════════════

_DENSITY_PATTERNS = [
    re.compile(r"density of\s+(\d+)", re.IGNORECASE),
    re.compile(r"total\s+density\s+of\s+(\d+)", re.IGNORECASE),
    re.compile(r"\[\s*DENSITY\s*:\s*(\d+)\s*\]"),
    re.compile(r"\[\s*TOTAL\s*:\s*(\d+)\s*\]"),
    re.compile(r"the value\s+(\d+)", re.IGNORECASE),
]


def _allowed_numbers(matrix_data: list) -> set:
    """Build the set of numbers that can legitimately appear in density claims:
    every cell value, every row total, and 0."""
    allowed = {0}
    for item in matrix_data:
        allowed.add(int(item.get("total_density", 0)))
        for v in (item.get("dimensions") or {}).values():
            allowed.add(int(v))
    return allowed


def verify_numeric_claims(text: str, matrix_data: list) -> dict:
    """Extract density numbers cited in the prose and confirm each appears in
    the matrix payload. Returns {passed, fabricated, checked}."""
    allowed = _allowed_numbers(matrix_data)
    checked = []
    fabricated = []
    for pattern in _DENSITY_PATTERNS:
        for m in pattern.finditer(text):
            n = int(m.group(1))
            checked.append(n)
            if n not in allowed:
                fabricated.append(n)
    return {
        "passed": not fabricated,
        "checked": checked,
        "fabricated": sorted(set(fabricated)),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AVAILABLE-SOURCES BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_available_sources(
    intent_reality_gap: list = None,
    stakeholder_attention: dict = None,
    archetype_distribution: list = None,
) -> list:
    """Build the source-label list from what is actually being injected. The
    model receives only labels for data that was actually included."""
    sources = [
        "Theme x Dimension Cross-Impact Matrix",
        "Foresight Knowledge Base",
        "Company Summary Index",
    ]
    if intent_reality_gap:
        sources.append("Intent-Reality Gap analysis")
    if stakeholder_attention and stakeholder_attention.get("matrix_text"):
        sources.append("Stakeholder Attention Map")
    if archetype_distribution:
        sources.append("Archetype Distribution")
    return sources


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_cross_impact_interpretation(
    company_details: list,
    dashboard_data: list,
    theme_descriptions: dict,
) -> dict:
    """Produce the Cross-Impact Matrix Interpretation payload.

    Returns:
      {
        text: str,                   # raw model output with section markers
        verification: dict,          # Gemini verification result
        numeric_check: dict,         # post-hoc numeric verifier result
        sources_used: list[str],
        matrix_snapshot: list,       # the shaped matrix the prompt actually saw
        generated_at: str (ISO8601),
        model: str,
      }
    """
    # Lazy import to avoid pulling Anthropic/Gemini deps at module load.
    from foresight_observatory import (
        _generate_with_verification,
        _aggregate_stakeholder_dynamics,
    )

    # 1. Build inputs from existing computations (no new scoring or scraping).
    cross_impact_flat = compute_cross_impact(company_details)
    matrix_data = shape_cross_impact_for_prompt(cross_impact_flat)
    if not matrix_data:
        raise ValueError("Cross-impact matrix is empty — cannot generate interpretation")

    gap_patterns = compute_gap_patterns(company_details)
    archetype_distribution = compute_archetype_flow(dashboard_data)
    stakeholder_data = _aggregate_stakeholder_dynamics(company_details, gap_patterns)
    if not stakeholder_data.get("matrix_text"):
        stakeholder_data = None  # signal "not provided"

    available_sources = build_available_sources(
        intent_reality_gap=gap_patterns,
        stakeholder_attention=stakeholder_data,
        archetype_distribution=archetype_distribution,
    )

    # 2. Build prompt + run generation with Gemini verification.
    prompt = _build_prompt(
        matrix_data=matrix_data,
        theme_descriptions=theme_descriptions,
        available_sources=available_sources,
        intent_reality_gap=gap_patterns,
        stakeholder_attention=stakeholder_data,
        archetype_distribution=archetype_distribution,
    )
    source_summary = (
        f"Themes: {len(matrix_data)}, "
        f"Top theme density: {matrix_data[0]['total_density']}, "
        f"Sources: {', '.join(available_sources)}"
    )
    text, verification = _generate_with_verification(
        prompt,
        source_summary,
        "Cross-Impact Matrix Interpretation",
    )

    # 3. Post-hoc numeric check. If a fabricated density slipped through,
    #    regenerate once with a correction prompt.
    numeric_check = verify_numeric_claims(text, matrix_data)
    if not numeric_check["passed"]:
        from foresight_observatory import _generate_sonnet, _verify_gemini
        correction = (
            "\n\nCRITICAL CORRECTION: Your previous attempt cited density values "
            f"that do not appear in the matrix payload: {numeric_check['fabricated']}. "
            "Every density number in the prose must match a value present in the "
            "matrix data above. Rewrite the interpretation using only numbers that "
            "appear in the matrix."
        )
        text, _wc = _generate_sonnet(prompt + correction)
        verification = _verify_gemini(text, source_summary, "Cross-Impact Matrix Interpretation")
        numeric_check = verify_numeric_claims(text, matrix_data)

    return {
        "text": text,
        "verification": verification,
        "numeric_check": numeric_check,
        "sources_used": available_sources,
        "matrix_snapshot": matrix_data,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": "claude-sonnet-4-6 + gemini-3.1-pro verification",
    }
