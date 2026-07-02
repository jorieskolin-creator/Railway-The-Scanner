from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from market_readiness_report import build_market_readiness_report, render_market_readiness_html  # noqa: E402


class MockStorage:
    dashboard = [
        {
            "companyName": "Sisu Auto",
            "domain": "Manufacturing",
            "reality_score": 47,
            "financial_index": 52,
            "reality_gap": 12,
            "lastUpdated": "2026-07-01T10:00:00",
        },
        {
            "companyName": "Vivicta",
            "domain": "IT Services",
            "reality_score": 54,
            "financial_index": 61,
            "reality_gap": 5,
            "lastUpdated": "2026-07-01T10:00:00",
        },
        {
            "companyName": "Other Example",
            "domain": "Other",
            "reality_score": 40,
            "financial_index": 45,
            "reality_gap": 10,
            "lastUpdated": "2026-07-01T10:00:00",
        },
    ]

    def retrieve_latest_data(self, prefix):
        assert prefix == "index/summary_list"
        return list(self.dashboard)

    def get_company_data(self, name):
        domain = "Manufacturing" if name == "Sisu Auto" else "IT Services"
        if name == "Other Example":
            domain = "Other"
        return {
            "companyName": name,
            "domain": domain,
            "reality_score": 47,
            "financial_index": 52,
            "reality_gap": 12,
            "evidence_based_risks": [
                {"risk": "AI and data readiness remains unclear for scaling operations."},
                {"risk": "Operating model scalability may constrain future execution."},
            ],
            "activation_pairs": [
                {
                    "theme_type": "current_status",
                    "why": "Service portfolio proof and delivery readiness should be validated.",
                    "what": "Validate delivery bottlenecks.",
                }
            ],
            "foresight_links": [
                {"fp_theme": "Artificial Intelligence", "kb_dimension": "AI Readiness"},
                {"fp_theme": "Industry & Manufacturing", "kb_dimension": "Operating Model"},
            ],
            "intent_breakdown": [
                {"name": "AI Readiness", "score": 65},
                {"name": "Operating Model", "score": 62},
            ],
        }

    def get_observatory(self):
        return {
            "generated_at": "2026-07-01T12:00:00",
            "finland_analysis": {
                "text": "Finnish market pressure is shifting toward data, resilience and measurable modernization."
            },
            "theme_momentum": [{"theme": "Artificial Intelligence"}],
        }

    def get_cross_impact_interpretation(self):
        return {
            "generated_at": "2026-07-01T12:30:00",
            "summary": "AI pressure concentrates around data readiness.",
        }


def test_market_readiness_report_generates_core_schema_and_hypotheses():
    report = build_market_readiness_report(MockStorage())

    assert report["title"] == "Market Readiness and Emerging Demand Outlook"
    assert report["metadata"]["company_count"] == 3
    assert report["metadata"]["domain_count"] == 2
    assert report["executive_summary"]["market_position_today"]
    assert report["current_market_challenges"]
    assert report["strategic_challenge_patterns"]
    assert report["domain_scorecards"]
    assert report["future_demand_analysis"]["emerging_demand_arenas"]
    assert report["emerging_need_hypotheses"]


def test_market_readiness_excludes_other_from_ranked_domain_scorecards_and_labels_small_samples():
    report = build_market_readiness_report(MockStorage())

    domains = [row["domain"] for row in report["domain_scorecards"]]
    assert "Other" not in domains
    assert all(row["sample_confidence"] == "Directional result - limited sample" for row in report["domain_scorecards"])


def test_market_readiness_html_uses_business_favi_labels_and_no_removed_future_widgets():
    report = build_market_readiness_report(MockStorage())
    html = render_market_readiness_html(report)

    assert "Priority Risk" in html
    assert "Top 10 Recurring Strategic Challenge Patterns" in html
    assert "Theme Momentum" not in html
    assert "Finnish Economy Force Map" not in html
