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
Each evidence item in the input is prefixed [A], [B], [C], [D], or [E] as a reading aid for YOU.
These tags are INTERNAL ONLY — NEVER write [A], [B], [C], [D], [E] or the word "tier" in your output prose.
Match the verb/hedge you choose to the tier of the evidence you are citing:
  [A] Hard external verification (analyst report, news) → use "reported", "recorded", "confirmed", "according to [source name]"
  [B] Filed/transactional disclosure (annual report, investor relations) → use "announced", "disclosed", "filed", "its annual report shows"
  [C] Company self-claim (website, press release, blog) → use "states", "positions itself as", "describes itself as", "according to the company"
  [D] Inferred / weak signals (job postings, reviews, narrow surveys) → use "suggests", "may indicate", "signals point to", "available evidence points to"
  [E] External trend or regulatory context only — not company-specific → use "could create risk/opportunity for", "emerging signals suggest", "if [trend] accelerates"

The tiers are input metadata only. The output must read as natural, fluent prose — calibrated by source strength, but never labelled with tier codes.

SELF-CLAIM RESTRICTION — ABSOLUTE:
Sources tagged [C] may ONLY support claims about: the company's own identity, brand positioning, self-described capabilities, and chronology of corporate events.
NEVER use [C] evidence to assert actual operational performance, real headcount, technology deployment, or revenue — even if the number sounds specific. Qualify it: "The company states..." not "The company has..."

ENTITY SCOPE:
When evidence is tagged [group], cite the parent/group level explicitly (e.g., "[Company] Group") — do NOT attribute group-level financials or headcount to the local entity.
When evidence is tagged [local], cite the local/subsidiary entity explicitly.
When a metric appears at both levels, state them separately.
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
