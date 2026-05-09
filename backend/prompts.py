"""
prompts.py — Shared prompt guardrails for forensic company summaries.

This module centralizes evidence discipline used by main.py, seed_manager.py,
general_summary.py and future generation paths.
"""

PROMPT_VERSION = "2026-05-03-phase2-label-governance"

FACT_DISCIPLINE_GUARDRAILS = """
EVIDENCE DISCIPLINE:
- State hard facts directly only when supported by admissible company-specific evidence.
- Use calibrated language for evidence-limited synthesis: "the company reports", "public evidence confirms", "available evidence is limited to", or "validate whether".
- Do not generalize narrow evidence to company-wide conclusions.
- Do not convert foresight, trend, or regulatory context into current company deployment, non-compliance, or deterministic obligation.
- If evidence is contradictory, state both sides with source scope and dates.
"""

DIAGNOSTIC_MODE_GUARDRAILS = """
DIAGNOSTIC MODE:
Separate the output into:
1. observed_facts — directly supported facts;
2. diagnostic_hypotheses — interpretations with confidence;
3. foresight_context — external trends/regulation, clearly future/contextual;
4. recommendations — actions framed as validation-first when confidence is low;
5. unsupported_or_low_confidence — claims that should not be asserted.
"""

LABEL_GOVERNANCE_GUARDRAILS = """
LABEL GOVERNANCE:
Do not use strong labels such as "Digital Taylorism", "Fake Agile", "Zombie Scrum",
"Blame Culture", "Feature Factory", "Pilot Purgatory", or "Modernization Washing"
unless at least two independent admissible sources support the same pattern and at
least one source is broad/company-wide/business-unit scope. If evidence is narrow,
use cautious wording such as "possible operating-model friction" or "validate whether".
"""

FORESIGHT_GUARDRAILS = """
FORESIGHT VS FACT:
External foresight, analyst trends and regulation sources may support future relevance
or preparation needs. They do not by themselves prove current company deployment,
company-specific non-compliance, or that the company must change a named process.
"""

CLAIM_TIER_GUARDRAILS = """
CLAIM-TIER WORDING — MANDATORY:
Each evidence item is tagged [A], [B], [C], [D], or [E]. Match your wording to the tier:
  [A] Hard external verification (analyst report, news, regulatory filing) → "reported", "recorded", "confirmed by", "according to [source]"
  [B] Filed/transactional disclosure (annual report, investor relations, M&A filing) → "announced", "disclosed", "filed", "reported in its annual report"
  [C] Company self-claim only (website, press release, blog) → "states", "claims", "positions itself as", "describes itself as", "according to the company"
  [D] Inferred / weak signals (job postings, employee reviews, narrow surveys, medium-credibility third-party) → "suggests", "may indicate", "signals", "available evidence points to"
  [E] External trend or regulatory context only — NOT company-specific evidence → "could create risk/opportunity for", "is relevant context for", "emerging signals suggest", "if [trend] accelerates"

SELF-CLAIM RESTRICTION — ABSOLUTE:
[C]-tier sources may ONLY support claims about: the company's own identity, brand positioning, self-described capabilities, chronology of corporate events, and explicitly quoted self-statements.
NEVER use [C]-tier evidence to assert actual operational performance, real headcount, technology deployment, or revenue — even if the number sounds specific. Qualify it: "The company states..." not "The company has..."

ENTITY SCOPE:
When evidence is tagged [group], cite the parent/group level explicitly (e.g., "[Company] Group") — do NOT attribute group-level financials or headcount to the local entity.
When evidence is tagged [local], cite the local/subsidiary entity explicitly.
When a metric appears at both levels (group and local), state them separately and label each.
"""


def build_shared_guardrails(include_diagnostic: bool = True, include_foresight: bool = True, include_claim_tier: bool = True) -> str:
    parts = [FACT_DISCIPLINE_GUARDRAILS, LABEL_GOVERNANCE_GUARDRAILS]
    if include_diagnostic:
        parts.append(DIAGNOSTIC_MODE_GUARDRAILS)
    if include_foresight:
        parts.append(FORESIGHT_GUARDRAILS)
    if include_claim_tier:
        parts.append(CLAIM_TIER_GUARDRAILS)
    return "\n".join(parts)
