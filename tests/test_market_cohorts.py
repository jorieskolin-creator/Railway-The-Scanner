from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from market_cohorts import compute_sweden_summary_cohort, normalize_url_host  # noqa: E402


def _base_opportunity(name: str, score: float = 70.0) -> dict:
    return {
        "name": name,
        "domain": "Financial Services",
        "opportunity_score": score,
        "foresight_pressure": 60,
        "readiness_gap": 50,
        "financial_capacity": 40,
        "intent_reality_gap": 30,
        "proposal_cluster_signal": 2,
    }


def _base_favi(name: str, score: float = 55.0) -> dict:
    return {
        "name": name,
        "domain": "Financial Services",
        "favi_score": score,
        "fps": 60,
        "readiness_gap": 50,
        "fac": 40,
        "current_vulnerability": 30,
        "segment": "Trapped",
    }


def test_normalize_url_host_ignores_www_path_and_trailing_slash():
    assert normalize_url_host("https://www.avanza.se/start") == "avanza.se"
    assert normalize_url_host("https://avanza.se/") == "avanza.se"
    assert normalize_url_host("avanza.se/start") == "avanza.se"


def test_sweden_cohort_matches_name_and_host_and_preserves_scores():
    dashboard = [
        {
            "companyName": "Swedbank",
            "companyUrl": "https://swedbank.se/private",
            "domain": "Financial Services",
        }
    ]
    opportunity = _base_opportunity("Swedbank", 81.5)
    favi = _base_favi("Swedbank", 64.2)

    cohort = compute_sweden_summary_cohort(
        dashboard,
        [],
        {"company_rankings": [opportunity]},
        [favi],
    )

    assert cohort["matched_count"] == 1
    assert cohort["opportunities"] == [opportunity]
    assert cohort["favi_companies"] == [favi]


def test_same_name_different_host_does_not_match():
    cohort = compute_sweden_summary_cohort(
        [{"companyName": "Swedbank", "companyUrl": "https://example.com/"}],
        [],
        {"company_rankings": [_base_opportunity("Swedbank")]},
        [_base_favi("Swedbank")],
    )

    assert cohort["matched_count"] == 0
    assert cohort["opportunities"] == []
    assert cohort["favi_companies"] == []


def test_same_host_different_name_does_not_match():
    cohort = compute_sweden_summary_cohort(
        [{"companyName": "Wrong Bank", "companyUrl": "https://www.swedbank.se/"}],
        [],
        {"company_rankings": [_base_opportunity("Wrong Bank")]},
        [_base_favi("Wrong Bank")],
    )

    assert cohort["matched_count"] == 0
    assert cohort["opportunities"] == []
    assert cohort["favi_companies"] == []
