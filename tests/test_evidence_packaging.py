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
    assert "semantic_snippet_for_routing_only" in render_packages_for_prompt(packages)[0]


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
