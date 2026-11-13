"""Stakeholder regressions from the earlier hierarchy and Veikkaus investigation."""
import importlib
import pytest

from stakeholder_policy import (
    demand_profile, seniority_tier, role_context, rank_stakeholders, search_rounds,
)

PROFILE = {"groups": ["ai_data_cloud_platform", "strategy_growth_customer", "finance_risk_regulatory"]}


def person(name, role, **extra):
    return {"name": name, "role": role, "current_affiliation_verified": True,
            "allowed_use": "allowed_as_discovery_question", "source": "https://example.fi/leadership",
            **extra}


@pytest.mark.parametrize("title,tier", [
    ("CEO", 1), ("Chief Operating Officer", 1), ("Managing Director", 1),
    ("Chair Of The Board Of Directors", 1), ("Board member, ASC Chair", 3), ("Group Chief Financial Officer", 2),
    ("Executive Vice President", 2), ("Chief Information Security Officer", 2),
    ("Senior Vice President, Digital", 3), ("Vice President, Product", 3),
    ("IT Director", 4), ("Head of Product", 5), ("IT Manager", 6),
    ("Trainer", 7), ("Inspector", 7), ("Assistant to the CEO", 7),
])
def test_historical_hierarchy_and_president_regression(title, tier):
    assert seniority_tier(title) == tier


@pytest.mark.parametrize("title", ["Trainer", "Inspector", "Potinhoitaja", "kirjanpitovastaava", "Airport Director Ret.", "Former CFO", "CIO (retired)", "Marketing Specialist", "HR Director"])
def test_non_stakeholders_are_not_accepted_from_substrings_or_unrelated_functions(title):
    profile = {"groups": ["ai_data_cloud_platform"]}
    assert role_context(title, profile)["matched"] is False


def test_demand_detection_does_not_treat_airline_or_training_as_ai():
    assert "ai_data_cloud_platform" not in demand_profile({"summary_text": "airline training"})["groups"]
    assert "ai_data_cloud_platform" in demand_profile({"summary_text": "AI readiness and digital product"})["groups"]


def test_sort_by_seniority_then_demand_relevance_before_limiting():
    pool = [person("Head Product", "Head of Product"), person("Digital Leader", "VP Digital Channels"),
            person("Finance Leader", "Group CFO"), person("Chief Executive", "CEO"),
            person("Finance Head", "Head of Finance"), person("Retired Person", "CFO Ret.")]
    ranked, stats = rank_stakeholders(pool, PROFILE, limit=4)
    assert [p["name"] for p in ranked] == ["Chief Executive", "Finance Leader", "Digital Leader", "Head Product"]
    assert stats["candidates_retained"] == 5
    assert stats["display_limit_excluded"] == 1
    assert stats["rejected_by_reason"] == {"historical_or_retired_role": 1}


@pytest.mark.parametrize("size,senior_count,kept", [(100, 3, True), (200, 2, True), (200, 3, False), (5000, 0, False)])
def test_legacy_company_size_manager_restrictions(size, senior_count, kept):
    pool = [person(f"Executive Person{i}", "CFO") for i in range(senior_count)] + [person("IT Owner", "IT Manager")]
    ranked, _ = rank_stakeholders(pool, PROFILE, employees=size)
    assert any(p["name"] == "IT Owner" for p in ranked) is kept


def test_stale_title_conflicts_are_resolved_across_sources():
    pool = [person("Retired Person", "Audit Manager"), person("Retired Person", "Director Ret."),
            person("Departed Person", "CFO"), person("Departed Person", "CFO", current_affiliation_verified=False),
            person("Verified Person", "CTO"), person("Verified Person", "CTO Ret."),
            person("Verified Person", "CIO", affiliation_basis="active_company_id")]
    ranked, stats = rank_stakeholders(pool, PROFILE)
    assert [(p["name"], p["role"]) for p in ranked] == [("Verified Person", "CIO")]
    assert stats["rejected_by_reason"]["conflicting_current_affiliation"] == 3


def test_profile_url_deduplicates_name_variations():
    pool = [person("First Last", "CFO", source="https://www.linkedin.com/in/same/"),
            person("First Middle Last", "CIO", source="https://linkedin.com/in/same", affiliation_basis="active_company_id")]
    ranked, _ = rank_stakeholders(pool, PROFILE)
    assert len(ranked) == 1
    assert ranked[0]["role"] == "CIO"


def test_company_and_role_filters_are_required_in_each_round(monkeypatch):
    ce = importlib.import_module("company_evidence")
    monkeypatch.setenv("CORESIGNAL_API_KEY", "test")
    monkeypatch.setenv("CORESIGNAL_ENABLED", "true")
    monkeypatch.setenv("CORESIGNAL_MAX_STAKEHOLDERS", "3")
    requests = []
    responses = iter([[1, 2, 3, 4], [4, 5], [5]])
    rows = {
        1: {"full_name": "Unrelated Trainer", "active_experience_title": "Trainer"},
        2: {"full_name": "Departed Leader", "active_experience_title": "CEO", "active_experience_company_id": 999},
        3: {"full_name": "Finance Leader", "active_experience_title": "Group CFO"},
        4: {"full_name": "Digital Leader", "active_experience_title": "VP Digital Channels"},
        5: {"full_name": "Product Leader", "active_experience_title": "Head of Product"},
    }
    def request(method, path, *, payload=None, errors=None):
        if "company_multi_source/enrich" in path:
            return {"id": 123, "name": "Example Oy", "website": "https://example.fi", "employees_count": 2000,
                    "key_executives": [{"name": "Current Executive", "title": "CEO"}]}
        if method == "POST":
            requests.append(payload)
            assert payload["query"]["bool"]["filter"] == [{"term": {"active_experience_company_id": 123}}]
            assert payload["query"]["bool"]["must"]
            return next(responses)
        pid = int(path.split("?")[0].rsplit("/", 1)[1])
        return {"id": pid, "active_experience_company_id": 123, **rows[pid]}
    monkeypatch.setattr(ce, "_coresignal_request", request)
    records, diagnostics, _ = ce._coresignal_records({"companyName": "Example Oy", "companyUrl": "https://example.fi", "summary_text": "Digital product growth"})
    assert len(requests) == 3  # A usable roster CEO must not suppress domain searches.
    assert [r["stakeholder"]["name"] for r in records if r.get("stakeholder")] == ["Current Executive", "Finance Leader", "Digital Leader"]
    assert diagnostics["executives_received"] == 1
    assert diagnostics["employee_ids_returned"] == 7
    assert diagnostics["profiles_collected"] == 5
    assert diagnostics["candidates_retained"] == 4
    assert diagnostics["candidates_selected"] == 3
    assert diagnostics["rejected_by_reason"] == {"unrecognized_or_non_leadership_role": 1, "current_affiliation_unverified": 1}
    assert requests[0]["query"]["bool"]["must"][0]["bool"]["minimum_should_match"] == 1
    assert all(clause["bool"]["minimum_should_match"] == 1 for clause in requests[1]["query"]["bool"]["must"][0]["bool"]["must"])
    bp = importlib.import_module("buying_potential")
    selected, _ = bp._ranked_stakeholder_pool({"summary_text": "Digital product growth"}, {"records": records, "provider_status": {"coresignal": diagnostics}})
    assert [p["name"] for p in selected] == ["Current Executive", "Finance Leader", "Digital Leader"]


def test_collection_budget_is_bounded_and_reserved_for_domain_rounds(monkeypatch):
    ce = importlib.import_module("company_evidence")
    collected = []
    searches = []
    def request(method, path, *, payload=None, errors=None):
        if method == "POST":
            searches.append(payload)
            return list(range(100 * len(searches), 100 * len(searches) + 100))
        collected.append(path)
        return {"full_name": "Test Leader", "active_experience_company_id": 123, "active_experience_title": "CEO"}
    monkeypatch.setattr(ce, "_coresignal_request", request)
    _, stats, _ = ce._coresignal_employee_search(123, "Example", "https://example.fi", PROFILE, 1, 1)
    assert len(searches) == 4
    assert len(collected) == stats["collection_budget"] == 24
    assert all(r["profiles_collected"] > 0 for r in stats["search_rounds"])
    assert all(r["uncollected_ids"] > 0 for r in stats["search_rounds"])


def test_provider_credit_error_is_logged_and_stops_collection(monkeypatch):
    ce = importlib.import_module("company_evidence")
    calls = []
    def request(method, path, *, payload=None, errors=None):
        calls.append(method)
        if method == "POST":
            return [1, 2]
        errors.append({"method": method, "status_code": 402})
    monkeypatch.setattr(ce, "_coresignal_request", request)
    candidates, stats, warnings = ce._coresignal_employee_search(123, "Example", "https://example.fi", PROFILE, 8, 1)
    assert candidates == []
    assert calls == ["POST", "GET"]
    assert stats["stopped_on_provider_error"] is True
    assert "402" in warnings[0]


def test_both_report_sections_use_verified_ranked_pool(monkeypatch, capsys):
    bp = importlib.import_module("buying_potential")
    monkeypatch.setattr(bp, "_load_modernization_tactics", lambda *a, **k: [])
    monkeypatch.setattr(bp, "_recover_required_spine_sections", lambda *a, **k: None)
    pool = [person("Product Leader", "Head of Product"), person("Finance Leader", "CFO"),
            person("Training Person", "Trainer"), person("Retired Person", "CFO Ret."),
            person("Unknown Current", "CTO", current_affiliation_verified=None)]
    db = {"records": [{"stakeholder": p, "sales_use": p["allowed_use"], "provider_record_type": "stakeholder_candidate"} for p in pool]}
    company = {"companyName": "Example", "summary_text": "AI product", "employees": 2000,
               "key_people": [person("Retired Person", "CFO")]}
    payload = bp._build_input_payload(company, {"services": []}, db)
    payload["account_signal_map"] = [{"id": "SIG-01", "buyer_roles": ["CIO"], "commercial_pressure": "AI readiness"}]
    report = {"stakeholder_map": [{"name": "Invented Person", "role": "CEO"}],
              "additional_stakeholder_names": [{"name": "Retired Person", "position": "CFO"}]}
    result = bp._post_process_report(report, payload)
    names = [p["name"] for p in result["stakeholder_map"] if p["name"] != "Role-level target"]
    assert names == ["Finance Leader", "Product Leader"]
    assert [p["name"] for p in result["additional_stakeholder_names"]] == names
    assert any(p["name"] == "Role-level target" and p["role"] == "CIO" for p in result["stakeholder_map"])
    assert result["stakeholder_selection"]["named_stakeholders_displayed"] == 2
    assert "[Buying Insight stakeholders]" in capsys.readouterr().out
    rendered = bp.render_buying_potential_html(result)
    assert "Finance Leader" in rendered and "Product Leader" in rendered
    assert "Retired Person" not in rendered and "Invented Person" not in rendered and "Training Person" not in rendered


def test_new_database_does_not_reintroduce_unselected_raw_executives():
    bp = importlib.import_module("buying_potential")
    db = {"records": [{"entity_verified": True, "stakeholder_selection_applied": True,
                       "coresignal_fields": {"key_executives": [{"name": "Omitted Person", "title": "CFO"}]}}]}
    assert bp._ranked_stakeholder_pool({}, db)[0] == []


def test_saved_employee_affiliation_conflict_blocks_legacy_name():
    bp = importlib.import_module("buying_potential")
    company = {"key_people": [person("Departed Person", "CFO")]}
    db = {"provider_status": {"coresignal": {"stakeholder_conflicts": [person("Departed Person", "CFO", current_affiliation_verified=False)]}}}
    assert bp._ranked_stakeholder_pool(company, db)[0] == []
