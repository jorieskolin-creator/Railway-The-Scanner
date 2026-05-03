"""
prompts.py — Shared prompt guardrails for forensic company summaries.

This module is intentionally lightweight. It gives main.py, seed_manager.py,
general_summary.py and future summary builders one shared place for evidence
rules. It can be adopted incrementally; importing it is optional until the
calling modules are refactored.
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


def build_shared_guardrails(include_diagnostic: bool = True, include_foresight: bool = True) -> str:
    parts = [FACT_DISCIPLINE_GUARDRAILS, LABEL_GOVERNANCE_GUARDRAILS]
    if include_diagnostic:
        parts.append(DIAGNOSTIC_MODE_GUARDRAILS)
    if include_foresight:
        parts.append(FORESIGHT_GUARDRAILS)
    return "\n".join(parts)
