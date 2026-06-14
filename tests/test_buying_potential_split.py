import importlib
import sys
import types


def load_buying_potential(monkeypatch):
    requests = types.ModuleType("requests")
    requests.get = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "requests", requests)

    pypdf = types.ModuleType("pypdf")
    pypdf.PdfReader = object
    monkeypatch.setitem(sys.modules, "pypdf", pypdf)

    llm_clients = types.ModuleType("llm_clients")
    llm_clients.CLAUDE_HAIKU_MODEL = "haiku"
    llm_clients.CLAUDE_OPUS_MODEL = "opus"
    llm_clients.CLAUDE_SONNET_MODEL = "sonnet"
    llm_clients.GPT_REASONING_MODEL = "gpt"
    llm_clients.GPT_VERIFIER_MODEL = "gptv"
    llm_clients.call_json_model_chain = lambda *args, **kwargs: ("{}", "stub")
    llm_clients.parse_json_object = lambda raw: {}
    monkeypatch.setitem(sys.modules, "llm_clients", llm_clients)

    source_quality = types.ModuleType("source_quality")
    source_quality.is_generation_eligible_source = lambda source: False
    monkeypatch.setitem(sys.modules, "source_quality", source_quality)

    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("buying_potential", None)
    return importlib.import_module("buying_potential")


def test_buying_potential_post_process_removes_legacy_angles_and_applies_downgrade(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    report = {
        "account_snapshot": {"text": "Snapshot", "gate": "allowed_as_fact", "evidence_basis": ["SRC-01"]},
        "buying_potential_thesis": {"text": "Thesis", "gate": "allowed_as_hypothesis"},
        "recommended_sales_plays": [
            {
                "service_or_offer": "Modernization",
                "why_relevant": "Reason",
                "gate": "allowed_as_fact",
                "discovery_questions": ["What is changing?"],
                "do_not_say": ["They are buying now."],
            }
        ],
        "conversation_angles": [{"angle_type": "do_not_say", "text": "legacy"}],
    }
    payload = {"verified_evidence": [{"id": "SRC-01"}], "financial_sources": []}
    gate_review = {
        "approved": False,
        "required_downgrades": [
            {
                "path": "recommended_sales_plays[0]",
                "new_gate": "allowed_as_discovery_question",
                "reason": "Recommendation must be question-led.",
            }
        ],
        "blocked_claims": ["Confirmed buying intent"],
    }

    processed = bp._post_process_report(report, payload, gate_review)

    assert "conversation_angles" not in processed
    assert processed["recommended_sales_plays"][0]["gate"] == "allowed_as_discovery_question"
    assert processed["recommended_sales_plays"][0]["discovery_questions"] == ["What is changing?"]
    assert "Confirmed buying intent" in processed["evidence_confidence"]["do_not_use_claims"]


def test_buying_potential_renderer_uses_new_section_order_and_nested_play_angles(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    html = bp.render_buying_potential_html(
        {
            "companyName": "TestCo",
            "generated_at": "2026-06-14T00:00:00",
            "overall_confidence": 50,
            "account_snapshot": {"text": "Snapshot", "gate": "allowed_as_hypothesis"},
            "buying_potential_thesis": {"text": "Thesis", "gate": "allowed_as_hypothesis"},
            "why_now": [],
            "pain_to_solution_map": [
                {
                    "service": "Service",
                    "gate": "allowed_as_discovery_question",
                    "interpreted_pressure": "Pressure",
                    "why_this_service": "Fit",
                    "safe_wording": "Ask",
                    "proof_point": "Proof",
                    "disqualifiers": ["x"],
                }
            ],
            "recommended_sales_plays": [
                {
                    "service_or_offer": "Offer",
                    "why_relevant": "Reason",
                    "target_stakeholder": "Owner",
                    "opening_angle": "Open",
                    "executive_angle": "Exec",
                    "technical_angle": "Tech",
                    "discovery_questions": ["Question"],
                    "do_not_say": ["Do not overclaim"],
                    "confidence": 40,
                    "overclaim_risk": "medium",
                }
            ],
            "pressure_signals": [],
            "stakeholder_map": [],
            "evidence_confidence": {},
            "source_counts": {},
        }
    )

    assert "Suggested Conversation Angles" not in html
    assert "Discovery Questions" in html
    assert "Do Not Say" in html
    assert html.index("Recommended Sales Plays") < html.index("Pressure Signals")
    assert html.index("Pressure Signals") < html.index("Stakeholder Map")
    assert html.index("Stakeholder Map") < html.index("Evidence, Confidence")
