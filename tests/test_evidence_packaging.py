import os
import sys


BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from evidence_contracts import JURY_EVIDENCE_PACKAGE
from evidence_packaging import (
    ARCHITECTURAL_JURY,
    CULTURAL_JURY,
    SERVICE_JURY,
    STRATEGIC_JURY,
    build_jury_evidence_packages,
    build_subdimension_coverage,
    render_packages_for_prompt,
)


def test_jury_package_uses_fetched_content_not_semantic_snippet_as_proof():
    source = {
        "url": "https://www.example.com/services/cloud",
        "title": "Cloud services",
        "source_type": "official_service_page",
        "semantic_snippet": "Routing hint that mentions broad services but is not proof.",
        "fetched_excerpt": (
            "Example states that its cloud services include managed operations, "
            "application support, and lifecycle maintenance for enterprise customers."
        ),
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
        "source_context_status": "scoped_context",
    }

    packages = build_jury_evidence_packages(SERVICE_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    assert package["evidence_contract"] == JURY_EVIDENCE_PACKAGE
    assert package["propositions"]
    assert "managed operations" in package["propositions"][0]["source_passage"]
    assert package["semantic_snippet"] == source["semantic_snippet"]
    rendered = render_packages_for_prompt(packages)[0]
    assert "semantic_snippet_for_routing_only" in rendered
    assert "parsed_source_summary" in rendered
    assert "support_passages" in rendered
    assert "safe_propositions" in rendered


def test_subdimension_coverage_tracks_weak_and_missing_areas():
    source = {
        "url": "https://www.example.com/services/reference",
        "title": "Customer reference for managed services",
        "fetched_excerpt": "The customer reference describes service delivery and lifecycle support.",
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "source_context_status": "scoped_context",
    }

    packages = build_jury_evidence_packages(SERVICE_JURY, [source])
    coverage = build_subdimension_coverage(SERVICE_JURY, packages)

    assert coverage["weak"]
    assert "measurable_outcomes" in coverage["missing"]


def test_culture_team_responsibility_routes_by_semantic_pattern():
    source = {
        "url": "https://www.example.com/careers/culture",
        "title": "How our teams work",
        "fetched_excerpt": (
            "Our teams take responsibility for end-to-end delivery. People are trusted "
            "to make decisions close to the customer, and leaders support continuous learning."
        ),
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
        "source_context_status": "scoped_context",
    }

    packages = build_jury_evidence_packages(CULTURAL_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    assert package["routing_basis"] == "semantic_pattern"
    pattern_names = {item["pattern_family"] for item in package["semantic_patterns"]}
    assert "team_responsibility" in pattern_names
    assert "learning_reskilling" in pattern_names
    proposition = package["propositions"][0]
    assert proposition["source_role"] == "scoped_context"
    assert "company-wide maturity" in " ".join(proposition["cannot_support"])


def test_strategic_carveout_routes_to_business_model_ownership():
    source = {
        "url": "https://news.example.com/company-carveout",
        "title": "Company becomes a separate portfolio company",
        "fetched_excerpt": (
            "The transaction creates a separate company after a carve-out from the former group. "
            "The new owner describes a long-term investment thesis and growth platform."
        ),
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "publisher_independence": "credible media",
    }

    packages = build_jury_evidence_packages(STRATEGIC_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    assert package["routing_basis"] == "semantic_pattern"
    assert "business_model_ownership" in package["subdimensions"]
    pattern_names = {item["pattern_family"] for item in package["semantic_patterns"]}
    assert {"ownership_change", "investment_thesis"} & pattern_names


def test_generic_values_page_is_not_upgraded_without_behavioral_detail():
    source = {
        "url": "https://www.example.com/values",
        "title": "Values and culture",
        "fetched_excerpt": "Our values are people, customer, quality, and trust. We care about culture.",
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
        "source_context_status": "scoped_context",
    }

    packages = build_jury_evidence_packages(CULTURAL_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    assert package["routing_basis"] == "keyword_or_metadata"
    assert package["routing_warning"] == "generic_brand_language"
    assert "generic_brand_language" in package["limitations"]


def test_operating_model_accountability_routes_architecture_context():
    source = {
        "url": "https://www.example.com/operating-model",
        "title": "Operating model",
        "fetched_excerpt": (
            "The governance model defines process owners, approval process responsibilities, "
            "and business-unit accountability for end-to-end delivery."
        ),
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
    }

    packages = build_jury_evidence_packages(ARCHITECTURAL_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    assert package["routing_basis"] == "semantic_pattern"
    assert {"operating_model", "decision_rights"} & set(package["subdimensions"])


def test_parser_selects_late_relevant_support_passage():
    filler = "General introduction about the company history and public website. " * 35
    relevant = (
        "The services portfolio includes lifecycle maintenance, spare parts, field service, "
        "and customer support for vehicles in operation. The page describes delivery reality "
        "rather than only a marketing slogan."
    )
    source = {
        "url": "https://www.example.com/services",
        "title": "Services",
        "fetched_excerpt": filler + relevant,
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
        "source_context_status": "scoped_context",
    }

    packages = build_jury_evidence_packages(SERVICE_JURY, [source])

    assert len(packages) == 1
    package = packages[0]
    passages = package["support_passages"]
    assert 1 <= len(passages) <= 4
    joined = " ".join(passage["text"] for passage in passages)
    assert "lifecycle maintenance" in joined
    assert "lifecycle maintenance" in package["propositions"][0]["source_passage"]


def test_asset_sources_are_not_packaged_for_jury():
    source = {
        "url": "https://www.example.com/assets/service-icon.png",
        "title": "Service icon",
        "content_type": "image/png",
        "fetched_excerpt": "service delivery customer support",
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
    }

    assert build_jury_evidence_packages(SERVICE_JURY, [source]) == []


def test_jury_package_exposes_parser_passage_proposition_lineage():
    source = {
        "url": "https://www.example.com/services/platform",
        "title": "Platform services",
        "fetched_excerpt": (
            "Example states that its platform service includes managed operations, "
            "customer support, lifecycle maintenance, and delivery monitoring. "
            "The page does not provide audited outcome metrics."
        ),
        "url_verified": True,
        "content_readable": True,
        "entity_verified": True,
        "official_domain_scope_inferred": True,
        "source_context_status": "scoped_context",
        "claim_support_status": "unsupported_for_this_claim",
    }

    package = build_jury_evidence_packages(SERVICE_JURY, [source])[0]
    rendered = render_packages_for_prompt([package])[0]

    assert package["source_id"].startswith("SRC-")
    assert package["parsed_source_id"].startswith("PRS-")
    assert package["support_passage_ids"]
    assert package["proposition_ids"]
    assert package["lineage"]["source_id"] == package["source_id"]
    assert package["lineage"]["parsed_source_id"] == package["parsed_source_id"]
    assert package["source_context_status"] == "scoped_context"
    assert package["claim_support_status"] == "unsupported_for_this_claim"
    assert "precise hard facts not present in the fetched content" in package["cannot_support"]
    assert rendered["evidence_lineage"]["proposition_ids"] == package["proposition_ids"]
    assert rendered["support_passage_ids"] == package["support_passage_ids"]
