"""Regression tests for the canonical evidence and claim graph."""

from __future__ import annotations

import os
import sys


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import evidence_graph  # noqa: E402
import reality_check  # noqa: E402


def test_accepted_foresight_duplicate_wins_failed_retrieval_for_same_url():
    url = "https://www.oecd.org/en/publications/industrial-ai"
    data = {
        "evidence_links": [],
        "foresight_links": [{
            "url": url,
            "title": "Industrial AI",
            "url_verified": False,
            "source_readable": False,
            "source_audit_status": "http_403",
            "foresight_context_eligible": False,
        }],
        "strategic_foresight": {
            "foresight_evidence": [{
                "url": url + "#chapter",
                "title": "Industrial AI",
                "url_verified": True,
                "source_readable": True,
                "content_verified": True,
                "foresight_context_eligible": True,
                "source_quote": "Industrial AI is moving from isolated pilots toward scalable operating capability.",
                "theme": "Artificial Intelligence",
            }],
        },
    }

    evidence_graph.attach_evidence_graph(data)

    rows = [row for row in data["evidence_manifest"] if row["evidence_kind"] == "foresight_context"]
    assert len(rows) == 1
    assert rows[0]["evidence_id"].startswith("FOR-")
    assert rows[0]["foresight_context_eligible"] is True
    assert rows[0]["source_readable"] is True
    assert rows[0]["duplicate_verdicts_resolved"] is True
    assert any(event.get("audit_status") == "http_403" for event in rows[0]["retrieval_history"])


def test_fact_checker_receives_the_same_canonical_manifest_used_by_generation():
    manifest = evidence_graph.build_evidence_manifest(
        company_links=[{
            "url": "https://example.com/news/investment",
            "title": "Investment",
            "url_verified": True,
            "content_verified": True,
            "entity_match_status": "exact",
            "claim_support_status": "supported",
            "source_quote": "Example Oy announced a EUR 10 million investment.",
        }],
        foresight_links=[{
            "url": "https://oecd.org/report/industrial-ai",
            "title": "Industrial AI",
            "url_verified": True,
            "source_readable": True,
            "foresight_context_eligible": True,
            "semantic_snippet": "Industrial firms are scaling governed AI capabilities.",
        }],
    )

    packets = reality_check._build_fact_check_source_pack(evidence_manifest=manifest)

    assert {packet["evidence_id"] for packet in packets} == {row["evidence_id"] for row in manifest}
    assert {packet["evidence_kind"] for packet in packets} == {"company_evidence", "foresight_context"}


def test_claim_graph_links_only_explicit_premises_and_all_ids_resolve():
    manifest = evidence_graph.build_evidence_manifest(company_links=[{
        "url": "https://example.com/platform",
        "source_quote": "Example Oy operates a shared data platform.",
        "url_verified": True,
        "content_verified": True,
        "entity_match_status": "exact",
    }])
    evidence_id = manifest[0]["evidence_id"]
    data = {
        "summary_text": (
            f"A shared platform may reduce fragmented delivery because the current platform is documented [{evidence_id}]. "
            "A second unsupported interpretation remains uncited."
        ),
    }

    claims, _ = evidence_graph.build_claim_graph(data, manifest)

    assert claims[0]["evidence_ids"] == [evidence_id]
    assert claims[0]["citation_status"] == "explicit"
    assert claims[1]["evidence_ids"] == []
    assert claims[1]["citation_status"] == "missing"
    assert all(evidence_id in {row["evidence_id"] for row in manifest} for evidence_id in claims[0]["evidence_ids"])


def test_claim_graph_preserves_jury_package_lineage():
    data = {
        "evidence_links": [{
            "url": "https://example.com/services",
            "title": "Services",
            "source_quote": "Example lists lifecycle services and customer support.",
            "url_verified": True,
            "content_verified": True,
            "entity_match_status": "exact",
            "source_context_status": "scoped_context",
            "claim_support_status": "context_only",
            "evidence_package_id": "PKG-abc123",
            "source_id": "SRC-source1",
            "parsed_source_id": "PRS-source1",
            "support_passage_ids": ["PASS-one"],
            "proposition_ids": ["PROP-one"],
        }],
        "reality_checklist": [{
            "found": True,
            "finding": "Example has lifecycle services and customer support.",
            "url": "https://example.com/services",
            "claim_support_status": "context_only",
            "support_level": "context_only",
            "source_context_status": "scoped_context",
            "evidence_package_id": "PKG-abc123",
            "support_passage_ids": ["PASS-one"],
            "proposition_ids": ["PROP-one"],
            "finding_contract": "jury_finding/v1",
            "finding_id": "FND-abc123",
            "claim_support_relationship": {
                "relationship_id": "CSR-abc123",
                "finding_id": "FND-abc123",
                "source_context_status": "scoped_context",
                "support_status": "context_only",
                "final_decision": "analysis_premise",
            },
        }],
    }

    evidence_graph.attach_evidence_graph(data)

    manifest_row = data["evidence_manifest"][0]
    claim = data["claim_graph"][0]
    assert manifest_row["evidence_package_id"] == "PKG-abc123"
    assert manifest_row["parsed_source_id"] == "PRS-source1"
    assert manifest_row["support_passage_ids"] == ["PASS-one"]
    assert manifest_row["proposition_ids"] == ["PROP-one"]
    assert claim["evidence_package_id"] == "PKG-abc123"
    assert claim["support_passage_ids"] == ["PASS-one"]
    assert claim["proposition_ids"] == ["PROP-one"]
    assert claim["finding_id"] == "FND-abc123"
    assert claim["claim_support_relationship"]["relationship_id"] == "CSR-abc123"
    assert manifest_row["claim_support_relationship"]["final_decision"] == "analysis_premise"
    assert data["evidence_links"][0]["associated_claim_ids"] == [claim["claim_id"]]
