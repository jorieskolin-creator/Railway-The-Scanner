import importlib
import json
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
    assert bp.buying_generation_chain()
    assert bp.buying_gate_chain()
    assert bp.service_kb_extraction_chain()
    assert bp.service_kb_repair_chain()
    assert bp.buying_report_repair_chain()
    assert all("haiku" not in model.lower() for model in bp.buying_generation_chain())
    assert all("haiku" not in model.lower() for model in bp.buying_report_repair_chain())


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
    assert "Account Need Hypothesis" in html
    assert "Possible Conversation Directions" in html
    assert "Recommended Sales Plays" in html
    assert "Jane Leader" in html
    assert "Technology sponsor" in html
    assert "Data and AI Governance" in html
    assert "Do not imply confirmed buying intent." in html
    assert html.index("Pressure & Demand Hypothesis Signals") < html.index("Why This May Matter Now")
    assert html.index("Buying Committee & Influence Map") < html.index("Possible Conversation Directions")
    assert html.index("Recommended Sales Plays") < html.index("Evidence, Confidence & Sales Guardrails")


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


def test_generated_text_cleanup_preserves_pronoun_and_removes_markdown_media(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    cleaned = bp._clean_generated_text(
        "It matters when it is evidenced. IT architecture is relevant. "
        "![image](https://example.com/image.png) [Company news](https://example.com/news) "
        "https://example.com/dangling"
    )

    assert "when it is evidenced" in cleaned
    assert "IT architecture" in cleaned
    assert "![" not in cleaned
    assert "https://" not in cleaned
    assert "Company news" in cleaned


def test_truncated_pressure_section_salvages_complete_items(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    raw = (
        '{"pressure_signals": ['
        '{"signal_type": "One", "description": "Complete", "evidence_basis": ["SIG-01"]},'
        '{"signal_type": "Two", "description": "Truncated"'
    )

    salvaged = bp._salvage_truncated_section("pressure_evidence", raw)

    assert salvaged is not None
    assert len(salvaged["pressure_signals"]) == 1
    assert salvaged["pressure_signals"][0]["evidence_basis"] == ["SIG-01"]
    assert salvaged["evidence_confidence"] == {}


def test_sales_play_fallback_does_not_force_ambiguous_service(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    signal = {
        "id": "SIG-03",
        "source": "scanner_activation_pair",
        "activation_pair_index": 3,
        "theme": "Strategy and financial capacity alignment",
        "why": "Validate whether execution capacity supports the stated ambition.",
        "what_to_do": "Create a validated alignment position.",
        "buyer_roles": ["CFO / Finance Lead", "COO / Operations Lead"],
        "service_fit_hints": [
            {"service": "Enterprise AI Services / AI for Outcomes"},
            {"service": "AI Business Consulting / AI-First Alignment"},
        ],
    }
    candidates = [
        {"name": "Enterprise AI Services / AI for Outcomes"},
        {"name": "AI Business Consulting / AI-First Alignment"},
    ]

    play = bp._sales_play_from_signal(signal, {"account_thesis": "Thesis"}, service_candidates=candidates)

    assert play["linked_signal_ids"] == ["SIG-03"]
    assert play["service_or_offer"] == "Discovery validation - offer to be selected after qualification"


def test_sales_plays_are_limited_to_one_primary_play_per_activation_sig(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    payload = {
        "services": [
            {"name": "Service A"},
            {"name": "Service B"},
            {"name": "Service C"},
        ],
        "account_signal_map": [
            {
                "id": f"SIG-0{index}",
                "source": "scanner_activation_pair",
                "activation_pair_index": index,
                "theme": f"Theme {index}",
                "why": f"Why theme {index} matters commercially.",
                "what_to_do": f"Validate outcome {index}.",
                "buyer_roles": ["CIO / CTO"],
                "service_fit_hints": [{"service": f"Service {chr(64 + index)}"}],
            }
            for index in range(1, 4)
        ],
    }
    report = {
        "sales_spine": {"account_thesis": "Account thesis"},
        "recommended_sales_plays": [
            {
                "play_name": f"Candidate {signal_id}-{candidate}",
                "linked_signal_ids": [signal_id],
                "evidence_basis": [signal_id],
                "service_or_offer": service,
                "buying_hypothesis": "A sufficiently complete buying hypothesis.",
                "trigger_to_offer_bridge": "A sufficiently complete response direction.",
                "first_meeting_objective": "Validate owner, timing, and consequence.",
                "safe_customer_wording": "Explore whether this pressure matters.",
                "proof_point": "A validated position exists.",
                "discovery_questions": ["Where would this become visible?"],
            }
            for signal_id, service in (
                ("SIG-01", "Service A"),
                ("SIG-01", "Service B"),
                ("SIG-02", "Service B"),
                ("SIG-02", "Service C"),
                ("SIG-03", "Service C"),
            )
            for candidate in (1,)
        ],
    }

    bp._enforce_one_primary_sales_play(report, payload)

    assert len(report["recommended_sales_plays"]) == 3
    assert [play["linked_signal_ids"] for play in report["recommended_sales_plays"]] == [
        ["SIG-01"],
        ["SIG-02"],
        ["SIG-03"],
    ]


def qualification_payload():
    return {
        "company": {"name": "Ensto"},
        "scanner_outputs": {
            "general_summary": "Ensto provides electricity-distribution products and services for industrial and utility customers.",
            "strategic_synthesis": "S" * 3600,
            "evidence_based_risks": ["Current architecture and AI readiness are not established in the promoted evidence set."],
            "activation_pairs": [],
        },
        "account_signal_map": [
            {
                "id": "SIG-01",
                "source": "scanner_activation_pair",
                "theme": "Operating-model proof",
                "why": "Ensto's stated ambition is not yet connected to verified operating-model capability.",
                "what_to_do": "Establish a decision-ready operating-model position.",
                "what_progress_shows": "Leadership can distinguish verified capability from assumptions.",
            },
            {
                "id": "SIG-02",
                "source": "scanner_activation_pair",
                "theme": "Adaptive industrial operations",
                "why": "Industrial AI and energy-data pressure may change the capability expected from electricity-distribution providers.",
                "what_to_do": "Clarify readiness for adaptive operations and governed energy data.",
                "what_progress_shows": "A validated readiness position informs priorities.",
            },
        ],
        "required_sales_spine": [],
        "verified_evidence": [
            {"title": "Tero-Luoma+bg-web.jpg", "snippet": "![image](https://example.com/noise.jpeg)"}
        ],
        "financial_sources": [],
        "source_ledger_recovery_records": [],
    }


def valid_qualification_reasoning():
    return {
        "account_thesis": "Ensto's electricity-distribution position creates a qualification question about whether its operating model and data foundations can support the next phase of industrial adaptation.",
        "current_position": "Ensto is an established electricity-distribution technology provider with a portfolio spanning grid accessories, automation, protection, and control.",
        "verified_strengths": ["Its current portfolio provides a credible industrial and utility-market foundation."],
        "evidence_boundaries": ["The promoted evidence does not establish current architecture, AI deployment, ownership, timing, or budget."],
        "structural_exposure": "the breadth of the portfolio requires coordinated operating ownership and governed information flows as external expectations develop",
        "strategic_tension": "The portfolio ambition is clearer than the evidence showing how operating capabilities support it today.",
        "external_pressure": "Industrial AI, energy productivity, and software-defined operations may raise customer expectations for adaptive and data-enabled delivery.",
        "likely_business_consequence": "If that pressure becomes material, unclear capability ownership could slow prioritization and weaken investment focus.",
        "emerging_need": "A decision-ready view of operating-model and data readiness may therefore be needed before selecting modernization priorities.",
        "ability_to_act": "Investment capacity and an accountable owner still need to be confirmed rather than inferred.",
        "validation_question": "Where would these pressures first affect customer value, operating performance, or investment choices?",
        "falsifier": "The hypothesis weakens if the relevant capabilities are already governed and the external pressures do not affect Ensto's priorities.",
        "causal_sequence": "Ensto has a credible electricity-distribution position, but operating-model evidence is incomplete. External industrial and energy-data pressure may raise the required capability level. If the intersection becomes material, leadership may need a validated readiness position before committing investment.",
        "evidence_basis": ["SIG-01", "SIG-02"],
    }


def test_snapshot_payload_keeps_complete_strategic_synthesis(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    compact = bp._section_payload("snapshot_thesis", qualification_payload())

    assert len(compact["scanner_outputs"]["strategic_synthesis"]) == 3600


def test_qualification_reasoning_builds_fluent_opening_without_raw_contract(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    sections = bp._compose_qualification_sections(valid_qualification_reasoning(), qualification_payload())

    combined = json.dumps(sections, ensure_ascii=False)
    assert "Ensto" in sections["account_snapshot"]["text"]
    assert len(sections["account_snapshot"]["paragraphs"]) == 2
    assert len(sections["buying_potential_thesis"]["paragraphs"]) == 2
    assert "WHAT_TO_DO" not in combined
    assert "SIG-01 — WHY" not in combined
    assert "Tero-Luoma" not in combined


def test_snapshot_generation_uses_high_effort_and_shared_reasoning_spine(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    calls = []
    response = {"qualification_reasoning": valid_qualification_reasoning(), "model_notes": []}

    def fake_call(*args, **kwargs):
        calls.append(kwargs)
        return json.dumps(response), "gpt-5.6-sol"

    monkeypatch.setattr(bp, "call_json_model_chain", fake_call)
    monkeypatch.setattr(bp, "parse_json_object", json.loads)

    section, meta = bp._generate_buying_section("snapshot_thesis", qualification_payload())

    assert calls[0]["reasoning_effort"] == "high"
    assert meta["qualification_reasoning_quality"]["status"] == "clean"
    assert section["sales_spine"]["account_thesis"].startswith("Ensto's electricity-distribution")


def test_incomplete_qualification_reasoning_triggers_focused_no_web_repair(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    calls = []

    def fake_call(prompt, *args, **kwargs):
        calls.append((prompt, kwargs))
        return json.dumps({"qualification_reasoning": valid_qualification_reasoning()}), "gpt-5.6-sol"

    monkeypatch.setattr(bp, "call_json_model_chain", fake_call)
    monkeypatch.setattr(bp, "parse_json_object", json.loads)

    section, quality = bp._finalize_qualification_section(
        {"qualification_reasoning": {"account_thesis": "Too short"}},
        qualification_payload(),
    )

    assert quality["status"] == "repaired"
    assert quality["initial_issues"]
    assert not quality["remaining_issues"]
    assert calls[0][1]["reasoning_effort"] == "high"
    assert "no-web" in calls[0][0]
    assert "Ensto" in section["account_snapshot"]["text"]


def test_short_opening_enrichment_never_appends_arbitrary_source_records(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "qualification_reasoning": valid_qualification_reasoning(),
        "account_snapshot": {"text": "Too short", "gate": "allowed_as_fact"},
        "buying_potential_thesis": {"text": "Too short", "rationale": "Too short"},
    }

    bp._enrich_short_opening_sections(report, qualification_payload())

    combined = json.dumps(report, ensure_ascii=False)
    assert "Tero-Luoma" not in combined
    assert "image" not in combined
    assert "Additional verified context" not in combined
    assert "qualification question" in combined
