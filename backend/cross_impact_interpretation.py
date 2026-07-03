"""
Cross-Impact Matrix Interpretation
==================================
Generates a structured prose interpretation of the Theme x Dimension Cross-Impact
Matrix for the Market Intelligence: Future Demand Analysis view.

Produces three views in section-marker format:
  - VERTICAL: dominant forces (Pareto top 3 themes)
  - HORIZONTAL: convergence points (top 3 highest-density cells)
  - FOCUSED: structural pattern (cross-analysis + conspicuous absences)

Reuses the LLM + GPT/Claude verification pipeline from foresight_observatory.
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


def _pct(value, total) -> float:
    try:
        if not total:
            return 0.0
        return round((float(value) / float(total)) * 100.0, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _theme_shape(item: dict) -> str:
    total = int(item.get("total_density") or 0)
    dims = item.get("dimensions") or {}
    if not item.get("apply_shape_label", True):
        return "SMALL_N"
    if total <= 0:
        return "SMALL_N"
    ranked = sorted((int(v or 0) for v in dims.values()), reverse=True)
    top = _pct(ranked[0] if ranked else 0, total)
    top_two = _pct(sum(ranked[:2]), total)
    if top >= 35.0 and top_two >= 60.0:
        return "CONCENTRATED"
    if top_two >= 60.0 and top < 35.0:
        return "BIMODAL"
    if top < 30.0 and top_two < 55.0:
        return "DIFFUSE"
    return "DIFFUSE"


def _shape_meaning(shape: str) -> str:
    return {
        "CONCENTRATED": "single primary organizational bottleneck",
        "BIMODAL": "two-front adaptation problem",
        "DIFFUSE": "whole-business pressure that cannot be owned by one function",
        "SMALL_N": "directional signal with limited density",
    }.get(shape, "uneven routing pattern")


def _response_origin(dimension: str) -> str:
    return {
        "Operating Model": "operating governance, workflow ownership, and execution design",
        "Service Portfolio": "portfolio renewal, offering architecture, and monetization choices",
        "Values": "leadership principles, culture, sustainability, and decision standards",
        "Strategy": "strategic prioritization, market positioning, and investment direction",
        "AI Readiness": "data foundations, AI governance, technology stack, and capability investment",
    }.get(dimension or "", "leadership attention and capability investment")


def _derive_analysis_context(matrix_data: list, theme_descriptions: dict) -> dict:
    """Create prompt-support context so the model can reason from the matrix
    without turning the prose into a score listing."""
    theme_context = []
    top_cells = []
    for item in matrix_data or []:
        theme = item.get("theme") or ""
        total = int(item.get("total_density") or 0)
        dims = item.get("dimensions") or {}
        ranked_dims = sorted(
            ((dim, int(dims.get(dim) or 0)) for dim in KB_DIMENSIONS),
            key=lambda pair: pair[1],
            reverse=True,
        )
        top_dim, top_value = ranked_dims[0] if ranked_dims else ("", 0)
        second_dim, second_value = ranked_dims[1] if len(ranked_dims) > 1 else ("", 0)
        shape = _theme_shape(item)
        absence_candidates = [
            (dim, value)
            for dim, value in ranked_dims
            if total > 0 and value < max(1, total * 0.10) and top_value >= max(2, total * 0.25)
        ]
        absence = absence_candidates[-1] if absence_candidates else None
        theme_context.append({
            "theme": theme,
            "total_density": total,
            "shape": shape,
            "shape_meaning": _shape_meaning(shape),
            "top_dimension": top_dim,
            "top_density": top_value,
            "top_share": _pct(top_value, total),
            "second_dimension": second_dim,
            "second_density": second_value,
            "second_share": _pct(second_value, total),
            "notable_absence": absence[0] if absence else None,
            "notable_absence_density": absence[1] if absence else None,
            "description": theme_descriptions.get(theme, "(no description provided)"),
        })
        for dim, value in ranked_dims:
            if value <= 0:
                continue
            top_cells.append({
                "theme": theme,
                "dimension": dim,
                "density": value,
                "row_share": _pct(value, total),
                "theme_total": total,
                "theme_shape": shape,
                "theme_pattern": "primary landing point" if dim == top_dim else "secondary pressure point",
                "response_origin": _response_origin(dim),
                "dimension_meaning": DIMENSION_DESCRIPTIONS.get(dim, ""),
            })

    top_cells = sorted(top_cells, key=lambda cell: cell["density"], reverse=True)[:3]
    for idx, cell in enumerate(top_cells, start=1):
        cell["rank"] = idx
    return {
        "themes": theme_context[:3],
        "cells": top_cells,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_prompt(
    matrix_data: list,
    theme_descriptions: dict,
    available_sources: list,
    intent_reality_gap: list = None,
    archetype_distribution: list = None,
    analysis_context: dict = None,
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

    analysis_context = analysis_context or _derive_analysis_context(matrix_data, theme_descriptions)
    theme_context_lines = []
    for item in analysis_context.get("themes") or []:
        absence = (
            f"{item['notable_absence']}={item['notable_absence_density']}"
            if item.get("notable_absence") is not None else "none"
        )
        theme_context_lines.append(
            "- {theme} | total_density={total_density} | shape={shape} | "
            "business_meaning={shape_meaning} | primary={top_dimension}:{top_density} ({top_share}%) | "
            "secondary={second_dimension}:{second_density} ({second_share}%) | notable_absence={absence} | "
            "description={description}".format(absence=absence, **item)
        )
    theme_context_block = "\n".join(theme_context_lines) if theme_context_lines else "(no derived theme context)"

    cell_context_lines = []
    for cell in analysis_context.get("cells") or []:
        cell_context_lines.append(
            "- rank={rank} | {theme} x {dimension} | density={density} | row_share={row_share}% | "
            "theme_total={theme_total} | theme_shape={theme_shape} | pattern={theme_pattern} | "
            "response_origin={response_origin} | dimension_meaning={dimension_meaning}".format(**cell)
        )
    cell_context_block = "\n".join(cell_context_lines) if cell_context_lines else "(no derived cell context)"

    # Optional cross-reference blocks
    if intent_reality_gap:
        gap_block = "\n".join(
            f"- {g.get('dimension')}: intent={g.get('avg_intent')}, reality={g.get('avg_reality')}, gap={g.get('avg_gap')}"
            for g in intent_reality_gap
        )
    else:
        gap_block = "(not provided — omit the intent-reality cross-reference)"

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

Derived analytical context (use this to reason; do not copy it mechanically):
Dominant-force context:
{theme_context_block}

Convergence-point context:
{cell_context_block}

Theme descriptions (use these for accurate paraphrasing — do not invent themes):
{desc_block}

Dimension descriptions (use these for accurate paraphrasing):
{dim_block}

Available sources for attribution (list verbatim in the SOURCES section):
{sources_block}

Optional cross-reference data:
Intent-Reality Gap:
{gap_block}

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
Treat these cells as organizational landing points: where a future force turns
into a concrete demand on a dimension such as operating model, strategy,
portfolio, values, or AI readiness.

Step 4 — Identify conspicuous absences. For each top theme, flag any cell that
holds less than 10% of that theme's total_density when other cells in the same
row hold much more. These absences are diagnostically meaningful.

Step 5 — Cross-analysis: compare distribution shapes of the top themes
(different routing, compounding pressure, contrast between concentrated and
diffuse).

Step 6 — Optional cross-reference. If Intent-Reality Gap or Archetype
Distribution is provided (not "(not provided ...)"), look for misalignment
between where matrix pressure lands and where company self-assessment,
portfolio health, or archetype concentration sits.

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
Two paragraphs, 120-150 words total per theme. Paragraph 1 must start with a
one-sentence analytical thesis about what this force means for the portfolio.
Then use only one or two key density references, usually the total_density and
the primary dimension or secondary dimension. Do NOT list the full distribution.
Translate the shape label into business meaning, such as single bottleneck,
two-front adaptation problem, whole-business pressure, or directional signal.
Paragraph 2 carries the interpretive payoff. Explain what emerging demand,
readiness gap, leadership choice, or strategic trade-off the routing reveals.
Reference the theme description so the reader knows what kinds of signals sit
inside the theme, but avoid copying the description. End with what kind of
decision the force puts in front of leadership.

[SECTION: HORIZONTAL — CONVERGENCE POINTS]
One opening sentence framing what the horizontal view answers.
Then, for each of the top 3 highest-density cells, produce in this exact format:
[CELL: <theme> x <dimension>] [DENSITY: <value>] [RANK: 1|2|3]
Two paragraphs, 110-140 words total per cell. Paragraph 1 must explain why this
theme-dimension intersection matters commercially: what future pressure is
landing, what dimension is being tested, and why that makes the cell more than
a large value. Use the density value once if helpful; do not repeat row totals
unless necessary for interpretation.
Paragraph 2 carries the interpretive payoff. Name the likely organizational
response area and where it should originate, using the convergence-point
context when available. Add one explicit demand-hypothesis sentence beginning
with "The demand hypothesis is that..." and keep it falsifiable rather than
claiming confirmed buying intent.

[SECTION: FOCUSED — STRUCTURAL PATTERN]
Three to four paragraphs. No sub-headers in this section.
Paragraph 1: Compare the distribution shapes of the top Pareto themes. Use
one or two specific numbers only where they sharpen the comparison.
Paragraph 2: Explain the conspicuous absences flagged in Step 4.
Paragraph 3: State the structural finding — one or two sentences that
summarize what the three views together reveal — then add a 30-40 word coda
that names the second-order implication of the pattern: not what the data
says, but what the pattern means about the portfolio's coherence, strategic
clarity, or adaptive capacity. This is the line a reader should remember.
Paragraph 4 (only if any optional cross-reference data was provided): Connect
the matrix pattern to the intent-reality gap or archetype distribution. Cite
specific numbers from the cross-reference data.

[SECTION: HOW TO USE THIS INTERPRETATION]
Two short paragraphs. Paragraph 1: Read densities relative to one another, not
as absolute counts. Notice absences as well as presences. Paragraph 2: The
matrix establishes where structural pressure is consolidating; the rest of the
report — gap analysis, archetype distribution, and forensic diagnostic —
refines those findings into actionable terrain.

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
6. Length budget: 1150-1600 words total across all sections. The interpretive
   payoff paragraphs (paragraph 2 of each theme in VERTICAL, paragraph 2 of
   each cell in HORIZONTAL, and the structural-finding coda in FOCUSED) carry
   the analytical weight. Use the extra length for implications and demand
   hypotheses, not additional value listing. Sources should be one short
   sentence plus the list.
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
        verification: dict,          # verification result
        numeric_check: dict,         # post-hoc numeric verifier result
        sources_used: list[str],
        matrix_snapshot: list,       # the shaped matrix the prompt actually saw
        generated_at: str (ISO8601),
        model: str,
      }
    """
    # Lazy import to avoid pulling provider clients at module load.
    from foresight_observatory import _generate_with_verification

    # 1. Build inputs from existing computations (no new scoring or scraping).
    cross_impact_flat = compute_cross_impact(company_details)
    matrix_data = shape_cross_impact_for_prompt(cross_impact_flat)
    if not matrix_data:
        raise ValueError("Cross-impact matrix is empty — cannot generate interpretation")

    gap_patterns = compute_gap_patterns(company_details)
    archetype_distribution = compute_archetype_flow(dashboard_data)

    available_sources = build_available_sources(
        intent_reality_gap=gap_patterns,
        archetype_distribution=archetype_distribution,
    )

    # 2. Build prompt + run GPT Pro generation with independent verification.
    prompt = _build_prompt(
        matrix_data=matrix_data,
        theme_descriptions=theme_descriptions,
        available_sources=available_sources,
        intent_reality_gap=gap_patterns,
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
        use_gpt_pro=True,
    )

    # 3. Post-hoc numeric check. If a fabricated density slipped through,
    #    regenerate once with a correction prompt.
    numeric_check = verify_numeric_claims(text, matrix_data)
    if not numeric_check["passed"]:
        from foresight_observatory import _generate_gpt_pro, _verify_with_models
        correction = (
            "\n\nCRITICAL CORRECTION: Your previous attempt cited density values "
            f"that do not appear in the matrix payload: {numeric_check['fabricated']}. "
            "Every density number in the prose must match a value present in the "
            "matrix data above. Rewrite the interpretation using only numbers that "
            "appear in the matrix."
        )
        text, _wc, generation_model = _generate_gpt_pro(prompt + correction)
        verification = _verify_with_models(text, source_summary, "Cross-Impact Matrix Interpretation")
        verification["generation_model"] = generation_model
        numeric_check = verify_numeric_claims(text, matrix_data)

    return {
        "text": text,
        "verification": verification,
        "numeric_check": numeric_check,
        "sources_used": available_sources,
        "matrix_snapshot": matrix_data,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "model": f"{verification.get('generation_model', 'gpt-5.5-pro')} + gpt/claude verification",
    }
