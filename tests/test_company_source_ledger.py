import importlib
import sys
import types


def load_ledger(monkeypatch):
    source_quality = types.ModuleType("source_quality")
    source_quality.is_generation_eligible_source = lambda source: (
        source.get("generation_eligible") is True
        and source.get("analysis_verified") is True
        and source.get("promotion_status") == "generation_eligible"
        and source.get("claim_support_status") == "supported"
        and source.get("wrong_entity") is not True
    )
    monkeypatch.setitem(sys.modules, "source_quality", source_quality)
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("company_source_ledger", None)
    return importlib.import_module("company_source_ledger")


def test_company_source_ledger_dedupes_urls_and_preserves_origins(monkeypatch):
    ledger_mod = load_ledger(monkeypatch)
    company = {
        "companyName": "TestCo",
        "companyUrl": "https://test.example",
        "evidence_links": [
            {
                "title": "Official strategy",
                "url": "https://test.example/news/strategy",
                "source_quote": "TestCo announced its strategy.",
                "analysis_verified": True,
                "generation_eligible": True,
                "promotion_status": "generation_eligible",
                "claim_support_status": "supported",
                "entity_match_status": "exact",
            }
        ],
    }
    company_evidence = {
        "records": [
            {
                "title": "Same official strategy",
                "url": "https://test.example/news/strategy?utm_source=x",
                "source_quote": "TestCo announced its strategy and investment priorities.",
                "buying_evidence_eligible": True,
                "allowed_as_fact": True,
                "analysis_verified": True,
                "generation_eligible": True,
                "promotion_status": "generation_eligible",
                "claim_support_status": "supported",
                "entity_match_status": "exact",
            }
        ]
    }

    ledger, _ = ledger_mod.build_company_source_ledger(company, company_evidence=company_evidence)

    assert ledger["stats"]["records"] == 1
    assert ledger["stats"]["deduplicated"] == 1
    assert ledger["records"][0]["origins"] == ["scanner", "company_evidence_db"]
    assert ledger["records"][0]["usable_as_scanner_seed"] is True
    assert ledger["records"][0]["buying_evidence_eligible"] is True


def test_wrong_entity_and_foresight_context_do_not_become_scanner_seeds(monkeypatch):
    ledger_mod = load_ledger(monkeypatch)
    company = {
        "companyName": "Rebase Consulting",
        "companyUrl": "https://rebase.fi",
        "evidence_links": [
            {
                "title": "Wrong Rebase",
                "url": "https://rebaseconsultancy.com/about",
                "source_quote": "Rebase Consultancy is led by another team.",
                "analysis_verified": True,
                "generation_eligible": True,
                "promotion_status": "generation_eligible",
                "claim_support_status": "supported",
                "entity_match_status": "wrong",
                "wrong_entity": True,
            }
        ],
        "foresight_links": [
            {
                "title": "AI trend",
                "url": "https://example.org/ai-trend",
                "source_quote": "AI adoption is rising in services.",
                "analysis_verified": True,
                "generation_eligible": True,
                "promotion_status": "generation_eligible",
                "claim_support_status": "supported",
                "source_type": "foresight",
            }
        ],
    }

    ledger, _ = ledger_mod.build_company_source_ledger(company)

    assert ledger["stats"]["records"] == 2
    assert ledger["stats"]["wrong_entity"] == 1
    assert ledger["stats"]["context_only"] == 1
    assert ledger["stats"]["scanner_seed_eligible"] == 0
    assert ledger_mod.ledger_scanner_seed_links(ledger) == []


def test_financial_source_can_be_retained_as_scanner_seed(monkeypatch):
    ledger_mod = load_ledger(monkeypatch)
    company = {
        "companyName": "FinanceCo",
        "companyUrl": "https://finance.example",
        "financial_findings": [
            {
                "source_title": "FinanceCo financials",
                "source_url": "https://registry.example/financeco",
                "evidence_snippet": "FinanceCo revenue was 10 million euros.",
                "analysis_verified": True,
                "generation_eligible": True,
                "promotion_status": "generation_eligible",
                "claim_support_status": "supported",
                "entity_match_status": "scoped",
            }
        ],
    }

    ledger, _ = ledger_mod.build_company_source_ledger(company)
    seeds = ledger_mod.ledger_scanner_seed_links(ledger)

    assert ledger["stats"]["records"] == 1
    assert ledger["stats"]["scanner_seed_eligible"] == 1
    assert seeds[0]["origin"] == "company_source_ledger"
    assert seeds[0]["source_type"] == "financial"
