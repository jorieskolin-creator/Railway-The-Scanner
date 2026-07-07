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


def test_buying_potential_defaults_to_quality_model_chain(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    monkeypatch.setenv("BUYING_POTENTIAL_MODEL_MODE", "testing")

    assert bp._buying_model_mode() == "quality"
    assert bp.buying_generation_chain() == ("opus", "gpt", "sonnet")
    assert bp.buying_gate_chain() == ("gptv", "sonnet")
    assert bp.service_kb_extraction_chain() == ("gpt", "sonnet", "haiku")
    assert bp.service_kb_repair_chain() == ("gptv", "sonnet", "haiku")
    assert bp.buying_report_repair_chain() == ("gptv", "sonnet", "haiku")


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
                    "play_name": "Signal-backed play",
                    "linked_signal_ids": ["SIG-01"],
                    "service_or_offer": "Offer",
                    "why_relevant": "Reason",
                    "primary_buyer": "Owner",
                    "trigger": "Trigger",
                    "business_hypothesis": "Hypothesis",
                    "first_meeting_objective": "Validate pressure",
                    "opening_angle": "Open",
                    "executive_angle": "Exec",
                    "technical_angle": "Tech",
                    "proof_point": "Proof",
                    "discovery_questions": ["Question"],
                    "qualification_upgrade_criteria": ["Customer confirms"],
                    "do_not_say": ["Do not overclaim"],
                    "confidence": 40,
                    "play_score": 55,
                    "play_classification": "monitor_soft_discovery",
                    "overclaim_risk": "medium",
                }
            ],
            "pressure_signals": [],
            "stakeholder_map": [
                {
                    "name": "Jane Leader",
                    "role": "CIO",
                    "likely_buying_committee_role": "Technology sponsor",
                    "relevance_rationale": "Owns platform and data modernization concerns.",
                    "linked_pressure_themes": ["platform pressure"],
                    "linked_service_themes": ["Data and AI Governance"],
                    "evidence_basis": "Company Evidence DB leadership record",
                    "allowed_use": "allowed_as_discovery_question",
                    "confidence": 55,
                    "do_not_say": ["Do not imply confirmed buying intent."],
                    "safety_notes": ["Validate current role before outreach."],
                }
            ],
            "evidence_confidence": {},
            "source_counts": {},
        }
    )

    assert "Suggested Conversation Angles" not in html
    assert "Discovery Questions" in html
    assert "Do Not Say" in html
    assert "Account Opportunity Hypothesis" in html
    assert "Opportunity-to-Offer Fit" in html
    assert "Recommended Growth Plays" in html
    assert "Jane Leader" in html
    assert "Technology sponsor" in html
    assert "Data and AI Governance" in html
    assert "Do not imply confirmed buying intent." in html
    assert html.index("Commercial Pressure Signals") < html.index("Timing Triggers")
    assert html.index("Buying Committee & Influence Map") < html.index("Opportunity-to-Offer Fit")
    assert html.index("Recommended Growth Plays") < html.index("Evidence, Confidence & Sales Guardrails")


def test_buying_potential_footer_points_to_scanner_instead_of_rendering_evidence_register(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    html = bp.render_buying_potential_html(
        {
            "companyName": "Sisu Auto",
            "generated_at": "2026-07-07T00:00:00",
            "overall_confidence": 58,
            "account_snapshot": {"text": "Snapshot", "gate": "allowed_as_hypothesis"},
            "buying_potential_thesis": {"text": "Thesis", "gate": "allowed_as_hypothesis"},
            "sales_spine": {"top_buying_hypotheses": ["SIG-01: hypothesis"]},
            "pressure_signals": [],
            "why_now": [],
            "stakeholder_map": [],
            "pain_to_solution_map": [],
            "recommended_sales_plays": [],
            "evidence_confidence": {
                "verified_facts": ["Fact"],
                "safe_hypotheses": ["Hypothesis"],
                "analysis_context": ["Context"],
                "use_only_as_question": ["Question?"],
                "weak_or_missing_evidence": ["Missing evidence"],
                "report_quality_notes": ["Repair note"],
                "do_not_say": ["Blocked claim"],
                "sensitive_claims": ["Sensitive claim"],
            },
            "source_counts": {},
        }
    )

    assert "Open Scanner" in html
    assert "https://the-scanner.up.railway.app/" in html
    assert "Scanner Company Summary" in html
    assert "<h3>Verified Facts</h3>" not in html
    assert "<h3>Safe Hypotheses</h3>" not in html
    assert "<h3>Analysis Context</h3>" not in html
    assert "<h3>Use Only As Question</h3>" in html
    assert "<h3>Weak / Missing Evidence</h3>" in html
    assert "<h3>Report Quality Notes</h3>" in html
    assert "<h3>Do Not Say</h3>" in html
    assert "<h3>Sensitive Claims</h3>" in html


def test_account_signal_map_translates_sisu_like_evidence_into_buyer_pressure(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    payload = bp._build_input_payload(
        {
            "companyName": "Sisu Auto",
            "companyUrl": "https://sisuauto.com",
            "evidence_links": [
                {
                    "title": "Sisu Auto production ramp and partner manufacturing agreement",
                    "snippet": "Rapid growth, partner production, defence-sector demand and autonomy cooperation increase capacity pressure.",
                    "analysis_verified": True,
                    "promotion_status": "generation_eligible",
                }
            ],
            "summary_text": "Rapid revenue growth and partner-led capacity scaling create operational coordination pressure.",
        },
        {
            "services": [
                {
                    "name": "Data and AI Governance",
                    "category": "AI / data",
                    "buyer_personas": ["CIO", "CTO", "COO"],
                    "pain_triggers": ["autonomy", "data", "platform", "scaling", "production"],
                    "evidence_patterns": ["autonomy", "data", "partner production"],
                    "proof_points": ["Governed data and AI operating models"],
                    "safe_wording": ["Explore product-data and governance readiness as systems become more digital."],
                    "do_not_claim": ["Do not claim an AI project exists."],
                    "disqualifiers": ["No product-data or governance owner."],
                }
            ]
        },
    )

    signals = payload["account_signal_map"]
    assert signals
    assert signals[0]["id"].startswith("SIG-")
    combined = " ".join(str(signal) for signal in signals).lower()
    assert "scaling" in combined or "partner" in combined
    assert any(signal.get("service_fit_hints") for signal in signals)
    assert any("COO" in role or "CIO" in role or "CTO" in role for signal in signals for role in signal["buyer_roles"])


def test_post_process_downgrades_generic_sales_play_without_signal_or_service_bridge(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    payload = {
        "verified_evidence": [{"id": "SRC-01"}],
        "financial_sources": [],
        "services": [{"name": "AI Governance", "category": "AI"}],
        "account_signal_map": [{"id": "SIG-01", "pressure_type": "Scaling pressure"}],
        "blocked_or_display_only_sources": [],
    }
    report = {
        "account_snapshot": {"text": "Snapshot", "gate": "allowed_as_hypothesis"},
        "buying_potential_thesis": {"text": "Thesis", "gate": "allowed_as_hypothesis"},
        "pressure_signals": [
            {"signal_type": "Foresight pressure 1", "description": "", "gate": "allowed_as_hypothesis"},
            {"signal_type": "Scaling", "description": "Growth pressure", "evidence_basis": ["SIG-01"], "gate": "allowed_as_hypothesis"},
        ],
        "recommended_sales_plays": [
            {
                "service_or_offer": "Generic consulting",
                "why_relevant": "AI pilots do not scale",
                "target_stakeholder": "Relevant owner",
                "discovery_questions": [],
                "do_not_say": [],
                "gate": "allowed_as_hypothesis",
            }
        ],
        "pain_to_solution_map": [
            {"service": "Generic consulting", "interpreted_pressure": "Generic pain", "gate": "allowed_as_hypothesis"}
        ],
        "stakeholder_map": [],
        "evidence_confidence": {},
    }

    processed = bp._post_process_report(report, payload)

    assert len(processed["pressure_signals"]) == 1
    play = processed["recommended_sales_plays"][0]
    assert play["gate"] in {"allowed_as_discovery_question", "internal_only"}
    assert play["overclaim_risk"] == "high"
    assert play["discovery_questions"]
    assert "signal" in " ".join(play["do_not_say"]).lower()


def test_top_buying_hypotheses_preserve_active_signal_coverage(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    payload = {
        "account_signal_map": [
            {
                "id": "SIG-01",
                "source": "scanner_activation_pair",
                "theme": "Operating-model verification gap",
                "why": "Growth and profitability are visible, but operating-model evidence still needs validation.",
                "what_to_do": "Validate the operating-model baseline.",
            },
            {
                "id": "SIG-02",
                "source": "scanner_activation_pair",
                "theme": "Industrial AI readiness gap",
                "why": "External industrial AI pressure may require clearer data and governance readiness.",
                "what_to_do": "Validate AI readiness around practical operational use cases.",
            },
            {
                "id": "SIG-03",
                "source": "scanner_activation_pair",
                "theme": "Lifecycle data opportunity",
                "why": "Traceability pressure may make lifecycle data a commercial and compliance topic.",
                "what_to_do": "Validate lifecycle-data readiness and ownership.",
            },
        ]
    }
    sales_spine = {
        "top_buying_hypotheses": [
            "Operating-model verification buying hypothesis (SIG-01): Validate how growth scales operationally.",
            "Industrial AI readiness buying hypothesis (SIG-02): Validate where AI readiness matters commercially.",
            "Operating-model verification gap despite financial strength (Operating model verification gap despite.)",
        ]
    }

    processed = bp._normalize_top_buying_hypotheses(sales_spine, payload)

    rendered = " ".join(processed["top_buying_hypotheses"])
    assert "SIG-01" in rendered
    assert "SIG-02" in rendered
    assert "SIG-03" in rendered
    assert len(processed["top_buying_hypotheses"]) == 3


def test_evidence_confidence_reroutes_facts_and_unsafe_items_out_of_questions(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    cleaned = bp._clean_evidence_confidence_buckets(
        {
            "verified_facts": [],
            "safe_hypotheses": [],
            "use_only_as_question": [
                "Business ID 1749559-9 is listed for Sisu Auto.",
                "Confirmed buying intent exists.",
                "Could lifecycle traceability become a relevant discovery topic?",
            ],
            "weak_or_missing_evidence": [],
            "do_not_use_claims": [],
        }
    )

    assert any("Business ID" in item for item in cleaned["verified_facts"])
    assert any("Confirmed buying intent" in item for item in cleaned["do_not_say"])
    assert cleaned["use_only_as_question"] == ["Could lifecycle traceability become a relevant discovery topic?"]


def test_deterministic_fallback_uses_account_signal_map_not_generic_service_triggers(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    report = bp._deterministic_buying_report(
        {
            "company": {"name": "Sisu Auto"},
            "scanner_outputs": {"ai_diagnosis": "Partner-led production scaling creates coordination pressure."},
            "verified_evidence": [],
            "financial_sources": [],
            "blocked_or_display_only_sources": [],
            "stakeholders": [],
            "account_signal_map": [
                {
                    "id": "SIG-01",
                    "account_movement": "Partner production and autonomy-adjacent cooperation",
                    "commercial_pressure": "Partner coordination and product-data governance pressure",
                    "business_consequence": "Scaling can create delivery and data-governance friction.",
                    "buyer_roles": ["COO / Operations Lead", "CIO / CTO"],
                    "buyer_role_concern": "COO owns scalable delivery; CIO owns digital backbone.",
                    "timing_relevance": "Recent partnership signal.",
                    "confidence": 50,
                    "gate": "allowed_as_hypothesis",
                    "service_fit_hints": [
                        {
                            "service": "Data and AI Governance",
                            "proof_points": ["Governance operating model"],
                            "safe_wording": ["Explore whether product-data governance is becoming relevant."],
                            "do_not_claim": ["Do not claim an AI project exists."],
                        }
                    ],
                }
            ],
            "services": [{"name": "Data and AI Governance"}],
        },
        "test fallback",
    )

    assert report["recommended_sales_plays"]
    play = report["recommended_sales_plays"][0]
    assert play["linked_signal_ids"] == ["SIG-01"]
    assert "Partner" in play["trigger"]
    assert "AI pilots do not scale" not in play["why_relevant"]
