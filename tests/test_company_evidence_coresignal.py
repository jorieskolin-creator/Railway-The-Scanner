import importlib
import json
import sys


def load_company_evidence(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("company_evidence", None)
    return importlib.import_module("company_evidence")


def test_company_evidence_skips_coresignal_when_key_missing(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.delenv("CORESIGNAL_API_KEY", raising=False)
    monkeypatch.setattr(ce, "_discover_urls", lambda company_url, max_urls: [])

    database, _ = ce.build_company_evidence_database({
        "companyName": "Sisu Auto",
        "companyUrl": "https://sisuauto.com",
    })

    assert database["providers"]["coresignal_enabled"] is False
    assert database["acquisition_policy"]["coresignal_enabled"] is False
    assert database["stats"]["coresignal_records"] == 0
    assert any("CORESIGNAL_API_KEY" in warning for warning in database["warnings"])


def test_coresignal_company_search_uses_entity_first_query_and_confirms_domain(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    calls = []

    def fake_request(method, path, *, payload=None, errors=None):
        calls.append((method, path, payload))
        if method == "POST":
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "company-1",
                            "_source": {
                                "company_name": "Sisu Auto",
                                "website_domain": "sisuauto.com",
                                "technologies_used": [{"technology": "Salesforce"}],
                            },
                        }
                    ]
                }
            }
        return {
            "id": "company-1",
            "company_name": "Sisu Auto",
            "website_domain": "sisuauto.com",
            "technologies_used": [{"technology": "Salesforce"}],
        }

    monkeypatch.setattr(ce, "_coresignal_request", fake_request)

    records, ids, diagnostics, warnings = ce._coresignal_company_search(
        "Sisu Auto",
        "https://sisuauto.com",
        2,
    )

    assert warnings == []
    assert ids == ["company-1"]
    assert calls[0][1] == "/cdapi/v2/company_multi_source/search/es_dsl"
    assert diagnostics["company_search_mode"] == "entity_domain_query"
    assert records[0]["website_domain"] == "sisuauto.com"
    assert calls[0][2]["query"]["bool"]["minimum_should_match"] == 1
    assert {"term": {"website_domain": "sisuauto.com"}} in calls[0][2]["query"]["bool"]["should"]
    assert {"match_phrase": {"company_name": "Sisu Auto"}} in calls[0][2]["query"]["bool"]["should"]

    normalized = ce._coresignal_company_record(1, "Sisu Auto", "https://sisuauto.com", records[0], "finland")
    assert normalized["entity_match_status"] == "exact"
    assert normalized["buying_evidence_eligible"] is True
    assert normalized["coresignal_fields"]["technologies"] == ["Salesforce"]


def test_coresignal_entity_decision_blocks_same_name_wrong_domain(monkeypatch):
    ce = load_company_evidence(monkeypatch)

    decision = ce._coresignal_entity_decision(
        "Rebase Consulting",
        "https://rebase.fi",
        {"name": "Rebase Consulting", "domain": "rebaseconsultancy.com", "country": "Finland"},
        "finland",
    )

    assert decision["confirmed"] is False
    assert decision["status"] == "wrong"
    assert "record_domain_conflicts" in decision["reason"]


def test_company_evidence_normalizes_coresignal_company_and_public_stakeholders(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    monkeypatch.setenv("CORESIGNAL_ENABLED", "true")
    monkeypatch.setenv("CORESIGNAL_MAX_STAKEHOLDERS", "4")
    monkeypatch.setattr(ce, "_discover_urls", lambda company_url, max_urls: [])

    def fake_coresignal_request(method, path, *, payload=None, errors=None):
        if "company" in path and "search" in path:
            return [123]
        if "company" in path and "collect" in path:
            return {
                "id": 123,
                "name": "Sisu Auto",
                "website": "https://sisuauto.com",
                "industries": ["manufacturing"],
                "technologies_used": [{"technology": "Azure"}, {"technology": "SAP"}, {"technology": "automation"}],
                "email": "do-not-store@example.com",
            }
        if "employee" in path and "search" in path:
            return [1, 2]
        if path.endswith("/1"):
            return {
                "id": 1,
                "name": "Jane Example",
                "title": "Chief Information Officer",
                "linkedin_url": "https://www.linkedin.com/in/jane-example",
                "email": "jane@example.com",
                "phone": "+358000000",
            }
        if path.endswith("/2"):
            return {
                "id": 2,
                "name": "Unmatched Person",
                "title": "Junior Designer",
                "linkedin_url": "https://www.linkedin.com/in/unmatched",
                "email": "unmatched@example.com",
            }
        return None

    monkeypatch.setattr(ce, "_coresignal_request", fake_coresignal_request)

    database, _ = ce.build_company_evidence_database({
        "companyName": "Sisu Auto",
        "companyUrl": "https://sisuauto.com",
        "summary_text": "The company has AI, cloud, SAP, data, automation and platform pressure.",
        "why_proposals": [{"title": "Modernize data platform"}],
    })

    stats = database["stats"]
    assert database["providers"]["coresignal_enabled"] is True
    assert stats["coresignal_company_records"] == 1
    assert stats["coresignal_technology_records"] == 1
    assert stats["coresignal_stakeholder_candidates"] == 1
    assert stats["coresignal_sales_usable"] >= 2

    stakeholder = next(record for record in database["records"] if record.get("provider_record_type") == "stakeholder_candidate")
    assert stakeholder["stakeholder"]["name"] == "Jane Example"
    assert stakeholder["sales_use"] == "allowed_as_discovery_question"
    assert stakeholder["generation_eligible"] is False
    serialized = json.dumps(stakeholder)
    assert "jane@example.com" not in serialized
    assert "+358" not in serialized


def test_buying_payload_includes_coresignal_stakeholder_candidates(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("buying_potential", None)
    bp = importlib.import_module("buying_potential")

    payload = bp._build_input_payload(
        {
            "companyName": "Sisu Auto",
            "companyUrl": "https://sisuauto.com",
            "key_people": [],
        },
        {"services": [{"name": "AI Readiness", "buyer_personas": ["CIO"]}]},
        company_evidence={
            "records": [
                {
                    "id": "CED-03",
                    "provider": "coresignal",
                    "provider_record_type": "stakeholder_candidate",
                    "source_category": "stakeholder_candidate",
                    "source_quote": "Coresignal public professional profile candidate: Jane Example, CIO.",
                    "url": "https://www.linkedin.com/in/jane-example",
                    "sales_use": "allowed_as_discovery_question",
                    "stakeholder": {
                        "name": "Jane Example",
                        "role": "CIO",
                        "public_profile_url": "https://www.linkedin.com/in/jane-example",
                        "allowed_use": "allowed_as_discovery_question",
                        "matched_demand_groups": ["ai_data_cloud_platform"],
                    },
                }
            ],
            "stats": {"coresignal_stakeholder_candidates": 1},
            "provider_status": {"coresignal": {"enabled": True, "queried": True}},
        },
    )

    assert payload["stakeholders"][0]["name"] == "Jane Example"
    assert payload["stakeholders"][0]["provider"] == "coresignal"
    assert payload["stakeholders"][0]["allowed_use"] == "allowed_as_discovery_question"
    assert payload["company_evidence_database"]["provider_status"]["coresignal"]["queried"] is True


def test_coresignal_search_success_uses_search_records_when_collect_fails(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")

    def fake_request(method, path, *, payload=None, errors=None):
        if method == "POST":
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "company-1",
                            "_source": {
                                "company_name": "Sisu Auto",
                                "website_domain": "sisuauto.com",
                            },
                        }
                    ]
                }
            }
        if errors is not None:
            errors.append({"method": method, "path": path, "status_code": 404})
        return None

    monkeypatch.setattr(ce, "_coresignal_request", fake_request)

    records, ids, diagnostics, warnings = ce._coresignal_company_search(
        "Sisu Auto",
        "https://sisuauto.com",
        2,
    )

    assert ids == ["company-1"]
    assert records[0]["website_domain"] == "sisuauto.com"
    assert diagnostics["company_records_confirmed"] == 1
    assert any("using search result records" in warning for warning in warnings)
