"""
Score Correlation & Statistical Analysis
=========================================
Analyzes all company scores from the dashboard index and runs:
  1. Pearson & Spearman correlation: Reality Score vs Financial Index
  2. Correlation matrix across all numeric scores
  3. Descriptive statistics (mean, median, std, min, max, skew)
  4. Archetype distribution
  5. Outlier detection (companies far from the regression line)
  6. Domain/industry and ownership breakdowns
  7. Score distributions

Can be used as:
  - API: imported by main.py and called via /api/analyze endpoint
  - CLI: python backend/analyze_scores.py (requires BLOB_READ_WRITE_TOKEN env var)
"""

import json, sys, statistics, math
from collections import Counter


# ---------------------------------------------------------------------------
# 1. DATA LOADING
# ---------------------------------------------------------------------------

def load_data_from_storage():
    """Load dashboard data via the BlobStorage layer (requires token in env)."""
    from storage import BlobStorage
    storage = BlobStorage()
    data = storage.retrieve_latest_data("index/summary_list")
    return data or []


def load_data_from_api():
    """Fetch from the live API (fallback for local dev without blob token)."""
    import requests, os
    base = os.environ.get("VITE_API_URL", "https://the-scanner.up.railway.app")
    resp = requests.get(f"{base}/dashboard-data", timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_data():
    """Try storage first, then API fallback."""
    import os
    if os.environ.get("BLOB_READ_WRITE_TOKEN"):
        print("Loading data from Vercel Blob Storage...")
        data = load_data_from_storage()
    else:
        print("No BLOB_READ_WRITE_TOKEN found, trying API...")
        data = load_data_from_api()
    print(f"  -> {len(data)} companies loaded.\n")
    return data


# ---------------------------------------------------------------------------
# 2. HELPERS
# ---------------------------------------------------------------------------

def pearson(x, y):
    n = len(x)
    if n < 3:
        return None, None
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = math.sqrt(sum((xi - mx)**2 for xi in x) / (n - 1))
    sy = math.sqrt(sum((yi - my)**2 for yi in y) / (n - 1))
    if sx == 0 or sy == 0:
        return 0.0, 1.0
    r = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / ((n - 1) * sx * sy)
    t_stat = r * math.sqrt((n - 2) / (1 - r**2 + 1e-12))
    p = 2 * (1 - _norm_cdf(abs(t_stat))) if n > 30 else None
    return r, p


def spearman(x, y):
    """Spearman rank correlation (non-parametric)."""
    def _rank(vals):
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(indexed):
            j = i
            while j < len(indexed) and indexed[j][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j + 1) / 2
            for k in range(i, j):
                ranks[indexed[k][0]] = avg_rank
            i = j
        return ranks
    return pearson(_rank(x), _rank(y))


def _norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _describe(values):
    """Return descriptive statistics as a dict."""
    if not values:
        return None
    n = len(values)
    avg = statistics.mean(values)
    std = statistics.stdev(values) if n > 1 else 0
    skew = 0
    if std > 0 and n > 2:
        skew = (n / ((n-1)*(n-2))) * sum(((v - avg)/std)**3 for v in values)
    return {
        "n": n,
        "mean": round(avg, 1),
        "median": round(statistics.median(values), 1),
        "std": round(std, 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "skew": round(skew, 2),
    }


def classify_archetype(intent, reality):
    if intent >= 50 and reality >= 50:
        return "Walk-and-Talk (Aligned)"
    elif intent >= 50 and reality < 50:
        return "Talk-not-Walk (Hype)"
    elif intent < 50 and reality >= 50:
        return "Silent Doers"
    else:
        return "Stagnation"


# ---------------------------------------------------------------------------
# 3. ANALYSIS ENGINE (returns structured dict)
# ---------------------------------------------------------------------------

def run_analysis(data):
    """Run full statistical analysis on dashboard data. Returns a structured dict."""

    # --- Extract scores ---
    companies = []
    for c in data:
        intent = c.get("intent_score")
        reality = c.get("reality_score")
        if intent is None or reality is None:
            continue
        companies.append({
            "name": c.get("companyName", "?"),
            "intent": float(intent),
            "reality": float(reality),
            "financial": float(c["financial_index"]) if c.get("financial_index") is not None else None,
            "confidence": float(c["confidence_score"]) if c.get("confidence_score") is not None else None,
            "gap": float(c["reality_gap"]) if c.get("reality_gap") is not None else None,
            "revenue": float(c["revenue_avg_3yr"]) if c.get("revenue_avg_3yr") else None,
            "employees": int(c["employees"]) if c.get("employees") else None,
            "ownership": c.get("ownership", ""),
            "domain": c.get("domain", ""),
        })

    if len(companies) < 3:
        return {"error": "Not enough companies with scores to analyze", "n": len(companies)}

    result = {"n": len(companies)}

    # --- A. Descriptive Statistics ---
    result["descriptive"] = {
        "intent": _describe([c["intent"] for c in companies]),
        "reality": _describe([c["reality"] for c in companies]),
        "financial": _describe([c["financial"] for c in companies if c["financial"] is not None]),
        "confidence": _describe([c["confidence"] for c in companies if c["confidence"] is not None]),
        "gap": _describe([c["gap"] for c in companies if c["gap"] is not None]),
    }

    # --- B. Core Correlation: Reality vs Financial ---
    paired_rf = [(c["reality"], c["financial"]) for c in companies if c["financial"] is not None]
    core_corr = {"n": len(paired_rf)}
    if len(paired_rf) >= 3:
        rx, ry = zip(*paired_rf)
        r_p, p_p = pearson(list(rx), list(ry))
        r_s, p_s = spearman(list(rx), list(ry))
        core_corr["pearson_r"] = round(r_p, 3) if r_p is not None else None
        core_corr["pearson_p"] = round(p_p, 4) if p_p is not None else None
        core_corr["spearman_rho"] = round(r_s, 3) if r_s is not None else None
        core_corr["spearman_p"] = round(p_s, 4) if p_s is not None else None

        # Interpret
        strength = abs(r_p) if r_p else 0
        if strength < 0.1:
            interp = "negligible"
        elif strength < 0.3:
            interp = "weak"
        elif strength < 0.5:
            interp = "moderate"
        elif strength < 0.7:
            interp = "strong"
        else:
            interp = "very strong"
        core_corr["interpretation"] = f"{interp} {'positive' if r_p and r_p > 0 else 'negative'}"

        # Outliers from regression line
        if len(paired_rf) >= 5 and r_p is not None:
            sx_val = statistics.stdev(list(rx))
            slope = r_p * (statistics.stdev(list(ry)) / (sx_val + 1e-12)) if sx_val else 0
            intercept = statistics.mean(list(ry)) - slope * statistics.mean(list(rx))
            residuals = []
            for c in companies:
                if c["financial"] is not None:
                    predicted = slope * c["reality"] + intercept
                    residual = c["financial"] - predicted
                    residuals.append({
                        "name": c["name"],
                        "reality": c["reality"],
                        "financial": c["financial"],
                        "residual": round(residual, 1),
                    })
            residuals.sort(key=lambda r: abs(r["residual"]), reverse=True)
            core_corr["outliers"] = residuals[:5]

    result["reality_vs_financial"] = core_corr

    # --- C. Full Correlation Matrix ---
    score_keys = ["intent", "reality", "financial", "confidence"]
    matrix = {}
    for ki in score_keys:
        row = {}
        for kj in score_keys:
            pairs = [(c[ki], c[kj]) for c in companies if c[ki] is not None and c[kj] is not None]
            if len(pairs) >= 3:
                x, y = zip(*pairs)
                r, _ = pearson(list(x), list(y))
                row[kj] = round(r, 3) if r is not None else None
            else:
                row[kj] = None
        matrix[ki] = row
    result["correlation_matrix"] = matrix

    # --- D. Archetype Distribution ---
    archetypes = [classify_archetype(c["intent"], c["reality"]) for c in companies]
    counts = Counter(archetypes)
    total = len(archetypes)
    result["archetypes"] = {
        arch: {"count": counts.get(arch, 0), "pct": round(counts.get(arch, 0) / total * 100, 1)}
        for arch in ["Walk-and-Talk (Aligned)", "Talk-not-Walk (Hype)", "Silent Doers", "Stagnation"]
    }

    # --- E. Domain Breakdown ---
    domains = {}
    for c in companies:
        d = c["domain"] or "Unknown"
        domains.setdefault(d, []).append(c)
    domain_stats = {}
    for d, cs in sorted(domains.items()):
        fin_vals = [c["financial"] for c in cs if c["financial"] is not None]
        gap_vals = [c["gap"] for c in cs if c["gap"] is not None]
        domain_stats[d] = {
            "n": len(cs),
            "avg_intent": round(statistics.mean([c["intent"] for c in cs]), 1),
            "avg_reality": round(statistics.mean([c["reality"] for c in cs]), 1),
            "avg_financial": round(statistics.mean(fin_vals), 1) if fin_vals else None,
            "avg_gap": round(statistics.mean(gap_vals), 1) if gap_vals else None,
        }
    result["by_domain"] = domain_stats

    # --- F. Ownership Breakdown ---
    ownerships = {}
    for c in companies:
        o = c["ownership"] or "Unknown"
        ownerships.setdefault(o, []).append(c)
    ownership_stats = {}
    for o, cs in sorted(ownerships.items()):
        fin_vals = [c["financial"] for c in cs if c["financial"] is not None]
        ownership_stats[o] = {
            "n": len(cs),
            "avg_intent": round(statistics.mean([c["intent"] for c in cs]), 1),
            "avg_reality": round(statistics.mean([c["reality"] for c in cs]), 1),
            "avg_financial": round(statistics.mean(fin_vals), 1) if fin_vals else None,
        }
    result["by_ownership"] = ownership_stats

    # --- G. Top & Bottom ---
    by_reality = sorted(companies, key=lambda c: c["reality"], reverse=True)
    result["top_10_reality"] = [
        {"name": c["name"], "intent": c["intent"], "reality": c["reality"], "financial": c["financial"]}
        for c in by_reality[:10]
    ]
    result["bottom_10_reality"] = [
        {"name": c["name"], "intent": c["intent"], "reality": c["reality"], "financial": c["financial"]}
        for c in by_reality[-10:]
    ]

    # --- H. Distributions ---
    distributions = {}
    for key in ["intent", "reality", "financial"]:
        vals = [c[key] for c in companies if c[key] is not None]
        if not vals:
            continue
        bins = [0] * 10
        for v in vals:
            idx = min(int(v // 10), 9)
            bins[idx] += 1
        distributions[key] = {
            "n": len(vals),
            "bins": [{"range": f"{i*10}-{(i+1)*10-1 if i < 9 else 100}", "count": bins[i]} for i in range(10)],
        }
    result["distributions"] = distributions

    return result


# ---------------------------------------------------------------------------
# 4. PRETTY PRINTER (for CLI usage)
# ---------------------------------------------------------------------------

def print_analysis(r):
    """Print structured analysis results in a human-readable format."""
    if "error" in r:
        print(f"ERROR: {r['error']}")
        return

    print(f"{'='*70}")
    print(f" STATISTICAL ANALYSIS — {r['n']} companies")
    print(f"{'='*70}\n")

    # A. Descriptive
    print("A. DESCRIPTIVE STATISTICS")
    print("-" * 70)
    for label, key in [("Intent Score", "intent"), ("Reality Score", "reality"),
                       ("Financial Index", "financial"), ("Confidence Score", "confidence"),
                       ("Reality Gap (I-R)", "gap")]:
        d = r["descriptive"].get(key)
        if d:
            print(f"  {label:25s}  n={d['n']:3d}  mean={d['mean']:5.1f}  median={d['median']:5.1f}  "
                  f"std={d['std']:5.1f}  min={d['min']:5.1f}  max={d['max']:5.1f}  skew={d['skew']:+.2f}")
        else:
            print(f"  {label:25s}  no data")
    print()

    # B. Core Correlation
    print("B. CORE CORRELATION: Reality Score vs Financial Index")
    print("-" * 70)
    cc = r["reality_vs_financial"]
    if cc.get("pearson_r") is not None:
        print(f"  Paired observations:  n = {cc['n']}")
        p_str = f"(p={cc['pearson_p']:.4f})" if cc.get('pearson_p') is not None else "(p: n/a, n<30)"
        s_str = f"(p={cc['spearman_p']:.4f})" if cc.get('spearman_p') is not None else "(p: n/a, n<30)"
        print(f"  Pearson  r = {cc['pearson_r']:+.3f}  {p_str}")
        print(f"  Spearman p = {cc['spearman_rho']:+.3f}  {s_str}")
        print(f"\n  Interpretation: {cc['interpretation'].upper()}")
        if cc.get("outliers"):
            print(f"\n  Largest outliers from Reality->Financial regression:")
            for i, o in enumerate(cc["outliers"]):
                label = "Financial > expected" if o["residual"] > 0 else "Financial < expected"
                fin = f"{o['financial']:4.0f}"
                print(f"    {i+1}. {o['name']:35s}  Reality={o['reality']:4.0f}  Financial={fin}  residual={o['residual']:+.1f}  ({label})")
    else:
        print(f"  Not enough paired data (n={cc['n']}). Need >=3 companies with both scores.")
    print()

    # C. Correlation Matrix
    print("C. FULL CORRELATION MATRIX (Pearson)")
    print("-" * 70)
    labels = ["Intent", "Reality", "Financial", "Confidence"]
    keys = ["intent", "reality", "financial", "confidence"]
    print(f"  {'':15s}", end="")
    for lbl in labels:
        print(f"{lbl:>12s}", end="")
    print()
    for i, ki in enumerate(keys):
        print(f"  {labels[i]:15s}", end="")
        for kj in keys:
            v = r["correlation_matrix"].get(ki, {}).get(kj)
            print(f"{v:+12.3f}" if v is not None else f"{'n/a':>12s}", end="")
        print()
    print()

    # D. Archetypes
    print("D. ARCHETYPE DISTRIBUTION")
    print("-" * 70)
    for arch in ["Walk-and-Talk (Aligned)", "Talk-not-Walk (Hype)", "Silent Doers", "Stagnation"]:
        a = r["archetypes"][arch]
        bar = "#" * int(a["pct"] / 100 * 40)
        print(f"  {arch:30s}  {a['count']:3d} ({a['pct']:4.1f}%)  {bar}")
    print()

    # E. Domain
    print("E. AVERAGE SCORES BY INDUSTRY DOMAIN")
    print("-" * 70)
    print(f"  {'Domain':40s} {'n':>3s} {'Intent':>7s} {'Reality':>8s} {'Fin.Idx':>8s} {'Gap':>6s}")
    for d, s in r["by_domain"].items():
        fin = f"{s['avg_financial']:8.1f}" if s["avg_financial"] is not None else "     n/a"
        gap = f"{s['avg_gap']:+6.1f}" if s["avg_gap"] is not None else "   n/a"
        print(f"  {d:40s} {s['n']:3d} {s['avg_intent']:7.1f} {s['avg_reality']:8.1f} {fin} {gap}")
    print()

    # F. Ownership
    print("F. AVERAGE SCORES BY OWNERSHIP TYPE")
    print("-" * 70)
    print(f"  {'Ownership':25s} {'n':>3s} {'Intent':>7s} {'Reality':>8s} {'Fin.Idx':>8s}")
    for o, s in r["by_ownership"].items():
        fin = f"{s['avg_financial']:8.1f}" if s["avg_financial"] is not None else "     n/a"
        print(f"  {o:25s} {s['n']:3d} {s['avg_intent']:7.1f} {s['avg_reality']:8.1f} {fin}")
    print()

    # G. Top/Bottom
    print("G. TOP 10 & BOTTOM 10 BY REALITY SCORE")
    print("-" * 70)
    print("  TOP 10:")
    for i, c in enumerate(r["top_10_reality"]):
        fin = f"{c['financial']:4.0f}" if c["financial"] is not None else " n/a"
        print(f"    {i+1:2d}. {c['name']:35s}  Intent={c['intent']:4.0f}  Reality={c['reality']:4.0f}  Financial={fin}")
    print("\n  BOTTOM 10:")
    for i, c in enumerate(r["bottom_10_reality"]):
        fin = f"{c['financial']:4.0f}" if c["financial"] is not None else " n/a"
        print(f"    {i+1:2d}. {c['name']:35s}  Intent={c['intent']:4.0f}  Reality={c['reality']:4.0f}  Financial={fin}")
    print()

    # H. Distributions
    print("H. SCORE DISTRIBUTIONS (text histogram)")
    print("-" * 70)
    for label, key in [("Reality Score", "reality"), ("Financial Index", "financial"), ("Intent Score", "intent")]:
        dist = r["distributions"].get(key)
        if not dist:
            continue
        bins = dist["bins"]
        max_count = max(b["count"] for b in bins) if bins else 1
        print(f"\n  {label} (n={dist['n']}):")
        for b in bins:
            bar = "#" * int(b["count"] / max_count * 30) if max_count > 0 else ""
            print(f"    {b['range']:>7s}: {bar} {b['count']}")
    print()

    print("=" * 70)
    print(" ANALYSIS COMPLETE")
    print("=" * 70)


# ---------------------------------------------------------------------------
# 5. CLI ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    data = load_data()
    result = run_analysis(data)
    print_analysis(result)
