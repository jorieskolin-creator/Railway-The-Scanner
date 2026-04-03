# The 5-Jury Forensic Tribunal System

How the Scanner analyzes companies through 5 independent evidence-gathering dimensions, synthesizes findings, and produces scored assessments.

---

## The 5 Juries

Each jury represents a dimension of organizational reality. Defined in `backend/scanner_two.py` (JURY_CATEGORY_MAP, line 835):

| Jury | Name | Categories | What It Investigates |
|------|------|-----------|---------------------|
| **1** | Architectural | Operating Model, Engineering, Architecture, Code, API, Design System, Job, Tech Stack | GitHub repos, engineering blogs, job ads (LinkedIn, Duunitori), tech stack evidence, API documentation |
| **2** | Strategic | Strategy, Business, Procurement, Revenue, Investment, Leadership | Procurement records (hankintailmoitukset.fi), recurring revenue, Crunchbase acquisitions, Finnish business news (Kauppalehti, Talouselama) |
| **3** | Cultural | Values, Culture, Talent, Employee, Inclusion, Community, HR, People, Careers | Glassdoor/Teamblind reviews, careers pages, boomerang employees on LinkedIn, inclusion initiatives (Mimmit Koodaa), meetup hosting |
| **4** | AI & Data | AI, Data, Technology, Cloud, Academic, Research | HuggingFace models, StackShare profiles, AWS/Azure case studies, academic research (Theseus, Aalto, VTT) |
| **5** | Service Portfolio | Service, Portfolio, Delivery, Platform, Solution, Client, Reference | Service pages, case studies, client references, analyst reports (Gartner, Forrester, ISG) |

---

## Step-by-Step Flow

### Step 1: Intent Analysis
**File:** `backend/scanner_one.py` -> `analyze_intent()`

- Analyzes the company's **own marketing claims** from their website and uploaded documents
- Produces: **Intent Score (0-100)** representing how ambitious the company's self-narrative is
- This is what the company *says* it is

### Step 2: The Forensic Scan (All 5 Juries)
**File:** `backend/scanner_two.py` -> `x_ray_search()`

- A single LLM call (Claude Sonnet 4.6 or Gemini) performs **web searches across all 5 jury dimensions simultaneously**
- This is NOT five separate calls — one comprehensive scan covers all juries
- Two-pass architecture:
  - **Pass 1 (Search):** Model searches freely, reports findings in relaxed format
  - **Pass 2 (Structure):** Converts findings to standardized JSON schema
- Each finding includes: `category`, `found` (yes/no), `sentiment` (positive/negative), `snippet` (evidence quote), `url`, `is_recent` (< 12 months old)
- Also produces: `reality_score_adjustment` (-50 to +50), `confidence_score` (0-100), `washing_flag`
- Model fallback chain: Claude Sonnet 4.6 + Brave Search -> Gemini 3.1 Pro -> Gemini 2.5 Pro

### Step 3: Weak Jury Detection & Re-Search
**File:** `backend/scanner_two.py` -> `_identify_weak_juries()` (line 844), `_build_research_prompt()` (line 865)

- System counts findings per jury using JURY_CATEGORY_MAP keyword matching
- If any jury has **zero findings**, it is flagged as "weak"
- Targeted re-search runs **only for weak juries** with progressively broader search terms
- Up to 2 re-search loops, each wider than the last
- Confidence recalculated after each loop: +5 per new finding, +10 per resolved weak jury
- Loop stops when confidence >= 45 or no new findings emerge

### Step 4: Financial Health Analysis (Parallel)
**File:** `backend/scanner_two.py` -> `financial_health_search()`

- Runs **in parallel** with Steps 2-3 (not sequential)
- Independently scores the company's financial health on 4 sub-dimensions:
  - Revenue Trend: 15% weight
  - Profitability: 60% weight (dominant signal)
  - Financial Stability: 15% weight
  - Investment Signal: 10% weight
- Produces: **Financial Index (0-100)**
- Formula applies a profit gate and soft penalty for missing dimensions

### Step 5: Evidence Validation
**File:** `backend/scanner_two.py` -> `_validate_evidence_urls()`, `_validate_key_people()`

- All evidence URLs are verified via HTTP GET:
  - HTTP status check (rejects 4xx/5xx)
  - Soft 404 detection (pages returning 200 but showing "not found" content)
  - Company entity check (verifies the page mentions the correct company)
- Dead URLs marked as `verified: false`, wrong-entity URLs flagged
- Key People filtered by seniority tier (C-Suite > CxO > VP > Director > Head of > Manager) and company size
- Stakeholders capped at 6 maximum
- If >20% of URLs are dead: triggers URL re-scan

### Step 6: Scoring & Washing Detection
**File:** `backend/scanner_two.py` (lines 1473-1531)

**Reality Score calculation:**
- Base score: 50 (symmetric headroom: 50 down to 0, 50 up to 100)
- Plus adjustment from Step 2: -50 to +50 points
- Temporal weighting:
  - Evidence < 12 months old: 100% impact
  - Evidence > 18 months old: 20% impact (near-irrelevant unless recurring)
- Technical Debt penalty: -20 to -35 points if confirmed by recent evidence
- Severe (near-zero evidence across juries): -35 to -50 points
- Positive signals: +15 to +45 depending on jury coverage (2-3 juries = +15-25, 4-5 = +35-45)

**Washing Detection (only when Intent > 60 AND Reality < Intent):**
- AI Washing: claims "AI-driven" but no HuggingFace/StackShare evidence
- Agile Washing: claims "Tribes/Squads" but evidence shows waterfall processes
- Green Washing: environmental claims without execution evidence

**Delta Verification:**
- If score changed +/-25 or more from previous scan: automatic re-scan with same model
- If both scans agree on direction: accept (average the two)
- If re-scan disagrees: use more conservative result (closer to previous)

### Step 7: Archetype Derivation
**File:** `backend/main.py` (lines 2089-2103)

Mechanically derived from two scores:

|  | Financial >= 50 | Financial < 50 |
|---|---|---|
| **Reality >= 50** | **Thriving** | **Modernizing** |
| **Reality < 50** | **Cash Cow** | **At Risk** |

### Step 8: Strategic Foresight
**File:** `backend/strategic_foresight.py`

- 30 megatrend themes from Futures Platform analyzed against the company
- Pre-validated signals from the Foresight Knowledge Base are re-verified before use
- Forward-looking forces and scenarios generated per company
- All foresight source URLs validated (soft 404 + liveness check)

### Step 9: Final Synthesis (The Judge)
**File:** `backend/main.py` (lines 2189-2500+)

- **Claude Opus 4.6** with Extended Thinking (10,000 token budget) receives ALL outputs from previous steps:
  - Intent Score + Reality Score + Financial Index + all sub-scores
  - All jury findings, verified evidence links, key people
  - Washing flags, foresight data, previous scan context
- Opus performs three tasks:
  1. **Score each KB dimension** (0-20) based on evidence
  2. **Write the summary** (~375 words, 3-4 paragraphs) mapping findings to Knowledge Base terms
  3. **Write the executive pitch** (90-110 words for C-level audience) + why/what proposals
- Fallback chain: Gemini 3.1 Pro -> Mistral Large

### Step 10: Hallucination Check & Fact Verification
**Files:** `backend/reality_check.py`, `backend/general_summary.py`

Two layers of verification:

1. **Payload-based fact-check** (general_summary.py):
   - Extracts every factual claim from the generated summary
   - Checks if each claim traces back to a specific evidence item in the payload
   - If unsupported claims found: summary is **automatically re-generated** with explicit corrections

2. **AI-powered reality check** (reality_check.py):
   - Gemini with Google Search grounding verifies claims against live web data
   - Heuristic pattern matching detects obvious fabrications
   - Final output includes `hallucination_score` and `hallucination_flags`

---

## Key Design Principles

1. **Evidence gatherers, not voters.** The 5 juries do not vote or average their scores. They gather evidence from different angles. The final judgment comes from Opus synthesizing all evidence into a coherent assessment.

2. **No single jury failure collapses the analysis.** Weak juries are automatically re-searched with broader terms. The synthesis weighs what is actually found.

3. **Single LLM call for all 5 juries.** The forensic scan is one comprehensive API call, not five separate ones. This ensures consistent cross-jury reasoning.

4. **Evidence persists across scans.** Historical findings carry forward for up to 90 days unless contradicted by newer evidence ("innocent until proven fixed").

5. **Temporal weighting prevents stale intelligence.** Recent evidence (< 12 months) dominates; old evidence (> 18 months) is nearly irrelevant.

6. **Every claim is traceable.** The summary generation enforces an evidence-only rule: every sentence must trace back to a specific source in the payload. External AI fact-checkers rating summaries as "hallucinated" is a known artifact of those tools not having access to the Scanner's source evidence — when given the sources, verification rates reach 100%.

---

## Data Flow Diagram

```
Company Website/Docs
        |
        v
  [Step 1: Intent Analysis]  ──────────────────────────────> Intent Score (0-100)
        |
        v
  [Step 2: Forensic Scan]  ── 5 Jury dimensions ──> Findings + Adjustment + Confidence
        |                                                    |
        v                                                    |
  [Step 3: Weak Jury Re-Search]  ── fills gaps ──> Enhanced Findings
        |                                                    |
   (parallel)                                                |
        |                                                    |
  [Step 4: Financial Health]  ──────────────────────> Financial Index (0-100)
        |                                                    |
        v                                                    v
  [Step 5: Evidence Validation]  ── URL checks ──> Verified Evidence
        |
        v
  [Step 6: Scoring]  ──> Reality Score (0-100) + Washing Flag
        |
        v
  [Step 7: Archetype]  ──> Thriving / Modernizing / Cash Cow / At Risk
        |
        v
  [Step 8: Foresight]  ──> Strategic Forces + Scenarios
        |
        v
  [Step 9: Synthesis]  ──> Summary + Pitch + Proposals (by Opus 4.6)
        |
        v
  [Step 10: Fact-Check]  ──> Verified Output + Hallucination Score
```

---

## Key Source Files

| File | Role |
|------|------|
| `backend/scanner_one.py` | Intent analysis (Step 1) |
| `backend/scanner_two.py` | Forensic scan, juries, scoring, financial health (Steps 2-6) |
| `backend/main.py` | Orchestration, archetype, synthesis (Steps 7, 9) |
| `backend/strategic_foresight.py` | Foresight analysis (Step 8) |
| `backend/reality_check.py` | Hallucination detection (Step 10) |
| `backend/general_summary.py` | General summary generation + payload fact-check (Step 10) |
| `backend/foresight_harvester.py` | Foresight KB signal harvesting and cleanup |
| `backend/foresight_vulnerability.py` | FAVI segment + score computation |
