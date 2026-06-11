# Market Intelligence Modal — Implementation Plan

## Architecture Overview

A new full-screen modal (`MarketIntelligenceModal.tsx`) accessible from the main dashboard. It aggregates cross-company data into strategic intelligence views using existing data structures. Backend provides a new `/api/market-intelligence` endpoint that computes all aggregations server-side.

**Dual-Lens Framework:**
- **Future Demand** = Foresight signals (where the market is heading)
- **Current Demand** = Diagnosis data (what the market needs now)

---

## Phase 1: Backend Aggregation Engine

### File: `backend/market_intelligence.py` (NEW)

**1A. TOP10 Foresights (Future Demand)**
- Input: All companies' `foresight_links[].fp_theme` + `foresight_summary` dimension forces
- Logic: Count occurrences of each of the 30 FP themes across all scanned companies. Group by the 7 theme categories. Return sorted descending with counts.
- Output: `top_foresights: [{theme, category, count, pct, example_companies}]`

**1B. TOP10 Root Causes / Methods (Current Demand)**
- Input: All companies' `why_proposals[]` and `what_proposals[]`
- Logic: Send all proposals to Claude Sonnet 4.6 with a clustering prompt — "Group these into max 10 recurring strategic patterns." Return cluster label + member count + representative examples.
- Fallback: If GPT/Claude unavailable, use simple keyword frequency counting on proposals.
- Output: `top_causes: [{cluster_label, count, type: "why"|"what", examples}]`

**1C. Gap Pattern Analysis**
- Input: All companies' `intent_breakdown[]` (per-dimension scores) and `reality_score`
- Logic: For each of the 5 KB dimensions + AI Readiness, compute average intent vs reality gap across all companies. Also compute per-dimension average reality scores.
- Output: `gap_patterns: [{dimension, avg_intent, avg_reality, avg_gap, company_count}]`

**1D. Archetype Distribution by Domain**
- Input: All companies' `archetype` and `domain` fields
- Logic: Cross-tabulate archetype x domain. Count companies in each cell.
- Output: `archetype_flow: [{domain, thriving, modernizing, cash_cow, at_risk, total}]`

**1E. Theme x Dimension Cross-Impact Matrix**
- Input: All `foresight_links[]` with both `fp_theme` and `kb_dimension`
- Logic: Build 30x5 matrix counting signals. Normalize to percentages per theme.
- Output: `cross_impact: [{theme, dimension, count}]` (sparse matrix)

**1F. Industry Foresight Radar**
- Input: `foresight_links[].fp_theme` grouped by company `domain`
- Logic: Per domain, count signals per theme category (7 categories). Normalize.
- Output: `industry_radar: [{domain, category_scores: {tech, economy, industry, environment, society, people, science}}]`

**1G. Market Health Gauge**
- Input: Per domain — avg reality_score, avg financial_index, avg reality_gap, archetype distribution
- Logic: Composite score = weighted(avg_reality * 0.4 + avg_financial * 0.3 + (100-avg_gap) * 0.15 + thriving_pct * 0.15)
- Output: `market_health: [{domain, score, avg_reality, avg_financial, avg_gap, company_count}]`

**1H. Foresight Coverage Meter**
- Input: All foresight_links across all companies
- Logic: Count distinct fp_themes that have 3+ company signals. Show coverage out of 30.
- Output: `foresight_coverage: {covered: N, total: 30, themes_detail: [{theme, signal_count}]}`

**1I. Most Vulnerable Companies (leverages existing statistical analysis)**
- Input: Reuse `analyze_scores.py` regression residuals + archetype + reality_gap
- Logic:
  1. Get all companies with negative residuals from reality_vs_financial regression (actual financial < predicted)
  2. Add companies classified as "At Risk" archetype
  3. Add companies with large positive reality_gap (intent >> reality = washing)
  4. Composite vulnerability score = `(-residual_normalized * 0.4) + (gap_normalized * 0.3) + (at_risk_flag * 0.3)`
  5. Sort descending, return TOP 15
- Output: `vulnerable_companies: [{name, domain, vulnerability_score, residual, gap, archetype, reason}]`

**1J. Demand Intensity per KB Dimension**
- Input: All companies' per-dimension gaps
- Logic: For each of the 5+1 KB dimensions, compute average gap across all companies
- Output: `demand_intensity: [{dimension, avg_gap, max_gap, companies_with_gap}]`

### File: `backend/main.py` — New endpoint

```python
@app.get("/api/market-intelligence")
def market_intelligence():
    data = load_all_company_data()  # reuse existing dashboard-data loading
    analysis = run_analysis(data)   # reuse existing statistical analysis
    mi = compute_market_intelligence(data, analysis)
    return JSONResponse(mi)
```

---

## Phase 2: Frontend Modal Component

### File: `components/MarketIntelligenceModal.tsx` (NEW)

**Structure:** Full-width modal (reuse CompanyModal pattern but `max-w-6xl` for charts). Tabbed navigation starts with a top-level **Opportunity Map** before the existing sections.

### Tab 0: Opportunity Map (Top-level Priority)

| Component | Visual | Data Source |
|-----------|--------|-------------|
| Domain Opportunity Ranking | Ranked table | `opportunity_map.domain_rankings` |
| Company Opportunity Ranking | Ranked table | `opportunity_map.company_rankings` |
| Scoring Formula | Formula snippet | `opportunity_map.formula` |

- Primary rank = `opportunity_score`
- Weighted inputs:
  1. High foresight pressure
  2. Low readiness / reality (readiness gap)
  3. Sufficient financial capacity
  4. Large intent-reality gap
  5. Repeated WHY/WHAT proposal cluster signals
- Goal: prioritize segments where future pressure is already colliding with current capability constraints.

### Tab 1: Executive Summary

| Component | Visual | Data Source |
|-----------|--------|-------------|
| Market Health Gauges | SVG arc/speedometer per domain | `market_health` |
| Foresight Coverage Meter | Circular progress ring (SVG) | `foresight_coverage` |
| Demand Intensity Bars | Recharts horizontal `BarChart` | `demand_intensity` |

- Market Health Gauges: Grid of gauges, color-coded Green (70+), Yellow (40-70), Red (<40)
- Foresight Coverage: "22/30 themes with 3+ signals" ring + mini bar chart per category
- Demand Intensity: Per KB dimension, average gap sorted descending (biggest gap = biggest demand)

### Tab 2: Future Demand (Foresight)

| Component | Visual | Data Source |
|-----------|--------|-------------|
| TOP10 Foresights Pareto | Recharts `ComposedChart` (bars + cumulative line) | `top_foresights` |
| Cross-Impact Matrix | HTML table with heat-colored cells | `cross_impact` |
| Industry Foresight Radar | Recharts `RadarChart` per domain | `industry_radar` |

- Pareto: Bars sorted descending, colored by theme category, cumulative % line overlay
- Matrix: Rows = top 15 themes by frequency, Columns = 5 KB dimensions, cell color = signal count
- Radar: One per top 4 domains, 7 axes = 7 theme categories, overlay for comparison

### Tab 3: Current Demand (Diagnosis)

| Component | Visual | Data Source |
|-----------|--------|-------------|
| TOP10 Root Causes Pareto | Recharts `BarChart` sorted descending | `top_causes` |
| Gap Pattern Analysis | Recharts grouped `BarChart` (intent vs reality) | `gap_patterns` |
| Archetype Flow by Domain | Recharts stacked `BarChart` | `archetype_flow` |

- Root Causes: Blue for WHY (strategic), Teal for WHAT (operational), tooltip shows examples
- Gap Pattern: 5 grouped bars, each showing intent (lighter) vs reality (darker) with gap annotation
- Archetype Flow: X = domains, stacks = Thriving (emerald), Modernizing (amber), Cash Cow (blue), At Risk (rose)

### Tab 4: Sales Priorities

| Component | Visual | Data Source |
|-----------|--------|-------------|
| Most Vulnerable Companies | Styled table with sort + color coding | `vulnerable_companies` |
| Vulnerability Scatter | Recharts `ScatterChart` (Reality vs Financial) | `vulnerable_companies` + regression |

- Table columns: Rank, Company, Domain, Vulnerability Score, Residual, Gap, Archetype, Primary Reason
- Color-coded rows (deep red = highest vulnerability)
- Click row → opens CompanyModal for drill-down
- Export button: CSV download
- Scatter: X = Reality Score, Y = Financial Index, dot size = vulnerability, regression line shown

---

## Phase 3: Integration & Wiring

### File: `App.tsx`

1. Add state: `isMarketIntelOpen`, `marketIntelData`, `marketIntelLoading`
2. Add handler: `handleOpenMarketIntel()` — fetches `/api/market-intelligence`
3. Add button: Next to existing "Statistical Analysis" button in dashboard header
4. Import and render `<MarketIntelligenceModal>` conditionally
5. Pass `onCompanyClick` handler for drill-down from vulnerability table to CompanyModal

### File: `types.ts`

Add `MarketIntelligenceData` interface covering all output structures from Phase 1.

---

## Phase 4: Theme Velocity Heatmap (Enhancement)

This requires **temporal data** — tracking when companies were scanned.

**Backend:**
- Use existing `lastUpdated` timestamps per company
- Group companies by scan month
- For each month bucket, count theme frequencies
- Output: `theme_velocity: [{month, theme, count, delta_vs_prev}]`

**Frontend:**
- Custom SVG heatmap grid
- Rows: Themes, Columns: Months
- Cell color: Intensity = frequency
- Arrow indicators for acceleration/deceleration

**Note:** Depends on having enough temporal spread in scan data. Implement after core is working.

---

## How Existing Statistical Analysis Supports "Most Vulnerable Companies"

The current `analyze_scores.py` already computes:
1. **Regression residuals** (Reality vs Financial) — companies with large negative residuals are financially underperforming relative to their operational maturity
2. **Archetype classification** — "At Risk" companies have both low Reality (<50) and low Financial (<50) scores
3. **Reality gap** — companies where intent >> reality show "washing" behavior (claiming more than delivering)

These three signals combine into a **vulnerability score** that directly answers: "Which companies are struggling most and represent the highest-priority sales opportunities?"

The key insight: **negative residuals from the existing regression analysis** identify companies that are worse off than they "should" be — their financial health doesn't match their operational reality. Combined with archetype and washing flags, this creates a prioritized list for the Sales Department without any new statistical methods.

---

## Implementation Order

| Step | What | Files | Effort |
|------|------|-------|--------|
| 1 | Types & interfaces | `types.ts` | Small |
| 2 | Backend aggregation engine | `backend/market_intelligence.py` | Large |
| 3 | API endpoint | `backend/main.py` | Small |
| 4 | Modal shell + tabs | `components/MarketIntelligenceModal.tsx` | Medium |
| 5 | Tab 1: Executive Summary | Same file | Medium |
| 6 | Tab 4: Sales Priorities (vulnerability table + scatter) | Same file | Medium |
| 7 | Tab 2: Future Demand (Pareto, matrix, radar) | Same file | Large |
| 8 | Tab 3: Current Demand (Pareto, gaps, archetypes) | Same file | Medium |
| 9 | App.tsx integration | `App.tsx` | Small |
| 10 | Theme Velocity heatmap | Enhancement | Medium |

---

## Key Design Decisions

1. **Server-side aggregation** — All heavy computation in Python (backend), frontend only renders. Keeps the modal fast.

2. **Reuse existing patterns** — Same modal styling as CompanyModal, same Recharts components, same Tailwind color scheme (slate/emerald/amber/rose).

3. **Vulnerability Score leverages existing stats** — Directly builds on `analyze_scores.py` regression residuals. No new statistical methods needed.

4. **GPT/Claude clustering for proposals** — Only the TOP10 Root Causes clustering requires an LLM call. Everything else is pure aggregation. GPT/Claude call is optional with keyword-frequency fallback.

5. **Progressive enhancement** — Core views (Pareto charts, tables) work with any data size. Advanced views (Radar, Heatmap) need 10+ companies. Show "insufficient data" messages gracefully.

6. **No new dependencies** — Recharts (already installed) covers BarChart, RadarChart, ScatterChart, ComposedChart. Custom SVG for gauges and heatmap.
