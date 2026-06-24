import importlib
import sys


def load_company_evidence(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("company_evidence", None)
    return importlib.import_module("company_evidence")


def test_outbound_regulator_reference_can_be_independent_candidate(monkeypatch):
    ce = load_company_evidence(monkeypatch)

    def fake_fetch(url):
        return {
            "provider": "direct",
            "title": "EU report mentions Vivicta",
            "text": "The European public report discusses Vivicta and its digital service obligations in Finland.",
            "url_verified": True,
            "content_verified": True,
            "page_not_found": False,
        }

    monkeypatch.setattr(ce, "_fetch_source", fake_fetch)
    record = ce._outbound_reference_record(
        7,
        "Vivicta",
        "https://www.vivicta.com",
        "https://www.vivicta.com/en/about-us",
        {
            "external_url": "https://ec.europa.eu/example/report",
            "anchor_text": "EU report",
            "reference_context": "External EU report",
        },
    )

    assert record["origin_relation"] == "official_page_outbound_reference"
    assert record["source_category"] == "independent_external_evidence"
    assert record["publisher_independence"] == "independent"
    assert record["allowed_as_fact"] is True
    assert record["buying_evidence_eligible"] is True


def test_outbound_pr_wire_is_company_distributed_not_independent(monkeypatch):
    ce = load_company_evidence(monkeypatch)

    def fake_fetch(url):
        return {
            "provider": "jina",
            "title": "Vivicta press release",
            "text": "Press release: Vivicta announces a new service portfolio for customers.",
            "url_verified": True,
            "content_verified": True,
            "page_not_found": False,
        }

    monkeypatch.setattr(ce, "_fetch_source", fake_fetch)
    record = ce._outbound_reference_record(
        8,
        "Vivicta",
        "https://www.vivicta.com",
        "https://www.vivicta.com/en/news",
        {
            "external_url": "https://news.cision.com/vivicta/r/vivicta-launches,c123",
            "anchor_text": "Press release",
            "reference_context": "Vivicta announcement on Cision",
        },
    )

    assert record["source_category"] == "company_distributed_material"
    assert record["publisher_independence"] == "company_distributed"
    assert record["allowed_as_fact"] is False
    assert record["allowed_as_hypothesis"] is True
    assert "not independent proof" in record["company_claim_vs_external_evidence_boundary"]


def test_outbound_social_and_assets_are_excluded(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    official_host = "vivicta.com"

    assert ce._is_outbound_reference_candidate("https://linkedin.com/company/vivicta", official_host) is False
    assert ce._is_outbound_reference_candidate("https://cdn.example.com/logo.svg", official_host) is False
    assert ce._is_outbound_reference_candidate("https://ec.europa.eu/example/report", official_host) is True


def test_outbound_discovery_reads_official_page_links(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("COMPANY_EVIDENCE_MAX_OUTBOUND_REFERENCES", "2")

    monkeypatch.setattr(
        ce,
        "_official_page_outbound_material",
        lambda url: {
            "text": "[EU report](https://ec.europa.eu/example/report)",
            "html": "",
            "providers": ["firecrawl"],
        },
    )
    monkeypatch.setattr(
        ce,
        "_fetch_source",
        lambda url: {
            "provider": "direct",
            "title": "EU report mentions Vivicta",
            "text": "The report mentions Vivicta and digital public-service obligations.",
            "url_verified": True,
            "content_verified": True,
            "page_not_found": False,
        },
    )

    records, diagnostics = ce._discover_outbound_references(
        [{
            "url": "https://www.vivicta.com/en/about-us",
            "url_verified": True,
            "source_type": "about",
        }],
        "Vivicta",
        "https://www.vivicta.com",
        start_idx=2,
    )

    assert diagnostics["official_pages_checked"] == 1
    assert diagnostics["outbound_candidates_found"] == 1
    assert diagnostics["records_added"] == 1
    assert records[0]["external_host"] == "ec.europa.eu"
