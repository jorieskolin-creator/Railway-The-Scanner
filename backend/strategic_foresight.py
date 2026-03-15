"""
strategic_foresight.py — Strategic Foresight module for forward-looking analysis.

Generates per-dimension industry forces, cross-impact chains, micro-scenarios,
and optional climate exposure assessment. Uses Gemini + Google Search grounding.

Includes post-generation validation:
- URL verification (HEAD/GET probe) for all source_url fields
- Source retry loop: re-searches for verifiable sources when initial URLs fail
- Multi-source requirement: each force requires 2+ supporting sources
- Minimum quality gate: foresight is suppressed if insufficient verified sources
- Reality-check pass (source quality, confidence calibration, claim plausibility)
- Extraction of validated sources into foresight_links (mirrors evidence_links schema)

Methodology inspired by Futures Platform's 30-theme taxonomy (NAICS/SIC-based)
and SIFT/Lateral Reading OSINT validation principles.
"""

import json
import os
import re
import time
import concurrent.futures

import anthropic
from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from source_quality import HIGH_CREDIBILITY_DOMAINS, LOW_CREDIBILITY_DOMAINS, assess_source_credibility


# ═══════════════════════════════════════════════════════════════════════════════
# FUTURES PLATFORM 30-THEME TAXONOMY
# Complete list of the 30 FP Themes utilized in Future Insight creation.
# Each theme represents a macro-level domain of change that shapes the external
# environment companies must navigate. Used to tag dimension-level forces and
# ensure comprehensive coverage across all foresight analyses.
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEMES_30 = [
    # --- Technology & Digital ---
    "Artificial Intelligence",          #  1 — AI, machine learning, autonomous systems
    "Data & Digitalisation",            #  2 — Big data, digital transformation, platforms
    "Communication & Media",            #  3 — Telecom, journalism, social media, content

    # --- Economy & Business ---
    "Business",                         #  4 — Business models, strategy, innovation mgmt
    "Finance & Ownership",              #  5 — Banking, investment, fintech, ownership models
    "Global Economy",                   #  6 — Trade, macroeconomics, supply chains, sanctions
    "Digital & Sharing Economies",      #  7 — Platform economy, gig work, collaborative models
    "Services",                         #  8 — Service design, customer experience, servitization

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing",         #  9 — Smart factories, automation, industrial IoT
    "Construction & Urbanisation",      # 10 — Smart cities, urban planning, building tech
    "Energy",                           # 11 — Renewables, grids, energy storage, nuclear
    "Freight & Logistics",              # 12 — Supply chains, last-mile, autonomous freight
    "Private & Public Transport",       # 13 — Mobility-as-a-Service, EVs, public transit

    # --- Environment & Resources ---
    "Climate Change",                   # 14 — Mitigation, adaptation, carbon markets
    "Sustainability & Recycling",       # 15 — Circular economy, waste, ESG reporting
    "Nature",                           # 16 — Biodiversity, ecosystems, land use
    "Food",                             # 17 — Agri-tech, food security, alternative proteins
    "Water & Oceans",                   # 18 — Water scarcity, marine economy, blue tech

    # --- Society & Governance ---
    "Public Governance",                # 19 — Regulation, democracy, public-sector innovation
    "Geopolitics",                      # 20 — International relations, conflicts, alliances
    "Security & Safety",                # 21 — Cybersecurity, defence, resilience, privacy
    "Values & Ethics",                  # 22 — Trust, transparency, corporate responsibility
    "Society",                          # 23 — Inequality, social cohesion, civil society

    # --- People & Well-being ---
    "Health & Wellbeing",               # 24 — Healthcare, mental health, biotech, longevity
    "Education & Knowledge",            # 25 — EdTech, lifelong learning, skills transformation
    "Work & Income",                    # 26 — Future of work, labour markets, UBI
    "Management & HR",                  # 27 — Leadership, org design, talent management
    "Leisure & Lifestyles",             # 28 — Consumer culture, tourism, entertainment

    # --- Science & Demographics ---
    "Sciences & Research",              # 29 — R&D, quantum computing, space, biotech research
    "Population & Demographics",        # 30 — Aging, migration, urbanisation trends
]

# ═══════════════════════════════════════════════════════════════════════════════
# FP THEME → AUTHORITATIVE SOURCE MAPPING
# Maps each of the 30 Futures Platform themes to the most relevant authoritative
# sources for finding evidence about that theme. These are "horizontal" sources
# (theme-specific, cross-industry) vs the "vertical" per-industry target_sources.
#
# Source categories:
# - Foresight orgs: Millennium Project, IFTF, CIFS, RAND, HRCFS, Tellus, DFF
# - Think tanks: Chatham House, Club of Rome, Oxford Martin, SFG, SOIF
# - Intergovernmental: OECD, WEF, EC, UN agencies, NIC, WMO
# - Climate & Earth systems: IPCC, NASA Climate, PIK, SRC, IIASA, WRI, Tyndall, Future Earth
# - Corporate & innovation: Gartner, McKinsey, BCG, Forrester, WBCSD, Arup, Fraunhofer ISI
# - Public innovation: Sitra
# - Data & demographics: Pew Research Center
# - Academic: Nature, IEEE, ScienceDirect, arxiv
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEME_SOURCES = {
    # --- Technology & Digital ---
    "Artificial Intelligence": [
        "site:iftf.org artificial intelligence",
        "site:oxfordmartin.ox.ac.uk AI",
        "site:cser.ac.uk artificial intelligence risk",
        "site:rand.org artificial intelligence",
        "site:gartner.com AI trends hype cycle",
        "site:isi.fraunhofer.de artificial intelligence",       # Fraunhofer — European tech forecasting
        "site:dubaifuture.gov.ae artificial intelligence",      # Dubai Future Foundation — applied AI
    ],
    "Data & Digitalisation": [
        "site:weforum.org digital transformation",
        "site:mckinsey.com digital transformation",
        "site:oecd.org digital economy",
        "site:ec.europa.eu digital strategy",
        "site:sitra.fi data digital transformation",            # Sitra — fair data economy
        "site:isi.fraunhofer.de digitalisation",                # Fraunhofer — European digital foresight
    ],
    "Communication & Media": [
        "site:reuters.com media industry future",
        "site:iftf.org media communication",
        "site:weforum.org media transformation",
        "site:oxfordmartin.ox.ac.uk media",
    ],

    # --- Economy & Business ---
    "Business": [
        "site:mckinsey.com business model innovation",
        "site:hbr.org business strategy future",
        "site:bcg.com business transformation",
        "site:weforum.org business future",
        "site:wbcsd.org business sustainability transformation", # WBCSD — Vision 2050, corporate transitions
        "site:dubaifuture.gov.ae business innovation",          # Dubai Future Foundation — applied foresight
    ],
    "Finance & Ownership": [
        "site:bis.org fintech future",
        "site:imf.org financial system",
        "site:mckinsey.com financial services",
        "site:weforum.org financial system",
    ],
    "Global Economy": [
        "site:oecd.org economic outlook",
        "site:imf.org world economic outlook",
        "site:weforum.org global economy",
        "site:chathamhouse.org global economy",
        "site:millennium-project.org global challenges",
        "site:dni.gov global trends",                           # NIC — Global Trends reports
        "site:tellus.org global transition economy",            # Tellus — Great Transition scenarios
    ],
    "Digital & Sharing Economies": [
        "site:oecd.org platform economy",
        "site:weforum.org sharing economy",
        "site:mckinsey.com platform economy",
        "site:iftf.org future of work platforms",
    ],
    "Services": [
        "site:mckinsey.com service industry",
        "site:forrester.com customer experience",
        "site:hbr.org service innovation",
        "site:deloitte.com service transformation",
    ],

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing": [
        "site:weforum.org manufacturing future",
        "site:mckinsey.com smart manufacturing",
        "site:oecd.org industrial policy",
        "site:deloitte.com manufacturing trends",
        "site:isi.fraunhofer.de manufacturing innovation",      # Fraunhofer — industrial innovation roadmaps
    ],
    "Construction & Urbanisation": [
        "site:weforum.org urban future",
        "site:oecd.org urbanisation",
        "site:mckinsey.com construction",
        "site:ec.europa.eu urban policy",
        "site:oxfordmartin.ox.ac.uk urbanisation",
        "site:arup.com cities future urbanisation",             # Arup — built environment, future cities
    ],
    "Energy": [
        "site:iea.org energy outlook",
        "site:irena.org renewable energy",
        "site:weforum.org energy transition",
        "site:bloombergnef.com energy",
        "site:clubofrome.org energy",
        "site:isi.fraunhofer.de energy technology",             # Fraunhofer — energy tech forecasting
        "site:tyndall.ac.uk energy decarbonisation",            # Tyndall — deep decarbonisation research
        "site:wri.org energy transition",                       # WRI — resource-based energy foresight
    ],
    "Freight & Logistics": [
        "site:weforum.org supply chain logistics",
        "site:mckinsey.com logistics",
        "site:oecd.org trade logistics",
        "site:deloitte.com supply chain",
    ],
    "Private & Public Transport": [
        "site:iea.org transport",
        "site:weforum.org mobility future",
        "site:mckinsey.com mobility",
        "site:oecd.org transport policy",
        "site:iftf.org future of mobility",
    ],

    # --- Environment & Resources ---
    "Climate Change": [
        "site:ipcc.ch climate",
        "site:weforum.org climate change",
        "site:clubofrome.org climate",
        "site:oxfordmartin.ox.ac.uk climate",
        "site:iea.org climate",
        "site:millennium-project.org climate",
        "site:climate.nasa.gov climate change",                 # NASA — satellite data, physical modeling
        "site:wmo.int climate state",                           # WMO — extreme weather, climate benchmarks
        "site:pik-potsdam.de climate tipping points",           # PIK — tipping elements, thresholds
        "site:stockholmresilience.org climate planetary",       # SRC — Planetary Boundaries, resilience
        "site:tellus.org climate transition",                   # Tellus — Great Transition, systemic paths
        "site:futureearth.org climate science",                 # Future Earth — 10 New Insights in Climate
        "site:sitra.fi climate megatrends",                     # Sitra — Finnish megatrend mapping
        "site:tyndall.ac.uk climate carbon budget",             # Tyndall — carbon budgets, climate justice
    ],
    "Sustainability & Recycling": [
        "site:weforum.org circular economy",
        "site:ec.europa.eu circular economy",
        "site:oecd.org sustainability",
        "site:clubofrome.org sustainability",
        "site:ellenmacarthurfoundation.org circular economy",
        "site:wbcsd.org sustainability circular economy",       # WBCSD — Vision 2050, corporate pathways
        "site:stockholmresilience.org sustainability",          # SRC — social-ecological systems
        "site:sitra.fi circular economy",                       # Sitra — circular economy frameworks
        "site:wri.org sustainability resources",                # WRI — resource tracking, supply chains
    ],
    "Nature": [
        "site:weforum.org biodiversity",
        "site:un.org biodiversity",
        "site:clubofrome.org nature",
        "site:oxfordmartin.ox.ac.uk biodiversity",
        "site:fao.org ecosystems",
        "site:stockholmresilience.org biodiversity planetary",  # SRC — Planetary Boundaries, biodiversity
        "site:futureearth.org biodiversity ecosystems",         # Future Earth — sustainability science
        "site:wri.org forests biodiversity",                    # WRI — forest & ecosystem tracking
    ],
    "Food": [
        "site:fao.org food security",
        "site:weforum.org food future",
        "site:oecd.org agriculture food",
        "site:oxfordmartin.ox.ac.uk food",
        "site:nature.com food systems",
        "site:wri.org food systems agriculture",                # WRI — food supply chain, resource scarcity
    ],
    "Water & Oceans": [
        "site:un.org water scarcity",
        "site:weforum.org water",
        "site:oecd.org water policy",
        "site:millennium-project.org water",
        "site:nature.com ocean",
        "site:strategicforesight.com water diplomacy",          # SFG — transnational water conflicts
        "site:wmo.int water climate",                           # WMO — water cycle, hydrological futures
        "site:iiasa.ac.at water scarcity",                      # IIASA — water-climate-population modeling
    ],

    # --- Society & Governance ---
    "Public Governance": [
        "site:oecd.org governance innovation",
        "site:ec.europa.eu governance",
        "site:weforum.org governance future",
        "site:chathamhouse.org governance",
        "site:rand.org governance",
        "site:soif.org.uk governance foresight",                # SOIF — policy foresight, future generations
        "site:dni.gov governance global trends",                # NIC — government-grade strategic foresight
        "site:sitra.fi governance society",                     # Sitra — societal innovation, public policy
    ],
    "Geopolitics": [
        "site:chathamhouse.org geopolitics",
        "site:rand.org geopolitics",
        "site:weforum.org geopolitics",
        "site:cifs.dk geopolitics",
        "site:millennium-project.org geopolitics",
        "site:dni.gov geopolitics global trends",               # NIC — US Intelligence Community foresight
        "site:strategicforesight.com geopolitics peace",        # SFG — Global South, cost of conflict
    ],
    "Security & Safety": [
        "site:rand.org cybersecurity",
        "site:cser.ac.uk security risk",
        "site:chathamhouse.org security",
        "site:weforum.org cybersecurity",
        "site:gartner.com security trends",
        "site:dni.gov security threats",                        # NIC — national security megatrends
        "site:strategicforesight.com security conflict",        # SFG — peace & security foresight
    ],
    "Values & Ethics": [
        "site:weforum.org ethics trust",
        "site:oxfordmartin.ox.ac.uk ethics",
        "site:oecd.org AI ethics",
        "site:cifs.dk values ethics",
        "site:millennium-project.org ethics",
        "site:futures.hawaii.edu futures values",               # HRCFS — futures epistemology, alternative futures
        "site:pewresearch.org values society",                  # Pew — generational values, public opinion
    ],
    "Society": [
        "site:oecd.org inequality social",
        "site:weforum.org inequality society",
        "site:oxfordmartin.ox.ac.uk inequality",
        "site:chathamhouse.org social change",
        "site:clubofrome.org society",
        "site:pewresearch.org society trends",                  # Pew — social trends, inequality data
        "site:soif.org.uk society future generations",          # SOIF — intergenerational fairness
        "site:tellus.org society transition",                   # Tellus — societal resilience pathways
        "site:futures.hawaii.edu society futures",              # HRCFS — Four Futures archetypes
    ],

    # --- People & Well-being ---
    "Health & Wellbeing": [
        "site:who.int health future",
        "site:weforum.org health",
        "site:mckinsey.com healthcare future",
        "site:nature.com health",
        "site:oxfordmartin.ox.ac.uk health",
    ],
    "Education & Knowledge": [
        "site:oecd.org education future",
        "site:weforum.org education",
        "site:mckinsey.com education",
        "site:iftf.org learning future",
        "site:ec.europa.eu education skills",
    ],
    "Work & Income": [
        "site:oecd.org future of work",
        "site:weforum.org future of work",
        "site:mckinsey.com future of work",
        "site:iftf.org future of work",
        "site:ilo.org future of work",
        "site:pewresearch.org work income employment",          # Pew — labour market data, gig economy
    ],
    "Management & HR": [
        "site:mckinsey.com talent management",
        "site:hbr.org future of management",
        "site:weforum.org talent",
        "site:deloitte.com human capital trends",
        "site:gartner.com HR trends",
    ],
    "Leisure & Lifestyles": [
        "site:mckinsey.com consumer trends",
        "site:weforum.org consumer lifestyle",
        "site:deloitte.com consumer trends",
        "site:cifs.dk lifestyle trends",
    ],

    # --- Science & Demographics ---
    "Sciences & Research": [
        "site:nature.com science future",
        "site:oxfordmartin.ox.ac.uk research",
        "site:cser.ac.uk technology risk",
        "site:rand.org technology",
        "site:weforum.org technology science",
        "site:isi.fraunhofer.de research innovation",           # Fraunhofer — European R&D roadmaps
        "site:dubaifuture.gov.ae science technology",           # DFF — emerging tech prototyping
        "site:futureearth.org research sustainability",         # Future Earth — cutting-edge climate science
        "site:pik-potsdam.de research earth system",            # PIK — Earth system science
        "site:iiasa.ac.at systems research",                    # IIASA — complex systems modeling
    ],
    "Population & Demographics": [
        "site:oecd.org demographics population",
        "site:weforum.org demographics",
        "site:un.org population",
        "site:cifs.dk demographics megatrends",
        "site:oxfordmartin.ox.ac.uk demographics",
        "site:pewresearch.org demographics population",         # Pew — global religion, migration, aging data
        "site:iiasa.ac.at population demographics",             # IIASA — demographic-climate interaction models
        "site:dni.gov demographics population trends",          # NIC — demographic power shift projections
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN → FORESIGHT THEME MAPPING
# Maps the scanner's 14 KNOWN_DOMAINS to Futures Platform's 30-theme taxonomy
# and identifies which KB dimensions are most impacted by external forces.
# ═══════════════════════════════════════════════════════════════════════════════

DOMAIN_TO_FORESIGHT = {
    "Technology & Software": {
        "fp_themes": ["Artificial Intelligence", "Data & Digitalisation", "Business"],
        "priority_dimensions": ["AI Readiness", "Operating Model", "Service Portfolio"],
        "osint_keywords": "agentic AI enterprise automation, SaaS market consolidation, open-source AI disruption",
        "target_sources": [
            "site:gartner.com software industry trends",
            "site:mckinsey.com technology strategy",
            "site:forrester.com software market forecast",
            "site:hbr.org technology disruption",
            "site:idc.com software industry",
        ],
    },
    "IT Services & Consulting": {
        "fp_themes": ["Data & Digitalisation", "Business", "Management & HR"],
        "priority_dimensions": ["Operating Model", "Strategy", "AI Readiness"],
        "osint_keywords": "consulting industry AI disruption, IT outsourcing market shifts, talent war technology sector",
        "target_sources": [
            "site:gartner.com IT services outsourcing",
            "site:mckinsey.com consulting industry transformation",
            "site:forrester.com IT services market",
            "site:isg-one.com IT outsourcing trends",
            "site:hbr.org professional services",
        ],
    },
    "Healthcare": {
        "fp_themes": ["Health & Wellbeing", "Artificial Intelligence", "Sciences & Research"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Values"],
        "osint_keywords": "healthcare AI diagnostics regulation, digital health transformation, pharmaceutical supply chain risks",
        "target_sources": [
            "site:mckinsey.com healthcare transformation",
            "site:deloitte.com healthcare trends",
            "site:who.int digital health",
            "site:nature.com AI healthcare",
            "site:hbr.org healthcare innovation",
        ],
    },
    "Retail & Consumer": {
        "fp_themes": ["Business", "Data & Digitalisation", "Leisure & Lifestyles"],
        "priority_dimensions": ["Service Portfolio", "AI Readiness", "Strategy"],
        "osint_keywords": "retail AI personalization, consumer behavior shifts, omnichannel transformation 2025 2026",
        "target_sources": [
            "site:mckinsey.com retail consumer trends",
            "site:forrester.com retail market",
            "site:deloitte.com consumer industry",
            "site:hbr.org retail strategy",
            "site:gartner.com retail technology",
        ],
    },
    "Industrial & Manufacturing": {
        "fp_themes": ["Industry & Manufacturing", "Artificial Intelligence", "Sustainability & Recycling"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "industrial AI automation, smart manufacturing disruption, supply chain reshoring nearshoring",
        "target_sources": [
            "site:mckinsey.com manufacturing industry",
            "site:weforum.org manufacturing future",
            "site:deloitte.com manufacturing trends",
            "site:idc.com manufacturing AI",
            "site:bcg.com industrial transformation",
        ],
    },
    "Energy & Utilities": {
        "fp_themes": ["Energy", "Climate Change", "Sustainability & Recycling"],
        "priority_dimensions": ["Strategy", "Service Portfolio", "Operating Model"],
        "climate_exposure": "critical",
        "osint_keywords": "energy transition decarbonization, renewable energy grid transformation, carbon taxation CBAM",
        "target_sources": [
            "site:iea.org energy transition outlook",
            "site:irena.org renewable energy trends",
            "site:mckinsey.com energy transition",
            "site:weforum.org energy future",
            "site:bloomberg.com energy market",
        ],
    },
    "Financial Services": {
        "fp_themes": ["Finance & Ownership", "Artificial Intelligence", "Security & Safety"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Operating Model"],
        "osint_keywords": "fintech disruption banking, AI risk management regulation, digital currency CBDC impact",
        "target_sources": [
            "site:mckinsey.com banking financial services",
            "site:accenture.com financial services trends",
            "site:deloitte.com financial services outlook",
            "site:bis.org fintech regulation",
            "site:ecb.europa.eu digital finance",
        ],
    },
    "Insurance & Pensions": {
        "fp_themes": ["Finance & Ownership", "Data & Digitalisation", "Climate Change"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "insurtech AI underwriting, climate risk insurance pricing, pension fund ESG regulation",
        "target_sources": [
            "site:mckinsey.com insurance industry",
            "site:deloitte.com insurance trends",
            "site:swissre.com insurance outlook",
            "site:eiopa.europa.eu insurance regulation",
            "site:genevaassociation.org insurance trends",
        ],
    },
    "Forest & Paper": {
        "fp_themes": ["Nature", "Sustainability & Recycling", "Industry & Manufacturing"],
        "priority_dimensions": ["Service Portfolio", "Strategy", "Operating Model"],
        "climate_exposure": "critical",
        "osint_keywords": "bioeconomy circular economy forest, sustainable packaging disruption, EU deforestation regulation",
        "target_sources": [
            "site:fao.org forestry industry",
            "site:weforum.org bioeconomy",
            "site:ec.europa.eu forestry regulation",
            "site:mckinsey.com paper packaging",
            "site:cepi.org paper industry trends",
        ],
    },
    "Telecommunications": {
        "fp_themes": ["Communication & Media", "Data & Digitalisation", "Artificial Intelligence"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Operating Model"],
        "osint_keywords": "telecom AI network optimization, 5G private networks enterprise, telecom industry consolidation",
        "target_sources": [
            "site:mckinsey.com telecommunications trends",
            "site:gartner.com telecom industry",
            "site:gsma.com 5G industry trends",
            "site:ericsson.com mobility report",
            "site:deloitte.com telecom outlook",
        ],
    },
    "Construction & Real Estate": {
        "fp_themes": ["Construction & Urbanisation", "Sustainability & Recycling", "Energy"],
        "priority_dimensions": ["Operating Model", "Service Portfolio", "Strategy"],
        "climate_exposure": "high",
        "osint_keywords": "construction technology disruption, green building regulation, real estate market transformation",
        "target_sources": [
            "site:mckinsey.com construction industry",
            "site:weforum.org construction future",
            "site:deloitte.com real estate outlook",
            "site:ec.europa.eu construction regulation",
            "site:rics.org real estate trends",
        ],
    },
    "Transport & Logistics": {
        "fp_themes": ["Freight & Logistics", "Private & Public Transport", "Artificial Intelligence"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "climate_exposure": "high",
        "osint_keywords": "autonomous logistics AI, supply chain resilience disruption, transport decarbonization regulation",
        "target_sources": [
            "site:mckinsey.com logistics transport",
            "site:weforum.org transport future",
            "site:iea.org transport decarbonization",
            "site:freightwaves.com logistics trends",
            "site:deloitte.com transport outlook",
        ],
    },
    "Media & Publishing": {
        "fp_themes": ["Communication & Media", "Artificial Intelligence", "Values & Ethics"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Strategy"],
        "osint_keywords": "generative AI media disruption, content industry transformation, digital advertising AI shift",
        "target_sources": [
            "site:reuters.com media industry trends",
            "site:niemanlab.org journalism AI",
            "site:mckinsey.com media transformation",
            "site:deloitte.com media trends",
            "site:weforum.org media future",
        ],
    },
    "Other": {
        "fp_themes": ["Business", "Artificial Intelligence", "Global Economy"],
        "priority_dimensions": ["Strategy", "AI Readiness", "Operating Model"],
        "osint_keywords": "business model disruption AI, digital transformation enterprise, global economic uncertainty outlook",
        "target_sources": [
            "site:mckinsey.com business trends",
            "site:weforum.org global economy outlook",
            "site:hbr.org strategy disruption",
            "site:oecd.org economic outlook",
            "site:bcg.com digital transformation",
        ],
    },
}


_MIN_KB_SIGNALS_FOR_SYNTHESIS = 3  # Need at least 3 KB signals to skip Google Search


def _quick_verify_kb_signals(signals: list[dict], max_workers: int = 8) -> list[dict]:
    """
    Quick URL re-verification of KB signals. Returns only signals whose
    primary URL is still alive. Fast parallel HEAD checks.
    """
    if not signals:
        return []

    urls_to_check = {}
    for sig in signals:
        url = (sig.get("source_url") or "").strip()
        if url and url.startswith("http"):
            urls_to_check[url] = None

    if not urls_to_check:
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_verify_foresight_url, url, 6): url
            for url in urls_to_check
        }
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                urls_to_check[url] = future.result()
            except Exception:
                urls_to_check[url] = False

    alive = []
    dead = 0
    for sig in signals:
        url = (sig.get("source_url") or "").strip()
        if urls_to_check.get(url, False):
            alive.append(sig)
        else:
            dead += 1

    print(f"   🔗 [KB Verify] {len(alive)} alive, {dead} dead out of {len(signals)} KB signals")
    return alive


def _build_kb_synthesis_prompt(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    domain_config: dict,
    kb_signals: list[dict],
) -> str:
    """
    Build a synthesis-only prompt that uses pre-validated KB signals as evidence.
    No Google Search needed — all evidence is already verified.
    """
    priority_dims = ", ".join(domain_config["priority_dimensions"])
    fp_themes = ", ".join(domain_config["fp_themes"])
    n_dims = len(domain_config["priority_dimensions"])
    fin_str = f"{financial_index}/100" if financial_index is not None else "N/A"
    climate_exposure = domain_config.get("climate_exposure")

    # Format top findings
    findings_text = ""
    for f in top_findings[:8]:
        cat = f.get("category", f.get("description", ""))
        label = f.get("label", f.get("description", ""))
        sentiment = f.get("sentiment", "")
        snippet = (f.get("snippet", "") or "")[:150]
        if cat or label:
            findings_text += f"- [{sentiment}] {cat}: {label}"
            if snippet:
                findings_text += f" ({snippet})"
            findings_text += "\n"

    # Format KB signals as evidence
    evidence_lines = []
    for i, sig in enumerate(kb_signals, 1):
        theme = sig.get("_kb_theme", sig.get("fp_theme", ""))
        dims = sig.get("kb_dimensions_affected", [])
        evidence_lines.append(f"Signal {i}: {sig['title']}")
        evidence_lines.append(f"  Theme: {theme}")
        evidence_lines.append(f"  Type: {sig.get('signal_type', 'N/A')} | Timeframe: {sig.get('timeframe', 'N/A')}")
        evidence_lines.append(f"  Description: {sig.get('description', '')}")
        evidence_lines.append(f"  Primary source: {sig.get('source_url', 'N/A')} (verified, {sig.get('source_credibility', 'N/A')} credibility)")
        n_supporting = len(sig.get("supporting_sources", []))
        evidence_lines.append(f"  Corroborating sources: {n_supporting}")
        if sig.get("industries_affected"):
            evidence_lines.append(f"  Industries affected: {', '.join(sig['industries_affected'])}")
        if dims:
            evidence_lines.append(f"  KB dimensions: {', '.join(dims)}")
        evidence_lines.append(f"  Confidence: {sig.get('confidence', 'N/A')}")
        evidence_lines.append("")

    evidence_block = "\n".join(evidence_lines)

    # Climate section
    climate_section = ""
    if climate_exposure:
        climate_section = f"""
TASK 4 — CLIMATE EXPOSURE ASSESSMENT:
This company operates in a sector with {climate_exposure.upper()} climate exposure.
Based on the signals above, evaluate:
- Physical risk: How would +2°C warming impact operations/supply chain in their sector?
- Transition risk: How would aggressive carbon taxation (EU CBAM, Green Deal) affect their margins?
- AI-climate paradox: If they are adopting AI, does this create carbon contradictions?
Return a "climate_exposure" object with: risk_level (low/medium/high/critical), physical_risk (1-2 sentences), transition_risk (1-2 sentences), headline (1 sentence summary).
"""

    climate_json_line = '"climate_exposure": {"risk_level": "...", "physical_risk": "...", "transition_risk": "...", "headline": "..."},' if climate_exposure else ""

    return f"""You are a Strategic Foresight Analyst. Your task: synthesize how pre-validated external forces will impact {company_name} ({domain}) in the next 12-36 months.

IMPORTANT: All evidence below has been independently verified with 2+ authoritative sources.
You do NOT need to search for new evidence. Synthesize company-specific implications from these validated signals.

COMPANY CONTEXT:
- Company: {company_name}
- Domain: {domain}
- Archetype: {archetype} (Reality: {reality_score}/100, Financial: {fin_str})
- Key findings from forensic scan:
{findings_text or "(limited findings available)"}

RELEVANT FORESIGHT THEMES: {fp_themes}
PRIORITY KB DIMENSIONS: {priority_dims}

PRE-VALIDATED EVIDENCE (from Foresight Knowledge Base — all URLs verified):
{evidence_block}

TASK 1 — DIMENSION-LEVEL FORESIGHT:
For each of the {n_dims} priority KB dimensions, select the most impactful signal from the evidence above and explain its specific impact on {company_name}. Map each force to the most relevant KB dimension.

Per force, provide:
- "trend": The signal title from the evidence above
- "type": One of: "Plausible/Maturing" | "Weakening/Declining" | "Preposterous/Black Swan" | "Probable/Emerging"
- "timeframe": Use the timeframe from the signal
- "kb_dimension": Which priority dimension this impacts most
- "fp_theme": Which Futures Platform theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}. Frame as what must happen by when.
- "source_url": Use the primary source URL from the signal above (already verified)
- "supporting_sources": Use the supporting sources from the signal above
- "source_type": Use the source_type from the signal
- "confidence": Use the confidence from the signal
- "osint_validation": "Pre-validated via Foresight Knowledge Base with [N] corroborating source(s)" — use correct grammar: "1 corroborating source" or "2 corroborating sources"

TASK 2 — CROSS-IMPACT CHAIN:
Trace how the strongest force cascades through {company_name}'s weakest dimensions. Format: "Force X → impacts Dimension A → cascades to Dimension B → ultimately affecting Dimension C."

TASK 3 — MICRO-SCENARIOS (specific to {company_name}):
- "acceleration": What happens in 12-24 months if {company_name} acts NOW on their weakest dimensions?
- "status_quo": What happens if {company_name} continues their current trajectory?
- "disruption": What wild card could disrupt {company_name} regardless of their actions?
{climate_section}
RETURN STRICT JSON ONLY:
{{
  "dimension_forces": [
    {{
      "trend": "...",
      "type": "Plausible/Maturing|Weakening/Declining|Preposterous/Black Swan|Probable/Emerging",
      "timeframe": "6-12mo|12-24mo|24-36mo",
      "kb_dimension": "...",
      "fp_theme": "...",
      "impact_on_company": "...",
      "proposal_angle": "...",
      "source_url": "https://...",
      "supporting_sources": [{{"url": "https://...", "source_type": "...", "title": "..."}}, {{"url": "https://...", "source_type": "...", "title": "..."}}],
      "source_type": "academic|think_tank|industry_analyst|government|news",
      "confidence": "high|medium|low",
      "osint_validation": "..."
    }}
  ],
  "cross_impact_chain": "Force → Dimension A → Dimension B → Dimension C",
  "scenarios": {{
    "acceleration": "...",
    "status_quo": "...",
    "disruption": "..."
  }},
  {climate_json_line}
  "foresight_headline": "One-sentence strategic outlook for {company_name}"
}}"""


def _build_foresight_prompt(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    domain_config: dict,
    kb_context: str = "",
) -> str:
    """Build the foresight analysis prompt with domain-specific context."""

    fp_themes = ", ".join(domain_config["fp_themes"])
    priority_dims = ", ".join(domain_config["priority_dimensions"])
    osint_kw = domain_config.get("osint_keywords", "")
    climate_exposure = domain_config.get("climate_exposure")
    n_dims = len(domain_config["priority_dimensions"])
    target_sources = domain_config.get("target_sources", [])

    # Collect theme-specific sources for the FP themes relevant to this domain
    theme_sources = []
    seen_theme_queries = set()
    for theme in domain_config["fp_themes"]:
        for query in FP_THEME_SOURCES.get(theme, []):
            if query not in seen_theme_queries:
                theme_sources.append(query)
                seen_theme_queries.add(query)

    # Format top findings
    findings_text = ""
    for f in top_findings[:8]:
        cat = f.get("category", f.get("description", ""))
        label = f.get("label", f.get("description", ""))
        sentiment = f.get("sentiment", "")
        snippet = (f.get("snippet", "") or "")[:150]
        if cat or label:
            findings_text += f"- [{sentiment}] {cat}: {label}"
            if snippet:
                findings_text += f" ({snippet})"
            findings_text += "\n"

    fin_str = f"{financial_index}/100" if financial_index is not None else "N/A"

    # Climate section (only for high/critical exposure domains)
    climate_section = ""
    if climate_exposure:
        climate_section = f"""
TASK 4 — CLIMATE EXPOSURE ASSESSMENT:
This company operates in a sector with {climate_exposure.upper()} climate exposure.
Evaluate using web search:
- Physical risk: How would +2°C warming impact operations/supply chain in their sector and geography?
- Transition risk: How would aggressive carbon taxation (EU CBAM, Green Deal) affect their margins?
- AI-climate paradox: If they are adopting AI, does this create carbon contradictions?
Return a "climate_exposure" object with: risk_level (low/medium/high/critical), physical_risk (1-2 sentences), transition_risk (1-2 sentences), headline (1 sentence summary).
"""

    climate_json_line = '"climate_exposure": {"risk_level": "...", "physical_risk": "...", "transition_risk": "...", "headline": "..."},' if climate_exposure else ""

    return f"""You are a Strategic Foresight Analyst combining OSINT methodology with futures studies.
Your role: identify external forces that will impact {company_name} ({domain}) in the next 12-36 months, and translate them into actionable strategic context.

METHODOLOGY — SIFT PROTOCOL (mandatory for every signal):
1. STOP: Do not accept the first search result as truth. Verify the signal is real, not marketing.
2. INVESTIGATE THE SOURCE: Is this corporate PR, an independent analyst, academic research, or government data?
3. FIND BETTER COVERAGE: Cross-reference each force against 2+ independent sources from different perspectives.
4. TRACE CLAIMS: Link to the ORIGINAL source, not a derivative article or summary.
If a sustainability/ESG claim comes from the company itself, flag it as unverified.

COMPANY CONTEXT:
- Company: {company_name}
- Domain: {domain}
- Archetype: {archetype} (Reality: {reality_score}/100, Financial: {fin_str})
- Key findings from forensic scan:
{findings_text or "(limited findings available)"}

RELEVANT FORESIGHT THEMES (from Futures Platform taxonomy): {fp_themes}
PRIORITY KB DIMENSIONS TO EVALUATE: {priority_dims}
DOMAIN-SPECIFIC OSINT KEYWORDS: {osint_kw}

MANDATORY SOURCE TARGETING — SEARCH THESE FIRST:
Use these targeted searches to find authoritative evidence. Start with these site-specific queries before any open web search.

Industry-specific searches for {domain}:
{chr(10).join(f"- {s}" for s in target_sources) if target_sources else "- site:mckinsey.com OR site:gartner.com OR site:weforum.org OR site:hbr.org {domain} trends"}

Theme-specific searches for {fp_themes}:
{chr(10).join(f"- {s}" for s in theme_sources[:8]) if theme_sources else "- site:millennium-project.org OR site:iftf.org OR site:weforum.org megatrends"}

{kb_context}
ALLOWED SOURCE DOMAINS (use ONLY these for primary sources):
Tier 1 (preferred analysts): gartner.com, forrester.com, mckinsey.com, bcg.com, deloitte.com, pwc.com, ey.com, kpmg.com, accenture.com, hbr.org, idc.com, isg-one.com
Tier 2 (quality news): reuters.com, ft.com, bloomberg.com, economist.com, wsj.com, bbc.com
Tier 3 (academic/gov): oecd.org, imf.org, worldbank.org, ec.europa.eu, europa.eu, nature.com, sciencedirect.com, arxiv.org, ieee.org, un.org, who.int, fao.org, iea.org, irena.org, bis.org, ipcc.ch
Tier 4 (foresight orgs): weforum.org, millennium-project.org, iftf.org, cifs.dk, rand.org, chathamhouse.org, clubofrome.org, oxfordmartin.ox.ac.uk, cser.ac.uk, wfsf.org, ilo.org

DO NOT USE sources from: blogs, content farms, regional tech news sites, outsourcing directories, marketing sites, press release wires, social media, or any site not recognized as an established research/analyst/news organization.

TASK 1 — DIMENSION-LEVEL FORESIGHT:
For each of the {n_dims} priority KB dimensions above, use the MANDATORY SOURCE TARGETING searches above to identify the single most impactful EXTERNAL force affecting companies in the {domain} sector. These must be macro-level industry/regulatory/technological forces — NOT company-specific findings.

Per force, provide:
- "trend": Concrete name of the external force (specific, not vague)
- "type": One of: "Plausible/Maturing" (gathering momentum, validated trend) | "Weakening/Declining" (losing relevance or reversing) | "Preposterous/Black Swan" (low probability, massive impact — see HORIZON SCANNING instruction below) | "Probable/Emerging" (early signal, gaining traction but insufficient data)
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "kb_dimension": Which priority dimension this impacts most
- "fp_theme": Which Futures Platform theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}. Frame as what must happen by when to either capture or defend against this force (e.g., "Must pilot X by Q2 2026 or risk Y").
- "source_url": PRIMARY URL from your web search results (ORIGINAL source only — must be a real, reachable URL)
- "supporting_sources": Array of 2-3 ADDITIONAL URLs that corroborate this force from DIFFERENT publications/organizations. Each entry: {{"url": "https://...", "source_type": "...", "title": "Brief title of the source"}}. CRITICAL: These must be REAL URLs you found via web search, not fabricated. If you cannot find 2+ corroborating sources, set confidence to "low".
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "confidence": "high" (3+ verified sources) | "medium" (2 verified sources) | "low" (0-1 verified sources)
- "osint_validation": 1 sentence on how you verified this signal (e.g., "Cross-referenced OECD report with McKinsey survey"). MUST reference specific source names.

HORIZON SCANNING for "Preposterous/Black Swan" forces:
At least one force MUST be a genuine weak signal — not a "known unknown" that everyone already discusses (e.g., "AI replaces jobs" is NOT a weak signal). Instead, scan the fringes: pre-commercial R&D breakthroughs, early-stage regulatory drafts not yet widely reported, niche social movements gaining quiet momentum, or emerging scientific findings that could upend assumptions in the {domain} sector within 24-36 months. Weak signals are barely visible today but could become mainstream disruptors. Look for what is NOT yet in the headlines.

TASK 2 — CROSS-IMPACT CHAIN:
Trace how the strongest identified force cascades through {company_name}'s weakest dimensions. Format as a chain: "Force X → impacts Dimension A → which cascades to Dimension B → ultimately affecting Dimension C." This must be specific to THIS company's situation, not generic.

TASK 3 — MICRO-SCENARIOS (specific to {company_name}, not generic industry scenarios):
- "acceleration": What specifically happens in 12-24 months if {company_name} acts NOW on their weakest dimensions while riding the strengthening forces? Reference their archetype.
- "status_quo": What specifically happens if {company_name} continues their current trajectory while these forces unfold? Be concrete about consequences for a "{archetype}" company.
- "disruption": What specific wild card, competitor move, or regulatory shift could disrupt {company_name} regardless of their own actions?
{climate_section}
CRITICAL SOURCE QUALITY RULES:
- Every source_url and supporting_sources URL MUST be a real URL you retrieved from web search.
- Do NOT fabricate, guess, or construct URLs. If you cannot find a real source, omit the URL and set confidence to "low".
- Each force MUST have at least 2 supporting_sources from different organizations. Forces with fewer than 2 corroborating sources must have confidence set to "low".
- MANDATORY: All primary sources (source_url) MUST come from the ALLOWED SOURCE DOMAINS listed above. If you cannot find evidence from an allowed domain, set confidence to "low" and note this in osint_validation.
- Supporting sources should also come from allowed domains. If a supporting source is from an unknown domain, it does NOT count toward the corroboration requirement.
- REJECTED SOURCES: Do not cite blogs (medium.com, substack.com, wordpress.com), press releases (prnewswire.com, businesswire.com), social media, regional tech news sites, outsourcing directories, or marketing content. These are NOT evidence.

RETURN STRICT JSON ONLY:
{{
  "dimension_forces": [
    {{
      "trend": "...",
      "type": "Plausible/Maturing|Weakening/Declining|Preposterous/Black Swan|Probable/Emerging",
      "timeframe": "6-12mo|12-24mo|24-36mo",
      "kb_dimension": "...",
      "fp_theme": "...",
      "impact_on_company": "...",
      "proposal_angle": "...",
      "source_url": "https://...",
      "supporting_sources": [{{"url": "https://...", "source_type": "...", "title": "..."}}, {{"url": "https://...", "source_type": "...", "title": "..."}}],
      "source_type": "academic|think_tank|industry_analyst|government|news",
      "confidence": "high|medium|low",
      "osint_validation": "..."
    }}
  ],
  "cross_impact_chain": "Force → Dimension A → Dimension B → Dimension C",
  "scenarios": {{
    "acceleration": "...",
    "status_quo": "...",
    "disruption": "..."
  }},
  {climate_json_line}
  "foresight_headline": "One-sentence strategic outlook for {company_name}"
}}"""


def _extract_json_from_text(text: str) -> str | None:
    """Extract JSON from model response that may contain markdown fences or preamble."""
    if not text:
        return None
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        return text
    # Try markdown fence
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    # Try to find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return None


def _generate_foresight_from_kb(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    domain_config: dict,
    kb_signals: list[dict],
    gemini_key: str,
) -> dict | None:
    """
    KB-first foresight generation: synthesize company-specific implications
    from pre-validated KB signals WITHOUT Google Search grounding.

    This is faster, cheaper, and more reliable than the full search path.
    Signals have already been URL-verified by the caller.
    Returns None if synthesis fails (caller should fall back to full search).
    """
    prompt = _build_kb_synthesis_prompt(
        company_name, domain, archetype, reality_score,
        financial_index, top_findings, domain_config, kb_signals,
    )

    client = genai.Client(api_key=gemini_key)

    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # No Google Search tool — synthesis only from pre-validated evidence
    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=3000),
            max_output_tokens=8000,
        ), 60),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=8000,
        ), 45),
    ]

    for model_name, config, timeout_sec in models:
        try:
            print(f"   📚 [KB Synthesis] Attempting {model_name} for {company_name} (no search)...")
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"KB Synthesis {company_name} {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 100:
                print(f"   ⚠️ [KB Synthesis] {model_name} returned insufficient data ({len(text) if text else 0} chars, {elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if not json_str:
                print(f"   ⚠️ [KB Synthesis] Could not extract JSON from {model_name} response ({elapsed}ms)")
                continue

            data = json.loads(json_str, strict=False)

            if not data.get("dimension_forces") or not data.get("scenarios"):
                print(f"   ⚠️ [KB Synthesis] {model_name} returned incomplete structure ({elapsed}ms)")
                continue

            # Mark all forces as pre-validated from KB and carry over rich KB data
            for force in data["dimension_forces"]:
                force["url_verified"] = True
                force["validated"] = True
                force["_from_kb"] = True

                # Match force to KB signal — try source_url first, then title
                source_url = (force.get("source_url") or "").strip()
                force_title = (force.get("trend") or "").strip().lower()
                matched_sig = None
                for sig in kb_signals:
                    if source_url and sig.get("source_url", "").strip() == source_url:
                        matched_sig = sig
                        break
                if not matched_sig and force_title:
                    for sig in kb_signals:
                        sig_title = (sig.get("title") or "").strip().lower()
                        if sig_title and (sig_title in force_title or force_title in sig_title):
                            matched_sig = sig
                            # Also fix the source_url to use the verified KB URL
                            if sig.get("source_url"):
                                force["source_url"] = sig["source_url"]
                            break

                if matched_sig:
                    kb_supporting = matched_sig.get("supporting_sources", [])
                    if kb_supporting and not force.get("supporting_sources"):
                        force["supporting_sources"] = kb_supporting
                    # Set verified_supporting_sources (reality_check_foresight needs this)
                    force["verified_supporting_sources"] = [
                        {**ss, "url_verified": True}
                        for ss in (force.get("supporting_sources") or kb_supporting)
                    ]
                    force["source_credibility"] = matched_sig.get("source_credibility", "medium")
                    force["source_count"] = 1 + len(force["verified_supporting_sources"])
                    if matched_sig.get("description"):
                        force["_kb_description"] = matched_sig["description"]
                else:
                    force["source_credibility"] = "medium"
                    force["source_count"] = 1
                    force["verified_supporting_sources"] = []

            # Add metadata
            data["fp_themes_used"] = domain_config["fp_themes"]
            data["model"] = model_name
            data["time_ms"] = elapsed
            data["domain"] = domain
            data["kb_first"] = True  # Flag: this came from KB-first path
            data["kb_signals_used"] = len(kb_signals)

            n_forces = len(data.get("dimension_forces", []))
            print(f"   📚 [KB Synthesis] {model_name} succeeded: {n_forces} forces from {len(kb_signals)} KB signals "
                  f"({'climate' if data.get('climate_exposure') else 'no climate'}) ({elapsed}ms)")
            return data

        except json.JSONDecodeError as je:
            elapsed = int((time.time() - t0) * 1000)
            print(f"   ⚠️ [KB Synthesis] {model_name} JSON parse failed ({elapsed}ms): {je}")
            continue
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [KB Synthesis] {model_name} failed ({elapsed}ms): {e}")
            continue

    return None


def generate_foresight(
    company_name: str,
    domain: str,
    archetype: str,
    reality_score: int,
    financial_index: int | None,
    top_findings: list[dict],
    kb_dimension_scores: dict | None = None,
) -> dict | None:
    """
    Generate strategic foresight overlay for a company scan.

    KB-first approach: When sufficient pre-validated KB signals exist, synthesize
    company-specific implications without Google Search (faster, cheaper, more reliable).
    Falls back to full search-grounded generation when KB signals are insufficient.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Foresight] No GEMINI_API_KEY — skipping")
        return None

    domain_config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT["Other"])

    # --- FORESIGHT KB: TRY KB-FIRST PATH ---
    # If we have enough pre-validated KB signals for this domain's themes,
    # skip the expensive Google Search-grounded call entirely.
    # Just verify URLs are still alive and synthesize company-specific implications.
    kb_signals = []
    try:
        from foresight_harvester import get_kb_signals_for_themes
        kb_signals = get_kb_signals_for_themes(domain_config["fp_themes"])
    except Exception as kb_err:
        print(f"   ⚠️ [Foresight KB] Could not load KB signals (non-fatal): {kb_err}")

    if len(kb_signals) >= _MIN_KB_SIGNALS_FOR_SYNTHESIS:
        print(f"   📚 [Foresight KB-First] {len(kb_signals)} KB signals available — using synthesis-only path (no Google Search)")

        # Quick-verify KB signal URLs are still alive
        verified_signals = _quick_verify_kb_signals(kb_signals)

        if len(verified_signals) >= _MIN_KB_SIGNALS_FOR_SYNTHESIS:
            kb_result = _generate_foresight_from_kb(
                company_name, domain, archetype, reality_score,
                financial_index, top_findings, domain_config,
                verified_signals, gemini_key,
            )
            if kb_result:
                return kb_result
            print(f"   ⚠️ [Foresight KB-First] Synthesis failed — falling back to full search")
        else:
            print(f"   ⚠️ [Foresight KB-First] Only {len(verified_signals)} signals alive (need {_MIN_KB_SIGNALS_FOR_SYNTHESIS}) — falling back to full search")

    # --- FALLBACK: FULL SEARCH PATH ---
    # Either KB signals are insufficient or KB synthesis failed.
    # Use the original Google Search-grounded approach.
    kb_context = ""
    if kb_signals:
        try:
            from foresight_harvester import format_kb_signals_for_prompt
            kb_context = format_kb_signals_for_prompt(kb_signals, max_signals=6)
            print(f"   📚 [Foresight] Injecting {len(kb_signals)} KB signals as supplementary context")
        except Exception:
            pass

    prompt = _build_foresight_prompt(
        company_name, domain, archetype, reality_score,
        financial_index, top_findings, domain_config,
        kb_context=kb_context,
    )

    client = genai.Client(api_key=gemini_key)

    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    # Primary: Gemini 3.1 Pro Preview with Google Search grounding
    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=3000),
            max_output_tokens=8000,
        ), 120),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.5,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=8000,
        ), 90),
    ]

    for model_name, config, timeout_sec in models:
        try:
            print(f"   🔮 [Foresight] Attempting {model_name} for {company_name} ({domain})...")
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Foresight {company_name} {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 100:
                print(f"   ⚠️ [Foresight] {model_name} returned insufficient data ({len(text) if text else 0} chars, {elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if not json_str:
                print(f"   ⚠️ [Foresight] Could not extract JSON from {model_name} response ({elapsed}ms)")
                continue

            data = json.loads(json_str, strict=False)

            # Validate minimum structure
            if not data.get("dimension_forces") or not data.get("scenarios"):
                print(f"   ⚠️ [Foresight] {model_name} returned incomplete structure ({elapsed}ms)")
                continue

            # Add metadata
            data["fp_themes_used"] = domain_config["fp_themes"]
            data["model"] = model_name
            data["time_ms"] = elapsed
            data["domain"] = domain

            n_forces = len(data.get("dimension_forces", []))
            print(f"   🔮 [Foresight] {model_name} succeeded: {n_forces} forces, "
                  f"{'climate' if data.get('climate_exposure') else 'no climate'} "
                  f"({elapsed}ms)")
            return data

        except json.JSONDecodeError as je:
            elapsed = int((time.time() - t0) * 1000)
            print(f"   ⚠️ [Foresight] {model_name} JSON parse failed ({elapsed}ms): {je}")
            continue
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Foresight] {model_name} failed ({elapsed}ms): {e}")
            continue

    print(f"   ❌ [Foresight] All models failed for {company_name} — foresight skipped")
    return None


def format_foresight_for_synthesis(foresight_data: dict, company_name: str, domain: str) -> str:
    """
    Format foresight data into a context block for the synthesis prompt.
    Returns an empty string if foresight data is insufficient.
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return ""

    forces_text = ""
    for f in foresight_data["dimension_forces"]:
        forces_text += (
            f"- [{f.get('type', '?')}] {f.get('trend', '?')} ({f.get('timeframe', '?')}) "
            f"→ impacts {f.get('kb_dimension', '?')}: {f.get('impact_on_company', '?')}"
        )
        if f.get("proposal_angle"):
            forces_text += f" — {f['proposal_angle']}"
        forces_text += "\n"

    scenarios = foresight_data.get("scenarios", {})
    cross_impact = foresight_data.get("cross_impact_chain", "")
    climate = foresight_data.get("climate_exposure", {})

    climate_line = ""
    if climate and climate.get("headline"):
        climate_line = f"\nClimate exposure ({climate.get('risk_level', '?')}): {climate['headline']}"

    return f"""
STRATEGIC FORESIGHT CONTEXT (external forces, next 12-36 months):
{forces_text}
Cross-impact chain: {cross_impact}

Key scenario — Status Quo: {scenarios.get('status_quo', 'N/A')}
Key scenario — Disruption: {scenarios.get('disruption', 'N/A')}
{climate_line}

INSTRUCTION: Weave 1-2 forward-looking insights into the summary's final paragraph.
The pitch MUST reference at least one external force to create urgency.
WHY/WHAT proposals should be future-aware — frame the weakest KB dimension against
its incoming external force.
Example: Instead of "Your AI maturity needs improvement" →
"With agentic AI reshaping {domain} in 12-18 months, your current state creates
an urgent modernization window."
"""


_FORESIGHT_SUMMARY_FORBIDDEN = [
    "Thriving", "Modernizing", "Cash Cow", "At Risk",
    "Digital Taylorism", "Feature Factory", "Command and Control",
    "Zombie Scrum", "Fake Agile", "Pilot Purgatory", "Excel Hell",
    "Knowledge Base", "KB dimension", "archetype",
]


def generate_foresight_summary(
    foresight_data: dict,
    company_name: str,
    domain: str,
) -> tuple[str, str]:
    """
    Generate a 250-300 word "Future Insight" narrative from foresight data.

    Model chain: Sonnet 4.6 → Gemini 3.1 Pro → Gemini 2.5 Pro.
    Returns (summary_text, model_used).
    Returns ("", "") on failure.
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return "", ""

    # Format forces for the prompt — include rich context for better narrative
    forces = foresight_data["dimension_forces"]
    forces_text = ""
    for idx, f in enumerate(forces, 1):
        n_supporting = len(f.get("verified_supporting_sources", []) or f.get("supporting_sources", []))
        forces_text += (
            f"Force {idx}/{len(forces)}: {f.get('trend', '?')}\n"
            f"  Type: {f.get('type', '?')} | Timeframe: {f.get('timeframe', '?')} | "
            f"Theme: {f.get('fp_theme', '?')} | Confidence: {f.get('confidence', '?')}\n"
            f"  Source credibility: {f.get('source_credibility', 'unknown')} | "
            f"Corroborating sources: {n_supporting}\n"
            f"  Impacts: {f.get('kb_dimension', '?')}\n"
            f"  Company impact: {f.get('impact_on_company', '?')}\n"
        )
        if f.get("proposal_angle"):
            forces_text += f"  Strategic imperative: {f['proposal_angle']}\n"
        if f.get("_kb_description"):
            forces_text += f"  Background: {f['_kb_description']}\n"
        forces_text += "\n"

    # Validation summary for overall evidence quality context
    vs = foresight_data.get("validation_summary", {})
    if vs:
        forces_text += (
            f"Overall validation: {vs.get('validated_forces', 0)}/{vs.get('total_forces', len(forces))} forces validated, "
            f"{vs.get('total_verified_urls', 0)} verified URLs, quality={vs.get('quality', 'unknown')}\n\n"
        )

    scenarios = foresight_data.get("scenarios", {})
    cross_impact = foresight_data.get("cross_impact_chain", "")
    climate = foresight_data.get("climate_exposure", {})
    forbidden = ", ".join(_FORESIGHT_SUMMARY_FORBIDDEN)

    climate_instruction = ""
    if climate and climate.get("headline"):
        climate_instruction = f"""
Third paragraph (optional): Climate/sustainability exposure — {climate.get('headline', '')}
Risk level: {climate.get('risk_level', 'unknown')}. Physical: {climate.get('physical_risk', 'N/A')}. Transition: {climate.get('transition_risk', 'N/A')}."""

    prompt = f"""Write a "Future Insight" summary for {company_name} ({domain}).
This is a forward-looking companion to the company's diagnostic assessment.

FORESIGHT DATA:
External forces identified:
{forces_text}
Cross-impact chain: {cross_impact}

Key scenarios:
- Status quo: {scenarios.get('status_quo', 'N/A')}
- Disruption: {scenarios.get('disruption', 'N/A')}
- Acceleration: {scenarios.get('acceleration', 'N/A')}

RULES:
- 250-300 words, 3-4 paragraphs separated by \\n\\n
- IMPORTANT: This analysis is based on {len(forces)} distinct external forces. Address ALL of them — do not focus on just one.
  Weight each force by its confidence level and source credibility (high-credibility sources carry more weight).
- First paragraph: The 2-3 most impactful external forces and their timeframe.
  Name the forces concretely — no vague "market changes are coming."
  Wrap each key force name in **double asterisks** when first mentioned (e.g., "**EU SFDR 2.0 implementation**").
- Second paragraph: What this specifically means for THIS company given
  their current weaknesses. Connect the dots between external forces and internal gaps.
  Explain the cross-impact chain — how one force triggers cascading effects across multiple areas.
- Third paragraph: What the company must do to prepare — concrete strategic imperatives
  tied to the identified forces. Be specific about timelines and capabilities needed.
{climate_instruction}
- Tone: Strategic, forward-looking, grounded in evidence. Like a foresight
  analyst briefing a board of directors. Not marketing fluff.
- Do NOT reference internal scores by name or number.
- Do NOT use any of these terms: {forbidden}. Use plain business language.
- Every claim must trace to a force identified in the foresight analysis.
- If confidence is low on a force, use hedging language ("emerging signals suggest...")
- KEY FORCE HIGHLIGHTING: When naming a key external force for the first time,
  wrap it in **double asterisks** (e.g., "**generative AI disruption**").
  Do this for the 2-4 most important forces only. Do not overuse.
- FORMAT: Prose only with **bold** markers for key forces. No headers, no bullet points, no other markdown, no links.

Output only the prose text. Nothing else."""

    # --- Attempt 1: Claude Sonnet 4.6 (primary) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        SONNET_MODEL = "claude-sonnet-4-6"
        try:
            print(f"   🔮 [Future Insight] Attempting {SONNET_MODEL}...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            response = claude_client.messages.create(
                model=SONNET_MODEL,
                max_tokens=4000,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 3000,
                },
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
            text = "\n".join(text_parts).strip()
            text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)
            wc = len(text.split())
            elapsed = int((time.time() - t0) * 1000)
            print(f"   🔮 [Future Insight] {SONNET_MODEL}: {wc} words ({elapsed}ms)")

            if text and wc >= 100:
                return text, SONNET_MODEL

            print(f"   ⚠️ [Future Insight] Sonnet too short ({wc} words) — falling back to Gemini")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Future Insight] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    # --- Attempt 2-3: Gemini fallbacks ---
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Future Insight] No GEMINI_API_KEY — skipping Gemini fallbacks")
        return "", ""

    client = genai.Client(api_key=gemini_key)

    gemini_models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=4000,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
        ), 90),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=4000,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
        ), 60),
    ]

    for model_name, config, timeout_sec in gemini_models:
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Future Insight {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = (response.text or "").strip()
            text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', text)
            wc = len(text.split())
            elapsed = int((time.time() - t0) * 1000)
            print(f"   [Future Insight] {model_name}: {wc} words ({elapsed}ms)")

            if text and wc >= 100:
                return text, model_name

            print(f"   ⚠️ [Future Insight] {model_name} too short ({wc} words) — trying next")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Future Insight] {model_name} failed ({elapsed}ms): {e}")
            continue

    return "", ""


# ═══════════════════════════════════════════════════════════════════════════════
# FORESIGHT VALIDATION — URL verification + reality-check pass
# ═══════════════════════════════════════════════════════════════════════════════

# Valid source types that the SIFT protocol considers credible
_CREDIBLE_SOURCE_TYPES = {"academic", "think_tank", "industry_analyst", "government", "news"}

# Domain credibility lists — imported from shared source_quality module
_HIGH_CREDIBILITY_DOMAINS = HIGH_CREDIBILITY_DOMAINS
_LOW_CREDIBILITY_DOMAINS = LOW_CREDIBILITY_DOMAINS


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Domains that return 403 for automated requests but the URL is still real.
# These are behind paywalls, Cloudflare, or aggressive bot protection.
_PAYWALL_DOMAINS = {
    "ft.com", "wsj.com", "economist.com", "bloomberg.com", "nytimes.com",
    "gartner.com", "forrester.com", "idc.com", "hbr.org",
    "mckinsey.com", "bcg.com", "bain.com", "deloitte.com",
    "pwc.com", "ey.com", "kpmg.com", "accenture.com",
    "sciencedirect.com", "nature.com", "ieee.org", "acm.org",
    "pitchbook.com", "kauppalehti.fi", "talouselama.fi",
    "cnbc.com", "di.se", "e24.no",
}


def _is_paywall_domain(url):
    """Check if a URL belongs to a known paywalled/bot-blocking domain."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain == d or domain.endswith("." + d) for d in _PAYWALL_DOMAINS)
    except Exception:
        return False


def _verify_foresight_url(url, timeout=8):
    """
    Verify a single foresight URL exists via HTTP HEAD request.
    Returns True if the URL responds with a success/redirect status (2xx, 3xx).
    Also returns True for 403 on known paywalled domains (URL exists but is gated).
    Returns False for 404, 5xx, timeouts, or connection errors.
    """
    if not url or not url.startswith("http"):
        return False
    paywall = _is_paywall_domain(url)
    try:
        import requests as http_requests
        resp = http_requests.head(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        )
        if resp.status_code < 400:
            return True
        if paywall and resp.status_code == 403:
            return True
        return False
    except Exception:
        # Timeout, connection error, SSL error — try GET as fallback
        try:
            import requests as http_requests
            resp = http_requests.get(
                url, timeout=timeout, allow_redirects=True, stream=True,
                headers={"User-Agent": _BROWSER_UA},
            )
            resp.close()
            if resp.status_code < 400:
                return True
            if paywall and resp.status_code == 403:
                return True
            return False
        except Exception:
            return False


def _get_url_domain(url):
    """Extract the domain from a URL, stripping www. prefix."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return ""


def validate_foresight_urls(foresight_data, max_workers=6):
    """
    URL Validation Gate for foresight signals.

    Verifies source_url in each dimension_force via parallel HEAD/GET probes.
    Adds 'url_verified' field (True/False) to each force.
    Returns (foresight_data, verified_count, dead_count).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, 0, 0

    start = time.time()
    forces = foresight_data["dimension_forces"]

    # Collect unique URLs to check
    url_map = {}
    for force in forces:
        url = (force.get("source_url") or "").strip()
        if url and url.startswith("http") and url not in url_map:
            url_map[url] = None

    # Also collect supporting source URLs
    for force in forces:
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in url_map:
                url_map[ss_url] = None

    if not url_map:
        for force in forces:
            force["url_verified"] = False
        return foresight_data, 0, 0

    # Verify in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(_verify_foresight_url, url): url for url in url_map}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                url_map[url] = future.result()
            except Exception:
                url_map[url] = False

    # Apply results to forces (primary URL)
    verified_count = 0
    dead_count = 0
    for force in forces:
        url = (force.get("source_url") or "").strip()
        if url and url in url_map:
            force["url_verified"] = url_map[url]
            if url_map[url]:
                verified_count += 1
            else:
                dead_count += 1
        else:
            force["url_verified"] = False

        # Apply results to supporting sources
        verified_supporting = []
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url in url_map:
                ss["url_verified"] = url_map[ss_url]
                if url_map[ss_url]:
                    verified_supporting.append(ss)
            else:
                ss["url_verified"] = False
        force["verified_supporting_sources"] = verified_supporting

    elapsed = int((time.time() - start) * 1000)
    total = verified_count + dead_count
    total_supporting = sum(len(f.get("verified_supporting_sources", [])) for f in forces)
    print(f"   🔗 [Foresight URL Validation] {verified_count}/{total} primary URLs verified, "
          f"{total_supporting} supporting URLs verified, {dead_count} dead/unreachable ({elapsed}ms)")

    return foresight_data, verified_count, dead_count


def _build_source_retry_prompt(failed_forces, domain):
    """Build a targeted prompt to find real, verifiable sources for forces that failed URL validation."""
    forces_desc = ""
    for i, force in enumerate(failed_forces, 1):
        forces_desc += (
            f"{i}. Trend: \"{force.get('trend', '?')}\"\n"
            f"   KB Dimension: {force.get('kb_dimension', '?')}\n"
            f"   FP Theme: {force.get('fp_theme', '?')}\n"
            f"   Original (failed) URL: {force.get('source_url', 'none')}\n\n"
        )

    return f"""You are a research assistant. Your ONLY task is to find REAL, VERIFIABLE URLs for the following industry trends/forces in the {domain} sector.

The original analysis identified these forces but the source URLs could not be verified (they returned 404 or were unreachable). You must find REAL sources that confirm these trends exist.

FORCES NEEDING VERIFIED SOURCES:
{forces_desc}

RULES:
- Use web search to find REAL articles, reports, or publications that discuss each trend.
- Each source MUST be a real, currently accessible URL — not fabricated or guessed.
- ONLY use authoritative sources from these domains: gartner.com, forrester.com, mckinsey.com, bcg.com, deloitte.com, pwc.com, ey.com, kpmg.com, accenture.com, hbr.org, weforum.org, idc.com, reuters.com, ft.com, bloomberg.com, economist.com, oecd.org, imf.org, worldbank.org, ec.europa.eu, nature.com, sciencedirect.com, arxiv.org, ieee.org, and established sector-specific analyst firms.
- Do NOT use blogs, press releases, regional tech news sites, content farms, outsourcing directories, or marketing sites.
- For EACH force, provide the primary source URL plus 2 additional corroborating sources from different organizations.
- If you genuinely cannot find a verifiable source from an authoritative domain, set "found": false for that entry.

RETURN STRICT JSON ONLY:
{{
  "retry_results": [
    {{
      "trend": "exact trend name from above",
      "found": true,
      "source_url": "https://real-verified-url...",
      "source_type": "academic|think_tank|industry_analyst|government|news",
      "supporting_sources": [
        {{"url": "https://...", "source_type": "...", "title": "Brief description"}},
        {{"url": "https://...", "source_type": "...", "title": "Brief description"}}
      ],
      "osint_validation": "How you verified this (reference specific source names)"
    }}
  ]
}}"""


def retry_failed_sources(foresight_data, domain, max_retries=1):
    """
    Source Retry Loop — re-searches for verifiable sources when initial URLs fail.

    For each dimension_force where url_verified=False, makes a targeted Gemini call
    with Google Search grounding to find real, accessible URLs for the same trend.

    Updates forces in-place with new URLs and re-verifies them.
    Returns (foresight_data, newly_verified_count).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, 0

    forces = foresight_data["dimension_forces"]
    failed_forces = [f for f in forces if not f.get("url_verified", False)]

    if not failed_forces:
        print("   [Source Retry] All sources already verified — no retry needed")
        return foresight_data, 0

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        return foresight_data, 0

    print(f"   🔄 [Source Retry] {len(failed_forces)}/{len(forces)} forces have unverified sources — attempting re-search...")

    client = genai.Client(api_key=gemini_key)
    _safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    prompt = _build_source_retry_prompt(failed_forces, domain)

    models = [
        ("gemini-3.1-pro-preview", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.3,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=6000,
        ), 90),
        ("gemini-2.5-pro", genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            safety_settings=_safety,
            temperature=0.3,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=1024),
            max_output_tokens=6000,
        ), 60),
    ]

    retry_data = None
    for model_name, config, timeout_sec in models:
        try:
            t0 = time.time()
            response = call_with_503_backoff(
                lambda mn=model_name, cfg=config: client.models.generate_content(
                    model=mn, contents=prompt, config=cfg,
                ),
                label=f"Source Retry {model_name}",
                model=model_name,
                timeout=timeout_sec,
            )
            text = response.text if hasattr(response, "text") else ""
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 50:
                print(f"   ⚠️ [Source Retry] {model_name} returned insufficient data ({elapsed}ms)")
                continue

            json_str = _extract_json_from_text(text)
            if json_str:
                retry_data = json.loads(json_str, strict=False)
                print(f"   🔄 [Source Retry] {model_name} returned {len(retry_data.get('retry_results', []))} retry results ({elapsed}ms)")
                break
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ⚠️ [Source Retry] {model_name} failed ({elapsed}ms): {e}")
            continue

    if not retry_data or not retry_data.get("retry_results"):
        print("   ⚠️ [Source Retry] No retry results obtained")
        return foresight_data, 0

    # Build lookup: trend name → retry result
    retry_lookup = {}
    for r in retry_data["retry_results"]:
        trend = (r.get("trend") or "").strip().lower()
        if trend and r.get("found"):
            retry_lookup[trend] = r

    # Apply retry results to failed forces and collect new URLs to verify
    new_urls = {}
    updated_forces = []
    for force in failed_forces:
        trend_key = (force.get("trend") or "").strip().lower()
        retry = retry_lookup.get(trend_key)
        if retry and retry.get("source_url"):
            force["source_url"] = retry["source_url"]
            force["source_type"] = retry.get("source_type", force.get("source_type", "news"))
            force["osint_validation"] = retry.get("osint_validation", force.get("osint_validation", ""))
            if retry.get("supporting_sources"):
                force["supporting_sources"] = retry["supporting_sources"]
            new_urls[retry["source_url"]] = None
            # Also collect supporting source URLs for verification
            for ss in retry.get("supporting_sources", []):
                ss_url = (ss.get("url") or "").strip()
                if ss_url and ss_url.startswith("http"):
                    new_urls[ss_url] = None
            updated_forces.append(force)

    if not new_urls:
        print("   ⚠️ [Source Retry] No new URLs found in retry results")
        return foresight_data, 0

    # Verify the new URLs in parallel
    print(f"   🔗 [Source Retry] Verifying {len(new_urls)} new URLs...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_to_url = {executor.submit(_verify_foresight_url, url): url for url in new_urls}
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                new_urls[url] = future.result()
            except Exception:
                new_urls[url] = False

    # Apply verification results
    newly_verified = 0
    for force in updated_forces:
        url = (force.get("source_url") or "").strip()
        if url in new_urls and new_urls[url]:
            force["url_verified"] = True
            newly_verified += 1
        else:
            force["url_verified"] = False

        # Also verify supporting sources
        verified_supporting = []
        for ss in force.get("supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url in new_urls:
                ss["url_verified"] = new_urls[ss_url]
                if new_urls[ss_url]:
                    verified_supporting.append(ss)
            else:
                ss["url_verified"] = False
        force["verified_supporting_sources"] = verified_supporting

    print(f"   🔄 [Source Retry] Re-search complete: {newly_verified}/{len(updated_forces)} forces now have verified sources")
    return foresight_data, newly_verified


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMUM SOURCE QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum thresholds for foresight to be considered trustworthy
_MIN_VERIFIED_SOURCES_RATIO = 0.34  # At least ~1/3 of forces must have verified primary URL
_MIN_VERIFIED_SOURCES_ABSOLUTE = 1  # At least 1 force must have a verified source
_MIN_TOTAL_VERIFIED_URLS = 2        # Across all forces (primary + supporting), need at least 2 verified URLs


def enforce_source_quality_gate(foresight_data):
    """
    Minimum Source Quality Gate — checks if foresight meets evidence thresholds.

    Rules:
    - At least 1 force must have a verified primary source URL
    - At least 1/3 of forces must have verified sources
    - Across all forces, at least 2 total verified URLs (primary + supporting)
    - If thresholds not met, marks foresight as insufficient_evidence=True

    Returns (foresight_data, passes_gate: bool).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data, False

    forces = foresight_data["dimension_forces"]
    total_forces = len(forces)

    # Count verified primary sources
    verified_primary = sum(1 for f in forces if f.get("url_verified", False))

    # Count total verified URLs (primary + supporting)
    total_verified_urls = verified_primary
    for force in forces:
        for ss in force.get("supporting_sources", []):
            if ss.get("url_verified", False):
                total_verified_urls += 1
        for ss in force.get("verified_supporting_sources", []):
            total_verified_urls += 1  # These are pre-filtered as verified

    # Check thresholds
    passes_ratio = verified_primary >= (total_forces * _MIN_VERIFIED_SOURCES_RATIO)
    passes_absolute = verified_primary >= _MIN_VERIFIED_SOURCES_ABSOLUTE
    passes_total_urls = total_verified_urls >= _MIN_TOTAL_VERIFIED_URLS

    passes_gate = passes_ratio and passes_absolute and passes_total_urls

    if not passes_gate:
        foresight_data["insufficient_evidence"] = True
        reasons = []
        if not passes_absolute:
            reasons.append(f"no verified primary sources (need >= {_MIN_VERIFIED_SOURCES_ABSOLUTE})")
        if not passes_ratio:
            reasons.append(f"verified ratio {verified_primary}/{total_forces} below {_MIN_VERIFIED_SOURCES_RATIO:.0%} threshold")
        if not passes_total_urls:
            reasons.append(f"only {total_verified_urls} total verified URLs (need >= {_MIN_TOTAL_VERIFIED_URLS})")
        foresight_data["gate_failure_reasons"] = reasons
        print(f"   🚫 [Quality Gate] FAILED — {', '.join(reasons)}")
    else:
        foresight_data["insufficient_evidence"] = False
        print(f"   ✅ [Quality Gate] PASSED — {verified_primary}/{total_forces} primary verified, "
              f"{total_verified_urls} total verified URLs")

    return foresight_data, passes_gate


def reality_check_foresight(foresight_data, company_name=""):
    """
    Reality-check pass for foresight signals — SIFT-based quality assessment.

    Evaluates each dimension_force for:
    - Source credibility (source_type + domain reputation)
    - URL verification status
    - OSINT validation quality
    - Confidence calibration

    Adds per-force fields:
    - 'source_credibility': "high" | "medium" | "low"
    - 'validation_flags': list of warning strings
    - 'validated': True if force passes minimum quality threshold

    Adds aggregate fields to foresight_data:
    - 'validation_summary': dict with counts and overall quality assessment
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return foresight_data

    forces = foresight_data["dimension_forces"]
    total_validated = 0
    total_flagged = 0
    all_flags = []

    for force in forces:
        flags = []
        source_url = (force.get("source_url") or "").strip()
        source_type = (force.get("source_type") or "").strip().lower()
        confidence = (force.get("confidence") or "").strip().lower()
        osint_validation = (force.get("osint_validation") or "").strip()
        url_verified = force.get("url_verified", False)
        domain = _get_url_domain(source_url)

        # --- Source type validation ---
        if source_type not in _CREDIBLE_SOURCE_TYPES:
            flags.append(f"Unknown source type: '{source_type}'")

        # --- URL verification ---
        if not source_url or not source_url.startswith("http"):
            flags.append("No source URL provided")
        elif not url_verified:
            flags.append("Source URL could not be verified (dead/unreachable)")

        # --- Domain credibility assessment (shared module) ---
        company_domain = ""
        if company_name:
            company_name_lower = company_name.lower().replace(" ", "")
            if domain and company_name_lower in domain.replace(".", ""):
                company_domain = domain
        credibility = assess_source_credibility(source_url, source_type, company_domain)
        if credibility == "low" and domain:
            flags.append(f"Low-credibility domain: {domain}")
        if company_domain:
            flags.append("Source appears to be company's own website — may be self-reported")
        if not domain and source_url:
            flags.append("Could not extract domain from URL")

        # --- OSINT validation quality ---
        if not osint_validation:
            flags.append("No OSINT validation description provided")
        elif len(osint_validation) < 20:
            flags.append("OSINT validation description too brief")

        # --- Confidence calibration ---
        # Downgrade confidence if URL is dead or source is low-credibility
        if confidence == "high" and (not url_verified or credibility == "low"):
            force["confidence"] = "medium"
            flags.append(f"Confidence downgraded from high to medium (url_verified={url_verified}, credibility={credibility})")
        elif confidence == "medium" and not url_verified and credibility == "low":
            force["confidence"] = "low"
            flags.append("Confidence downgraded from medium to low (unverified + low-credibility)")

        # --- Supporting source quality ---
        verified_supporting = force.get("verified_supporting_sources", [])
        supporting_sources = force.get("supporting_sources", [])
        total_supporting = len(supporting_sources)
        verified_supporting_count = len(verified_supporting)

        if total_supporting == 0:
            flags.append("No supporting sources provided (SIFT requires 2+ independent sources)")
        elif verified_supporting_count == 0:
            flags.append(f"None of {total_supporting} supporting sources could be verified")
        elif verified_supporting_count < 2:
            flags.append(f"Only {verified_supporting_count}/{total_supporting} supporting sources verified (need 2+)")

        # --- Determine validation status ---
        # A force is "validated" if it has a verified primary URL from a credible source
        # AND at least 1 verified supporting source (multi-source requirement)
        has_verified_primary = url_verified and credibility in ("high", "medium")
        has_corroboration = verified_supporting_count >= 1
        validated = has_verified_primary and has_corroboration and len(flags) <= 3

        force["source_credibility"] = credibility
        force["validation_flags"] = flags
        force["validated"] = validated
        force["source_count"] = 1 + verified_supporting_count if url_verified else verified_supporting_count

        if validated:
            total_validated += 1
        if flags:
            total_flagged += 1
            all_flags.extend(flags)

    # --- Aggregate validation summary (tightened thresholds) ---
    total_forces = len(forces)
    # Count total verified URLs across all forces (primary + supporting)
    total_verified_urls = sum(
        (1 if f.get("url_verified") else 0) + len(f.get("verified_supporting_sources", []))
        for f in forces
    )
    # HIGH: all forces validated (verified primary + corroboration)
    # MEDIUM: at least 2 forces have verified primary URLs AND total verified URLs >= 3
    # LOW: anything below that
    quality = "high" if total_validated == total_forces else (
        "medium" if (total_validated >= 2 or (total_validated >= 1 and total_verified_urls >= 3)) else "low"
    )

    foresight_data["validation_summary"] = {
        "total_forces": total_forces,
        "validated_forces": total_validated,
        "flagged_forces": total_flagged,
        "total_verified_urls": total_verified_urls,
        "quality": quality,
        "all_flags": all_flags[:20],  # cap to prevent bloat
    }

    print(f"   🔍 [Foresight Reality Check] {total_validated}/{total_forces} forces validated, "
          f"{total_verified_urls} total verified URLs, quality={quality}, {len(all_flags)} flags")

    return foresight_data


def extract_foresight_links(foresight_data, company_name=""):
    """
    Extract validated foresight sources into a foresight_links array
    that mirrors the evidence_links schema used throughout the Scanner.

    Each link has:
    - title: Force trend name
    - url: source_url
    - snippet: impact_on_company text
    - source_type: academic/think_tank/etc.
    - verified: True/False (from URL verification)
    - source_credibility: high/medium/low (from reality check)
    - validated: True/False (passed quality threshold)
    - kb_dimension: Which KB dimension this force impacts
    - fp_theme: Futures Platform theme
    - timeframe: Force timeframe
    - force_type: Plausible/Maturing, etc.
    - origin: "foresight" (to distinguish from forensic evidence_links)

    Returns list of foresight_link dicts, sorted by credibility (high first).
    """
    if not foresight_data or not foresight_data.get("dimension_forces"):
        return []

    links = []
    seen_urls = set()
    for force in foresight_data["dimension_forces"]:
        source_url = (force.get("source_url") or "").strip()
        if source_url and source_url.startswith("http") and source_url not in seen_urls:
            seen_urls.add(source_url)
            link = {
                "title": force.get("trend", "Unknown Force"),
                "url": source_url,
                "snippet": (force.get("impact_on_company") or "")[:300],
                "source_type": force.get("source_type", "unknown"),
                "verified": force.get("url_verified", False),
                "source_credibility": force.get("source_credibility", "unknown"),
                "validated": force.get("validated", False),
                "kb_dimension": force.get("kb_dimension", ""),
                "fp_theme": force.get("fp_theme", ""),
                "timeframe": force.get("timeframe", ""),
                "force_type": force.get("type", ""),
                "origin": "foresight",
                "is_primary": True,
            }
            links.append(link)

        # Also include verified supporting sources
        for ss in force.get("verified_supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in seen_urls:
                seen_urls.add(ss_url)
                link = {
                    "title": ss.get("title", force.get("trend", "Unknown Force")),
                    "url": ss_url,
                    "snippet": (force.get("impact_on_company") or "")[:300],
                    "source_type": ss.get("source_type", "unknown"),
                    "verified": True,
                    "source_credibility": "medium",
                    "validated": True,
                    "kb_dimension": force.get("kb_dimension", ""),
                    "fp_theme": force.get("fp_theme", ""),
                    "timeframe": force.get("timeframe", ""),
                    "force_type": force.get("type", ""),
                    "origin": "foresight",
                    "is_primary": False,
                }
                links.append(link)

    # Sort: validated first, then by credibility
    credibility_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    links.sort(key=lambda l: (
        0 if l["validated"] else 1,
        credibility_order.get(l["source_credibility"], 3),
    ))

    if links:
        validated_count = sum(1 for l in links if l["validated"])
        print(f"   📎 [Foresight Links] Extracted {len(links)} source links ({validated_count} validated)")

    return links
