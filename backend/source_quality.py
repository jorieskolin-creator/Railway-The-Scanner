"""
source_quality.py — Shared source credibility assessment for all scan pipelines.

Evaluates the QUALITY and AUTHORITY of evidence sources, not just URL liveness.
Used by scanner_two.py (core scanner) and strategic_foresight.py (foresight signals).

Key distinction:
- "verified" = URL is reachable (HTTP 200) — says NOTHING about quality
- "source_credibility" = domain authority assessment — high/medium/low/unknown
"""

from urllib.parse import urlparse


# === DOMAIN AUTHORITY TIERS ===

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


def assess_source_credibility(url: str, source_type: str = "", company_domain: str = "") -> str:
    """
    Assess a single source URL's credibility tier.

    Returns: "high", "medium", or "low"

    Logic:
    1. Known high-credibility domain → "high"
    2. Known low-credibility domain or pattern → "low"
    3. Company's own domain (self-reporting) → "low"
    4. Source type "academic"/"government"/"think_tank" on unknown domain → "medium"
    5. Everything else → "low" (default-deny for unknown sources)
    """
    domain = _get_domain(url)
    if not domain:
        return "low"

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

    # If the AI labeled it as academic/government/think_tank, give benefit of doubt
    source_type_lower = (source_type or "").strip().lower()
    if source_type_lower in ("academic", "government", "think_tank"):
        return "medium"

    # Default: unknown source = low credibility
    # This is the critical change — previously defaulted to "medium"
    return "low"


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
        url = (link.get("url") or link.get("source_url") or "").strip()
        source_type = (link.get("source_type") or link.get("category") or "").strip()
        credibility = assess_source_credibility(url, source_type, company_domain)
        link["source_credibility"] = credibility

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
