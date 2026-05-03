"""
foresight_guardrails.py — Shared safeguards to keep foresight as foresight.

Central enforcement for modal foresight language, company-specific corroboration,
and report-date-safe future deadlines.
"""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from datetime import date
from typing import Any

FORESIGHT_CLAIM_TYPE = "foresight_context"
REGULATORY_CLAIM_TYPE = "regulatory_context"
FORESIGHT_SANITIZER_VERSION = "2026-05-03-v3-hardening"

# Narrowed per spec — do NOT use \brequires?\b (flags "may require") or bare \bforces?\b
# (flags noun usage "forces are building"). Use obligation/deployment-specific patterns only.
FORBIDDEN_FACT_PATTERNS = [
    r"\bmust\b",
    # "requires Wärtsilä to" / "requires the company to" — company obligation assertion.
    # Lookbehind excludes the already-modal form "may require … to" produced by MODAL_REPLACEMENTS.
    r"(?<!may )(?<!could )(?<!should )\brequires?\s+(?:the\s+company|the\s+firm|[A-Z][a-zA-Z\xc0-\xff\-]+)\s+to\b",
    r"\brequired\s+to\b",
    # "forces Wärtsilä" / "forces the company" — deterministic compulsion verb.
    # Lookbehind excludes the modal form "may increase pressure on … " produced by MODAL_REPLACEMENTS.
    r"(?<!may increase pressure on )(?<!pressure on )\bforces?\s+(?:the\s+company|the\s+firm|[A-Z][a-zA-Z\xc0-\xff\-]+)\b",
    r"\bhas\s+to\b",
    r"\bwill\s+need\s+to\b",
    r"\bis\s+obligated\s+to\b",
    r"\bfaces\s+penalties\b",
    r"\bis\s+non[-\s]?compliant\b",
    r"\bwill\s+fall\s+behind\b",
    r"\bwill\s+lose\b",
    r"\bhas\s+deployed\b",
    r"\bhave\s+deployed\b",
    r"\bis\s+deploying\b",
    r"\bare\s+deploying\b",
    r"\bcurrently\s+uses\b",
    r"\buses\s+multi-agent\b",
    r"\bbroad\s+deployment\b",
]

# Replacements that transform deterministic language to modal language.
#
# Idempotency lookbehinds — prevent re-application on second sanitizer pass:
#   (?<!may )(?<!could )(?<!should )(?<!might )(?<!would ) on requires?
#     → "may require" is already safe; don't produce "may may require".
#   (?<!whether it )(?<!whether ) on needs to
#     → "should evaluate whether it needs to" must not grow another layer of
#       modal wrapping when the sanitizer runs a second time.
#
# Processing order matters: longer/more-specific patterns run before shorter ones
# so "will need to" is caught before the bare "needs to" alternative.
MODAL_REPLACEMENTS = [
    (r"\bwill\s+need\s+to\b", "may need to"),
    (r"\bis\s+obligated\s+to\b",
     "may be subject to obligations if in scope and should assess whether it needs to"),
    (r"\bfaces\s+penalties\b",
     "may face compliance risk if in scope and if requirements are not addressed"),
    (r"\bis\s+non[-\s]?compliant\b", "may require compliance assessment if in scope"),
    (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
    (r"\bwill\s+lose\b", "could risk losing"),
    (r"\bhas\s+deployed\b",
     "is not proven by this foresight evidence alone to have deployed"),
    (r"\bhave\s+deployed\b",
     "are not proven by this foresight evidence alone to have deployed"),
    (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bare\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
    (r"\buses\s+multi-agent\b", "could evaluate multi-agent"),
    (r"\bbroad\s+deployment\b", "potential broad relevance"),
    (r"\brequired\s+to\b", "potentially expected to if in scope"),
    # Lookbehinds: skip when already preceded by a modal word → prevents "may may require".
    (r"(?<!may )(?<!could )(?<!should )(?<!might )(?<!would )\brequires?\b", "may require"),
    (r"\bforces?\b", "may increase pressure on"),
    (r"\bhas\s+to\b", "should assess whether it needs to"),
    # Lookbehind: skip when already inside "whether it needs to" so the phrase produced
    # by \bmust\b and \bhas\s+to\b replacements is not re-expanded on a second pass.
    (r"(?<!whether it )(?<!whether )\bneeds\s+to\b", "could evaluate whether it needs to"),
    (r"\bmust\b", "should evaluate whether it needs to"),
]

# Deployment/outcome-state patterns — safe to apply to ANY field including source_says and
# neutral descriptions.  These claim a company's current deployment state or competitive
# outcome, which is never appropriate in foresight context regardless of which field they
# appear in.
#
# OBLIGATION patterns (requires/must/forces etc.) are intentionally excluded here — when
# those words appear in source_says or description fields they accurately describe what a
# regulation or source says (e.g. "GDPR requires organizations to…").  Softening them there
# produces a factually misleading hedge.  Obligation patterns are only applied to company-
# impact fields (possible_relevance, impact, insight, recommended_action) via the full
# MODAL_REPLACEMENTS list.
DEPLOYMENT_REPLACEMENTS = [
    (r"\bwill\s+fall\s+behind\b", "may face a disadvantage if the trend becomes material"),
    (r"\bwill\s+lose\b", "could risk losing"),
    (r"\bhas\s+deployed\b",
     "is not proven by this foresight evidence alone to have deployed"),
    (r"\bhave\s+deployed\b",
     "are not proven by this foresight evidence alone to have deployed"),
    (r"\bis\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bare\s+deploying\b", "may be evaluating or could evaluate"),
    (r"\bcurrently\s+uses\b", "may need to evaluate use of"),
    (r"\buses\s+multi-agent\b", "could evaluate multi-agent"),
    (r"\bbroad\s+deployment\b", "potential broad relevance"),
]

FORESIGHT_LANGUAGE_GUARDRAILS = """
FORESIGHT-MODALITY RULES — STRICT:
- Treat all foresight, trend, analyst, scenario and regulatory sources as context, not proof of current company deployment.
- Use modal language: "may", "could", "is becoming relevant", "should evaluate", "should assess whether", "if in scope", "if not already completed".
- Do NOT write deterministic language such as "must", "requires the company to", "forces", "has deployed", "is deploying", "currently uses", "broad deployment", "is non-compliant", or "will fall behind" unless a separate company-specific source proves it.
- Regulatory sources support what the regulation says and affected contexts; they do not alone prove that a named company must change a specific process.
- Do not generate deadlines earlier than the report date. If a date from a source is already in the past, treat it as historical context or write "if not already completed, in the next planning cycle".
- If company-specific evidence is missing, phrase recommendations as "validate whether...", "assess exposure to...", or "evaluate whether...".
"""
QUARTER_ENDS = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31)}


def build_foresight_guardrails(domain: str = "", applicability: dict | None = None) -> str:
    app_txt = ""
    if applicability:
        relevant = applicability.get("relevant_frameworks") or []
        conditional = applicability.get("conditional_frameworks") or []
        notes = applicability.get("notes") or []
        app_txt = (
            "\nDOMAIN APPLICABILITY:\n"
            f"- Domain: {domain or applicability.get('domain', 'unknown')}\n"
            f"- Relevant frameworks: {', '.join(relevant) if relevant else 'none specified'}\n"
            f"- Conditional frameworks: {', '.join(conditional) if conditional else 'none specified'}\n"
            f"- Notes: {'; '.join(notes) if notes else 'Use company-specific evidence before asserting obligations.'}\n"
        )
    return FORESIGHT_LANGUAGE_GUARDRAILS + app_txt


def _parse_quarter_deadline(text: str) -> date | None:
    m = re.match(r"\b(Q[1-4])\s+(20\d{2})\b", text.strip(), re.I)
    if not m:
        return None
    month, day = QUARTER_ENDS[m.group(1).upper()]
    return date(int(m.group(2)), month, day)


def validate_future_deadlines(text: str, report_date: date | None = None) -> list[dict[str, Any]]:
    report_date = report_date or date.today()
    issues: list[dict[str, Any]] = []
    if not text:
        return issues
    for match in re.finditer(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", text, re.I):
        d = _parse_quarter_deadline(match.group(1))
        if d and d < report_date:
            issues.append({
                "deadline": match.group(0),
                "deadline_date": d.isoformat(),
                "report_date": report_date.isoformat(),
                "issue": "past_deadline",
            })
    for match in re.finditer(r"\b(?:by|before|until|in)\s+(20\d{2})\b", text, re.I):
        d = date(int(match.group(1)), 12, 31)
        if d < report_date:
            issues.append({
                "deadline": match.group(0),
                "deadline_date": d.isoformat(),
                "report_date": report_date.isoformat(),
                "issue": "past_deadline",
            })
    return issues


def rewrite_past_deadlines(text: str, report_date: date | None = None) -> str:
    report_date = report_date or date.today()
    if not text:
        return text

    def repl_q(match: re.Match) -> str:
        d = _parse_quarter_deadline(match.group(1))
        return (
            "if not already completed, in the next planning cycle"
            if d and d < report_date
            else match.group(0)
        )

    def repl_y(match: re.Match) -> str:
        d = date(int(match.group(1)), 12, 31)
        return (
            "if not already completed, in the next planning cycle"
            if d < report_date
            else match.group(0)
        )

    out = re.sub(r"\b(?:by|before|until|in)\s+(Q[1-4]\s+20\d{2})\b", repl_q, text, flags=re.I)
    return re.sub(r"\b(?:by|before|until|in)\s+(20\d{2})\b", repl_y, out, flags=re.I)


def validate_foresight_text(
    text: str,
    report_date: date | None = None,
    *,
    allow_word_must_in_rules: bool = False,
) -> list[str]:
    warnings: list[str] = []
    if not text:
        return warnings
    for pattern in FORBIDDEN_FACT_PATTERNS:
        if allow_word_must_in_rules and pattern == r"\bmust\b":
            continue
        if re.search(pattern, text, re.I):
            warnings.append(f"Forbidden deterministic foresight wording matched: {pattern}")
    for issue in validate_future_deadlines(text, report_date):
        warnings.append(
            f"Past deadline found: {issue['deadline']} "
            f"({issue['deadline_date']} < report {issue['report_date']})"
        )
    return warnings


def collapse_repeated_modals(text: str) -> str:
    """
    Collapse repeated modal artifacts produced when sanitization runs on already-sanitized text.

    Examples fixed:
      "may may require"                                       → "may require"
      "may may may require"                                   → "may require"
      "should evaluate whether it could evaluate whether it"  → "should evaluate whether it"
      "could evaluate whether it needs to"                    (already minimal — unchanged)

    Runs to a fixed point (recursively until stable).
    """
    if not text:
        return text

    _MODALS = r"(?:may|could|should|might|would)"
    prev: str | None = None
    while prev != text:
        prev = text
        # Collapse back-to-back identical modal words: "may may" → "may"
        text = re.sub(rf"\b({_MODALS})\s+\1\b", r"\1", text, flags=re.I)
        # Collapse nested evaluate-whether chains: keep outermost opener + innermost continuation
        text = re.sub(
            rf"\b({_MODALS})\s+evaluate whether(?:\s+it)?\s+(?:{_MODALS})\s+evaluate whether\b",
            r"\1 evaluate whether",
            text,
            flags=re.I,
        )
        # Same for "assess whether" chains
        text = re.sub(
            rf"\b({_MODALS})\s+assess whether(?:\s+it)?\s+(?:{_MODALS})\s+assess whether\b",
            r"\1 assess whether",
            text,
            flags=re.I,
        )
        # Collapse any remaining mid-string "evaluate whether … evaluate whether"
        text = re.sub(
            rf"\bevaluate whether(?:\s+it)?\s+(?:{_MODALS})\s+evaluate whether\b",
            "evaluate whether",
            text,
            flags=re.I,
        )

    return re.sub(r"\s{2,}", " ", text).strip()


def sanitize_foresight_modality(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in MODAL_REPLACEMENTS:
        out = re.sub(pat, repl, out, flags=re.I)
    out = collapse_repeated_modals(out)
    return re.sub(r"\s{2,}", " ", out).strip()


def sanitize_foresight_text(text: str, report_date: date | None = None) -> str:
    return rewrite_past_deadlines(sanitize_foresight_modality(text), report_date)


def sanitize_source_description(text: str, report_date: date | None = None) -> str:
    """Sanitize a source/description field using deployment patterns only.

    Use for source_says, description, snippet, summary — fields that describe
    what an external source or regulation says.  Obligation-language patterns
    (requires, must, forces…) are intentionally NOT applied here because words
    like "requires" in "GDPR requires organizations to…" are factually correct
    descriptions of the source's content.  Only deployment/state patterns and
    past-deadline rewrites are applied.
    """
    if not text:
        return text
    out = text
    for pat, repl in DEPLOYMENT_REPLACEMENTS:
        out = re.sub(pat, repl, out, flags=re.I)
    out = collapse_repeated_modals(out)
    out = rewrite_past_deadlines(out, report_date)
    return re.sub(r"\s{2,}", " ", out).strip()


def sanitize_future_insight(
    text: str, report_date: date | None = None
) -> tuple[str, list[dict[str, Any]]]:
    report_date = report_date or date.today()
    issues = validate_future_deadlines(text, report_date)
    return sanitize_foresight_text(text, report_date), issues


def _split_source_and_relevance(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    m = re.search(
        r"source says:\s*(.*?)\s*(possible relevance:|relevance:)(.*)", text, re.I | re.S
    )
    if m:
        return m.group(1).strip(), m.group(3).strip()
    return text.strip(), ""


def _normalize_company_name(name: str) -> str:
    """Return ASCII-normalized company name for accent-insensitive matching.

    E.g. Wärtsilä → Wartsila so both forms match in source_says checks.
    """
    return unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii")


# Regex that strips trailing legal suffixes from a company name.
# E.g. "Wärtsilä Oyj" → "Wärtsilä", "Acme Corp." → "Acme"
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+\b(?:oyj|abp|plc|ltd|limited|inc|incorporated|corp|corporation|group|"
    r"holdings?|a[./]?s|se|ag|sa|b\.?v|n\.?v|gmbh|co\.?|llc|llp)\b\.?\s*$",
    re.I,
)


def _company_name_variants(name: str) -> list[str]:
    """Return all name forms to match in source_says:
    full name, legal-suffix-stripped short name, and ASCII-normalized variants of each.

    E.g. "Wärtsilä Oyj" → ["Wärtsilä Oyj", "Wartsila Oyj", "Wärtsilä", "Wartsila"]
    """
    if not name:
        return []
    variants: set[str] = set()
    full = name.strip()
    variants.add(full)
    variants.add(_normalize_company_name(full))
    short = _LEGAL_SUFFIX_RE.sub("", full).strip()
    if short and short.lower() != full.lower():
        variants.add(short)
        variants.add(_normalize_company_name(short))
    return [v for v in variants if v]


def _genericize_source_says(text: str, company_name: str) -> str:
    """Replace all company-name variants in text with "this organization"."""
    if not text or not company_name:
        return text
    # Replace longest variants first to avoid partial matches (e.g. "Wärtsilä" inside "Wärtsilä Oyj")
    for v in sorted(_company_name_variants(company_name), key=len, reverse=True):
        text = re.sub(re.escape(v), "this organization", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


def _enforce_source_says_neutrality(
    record: dict,
    company_name: str,
    *,
    report_date: date | None = None,
) -> dict:
    """
    Invariant: source_says must be company-neutral when company_specific == False.

    Checks all company-name variants (full legal name, short name without Oyj/Plc/etc.,
    ASCII-normalized forms) so "Wärtsilä" is caught when company_name is "Wärtsilä Oyj".

    If any variant appears in source_says for a non-company-specific record:
    1. Preserve company-specific interpretation (modality-sanitized) in possible_relevance.
    2. Replace all name variants with "this organization" in source_says.
    3. Apply full modal softening to the genericized text (company name removed, so
       "this organization must upgrade" → "this organization should evaluate whether…").
    4. Set not_evidence_of_deployment = True, source_says_was_genericized = True.
    """
    if not company_name or record.get("company_specific") is True:
        return record
    source_says = record.get("source_says") or ""
    if not source_says:
        return record

    variants = _company_name_variants(company_name)
    company_in_source = any(
        re.search(re.escape(v), source_says, re.I) for v in variants
    )
    if not company_in_source:
        return record

    # Move original (modality-sanitized) text to possible_relevance
    sanitized_original = sanitize_foresight_modality(source_says)
    existing_pr = (record.get("possible_relevance") or "").strip()
    if sanitized_original not in existing_pr:
        record["possible_relevance"] = (
            (existing_pr + " " + sanitized_original).strip() if existing_pr else sanitized_original
        )

    # Genericize: replace all name variants, then apply full modal softening.
    # Full MODAL_REPLACEMENTS are correct here because the company name has been removed —
    # "this organization must upgrade" is now generic and should be softened.
    generic = _genericize_source_says(source_says, company_name)
    record["source_says"] = sanitize_foresight_text(generic, report_date)
    record["not_evidence_of_deployment"] = True
    record["requires_company_specific_corroboration"] = True
    record["source_says_was_genericized"] = True
    return record


# Source types that identify direct company evidence — must never be re-classified as
# generic foresight/trend context.  Mirrors the set in database_sanitizer.py.
_COMPANY_SPECIFIC_SOURCE_TYPES = frozenset({
    "company_report", "annual_report", "company_press_release", "press_release",
    "investor_report", "case_study", "company_website", "company_filing",
    "earnings_report", "sustainability_report",
})


def _is_company_specific_record(rec: dict) -> bool:
    """Return True for records that are proven company evidence, not foresight/trend context."""
    if rec.get("company_specific") is True:
        return True
    if str(rec.get("source_type") or "").lower() in _COMPANY_SPECIFIC_SOURCE_TYPES:
        return True
    if str(rec.get("entity_match") or "").lower() == "exact" and rec.get("company_mentioned") is True:
        return True
    return False


def sanitize_foresight_record(
    record: dict,
    *,
    report_date: date | None = None,
    domain: str = "",
    company_name: str = "",
) -> dict:
    if callable(record):
        print("   ⚠️ [Foresight Hygiene] sanitize_foresight_record received callable; skipping")
        return {}
    if not isinstance(record, dict):
        return record
    rec = deepcopy(record)
    report_date = report_date or date.today()

    is_company_evidence = _is_company_specific_record(rec)

    if not is_company_evidence:
        # Generic foresight/regulatory/trend context — stamp foresight metadata defaults.
        rec.setdefault("origin", "foresight")
        rec.setdefault("claim_type", FORESIGHT_CLAIM_TYPE)
        rec.setdefault("allowed_claim_types", [FORESIGHT_CLAIM_TYPE])
        rec.setdefault("company_specific", False)
        rec.setdefault("requires_company_specific_corroboration", True)
        rec.setdefault("modal_language_required", True)
        rec.setdefault("forbidden_as_current_company_fact", True)
        rec.setdefault("not_evidence_of_deployment", True)
        rec.setdefault("scope", "external-trend")
    else:
        # Company-specific evidence: preserve/restore company-fact metadata.
        # Text sanitization still runs below, but foresight-only flags must NOT be stamped.
        rec["company_specific"] = True
        rec["generation_eligible"] = True
        # Correct claim_type if a previous (buggy) sanitizer pass wrongly set foresight_context.
        if rec.get("claim_type") in (FORESIGHT_CLAIM_TYPE, REGULATORY_CLAIM_TYPE, None, ""):
            rec["claim_type"] = rec.get("original_claim_type") or "company_fact"
        # Correct scope if wrongly set to external-trend.
        if rec.get("scope") in ("external-trend", None, ""):
            rec["scope"] = "company-specific"
        # Remove foresight-only flags that are inappropriate for proven company evidence.
        rec.pop("not_evidence_of_deployment", None)
        rec.pop("forbidden_as_current_company_fact", None)
        rec.pop("modal_language_required", None)

    rec["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
    if domain:
        rec.setdefault("domain_context", domain)

    # Split description into source_says / possible_relevance if not already split
    description = rec.get("description") or rec.get("snippet") or rec.get("summary") or ""
    if description and not rec.get("source_says"):
        source_says, possible_relevance = _split_source_and_relevance(description)
        # source_says describes what the external source says — preserve regulatory language
        # like "GDPR requires…" by using deployment-only sanitization.
        rec["source_says"] = sanitize_source_description(source_says, report_date)
        if possible_relevance:
            # possible_relevance is a company-impact hypothesis — full modal softening applies.
            rec["possible_relevance"] = sanitize_foresight_text(possible_relevance, report_date)
    elif rec.get("source_says"):
        rec["source_says"] = sanitize_source_description(str(rec["source_says"]), report_date)

    if rec.get("possible_relevance"):
        rec["possible_relevance"] = sanitize_foresight_text(
            str(rec["possible_relevance"]), report_date
        )

    # Source/evidence description fields — preserve factual "requires" / "must" language
    # that describes what a regulation or trend source says.
    for key in ("description", "snippet", "summary"):
        if isinstance(rec.get(key), str):
            rec[key] = sanitize_source_description(rec[key], report_date)

    # Company-impact fields — full modal softening: "requires this company to" becomes
    # "may require this company to", "must invest" becomes "should evaluate whether…".
    for key in ("insight", "impact", "recommended_action"):
        if isinstance(rec.get(key), str):
            rec[key] = sanitize_foresight_text(rec[key], report_date)

    # Enforce source_says company-neutrality
    if company_name:
        rec = _enforce_source_says_neutrality(rec, company_name, report_date=report_date)

    # Collect residual warnings (post-sanitization — should be empty on clean records)
    warnings: list[str] = []
    for key in (
        "description", "snippet", "summary",
        "source_says", "possible_relevance",
        "insight", "impact", "recommended_action",
    ):
        if isinstance(rec.get(key), str):
            warnings.extend(validate_foresight_text(rec[key], report_date))
    rec["foresight_language_warnings"] = sorted(set(warnings))
    rec["foresight_sanitized"] = True
    return rec


def annotate_foresight_signal(
    signal: dict, *, theme: str = "", domain: str = "", company_name: str = ""
) -> dict:
    sig = sanitize_foresight_record(signal, domain=domain, company_name=company_name)
    if isinstance(sig, dict) and theme:
        sig.setdefault("fp_theme", theme)
        sig.setdefault("_kb_theme", theme)
    return sig


def annotate_foresight_link(
    link: dict, *, domain: str = "", company_name: str = ""
) -> dict:
    return sanitize_foresight_record(link, domain=domain, company_name=company_name)


def sanitize_foresight_collection(
    items: list,
    *,
    report_date: date | None = None,
    domain: str = "",
    company_name: str = "",
) -> list:
    if not isinstance(items, list):
        return []
    out = []
    dropped = 0
    for item in items:
        if isinstance(item, dict):
            cleaned = sanitize_foresight_record(
                item, report_date=report_date, domain=domain, company_name=company_name
            )
            if isinstance(cleaned, dict):
                out.append(cleaned)
        else:
            dropped += 1
    if dropped:
        print(f"   ⚠️ [Foresight Hygiene] Dropped {dropped} non-dict item(s) during collection sanitation")
    return out


def sanitize_report_foresight_fields(
    data: dict,
    *,
    report_date: date | None = None,
    domain: str = "",
    company_name: str = "",
) -> dict:
    if callable(data):
        print("   ⚠️ [Foresight Hygiene] sanitize_report_foresight_fields received callable; skipping")
        return {}
    if not isinstance(data, dict):
        return data
    out = deepcopy(data)
    report_date = report_date or date.today()

    for field in [
        "foresight_links", "foresight_evidence", "deep_dive_findings",
        "source_cards", "dimension_forces", "future_insights", "signals", "evidence_links",
    ]:
        if isinstance(out.get(field), list):
            out[field] = sanitize_foresight_collection(
                out[field], report_date=report_date, domain=domain, company_name=company_name
            )

    for field in ["future_insight", "future_insights_text", "domain_force_dossier", "climate_exposure"]:
        if isinstance(out.get(field), str):
            out[field] = sanitize_foresight_text(out[field], report_date)

    out["foresight_sanitizer_version"] = FORESIGHT_SANITIZER_VERSION
    out["foresight_sanitized"] = True
    return out


def validate_clean_by_construction(data: dict, *, company_name: str = "", report_date: date | None = None) -> list[str]:
    """Validate that newly generated source cards and Future Insight are clean before save."""
    issues: list[str] = []
    if not isinstance(data, dict):
        return ["Generated foresight payload is not a dict"]
    report_date = report_date or date.today()
    for field in ["future_insight", "future_insights_text", "climate_exposure", "domain_force_dossier"]:
        if isinstance(data.get(field), str):
            issues.extend([f"{field}: {w}" for w in validate_foresight_text(data[field], report_date)])
    variants = _company_name_variants(company_name) if company_name else []
    for list_field in ["foresight_links", "foresight_evidence", "source_cards", "dimension_forces", "future_insights"]:
        for idx, rec in enumerate(data.get(list_field) or []):
            if not isinstance(rec, dict):
                continue
            if rec.get("company_specific") is False and rec.get("source_says"):
                if any(re.search(re.escape(v), rec["source_says"], re.I) for v in variants):
                    issues.append(f"{list_field}[{idx}]: source_says contains company name in context-only record")
    return issues
