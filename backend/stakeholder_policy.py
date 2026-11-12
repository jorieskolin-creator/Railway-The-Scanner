"""Shared stakeholder eligibility, seniority and demand ranking.

Hierarchy reference: scanner_two.py at 34b7f92 (before the November model and
Coresignal changes). Keep six tiers and company-size manager restrictions.
Provider snapshots establish role context, never confirmed buying intent.
"""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlparse

ROLE_DEMAND_MAP = {
    "ai_data_cloud_platform": {
        "keywords": ("ai", "tekoäly", "genai", "copilot", "agent", "data", "analytics", "cloud", "azure", "aws", "gcp", "platform", "devops", "architecture", "digital", "product", "engineering"),
        "roles": ("CIO", "CTO", "CDO", "Chief Data Officer", "Head of Data", "Head of AI", "Enterprise Architect", "IT Director", "Head of Platform"),
    },
    "erp_workplace_cyber": {
        "keywords": ("erp", "sap", "dynamics", "salesforce", "oracle", "workday", "m365", "cyber", "security", "nis2", "dora", "iam", "soc", "workplace"),
        "roles": ("CIO", "CISO", "Head of Infrastructure", "Head of Applications", "IT Director", "Procurement Director", "Chief Information Security Officer"),
    },
    "operations_manufacturing_automation": {
        "keywords": ("manufacturing", "production", "operations", "automation", "rpa", "process mining", "supply", "logistics", "factory", "plant", "tuotanto"),
        "roles": ("COO", "Chief Operating Officer", "Operations Director", "Production Director", "Head of Digitalization", "Automation Lead", "Supply Chain Director"),
    },
    "finance_risk_regulatory": {
        "keywords": ("finance", "risk", "compliance", "regulation", "csrd", "esrs", "dora", "audit", "governance", "investor", "margin", "cost"),
        "roles": ("CFO", "Chief Financial Officer", "Risk Director", "Compliance Director", "Legal Counsel", "General Counsel", "Head of Governance"),
    },
    "hr_workforce_culture": {
        "keywords": ("hr", "people", "talent", "workforce", "culture", "employee", "training", "skills", "reskilling", "recruit", "career"),
        "roles": ("CHRO", "HR Director", "People Director", "Talent Director", "Head of People", "Learning and Development Director"),
    },
    "strategy_growth_customer": {
        "keywords": ("strategy", "growth", "customer", "sales", "market", "business unit", "transformation", "digital", "innovation", "service"),
        "roles": ("CEO", "Chief Executive Officer", "Chief Strategy Officer", "Strategy Director", "Business Unit Director", "Commercial Director", "Customer Experience Director"),
    },
}
TOP_MANAGEMENT_ROLES = (
    "CEO", "Chief Executive Officer", "CFO", "Chief Financial Officer", "CIO",
    "CTO", "COO", "CHRO", "CISO", "Chief Digital Officer", "Chief Data Officer",
    "Strategy Director", "Business Development Director",
)
TITLE_GROUP_HINTS = {
    "ai_data_cloud_platform": (
        "cio", "cto", "cdo", "data", "ai", "application", "applications",
        "platform", "architecture", "architect", "digital", "technology",
        "information", "it", "software", "cloud", "product", "engineering",
        "tietohallinto", "tietohallintojohtaja", "teknologiajohtaja", "tuote",
    ),
    "erp_workplace_cyber": (
        "ciso", "security", "cyber", "infrastructure", "applications",
        "application", "it", "procurement", "sourcing", "workplace",
        "enterprise systems", "erp", "sap",
    ),
    "operations_manufacturing_automation": (
        "coo", "operations", "operation", "production", "maintenance",
        "equipment", "infrastructure", "airport development", "automation",
        "supply", "logistics", "network",
    ),
    "finance_risk_regulatory": (
        "cfo", "finance", "financial", "business control", "controller",
        "risk", "compliance", "legal", "governance", "audit", "talousjohtaja", "talous",
    ),
    "hr_workforce_culture": (
        "chro", "hr", "human resources", "people", "talent", "learning",
        "workforce", "culture", "henkilöstöjohtaja", "henkilöstö", "henkilostojohtaja",
    ),
    "strategy_growth_customer": (
        "ceo", "strategy", "business development", "commercial", "sales",
        "customer", "marketing", "growth", "digital", "product", "strategy",
        "liiketoiminta", "myynti", "markkinointi", "strategia", "myyntijohtaja", "markkinointijohtaja", "liiketoimintajohtaja",
    ),
}



def contains_term(text: str, term: str) -> bool:
    """Match words/phrases, never AI inside trainer or CTO inside inspector."""
    return bool(re.search(r"(?<!\w)" + re.escape(term).replace(r"\ ", r"\s+") + r"(?!\w)", str(text), re.I))


def is_historical_role(title: str) -> bool:
    return bool(re.search(r"\b(?:retired|former|emeritus|previous|past|alumni|entinen|eläkkeellä|elakkeella)\b|\bret\.(?=\W|$)|\bex[-\s]", str(title), re.I))


# Evaluate VP phrases before President so EVP/VP can never become Tier 1.
SENIORITY_TIERS = (
    (1, r"CEO|COO|Chief\s+(?:Executive|Operating)\s+Officer|Chair(?:man|woman|person)|Chair\s+(?:of\s+)?(?:the\s+)?Board|^Chair$|Founder|Co-Founder|Toimitusjohtaja|Maajohtaja|Managing\s+Director|Country\s+Manager|Group\s+Executive|General\s+Manager|Pääjohtaja|Hallituksen\s+puheenjohtaja"),
    (2, r"CTO|CIO|CDO|CPO|CFO|CHRO|CMO|CISO|CSO|Chief\s+(?:\w+[\s&-]+){1,5}Officer|Executive\s+Vice\s+President|EVP|Talousjohtaja|Tietohallintojohtaja|Teknologiajohtaja"),
    (3, r"SVP|Senior\s+Vice\s+President|VP|Vice\s+President|Board\s+Member|Member\s+of\s+(?:the\s+)?Board|Hallituksen\s+jäsen"),
    (4, r"Director|[\w]*johtaja"),
    (5, r"Head\s+of"),
    (6, r"Manager|Lead|[\w]*päällikkö|[\w]*paallikko"),
)


def seniority_tier(title: str) -> int:
    title = str(title or "")
    # Assistant to the CEO and assistant managers are not executive leadership.
    if re.search(r"\b(?:assistant|intern|trainee|trainer|retail|cashier)\b", title, re.I):
        return 7
    for tier, pattern in SENIORITY_TIERS:
        if re.search(r"\b(?:" + pattern + r")\b", title, re.I):
            return tier
    return 1 if contains_term(title, "President") else 7


def demand_profile(company: dict) -> dict:
    values = [company.get(k) for k in (
        "summary_text", "general_summary", "pitch_text", "domain_force_dossier",
        "domain", "archetype", "why_proposals", "what_proposals", "strategic_foresight",
    )]
    text = " ".join(json.dumps(v, ensure_ascii=False)[:12000] for v in values if v)
    groups, reasons, roles = [], [], list(TOP_MANAGEMENT_ROLES)
    for group, spec in ROLE_DEMAND_MAP.items():
        matches = [kw for kw in spec["keywords"] if contains_term(text, kw)]
        if matches:
            groups.append(group)
            reasons.append({"group": group, "matched_keywords": matches[:8]})
            roles.extend(spec["roles"])
    if not groups:
        groups = ["strategy_growth_customer"]
        reasons = [{"group": groups[0], "matched_keywords": ["default_top_management"]}]
    return {"groups": groups, "roles": list(dict.fromkeys(roles)), "reasons": reasons}


def role_context(title: str, profile: dict) -> dict:
    tier = seniority_tier(title)
    historical = is_historical_role(title)
    groups = [g for g, hints in TITLE_GROUP_HINTS.items() if any(contains_term(title, h) for h in hints)]
    relevant = [g for g in groups if g in profile.get("groups", [])]
    generalist = tier == 1 or bool(re.search(r"\b(?:board|hallitus|hallituksen)\b", title, re.I))
    if generalist:
        relevant = [g for g in ("strategy_growth_customer", "finance_risk_regulatory", "ai_data_cloud_platform") if g in profile.get("groups", [])]
    # C-suite/EVP are enterprise leaders even without a detected functional theme.
    matched = not historical and (tier <= 2 or generalist or (tier <= 6 and bool(relevant)))
    reason = ("historical_or_retired_role" if historical else
              "unrecognized_or_non_leadership_role" if tier == 7 else
              "executive_generalist" if generalist else
              "role_matches_title_specific_demand" if relevant else
              "enterprise_executive" if tier <= 2 else "unrelated_role_family")
    return {"matched": matched, "reason": reason, "groups": relevant or (groups if tier <= 2 else []),
            "historical": historical, "seniority_tier": tier, "demand_relevance": len(relevant)}


def search_rounds(profile: dict) -> list[tuple[str, dict]]:
    def phrases(terms):
        return {"bool": {"should": [{"match_phrase": {"active_experience_title": t}} for t in dict.fromkeys(terms)], "minimum_should_match": 1}}
    executive_terms = (
        "CEO", "COO", "CFO", "CIO", "CTO", "CDO", "CPO", "CMO", "CHRO", "CISO", "CSO",
        "chief", "executive vice president", "EVP", "president", "chair", "chairman", "board member",
        "managing director", "founder", "country manager", "general manager", "toimitusjohtaja",
        "talousjohtaja", "tietohallintojohtaja", "maajohtaja", "hallituksen puheenjohtaja",
    )
    rounds = [("executive_leadership", phrases(executive_terms))]
    leadership = phrases(("director", "head", "vice president", "VP", "SVP", "manager", "lead", "johtaja", "päällikkö", "paallikko", "myyntijohtaja", "markkinointijohtaja", "liiketoimintajohtaja", "henkilöstöjohtaja", "henkilostojohtaja", "tietohallintojohtaja", "teknologiajohtaja", "talousjohtaja"))
    for group in profile.get("groups", []):
        if group not in TITLE_GROUP_HINTS:
            continue
        # Both a leadership title and this demand's role family are required.
        rounds.append((group, {"bool": {"must": [leadership, phrases(TITLE_GROUP_HINTS[group])]}}))
    return rounds


def person_key(person: dict) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(person.get("name") or "")).casefold().split())


def person_aliases(person: dict) -> set[str]:
    aliases = {"name:" + person_key(person)} if person_key(person) else set()
    url = str(person.get("public_profile_url") or person.get("source") or "")
    parsed = urlparse(url)
    if parsed.hostname and (parsed.hostname.lower() == "linkedin.com" or parsed.hostname.lower().endswith(".linkedin.com")) and parsed.path.startswith("/in/"):
        aliases.add("profile:" + parsed.path.rstrip("/").casefold())
    pid = person.get("provider_person_id")
    if pid and person.get("provider") == "coresignal":
        aliases.add("coresignal:" + str(pid))
    return aliases


def employee_count(data: dict) -> int | None:
    for key in ("employees_count", "employee_count", "employees", "total_employees", "size_range"):
        raw = data.get(key)
        if raw is None or isinstance(raw, (dict, list, bool)):
            continue
        match = re.match(r"^\s*([\d, ]+)", str(raw))
        if match:
            value = int(re.sub(r"\D", "", match[1]) or "0")
            if value:
                return value
    return None


def candidate_reason(person: dict, profile: dict) -> str | None:
    name = person_key(person)
    role = str(person.get("role") or person.get("position") or person.get("title") or "")
    if len(name.split()) < 2 or re.search(r"\b(?:unknown|unnamed|unspecified|not found|role-level|source-bound)\b", name):
        return "invalid_name"
    if is_historical_role(role) or person.get("historical") is True:
        return "historical_or_retired_role"
    if person.get("current_affiliation_verified") is not True:
        return "current_affiliation_unverified"
    if person.get("blocked") is True or person.get("wrong_entity") is True or person.get("allowed_use") in {"blocked", "internal_only"}:
        return "blocked_or_internal_only"
    ctx = role_context(role, profile)
    return None if ctx["matched"] else ctx["reason"]


def rank_stakeholders(people: list[dict], profile: dict, *, employees: int | None = None, limit: int | None = None) -> tuple[list[dict], dict]:
    """Filter, resolve identity conflicts, rank the complete pool, then cap it.

    A stale retired/left-company record suppresses alternate titles unless an
    active Employee API match supplies stronger current-affiliation evidence.
    """
    rejected: dict[str, int] = {}
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    conflicts = set()
    for p in people:
        if is_historical_role(str(p.get("role") or p.get("position") or "")) or p.get("historical") or p.get("current_affiliation_verified") is False:
            conflicts.update(person_aliases(p))
    eligible = []
    for p in people:
        reason = candidate_reason(p, profile)
        if not reason and person_aliases(p) & conflicts and p.get("affiliation_basis") != "active_company_id":
            reason = "conflicting_current_affiliation"
        if reason:
            reject(reason)
            continue
        role = str(p.get("role") or p.get("position") or p.get("title") or "")
        ctx = role_context(role, profile)
        eligible.append({**p, "role": role, "position": role, "seniority_tier": ctx["seniority_tier"],
                         "demand_relevance": ctx["demand_relevance"], "matched_demand_groups": ctx["groups"]})
    # Prefer authoritative active-employer records during cross-source deduplication.
    eligible.sort(key=lambda p: (p.get("affiliation_basis") != "active_company_id", p["seniority_tier"], -p["demand_relevance"], person_key(p)))
    deduped, seen = [], set()
    for p in eligible:
        aliases = person_aliases(p)
        if aliases & seen:
            reject("duplicate_person")
            seen.update(aliases)
            continue
        seen.update(aliases)
        deduped.append(p)
    senior_count = sum(p["seniority_tier"] < 6 for p in deduped)
    ranked = []
    for p in deduped:
        if p["seniority_tier"] == 6 and employees and (employees >= 5000 or (employees >= 200 and senior_count >= 3)):
            reject("manager_below_company_size_threshold")
        else:
            ranked.append(p)
    ranked.sort(key=lambda p: (p["seniority_tier"], -p["demand_relevance"], person_key(p)))
    retained = len(ranked)
    if limit is not None:
        ranked = ranked[:max(0, limit)]
    return ranked, {"candidates_received": len(people), "rejected_by_reason": rejected,
                    "candidates_retained": retained, "display_limit_excluded": retained - len(ranked), "candidates_selected": len(ranked)}
