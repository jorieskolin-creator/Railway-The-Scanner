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
    monkeypatch.setenv("COMPANY_EVIDENCE_PRH_ENABLED", "false")
    monkeypatch.setattr(ce, "_discover_urls", lambda company_url, max_urls: [])

    database, _ = ce.build_company_evidence_database({
        "companyName": "Sisu Auto",
        "companyUrl": "https://sisuauto.com",
    })

    assert database["providers"]["coresignal_enabled"] is False
    assert database["acquisition_policy"]["coresignal_enabled"] is False
    assert database["stats"]["coresignal_records"] == 0
    assert any("CORESIGNAL_API_KEY" in warning for warning in database["warnings"])


def test_coresignal_company_search_uses_domain_enrich_first_and_confirms_domain(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    calls = []

    def fake_request(method, path, *, payload=None, errors=None):
        calls.append((method, path, payload))
        return {
            "type": "Success",
            "data": {
                "id": 4288299,
                "company_name": "Oy Sisu Auto Ab",
                "company_legal_name": "Oy Sisu Auto Ab",
                "website": "https://www.sisuauto.com",
                "website_domain": "sisuauto.com",
                "hq_country": "Finland",
                "industry": "Motor Vehicle Manufacturing",
                "size_range": "51-200 employees",
                "employees_count": 61,
                "technologies_used": [{"technology": "Salesforce"}],
                "company_updates": [
                    {
                        "date": "2026-05-25",
                        "description": "Leading Finnish defence and research organisations signed a Letter of Intent to establish C4IMobility at Oy Sisu Auto Ab factory.",
                    }
                ],
            },
        }

    monkeypatch.setattr(ce, "_coresignal_request", fake_request)

    records, ids, diagnostics, warnings = ce._coresignal_company_search(
        "Sisu Auto",
        "https://sisuauto.com",
        2,
    )

    assert warnings == []
    assert ids == [4288299]
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/cdapi/v2/company_multi_source/enrich?website=sisuauto.com"
    assert diagnostics["company_search_mode"] == "domain_enrich"
    assert diagnostics["company_enrich_queried"] is True
    assert records[0]["website_domain"] == "sisuauto.com"

    normalized = ce._coresignal_company_record(1, "Sisu Auto", "https://sisuauto.com", records[0], "finland")
    assert normalized["entity_match_status"] == "exact"
    assert normalized["buying_evidence_eligible"] is True
    assert normalized["coresignal_fields"]["legal_name"] == "Oy Sisu Auto Ab"
    assert normalized["coresignal_fields"]["employees_count"] == 61
    assert normalized["coresignal_fields"]["technologies"] == ["Salesforce"]
    update_records = ce._coresignal_update_records(2, "Sisu Auto", "https://sisuauto.com", normalized)
    assert update_records[0]["provider_record_type"] == "company_update_trigger"
    assert update_records[0]["allowed_as_hypothesis"] is True


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


def test_coresignal_search_uses_legal_aliases_and_confirms_collected_legal_name(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    calls = []

    def fake_request(method, path, *, payload=None, errors=None):
        calls.append((method, path, payload))
        if method == "GET" and "enrich" in path:
            return None
        if method == "POST":
            payload_text = json.dumps(payload)
            return [321] if "Finavia Oyj" in payload_text else []
        return {
            "id": 321,
            "company_name": "Airport operator",
            "company_legal_name": "Finavia Oyj",
            "hq_country": "Finland",
            "employees_count": 2500,
        }

    monkeypatch.setattr(ce, "_coresignal_request", fake_request)

    records, ids, diagnostics, warnings = ce._coresignal_company_search(
        "Finavia",
        "https://finavia.fi",
        2,
        {"companyName": "Finavia", "companyUrl": "https://finavia.fi", "legal_name": "Finavia Oyj"},
    )

    assert warnings == []
    assert ids == [321]
    assert diagnostics["company_search_mode"] == "entity_alias_identifier_query"
    assert "Finavia Oyj" in diagnostics["identity_names_used"]
    assert records[0]["company_legal_name"] == "Finavia Oyj"
    post_calls = [call for call in calls if call[0] == "POST"]
    assert post_calls[0][2]["query"]["bool"]["must"] == [{"term": {"website_domain": "finavia.fi"}}]

    normalized = ce._coresignal_company_record(
        1,
        "Finavia",
        "https://finavia.fi",
        records[0],
        "finland",
        ce._company_identity_bundle({"legal_name": "Finavia Oyj"}, "Finavia", "https://finavia.fi"),
    )
    assert normalized["entity_verified"] is True
    assert normalized["entity_match_status"] == "scoped"


def test_coresignal_entity_decision_confirms_registration_identifier(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    identity = ce._company_identity_bundle(
        {"business_id": "2302570-2"},
        "Finavia",
        "https://finavia.fi",
    )

    decision = ce._coresignal_entity_decision(
        "Finavia",
        "https://finavia.fi",
        {
            "company_name": "Airport infrastructure record",
            "website_domain": "airport-example.com",
            "hq_country": "Finland",
            "business_id": "2302570-2",
        },
        "finland",
        identity,
    )

    assert decision["confirmed"] is True
    assert decision["status"] == "exact"
    assert decision["identifier_match"] is True
    assert "registration_identifier" in decision["reason"]


def test_company_evidence_normalizes_coresignal_company_and_public_stakeholders(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")
    monkeypatch.setenv("CORESIGNAL_ENABLED", "true")
    monkeypatch.setenv("CORESIGNAL_MAX_STAKEHOLDERS", "4")
    monkeypatch.setenv("COMPANY_EVIDENCE_PRH_ENABLED", "false")
    monkeypatch.setattr(ce, "_discover_urls", lambda company_url, max_urls: [])

    def fake_coresignal_request(method, path, *, payload=None, errors=None):
        assert "professional_network/employee" not in path
        if "company" in path and "search" in path:
            return [123]
        if "company" in path and "collect" in path:
            return {
                "id": 123,
                "name": "Sisu Auto",
                "website": "https://sisuauto.com",
                "industries": ["manufacturing"],
                "technologies_used": [{"technology": "Azure"}, {"technology": "SAP"}, {"technology": "automation"}],
                "key_executives": [
                    {
                        "parent_id": 1,
                        "member_full_name": "Jane Example",
                        "member_position_title": "Chief Information Officer",
                    },
                    {
                        "parent_id": 2,
                        "member_full_name": "Unmatched Person",
                        "member_position_title": "Junior Designer",
                    },
                ],
                "email": "do-not-store@example.com",
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
    assert "do-not-store@example.com" not in serialized


def test_coresignal_stakeholder_groups_are_title_specific(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    demand_profile = {
        "groups": [
            "ai_data_cloud_platform",
            "operations_manufacturing_automation",
            "finance_risk_regulatory",
            "hr_workforce_culture",
            "strategy_growth_customer",
        ],
        "roles": [
            "Head of Applications",
            "CFO",
            "HR Director",
            "Chief Executive Officer",
        ],
    }

    app_owner = ce._coresignal_stakeholder_record(
        1,
        "Finavia",
        "https://www.finavia.fi/fi",
        {"name": "Nina Korpela", "title": "Head Of Application Services"},
        demand_profile,
    )
    assert app_owner["stakeholder_role_match_reason"] == "role_matches_title_specific_demand"
    assert app_owner["demand_signal_ids"] == ["ai_data_cloud_platform"]
    assert "hr_workforce_culture" not in app_owner["demand_signal_ids"]

    finance_owner = ce._coresignal_stakeholder_record(
        2,
        "Finavia",
        "https://www.finavia.fi/fi",
        {"name": "Finance Owner", "title": "Chief Financial Officer"},
        demand_profile,
    )
    assert finance_owner["demand_signal_ids"] == ["finance_risk_regulatory"]


def test_coresignal_executive_generalist_and_retired_roles(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    demand_profile = {
        "groups": ["ai_data_cloud_platform", "finance_risk_regulatory", "strategy_growth_customer"],
        "roles": ["CEO", "CFO", "IT Director"],
    }

    chair = ce._coresignal_stakeholder_record(
        1,
        "Finavia",
        "https://www.finavia.fi/fi",
        {"name": "Kati Levoranta", "title": "Chair Of The Board Of Directors"},
        demand_profile,
    )
    assert chair["stakeholder_role_match_reason"] == "executive_generalist"
    assert chair["demand_signal_ids"] == ["strategy_growth_customer", "finance_risk_regulatory", "ai_data_cloud_platform"]
    assert chair["sales_use"] == "allowed_as_discovery_question"

    retired = ce._coresignal_stakeholder_record(
        2,
        "Finavia",
        "https://www.finavia.fi/fi",
        {"name": "Raimo Seppänen", "title": "Airport Director Ret."},
        demand_profile,
    )
    assert retired["stakeholder_role_match_reason"] == "historical_or_retired_role"
    assert retired["sales_use"] == "internal_only"
    assert retired["internal_only"] is True
    assert retired["buying_evidence_eligible"] is False
    assert retired["demand_signal_ids"] == []


def test_company_evidence_warnings_distinguish_coresignal_trigger_updates(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    records = [
        {
            "source_category": "strategy_trigger_event",
            "provider_record_type": "company_update_trigger",
            "provider": "coresignal",
            "sales_use": "allowed_as_hypothesis",
            "buying_evidence_eligible": True,
            "content_verified": True,
            "url_verified": True,
            "entity_verified": True,
        }
    ]
    stats = ce._stats(records)
    warnings = ce._warnings(records, stats)
    assert "No official-domain strategy/newsroom trigger sources found; Coresignal updates are available as weak trigger signals." in warnings
    assert "No recent strategy/newsroom trigger sources found." not in warnings


def test_short_prh_aliases_are_kept_out_of_coresignal_fallback(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    accepted, skipped = ce._prh_filtered_aliases("Finavia", ["Finavia Group", "Finavia", "AVIA"])
    assert "Finavia Group" in accepted
    assert "Finavia" in accepted
    assert "AVIA" not in accepted
    assert {"name": "AVIA", "reason": "alias_too_short_for_fallback_search"} in skipped


def test_soft_oops_pages_are_blocked_not_fact_safe(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    fetched = {
        "provider": "firecrawl",
        "status_code": 200,
        "title": "Oops! Finavia",
        "text": "Palaa etusivulle Lentoasemat Tervetuloa Finavian lentoasemille",
    }
    fetched["page_not_found"] = ce._is_not_found_content(fetched["title"], fetched["text"], fetched["status_code"])
    fetched["url_verified"] = not fetched["page_not_found"]
    fetched["content_verified"] = len(fetched["text"]) >= 20 and not fetched["page_not_found"]

    verified = ce._verify_record("Finavia", "https://www.finavia.fi/fi", "https://www.finavia.fi/fi/about", fetched)

    assert verified["page_not_found"] is True
    assert verified["buying_evidence_eligible"] is False
    assert verified["allowed_as_fact"] is False
    assert verified["sales_use"] == "blocked"


def test_short_ai_keyword_does_not_match_airline_names(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    profile = ce._source_profile("https://www.finavia.fi/fi/lentoyhti%C3%B6t/air-france-af", "Air France (AF) | Finavia")

    assert profile["source_type"] != "services"
    assert ce._any_keyword("Air France and airBaltic airline page", ce.TECH_SIGNAL_KEYWORDS) is False
    assert ce._any_keyword("AI readiness and cloud platform", ce.TECH_SIGNAL_KEYWORDS) is True


def test_prh_identity_is_merged_into_coresignal_identity_bundle(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    enrichment = {
        "matched": True,
        "business_id": "2302570-2",
        "business_id_normalized": "23025702",
        "legal_name": "Finavia Oyj",
        "names": ["Finavia Oyj"],
    }

    merged = ce._merge_identity_enrichment({"companyName": "Finavia", "companyUrl": "https://finavia.fi"}, enrichment)
    identity = ce._company_identity_bundle(merged, "Finavia", "https://finavia.fi")

    assert "2302570-2" in identity["identifiers"]
    assert "23025702" in identity["identifiers"]
    assert "Finavia Oyj" in identity["names"]
    assert "finavia.fi" in identity["domains"]


def test_prh_identity_parses_business_id_and_filters_noisy_aliases(monkeypatch):
    ce = load_company_evidence(monkeypatch)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "companies": [
                    {
                        "businessId": {"value": "0246812-5", "registrationDate": "1994-07-13"},
                        "registrationDate": "1994-07-13",
                        "names": [
                            {"name": "Ilmailulaitos"},
                            {"name": "Finnish Airports System FAS"},
                            {"name": "Avia College"},
                            {"name": "CA GROUP"},
                            {"name": "Finavia Group"},
                            {"name": "Finavia"},
                        ],
                    }
                ]
            }

    monkeypatch.setenv("COMPANY_EVIDENCE_PRH_ENABLED", "true")
    monkeypatch.setattr(ce.requests, "get", lambda *args, **kwargs: FakeResponse())

    enrichment = ce._fetch_prh_identity("Finavia", "https://finavia.fi")

    assert enrichment["matched"] is True
    assert enrichment["business_id"] == "0246812-5"
    assert enrichment["business_id_normalized"] == "02468125"
    assert "Finavia" in enrichment["names"]
    assert "Finavia Group" in enrichment["names"]
    assert "CA GROUP" not in enrichment["names"]
    assert "Avia College" not in enrichment["names"]
    assert any(item["name"] == "CA GROUP" for item in enrichment["skipped_names"])

    merged = ce._merge_identity_enrichment({"companyName": "Finavia", "companyUrl": "https://finavia.fi"}, enrichment)
    identity = ce._company_identity_bundle(merged, "Finavia", "https://finavia.fi")

    assert "0246812-5" in identity["identifiers"]
    assert "02468125" in identity["identifiers"]
    assert "CA GROUP" not in identity["names"]
    assert "Avia College" not in identity["names"]
    assert {"name": "CA GROUP", "reason": "alias_not_specific_to_scanned_company"} in identity["prh_aliases_skipped"]


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
        if method == "GET" and "enrich" in path:
            return None
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


def test_coresignal_422_is_classified_as_payload_shape_error(monkeypatch):
    ce = load_company_evidence(monkeypatch)
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test-key")

    def fake_request(method, path, *, payload=None, errors=None):
        if errors is not None:
            errors.append({
                "method": method,
                "path": path,
                "status_code": 422,
                "error_type": "payload_shape_error",
                "response_excerpt": "failed to parse query",
            })
        return None

    monkeypatch.setattr(ce, "_coresignal_request", fake_request)

    records, ids, diagnostics, warnings = ce._coresignal_company_search(
        "Sisu Auto",
        "https://sisuauto.com",
        2,
    )

    assert records == []
    assert ids == []
    assert diagnostics["company_search_error_type"] == "payload_shape_error"
    assert any("422 payload_shape_error" in warning for warning in warnings)
