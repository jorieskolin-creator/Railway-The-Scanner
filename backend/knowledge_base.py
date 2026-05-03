
"""
knowledge_base.py — Evolving Modernization knowledge base and label governance.

Phase 2 update: the KB now includes deterministic label-governance thresholds.
The goal is to keep strong framework labels useful without letting them become
semantic attractors that overdiagnose weak or narrow evidence.
"""

from __future__ import annotations

import json
import requests

REMOTE_KNOWLEDGE_URL = "https://l9fgwjm9p1o6atow.public.blob.vercel-storage.com/tactics.json"
_kb_cache = {"content": None}

LABEL_EVIDENCE_THRESHOLDS = {
    "Digital Taylorism": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible operating-model friction"},
    "Fake Agile": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible delivery-model friction"},
    "Zombie Scrum": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible agile-practice inconsistency"},
    "Blame Culture": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible trust or learning-culture risk"},
    "Feature Factory": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible output-over-outcome orientation"},
    "Pilot Purgatory": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible scaling gap"},
    "Modernization Washing": {"minimum_independent_sources": 2, "minimum_broad_or_business_unit_sources": 1, "safe_fallback": "possible gap between stated ambition and verified execution evidence"},
}

FALLBACK_FRAMEWORK = """
# ROLE & METHODOLOGY
You are a Senior Market Forensics Analyst specializing in the "Evolving Modernization" framework.
Your task is to diagnose the "Reality Gap" between a company's stated Intent (Narrative) and their Operational Reality (Evidence).

## 1. THE CORE FRAMEWORK
Evaluate companies against these 5 Dimensions, but use strong labels only when label-governance thresholds are met.

### A. OPERATING MODEL (Autonomy vs. Control)
- Old Way label: "Digital Taylorism".
- Modern Way label: "Flow Efficiency".

### B. STRATEGY (Adaptability vs. Rigidity)
- Old Way labels: "Fake Agile" / "Zombie Scrum".
- Modern Way label: "Adaptive Strategy".

### C. VALUES (Safety vs. Fear)
- Old Way label: "Blame Culture".
- Modern Way label: "Psychological Safety".

### D. SERVICE PORTFOLIO (Outcomes vs. Outputs)
- Old Way label: "Feature Factory".
- Modern Way label: "Value Streams".

### E. AI READINESS (Augmentation vs. Inertia)
- Old Way label: "Pilot Purgatory".
- Modern Way label: "AI-Augmented".

## 2. DIAGNOSTIC ARCHETYPES
Classify into Thriving, Modernizing, Cash Cow, or At Risk only with explicit archetype confidence.

## 3. WRITING GUARDRAILS
- Tone: Analytical, Direct, Forensic. No marketing fluff.
- Evidence First: separate observed facts from diagnostic hypotheses.
- Language: use framework terminology only when label-governance thresholds are met.
"""


def get_label_thresholds() -> dict:
    """Return deterministic label thresholds for diagnosis/archetype prompts."""
    return LABEL_EVIDENCE_THRESHOLDS


def assess_label_permission(label: str, evidence_links: list | None = None) -> dict:
    """Conservative label-use gate for KB terms.

    A strong label requires at least two independent admissible sources and at
    least one broad/company-wide/business-unit source. If the evidence is narrow
    or absent, callers should use the safe_fallback wording instead.
    """
    threshold = LABEL_EVIDENCE_THRESHOLDS.get(label)
    if not threshold:
        return {"allowed": False, "confidence": "unknown", "reason": "unknown_label", "safe_fallback": "diagnostic hypothesis"}

    links = [l for l in (evidence_links or []) if isinstance(l, dict) and l.get("admissible") is not False]
    if not links:
        return {"allowed": False, "confidence": "low", "reason": "no_admissible_evidence", "safe_fallback": threshold["safe_fallback"]}

    independent = sum(1 for l in links if l.get("independence") != "company-reported")
    broadish = sum(1 for l in links if (l.get("scope") or "").lower() in {"broad", "business-unit", "company-wide", "enterprise"})
    source_types = sorted({(l.get("source_type") or "unknown").lower() for l in links})

    if independent < threshold["minimum_independent_sources"] or broadish < threshold["minimum_broad_or_business_unit_sources"]:
        return {
            "allowed": False,
            "confidence": "low",
            "reason": "insufficient_independent_or_broad_evidence",
            "safe_fallback": threshold["safe_fallback"],
            "independent_sources": independent,
            "broad_or_business_unit_sources": broadish,
            "source_types": source_types,
        }

    return {
        "allowed": True,
        "confidence": "medium" if broadish == 1 else "high",
        "reason": "threshold_met",
        "safe_fallback": threshold["safe_fallback"],
        "independent_sources": independent,
        "broad_or_business_unit_sources": broadish,
        "source_types": source_types,
    }


def get_label_governance_prompt() -> str:
    """Prompt text appended to the KB to stop overdiagnosis."""
    return """

## 4. LABEL GOVERNANCE — DO NOT OVERDIAGNOSE
Use framework labels as controlled vocabulary, not as assumptions.

Before using a strong negative label such as "Digital Taylorism", "Fake Agile",
"Zombie Scrum", "Blame Culture", "Feature Factory", "Pilot Purgatory", or
"Modernization Washing", verify that the evidence meets these rules:

- At least two independent admissible sources must support the same pattern.
- At least one source must be broad, company-wide, or business-unit level.
- A single thesis, single survey, job posting, employee review, pilot, GitHub
  repository, or foresight report is not enough for a strong label.
- If evidence is narrow, anecdotal, old, or mostly self-reported, use cautious
  wording such as "possible ...", "available evidence suggests ...", or
  "validate whether ...".
- Separate observed facts from diagnostic hypotheses and recommendations.
- Foresight and regulation sources can support future relevance, not current
  company deployment or non-compliance unless a company-specific source proves it.
"""


def _build_framework_from_json(data: dict) -> str:
    kb_content = json.dumps(data, indent=2, ensure_ascii=False)
    return f"""
# ROLE & METHODOLOGY
You are a Senior Market Forensics Analyst specializing in the "Evolving Modernization" framework.
Your task is to diagnose the "Reality Gap" between a company's stated Intent and Operational Reality.

## 1. THE KNOWLEDGE BASE (Source of Truth)
The following JSON data contains the specific Tactics, Patterns, and Vocabulary to use for diagnosis.

```json
{kb_content}
```

## 2. DIAGNOSTIC ARCHETYPES
Classify the company into Thriving, Modernizing, Cash Cow, or At Risk based on Reality Score and Financial Index, but include archetype confidence.

## 3. WRITING GUARDRAILS
- Tone: Analytical, Direct, Forensic. No marketing fluff.
- Evidence First: separate observed facts from diagnostic hypotheses.
- Language: use KB terminology only when label-governance thresholds are met.
""" + get_label_governance_prompt()


def get_evolving_framework():
    """Fetch latest framework; fall back to hardcoded version with governance guardrails."""
    if _kb_cache["content"]:
        return _kb_cache["content"]

    try:
        print(f"Fetching Knowledge Base from {REMOTE_KNOWLEDGE_URL}...")
        response = requests.get(REMOTE_KNOWLEDGE_URL, timeout=3)
        if response.status_code == 200:
            result = _build_framework_from_json(response.json())
            _kb_cache["content"] = result
            return result
    except Exception as e:
        print(f"Knowledge Base Fetch Failed ({e}). Using Fallback.")

    result = FALLBACK_FRAMEWORK + get_label_governance_prompt()
    _kb_cache["content"] = result
    return result
