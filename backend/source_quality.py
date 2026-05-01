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
    cred = link.get("source_credibility", "medium")
    verified = link.get("verified", False)
    return _EVICTION_RANK.get((cred, bool(verified)), 2)


def manage_evidence_sources(
    existing_links: list,
    new_links: list,
    company_domain: str = "",
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
    # Ensure all sources have credibility assessed
    for link in existing_links + new_links:
        url = (link.get("url") or link.get("source_url") or "").strip()
        source_type = (link.get("source_type") or link.get("category") or "").strip()
        if "source_credibility" not in link:
            link["source_credibility"] = assess_source_credibility(url, source_type, company_domain)

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
    }
