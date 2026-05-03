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

Methodology inspired by Strategic Foresight 30-theme taxonomy (NAICS/SIC-based)
and SIFT/Lateral Reading OSINT validation principles.
"""

import json
import os
import re
import time
import concurrent.futures
from datetime import datetime

import anthropic
from google import genai
from google.genai import types as genai_types
from gemini_backoff import call_with_503_backoff
from gemini_client import get_gemini_client
from source_quality import HIGH_CREDIBILITY_DOMAINS, LOW_CREDIBILITY_DOMAINS, assess_source_credibility, classify_generation_context
from foresight_guardrails import build_foresight_guardrails, annotate_foresight_signal, annotate_foresight_link, validate_foresight_text, sanitize_foresight_text, sanitize_future_insight, sanitize_foresight_record, sanitize_foresight_collection, sanitize_report_foresight_fields


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGIC FORESIGHT 30-THEME TAXONOMY
# Complete list of the 30 FP Themes utilized in Future Insight creation.
# Each theme represents a macro-level domain of change that shapes the external
# environment companies must navigate. Used to tag dimension-level forces and
# ensure comprehensive coverage across all foresight analyses.
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEMES_30 = [
    # --- Technology & Digital ---
    "Artificial Intelligence",          #  1 — Generative AI, AI governance, AI safety, human-AI collaboration
    "Data & Digitalisation",            #  2 — Data governance, datafication, digital divide, privacy
    "Communication & Media",            #  3 — Media transformation, disinformation, platform governance

    # --- Economy & Business ---
    "Business",                         #  4 — Business model transformation, stakeholder capitalism
    "Finance & Ownership",              #  5 — Banking transformation, fintech, CBDCs, financial inclusion
    "Global Economy",                   #  6 — Global trade, geoeconomics, deglobalisation, sovereign debt
    "Digital & Sharing Economies",      #  7 — Platform economy, gig labour rights, digital labour governance
    "Services",                         #  8 — Service economy transformation, AI-augmented services

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing",         #  9 — Industry 5.0, industrial decarbonisation, reshoring
    "Construction & Urbanisation",      # 10 — Climate-resilient cities, housing, urban governance
    "Energy",                           # 11 — Energy transition, hydrogen, energy security, just transition
    "Freight & Logistics",              # 12 — Supply chain resilience, freight decarbonisation, nearshoring
    "Private & Public Transport",       # 13 — EVs, autonomous mobility, MaaS, aviation decarbonisation

    # --- Environment & Resources ---
    "Climate Change",                   # 14 — Climate tipping points, climate finance, climate justice
    "Sustainability & Recycling",       # 15 — Circular economy, regenerative systems, ESG transformation
    "Nature",                           # 16 — Biodiversity, natural capital, nature-based solutions, rewilding
    "Food",                             # 17 — Food systems transformation, agroecology, cellular proteins
    "Water & Oceans",                   # 18 — Water security, ocean governance, water-food-energy nexus

    # --- Society & Governance ---
    "Public Governance",                # 19 — Democratic governance, digital governance, institutional trust
    "Geopolitics",                      # 20 — Great power competition, multipolar order, tech nationalism
    "Security & Safety",                # 21 — Cybersecurity, hybrid warfare, biosecurity, societal resilience
    "Values & Ethics",                  # 22 — AI ethics, bioethics, intergenerational justice
    "Society",                          # 23 — Social inequality, polarisation, social capital, loneliness

    # --- People & Well-being ---
    "Health & Wellbeing",               # 24 — Healthcare transformation, precision medicine, pandemic preparedness
    "Education & Knowledge",            # 25 — Education futures, AI in education, knowledge democracy
    "Work & Income",                    # 26 — Future of work, automation displacement, income security models
    "Management & HR",                  # 27 — Leadership futures, hybrid work, DEI, people analytics
    "Leisure & Lifestyles",             # 28 — Experience economy, digital leisure, wellness economy

    # --- Science & Demographics ---
    "Sciences & Research",              # 29 — Frontier sciences, convergent technologies, open science
    "Population & Demographics",        # 30 — Population dynamics, ageing, migration, generational transitions
]

# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGIC FORESIGHT THEME DESCRIPTIONS
# Revised, futures-oriented descriptions for each of the 30 themes.
# Based on academic analysis applying futures studies methodology.
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEME_DESCRIPTIONS = {
    "Artificial Intelligence": "Generative AI, machine learning, autonomous systems, AI governance and regulation, AI safety, human-AI collaboration, societal and economic AI impacts",
    "Data & Digitalisation": "Data governance and sovereignty, digital transformation, datafication, open data ecosystems, digital divide and inclusion, privacy and data rights",
    "Communication & Media": "Media transformation, journalism futures, platform governance, disinformation and synthetic media, social media dynamics, digital content ecosystems, trust in information",
    "Business": "Business model transformation, corporate strategy, entrepreneurship and innovation ecosystems, organizational resilience, stakeholder capitalism, platform disruption",
    "Finance & Ownership": "Banking transformation, investment futures, fintech and CBDCs, financial inclusion, wealth distribution and inequality, alternative and commons-based ownership models",
    "Global Economy": "Global trade, macroeconomics, supply chain transformation, geoeconomics, deglobalisation, emerging economies, sovereign debt, global inequality",
    "Digital & Sharing Economies": "Platform economy, algorithmic management and gig labour rights, peer-to-peer models, platform monopolies, collaborative and commons-based digital economies, digital labour governance",
    "Services": "Service economy transformation, AI-augmented services, public service innovation, service design futures, platform-mediated services, knowledge-intensive services",
    "Industry & Manufacturing": "Smart manufacturing and Industry 5.0, industrial automation, industrial decarbonisation, reshoring and industrial resilience, additive manufacturing, green industrial policy",
    "Construction & Urbanisation": "Urban futures and climate-resilient cities, housing systems, smart urban infrastructure, urban planning innovation, construction technology, built environment sustainability",
    "Energy": "Renewable energy transition, energy grids and storage, nuclear renaissance, hydrogen economy, energy security, just transition, energy geopolitics",
    "Freight & Logistics": "Supply chain resilience and transformation, freight decarbonisation, autonomous and digital logistics, nearshoring, maritime and port futures, last-mile innovation",
    "Private & Public Transport": "Electric vehicles and decarbonisation, autonomous mobility, Mobility-as-a-Service, shared and active mobility, public transit futures, aviation decarbonisation, mobility equity",
    "Climate Change": "Climate mitigation and adaptation, carbon markets and removal technologies, climate tipping points, climate finance and loss and damage, climate justice, geoengineering governance",
    "Sustainability & Recycling": "Circular economy, sustainable production and consumption, regenerative systems, ESG and corporate sustainability transformation, material flows, biodegradable innovation",
    "Nature": "Biodiversity loss and recovery, ecosystem services, natural capital valuation, nature-based solutions, rewilding, land and ocean governance, biodiversity finance",
    "Food": "Food systems transformation, agricultural innovation, food security and sovereignty, alternative and cellular proteins, agroecology, food waste, nutrition futures",
    "Water & Oceans": "Water security and freshwater scarcity, ocean governance and health, marine economy, ocean acidification, water-food-energy nexus, desalination and water technology",
    "Public Governance": "Democratic governance futures, public-sector innovation, digital and AI governance, regulatory transformation, open government and citizen participation, institutional trust",
    "Geopolitics": "Great power competition, multipolar world order, economic statecraft and tech nationalism, regional alliances, conflict dynamics, multilateral system transformation, climate geopolitics",
    "Security & Safety": "Cybersecurity, hybrid warfare, critical infrastructure resilience, defence technology, biosecurity, information operations, privacy and surveillance, societal resilience",
    "Values & Ethics": "Digital and AI ethics, bioethics, intergenerational justice, social trust, value pluralism, corporate ethics and accountability, ethics of emerging technologies",
    "Society": "Social inequality, polarisation and cohesion, social capital, civil society and social movements, community futures, loneliness and belonging, demographic social transformation",
    "Health & Wellbeing": "Healthcare transformation, mental health futures, biotech and precision medicine, longevity and ageing health, pandemic preparedness, health equity, AI in healthcare, One Health",
    "Education & Knowledge": "Education futures, lifelong and distributed learning, AI in education, skills transformation, future of universities, knowledge democracy, learning sciences",
    "Work & Income": "Future of work, labour market transformation, automation and displacement, digital labour rights, income security models, skills transitions, labour geographies",
    "Management & HR": "Leadership futures, organisational design transformation, talent management, distributed and hybrid work, DEI strategy, people analytics and HR technology, organisational culture",
    "Leisure & Lifestyles": "Consumer culture transformation, experience economy, digital leisure and gaming, wellness and wellbeing economy, sustainable tourism, lifestyle futures, platform-mediated consumption",
    "Sciences & Research": "Frontier sciences, convergent technologies, R&D systems transformation, open science, science-policy interface, quantum computing, space science, synthetic biology",
    "Population & Demographics": "Population dynamics and fertility trends, ageing societies, migration and climate displacement, demographic dividends, labour demographics, generational transitions",
}

# ═══════════════════════════════════════════════════════════════════════════════
# FP THEME → AUTHORITATIVE SOURCE MAPPING
# Maps each of the 30 Strategic Foresight themes to the most relevant authoritative
# sources for finding evidence about that theme. These are "horizontal" sources
# (theme-specific, cross-industry) vs the "vertical" per-industry target_sources.
#
# Source quality tiers (applied during curation):
# Tier 1 — Highest: Intergovernmental bodies, UN agencies, flagship journals
# Tier 2 — High: Independent think tanks, major academic institutions
# Tier 3 — Solid: Specialist research bodies, futures organisations, credible NGOs
# Tier 4 — Supplementary: Commercial analysts (used selectively)
# ═══════════════════════════════════════════════════════════════════════════════

FP_THEME_SOURCES = {
    # --- Technology & Digital ---
    "Artificial Intelligence": [
        "site:oxfordmartin.ox.ac.uk AI governance future risks",           # Oxford Martin — AI governance, future risks
        "site:hai.stanford.edu AI human-centred",                          # Stanford HAI — human-centred AI, AI Index
        "site:technologyreview.com artificial intelligence",               # MIT Tech Review — AI trends and critique
        "site:turing.ac.uk artificial intelligence safety",                # Alan Turing Institute — AI safety, applications
        "site:cser.ac.uk artificial intelligence existential risk",        # CSER Cambridge — AI existential risk
        "site:futureoflife.org artificial intelligence governance",        # Future of Life Institute — AI governance, risk
        "site:ainowinstitute.org social impacts AI",                       # AI Now Institute — social impacts of AI
        "site:rand.org artificial intelligence policy",                    # RAND — AI policy analysis
        "site:oecd.org artificial intelligence principles",                # OECD — AI Principles, policy observatory
        "site:ec.europa.eu AI Act regulation",                             # European Commission — EU AI Act
        "site:isi.fraunhofer.de artificial intelligence",                  # Fraunhofer ISI — AI technology research
        "site:brookings.edu artificial intelligence governance",           # Brookings — AI governance
        "site:weforum.org artificial intelligence impact",                 # WEF — AI global impact, adoption
        "site:iftf.org artificial intelligence futures",                   # IFTF — long-range AI futures
    ],
    "Data & Digitalisation": [
        "site:oecd.org digital economy data policy",                       # OECD — digital economy, data policy
        "site:ec.europa.eu data strategy digital markets",                 # EU — data strategy, digital markets
        "site:weforum.org digital transformation",                         # WEF — digital transformation
        "site:mckinsey.com digitalisation impact",                         # McKinsey — digitalisation impact
        "site:itu.int digital development connectivity",                   # ITU — digital development, connectivity
        "site:worldbank.org digital dividends",                            # World Bank — digital dividends
        "site:isi.fraunhofer.de digitalisation",                           # Fraunhofer ISI — digitalisation research
        "site:turing.ac.uk data science",                                  # Alan Turing Institute — data science
        "site:theodi.org data governance",                                 # Open Data Institute — data governance
        "site:bennettinstitute.cam.ac.uk digital public policy",           # Bennett Institute Cambridge — digital policy
        "site:sitra.fi fair data economy",                                 # Sitra — fair data economy
        "site:brookings.edu digital economy",                              # Brookings — digital economy
    ],
    "Communication & Media": [
        "site:reutersinstitute.politics.ox.ac.uk journalism future",       # Reuters Institute — journalism futures, Digital News Report
        "site:niemanlab.org future journalism",                            # Harvard Nieman Lab — journalism innovation
        "site:oii.ox.ac.uk platform media disinformation",                 # Oxford Internet Institute — platform media
        "site:pewresearch.org media consumption news",                     # Pew — media consumption, news habits
        "site:knightfoundation.org journalism media trust",                # Knight Foundation — journalism, media trust
        "site:poynter.org journalism ethics fact-checking",                # Poynter Institute — journalism ethics
        "site:firstdraftnews.org disinformation",                          # First Draft — disinformation research
        "site:weforum.org media transformation",                           # WEF — media transformation
        "site:iftf.org communication futures",                             # IFTF — communication futures
        "site:cifs.dk media communication trends",                         # CIFS — media and communication trends
        "site:media.mit.edu digital media innovation",                     # MIT Media Lab — digital media innovation
        "site:freedomhouse.org press freedom",                             # Freedom House — press freedom
    ],

    # --- Economy & Business ---
    "Business": [
        "site:mckinsey.com business transformation",                       # McKinsey — business transformation
        "site:hbr.org strategy management innovation",                     # HBR — strategy and management innovation
        "site:bcg.com business strategy",                                  # BCG Henderson Institute — business strategy
        "site:sloanreview.mit.edu management research",                    # MIT Sloan — management research
        "site:weforum.org global business futures",                        # WEF — global business futures
        "site:oecd.org business dynamism innovation policy",               # OECD — business dynamism, innovation policy
        "site:nesta.org.uk innovation ecosystems",                         # NESTA — innovation ecosystems
        "site:deloitte.com business transformation",                       # Deloitte — business transformation
        "site:kauffman.org entrepreneurship",                              # Kauffman Foundation — entrepreneurship
        "site:wbcsd.org sustainable business models",                      # WBCSD — sustainable business models
        "site:ssir.org social innovation purpose business",                # Stanford Social Innovation Review
        "site:brookings.edu business economics",                           # Brookings — business economics
    ],
    "Finance & Ownership": [
        "site:bis.org fintech monetary policy",                            # BIS — central banking, fintech
        "site:imf.org financial system stability",                         # IMF — global financial system
        "site:fsb.org financial stability regulation",                     # FSB — financial stability
        "site:worldbank.org financial inclusion",                          # World Bank — financial inclusion
        "site:oecd.org financial markets capital",                         # OECD — financial markets
        "site:ecb.europa.eu CBDC monetary innovation",                     # ECB — CBDC, monetary innovation
        "site:mckinsey.com financial services transformation",             # McKinsey — financial services
        "site:weforum.org financial system futures",                       # WEF — financial system futures
        "site:chathamhouse.org finance geopolitics",                       # Chatham House — global finance
        "site:ineteconomics.org alternative economic architectures",       # INET — alternative economics
        "site:neweconomics.org alternative ownership",                     # NEF — alternative ownership models
        "site:sitra.fi fair economy ownership",                            # Sitra — fair economy, ownership
        "site:brookings.edu financial regulation",                         # Brookings — financial regulation
    ],
    "Global Economy": [
        "site:imf.org world economic outlook",                             # IMF — World Economic Outlook
        "site:oecd.org economic analysis indicators",                      # OECD — economic analysis
        "site:worldbank.org development economics",                        # World Bank — development economics
        "site:wto.org global trade",                                       # WTO — global trade
        "site:unctad.org trade development",                               # UNCTAD — trade and development
        "site:weforum.org global competitiveness",                         # WEF — global competitiveness
        "site:chathamhouse.org geoeconomics",                              # Chatham House — geoeconomics
        "site:piie.com international economics",                           # Peterson Institute — international economics
        "site:brookings.edu global economy",                               # Brookings — global economy
        "site:atlanticcouncil.org economic statecraft",                    # Atlantic Council — economic statecraft
        "site:bruegel.org european economic policy",                       # Bruegel — European economic policy
        "site:dni.gov global economic trends",                             # NIC — global economic trends
        "site:millennium-project.org global economic challenges",          # Millennium Project — global challenges
    ],
    "Digital & Sharing Economies": [
        "site:oecd.org platform economy digital markets",                  # OECD — platform economy
        "site:ec.europa.eu digital markets act platform regulation",       # EU — Digital Markets Act
        "site:ilo.org gig work digital labour",                            # ILO — gig work, digital labour
        "site:mckinsey.com platform economy",                              # McKinsey — platform economy
        "site:weforum.org sharing economy futures",                        # WEF — sharing economy futures
        "site:oii.ox.ac.uk platform dynamics digital labour",              # Oxford Internet Institute — digital labour
        "site:iftf.org future platforms work",                             # IFTF — future platforms and work
        "site:fair.work platform labour rights",                           # Fairwork Foundation — platform labour
        "site:sitra.fi fair data economy",                                 # Sitra — fair data economy
        "site:deloitte.com platform business models",                      # Deloitte — platform models
        "site:brookings.edu digital competition",                          # Brookings — digital competition
        "site:media.mit.edu collaborative digital models",                 # MIT Media Lab — collaborative models
    ],
    "Services": [
        "site:mckinsey.com service sector transformation",                 # McKinsey — service sector
        "site:oecd.org services trade innovation",                         # OECD — services trade and innovation
        "site:weforum.org services economy futures",                       # WEF — services economy
        "site:hbr.org service innovation management",                      # HBR — service innovation
        "site:deloitte.com service transformation",                        # Deloitte — service transformation
        "site:forrester.com customer experience service futures",          # Forrester — customer experience
        "site:nesta.org.uk public service innovation",                     # NESTA — public service innovation
        "site:thegovlab.org data-driven public services",                  # GovLab NYU — public services
        "site:thersa.org service futures",                                 # RSA — service futures
        "site:isi.fraunhofer.de service research",                         # Fraunhofer ISI — service research
        "site:sitra.fi service innovation",                                # Sitra — service innovation
        "site:pwc.com service industry transformation",                    # PwC — service industry
    ],

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing": [
        "site:weforum.org manufacturing future industry",                  # WEF — manufacturing futures, Industry 4.0/5.0
        "site:isi.fraunhofer.de industrial technology research",           # Fraunhofer ISI — industrial technology
        "site:oecd.org industrial policy",                                 # OECD — industrial policy
        "site:ec.europa.eu industrial strategy",                           # EU — industrial strategy
        "site:unido.org sustainable industrial development",               # UNIDO — sustainable industrialisation
        "site:mckinsey.com manufacturing transformation",                  # McKinsey — manufacturing transformation
        "site:iea.org industrial decarbonisation",                         # IEA — industrial decarbonisation
        "site:deloitte.com manufacturing trends",                          # Deloitte — manufacturing trends
        "site:bcg.com industry green manufacturing",                       # BCG — Industry 4.0/5.0
        "site:brookings.edu reindustrialisation",                          # Brookings — reindustrialisation
        "site:nesta.org.uk industrial innovation",                         # NESTA — industrial innovation
        "site:ipc.mit.edu manufacturing competitiveness",                  # MIT Industrial Performance Center
    ],
    "Construction & Urbanisation": [
        "site:unhabitat.org urban development housing",                    # UN Habitat — urban development, housing
        "site:oecd.org urbanisation policy",                               # OECD — urbanisation policy
        "site:c40.org climate-resilient cities",                           # C40 Cities — climate-resilient cities
        "site:weforum.org urban transformation",                           # WEF — urban transformation
        "site:lse.ac.uk cities urban research",                            # LSE Cities — urban research
        "site:wri.org sustainable cities",                                 # WRI Ross Centre — sustainable cities
        "site:ec.europa.eu urban agenda",                                  # EU — urban agenda
        "site:mckinsey.com cities futures",                                # McKinsey — cities futures
        "site:arup.com built environment futures",                         # Arup — built environment futures
        "site:oxfordmartin.ox.ac.uk urbanisation",                         # Oxford Martin — urbanisation
        "site:iclei.org local sustainability",                             # ICLEI — local sustainability
        "site:uli.org real estate cities",                                 # Urban Land Institute — real estate
        "site:brookings.edu metropolitan futures",                         # Brookings — metropolitan futures
    ],
    "Energy": [
        "site:iea.org energy outlook",                                     # IEA — global energy outlook
        "site:irena.org renewable energy",                                 # IRENA — renewable energy
        "site:weforum.org energy transition",                              # WEF — energy transition
        "site:rmi.org clean energy transition",                            # RMI — clean energy transition
        "site:oxfordenergy.org energy markets geopolitics",                # OIES — energy markets, geopolitics
        "site:isi.fraunhofer.de energy technology",                        # Fraunhofer ISI — energy technology
        "site:tyndall.ac.uk energy decarbonisation carbon budgets",        # Tyndall Centre — decarbonisation
        "site:bloombergnef.com energy market data",                        # BloombergNEF — energy market data
        "site:chathamhouse.org energy security",                           # Chatham House — energy security
        "site:wri.org energy transition",                                  # WRI — energy transition
        "site:ember-climate.org electricity data",                         # Ember — electricity data analytics
        "site:energypolicy.columbia.edu energy geopolitics",               # Columbia SIPA — energy geopolitics
    ],
    "Freight & Logistics": [
        "site:weforum.org supply chain futures",                           # WEF — supply chain futures
        "site:itf-oecd.org transport logistics policy",                    # ITF — transport and logistics policy
        "site:iea.org freight decarbonisation",                            # IEA — freight decarbonisation
        "site:imo.org maritime shipping regulation",                       # IMO — maritime shipping
        "site:unctad.org shipping trade data",                             # UNCTAD — shipping and trade data
        "site:worldbank.org trade logistics",                              # World Bank — trade logistics
        "site:mckinsey.com supply chain transformation",                   # McKinsey — supply chain transformation
        "site:ctl.mit.edu supply chain research",                          # MIT CTL — supply chain research
        "site:isi.fraunhofer.de logistics research",                       # Fraunhofer ISI — logistics research
        "site:ilo.org supply chain labour",                                # ILO — supply chain labour
        "site:deloitte.com supply chain strategy",                         # Deloitte — supply chain strategy
        "site:gartner.com supply chain technology",                        # Gartner — supply chain technology
    ],
    "Private & Public Transport": [
        "site:iea.org EV outlook transport energy",                        # IEA — EV outlook, transport energy
        "site:theicct.org clean transportation standards",                 # ICCT — clean transportation
        "site:itf-oecd.org transport policy",                              # ITF — transport policy
        "site:transportenvironment.org transport decarbonisation",         # T&E — transport decarbonisation
        "site:weforum.org mobility futures",                               # WEF — mobility futures
        "site:mckinsey.com mobility transformation",                       # McKinsey — mobility transformation
        "site:oecd.org transport policy",                                  # OECD — transport policy
        "site:uitp.org public transit",                                    # UITP — public transit
        "site:rmi.org clean mobility",                                     # RMI — clean mobility
        "site:deloitte.com mobility trends",                               # Deloitte — mobility trends
        "site:rand.org transport scenarios",                               # RAND — transport scenarios
        "site:iftf.org mobility futures",                                  # IFTF — mobility futures
    ],

    # --- Environment & Resources ---
    "Climate Change": [
        "site:ipcc.ch climate science",                                    # IPCC — climate science authority
        "site:unep.org emissions gap adaptation",                          # UNEP — Emissions Gap, Adaptation Gap
        "site:wmo.int climate state weather",                              # WMO — weather and climate data
        "site:pik-potsdam.de climate tipping points earth system",         # PIK — tipping points, earth system
        "site:stockholmresilience.org planetary boundaries",               # SRC — planetary boundaries
        "site:tyndall.ac.uk carbon budgets decarbonisation",               # Tyndall Centre — carbon budgets
        "site:oxfordmartin.ox.ac.uk climate policy",                       # Oxford Martin — climate policy
        "site:weforum.org climate action global risks",                    # WEF — Global Risks, climate action
        "site:iea.org energy climate nexus",                               # IEA — energy-climate nexus
        "site:futureearth.org climate systems science",                    # Future Earth — climate systems science
        "site:climate.nasa.gov climate data monitoring",                   # NASA — climate data and monitoring
        "site:unfccc.int climate policy framework",                        # UNFCCC — climate policy framework
        "site:wri.org climate solutions",                                  # WRI — climate solutions
        "site:sitra.fi climate megatrends",                                # Sitra — climate megatrends
    ],
    "Sustainability & Recycling": [
        "site:ellenmacarthurfoundation.org circular economy",              # Ellen MacArthur — circular economy
        "site:ec.europa.eu circular economy action plan",                  # EU — Circular Economy Action Plan
        "site:unep.org global sustainability frameworks",                  # UNEP — sustainability frameworks
        "site:weforum.org ESG sustainability transformation",              # WEF — ESG, sustainability
        "site:oecd.org sustainability policy",                             # OECD — sustainability policy
        "site:wbcsd.org corporate sustainability",                         # WBCSD — corporate sustainability
        "site:globalreporting.org ESG reporting standards",                # GRI — ESG reporting standards
        "site:stockholmresilience.org sustainability science",             # SRC — sustainability science
        "site:clubofrome.org systemic sustainability",                     # Club of Rome — systemic sustainability
        "site:cdp.net corporate environmental data",                       # CDP — corporate environmental data
        "site:wri.org resource efficiency",                                # WRI — resource efficiency
        "site:sciencebasedtargets.org corporate net-zero",                 # SBTi — corporate net-zero
        "site:sitra.fi circular economy",                                  # Sitra — circular economy
    ],
    "Nature": [
        "site:ipbes.net biodiversity science assessment",                  # IPBES — biodiversity science authority
        "site:iucn.org conservation nature policy",                        # IUCN — conservation status, nature policy
        "site:cbd.int biodiversity framework",                             # UN CBD — Biodiversity Framework
        "site:stockholmresilience.org planetary boundaries biodiversity",  # SRC — planetary boundaries
        "site:weforum.org nature biodiversity",                            # WEF — nature and biodiversity
        "site:futureearth.org biodiversity research",                      # Future Earth — biodiversity research
        "site:unep-wcmc.org nature monitoring",                            # UNEP-WCMC — nature monitoring
        "site:wri.org forests biodiversity",                               # WRI — forests and biodiversity
        "site:fao.org land ecosystems",                                    # FAO — land ecosystems
        "site:oxfordmartin.ox.ac.uk biodiversity",                         # Oxford Martin — biodiversity
        "site:tnfd.global nature-related financial disclosure",            # TNFD — nature financial disclosure
        "site:capitalscoalition.org natural capital valuation",            # Capitals Coalition — natural capital
    ],
    "Food": [
        "site:fao.org food security agriculture",                          # FAO — food security, agriculture
        "site:wfp.org food security hunger",                               # WFP — food security, hunger
        "site:cgiar.org agricultural research",                            # CGIAR — agricultural research
        "site:ifpri.org food policy",                                      # IFPRI — food policy
        "site:weforum.org food systems",                                   # WEF — food systems
        "site:oecd.org agriculture food",                                  # OECD — agriculture and food
        "site:wri.org food systems land use",                              # WRI — food systems, land use
        "site:eatforum.org sustainable diets",                             # EAT Forum — sustainable diets
        "site:gfi.org alternative proteins",                               # Good Food Institute — alternative proteins
        "site:oxfordmartin.ox.ac.uk food futures",                         # Oxford Martin — food futures
        "site:ipes-food.org food system governance",                       # IPES-Food — food system governance
        "site:nature.com food systems research",                           # Nature — food systems research
    ],
    "Water & Oceans": [
        "site:unwater.org water governance",                               # UN Water — water governance
        "site:oecd.org water policy",                                      # OECD — water policy
        "site:weforum.org water ocean futures",                            # WEF — water and ocean futures
        "site:unep.org ocean environment",                                 # UNEP — ocean environment
        "site:wmo.int hydrological data",                                  # WMO — hydrological data
        "site:iiasa.ac.at water scenarios",                                # IIASA — water scenarios
        "site:worldwatercouncil.org water policy",                         # World Water Council — water policy
        "site:en.unesco.org water resources",                              # UNESCO-IHP — water resources
        "site:oceana.org ocean conservation",                              # Oceana — ocean conservation
        "site:wwf.org freshwater ecosystems",                              # WWF — freshwater ecosystems
        "site:nature.com ocean water research",                            # Nature — ocean and water research
        "site:strategicforesight.com water diplomacy",                     # SFG — water diplomacy
        "site:millennium-project.org water futures",                       # Millennium Project — water futures
    ],

    # --- Society & Governance ---
    "Public Governance": [
        "site:oecd.org governance innovation open government",             # OECD — governance, open government
        "site:v-dem.net democracy data",                                   # V-Dem Institute — democracy data
        "site:freedomhouse.org democracy indices",                         # Freedom House — democracy indices
        "site:thegovlab.org digital governance",                           # GovLab NYU — digital governance
        "site:opengovpartnership.org transparency",                        # OGP — transparency
        "site:weforum.org governance futures",                             # WEF — governance futures
        "site:transparency.org anti-corruption",                           # Transparency International — anti-corruption
        "site:ec.europa.eu regulatory innovation",                         # EU — regulatory innovation
        "site:chathamhouse.org governance",                                # Chatham House — governance
        "site:rand.org governance policy",                                 # RAND — governance policy
        "site:hks.harvard.edu public policy",                              # Harvard Kennedy School — public policy
        "site:soif.org.uk governance foresight",                           # SOIF — governance foresight
        "site:sitra.fi public innovation",                                 # Sitra — public innovation
    ],
    "Geopolitics": [
        "site:chathamhouse.org international relations",                   # Chatham House — international relations
        "site:carnegieendowment.org international peace",                  # Carnegie — international peace
        "site:cfr.org global governance",                                  # CFR — global governance
        "site:iiss.org strategic military affairs",                        # IISS — strategic affairs
        "site:rand.org geopolitical analysis",                             # RAND — geopolitical analysis
        "site:ecfr.eu european geopolitics",                               # ECFR — European geopolitics
        "site:dni.gov global trends",                                      # NIC — Global Trends assessment
        "site:atlanticcouncil.org transatlantic affairs",                   # Atlantic Council — transatlantic
        "site:sipri.org peace security conflict",                          # SIPRI — peace, security, conflict
        "site:csis.org strategic studies",                                 # CSIS — strategic studies
        "site:strategicforesight.com conflict prevention",                 # SFG — conflict prevention
        "site:millennium-project.org geopolitical foresight",              # Millennium Project — geopolitical foresight
    ],
    "Security & Safety": [
        "site:rand.org security analysis cybersecurity",                   # RAND — security analysis
        "site:iiss.org strategic military security",                       # IISS — strategic security
        "site:sipri.org security conflict data",                           # SIPRI — security and conflict data
        "site:chathamhouse.org security policy",                           # Chatham House — security policy
        "site:dni.gov threat assessment",                                  # NIC — threat assessment
        "site:enisa.europa.eu cybersecurity",                              # ENISA — European cybersecurity
        "site:weforum.org global risks cyber",                             # WEF — Global Risks, cyber
        "site:nato.int collective security defence",                       # NATO — collective security
        "site:hybridcoe.fi hybrid threats",                                # Hybrid CoE Helsinki — hybrid threats
        "site:belfercenter.org international security",                    # Belfer Center Harvard — security
        "site:atlanticcouncil.org cybersecurity defence",                  # Atlantic Council — cybersecurity
        "site:cser.ac.uk existential security risks",                      # CSER Cambridge — existential risks
    ],
    "Values & Ethics": [
        "site:oxfordmartin.ox.ac.uk futures ethics future generations",    # Oxford Martin — futures ethics
        "site:oecd.org AI ethics principles",                              # OECD — AI ethics principles
        "site:nuffieldbioethics.org bioethics",                            # Nuffield Council — bioethics
        "site:adalovelaceinstitute.org data AI ethics",                    # Ada Lovelace Institute — AI ethics
        "site:weforum.org global ethics trust",                            # WEF — global ethics, trust
        "site:partnershiponai.org responsible AI",                         # Partnership on AI — responsible AI
        "site:futures.hawaii.edu values futures",                          # HRCFS — values futures
        "site:pewresearch.org social values trust",                        # Pew — social values, trust
        "site:ethicsinaction.ieee.org technology ethics standards",        # IEEE — technology ethics
        "site:millennium-project.org ethical scenarios",                   # Millennium Project — ethical scenarios
        "site:cifs.dk cultural values trends",                             # CIFS — cultural values
        "site:futuregenerations.wales intergenerational ethics",           # Future Generations Commissioner
    ],
    "Society": [
        "site:oecd.org inequality social well-being",                      # OECD — inequality, well-being
        "site:undp.org human development report",                          # UNDP — Human Development Report
        "site:weforum.org social cohesion inclusion",                      # WEF — social cohesion
        "site:oxfordmartin.ox.ac.uk inequality social change",             # Oxford Martin — inequality
        "site:pewresearch.org social trends",                              # Pew — social trends
        "site:brookings.edu social policy",                                # Brookings — social policy
        "site:lse.ac.uk social science research",                          # LSE — social science
        "site:chathamhouse.org social political change",                   # Chatham House — social change
        "site:soif.org.uk social futures",                                 # SOIF — social futures
        "site:thersa.org social innovation",                               # RSA — social innovation
        "site:jrf.org.uk poverty inequality",                              # Joseph Rowntree Foundation — poverty
        "site:futures.hawaii.edu society futures",                         # HRCFS — society futures
    ],

    # --- People & Well-being ---
    "Health & Wellbeing": [
        "site:who.int health future",                                      # WHO — global health authority
        "site:thelancet.com health systems medical science",               # Lancet — medical science
        "site:wellcome.org health research",                               # Wellcome Trust — health research
        "site:healthdata.org global disease burden",                       # IHME — global disease burden data
        "site:gatesfoundation.org global health equity",                   # Gates Foundation — global health
        "site:weforum.org health transformation",                          # WEF — health transformation
        "site:oxfordmartin.ox.ac.uk health futures",                       # Oxford Martin — health futures
        "site:mckinsey.com healthcare economics",                          # McKinsey — healthcare economics
        "site:rand.org health policy",                                     # RAND — health policy
        "site:ecdc.europa.eu public health",                               # ECDC — European public health
        "site:nih.gov biomedical innovation",                              # NIH — biomedical innovation
        "site:nuffieldtrust.org.uk health systems",                        # Nuffield Trust — health systems
        "site:nature.com biomedical research",                             # Nature — biomedical research
    ],
    "Education & Knowledge": [
        "site:oecd.org education systems PISA",                            # OECD — education systems, PISA
        "site:en.unesco.org education global",                             # UNESCO — global education
        "site:worldbank.org education development",                        # World Bank — education development
        "site:weforum.org education skills futures",                       # WEF — education and skills
        "site:ec.europa.eu education skills agenda",                       # EU — education skills agenda
        "site:iftf.org learning futures",                                  # IFTF — learning futures
        "site:knowledgeworks.org education foresight",                     # KnowledgeWorks — education foresight
        "site:educause.edu EdTech higher education",                       # EDUCAUSE — EdTech
        "site:ed.stanford.edu learning sciences",                          # Stanford GSE — learning sciences
        "site:mckinsey.com education skills",                              # McKinsey — education and skills
        "site:wise-qatar.org education innovation",                        # WISE — education innovation
        "site:openlearning.mit.edu digital education",                     # MIT OpenLearning — digital education
    ],
    "Work & Income": [
        "site:ilo.org labour policy standards",                            # ILO — labour policy and standards
        "site:oecd.org future of work",                                    # OECD — future of work
        "site:weforum.org jobs skills",                                    # WEF — jobs and skills
        "site:mckinsey.com automation impact future of work",              # McKinsey — automation impact
        "site:workofthefuture.mit.edu technology work",                    # MIT Work of the Future — technology and work
        "site:brookings.edu employment futures",                           # Brookings — employment futures
        "site:iftf.org work futures",                                      # IFTF — work futures
        "site:oii.ox.ac.uk digital labour",                               # Oxford Internet Institute — digital labour
        "site:epi.org labour economics",                                   # Economic Policy Institute — labour economics
        "site:pewresearch.org work employment",                            # Pew — work and employment
        "site:thersa.org future of work",                                  # RSA — future of work
        "site:fair.work platform work rights",                             # Fairwork Foundation — platform work
    ],
    "Management & HR": [
        "site:mckinsey.com leadership management",                         # McKinsey — leadership and management
        "site:hbr.org management innovation",                              # HBR — management innovation
        "site:deloitte.com human capital workforce trends",                # Deloitte — workforce trends
        "site:weforum.org talent workforce",                               # WEF — talent and workforce
        "site:gartner.com HR technology trends",                           # Gartner — HR technology
        "site:cipd.org people management",                                 # CIPD — people management
        "site:sloanreview.mit.edu management research",                    # MIT Sloan — management research
        "site:gallup.com workforce engagement",                            # Gallup — workforce engagement
        "site:insead.edu leadership research",                             # INSEAD — leadership research
        "site:ccl.org leadership development",                             # CCL — leadership development
        "site:shrm.org HR practices",                                      # SHRM — HR practices
        "site:sbs.ox.ac.uk management research",                          # Oxford Said — management research
    ],
    "Leisure & Lifestyles": [
        "site:mckinsey.com consumer trends",                               # McKinsey — consumer trends
        "site:oecd.org leisure tourism economy",                           # OECD — leisure, tourism economy
        "site:unwto.org tourism futures",                                  # UNWTO — tourism futures
        "site:weforum.org consumer lifestyle",                             # WEF — consumer lifestyle
        "site:pewresearch.org consumer lifestyle trends",                  # Pew — consumer and lifestyle trends
        "site:cifs.dk lifestyle megatrends",                               # CIFS — lifestyle megatrends
        "site:deloitte.com consumer industry",                             # Deloitte — consumer industry
        "site:iftf.org consumer futures",                                  # IFTF — consumer futures
        "site:euromonitor.com global consumer markets",                    # Euromonitor — global consumer markets
        "site:wundermanthompson.com consumer culture trends",              # JWT/WT — consumer culture trends
        "site:mintel.com consumer insights",                               # Mintel — consumer insights
        "site:worldleisure.org leisure futures",                           # World Leisure Organization — leisure
    ],

    # --- Science & Demographics ---
    "Sciences & Research": [
        "site:nature.com scientific research",                             # Nature — scientific research authority
        "site:science.org multidisciplinary research",                     # Science/AAAS — multidisciplinary research
        "site:oxfordmartin.ox.ac.uk emerging science",                     # Oxford Martin — emerging science
        "site:royalsociety.org scientific horizon scanning",               # Royal Society — horizon scanning
        "site:aaas.org science advancement policy",                        # AAAS — science advancement
        "site:erc.europa.eu frontier research",                            # ERC — frontier research
        "site:oecd.org R&D indicators science policy",                     # OECD — R&D indicators
        "site:isi.fraunhofer.de applied research innovation",             # Fraunhofer ISI — applied research
        "site:iiasa.ac.at systems science",                                # IIASA — systems science
        "site:cser.ac.uk technology risk",                                 # CSER Cambridge — technology risk
        "site:pik-potsdam.de earth system science",                        # PIK — earth system science
        "site:futureearth.org sustainability science",                     # Future Earth — sustainability science
        "site:en.unesco.org science policy open science",                   # UNESCO — science policy, open science
    ],
    "Population & Demographics": [
        "site:population.un.org world population prospects",               # UN Population Division — population data
        "site:oecd.org ageing societies",                                  # OECD — ageing societies
        "site:iom.int migration data policy",                             # IOM — migration data and policy
        "site:unhcr.org displacement refugees",                            # UNHCR — displacement and refugees
        "site:pewresearch.org demographic trends",                         # Pew — demographic trends
        "site:iiasa.ac.at population scenarios",                           # IIASA — population scenarios
        "site:oxfordmartin.ox.ac.uk demographics",                         # Oxford Martin — demographics
        "site:prb.org population analysis",                                # Population Reference Bureau — analysis
        "site:dni.gov demographic global trends",                          # NIC — demographic global trends
        "site:brookings.edu demographic change",                           # Brookings — demographic change
        "site:weforum.org demographic futures",                            # WEF — demographic futures
        "site:oeaw.ac.at demographic research",                            # Vienna Institute of Demography
        "site:cifs.dk demographic megatrends",                             # CIFS — demographic megatrends
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN → FORESIGHT THEME MAPPING
# Maps the scanner's 14 KNOWN_DOMAINS to the Strategic Foresight 30-theme taxonomy
# and identifies which KB dimensions are most impacted by external forces.
# ═══════════════════════════════════════════════════════════════════════════════

DOMAIN_TO_FORESIGHT = {
    "Technology & Software": {
        "fp_themes": ["Artificial Intelligence", "Data & Digitalisation", "Business"],
        "core_dimensions": ["AI Readiness", "Operating Model", "Service Portfolio"],
        "priority_dimensions": ["AI Readiness", "Operating Model", "Service Portfolio", "Strategy", "Values"],
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
        "core_dimensions": ["Operating Model", "Strategy", "AI Readiness"],
        "priority_dimensions": ["Operating Model", "Strategy", "AI Readiness", "Service Portfolio", "Values"],
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
        "core_dimensions": ["AI Readiness", "Service Portfolio", "Values"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Values", "Operating Model", "Strategy"],
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
        "core_dimensions": ["Service Portfolio", "AI Readiness", "Strategy"],
        "priority_dimensions": ["Service Portfolio", "AI Readiness", "Strategy", "Operating Model", "Values"],
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
        "core_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio", "Strategy", "Values"],
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
        "core_dimensions": ["Strategy", "Service Portfolio", "Operating Model"],
        "priority_dimensions": ["Strategy", "Service Portfolio", "Operating Model", "AI Readiness", "Values"],
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
        "core_dimensions": ["AI Readiness", "Strategy", "Operating Model"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Operating Model", "Service Portfolio", "Values"],
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
        "core_dimensions": ["AI Readiness", "Strategy", "Service Portfolio"],
        "priority_dimensions": ["AI Readiness", "Strategy", "Service Portfolio", "Operating Model", "Values"],
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
        "core_dimensions": ["Service Portfolio", "Strategy", "Operating Model"],
        "priority_dimensions": ["Service Portfolio", "Strategy", "Operating Model", "AI Readiness", "Values"],
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
        "core_dimensions": ["AI Readiness", "Service Portfolio", "Operating Model"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Operating Model", "Strategy", "Values"],
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
        "core_dimensions": ["Operating Model", "Service Portfolio", "Strategy"],
        "priority_dimensions": ["Operating Model", "Service Portfolio", "Strategy", "AI Readiness", "Values"],
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
        "core_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio"],
        "priority_dimensions": ["Operating Model", "AI Readiness", "Service Portfolio", "Strategy", "Values"],
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
        "core_dimensions": ["AI Readiness", "Service Portfolio", "Strategy"],
        "priority_dimensions": ["AI Readiness", "Service Portfolio", "Strategy", "Operating Model", "Values"],
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
        "core_dimensions": ["Strategy", "AI Readiness", "Operating Model"],
        "priority_dimensions": ["Strategy", "AI Readiness", "Operating Model", "Service Portfolio", "Values"],
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


_MIN_KB_SIGNALS_FOR_SYNTHESIS = 3  # Need at least 3 KB signals for synthesis

# ═══════════════════════════════════════════════════════════════════════════════
# THEME FALLBACK MAP — Near-fit substitution for missing KB themes
#
# When a requested FP theme has no data in the Foresight KB, substitute signals
# from a semantically related theme instead of triggering a live Google Search.
# This eliminates hallucination risk: all foresight data flows through the
# harvester's controlled pipeline (URL verification + credibility checks).
#
# Organised by KB dimension clusters — themes in the same cluster share enough
# strategic overlap that cross-referencing is safe and informative.
# ═══════════════════════════════════════════════════════════════════════════════

THEME_FALLBACK_MAP = {
    # --- Operating Model cluster ---
    "Management & HR":              ["Business", "Data & Digitalisation", "Work & Income"],
    "Data & Digitalisation":        ["Artificial Intelligence", "Business", "Communication & Media"],
    "Business":                     ["Data & Digitalisation", "Global Economy", "Services"],

    # --- Values cluster ---
    "Values & Ethics":              ["Society", "Public Governance", "Work & Income"],
    "Society":                      ["Values & Ethics", "Work & Income", "Public Governance"],
    "Work & Income":                ["Management & HR", "Society", "Education & Knowledge"],

    # --- Strategy cluster ---
    "Global Economy":               ["Geopolitics", "Finance & Ownership", "Business"],
    "Geopolitics":                   ["Global Economy", "Security & Safety", "Public Governance"],
    "Climate Change":                ["Sustainability & Recycling", "Energy", "Nature"],

    # --- Service Portfolio cluster ---
    "Digital & Sharing Economies":   ["Business", "Services", "Data & Digitalisation"],
    "Services":                      ["Business", "Digital & Sharing Economies", "Data & Digitalisation"],

    # --- AI Readiness cluster ---
    "Artificial Intelligence":       ["Data & Digitalisation", "Sciences & Research", "Industry & Manufacturing"],

    # --- Industry & Infrastructure ---
    "Industry & Manufacturing":      ["Artificial Intelligence", "Energy", "Business"],
    "Construction & Urbanisation":   ["Energy", "Sustainability & Recycling", "Industry & Manufacturing"],
    "Energy":                        ["Climate Change", "Sustainability & Recycling", "Industry & Manufacturing"],
    "Freight & Logistics":           ["Private & Public Transport", "Industry & Manufacturing", "Global Economy"],
    "Private & Public Transport":    ["Freight & Logistics", "Energy", "Construction & Urbanisation"],

    # --- Environment & Resources ---
    "Sustainability & Recycling":    ["Climate Change", "Nature", "Energy"],
    "Nature":                        ["Climate Change", "Sustainability & Recycling", "Water & Oceans"],
    "Food":                          ["Nature", "Sustainability & Recycling", "Health & Wellbeing"],
    "Water & Oceans":                ["Nature", "Climate Change", "Sustainability & Recycling"],

    # --- Society & Governance ---
    "Public Governance":             ["Geopolitics", "Values & Ethics", "Society"],
    "Security & Safety":             ["Geopolitics", "Artificial Intelligence", "Public Governance"],

    # --- People & Well-being ---
    "Health & Wellbeing":            ["Sciences & Research", "Artificial Intelligence", "Society"],
    "Education & Knowledge":         ["Work & Income", "Artificial Intelligence", "Management & HR"],
    "Leisure & Lifestyles":          ["Digital & Sharing Economies", "Society", "Services"],

    # --- Science & Demographics ---
    "Sciences & Research":           ["Artificial Intelligence", "Health & Wellbeing", "Energy"],
    "Population & Demographics":     ["Global Economy", "Society", "Work & Income"],

    # --- Media ---
    "Communication & Media":         ["Data & Digitalisation", "Artificial Intelligence", "Values & Ethics"],
    "Finance & Ownership":           ["Global Economy", "Business", "Security & Safety"],
}


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
        # Lifecycle stage — use stored value or infer from signal_type for backward compat
        lc_stage = sig.get("lifecycle_stage", "")
        if not lc_stage:
            _type_to_lifecycle = {
                "Preposterous/Black Swan": "weak_signal",
                "Probable/Emerging": "strong_signal",
                "Plausible/Maturing": "established_trend",
                "Weakening/Declining": "weakening_trend",
            }
            lc_stage = _type_to_lifecycle.get(sig.get("signal_type", ""), "")
        if lc_stage:
            momentum = sig.get("trend_momentum", "stable")
            evidence_lines.append(f"  Lifecycle stage: {lc_stage} | Momentum: {momentum}")
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
- "fp_theme": Which Strategic Foresight theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}. Frame as what the company should evaluate, using only future-valid timing; avoid deterministic obligation language.
- "source_url": Use the primary source URL from the signal above (already verified)
- "supporting_sources": Use the supporting sources from the signal above
- "source_type": Use the source_type from the signal
- "confidence": Use the confidence from the signal
- "lifecycle_stage": Use the lifecycle_stage from the KB signal if available, otherwise assess based on evidence. One of: "weak_signal" | "strong_signal" | "hype_cycle" | "valley_of_death" | "go_to_market" | "market_breakout" | "established_trend" | "market_saturation" | "weakening_trend" | "fading_signal"
- "trend_momentum": Use the trend_momentum from the KB signal if available, otherwise assess: "accelerating" | "stable" | "decelerating"
- "action_timing": Derive from lifecycle_stage and company archetype: "monitor" (weak_signal/strong_signal — track but don't invest yet) | "prepare" (hype_cycle/valley_of_death — build capabilities, run pilots) | "act_now" (go_to_market/market_breakout — invest and execute) | "last_chance" (established_trend — adopt or fall behind competitors) | "optimize" (market_saturation/weakening_trend/fading_signal — extract value, prepare for next wave)
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
      "lifecycle_stage": "weak_signal|strong_signal|hype_cycle|valley_of_death|go_to_market|market_breakout|established_trend|market_saturation|weakening_trend|fading_signal",
      "trend_momentum": "accelerating|stable|decelerating",
      "action_timing": "monitor|prepare|act_now|last_chance|optimize",
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

RELEVANT FORESIGHT THEMES (from Strategic Foresight taxonomy): {fp_themes}
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
Tier 1 (intergovernmental/UN/flagship journals): ipcc.ch, who.int, iea.org, irena.org, imf.org, worldbank.org, oecd.org, nature.com, science.org, thelancet.com, bis.org, fao.org, ilo.org, unep.org, wto.org, unctad.org, unfccc.int, ipbes.net, iom.int, un.org, ec.europa.eu, europa.eu, wmo.int
Tier 2 (think tanks/academic): chathamhouse.org, rand.org, brookings.edu, carnegieendowment.org, csis.org, iiss.org, sipri.org, oxfordmartin.ox.ac.uk, turing.ac.uk, hai.stanford.edu, lse.ac.uk, oii.ox.ac.uk, bruegel.org, cser.ac.uk, belfercenter.org, hks.harvard.edu, piie.com
Tier 3 (specialist research/foresight): iftf.org, cifs.dk, soif.org.uk, futures.hawaii.edu, weforum.org, ellenmacarthurfoundation.org, wri.org, rmi.org, iucn.org, fair.work, sitra.fi, nesta.org.uk, c40.org, millennium-project.org, clubofrome.org, wfsf.org, hybridcoe.fi, enisa.europa.eu
Tier 4 (commercial/supplementary): mckinsey.com, deloitte.com, gartner.com, forrester.com, bcg.com, pwc.com, hbr.org, reuters.com, ft.com, bloomberg.com, economist.com, wsj.com, bbc.com, idc.com, accenture.com

DO NOT USE sources from: blogs, content farms, regional tech news sites, outsourcing directories, marketing sites, press release wires, social media, or any site not recognized as an established research/analyst/news organization.

TASK 1 — DIMENSION-LEVEL FORESIGHT:
For each of the {n_dims} priority KB dimensions above, use the MANDATORY SOURCE TARGETING searches above to identify the single most impactful EXTERNAL force affecting companies in the {domain} sector. These must be macro-level industry/regulatory/technological forces — NOT company-specific findings.

Per force, provide:
- "trend": Concrete name of the external force (specific, not vague)
- "type": One of: "Plausible/Maturing" (gathering momentum, validated trend) | "Weakening/Declining" (losing relevance or reversing) | "Preposterous/Black Swan" (low probability, massive impact — see HORIZON SCANNING instruction below) | "Probable/Emerging" (early signal, gaining traction but insufficient data)
- "timeframe": "6-12mo" | "12-24mo" | "24-36mo"
- "kb_dimension": Which priority dimension this impacts most
- "fp_theme": Which Strategic Foresight theme this maps to
- "impact_on_company": 1-2 sentences on how this specifically affects {company_name} given their archetype ({archetype}) and current state
- "proposal_angle": 1 sentence on how to use this in a consulting proposal to {company_name}. Frame as what must happen by when to either capture or defend against this force (e.g., "Could evaluate a pilot for X by Q2 {datetime.now().year + 1} or risk Y").
- "source_url": PRIMARY URL from your web search results (ORIGINAL source only — must be a real, reachable URL)
- "supporting_sources": Array of 2-3 ADDITIONAL URLs that corroborate this force from DIFFERENT publications/organizations. Each entry: {{"url": "https://...", "source_type": "...", "title": "Brief title of the source"}}. CRITICAL: These must be REAL URLs you found via web search, not fabricated. If you cannot find 2+ corroborating sources, set confidence to "low".
- "source_type": "academic" | "think_tank" | "industry_analyst" | "government" | "news"
- "confidence": "high" (3+ verified sources) | "medium" (2 verified sources) | "low" (0-1 verified sources)
- "lifecycle_stage": Assess where this force sits on the trend adoption S-curve. One of: "weak_signal" (barely detectable, fringe/academic sources only) | "strong_signal" (multiple independent confirmations, not yet mainstream) | "hype_cycle" (inflated expectations, media buzz exceeds real-world adoption) | "valley_of_death" (post-hype disillusionment, initiatives stalling) | "go_to_market" (real products/regulations appearing) | "market_breakout" (rapid scaling, early mainstream adoption) | "established_trend" (widely adopted, industry standard) | "market_saturation" (peak adoption, diminishing returns) | "weakening_trend" (declining relevance, being replaced) | "fading_signal" (near-obsolete)
- "trend_momentum": "accelerating" (adoption rate increasing) | "stable" (steady pace) | "decelerating" (slowing down)
- "action_timing": Derive from lifecycle_stage and company archetype: "monitor" (weak_signal/strong_signal) | "prepare" (hype_cycle/valley_of_death) | "act_now" (go_to_market/market_breakout) | "last_chance" (established_trend) | "optimize" (market_saturation/weakening_trend/fading_signal)
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
      "lifecycle_stage": "weak_signal|strong_signal|hype_cycle|valley_of_death|go_to_market|market_breakout|established_trend|market_saturation|weakening_trend|fading_signal",
      "trend_momentum": "accelerating|stable|decelerating",
      "action_timing": "monitor|prepare|act_now|last_chance|optimize",
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

    client = get_gemini_client()

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
        ), 90),
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

            # Re-verify KB source URLs before trusting them (they may have gone stale)
            _kb_urls_to_check = set()
            for force in data["dimension_forces"]:
                _url = (force.get("source_url") or "").strip()
                if _url and _url.startswith("http"):
                    _kb_urls_to_check.add(_url)
            # Also collect supporting source URLs from KB signals
            for sig in kb_signals:
                for ss in sig.get("supporting_sources", []):
                    _ss_url = (ss.get("url") or "").strip()
                    if _ss_url and _ss_url.startswith("http"):
                        _kb_urls_to_check.add(_ss_url)

            _kb_url_status = {}
            if _kb_urls_to_check:
                print(f"   🔍 [KB Re-verify] Checking {len(_kb_urls_to_check)} KB URLs for {company_name}...")
                _rv_t0 = time.time()
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _rv_pool:
                    _rv_futures = {
                        _rv_pool.submit(_verify_foresight_url, u, 6): u
                        for u in _kb_urls_to_check
                    }
                    for fut in concurrent.futures.as_completed(_rv_futures):
                        _u = _rv_futures[fut]
                        try:
                            _kb_url_status[_u] = fut.result()
                        except Exception:
                            _kb_url_status[_u] = False
                _alive = sum(1 for v in _kb_url_status.values() if v)
                _dead = len(_kb_url_status) - _alive
                print(f"   🔍 [KB Re-verify] {_alive} alive, {_dead} dead ({int((time.time()-_rv_t0)*1000)}ms)")

            # Enrich forces with KB data and apply real verification status
            for force in data["dimension_forces"]:
                force["_from_kb"] = True
                force["validated"] = True

                source_url = (force.get("source_url") or "").strip()
                # Use real verification result instead of blindly trusting
                force["url_verified"] = _kb_url_status.get(source_url, False) if source_url else False

                # Match force to KB signal — try source_url first, then title
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
                                force["url_verified"] = _kb_url_status.get(sig["source_url"], False)
                            break

                if matched_sig:
                    kb_supporting = matched_sig.get("supporting_sources", [])
                    if kb_supporting and not force.get("supporting_sources"):
                        force["supporting_sources"] = kb_supporting
                    # Set verified_supporting_sources with real verification status
                    force["verified_supporting_sources"] = [
                        {**ss, "url_verified": _kb_url_status.get((ss.get("url") or "").strip(), False)}
                        for ss in (force.get("supporting_sources") or kb_supporting)
                    ]
                    force["source_credibility"] = matched_sig.get("source_credibility", "medium")
                    force["source_count"] = 1 + len(force["verified_supporting_sources"])
                    if matched_sig.get("description"):
                        force["_kb_description"] = matched_sig["description"]
                    # Carry over lifecycle fields from KB signal if force lacks them
                    for lc_field in ("lifecycle_stage", "trend_momentum", "action_timing"):
                        if matched_sig.get(lc_field) and not force.get(lc_field):
                            force[lc_field] = matched_sig[lc_field]
                else:
                    force["source_credibility"] = "medium"
                    force["source_count"] = 1
                    force["verified_supporting_sources"] = []

            # Drop forces whose primary URL is dead (keep forces with no URL — they may be valid synthesized insights)
            _original_count = len(data["dimension_forces"])
            data["dimension_forces"] = [
                f for f in data["dimension_forces"]
                if f.get("url_verified") is True or not (f.get("source_url") or "").strip()
            ]
            _dropped = _original_count - len(data["dimension_forces"])
            if _dropped:
                print(f"   🗑️ [KB Synthesis] Dropped {_dropped} forces with dead primary URLs")

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

    # Attempt 3: Claude Sonnet 4.6 backup (independent provider — survives Google quota)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        try:
            SONNET_MODEL = "claude-sonnet-4-6"
            print(f"   📚 [KB Synthesis] Gemini models exhausted — trying backup {SONNET_MODEL} for {company_name}...")
            t0 = time.time()
            claude_client = anthropic.Anthropic(api_key=anthropic_key)
            response = claude_client.messages.create(
                model=SONNET_MODEL,
                max_tokens=8000,
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
            elapsed = int((time.time() - t0) * 1000)

            if not text or len(text) < 100:
                print(f"   ⚠️ [KB Synthesis] {SONNET_MODEL} returned insufficient data ({len(text) if text else 0} chars, {elapsed}ms)")
            else:
                json_str = _extract_json_from_text(text)
                if not json_str:
                    print(f"   ⚠️ [KB Synthesis] Could not extract JSON from {SONNET_MODEL} response ({elapsed}ms)")
                else:
                    data = json.loads(json_str, strict=False)
                    if not data.get("dimension_forces") or not data.get("scenarios"):
                        print(f"   ⚠️ [KB Synthesis] {SONNET_MODEL} returned incomplete structure ({elapsed}ms)")
                    else:
                        # Re-verify KB source URLs
                        _kb_urls_to_check = set()
                        for force in data["dimension_forces"]:
                            _url = (force.get("source_url") or "").strip()
                            if _url and _url.startswith("http"):
                                _kb_urls_to_check.add(_url)
                        for sig in kb_signals:
                            for ss in sig.get("supporting_sources", []):
                                _ss_url = (ss.get("url") or "").strip()
                                if _ss_url and _ss_url.startswith("http"):
                                    _kb_urls_to_check.add(_ss_url)

                        _kb_url_status = {}
                        if _kb_urls_to_check:
                            print(f"   🔍 [KB Re-verify] Checking {len(_kb_urls_to_check)} KB URLs for {company_name}...")
                            _rv_t0 = time.time()
                            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as _rv_pool:
                                _rv_futures = {
                                    _rv_pool.submit(_verify_foresight_url, u, 6): u
                                    for u in _kb_urls_to_check
                                }
                                for fut in concurrent.futures.as_completed(_rv_futures):
                                    _u = _rv_futures[fut]
                                    try:
                                        _kb_url_status[_u] = fut.result()
                                    except Exception:
                                        _kb_url_status[_u] = False
                            _alive = sum(1 for v in _kb_url_status.values() if v)
                            _dead = len(_kb_url_status) - _alive
                            print(f"   🔍 [KB Re-verify] {_alive} alive, {_dead} dead ({int((time.time()-_rv_t0)*1000)}ms)")

                        # Enrich forces with KB data and apply real verification status
                        for force in data["dimension_forces"]:
                            force["_from_kb"] = True
                            _f_url = (force.get("source_url") or "").strip()
                            if _f_url in _kb_url_status:
                                force["url_verified"] = _kb_url_status[_f_url]

                        # Drop forces whose primary URL is confirmed dead
                        orig_count = len(data["dimension_forces"])
                        data["dimension_forces"] = [
                            f for f in data["dimension_forces"]
                            if f.get("url_verified", True) is not False
                        ]
                        _dropped = orig_count - len(data["dimension_forces"])
                        if _dropped:
                            print(f"   🗑️ [KB Synthesis] Dropped {_dropped} forces with dead primary URLs")

                        n_forces = len(data["dimension_forces"])
                        n_scenarios = len(data.get("scenarios", []))
                        print(f"   📚 [KB Synthesis] {SONNET_MODEL} succeeded: {n_forces} forces from {len(kb_signals)} KB signals "
                              f"+ {n_scenarios} scenarios ({elapsed}ms)")
                        return data
        except json.JSONDecodeError as je:
            elapsed = int((time.time() - t0) * 1000)
            print(f"   ⚠️ [KB Synthesis] {SONNET_MODEL} JSON parse failed ({elapsed}ms): {je}")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [KB Synthesis] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    return None


def _sanitize_foresight_data(foresight_data: dict, domain: str = "", company_name: str = "") -> dict:
    """Sanitize generated foresight data before report rendering/storage.

    New scans and refreshes must be clean-by-construction. This function is part
    of the normal generation path, not the legacy database Sanitizer.
    """
    if not isinstance(foresight_data, dict):
        return foresight_data
    cleaned = dict(foresight_data)

    # Classify and sanitize source-card-like lists.
    for field in ["dimension_forces", "future_insights", "foresight_links", "foresight_evidence", "source_cards"]:
        if isinstance(cleaned.get(field), list):
            cleaned[field] = [classify_generation_context(x) if isinstance(x, dict) else x for x in cleaned[field]]
            cleaned[field] = sanitize_foresight_collection(cleaned[field], domain=domain, company_name=company_name)

    # Future Insight and narrative foresight fields must be modal/conditional.
    for field in ["future_insight", "future_insights_text", "climate_exposure", "cross_impact_chain"]:
        if isinstance(cleaned.get(field), str):
            cleaned[field], _issues = sanitize_future_insight(cleaned[field])

    # Whole-record pass to catch nested source_says/possible_relevance and deadline issues.
    cleaned = sanitize_report_foresight_fields(cleaned, domain=domain, company_name=company_name)
    cleaned["foresight_sanitized"] = True
    cleaned["clean_by_construction"] = True
    return cleaned


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

    KB-only approach: All foresight data comes from the pre-validated Foresight KB.
    No live Google Search is performed during per-company analysis — this eliminates
    hallucination risk. New data enters the system only through the harvester's
    controlled pipeline (URL verification + credibility checks + deduplication).

    When a theme has no KB data, get_kb_signals_for_themes() automatically
    substitutes signals from the nearest related theme (THEME_FALLBACK_MAP).
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("API_KEY")
    if not gemini_key:
        print("   ⚠️ [Foresight] No GEMINI_API_KEY — skipping")
        return None

    domain_config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT["Other"])

    # --- FORESIGHT KB-ONLY PATH ---
    # All foresight data comes from the pre-validated KB (with near-fit fallback).
    # No live Google Search — searches happen only at harvest time.
    kb_signals = []
    try:
        from foresight_harvester import get_kb_signals_for_themes
        kb_signals = get_kb_signals_for_themes(domain_config["fp_themes"])
    except Exception as kb_err:
        print(f"   ⚠️ [Foresight KB] Could not load KB signals: {kb_err}")

    if len(kb_signals) < _MIN_KB_SIGNALS_FOR_SYNTHESIS:
        print(f"   ⚠️ [Foresight KB-Only] Only {len(kb_signals)} KB signals available "
              f"(need {_MIN_KB_SIGNALS_FOR_SYNTHESIS}) — foresight skipped. "
              f"Run the harvester to populate the Foresight KB.")
        return None

    # Log near-fit substitutions for transparency
    fallback_count = sum(1 for s in kb_signals if s.get("_kb_fallback_from"))
    if fallback_count:
        print(f"   📚 [Foresight KB-Only] {len(kb_signals)} KB signals "
              f"({fallback_count} from near-fit themes) — synthesis-only path")
    else:
        print(f"   📚 [Foresight KB-Only] {len(kb_signals)} KB signals available — synthesis-only path")

    # Quick-verify KB signal URLs are still alive
    verified_signals = _quick_verify_kb_signals(kb_signals)

    if len(verified_signals) < _MIN_KB_SIGNALS_FOR_SYNTHESIS:
        print(f"   ⚠️ [Foresight KB-Only] Only {len(verified_signals)} signals alive "
              f"(need {_MIN_KB_SIGNALS_FOR_SYNTHESIS}) — foresight skipped. "
              f"Run the harvester to refresh stale KB data.")
        return None

    kb_result = _generate_foresight_from_kb(
        company_name, domain, archetype, reality_score,
        financial_index, top_findings, domain_config,
        verified_signals, gemini_key,
    )
    if kb_result:
        return kb_result

    print(f"   ❌ [Foresight KB-Only] Synthesis failed for {company_name} — foresight skipped")
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
        lc_tag = ""
        if f.get("lifecycle_stage"):
            lc_tag = f" [{f['lifecycle_stage']}/{f.get('trend_momentum', '?')}]"
        forces_text += (
            f"- [{f.get('type', '?')}] {f.get('trend', '?')} ({f.get('timeframe', '?')}){lc_tag} "
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

INSTRUCTION: You may weave 1-2 forward-looking insights into the summary's final paragraph, but only as external context.
The pitch should reference an external force only when the evidence supports relevance; do not state it as current company deployment.
WHY/WHAT proposals should be future-aware and validation-first — frame the weakest KB dimension against
its potentially relevant external force.
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

    # Build attribution line — shows which Foresight themes from the DB were used
    _themes_used: set[str] = set()
    for f in forces:
        theme = f.get("fp_theme", "Unknown")
        if theme and theme != "Unknown":
            _themes_used.add(theme)
    _theme_list = sorted(_themes_used)
    _basis_line = (
        "This Future Insight is based on the following Foresight(s) Database: "
        + ", ".join(_theme_list) + "."
    ) if _theme_list else ""
    forces_text = ""
    for idx, f in enumerate(forces, 1):
        n_supporting = len(f.get("verified_supporting_sources", []) or f.get("supporting_sources", []))
        forces_text += (
            f"Force {idx}/{len(forces)}: {f.get('trend', '?')}\n"
            f"  Type: {f.get('type', '?')} | Timeframe: {f.get('timeframe', '?')} | "
            f"Theme: {f.get('fp_theme', '?')} | Confidence: {f.get('confidence', '?')}\n"
            f"  Source credibility: {f.get('source_credibility', 'unknown')} | "
            f"Corroborating sources: {n_supporting}\n"
            f"  Lifecycle: {f.get('lifecycle_stage', '?')} | Momentum: {f.get('trend_momentum', '?')} | "
            f"Action timing: {f.get('action_timing', '?')}\n"
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
- IMPORTANT: This analysis is based on {len(forces)} distinct external forces. Focus on the top 2-3 most impactful.
  Weight each force by its confidence level and source credibility (high-credibility sources carry more weight).
- First paragraph: The 2-3 most impactful external forces and their timeframe.
  Name the forces concretely — no vague "market changes are coming."
  Wrap each key force name in **double asterisks** when first mentioned (e.g., "**EU SFDR 2.0 implementation**").
- Second paragraph: What this specifically means for THIS company given
  their current weaknesses. Connect the dots between external forces and internal gaps.
  Explain the cross-impact chain — how one force triggers cascading effects across multiple areas.
- Third paragraph: What the company should evaluate or prepare for, if the force is relevant and in scope — concrete strategic imperatives
  tied to the identified forces. Be specific about timelines and capabilities needed.
{climate_instruction}
- Tone: Strategic, forward-looking, grounded in evidence. Like a foresight
  analyst briefing a board of directors. Not marketing fluff.
- Do NOT reference internal scores by name or number.
- Do NOT use any of these terms: {forbidden}. Use plain business language.
- Every claim must trace to a force identified in the foresight analysis.
- If confidence is low on a force, use hedging language ("emerging signals suggest...")
- LIFECYCLE TIMING: When each force has lifecycle_stage and action_timing data, explicitly reference WHERE
  each force sits on its adoption lifecycle and what this means for timing. Use phrases like "still in its
  hype cycle — prepare but don't over-invest", "at market breakout — act now", or "approaching
  saturation — optimize existing position". The timing dimension is what makes this actionable.
  Forces with action_timing "act_now" or "last_chance" should be emphasized as urgent.
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
                if _basis_line:
                    text += "\n\n" + _basis_line
                return text, SONNET_MODEL

            print(f"   ⚠️ [Future Insight] Sonnet too short ({wc} words) — falling back to Gemini")
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000) if 't0' in dir() else 0
            print(f"   ❌ [Future Insight] {SONNET_MODEL} failed ({elapsed}ms): {e}")

    # --- Attempt 2-3: Gemini fallbacks ---
    try:
        client = get_gemini_client()
    except ValueError:
        print("   ⚠️ [Future Insight] No GEMINI_API_KEY — skipping Gemini fallbacks")
        return "", ""

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
                if _basis_line:
                    text += "\n\n" + _basis_line
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


_FORESIGHT_SOFT_404_RE = re.compile(
    r'page\s*not\s*found|'
    r'404\s*(error|not\s*found)|'
    r'not\s*found\s*error|'
    r'could\s*n.?t\s*(find|locate)\s*(this|that|the)?\s*page|'
    r'no\s*longer\s*(available|exists)|'
    r'has\s*been\s*(removed|deleted|moved)|'
    r'this\s*page\s*(does\s*n.?t|doesn.?t)\s*exist|'
    r'requested\s*page\s*(was|is)\s*not\s*found',
    re.IGNORECASE
)


def _verify_foresight_url(url, timeout=8):
    """
    Verify a single foresight URL exists via HTTP GET request.
    Detects soft 404s (pages returning HTTP 200 but showing 'not found' content).
    Returns True for 403 on known paywalled domains (URL exists but is gated).
    Returns False for 404, 5xx, soft 404s, timeouts, or connection errors.
    """
    if not url or not url.startswith("http"):
        return False
    paywall = _is_paywall_domain(url)
    try:
        import requests as http_requests
        resp = http_requests.get(
            url, timeout=timeout, allow_redirects=True, stream=True,
            headers={"User-Agent": _BROWSER_UA},
        )

        if resp.status_code >= 400:
            resp.close()
            if paywall and resp.status_code == 403:
                return True
            return False

        # Read first 10KB for soft 404 detection
        body_chunk = ""
        try:
            raw = resp.raw.read(10240)
            body_chunk = raw.decode("utf-8", errors="ignore").lower()
        except Exception:
            pass
        resp.close()

        # Soft 404 check
        if body_chunk and _FORESIGHT_SOFT_404_RE.search(body_chunk):
            return False

        return True
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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0, 0

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
        for ss in _dict_items_only(force.get("supporting_sources", []), "supporting_sources"):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in url_map:
                url_map[ss_url] = None

    if not url_map:
        for force in forces:
            force["url_verified"] = False
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0, 0

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
        for ss in _dict_items_only(force.get("supporting_sources", []), "supporting_sources"):
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

    return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), verified_count, dead_count


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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0

    forces = foresight_data["dimension_forces"]
    failed_forces = [f for f in forces if not f.get("url_verified", False)]

    if not failed_forces:
        print("   [Source Retry] All sources already verified — no retry needed")
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0

    try:
        client = get_gemini_client()
    except ValueError:
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0

    print(f"   🔄 [Source Retry] {len(failed_forces)}/{len(forces)} forces have unverified sources — attempting re-search...")
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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0

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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), 0

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
        for ss in _dict_items_only(force.get("supporting_sources", []), "supporting_sources"):
            ss_url = (ss.get("url") or "").strip()
            if ss_url in new_urls:
                ss["url_verified"] = new_urls[ss_url]
                if new_urls[ss_url]:
                    verified_supporting.append(ss)
            else:
                ss["url_verified"] = False
        force["verified_supporting_sources"] = verified_supporting

    print(f"   🔄 [Source Retry] Re-search complete: {newly_verified}/{len(updated_forces)} forces now have verified sources")
    return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), newly_verified


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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), False

    forces = foresight_data["dimension_forces"]
    total_forces = len(forces)

    # Count verified primary sources
    verified_primary = sum(1 for f in forces if f.get("url_verified", False))

    # Count total verified URLs (primary + supporting)
    total_verified_urls = verified_primary
    for force in forces:
        for ss in _dict_items_only(force.get("supporting_sources", []), "supporting_sources"):
            if ss.get("url_verified", False):
                total_verified_urls += 1
        for ss in _dict_items_only(force.get("verified_supporting_sources", []), "verified_supporting_sources"):
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

    return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name), passes_gate


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
        return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name)

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

        # --- Lifecycle stage consistency checks ---
        lifecycle_stage = (force.get("lifecycle_stage") or "").strip().lower()
        trend_momentum = (force.get("trend_momentum") or "").strip().lower()
        if lifecycle_stage:
            _valid_stages = {
                "weak_signal", "strong_signal", "hype_cycle", "valley_of_death",
                "go_to_market", "market_breakout", "established_trend",
                "market_saturation", "weakening_trend", "fading_signal",
            }
            if lifecycle_stage not in _valid_stages:
                flags.append(f"Unknown lifecycle_stage: '{lifecycle_stage}'")
            else:
                # Cross-check lifecycle vs confidence
                if lifecycle_stage == "established_trend" and confidence == "low":
                    flags.append("lifecycle_confidence_mismatch: established_trend with low confidence")
                # Weak signals usually have longer timeframes
                timeframe = (force.get("timeframe") or "").strip()
                if lifecycle_stage == "weak_signal" and timeframe == "6-12mo":
                    flags.append("lifecycle_timeframe_mismatch: weak_signal unlikely to impact in 6-12mo")
                # Saturation shouldn't be accelerating
                if lifecycle_stage == "market_saturation" and trend_momentum == "accelerating":
                    flags.append("lifecycle_momentum_mismatch: market_saturation with accelerating momentum")
                # Fading signal shouldn't be accelerating
                if lifecycle_stage == "fading_signal" and trend_momentum == "accelerating":
                    flags.append("lifecycle_momentum_mismatch: fading_signal with accelerating momentum")

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

    return _sanitize_foresight_data(foresight_data, domain=domain, company_name=company_name)



def _dict_items_only(items, label: str = "items") -> list:
    """Filter generated/legacy lists to dict records only before .get() access."""
    if not isinstance(items, list):
        return []
    out = []
    dropped = 0
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        else:
            dropped += 1
    if dropped:
        print(f"   ⚠️ [Foresight Hygiene] Dropped {dropped} non-dict item(s) from {label}")
    return out

def extract_foresight_links(foresight_data, company_name="", domain=""):
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
    - fp_theme: Strategic Foresight theme
    - timeframe: Force timeframe
    - force_type: Plausible/Maturing, etc.
    - origin: "foresight" (to distinguish from forensic evidence_links)
    - is_priority_dimension: True if kb_dimension is a core (scoring) dimension for this domain

    Returns list of foresight_link dicts, sorted by credibility (high first).
    """
    if callable(foresight_data) or not isinstance(foresight_data, dict):
        return []
    foresight_data["dimension_forces"] = _dict_items_only(foresight_data.get("dimension_forces", []), "dimension_forces")
    if not foresight_data.get("dimension_forces"):
        return []

    # Resolve core dimensions for this domain (the original 3 that feed into FAVI/FPS/RG)
    domain_config = DOMAIN_TO_FORESIGHT.get(domain, DOMAIN_TO_FORESIGHT.get("Other", {}))
    core_dims = set(domain_config.get("core_dimensions", domain_config.get("priority_dimensions", [])))

    links = []
    seen_urls = set()
    for force in _dict_items_only(foresight_data.get("dimension_forces", []), "dimension_forces"):
        source_url = (force.get("source_url") or "").strip()
        if source_url and source_url.startswith("http") and source_url not in seen_urls:
            seen_urls.add(source_url)
            # source_says = company-neutral trend description (NOT impact_on_company).
            # possible_relevance = company-specific hypothesis (impact_on_company).
            # Invariant: source_says is generic; only possible_relevance carries
            # company-specific interpretation.
            _neutral_desc = (
                force.get("description")
                or force.get("trend_description")
                or force.get("trend", "Unknown Force")
            )
            _impact = (force.get("impact_on_company") or "")[:300]
            link = {
                "title": force.get("trend", "Unknown Force"),
                "url": source_url,
                "snippet": _neutral_desc[:300],
                "source_says": _neutral_desc[:300],
                "possible_relevance": _impact,
                "source_type": force.get("source_type", "unknown"),
                "verified": force.get("url_verified", False),
                "source_credibility": force.get("source_credibility", "unknown"),
                "validated": force.get("validated", False),
                "kb_dimension": force.get("kb_dimension", ""),
                "fp_theme": force.get("fp_theme", ""),
                "timeframe": force.get("timeframe", ""),
                "force_type": force.get("type", ""),
                "lifecycle_stage": force.get("lifecycle_stage", ""),
                "trend_momentum": force.get("trend_momentum", ""),
                "action_timing": force.get("action_timing", ""),
                "origin": "foresight",
                "is_primary": True,
                "not_evidence_of_deployment": True,
                "is_priority_dimension": force.get("kb_dimension", "") in core_dims,
            }
            links.append(annotate_foresight_link(link, domain=domain, company_name=company_name))

        # Also include verified supporting sources
        for ss in force.get("verified_supporting_sources", []):
            ss_url = (ss.get("url") or "").strip()
            if ss_url and ss_url.startswith("http") and ss_url not in seen_urls:
                seen_urls.add(ss_url)
                _ss_neutral = (
                    ss.get("description") or ss.get("title") or force.get("trend", "Unknown Force")
                )
                link = {
                    "title": ss.get("title", force.get("trend", "Unknown Force")),
                    "url": ss_url,
                    "snippet": _ss_neutral[:300],
                    "source_says": _ss_neutral[:300],
                    "possible_relevance": (force.get("impact_on_company") or "")[:300],
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
                    "not_evidence_of_deployment": True,
                    "is_priority_dimension": force.get("kb_dimension", "") in core_dims,
                }
                links.append(annotate_foresight_link(link, domain=domain, company_name=company_name))

    # Sort: validated first, then by credibility
    credibility_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    links.sort(key=lambda l: (
        0 if l["validated"] else 1,
        credibility_order.get(l["source_credibility"], 3),
    ))

    if links:
        validated_count = sum(1 for l in links if l["validated"])
        print(f"   📎 [Foresight Links] Extracted {len(links)} source links ({validated_count} validated)")
    return sanitize_foresight_collection, sanitize_report_foresight_fields(links, domain=domain)
