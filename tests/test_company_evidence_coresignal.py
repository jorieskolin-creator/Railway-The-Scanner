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


def test_company_evidence_normalizes_coresignal_company_and_public_stakeholders(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    monkeypatch.setenv("CORESIGNAL_ENABLED", "true")
    monkeypatch.setenv("CORESIGNAL_MAX_STAKEHOLDERS", "4")
    monkeypatch.setattr(ce, "_discover_urls", lambda company_url, max_urls: [])

    def fake_coresignal_request(method, path, payload=None):
        if "company/search" in path:
            return [123]
        if "company/collect" in path:
            return {
                "id": 123,
                "name": "Sisu Auto",
                "website": "https://sisuauto.com",
                "industries": ["manufacturing"],
                "technologies": ["Azure", "SAP", "automation"],
                "email": "do-not-store@example.com",
            }
        if "employee/search" in path:
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
