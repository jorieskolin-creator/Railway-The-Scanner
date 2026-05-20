"""
source_quality.py — Shared source credibility assessment for all scan pipelines.

Evaluates the QUALITY and AUTHORITY of evidence sources, not just URL liveness.
Used by scanner_two.py (core scanner) and strategic_foresight.py (foresight signals).

Key distinction:
- "verified" = URL is reachable (HTTP 200) — says NOTHING about quality
- "source_credibility" = domain authority assessment — high/medium/low/unknown

Source Management (manage_evidence_sources):
- Caps evidence at MAX_EVIDENCE_SOURCES (15) with credibility-tiered eviction.
- "Gold Standard" detection: 15 high-credibility verified sources → skip new search.
"""

import re
from urllib.parse import urlparse
from datetime import date, datetime
from typing import Dict, Iterable, List, Tuple

from entity_identity import (
    ENTITY_SCOPE_CANONICAL_GROUP,
    ENTITY_SCOPE_FORMER_NAME,
    ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
    ENTITY_SCOPE_PARENT_OWNER,
    ENTITY_SCOPE_SUBSIDIARY,
    ENTITY_SCOPE_WRONG_ENTITY,
    ENTITY_SCOPE_UNKNOWN,
    apply_entity_scope,
    assess_entity_match,
)


# -----------------------------------------------------------------------------
# Source context classification constants
# -----------------------------------------------------------------------------
# These constants must be defined near the top of the module because scan-time
# helpers may call is_company_specific_evidence() / is_context_only_source()
# early in the pipeline. Keep names stable; scanner_two.py and
# strategic_foresight.py may import helpers that rely on these globals.
COMPANY_SPECIFIC_SOURCE_TYPES = {
    "company_report",
    "annual_report",
    "financial_report",
    "quarterly_report",
    "company_press_release",
    "investor_report",
    "company_website",
    "case_study",
    "company_blog",
    "company_news",
    "company_filing",
    "official_company_source",
}

CONTEXT_ONLY_SOURCE_TYPES = {
    "foresight",
    "foresight_report",
    "trend_report",
    "regulation",
    "regulatory_context",
    "analyst",
    "analyst_report",
    "market_report",
    "industry_report",
    "research_report",
    "think_tank",
    "policy_report",
}

ANALYSIS_SUPPORT_SUPPORTED = "supported"
ANALYSIS_SUPPORT_PARTIAL = "partial"
ANALYSIS_SUPPORT_UNSUPPORTED = "unsupported"
ANALYSIS_SUPPORT_UNCHECKED = "unchecked"
ANALYSIS_SUPPORT_UNAVAILABLE = "unavailable"

PROMOTION_GENERATION_ELIGIBLE = "generation_eligible"
PROMOTION_DISPLAY_ONLY = "display_only"
PROMOTION_EXCLUDED = "excluded"


# === DOMAIN AUTHORITY TIERS ===

# Third-party financial / business data aggregators. These are HIGH credibility but
# they are NOT the company's own filings — financial data sourced from these domains
# must be attributed as third-party verification, never as "the company's annual report".
FINANCIAL_AGGREGATOR_DOMAINS = {
    "finder.fi", "asiakastieto.fi", "profinder.fi",
    "kauppalehti.fi", "talouselama.fi", "arvopaperi.fi",
    "bloomberg.com", "reuters.com", "ft.com",
    "nasdaqomxnordic.com", "marketscreener.com",
}

# Tier 1: Authoritative sources — research institutions, major analysts, gov agencies
HIGH_CREDIBILITY_DOMAINS = {
    # Research & analyst firms
    "gartner.com", "forrester.com", "idc.com", "mckinsey.com", "bcg.com",
    "bain.com", "deloitte.com", "pwc.com", "ey.com", "kpmg.com",
    "accenture.com", "hbr.org", "weforum.org",
    # Financial/business news (editorial standards)
    "reuters.com", "ft.com", "bloomberg.com", "economist.com", "wsj.com",
    "cnbc.com", "bbc.com", "nytimes.com",
    # Academic & government
    "nature.com", "sciencedirect.com", "arxiv.org", "ssrn.com",
    "ieee.org", "acm.org", "nist.gov", "europa.eu", "un.org",
    "oecd.org", "imf.org", "worldbank.org", "ec.europa.eu",
    # Nordic business press (high editorial standards)
    "kauppalehti.fi", "talouselama.fi", "hs.fi", "yle.fi",
    "di.se", "e24.no",
    # Tech industry credible sources
    "techcrunch.com", "arstechnica.com", "theregister.com",
    "zdnet.com", "infoworld.com", "computerworld.com",
    # Verified company data
    "linkedin.com", "github.com", "glassdoor.com", "stackshare.io",
    "crunchbase.com", "pitchbook.com",
    # Finnish institutional
    "hankintailmoitukset.fi", "prh.fi", "vtt.fi", "aalto.fi",
    "theseus.fi",
    # Finnish institutional — extended coverage
    "greatplacetowork.fi",  # Great Place to Work Finland — employee trust surveys
    "etla.fi",              # ETLA — Research Institute of the Finnish Economy
    "finder.fi",            # Finder.fi — official Finnish company financial data
    "openprocurement.fi",   # Finnish public procurement transparency data
    "tutkihankintoja.fi",   # Tutki Hankintoja — public procurement spending data
    "espacenet.com",        # European Patent Office — patent database
    "stat.fi",              # Statistics Finland — national statistics authority
    "oikotie.fi",           # Oikotie — major Finnish job/services platform (Sanoma)
    "avoimuusrekisteri.fi",  # Finnish Transparency Register — lobbying disclosures
    # Strategic foresight organizations
    "millennium-project.org",   # Global Futures Intelligence System, Delphi methodology
    "iftf.org",                 # Institute for the Future — ex-RAND, 50+ year track record
    "cifs.dk",                  # Copenhagen Institute for Futures Studies — European megatrends
    "rand.org",                 # RAND Corporation — invented Delphi, foundational foresight
    "chathamhouse.org",         # Royal Institute of Intl Affairs — geopolitics think tank
    "clubofrome.org",           # Club of Rome — systems dynamics, planetary boundaries
    "oxfordmartin.ox.ac.uk",    # Oxford Martin School — cross-disciplinary global challenges
    "cser.ac.uk",               # Centre for Study of Existential Risk, Cambridge
    # Foresight-adjacent academic & think tanks
    "wfsf.org",                 # World Futures Studies Federation
    "apf.org",                  # Association of Professional Futurists
    "emerald.com",              # Foresight journal (peer-reviewed)
    "iea.org",                  # International Energy Agency
    "irena.org",                # International Renewable Energy Agency
    "who.int",                  # World Health Organization
    "bis.org",                  # Bank for International Settlements
    "fao.org",                  # Food and Agriculture Organization
    "isg-one.com",              # ISG — IT services analyst
    # Intelligence, geopolitical & built-environment foresight
    "dni.gov",                  # National Intelligence Council — Global Trends reports
    "strategicforesight.com",   # Strategic Foresight Group — peace, water diplomacy, Global South
    "arup.com",                 # Arup — future of cities, built environment, engineering foresight
    "wbcsd.org",                # World Business Council for Sustainable Development — Vision 2050
    "isi.fraunhofer.de",        # Fraunhofer ISI — European tech forecasting, innovation systems
    "pewresearch.org",          # Pew Research Center — demographics, public opinion, social trends
    "soif.org.uk",              # School of International Futures — policy foresight, governance
    "futures.hawaii.edu",       # Hawaii Research Center for Futures Studies — futures methodology
    # Climate, Earth systems & ecological foresight
    "sitra.fi",                 # Sitra (Finnish Innovation Fund) — megatrends, circular economy
    "ipcc.ch",                  # IPCC — definitive climate science consensus, SSP scenarios
    "climate.nasa.gov",         # NASA Climate — satellite Earth data, physical climate modeling
    "wmo.int",                  # World Meteorological Organization — extreme weather, climate state
    "pik-potsdam.de",           # Potsdam Institute — climate tipping points, Earth system boundaries
    "stockholmresilience.org",  # Stockholm Resilience Centre — Planetary Boundaries framework
    "iiasa.ac.at",              # IIASA — complex systems analysis, integrated assessment models
    "wri.org",                  # World Resources Institute — resource tracking, supply chain risk
    "tyndall.ac.uk",            # Tyndall Centre — decarbonisation, climate justice, carbon budgets
    "futureearth.org",          # Future Earth — sustainability science, 10 New Insights in Climate
    # Academic institutions & research centres (from curated source analysis)
    "hai.stanford.edu",         # Stanford HAI — human-centred AI research
    "turing.ac.uk",             # Alan Turing Institute — AI and data science
    "brookings.edu",            # Brookings Institution — policy research
    "carnegieendowment.org",    # Carnegie Endowment — international peace
    "csis.org",                 # Center for Strategic and International Studies
    "iiss.org",                 # International Institute for Strategic Studies
    "sipri.org",                # Stockholm International Peace Research Institute
    "lse.ac.uk",                # London School of Economics
    "oii.ox.ac.uk",             # Oxford Internet Institute
    "bruegel.org",              # Bruegel — European economic policy
    "piie.com",                 # Peterson Institute for International Economics
    "belfercenter.org",         # Belfer Center, Harvard — security
    "hks.harvard.edu",          # Harvard Kennedy School — public policy
    "sloanreview.mit.edu",      # MIT Sloan Management Review
    "reutersinstitute.politics.ox.ac.uk",  # Reuters Institute for Journalism
    "bennettinstitute.cam.ac.uk",  # Bennett Institute, Cambridge — digital policy
    "ed.stanford.edu",          # Stanford Graduate School of Education
    # Intergovernmental & UN agencies (additions)
    "unep.org",                 # UN Environment Programme
    "ilo.org",                  # International Labour Organization
    "unctad.org",               # UN Conference on Trade and Development
    "wto.org",                  # World Trade Organization
    "unfccc.int",               # UN Framework Convention on Climate Change
    "ipbes.net",                # Intergovernmental Science-Policy Platform on Biodiversity
    "iom.int",                  # International Organization for Migration
    "itu.int",                  # International Telecommunication Union
    "unido.org",                # UN Industrial Development Organization
    "unhabitat.org",            # UN Habitat — urban development
    "unhcr.org",                # UN Refugee Agency
    "undp.org",                 # UN Development Programme
    "population.un.org",        # UN Population Division
    "unwater.org",              # UN Water
    "en.unesco.org",            # UNESCO
    "imo.org",                  # International Maritime Organization
    "cbd.int",                  # UN Convention on Biological Diversity
    # Specialist research & policy bodies
    "science.org",              # Science/AAAS — flagship journal
    "thelancet.com",            # The Lancet — medical research
    "royalsociety.org",         # Royal Society — UK national academy
    "aaas.org",                 # American Association for Advancement of Science
    "erc.europa.eu",            # European Research Council
    "enisa.europa.eu",          # EU Agency for Cybersecurity
    "ecdc.europa.eu",           # European Centre for Disease Prevention
    "ecb.europa.eu",            # European Central Bank
    "hybridcoe.fi",             # Hybrid CoE Helsinki — hybrid threats
    "nato.int",                 # NATO — collective security
    "iucn.org",                 # International Union for Conservation of Nature
    "rmi.org",                  # Rocky Mountain Institute — clean energy
    "c40.org",                  # C40 Cities — climate-resilient cities
    "ellenmacarthurfoundation.org",  # Ellen MacArthur — circular economy
    "nesta.org.uk",             # NESTA — innovation foundation
    "fair.work",                # Fairwork Foundation — platform labour
    "theodi.org",               # Open Data Institute — data governance
    "oxfordenergy.org",         # Oxford Institute for Energy Studies
    "ember-climate.org",        # Ember — electricity data analytics
    "itf-oecd.org",             # International Transport Forum
    "theicct.org",              # ICCT — clean transportation
    "cgiar.org",                # CGIAR — agricultural research
    "ifpri.org",                # IFPRI — food policy research
    "healthdata.org",           # IHME — global health data
    "v-dem.net",                # V-Dem — democracy data
    "transparency.org",         # Transparency International
    "ineteconomics.org",        # Institute for New Economic Thinking
    "ssir.org",                 # Stanford Social Innovation Review
    "nuffieldbioethics.org",    # Nuffield Council on Bioethics
    "adalovelaceinstitute.org", # Ada Lovelace Institute — AI ethics
    "tnfd.global",              # Taskforce on Nature-related Financial Disclosures
    "sciencebasedtargets.org",  # Science Based Targets initiative
    "cdp.net",                  # CDP — corporate environmental disclosure
    "globalreporting.org",      # GRI — ESG reporting standards
    "wellcome.org",             # Wellcome Trust — health research
    "gatesfoundation.org",      # Gates Foundation — global health
    "prb.org",                  # Population Reference Bureau
    "knowledgeworks.org",       # KnowledgeWorks — education foresight
    "educause.edu",             # EDUCAUSE — EdTech higher education
    "cipd.org",                 # CIPD — people management
    "ccl.org",                  # Center for Creative Leadership
    "unwto.org",                # World Tourism Organization
    "worldleisure.org",         # World Leisure Organization
    "atlanticcouncil.org",      # Atlantic Council — transatlantic affairs
    "ecfr.eu",                  # European Council on Foreign Relations
    "cfr.org",                  # Council on Foreign Relations
}

# Tier 2: Low credibility — blogs, press release wires, content farms, marketing
LOW_CREDIBILITY_DOMAINS = {
    # Blogging platforms (anyone can publish)
    "medium.com", "wordpress.com", "blogspot.com", "substack.com",
    "wixsite.com", "weebly.com", "squarespace.com", "ghost.io",
    "hashnode.dev", "dev.to", "hubpages.com", "tumblr.com",
    # Press release wires (paid placement, not editorial)
    "pr.com", "prnewswire.com", "businesswire.com", "globenewswire.com",
    "prweb.com", "accesswire.com", "cision.com", "einpresswire.com",
    # Content farms & low-quality aggregators
    "buzzfeed.com", "ehow.com", "wikihow.com",
    "about.com", "suite101.com", "articlesbase.com",
    # SEO/marketing content mills
    "entrepreneur.com", "inc.com", "forbes.com",  # contributor model = no editorial gate
    # Outsourcing/vendor marketplace sites (promotional, not analytical)
    "clutch.co", "goodfirms.co", "toptal.com",
    "upwork.com", "fiverr.com",
    # Generic tech rewrite sites
    "techradar.com", "makeuseof.com", "howtogeek.com",
    # Social media (not evidence sources)
    "reddit.com", "quora.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "tiktok.com",
    # Wikipedia (useful for context, not as primary evidence)
    "wikipedia.org",
}

# Patterns that indicate low-quality domains (regex-free, suffix matching)
LOW_CREDIBILITY_PATTERNS = [
    # Regional tech blogs with no editorial standards
    "tech.news", "technews.", "techblog.",
    # Outsourcing marketplace/directory sites
    "outsource", "offshoring", "nearshore",
    # Generic listicle/directory sites
    "top10", "best-of", "ranking",
    # Country-specific tech content farms
    "techpoint.", "techcabal.", "techinasia.",
]


def _get_domain(url: str) -> str:
    """Extract clean domain from URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().replace("www.", "")
        return domain
    except Exception:
        return ""


def _domain_matches_set(domain: str, domain_set: set) -> bool:
    """Check if domain matches any entry in the set (exact or subdomain)."""
    if domain in domain_set:
        return True
    for d in domain_set:
        if domain.endswith("." + d):
            return True
    return False


def _domain_matches_patterns(domain: str) -> bool:
    """Check if domain matches low-credibility naming patterns."""
    for pattern in LOW_CREDIBILITY_PATTERNS:
        if pattern in domain:
            return True
    return False


def _is_institutional_tld(domain: str) -> bool:
    """Check if domain has a TLD associated with institutional credibility."""
    # Government TLDs (any country)
    if domain.endswith(".gov") or ".gov." in domain:
        return True
    # Education TLDs
    if domain.endswith(".edu") or ".edu." in domain:
        return True
    # Academic institution patterns (UK, AU, etc.)
    if ".ac." in domain:
        return True
    # Military
    if domain.endswith(".mil") or ".mil." in domain:
        return True
    # European/international institutional
    if domain.endswith(".int"):
        return True
    return False


def assess_source_credibility(url: str, source_type: str = "", company_domain: str = "") -> str:
    """
    Assess a single source URL's credibility tier.

    Returns: "high", "medium", or "low"

    Logic:
    1. Known high-credibility domain → "high"
    2. Known low-credibility domain or pattern → "low"
    3. Company's own domain (self-reporting) → "low"
    4. Institutional TLD (.gov, .edu, .ac.*, .mil, .int) → "high"
    5. Source type "academic"/"government"/"think_tank" on unknown domain → "medium"
    6. Everything else → "medium" (unknown ≠ low; low-credibility list catches bad sources)
    """
    domain = _get_domain(url)
    if not domain:
        # "No URL" means unknown provenance (Scanner analysis, training-data knowledge),
        # not an actively bad source.  "low" would trigger the admissibility gate and
        # silently block all URL-less Scanner findings.  Use "medium" so these findings
        # reach the evidence pool with an appropriate confidence penalty.
        return "medium"

    # Check high-credibility domains
    if _domain_matches_set(domain, HIGH_CREDIBILITY_DOMAINS):
        return "high"

    # Check low-credibility domains
    if _domain_matches_set(domain, LOW_CREDIBILITY_DOMAINS):
        return "low"

    # Check low-credibility patterns
    if _domain_matches_patterns(domain):
        return "low"

    # Self-reporting check: company citing its own website
    if company_domain:
        clean_company = company_domain.lower().replace("www.", "")
        if clean_company and (domain == clean_company or domain.endswith("." + clean_company)):
            return "low"

    # Institutional TLDs are inherently credible (gov, edu, academic, military)
    if _is_institutional_tld(domain):
        return "high"

    # If the AI labeled it as academic/government/think_tank, give benefit of doubt
    source_type_lower = (source_type or "").strip().lower()
    if source_type_lower in ("academic", "government", "think_tank"):
        return "medium"

    # Default: unknown source = medium credibility
    # Unknown domains that passed the low-credibility filters deserve benefit of doubt.
    # The LOW_CREDIBILITY_DOMAINS and LOW_CREDIBILITY_PATTERNS lists already catch
    # blogs, content farms, press releases, social media, and SEO sites.
    # Remaining unknowns are typically legitimate industry sites, news outlets,
    # professional organizations, or niche publications.
    return "medium"


# === SCOPE CLASSIFICATION ===
# A source can be perfectly credible AND describe only a narrow population.
# A master's thesis surveying 100 HR personnel is "high credibility, narrow scope".
# Treating it as a company-wide statistic — like the Wärtsilä case where a
# trepo.tuni.fi thesis was elevated to a portfolio-wide claim — is the bias
# this classifier is meant to flag. The LLM uses the resulting "scope": "narrow"
# field to apply scope qualifiers in the generated prose.

# Thesis / dissertation repositories — anything published here is, by definition,
# a single student's research with a constrained sample. Mostly Nordic, but the
# pattern generalises (DiVA, ProQuest, dspace, etc.).
THESIS_REPO_DOMAINS = {
    "trepo.tuni.fi",
    "aaltodoc.aalto.fi",
    "helda.helsinki.fi",
    "osuva.uwasa.fi",
    "theseus.fi",
    "jyx.jyu.fi",
    "lutpub.lut.fi",
    "uta.fi",
    "lauda.ulapland.fi",
    "diva-portal.org",
    "etda.libraries.psu.edu",
    "dspace.mit.edu",
    "escholarship.org",
    "proquest.com",
    "digitalcommons.usu.edu",
}

# Tokens in the URL path that indicate a thesis/dissertation regardless of host.
_THESIS_URL_TOKENS = (
    "thesis", "theses", "dissertation", "dissertations",
    "gradu", "pro_gradu", "pro-gradu",
    "bachelor", "masters", "master-s", "licentiate",
    "studentthesis", "student-thesis",
)

# Tokens in title/snippet that indicate a narrow-scope study (a sample, a pilot,
# a single department). Matched word-boundary on lowercase text.
_NARROW_SCOPE_PHRASES = (
    "master's thesis", "masters thesis", "doctoral thesis", "phd thesis",
    "doctoral dissertation", "case study of", "pilot study",
    "survey of", "survey conducted", "respondents", "interviewees",
    "participants", "questionnaire", "focus group",
    "(n=", "(n =", "n =", "sample of",
)


def _matches_url_token(url_lc: str, tokens) -> bool:
    """Return True when one of the tokens appears in the URL path/query."""
    for t in tokens:
        if t in url_lc:
            return True
    return False


def assess_source_scope(url: str = "", title: str = "", snippet: str = "") -> str:
    """Classify how broadly a source's claim applies.

    Returns:
        "narrow" — thesis, dissertation, pilot, single-department survey, or
                   any source whose population is explicitly limited.
        "broad"  — everything else (the safe default; we under-flag rather
                   than over-flag).

    Used by the General Summary and Detailed Forensic Report payload builders
    to attach a `"scope": "narrow"` flag to evidence items so the LLM can
    apply scope qualifiers in the prose ("In a study of HR personnel...",
    "In a 100-respondent survey...") instead of presenting departmental
    findings as company-wide truths.
    """
    url_lc = (url or "").lower()
    title_lc = (title or "").lower()
    snippet_lc = (snippet or "").lower()

    # 1. Known thesis-repository host → narrow.
    domain = _get_domain(url_lc)
    if domain and _domain_matches_set(domain, THESIS_REPO_DOMAINS):
        return "narrow"

    # 2. Thesis-style token in the URL path → narrow.
    if _matches_url_token(url_lc, _THESIS_URL_TOKENS):
        return "narrow"

    # 3. Narrow-scope phrasing in the title or snippet → narrow.
    haystack = title_lc + " " + snippet_lc
    for phrase in _NARROW_SCOPE_PHRASES:
        if phrase in haystack:
            return "narrow"

    return "broad"


# === PHASE 1: SOURCE ADMISSIBILITY / CLAIM-GOVERNANCE HELPERS ===
# These helpers are intentionally deterministic and conservative.  They do not
# decide whether a source is interesting; they decide how safely a generated
# summary may use the source.

SELF_REPORTED_SOURCE_TYPES = {
    "company", "company_website", "company_press_release", "press_release",
    "annual_report", "financial_report", "investor_relations", "self_reported",
}

FORESIGHT_SOURCE_TYPES = {
    "foresight", "foresight_report", "trend_report", "analyst_trend",
    "regulatory_context", "regulation", "law", "policy",
}

NARROW_SOURCE_TYPES = {
    "academic_thesis", "thesis", "dissertation", "case_study", "pilot",
    "survey", "employee_review", "job_posting", "github",
}

SOURCE_TYPE_ALLOWED_USES = {
    "company_financial_report": {"financial_fact", "company_event", "self_reported_capability"},
    "financial_report": {"financial_fact", "company_event", "self_reported_capability"},
    "annual_report": {"financial_fact", "company_event", "self_reported_capability"},
    "company_press_release": {"company_event", "self_reported_capability"},
    "press_release": {"company_event", "self_reported_capability"},
    "company_website": {"self_reported_capability", "company_event"},
    "academic_thesis": {"narrow_survey_result", "narrow_research_result"},
    "thesis": {"narrow_survey_result", "narrow_research_result"},
    "dissertation": {"narrow_research_result"},
    "employee_review": {"anecdotal_signal"},
    "job_posting": {"hiring_signal"},
    "github": {"technical_artifact"},
    "regulation": {"regulatory_context"},
    "regulatory_context": {"regulatory_context"},
    "foresight_report": {"foresight_context"},
    "foresight": {"foresight_context"},
    "trend_report": {"foresight_context"},
    "media": {"company_event", "third_party_observation"},
    "news": {"company_event", "third_party_observation"},
    "analyst": {"third_party_observation", "foresight_context"},
    "financial_data_aggregator": {"financial_fact", "third_party_observation"},
}


def infer_source_type(url: str = "", title: str = "", snippet: str = "", existing_type: str = "") -> str:
    """Infer a coarse source_type used by downstream admissibility rules."""
    explicit = (existing_type or "").strip().lower().replace(" ", "_")
    if explicit and explicit not in {"current", "general", "unknown", "other"}:
        return explicit

    domain = _get_domain(url or "")
    text = f"{title or ''} {snippet or ''} {url or ''}".lower()

    # CRITICAL: aggregator domain check MUST come before "annual report" text heuristic.
    # Otherwise a Finder.fi page that mentions "annual report" gets miscategorised as
    # the company's own filing — it's actually a third-party business database.
    if domain and _domain_matches_set(domain, FINANCIAL_AGGREGATOR_DOMAINS):
        return "financial_data_aggregator"
    if domain and _domain_matches_set(domain, THESIS_REPO_DOMAINS):
        return "academic_thesis"
    if _matches_url_token((url or "").lower(), _THESIS_URL_TOKENS):
        return "academic_thesis"
    if any(tok in text for tok in ["annual report", "financial statements", "interim report", "quarterly report", "investor relations"]):
        return "company_financial_report"
    if any(tok in text for tok in ["press release", "/media/news/", "/news/", "stock exchange release"]):
        return "company_press_release"
    if "github.com" in domain:
        return "github"
    if any(tok in domain for tok in ["glassdoor", "indeed", "kununu"]):
        return "employee_review"
    if any(tok in domain for tok in ["linkedin.com", "oikotie.fi"]):
        return "job_posting" if any(t in text for t in ["job", "career", "hiring", "rekry"]) else "media"
    if domain.endswith("europa.eu") or domain in {"eur-lex.europa.eu", "ec.europa.eu"}:
        return "regulation"
    if any(tok in text for tok in ["foresight", "megatrend", "trend report", "future of", "scenario"]):
        return "foresight_report"
    if domain:
        cred = assess_source_credibility(url, explicit)
        if cred == "high":
            return "analyst" if any(d in domain for d in ["gartner", "forrester", "idc", "mckinsey", "deloitte", "pwc", "accenture"]) else "news"
    return explicit or "unknown"


def infer_time_validity(link: dict) -> str:
    """Classify time validity from extracted_date/is_recent/future markers."""
    if not isinstance(link, dict):
        return "unknown"
    text = f"{link.get('title','')} {link.get('snippet','')}".lower()
    if any(w in text for w in ["will", "expected", "by 2030", "by 2027", "forecast", "scenario"]):
        # future-ish text does not automatically make the whole source future,
        # but it prevents treating it as a current deployment claim.
        return "future_or_foresight"
    if link.get("is_recent") is True:
        return "current"
    if link.get("is_recent") is False:
        return "historical"
    return "unknown"


_FINANCIAL_FILING_SOURCE_TYPES = {
    "company_financial_report", "financial_report", "annual_report",
    "quarterly_report", "investor_relations",
}

_A_TIER_SOURCE_TYPES = {
    "analyst", "analyst_report", "news", "media",
    "regulation", "regulatory_context",
    "financial_data_aggregator",  # Finder, Profinder, Bloomberg etc. — third-party verifiers
}
_E_TIER_SOURCE_TYPES = FORESIGHT_SOURCE_TYPES | {"analyst_trend"}


def compute_claim_tier(link: dict) -> str:
    """Collapse per-link metadata into a single A-E epistemic tier.

    A — hard external verification (analyst/news, third-party, broad, high credibility)
    B — filed/transactional disclosure (annual reports, investor relations, M&A filings)
    C — company self-claim only (website, press release, blog)
    D — inferred / weak signals (narrow scope, job postings, reviews, medium-credibility third-party)
    E — external trend/regulatory context only (foresight, trend reports, regulation)
    """
    st = link.get("source_type", "")
    credibility = link.get("source_credibility", "medium")
    independence = link.get("independence", "third-party-or-independent")
    scope = link.get("scope", "broad")

    # E first — foresight/regulation is always context-only regardless of other signals
    if st in _E_TIER_SOURCE_TYPES:
        return "E"

    # A — hard external: reputable third-party, broad, high credibility
    if (independence == "third-party-or-independent"
            and credibility == "high"
            and scope == "broad"
            and st in _A_TIER_SOURCE_TYPES):
        return "A"

    # B — filed transactional disclosure (verifiable even if self-originated)
    if st in _FINANCIAL_FILING_SOURCE_TYPES:
        return "B"

    # C — company self-report
    if independence == "company-reported" or st in SELF_REPORTED_SOURCE_TYPES:
        return "C"

    # D — narrow scope or medium-credibility third-party
    if scope == "narrow" or st in NARROW_SOURCE_TYPES or credibility != "high":
        return "D"

    return "D"  # safe fallback


def describe_source(link: dict) -> str:
    """Short human-readable label for a source, for use in LLM prompts.

    Maps the existing source_type/independence metadata into a short attribution
    phrase the GS model can use as context (e.g. "according to <source_label>").
    Used by general_summary._build_input_payload() to wire classification metadata
    into the evidence entries that reach the LLM, so the model knows whether a
    source is third-party, company self-report, foresight, etc.
    """
    if not isinstance(link, dict):
        return "Source"
    st = (link.get("source_type") or "").lower()
    if st == "financial_data_aggregator":
        return "Third-party financial database"
    if st in _FINANCIAL_FILING_SOURCE_TYPES:
        return "Company filing / IR"
    if st in {"analyst", "analyst_report", "news", "media"}:
        return "Third-party news / analyst"
    if st in {"regulation", "regulatory_context"}:
        return "Regulatory source"
    if st in {"foresight", "foresight_report", "trend_report", "analyst_trend"}:
        return "Forward-looking foresight"
    if st in {"company_press_release", "press_release"}:
        return "Company press release"
    if st in {"company_website", "case_study", "company_blog", "company_news",
              "official_company_source", "company_report", "company_filing"}:
        return "Company self-report"
    if st in {"academic_thesis", "thesis", "dissertation"}:
        return "Academic study (narrow scope)"
    if st == "employee_review":
        return "Workforce signal (employee reviews)"
    if st == "job_posting":
        return "Workforce signal (hiring activity)"
    if st == "github":
        return "Technical artifact (code repo)"
    if link.get("independence") == "company-reported":
        return "Company self-report"
    return "Source"


# -----------------------------------------------------------------------------
# Snippet quality helpers
#
# These three helpers let general_summary._build_input_payload() apply
# tier-aware filtering and substitution on evidence snippets.  Pre-Path-B the
# snippet rules had to do double duty (content + credibility signal).  Now
# claim_tier and source_label carry credibility, so snippets can return to
# their proper role of providing the actual content the LLM should quote.
# -----------------------------------------------------------------------------

_BOILERPLATE_PATTERNS = (
    re.compile(r"\b(accept|manage)\s+(all\s+)?cookies\b", re.I),
    re.compile(r"\bcookie\s+(policy|preferences|settings)\b", re.I),
    re.compile(r"\bprivacy\s+policy\b.*\bterms\b", re.I),
    re.compile(r"\b(read|learn)\s+more\s+(about|here)\b", re.I),
    re.compile(r"\b(sign|log)\s+in\s+to\s+continue\b", re.I),
    re.compile(r"^\s*(home|about|services|contact)\s*[|/]", re.I),
    re.compile(r"\b(404|page\s+not\s+found)\b", re.I),
)


def is_boilerplate_snippet(text: str) -> bool:
    """True when text is cookie/legal/nav stub rather than substantive content."""
    if not text or len(text) < 8:
        return False
    return any(p.search(text) for p in _BOILERPLATE_PATTERNS)


_SITE_SUFFIX = re.compile(r"\s*[|\-–—]\s*[^|\-–—]{1,40}\s*$")
_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize_for_match(text: str) -> str:
    if not text:
        return ""
    t = text.strip().lower()
    t = _SITE_SUFFIX.sub("", t)
    t = _PUNCT_RE.sub("", t)
    return " ".join(t.split())


def is_title_echo(snippet: str, title: str) -> bool:
    """True when snippet is just a (near-)copy of the title.

    Catches both exact matches and near-misses like trailing site suffixes
    ("Foo | BCG" vs "Foo") and high-overlap substrings.
    """
    sn = _normalize_for_match(snippet)
    ti = _normalize_for_match(title)
    if not sn or not ti:
        return False
    if sn == ti:
        return True
    shorter, longer = sorted([sn, ti], key=len)
    if shorter and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.80:
        return True
    return False


_TIER_BOUNDS = {
    "A": (30, 300),  # third-party verified — dense, no padding needed
    "B": (30, 300),  # filed disclosures — terse, factual
    "C": (50, 400),  # company self-report — trim promotional bloat
    "D": (50, 350),  # narrow / weak signal
    "E": (60, 400),  # foresight — needs trend context
}


def tier_snippet_bounds(claim_tier: str) -> Tuple[int, int]:
    """Per-tier (min_len, max_len) for snippet length gating and truncation."""
    return _TIER_BOUNDS.get((claim_tier or "D").upper(), (50, 400))


def infer_entity_scope(link: dict, company_name: str = "") -> str:
    """Infer whether evidence refers to the canonical target or a scoped entity."""
    if company_name:
        enriched = dict(link or {})
        apply_entity_scope(enriched, company_name)
        scope = enriched.get("entity_scope")
        if scope and scope != "unknown":
            return scope

    text = " ".join([
        link.get("title", ""),
        link.get("snippet", ""),
        link.get("url", ""),
    ]).lower()

    group_signals = {"group", "konserni", "group annual report", "group accounts",
                     "plc group", "oyj group", "holding", "parent company", "emoyhtiö",
                     "consolidated", "konsolidoitu"}
    local_signals = {"finland", "suomi", "oy ", " oy,", "oy.", "finland operations",
                     "nordic", "local entity"}

    if any(sig in text for sig in group_signals):
        return ENTITY_SCOPE_CANONICAL_GROUP
    if any(sig in text for sig in local_signals):
        return ENTITY_SCOPE_LOCAL_LEGAL_ENTITY
    return "unknown"


def normalize_evidence_link(link: dict, company_domain: str = "", company_name: str = "") -> dict:
    """Return the same dict enriched with deterministic governance metadata."""
    if not isinstance(link, dict):
        return link
    url = (link.get("url") or link.get("source_url") or "").strip()
    title = link.get("title") or link.get("source_title") or ""
    snippet = link.get("snippet") or link.get("evidence_snippet") or ""

    source_type = infer_source_type(url, title, snippet, link.get("source_type") or link.get("category") or "")
    link["source_type"] = source_type
    link["source_credibility"] = link.get("source_credibility") or assess_source_credibility(url, source_type, company_domain)
    link["scope"] = link.get("scope") or assess_source_scope(url, title, snippet)
    link["time_validity"] = link.get("time_validity") or infer_time_validity(link)

    # Entity match is separate from URL liveness.  wrong_entity always dominates.
    if link.get("wrong_entity") is True:
        link["entity_match"] = "wrong"
    elif link.get("company_mentioned") is False:
        link["entity_match"] = "weak"
    else:
        link["entity_match"] = link.get("entity_match") or "unknown"

    link["independence"] = link.get("independence") or (
        "company-reported" if source_type in SELF_REPORTED_SOURCE_TYPES else "third-party-or-independent"
    )
    link["allowed_claim_types"] = sorted(SOURCE_TYPE_ALLOWED_USES.get(source_type, {"third_party_observation"}))

    # Claim tier, entity scope, and target-entity match — computed last so all
    # upstream fields are set.  Entity match is stricter than URL liveness:
    # same-name but wrong-company pages must not feed generated prose.
    if not link.get("claim_tier"):
        link["claim_tier"] = compute_claim_tier(link)
    apply_entity_scope(link, company_name)

    entity_match = assess_entity_match(link, company_name, company_domain)
    link["entity_match_status"] = entity_match.get("entity_match_status", "unknown")
    link["entity_match_reason"] = entity_match.get("entity_match_reason", "")
    link["canonical_source_host"] = entity_match.get("canonical_source_host", "")
    inferred_scope = entity_match.get("entity_scope")
    if entity_match.get("entity_match_status") == "wrong":
        link["entity_scope"] = ENTITY_SCOPE_WRONG_ENTITY
    elif inferred_scope and (not link.get("entity_scope") or link.get("entity_scope") == ENTITY_SCOPE_UNKNOWN):
        link["entity_scope"] = inferred_scope
    if link["entity_match_status"] == "wrong":
        link["wrong_entity"] = True
        link["entity_match"] = "wrong"
    elif link["entity_match_status"] in {"exact", "scoped"}:
        link["entity_match"] = link["entity_match_status"]
        if link["entity_match_status"] == "exact" and link.get("entity_scope") == ENTITY_SCOPE_UNKNOWN:
            link["entity_scope"] = ENTITY_SCOPE_CANONICAL_GROUP
    if link.get("entity_scope") in {
        ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
        ENTITY_SCOPE_SUBSIDIARY,
        ENTITY_SCOPE_PARENT_OWNER,
        ENTITY_SCOPE_FORMER_NAME,
    } and not link.get("scope_warning"):
        link["scope_warning"] = (
            f"Evidence scope is {link.get('entity_scope')}; do not use as unqualified "
            f"{company_name or 'company'} group-wide evidence."
        )

    return apply_language_metadata(link)


def is_admissible_source(
    link: dict,
    company_domain: str = "",
    company_name: str = "",
    *,
    allow_unverified_without_url: bool = True,
) -> Tuple[bool, str]:
    """Hard gate for sources allowed to feed factual generation.

    This intentionally treats `wrong_entity=True` as fatal unless a human/manual
    override is present.  It also excludes dead URLs.  It does NOT exclude narrow
    or foresight sources; those are admissible but restricted by `allowed_claim_types`
    and must be scoped by downstream prompts/validators.
    """
    if not isinstance(link, dict):
        return False, "not_a_dict"
    link = normalize_evidence_link(link, company_domain, company_name)
    if link.get("manual_override") is True:
        return True, "manual_override"
    entity_status = str(link.get("entity_match_status") or "").lower()
    if (
        link.get("wrong_entity") is True
        or link.get("entity_match") == "wrong"
        or link.get("entity_scope") == ENTITY_SCOPE_WRONG_ENTITY
        or entity_status == "wrong"
    ):
        return False, "wrong_entity_true"
    url = (link.get("url") or link.get("source_url") or "").strip()
    if url and link.get("verified") is False:
        return False, "url_not_verified"
    if not url and not allow_unverified_without_url:
        return False, "missing_url"
    # Only block sources from explicitly known-bad domains (spam, content farms,
    # press release wires).  Company self-report (own domain) and URL-less Scanner
    # findings are admissible — they are biased or unverifiable, not fabricated.
    # The confidence_quality_penalty() system downgrades the overall confidence
    # score when low-credibility sources dominate, so callers are still aware of
    # the evidence quality without silently discarding legitimate signals.
    if link.get("source_credibility") == "low" and url:
        domain = _get_domain(url)
        if domain and (_domain_matches_set(domain, LOW_CREDIBILITY_DOMAINS) or _domain_matches_patterns(domain)):
            return False, "low_credibility_domain"
    return True, "admissible"


def apply_analysis_verification(link: dict, company_domain: str = "", company_name: str = "") -> dict:
    """Annotate whether a source is safe to use in generated analysis.

    URL verification only proves that a link resolves.  `analysis_verified`
    requires a readable source body plus a content-relevance check that says the
    page supports the attached claim/snippet.  Sources that fail this stricter
    test can still render in Deep Dive as audit material, but generation paths
    should ignore them.
    """
    if not isinstance(link, dict):
        return link

    normalize_evidence_link(link, company_domain, company_name)

    ok, admissibility_reason = is_admissible_source(link, company_domain, company_name)
    url = (link.get("url") or link.get("source_url") or "").strip()
    support_status = (link.get("claim_support_status") or "").strip().lower()
    if not support_status:
        if link.get("claim_irrelevant") is True:
            support_status = ANALYSIS_SUPPORT_UNSUPPORTED
        elif link.get("claim_relevance") is not None or link.get("content_relevance_score") is not None:
            try:
                score = int(link.get("content_relevance_score", link.get("claim_relevance")))
            except Exception:
                score = -1
            if score >= 55:
                support_status = ANALYSIS_SUPPORT_SUPPORTED
            elif score >= 40:
                support_status = ANALYSIS_SUPPORT_PARTIAL
            elif score >= 0:
                support_status = ANALYSIS_SUPPORT_UNSUPPORTED
            else:
                support_status = ANALYSIS_SUPPORT_UNAVAILABLE
        else:
            support_status = ANALYSIS_SUPPORT_UNCHECKED
    link["claim_support_status"] = support_status

    if link.get("content_relevance_score") is None and link.get("claim_relevance") is not None:
        link["content_relevance_score"] = link.get("claim_relevance")

    if link.get("content_verified") is None:
        link["content_verified"] = bool(link.get("source_quote"))

    reason = ""
    promotion = PROMOTION_DISPLAY_ONLY
    analysis_verified = False

    entity_status = str(link.get("entity_match_status") or "").lower()
    entity_scope = str(link.get("entity_scope") or ENTITY_SCOPE_UNKNOWN).lower()
    context_only = is_context_only_source(link) and link.get("company_specific") is not True
    scoped_entity_scopes = {
        ENTITY_SCOPE_LOCAL_LEGAL_ENTITY,
        ENTITY_SCOPE_SUBSIDIARY,
        ENTITY_SCOPE_PARENT_OWNER,
        ENTITY_SCOPE_FORMER_NAME,
    }
    if entity_status == "scoped" and entity_scope == ENTITY_SCOPE_UNKNOWN:
        entity_scope = ENTITY_SCOPE_LOCAL_LEGAL_ENTITY

    if link.get("manual_override") is True:
        reason = "manual_override"
        promotion = PROMOTION_GENERATION_ELIGIBLE
        analysis_verified = True
    elif not ok:
        reason = admissibility_reason
        promotion = PROMOTION_EXCLUDED if admissibility_reason in {"wrong_entity_true", "url_not_verified"} else PROMOTION_DISPLAY_ONLY
    elif not url:
        reason = "missing_url"
    elif link.get("verified") is not True and link.get("url_verified") is not True:
        reason = "url_not_verified"
        promotion = PROMOTION_EXCLUDED
    elif link.get("entity_scope") == ENTITY_SCOPE_WRONG_ENTITY or link.get("wrong_entity") is True:
        reason = "wrong_entity"
        promotion = PROMOTION_EXCLUDED
    elif entity_status == "wrong":
        reason = "wrong_entity"
        promotion = PROMOTION_EXCLUDED
    elif entity_status == "weak" and not context_only:
        reason = "weak_entity_match"
    elif entity_status in {"", "unknown"} and not context_only:
        reason = "unknown_entity"
    elif entity_scope == ENTITY_SCOPE_UNKNOWN and not context_only:
        reason = "unknown_entity_scope"
    elif entity_status not in {"exact", "scoped", "", "unknown"} and not context_only:
        reason = "entity_not_generation_safe"
    elif link.get("source_credibility") == "low":
        reason = "weak_source"
    elif link.get("content_verified") is not True:
        reason = "content_unavailable"
    elif support_status == ANALYSIS_SUPPORT_SUPPORTED:
        reason = "verified_for_analysis"
        promotion = PROMOTION_GENERATION_ELIGIBLE
        analysis_verified = True
    elif support_status == ANALYSIS_SUPPORT_PARTIAL:
        reason = "partial_support"
    elif support_status == ANALYSIS_SUPPORT_UNSUPPORTED:
        reason = "claim_unsupported"
    elif support_status == ANALYSIS_SUPPORT_UNAVAILABLE:
        reason = "content_unavailable"
    else:
        reason = "claim_unchecked"

    link["analysis_verified"] = analysis_verified
    link["promotion_status"] = promotion
    link["generation_eligible"] = promotion == PROMOTION_GENERATION_ELIGIBLE
    link["verification_reason"] = reason
    if analysis_verified and not link.get("analysis_verified_at"):
        link["analysis_verified_at"] = datetime.utcnow().isoformat()
    return link


def is_generation_eligible_source(link: dict) -> bool:
    """True when a source may feed generated prose."""
    if not isinstance(link, dict):
        return False
    entity_status = str(link.get("entity_match_status") or "").lower()
    entity_scope = str(link.get("entity_scope") or ENTITY_SCOPE_UNKNOWN).lower()
    context_only = is_context_only_source(link) and link.get("company_specific") is not True
    entity_ok = (
        entity_status in {"exact", "scoped"}
        and entity_scope not in {"", ENTITY_SCOPE_UNKNOWN, ENTITY_SCOPE_WRONG_ENTITY}
    ) or (
        context_only
        and entity_status not in {"wrong"}
        and link.get("wrong_entity") is not True
    )
    return (
        link.get("analysis_verified") is True
        and link.get("promotion_status") == PROMOTION_GENERATION_ELIGIBLE
        and link.get("generation_eligible") is True
        and (link.get("verified") is True or link.get("url_verified") is True)
        and link.get("content_verified") is True
        and str(link.get("claim_support_status") or "").lower() == ANALYSIS_SUPPORT_SUPPORTED
        and entity_ok
    )


def annotate_evidence_links(links: list, company_domain: str = "", company_name: str = "") -> list:
    """Normalize links in-place and annotate admissibility fields."""
    annotated = []
    for link in links or []:
        if not isinstance(link, dict):
            continue
        normalize_evidence_link(link, company_domain, company_name)
        ok, reason = is_admissible_source(link, company_domain, company_name)
        link["admissible"] = ok
        link["exclusion_reason"] = "" if ok else reason
        apply_analysis_verification(link, company_domain, company_name)
        annotated.append(link)
    return annotated


def filter_admissible_sources(links: list, company_domain: str = "", company_name: str = "") -> Tuple[List[dict], List[dict]]:
    """Return (admissible, excluded) after deterministic source gating."""
    admissible, excluded = [], []
    for link in annotate_evidence_links(links or [], company_domain, company_name):
        if link.get("admissible"):
            admissible.append(link)
        else:
            excluded.append(link)
    return admissible, excluded


def evidence_sufficiency_score(links: list, company_domain: str = "", company_name: str = "") -> dict:
    """Small 0-5 evidence sufficiency score for diagnosis confidence."""
    usable, excluded = filter_admissible_sources(links or [], company_domain, company_name)
    if not usable:
        return {"score": 0, "label": "none", "usable": 0, "excluded": len(excluded)}
    broad = sum(1 for l in usable if l.get("scope") == "broad")
    high = sum(1 for l in usable if l.get("source_credibility") == "high")
    independent = sum(1 for l in usable if l.get("independence") != "company-reported")
    current = sum(1 for l in usable if l.get("time_validity") == "current" or l.get("is_recent") is True)
    score = 1
    if len(usable) >= 3 and independent >= 1:
        score = 2
    if len(usable) >= 5 and broad >= 2 and independent >= 2:
        score = 3
    if len(usable) >= 8 and high >= 2 and broad >= 3 and current >= 2:
        score = 4
    if len(usable) >= 10 and high >= 4 and broad >= 5 and independent >= 4:
        score = 5
    labels = {1: "very_low", 2: "limited", 3: "moderate", 4: "strong", 5: "very_strong"}
    return {"score": score, "label": labels[score], "usable": len(usable), "excluded": len(excluded)}



def assess_evidence_quality(links: list, company_domain: str = "") -> tuple:
    """
    Assess quality of an entire evidence set. Adds 'source_credibility' field
    to each link dict.

    Returns: (high_count, medium_count, low_count, quality_score)
    where quality_score is 0-100 based on proportion of high/medium sources.
    """
    high_count = 0
    medium_count = 0
    low_count = 0

    for link in links:
        if not isinstance(link, dict):
            continue
        normalize_evidence_link(link, company_domain)
        credibility = link.get("source_credibility") or "medium"

        if credibility == "high":
            high_count += 1
        elif credibility == "medium":
            medium_count += 1
        else:
            low_count += 1

    total = high_count + medium_count + low_count
    if total == 0:
        return 0, 0, 0, 0

    # Quality score: high sources worth 100%, medium worth 50%, low worth 0%
    quality_score = int(((high_count * 100) + (medium_count * 50)) / total)
    return high_count, medium_count, low_count, quality_score


def confidence_quality_penalty(confidence: float, links: list, company_domain: str = "") -> float:
    """
    Apply a quality-based penalty to confidence score.
    If most evidence comes from low-quality sources, confidence should reflect that.

    Returns adjusted confidence score.
    """
    high, medium, low, quality_score = assess_evidence_quality(links, company_domain)
    total = high + medium + low

    if total == 0:
        return confidence

    low_ratio = low / total

    # If >60% of sources are low-credibility, apply penalty
    if low_ratio > 0.6:
        penalty = int((low_ratio - 0.4) * 40)  # max ~24 point penalty
        old = int(confidence)
        confidence = max(10, confidence - penalty)
        print(f"   ⚠️ [Source Quality] {int(low_ratio*100)}% low-credibility sources → confidence penalty -{penalty} ({old} → {int(confidence)})")
        print(f"      Sources: {high} high, {medium} medium, {low} low credibility (quality score: {quality_score}/100)")

    # If NO high-credibility sources at all, cap at 65
    if high == 0 and confidence > 65:
        old = int(confidence)
        confidence = 65
        print(f"   ⚠️ [Source Quality] No high-credibility sources → confidence capped at 65 ({old} → 65)")

    return confidence


# === SOURCE CAP & EVICTION ===

MAX_EVIDENCE_SOURCES = 15

# Eviction priority: lower value = evicted first
_EVICTION_RANK = {
    ("low", False):    0,   # dead low — worst, first to go
    ("low", True):     1,   # live low — still low value
    ("medium", False): 2,   # dead medium — was decent but unreachable
    ("medium", True):  3,   # live medium — solid, keep if possible
    ("high", False):   4,   # dead high — was great, might come back
    ("high", True):    5,   # live high — never evicted by replacement
}


def _eviction_rank(link: dict) -> int:
    """Return eviction priority for a source. Lower = evicted first."""
    if not isinstance(link, dict):
        return -2
    if link.get("admissible") is False or link.get("wrong_entity") is True:
        return -1
    cred = link.get("source_credibility", "medium")
    verified = link.get("verified", False)
    return _EVICTION_RANK.get((cred, bool(verified)), 2)


def manage_evidence_sources(
    existing_links: list,
    new_links: list,
    company_domain: str = "",
    company_name: str = "",
) -> dict:
    """
    Merge new evidence sources into existing ones, respecting the MAX_EVIDENCE_SOURCES cap
    with credibility-tiered eviction.

    Decision tree:
    1. If existing has < MAX sources → add new sources up to cap (best first)
    2. If existing has MAX sources AND all are high + verified → "gold_standard":
       skip adding, flag for re-verification only
    3. If at cap with mixed quality → apply eviction rules:
       - new high → evicts lowest-ranked existing source
       - new medium → evicts only low sources
       - new low → rejected (never added when at cap)

    Returns dict:
    {
        "links": [...],              # final merged evidence list (max 15)
        "gold_standard": bool,       # True if all 15 are high+verified
        "skipped_search": bool,      # True if gold_standard (caller should skip new search)
        "added": int,                # how many new sources were added
        "evicted": int,              # how many old sources were replaced
        "rejected": int,             # how many new sources were rejected (no room)
        "stats": {"high": n, "medium": n, "low": n}
    }
    """
    # Ensure all sources have credibility, scope, source_type and admissibility assessed.
    # Bad evidence is not allowed to compete with good evidence in the eviction pool.
    existing_links = annotate_evidence_links(existing_links or [], company_domain, company_name)
    new_links = annotate_evidence_links(new_links or [], company_domain, company_name)
    excluded_links = [l for l in existing_links + new_links if l.get("admissible") is False]
    existing_links = [l for l in existing_links if l.get("admissible") is not False]
    new_links = [l for l in new_links if l.get("admissible") is not False]

    # Dedup existing by URL
    seen_urls = set()
    deduped_existing = []
    for link in existing_links:
        url = (link.get("url") or link.get("source_url") or "").strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped_existing.append(link)
        elif not url:
            deduped_existing.append(link)

    pool = list(deduped_existing)

    # --- Gold Standard check ---
    if len(pool) >= MAX_EVIDENCE_SOURCES:
        all_high_verified = all(
            link.get("source_credibility") == "high" and link.get("verified") is True
            and is_generation_eligible_source(link)
            for link in pool[:MAX_EVIDENCE_SOURCES]
        )
        if all_high_verified:
            final = pool[:MAX_EVIDENCE_SOURCES]
            high, medium, low, _ = assess_evidence_quality(final, company_domain)
            print(f"   ⭐ [Source Manager] Gold Standard: {MAX_EVIDENCE_SOURCES}/{MAX_EVIDENCE_SOURCES} high-credibility verified sources. Skipping new search.")
            return {
                "links": final,
                "gold_standard": True,
                "skipped_search": True,
                "added": 0,
                "evicted": 0,
                "rejected": 0,
                "stats": {"high": high, "medium": medium, "low": low},
                "excluded": excluded_links,
            }

    # --- Merge new sources ---
    added = 0
    evicted = 0
    rejected = 0

    # Sort new links: high first, then medium, then low (best candidates first)
    cred_order = {"high": 0, "medium": 1, "low": 2}
    sorted_new = sorted(new_links, key=lambda l: cred_order.get(l.get("source_credibility", "medium"), 1))

    for new_link in sorted_new:
        new_url = (new_link.get("url") or new_link.get("source_url") or "").strip()

        # Dedup: if URL already exists, update metadata in-place
        if new_url and new_url in seen_urls:
            for existing in pool:
                existing_url = (existing.get("url") or existing.get("source_url") or "").strip()
                if existing_url == new_url:
                    # Update metadata fields (snippet, title, verified, etc.)
                    for key in ("snippet", "title", "verified", "is_recent", "source_type"):
                        if new_link.get(key) is not None:
                            existing[key] = new_link[key]
                    break
            continue

        new_cred = new_link.get("source_credibility", "medium")

        # Room available — just add
        if len(pool) < MAX_EVIDENCE_SOURCES:
            pool.append(new_link)
            if new_url:
                seen_urls.add(new_url)
            added += 1
            continue

        # At cap — apply eviction rules
        if new_cred == "low":
            # Low sources never added when at cap
            rejected += 1
            continue

        # Find the weakest existing source
        weakest_idx = None
        weakest_rank = _eviction_rank(new_link)  # new source must be better than what it replaces

        for i, existing in enumerate(pool):
            rank = _eviction_rank(existing)
            if rank < weakest_rank:
                if weakest_idx is None or rank < _eviction_rank(pool[weakest_idx]):
                    weakest_idx = i

        if new_cred == "medium":
            # Medium can only evict low sources
            if weakest_idx is not None and pool[weakest_idx].get("source_credibility") == "low":
                old_url = (pool[weakest_idx].get("url") or "").strip()
                if old_url:
                    seen_urls.discard(old_url)
                pool[weakest_idx] = new_link
                if new_url:
                    seen_urls.add(new_url)
                added += 1
                evicted += 1
            else:
                rejected += 1
        elif new_cred == "high":
            # High can evict anything below high+verified
            if weakest_idx is not None:
                old_url = (pool[weakest_idx].get("url") or "").strip()
                if old_url:
                    seen_urls.discard(old_url)
                pool[weakest_idx] = new_link
                if new_url:
                    seen_urls.add(new_url)
                added += 1
                evicted += 1
            else:
                rejected += 1
        else:
            rejected += 1

    # Final sort: high verified first, then by credibility tier
    pool.sort(key=lambda l: -_eviction_rank(l))
    final = pool[:MAX_EVIDENCE_SOURCES]

    high, medium, low, _ = assess_evidence_quality(final, company_domain)

    if added or evicted:
        print(f"   📊 [Source Manager] Sources: {added} added, {evicted} evicted, {rejected} rejected. Final: {high} high, {medium} medium, {low} low ({len(final)}/{MAX_EVIDENCE_SOURCES})")

    return {
        "links": final,
        "gold_standard": False,
        "skipped_search": False,
        "added": added,
        "evicted": evicted,
        "rejected": rejected,
        "stats": {"high": high, "medium": medium, "low": low},
        "excluded": excluded_links,
    }


# Phase: language metadata for foresight/regulatory sources.
FORESIGHT_ALLOWED_LANGUAGE = ["may", "could", "should evaluate", "should assess whether", "if in scope", "is becoming relevant"]
FORESIGHT_FORBIDDEN_LANGUAGE = ["must", "requires company to", "forces", "has deployed", "is deploying", "currently uses", "broad deployment", "will fall behind"]

def apply_language_metadata(link: dict) -> dict:
    """Annotate source records with language constraints for downstream rendering.

    This function is deliberately conservative: direct company evidence remains
    company evidence even when the topic is AI, governance, simulation or lifecycle
    traceability. Only generic foresight/regulatory/trend sources are marked as
    context-only.
    """
    if not isinstance(link, dict):
        return link

    link = classify_generation_context(link)

    if is_context_only_source(link):
        link.setdefault("allowed_language", FORESIGHT_ALLOWED_LANGUAGE)
        link.setdefault("forbidden_language", FORESIGHT_FORBIDDEN_LANGUAGE)
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)
    return link


def is_company_specific_evidence(link: dict) -> bool:
    """Return True when a source can support company-specific factual claims.

    Important: do not classify a source as context-only merely because it mentions
    AI, analytics, simulation, governance, DPP, lifecycle, or regulations. Those
    terms can appear in direct company evidence too.
    """
    if not isinstance(link, dict):
        return False
    if link.get("company_specific") is True:
        return True
    source_type = str(link.get("source_type") or "").lower()
    if source_type in COMPANY_SPECIFIC_SOURCE_TYPES:
        return True
    if str(link.get("entity_match") or "").lower() == "exact" and link.get("company_mentioned") is True:
        return True
    if link.get("company_mentioned") is True and link.get("verified") is True and source_type not in CONTEXT_ONLY_SOURCE_TYPES:
        return True
    return False


def is_context_only_source(link: dict) -> bool:
    """Return True for generic foresight/regulatory/trend context sources."""
    if not isinstance(link, dict):
        return False
    if is_company_specific_evidence(link):
        return False
    source_type = str(link.get("source_type") or "").lower()
    claim_type = str(link.get("claim_type") or "").lower()
    scope = str(link.get("scope") or "").lower()
    origin = str(link.get("origin") or "").lower()
    allowed = [str(x).lower() for x in (link.get("allowed_claim_types") or [])]
    return (
        source_type in CONTEXT_ONLY_SOURCE_TYPES
        or claim_type in {"foresight_context", "regulatory_context"}
        or scope == "external-trend"
        or origin == "foresight"
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
    )


def classify_generation_context(link: dict) -> dict:
    """Apply generation-time metadata without overwriting direct company evidence."""
    if not isinstance(link, dict):
        return link
    if is_company_specific_evidence(link):
        link["company_specific"] = True
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "company_fact")
        link.setdefault("scope", "company-specific")
        # Remove context-only flags if an earlier pass over-stamped the record.
        for key in [
            "excluded_from_company_fact_generation",
            "not_evidence_of_deployment",
            "forbidden_as_current_company_fact",
        ]:
            link.pop(key, None)
        if link.get("origin") == "foresight":
            link.pop("origin", None)
        return link
    if is_context_only_source(link):
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "foresight_context")
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)
        link.setdefault("scope", "external-trend")
    return link


def is_company_specific_evidence(link: dict) -> bool:
    """Return True when a source can support company-specific factual claims.

    This helper is intentionally defensive: it uses fallback source-type sets so a
    missing global constant cannot break Scanner 2 / Foresight generation at
    runtime. Do not classify a source as context-only merely because it mentions
    AI, analytics, simulation, governance, DPP, lifecycle, or regulation; those
    topics can appear in direct company evidence too.
    """
    if not isinstance(link, dict):
        return False

    company_specific_types = globals().get("COMPANY_SPECIFIC_SOURCE_TYPES", {
        "company_report", "annual_report", "financial_report", "quarterly_report",
        "company_press_release", "investor_report", "company_website", "case_study",
        "company_blog", "company_news", "company_filing", "official_company_source",
    })
    context_only_types = globals().get("CONTEXT_ONLY_SOURCE_TYPES", {
        "foresight", "foresight_report", "trend_report", "regulation",
        "regulatory_context", "analyst", "analyst_report", "market_report",
        "industry_report", "research_report", "think_tank", "policy_report",
    })

    if link.get("company_specific") is True:
        return True

    source_type = str(link.get("source_type") or "").lower()

    if source_type in company_specific_types:
        return True

    if str(link.get("entity_match") or "").lower() == "exact" and link.get("company_mentioned") is True:
        return True

    if link.get("company_mentioned") is True and link.get("verified") is True and source_type not in context_only_types:
        return True

    return False



def is_context_only_source(link: dict) -> bool:
    """Return True for generic foresight/regulatory/trend context sources.

    Direct company evidence always wins over generic context classification.
    """
    if not isinstance(link, dict):
        return False

    if is_company_specific_evidence(link):
        return False

    context_only_types = globals().get("CONTEXT_ONLY_SOURCE_TYPES", {
        "foresight", "foresight_report", "trend_report", "regulation",
        "regulatory_context", "analyst", "analyst_report", "market_report",
        "industry_report", "research_report", "think_tank", "policy_report",
    })

    source_type = str(link.get("source_type") or "").lower()
    claim_type = str(link.get("claim_type") or "").lower()
    scope = str(link.get("scope") or "").lower()
    origin = str(link.get("origin") or "").lower()
    allowed = [str(x).lower() for x in (link.get("allowed_claim_types") or [])]

    return (
        source_type in context_only_types
        or claim_type in {"foresight_context", "regulatory_context"}
        or scope == "external-trend"
        or origin == "foresight"
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
    )



def classify_generation_context(link: dict) -> dict:
    """Apply generation-time metadata without overwriting direct company evidence.

    This function is used by scan-time and foresight-generation flows so newly
    refreshed company records are clean before they are saved. The Sanitizer
    remains only a legacy cleanup/audit tool.
    """
    if not isinstance(link, dict):
        return link

    if is_company_specific_evidence(link):
        link["company_specific"] = True
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "company_fact")
        link.setdefault("scope", "company-specific")
        # Remove context-only flags if an earlier pass over-stamped the record.
        for key in [
            "excluded_from_company_fact_generation",
            "not_evidence_of_deployment",
            "forbidden_as_current_company_fact",
        ]:
            link.pop(key, None)
        if link.get("origin") == "foresight":
            link.pop("origin", None)
        return link

    if is_context_only_source(link):
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "foresight_context")
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)
        link.setdefault("scope", "external-trend")

    return link



def apply_language_metadata(link: dict) -> dict:
    """Annotate source records with language constraints for downstream rendering.

    Direct company evidence remains company evidence even when the topic is AI,
    governance, simulation, lifecycle traceability, DPP, or regulation. Only
    generic foresight/regulatory/trend sources are marked as context-only.
    """
    if not isinstance(link, dict):
        return link

    link = classify_generation_context(link)

    if is_context_only_source(link):
        link.setdefault("allowed_language", FORESIGHT_ALLOWED_LANGUAGE)
        link.setdefault("forbidden_language", FORESIGHT_FORBIDDEN_LANGUAGE)
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)

    return link



def is_company_specific_evidence(link: dict) -> bool:
    """Return True when a source can support company-specific factual claims.

    This helper is intentionally defensive: it uses fallback source-type sets so a
    missing global constant cannot break Scanner 2 / Foresight generation at
    runtime. Do not classify a source as context-only merely because it mentions
    AI, analytics, simulation, governance, DPP, lifecycle, or regulation; those
    topics can appear in direct company evidence too.
    """
    if not isinstance(link, dict):
        return False

    company_specific_types = globals().get("COMPANY_SPECIFIC_SOURCE_TYPES", {
        "company_report", "annual_report", "financial_report", "quarterly_report",
        "company_press_release", "investor_report", "company_website", "case_study",
        "company_blog", "company_news", "company_filing", "official_company_source",
    })
    context_only_types = globals().get("CONTEXT_ONLY_SOURCE_TYPES", {
        "foresight", "foresight_report", "trend_report", "regulation",
        "regulatory_context", "analyst", "analyst_report", "market_report",
        "industry_report", "research_report", "think_tank", "policy_report",
    })

    if link.get("company_specific") is True:
        return True

    source_type = str(link.get("source_type") or "").lower()

    if source_type in company_specific_types:
        return True

    if str(link.get("entity_match") or "").lower() == "exact" and link.get("company_mentioned") is True:
        return True

    if link.get("company_mentioned") is True and link.get("verified") is True and source_type not in context_only_types:
        return True

    return False



def is_context_only_source(link: dict) -> bool:
    """Return True for generic foresight/regulatory/trend context sources.

    Direct company evidence always wins over generic context classification.
    """
    if not isinstance(link, dict):
        return False

    if is_company_specific_evidence(link):
        return False

    context_only_types = globals().get("CONTEXT_ONLY_SOURCE_TYPES", {
        "foresight", "foresight_report", "trend_report", "regulation",
        "regulatory_context", "analyst", "analyst_report", "market_report",
        "industry_report", "research_report", "think_tank", "policy_report",
    })

    source_type = str(link.get("source_type") or "").lower()
    claim_type = str(link.get("claim_type") or "").lower()
    scope = str(link.get("scope") or "").lower()
    origin = str(link.get("origin") or "").lower()
    allowed = [str(x).lower() for x in (link.get("allowed_claim_types") or [])]

    return (
        source_type in context_only_types
        or claim_type in {"foresight_context", "regulatory_context"}
        or scope == "external-trend"
        or origin == "foresight"
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
    )



def classify_generation_context(link: dict) -> dict:
    """Apply generation-time metadata without overwriting direct company evidence.

    This function is used by scan-time and foresight-generation flows so newly
    refreshed company records are clean before they are saved. The Sanitizer
    remains only a legacy cleanup/audit tool.
    """
    if not isinstance(link, dict):
        return link

    if is_company_specific_evidence(link):
        link["company_specific"] = True
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "company_fact")
        link.setdefault("scope", "company-specific")
        for key in [
            "excluded_from_company_fact_generation",
            "not_evidence_of_deployment",
            "forbidden_as_current_company_fact",
        ]:
            link.pop(key, None)
        if link.get("origin") == "foresight":
            link.pop("origin", None)
        return link

    if is_context_only_source(link):
        link.setdefault("generation_eligible", False)
        link.setdefault("claim_type", "foresight_context")
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)
        link.setdefault("scope", "external-trend")

    return link



def apply_language_metadata(link: dict) -> dict:
    """Annotate source records with language constraints for downstream rendering.

    Direct company evidence remains company evidence even when the topic is AI,
    governance, simulation, lifecycle traceability, DPP, or regulation. Only
    generic foresight/regulatory/trend sources are marked as context-only.
    """
    if not isinstance(link, dict):
        return link

    link = classify_generation_context(link)

    if is_context_only_source(link):
        link.setdefault("allowed_language", FORESIGHT_ALLOWED_LANGUAGE)
        link.setdefault("forbidden_language", FORESIGHT_FORBIDDEN_LANGUAGE)
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)

    return link



# --- FINAL SOURCE CLASSIFICATION OVERRIDES -----------------------------------
# Keep these definitions at the end of the module. The file has accumulated older
# helper definitions during iterative hardening; Python uses the last definition.
# These final overrides are the scan-time authority for source-card classification.

COMPANY_SPECIFIC_SOURCE_TYPES = globals().get("COMPANY_SPECIFIC_SOURCE_TYPES", {
    "company_report", "annual_report", "financial_report", "quarterly_report",
    "company_press_release", "press_release", "investor_report", "company_website",
    "case_study", "company_blog", "company_news", "company_filing",
    "official_company_source", "earnings_report", "sustainability_report",
    "company_financial_report", "investor_relations",
})

CONTEXT_ONLY_SOURCE_TYPES = globals().get("CONTEXT_ONLY_SOURCE_TYPES", {
    "foresight", "foresight_report", "trend_report", "regulation",
    "regulatory_context", "analyst", "analyst_report", "market_report",
    "industry_report", "research_report", "think_tank", "policy_report",
    "government", "standards_body",
})


def is_company_specific_evidence(link: dict) -> bool:
    """Return True when a source can support company-specific factual claims.

    Company-specific evidence wins over context-only classification. A direct
    company case study, annual report, press release, implementation story,
    acquisition article, or exact entity match must remain company evidence even
    if the topic is AI, cloud, data, DPP, regulation, governance, or foresight.
    """
    if not isinstance(link, dict):
        return False

    source_type = str(link.get("source_type") or "").lower().replace(" ", "_")
    claim_type = str(link.get("claim_type") or "").lower()
    scope = str(link.get("scope") or "").lower()

    if link.get("company_specific") is True:
        return True
    if claim_type == "company_fact" or scope == "company-specific":
        return True
    if source_type in COMPANY_SPECIFIC_SOURCE_TYPES:
        return True
    if str(link.get("entity_match") or "").lower() == "exact" and link.get("company_mentioned") is True:
        return True
    if link.get("company_mentioned") is True and link.get("verified") is True and source_type not in CONTEXT_ONLY_SOURCE_TYPES:
        return True

    # Heuristic fallback: source/title/snippet clearly describes the target entity.
    text = f"{link.get('title','')} {link.get('snippet','')} {link.get('source_says','')}".lower()
    if link.get("company_mentioned") is True and any(t in text for t in ["case study", "annual report", "business review", "press release", "appointment", "acquisition"]):
        return True

    return False


def is_context_only_source(link: dict) -> bool:
    """Return True for generic foresight/regulatory/trend context sources."""
    if not isinstance(link, dict):
        return False
    if is_company_specific_evidence(link):
        return False

    source_type = str(link.get("source_type") or "").lower().replace(" ", "_")
    claim_type = str(link.get("claim_type") or "").lower()
    scope = str(link.get("scope") or "").lower()
    origin = str(link.get("origin") or "").lower()
    allowed = [str(x).lower() for x in (link.get("allowed_claim_types") or [])]

    return (
        source_type in CONTEXT_ONLY_SOURCE_TYPES
        or claim_type in {"foresight_context", "regulatory_context"}
        or scope == "external-trend"
        or origin == "foresight"
        or "foresight_context" in allowed
        or "regulatory_context" in allowed
    )


def classify_generation_context(link: dict) -> dict:
    """Apply generation-time metadata without overwriting direct company evidence."""
    if not isinstance(link, dict):
        return link

    if is_company_specific_evidence(link):
        link["company_specific"] = True
        # Being company-specific is not the same as being safe for generation.
        # `apply_analysis_verification()` is the sole authority that can promote
        # a source after URL, content, claim-support, and entity checks pass.
        link.setdefault("analysis_verified", False)
        link.setdefault("generation_eligible", False)
        link.setdefault("promotion_status", PROMOTION_DISPLAY_ONLY)
        link["claim_type"] = "company_fact"
        link["scope"] = "company-specific"
        for key in [
            "excluded_from_company_fact_generation",
            "not_evidence_of_deployment",
            "forbidden_as_current_company_fact",
        ]:
            link.pop(key, None)
        if link.get("origin") == "foresight":
            link.pop("origin", None)
        return link

    if is_context_only_source(link):
        link.setdefault("analysis_verified", False)
        link.setdefault("generation_eligible", False)
        link.setdefault("promotion_status", PROMOTION_DISPLAY_ONLY)
        link["claim_type"] = link.get("claim_type") or "foresight_context"
        link["company_specific"] = False
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)
        link.setdefault("scope", "external-trend")

    return link


def apply_language_metadata(link: dict) -> dict:
    """Annotate source records with language constraints for downstream rendering."""
    if not isinstance(link, dict):
        return link

    link = classify_generation_context(link)

    if is_context_only_source(link):
        link.setdefault("allowed_language", FORESIGHT_ALLOWED_LANGUAGE)
        link.setdefault("forbidden_language", FORESIGHT_FORBIDDEN_LANGUAGE)
        link.setdefault("company_specific", False)
        link.setdefault("requires_company_specific_corroboration", True)
        link.setdefault("not_evidence_of_deployment", True)
        link.setdefault("excluded_from_company_fact_generation", True)

    return link
