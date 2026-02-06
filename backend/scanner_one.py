import re

# Keyword Definitions (Mirrors constants.ts)
# INCLUDES FINNISH (FI) TRANSLATIONS
# UPGRADE v2.5: Doubled Vocabulary for High-Resolution "Intent" Analysis.
# Focused on distinguishing "Marketing Fluff" from "Structural Intent".

CATEGORIES = {
   "Operating Model": {
       "good": [
           "Service Areas", "Value Streams", "Stream-aligned", "Distributed Authority", "Sense and Respond",
           "Psychological Safety", "Cross-functional", "Flow Efficiency", "Platform Team", "Autonomy",
           "Outcome-driven", "Micro-enterprises", "Customer Intimacy", "Self-organization", "Experimentation",
           "Launch-and-Learn", "Fluid structures", "Network of Teams", "Enabling Leadership", "Cognitive Load",
           "Zero Distance", "Communities of Practice", "Transparency", "Human-centric", "End-to-End Responsibility",
           "Rendanheyi", "Zero distance to user", "Paid by user", "Catfish effect", "Ecosystem Micro-community", 
           "EMC", "User scenario", "Maker culture", "Inverted pyramid", "Servant leadership",
           "Conways Law", "Reverse Conway Maneuver", "Thinnest Viable Platform", "Interaction modes", 
           "Team API", "Software architecture as strategy", "Architectural isomorphism", "Unhindered flow",
           "Radical delegation", "Trust-based rules", "Relative targets", "Dynamic resource allocation", 
           "Frontline empowerment", "De-scaling", "Internal open source", "InnerSource", "Safe to fail"
       ],
       "good_fi": [
           "arvovirta", "itseohjautuvuu", "matala hierarkia", "kokeilukulttuuri", "asiakaslähtöisy",
           "ketterä toimintatapa", "tiimimalli", "vastuun jakami", "psykologinen turvallisuu", "poikkitoiminnalli",
           "oppiva organisaatio", "palvelumuotoilu", "onnistumisen edellytyk", "virtaustehokkuus", "alustatiimi",
           "jaettu johtajuus", "itseorganisoitumi", "tiimiorganisaatio", "valtuutettu", "ihmiskeskeinen",
           "verkostomainen", "päätösvalta", "lähijohtami", "valmentava ote", "käänteinen organisaatio",
           "nollaetäisyys", "käyttäjä maksaa palkan", "sisäinen yrittäjyys", "luottamusverkosto",
           "jatkuva parantami", "ketterä skaalautumi", "tulosvastuullinen tiimi", "asiakasrajapinta"
       ],
       "bad": [
           "Functional Silos", "Command and Control", "Digital Taylorism", "Handovers", "Resource Efficiency",
           "Approval Chain", "Micromanagement", "Output-driven", "Rigid processes", "Bureaucracy",
           "Gatekeeping", "Waterfall", "Order-taker", "Compliance-driven", "Siloed Data",
           "HiPPO", "Standardization", "Big Bang transformation", "Utilization Rate", "Legacy Thinking",
           "Spot Optimization", "Ticket-based", "Information Asymmetry", "Need to Know", "Predictability paradox",
           "Matrix misery", "Steering committee", "Change control board", "CCB", "Lines of command", 
           "Top-down mandate", "Departmental barriers", "Budget compliance", "Scope creep", "Change request", 
           "Sign-off culture", "HiPPO decision making", "Analysis paralysis", "Empire building", 
           "Information hoarding", "Design by committee", "Taylorist management", "Theory X",
           "Rank and yank", "Annual performance review", "Cascading goals"
       ],
       "bad_fi": [
           "siilo", "poteroitumi", "käskyttämi", "hierarkia", "mikromanagerointi",
           "byrokratia", "hyväksyntäketju", "vesiputousmalli", "kankeus", "siiloutumi",
           "suorituskeskeisy", "kontrollointi", "ylhäältä alas", "jäykkä", 
           "resurssitehokkuus", "käyttöaste", "kapulanvaihto", "portinvartijuus",
           "matriisiorganisaatio", "ohjausryhmä", "linjaorganisaatio", "komentoketju", "hyväksytys",
           "reviiriajattelu", "päällikkötaso", "hallinnollinen taakka", "kustannuspaikka",
           "siilopuolustus", "muutosvastarinta", "hallintahimmeli", "jäykkä budjetointi"
       ]
   },
   "Service Portfolio": {
       "good": [
           "SaaS", "Recurring Revenue", "API-First", "Platform Strategy", "Ecosystem",
           "Modular design", "Scalability", "Continuous Delivery", "User-centric", "Product-Market Fit",
           "Subscription model", "Cloud-native", "Data-driven", "Composable business", "Service Design",
           "Value Proposition", "Feedback Loop", "MVP", "Consumption-based", "Self-service",
           "Interoperability", "Digital Twin", "Solution-oriented", "Lifetime Value", "Frictionless experience",
           "Microservices architecture", "Headless commerce", "Omnichannel", "Usage-based pricing", "Stickiness",
           "Integration ready", "Developer friendly", "Marketplace", "Plug and play", "Always-on",
           "Outcome over output", "Solving wicked problems", "Living roadmap", "Continuous discovery",
           "Jobs to be done", "Value co-creation", "Product-led", "Community-driven", "Open API"
       ],
       "good_fi": [
           "jatkuva laskutus", "alustatalous", "ekosysteemi", "skaalautuvuu", "käyttäjälähtöi",
           "pilvipohjai", "datapohjai", "itsepalvelu", "modulaari", "toistuvaislaskutus",
           "palvelullistami", "rajapinta", "integroitavuus", "kokonaisratkaisu", "asiakasarvo",
           "käyttöperusteinen", "tilausmalli", "digitaalinen kaksonen", "kitkaton", "asiakaskokemus",
           "jatkuva toimitus", "arvolupaus", "ketterä kehitys", "modulaarisuus", "avoimet rajapinnat"
       ],
       "bad": [
           "Legacy system", "Monolith", "On-premise", "Heavy Customization", "Project-based",
           "One-off Sales", "Maintenance-heavy", "Vendor Lock-in", "Siloed Data", "Hardware-centric",
           "Manual Updates", "Consulting-heavy", "End-of-Life", "Fragmented portfolio", "Feature Factory",
           "Complexity", "Static roadmap", "Commoditized", "Proprietary", "License-based",
           "Discontinued", "Cost-plus Pricing", "Bloatware", "Version 1.0", "Cannibalization",
           "Bespoke development", "Heavily customized", "Sunset phase", "On-prem", "Perpetual license",
           "Lift and shift", "Golden handcuffs", "Spaghetti code", "Big design up front", "BDUF",
           "Scope negotiation", "Change order", "Point solution", "Closed garden"
       ],
       "bad_fi": [
           "vanhentu", "räätälöity", "projektiluontoi", "kertakauppa", "ylläpitoraskas",
           "toimittajaloukku", "järjestelmäriippuvuu", "monoliitti", "paikallinen asennus", "lisenssipohjai",
           "hajanaiset järjestelmät", "kustomoitu", "rautapohjainen", "massiivinen", "monimutkainen",
           "suljettu järjestelmä", "elinkaaren loppu", "projektitoimitus", "raskas ylläpito",
           "päivitysrumba", "järjestelmäviidakko", "yhteensopimaton", "pisteporautuminen"
       ]
   },
   "Values": {
       "good": [
           "Trust", "Transparency", "Psychological Safety", "Curiosity", "Courage",
           "Inclusion", "Empowerment", "Learning Organization", "Openness", "Empathy",
           "Collaboration", "Purpose-driven", "Adaptability", "Resilience", "Accountability",
           "Diversity", "Innovation", "Feedback culture", "Growth Mindset", "Integrity",
           "Sustainability", "Experimentation", "Fun", "Human-centric", "Servant Leadership",
           "Blameless post-mortem", "Radical candor", "Wellbeing", "Work-life balance", "Equity",
           "Social responsibility", "Ethical business", "Community", "Respect", "Ownership",
           "Freedom and responsibility", "Default to open", "Disagree and commit", "Celebrate failure",
           "Always learning", "Customer obsession", "Bias for action", "Invent and simplify"
       ],
       "good_fi": [
           "luottamus", "avoimuus", "läpinäkyvyys", "rohkeus", "oppimi", "yhteistyö",
           "tarkoitusperäisy", "sopeutumiskyky", "vastuullisuu", "monimuotoisuu", "palaute",
           "kasvun asenne", "kestävä kehitys", "ihmisläheisy", "nollaetäisyys",
           "syyllistämättömyys", "hyvinvointi", "tasa-arvo", "eettisyys", "yhteisöllisyys",
           "kokeilunhalu", "epäonnistumisen sallimi", "arvostus", "omistajuus", "itseohjautuvuus",
           "avoimuuden kulttuuri", "oppimiskyky", "sisu", "rehellisyys"
       ],
       "bad": [
           "Secrecy", "Fear culture", "Blame game", "Control freak", "Compliance focused",
           "Hierarchy", "Silos", "Micromanagement", "Obedience", "Office Politics",
           "Status Quo", "Risk-averse", "Arrogance", "Bureaucracy", "Rigidity",
           "Greed", "Exclusion", "Punishment", "Competitiveness", "Opacity",
           "Command structure", "Ego driven", "Short-termism", "Exploitation", "Distrust",
           "Nepotism", "Favoritism", "Crunch time", "Burnout", "Overwork",
           "Internal competition", "Face time", "Presenteeism", "Top-down culture", "Not my job",
           "Throw under the bus", "Cover your ass", "CYA", "Information silos", "Gatekeepers",
           "Management by fear", "Toxic positivity", "Gaslighting"
       ],
       "bad_fi": [
           "salailu", "pelko", "syyttely", "kontrolli", "sokea tottelemi",
           "valtapeli", "muutosvastarinta", "ylimielisy", "rangaistus", "kilpailuhenkisy",
           "epäluottamus", "lyhytnäköisy", "hyväksikäyttö", "kyttäyskulttuuri", "suosinta",
           "uupumus", "sisäinen kilpailu", "läsnäolopakko", "jäykkyys",
           "selän takana puhumi", "kuppikunnat", "johtajakeskeisyys", "virheiden peittely",
           "syntipukki", "pelolla johtaminen", "sanelu", "byrokraattinen"
       ]
   },
   "Strategy": {
       "good": [
           "North Star Metric", "Adaptive Strategy", "Value Creation", "Digital Transformation", "Growth engine",
           "Vision", "Mission", "Customer-centric", "Agile Strategy", "Innovation ecosystem",
           "Ecosystem play", "Data-driven", "Future-proof", "Resilience", "Differentiation",
           "Outcome-based", "Sustainability", "Disruption", "Blue Ocean", "Pivot",
           "Horizon planning", "Focus", "Alignment", "Execution", "Emergent strategy",
           "Strategic bet", "Optionality", "Customer success", "Market shaping", "Trend setting",
           "Proactive", "Visionary", "Holistic view", "Systemic thinking", "Long-term value",
           "OODA loop", "First principles thinking", "Backcasting", "Antifragility", "Infinite game",
           "Effectuation", "Sensemaking", "Value migration", "Crossing the chasm", "Flywheel effect",
           "Network effects", "Doing the right thing", "Impact driven"
       ],
       "good_fi": [
           "pohjantähti", "arvonluonti", "digitaalinen transformaatio", "kasvu", "visio",
           "asiakaskeskei", "innovaatio", "tulevaisuudenkestävä", "erottautumi", "kestävyys",
           "suunnanmuutos", "toimeenpano", "havainnoi ja reagoi", "vaikuttavuusohjautuva", "julkaise ja opi",
           "strateginen valinta", "asiakasmenestys", "markkinan muokkaami", "ennakoiva", "kokonaisvaltainen",
           "systeemiajattelu", "pitkän aikavälin arvo", "verkostovaikutus", "ketterä strategia",
           "kokeileva kehittämi", "uudistumiskyky", "strateginen ketteryys", "vaikuttavuus"
       ],
       "bad": [
           "Cost-cutting", "Defensive", "Short-term focus", "Static planning", "Reactive",
           "Generic strategy", "Status Quo", "Siloed execution", "Unclear goals", "Budget-driven",
           "Top-down", "Rigid plan", "Risk-averse", "Legacy-focused", "Competitor-obsessed",
           "Shareholder value only", "Volume-based", "Efficiency-only", "Drifting", "Fragmented",
           "Bureaucratic", "Complacent", "Opaque", "Incrementalism", "Project-based funding",
           "Quarterly result focused", "Cost reduction", "Layoffs", "Downsizing", "Optimizing for efficiency",
           "Race to the bottom", "Imitation", "Follower", "Conservative", "Sunk cost fallacy",
           "Vanity metrics", "Success theater", "Five year plan", "Annual budget cycle", "Cost center mentality",
           "Maximize utilization", "Headcount freeze", "Arbitrary targets", "Hockey stick growth"
       ],
       "bad_fi": [
           "kulukuri", "säästöt", "leikkaukset", "yt-neuvottelut", "lyhytnäköi", "staattinen",
           "reagoiva", "budjettiohjattu", "ylhäältä saneltu", "riskin välttämi", "kilpailijakeskei",
           "tehokkuusajattelu", "viisivuotissuunnitelma", "kiinteä tavoite", "kertarysäys",
           "kvartaalitalous", "kustannussäästö", "irtisanomi", "tehostami", "säilyttävä",
           "siiloutunut", "epäselvä suunta", "reaktiivinen", "numerolla johtaminen",
           "hallinnollinen", "kaavamainen", "joustamaton"
       ]
   },
   "Business Model": {
       "good": [
           "Platform Economy", "Ecosystem", "Recurring Revenue", "Network Effects", "Freemium",
           "SaaS", "Marketplace", "Data Monetization", "Servitization", "Outcome-based Pricing",
           "Direct-to-Consumer", "Sharing Economy", "API Economy", "Scalable", "Asset-light",
           "Community", "User-generated Content", "Open Source", "Partner Network", "Dynamic Pricing",
           "Lifetime Value", "Circular Economy", "Membership", "Flywheel", "Value Co-creation",
           "Product-led growth", "PLG", "Community-led", "Open innovation", "Crowdsourcing",
           "Long tail", "Two-sided market", "Demand aggregation", "Unbundling", "Bundling",
           "Subscription economy", "As-a-service", "XaaS", "Digital ecosystem", "Viral loop"
       ],
       "good_fi": [
           "alusta", "verkostovaikutus", "palvelullistami", "yhteisö", "jakamistalous",
           "skaalautuva", "kumppaniverkosto", "kiertotalous", "jäsenyys", "tuotevetoinen",
           "avoin innovaatio", "joukkoistami", "yhteiskehittämi", "kuukausilaskutus",
           "palvelumalli", "digitaalinen liiketoiminta", "arvoverkosto", "ekosysteemiajattelu",
           "suoramyynti", "käyttäjälähtöinen", "skaalautuvuus", "verkostoituminen"
       ],
       "bad": [
           "Pipeline business", "One-off Sales", "Middlemen", "Rent-seeking", "Asset-heavy",
           "Commodity", "Closed System", "Linear model", "Transactional", "Cost-plus",
           "Ad-driven", "Brick-and-mortar", "Intermediary", "Manual sales", "High Friction",
           "Locked-in", "Inventory-heavy", "Time-based billing", "Project-based", "Hidden Fees",
           "Arbitrage", "Unsustainable", "Predatory", "Analog", "Dependent",
           "Consulting hours", "Billable hours", "Markup", "Reselling", "Vendor lock",
           "Planned obsolescence", "Race to zero", "Supply chain fragility", "Single point of failure",
           "Margin compression", "Hardware dependency", "Physical distribution", "Legacy assets"
       ],
       "bad_fi": [
           "välikädet", "omaisuuserät", "suljettu järjestelmä", "lineaarinen", "tapahtumapohjai",
           "piilokulut", "kestämätön", "analoginen", "riippuvai", "tuntilaskutus",
           "katehinnoittelu", "jälleenmyynti", "varastoriippuvainen", "kertasuorite",
           "fyysinen jakelu", "vanhakantainen", "toimitusketju", "katteen pieneneminen",
           "suhdanneriippuvainen", "investointiraskas"
       ]
   },
   "AI Readiness": {
       "good": [
           "Cloud Native", "Data Lake", "Machine Learning", "API", "Microservices",
           "DevOps", "Data Mesh", "Automation", "Real-time data", "Predictive analytics",
           "Scalable infra", "Cybersecurity", "Data Quality", "Integration", "Open AI",
           "Python", "Algorithm", "Digital Twin", "Smart systems", "Self-healing",
           "Serverless", "Agile IT", "Citizen Developer", "Data Governance", "Augmented Intelligence",
           "LLM", "Generative AI", "Neural network", "Deep learning", "MLOps",
           "Data pipeline", "Streaming data", "Cloud-first", "Automated decisioning",
           "Data democratization", "Architectural isomorphism", "Contextual data", "Feedback loops",
           "Human-in-the-loop", "Ethical AI", "Vector database", "RAG", "Retrieval Augmented Generation",
           "Prompt engineering", "Fine-tuning", "Semantic search", "Knowledge graph",
           "Data as a Product", "Domain driven design", "Event sourcing"
       ],
       "good_fi": [
           "pilvi", "koneoppimi", "automaatio", "reaaliaikai", "ennakoiva",
           "tietoturva", "integraatio", "algoritmi", "älykäs", "tiedolla johtami",
           "tekoälyavusteinen", "generatiivinen", "neuroverkko", "pilviensin",
           "tietovarasto", "rajapinnat", "mikroplavelut", "itsekorjautuva",
           "datan laatu", "tietojohtaminen", "älykkäät järjestelmät", "kielimalli",
           "tapahtumapohjainen", "tietovirta", "skaalautuva arkkitehtuuri"
       ],
       "bad": [
           "Legacy IT", "On-premise", "Siloed Data", "Manual Process", "Spreadsheet",
           "Mainframe", "Monolith", "Security Breach", "Downtime", "Batch Processing",
           "Technical Debt", "Paper-based", "Disconnected systems", "Proprietary format", "Fragmented",
           "Unstructured Data", "Shadow IT", "Waterfall IT", "Cost Center", "Outdated stack",
           "Manual Entry", "Black Box", "Vulnerable", "Rigid infra", "Offline",
           "Excel-based", "Legacy migration", "Spaghetti code", "Data swamp", "Breach",
           "Tool-first fallacy", "Garbage in garbage out", "Algorithmic bias", "Isolated pilot",
           "POC purgatory", "Hardcoded", "Vendor dependent", "Data hoarding", "Lack of API",
           "Point-to-point integration", "Manual reconciliation", "Security theater", "Vaporware"
       ],
       "bad_fi": [
           "vanha järjestelmä", "siiloutunut data", "manuaalinen", "taulukkolaskenta", "tietomurto",
           "katkos", "tekninen velka", "paperi", "hajanai", "tietosuojaongelma",
           "excel-sulkeiset", "tietosuo", "vanhentunut", "irrallinen",
           "pisteporautuminen", "musta laatikko", "haavoittuva", "jäykkä",
           "purkkaviritys", "manuaalinen syöttö", "yhteensopivuusongelma", "järjestelmävirhe",
           "tietojärjestelmäprojekti", "toimittajariippuvuus"
       ]
   }
}

# Negation terms based on PDF Page 10 ("Negative Look-around")
NEGATION_TERMS = [
    "eliminate", "remove", "avoid", "anti", "reduce", "less", "no", "not", "without", "free from", "versus", "vs",
    "ei", "älä", "ilman", "poista", "vältä", "vähennä", "kiellä", "lopeta"
]

def is_negated(text_window):
    """
    Checks if any negation term appears in the text window preceding a keyword.
    """
    lower_window = text_window.lower()
    for term in NEGATION_TERMS:
        if term in lower_window:
            return True
    return False

def analyze_intent(text: str):
    breakdown = []
    
    total_good_matches = 0
    total_bad_matches = 0
    total_words = len(text.split())
    if total_words == 0: total_words = 1 

    # --- ADAPTIVE SENSITIVITY ---
    is_short_text = total_words < 400
    
    if is_short_text:
        GOOD_THRESHOLD = 1
        BAD_THRESHOLD = 1
        NORMALIZATION_DIVISOR = 5.0 
    else:
        GOOD_THRESHOLD = 3
        BAD_THRESHOLD = 1
        NORMALIZATION_DIVISOR = 25.0

    lower_text = text.lower()

    for category, keywords in CATEGORIES.items():
        unique_good_found = 0
        unique_bad_found = 0
        
        # Merge English and Finnish lists
        good_list = keywords["good"] + keywords.get("good_fi", [])
        bad_list = keywords["bad"] + keywords.get("bad_fi", [])

        # 1. Count Good Signals
        for word in good_list:
            # FIX: Morphological Matching for Finnish
            # English uses strict boundary \bword\b
            # Finnish uses prefix boundary \bword (allows suffixes like -ssa, -lla, -t, -yys)
            is_finnish = word in keywords.get("good_fi", [])
            
            if is_finnish:
                # Match start of word, allow characters after
                pattern = r'\b' + re.escape(word.lower())
            else:
                # Strict match for English to avoid false positives (e.g. "is" matching "island")
                pattern = r'\b' + re.escape(word.lower()) + r'\b'
                
            matches = list(re.finditer(pattern, lower_text))
            
            if len(matches) >= GOOD_THRESHOLD:
                unique_good_found += 1
            
            total_good_matches += len(matches)
            
        # 2. Count Bad Signals
        for word in bad_list:
            is_finnish = word in keywords.get("bad_fi", [])
            
            if is_finnish:
                pattern = r'\b' + re.escape(word.lower())
            else:
                pattern = r'\b' + re.escape(word.lower()) + r'\b'
                
            matches = list(re.finditer(pattern, lower_text))
            
            valid_bad_occurrences_for_word = 0
            
            for match in matches:
                # Context Window: Check ~50 chars before match
                start_index = max(0, match.start() - 50)
                preceding_text = lower_text[start_index:match.start()]
                
                if not is_negated(preceding_text):
                    valid_bad_occurrences_for_word += 1

            if valid_bad_occurrences_for_word >= BAD_THRESHOLD:
                unique_bad_found += 1
            
            total_bad_matches += valid_bad_occurrences_for_word

        # 3. Calculate Score with FRICTION WEIGHTING
        # UPGRADE: Bad signals weigh more (1.5x) than good signals.
        # This prevents "Keyword Stuffing" where a company mentions "Agile" 50 times 
        # but also mentions "Micromanagement" 5 times.
        FRICTION_WEIGHT = 2.0
        
        net_result = unique_good_found - (unique_bad_found * FRICTION_WEIGHT)
        
        if net_result < 0:
            score = 0
        else:
            score = (net_result / NORMALIZATION_DIVISOR) * 100
            if score > 100: score = 100

        breakdown.append({
            "name": category,
            "score": int(score),
            "goodCount": unique_good_found,
            "badCount": unique_bad_found
        })

    # --- AGGREGATE METRICS ---
    total_score = sum([c["score"] for c in breakdown])
    global_score = total_score / len(CATEGORIES)

    # Friction Index
    denominator = total_good_matches + total_bad_matches
    if denominator == 0:
        friction_index = 0.0
    else:
        ratio = abs(total_good_matches - total_bad_matches) / denominator
        friction_index = 1.0 - ratio 

    # Vibrancy Score
    vibrancy_raw = denominator / total_words
    vibrancy_score = (vibrancy_raw / 0.05) * 100 
    if vibrancy_score > 100: vibrancy_score = 100

    return {
        "score": int(global_score),
        "breakdown": breakdown,
        "metrics": {
            "friction_index": round(friction_index, 2),
            "vibrancy_score": int(vibrancy_score),
            "total_good_mentions": total_good_matches,
            "total_bad_mentions": total_bad_matches
        }
    }