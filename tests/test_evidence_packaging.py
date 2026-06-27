import os
import sys


BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from evidence_contracts import JURY_EVIDENCE_PACKAGE
from evidence_packaging import (
    SERVICE_JURY,
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
