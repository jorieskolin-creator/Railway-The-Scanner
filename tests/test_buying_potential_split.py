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


def test_buying_potential_uses_reasoning_aware_sol_budgets_and_strict_schemas(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    assert bp._section_token_limit("snapshot_thesis") == 9000
    assert bp._section_generation_controls("snapshot_thesis") == ("high", "medium")
    assert bp._section_token_limit("sales_plays") == 6000
    assert bp._section_generation_controls("sales_plays") == ("medium", "medium")
    assert bp._section_token_limit("pressure_evidence") == 5000
    assert bp._section_generation_controls("pressure_evidence") == ("low", "low")
    assert bp.BUYING_SECTION_JSON_SCHEMAS["snapshot_thesis"]["additionalProperties"] is False
    assert bp._BUYING_SALES_PLAY_JSON_SCHEMA["additionalProperties"] is False
    gate_policy = bp.policy_for("buying_insight_gate")
    assert gate_policy["effort"] == "low"
    assert gate_policy["verbosity"] == "low"
    assert gate_policy["max_output_tokens"] == 5000
    assert gate_policy["timeout_seconds"] == 90
    assert bp._BUYING_GATE_REVIEW_SCHEMA["additionalProperties"] is False
    repair_policy = bp.policy_for("buying_insight_gate_repair")
    assert repair_policy["effort"] == "medium"
    assert repair_policy["chain"][0] == "claude-sonnet-5"
    assert repair_policy["max_batch_items"] == 5
    assert bp.policy_for("buying_insight_language_qa")["chain"][0] == "claude-sonnet-5"
    assert bp.policy_for("buying_insight_gate_reanalysis")["chain"][0] == "gpt-5.6-sol"


def test_gate_claim_ids_are_stable_and_nested_spine_path_is_writable(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "sales_spine": {
            "top_buying_hypotheses": [{
                "hypothesis": "An unsupported strong claim.",
                "linked_signal_ids": ["SIG-01"],
                "linked_hypothesis_ids": ["HYP-01"],
            }]
        }
    }
    first = bp._build_gate_review_packet(report, {})["claims"][0]
    second = bp._build_gate_review_packet(report, {})["claims"][0]

    assert first["claim_id"] == second["claim_id"]
    assert first["path"] == "sales_spine.top_buying_hypotheses[0].hypothesis"

    applied, rejected = bp._apply_gate_field_actions(report, [{
        "claim_id": first["claim_id"],
        "object_id": "SIG-01",
        "path": first["path"],
        "action": "replace",
        "replacement": "A narrower question-led hypothesis.",
        "reason": "Evidence supports a hypothesis only.",
    }])

    assert not rejected
    assert applied[0]["claim_id"] == first["claim_id"]
    assert report["sales_spine"]["top_buying_hypotheses"][0]["hypothesis"] == "A narrower question-led hypothesis."


def test_gate_decision_requires_complete_claim_coverage(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    packet = bp._build_gate_review_packet(
        {"account_snapshot": {"text": "A factual account statement.", "gate": "allowed_as_fact"}},
        {},
    )
    valid, reason = bp._gate_decision_is_complete({
        "status": "approved",
        "reviewed_claim_ids": [],
        "claim_decisions": [],
    }, packet)

    assert valid is False
    assert "coverage incomplete" in reason.lower()


def test_gate_workflow_splits_decision_and_wording_repair(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {"account_snapshot": {"text": "Ensto has proven enterprise AI maturity.", "gate": "allowed_as_fact"}}
    packet = bp._build_gate_review_packet(report, {})
    claim = packet["claims"][0]
    calls = []

    def fake_stage(_prompt, _chain, **kwargs):
        calls.append(kwargs["log_label"])
        if "Decision" in kwargs["log_label"]:
            return ({
                "status": "changes_required",
                "reviewed_claim_ids": [claim["claim_id"]],
                "claim_decisions": [{
                    "claim_id": claim["claim_id"],
                    "decision": "rewrite",
                    "new_gate": "allowed_as_discovery_question",
                    "unsafe_text": claim["text"],
                    "reason": "Current AI maturity is not verified.",
                }],
                "notes": [],
            }, "gpt-5.4")
        return ({
            "repairs": [{
                "claim_id": claim["claim_id"],
                "object_id": claim["object_id"],
                "path": claim["path"],
                "action": "replace",
                "replacement": "Discovery should establish Ensto's current enterprise AI maturity.",
                "reason": "Reframed as a validation question.",
            }],
            "notes": [],
        }, "gpt-5.4")

    monkeypatch.setattr(bp, "_call_gate_stage", fake_stage)
    review = bp._run_gate_review_workflow(report, {})

    assert calls == ["Buying Insight Gate Decision 1/1", "Buying Insight Gate Wording Repair 1/1"]
    assert review["field_actions"][0]["replacement"].startswith("Discovery should establish")
    assert review["deterministic_removal_count"] == 0


def test_gate_workflow_uses_sol_reanalysis_then_deterministic_removal(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {"buying_potential_thesis": {"text": "The unsupported causal chain proves immediate demand.", "gate": "allowed_as_hypothesis"}}
    packet = bp._build_gate_review_packet(report, {})
    claim = packet["claims"][0]

    def fake_stage(_prompt, _chain, **kwargs):
        if "Decision" in kwargs["log_label"]:
            return ({
                "status": "changes_required",
                "reviewed_claim_ids": [claim["claim_id"]],
                "claim_decisions": [{
                    "claim_id": claim["claim_id"],
                    "decision": "reanalysis_required",
                    "new_gate": "allowed_as_discovery_question",
                    "unsafe_text": claim["text"],
                    "reason": "Removing the sentence would break the account thesis.",
                }],
                "notes": [],
            }, "gpt-5.4")
        raise ValueError("focused reanalysis unavailable")

    monkeypatch.setattr(bp, "_call_gate_stage", fake_stage)
    review = bp._run_gate_review_workflow(report, {})

    assert review["deterministic_removal_count"] == 1
    assert review["field_actions"][0]["action"] == "remove"
    assert review["field_actions"][0]["claim_id"] == claim["claim_id"]


def test_gate_decision_batches_preserve_all_claims_without_order_truncation(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    packet = {
        "review_scope": "test",
        "allowed_use_boundary": "internal",
        "evidence_sufficiency": {},
        "required_sales_spine": [],
        "hypothesis_contracts": [],
        "claims": [
            {
                "claim_id": f"CLM-{index:03d}",
                "path": f"pressure_signals[{index}].description",
                "object_id": f"SIG-{index:02d}",
                "text": f"Claim {index}",
                "gate": "allowed_as_hypothesis",
                "signal_ids": [f"SIG-{index:02d}"],
                "hypothesis_ids": [],
                "evidence_ids": [],
            }
            for index in range(67)
        ],
        "signals": [],
        "evidence_index": [],
        "service_bridges": [],
    }

    batches = bp._gate_decision_batches(packet, max_claims=30)
    claim_ids = [item["claim_id"] for batch in batches for item in batch["claims"]]

    assert [len(batch["claims"]) for batch in batches] == [30, 30, 7]
    assert claim_ids == [f"CLM-{index:03d}" for index in range(67)]


def test_gate_wording_repair_batches_are_bounded_and_keep_all_claims(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    packet = {
        "claims": [
            {
                "claim_id": f"CLM-{index:03d}",
                "path": f"recommended_sales_plays[{index}].buying_hypothesis",
                "object_id": f"HYP-{index:02d}",
                "text": f"Unsafe claim {index} with enough text to require a bounded rewrite.",
                "gate": "allowed_as_hypothesis",
                "signal_ids": [f"SIG-{index:02d}"],
                "hypothesis_ids": [f"HYP-{index:02d}"],
                "evidence_ids": [],
            }
            for index in range(12)
        ],
        "hypothesis_contracts": [],
        "signals": [],
        "evidence_index": [],
    }
    decisions = [
        {"claim_id": f"CLM-{index:03d}", "decision": "rewrite", "reason": "Narrow the claim."}
        for index in range(12)
    ]

    batches = bp._gate_repair_batches(packet, decisions, max_items=5, max_chars=50000)

    assert [len(batch) for batch in batches] == [5, 5, 2]
    assert [item["claim_id"] for batch in batches for item in batch] == [f"CLM-{index:03d}" for index in range(12)]


def test_failed_gate_repair_batch_is_removed_without_sol_reanalysis(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "recommended_sales_plays": [
            {
                "hypothesis_id": f"HYP-{index:02d}",
                "linked_signal_ids": [f"SIG-{index:02d}"],
                "opening_angle": f"Unsupported opening claim {index} proves immediate demand.",
                "gate": "allowed_as_hypothesis",
            }
            for index in range(6)
        ]
    }
    packet = bp._build_gate_review_packet(report, {})
    claims = packet["claims"]
    calls = []

    def fake_stage(_prompt, chain, **kwargs):
        calls.append((kwargs["log_label"], tuple(chain or ())))
        if "Decision" in kwargs["log_label"]:
            return ({
                "status": "changes_required",
                "reviewed_claim_ids": [item["claim_id"] for item in claims],
                "claim_decisions": [
                    {
                        "claim_id": item["claim_id"],
                        "decision": "rewrite",
                        "new_gate": "allowed_as_discovery_question",
                        "unsafe_text": item["text"],
                        "reason": "Immediate demand is unsupported.",
                    }
                    for item in claims
                ],
                "notes": [],
            }, "gpt-5.4")
        if kwargs["log_label"].endswith("1/2"):
            batch_ids = [item["claim_id"] for item in claims[:5]]
            return ({
                "repairs": [
                    {
                        "claim_id": claim_id,
                        "object_id": next(item["object_id"] for item in claims if item["claim_id"] == claim_id),
                        "path": next(item["path"] for item in claims if item["claim_id"] == claim_id),
                        "action": "replace",
                        "replacement": "Discovery should establish whether this demand exists.",
                        "reason": "Reframed as a discovery question.",
                    }
                    for claim_id in batch_ids
                ],
                "notes": [],
            }, "claude-sonnet-5")
        raise ValueError("repair batch unavailable")

    monkeypatch.setattr(bp, "_call_gate_stage", fake_stage)
    review = bp._run_gate_review_workflow(report, {})

    assert len(review["field_actions"]) == 6
    assert sum(action["action"] == "remove" for action in review["field_actions"]) == 1
    assert not review["reanalysis_models"]
    assert calls[1][1][0] == "claude-sonnet-5"


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
    assert html.index("Recommended Sales Plays") < html.index("Evidence & Sales Guardrails")


def test_recommended_sales_play_card_renders_only_simplified_reader_fields(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    play = {
        "play_name": "Capacity proof discovery play",
        "linked_signal_ids": ["SIG-01"],
        "activation_pair_index": 1,
        "play_type": "current_status",
        "service_or_offer": "Service One",
        "response_directions": {
            "vivicta_potential_contribution": "Vivicta may help frame the outcome after discovery confirms the need.",
            "evolving_modernization_directions": ["Delivery & Capacity Planning: validate the capacity constraint."],
            "other_capabilities_may_be_required": ["Industrial engineering capability"],
        },
        "first_meeting_objective": "Establish whether the pressure has an owner and business consequence.",
        "opening_angle": "Where is delivery pressure most visible today?",
        "executive_angle": "The executive question is whether growth can be converted into repeatable delivery.",
        "technical_angle": "The technical question is whether process and data baselines support that delivery.",
        "discovery_questions": ["Which outcome would show that the constraint is easing?"],
        "proof_point": "An owner and observable success condition are agreed.",
        "safe_customer_wording": "It may be useful to explore whether delivery capacity is becoming a strategic constraint.",
        "readiness_band": "Validate now",
        "priority_band": "High",
        "evidence_coverage": "Internal governance detail",
        "confidence_boundary": "Internal boundary",
        "primary_buyer": "COO",
        "trigger": "Internal trigger",
        "account_thesis": "Internal thesis",
        "buying_hypothesis": "Internal buying hypothesis",
        "trigger_to_offer_bridge": "Internal bridge",
        "qualification_upgrade_criteria": ["Internal criterion"],
        "do_not_say": ["Internal prohibition"],
        "play_classification": "qualified_discovery_play",
    }
    html = bp.render_buying_potential_html({
        "companyName": "TestCo",
        "generated_at": "2026-07-21T00:00:00",
        "account_snapshot": {},
        "buying_potential_thesis": {},
        "sales_spine": {},
        "recommended_sales_plays": [play],
        "evidence_confidence": {},
        "source_counts": {},
    })

    for label in (
        "Potential Offer Area", "Vivicta Potential Contribution", "Modernization Lens",
        "Other Capabilities To Check", "First Meeting Objective", "Opening Angle",
        "Executive Angle", "Technical Angle", "Discovery Questions", "Proof Point",
        "Safe Customer-Facing Wording",
    ):
        assert label in html
    for hidden_label in (
        "Discovery readiness:", "Priority:", "Evidence coverage:", "Confidence boundary:",
        "Primary buyer:", "Trigger-to-offer bridge:", "Why This Might Matter",
        "Questions By Buyer Role", "Qualification Upgrade Criteria", "Activation pair 1",
    ):
        assert hidden_label not in html
    assert html.index("Proof Point") < html.index("Safe Customer-Facing Wording")
    assert play["readiness_band"] == "Validate now"


def test_sales_play_uses_only_exact_service_and_explicit_modernization_tactic(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    signal = {
        "id": "SIG-01",
        "hypothesis_id": "HYP-01",
        "activation_pair_index": 1,
        "theme": "Delivery capacity planning",
        "why": "Growth may create delivery capacity and planning pressure.",
        "what_to_do": "Establish an observable delivery-capacity position.",
        "what_progress_shows": "Capacity constraints and owners are explicitly evidenced.",
        "buyer_roles": ["COO / Operations Lead"],
        "gate": "allowed_as_hypothesis",
    }
    services = [{"service_id": "SVC-01", "name": "Capacity Advisory"}]
    tactics = [{
        "tactic_id": "TAC-01",
        "concept": "Delivery & Capacity Planning",
        "lens": "Delivery & Capacity Planning: validate where capacity constrains delivery.",
    }]
    analysis = {
        "account_signal_id": "SIG-01",
        "hypothesis_id": "HYP-01",
        "service_id": "SVC-01",
        "service_or_offer": "Capacity Advisory",
        "service_fit_rationale": "The service is relevant only if the delivery-capacity pressure is confirmed.",
        "modernization_tactic_ids": ["TAC-01", "TAC-NOT-ELIGIBLE"],
        "vivicta_potential_contribution": "Vivicta may help define the target delivery-capacity outcome.",
        "other_capabilities_to_check": ["Industrial engineering", "Industrial engineering"],
        "first_meeting_objective": "Validate owner, timing, and consequence.",
        "opening_angle": "Where is capacity pressure becoming visible?",
        "executive_angle": "Could capacity constrain strategic growth commitments?",
        "technical_angle": "Which process and data baselines show current capacity?",
        "discovery_questions": ["Who owns the constraint?", "Who owns the constraint?", "How is progress observed?"],
        "proof_point": "A named owner and observable target are agreed.",
        "safe_customer_wording": "It may be useful to explore whether capacity is becoming a constraint.",
    }

    play = bp._sales_play_from_signal(signal, {"account_thesis": "Internal thesis"}, analysis, services, tactics)

    assert play["service_id"] == "SVC-01"
    assert play["service_or_offer"] == "Capacity Advisory"
    assert [item["tactic_id"] for item in play["response_directions"]["modernization_lenses"]] == ["TAC-01"]
    assert play["response_directions"]["other_capabilities_may_be_required"] == ["Industrial engineering"]
    assert len(play["discovery_questions"]) == 2
    assert len({play["opening_angle"], play["executive_angle"], play["technical_angle"]}) == 3


def test_gate_and_language_qa_scope_sales_plays_to_reader_facing_fields(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "recommended_sales_plays": [{
            "linked_signal_ids": ["SIG-01"],
            "buying_hypothesis": "Internal wording should not enter the reader-facing Gate packet.",
            "opening_angle": "Could we explore whether this pressure is becoming relevant?",
            "executive_angle": "The executive question is whether this pressure changes strategic priorities.",
            "technical_angle": "The technical question is where the pressure is visible in current operations.",
            "first_meeting_objective": "Validate whether the pressure has an owner and consequence.",
            "proof_point": "An owner and observable validation outcome are agreed.",
            "safe_customer_wording": "It may be useful to explore whether this pressure matters.",
            "discovery_questions": ["Where would this pressure become visible first?"],
            "response_directions": {
                "vivicta_potential_contribution": "Vivicta may help frame the target outcome after validation.",
                "evolving_modernization_directions": [],
                "other_capabilities_may_be_required": [],
            },
        }],
    }
    packet = bp._build_gate_review_packet(report, {"account_signal_map": [{"id": "SIG-01"}]})
    claim_paths = {item["path"] for item in packet["claims"]}

    assert "recommended_sales_plays[0].opening_angle" in claim_paths
    assert "recommended_sales_plays[0].safe_customer_wording" in claim_paths
    assert "recommended_sales_plays[0].buying_hypothesis" not in claim_paths

    report["recommended_sales_plays"][0]["buying_hypothesis"] = "This hidden internal metadata stops with"
    report["recommended_sales_plays"][0]["opening_angle"] = "This reader-facing opening stops with"
    language_paths = {item["path"] for item in bp._language_public_items(report)}
    assert "recommended_sales_plays[0].opening_angle" in language_paths
    assert "recommended_sales_plays[0].buying_hypothesis" not in language_paths


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


def test_account_signal_map_uses_governed_lanes_instead_of_insertion_order(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    services = [{"service_id": "SVC-DATA", "name": "Data Foundation", "category": "Data", "pain_triggers": ["data", "growth"]}]
    payload = {
        "company": {"name": "Balanced Co"},
        "scanner_outputs": {
            "activation_pairs": [
                {
                    "theme_type": play_type,
                    "theme": f"Theme {index}",
                    "why": f"Customer pressure {index} matters for growth and data readiness.",
                    "what_to_do": f"A decision-ready outcome {index} is established.",
                    "what_progress_shows": f"Progress evidence {index} is visible.",
                }
                for index, play_type in enumerate(("current_status", "foresight_driven", "proactive_opportunity"), start=1)
            ],
            "ai_diagnosis": "Strategic context about operating capability and growth.",
            "pitch": "This must not become a signal.",
            "domain_force_dossier": "Domain context about changing customer expectations.",
            "evidence_absence_signals": ["Current data ownership is not verified."],
            "evidence_trust": {"false_or_unsupported_claims": []},
        },
        "verified_evidence": [
            {
                "id": f"CED-{index:02d}",
                "title": f"Account movement {index}",
                "quote": f"Verified account fact {index} about growth and operations.",
                "gate": "allowed_as_fact",
                "generation_eligible": True,
                "independence": "independent",
                "source_freshness": "fresh",
            }
            for index in range(1, 9)
        ],
        "financial_sources": [
            {"id": "FIN-01", "metric": "Revenue", "value": "growing", "year": "2026", "generation_eligible": True}
        ],
        "foresight_forces": [
            {"id": f"FORCE-{index:02d}", "description": f"External pressure {index} changes data and operating expectations.", "confidence": 70, "horizon": "12-24 months"}
            for index in range(1, 4)
        ],
    }

    signals = bp._build_account_signal_map(payload, services)
    lanes = [signal["signal_lane"] for signal in signals]

    assert len(signals) == 10
    assert lanes[:3] == ["activation_hypothesis"] * 3
    assert lanes.count("account_fact") == 2
    assert lanes.count("financial_capacity") == 1
    assert lanes.count("external_pressure") == 2
    assert lanes.count("contradiction_or_gap") == 1
    assert lanes.count("strategic_context") == 1
    assert [signal["id"] for signal in signals[:3]] == ["SIG-01", "SIG-02", "SIG-03"]
    assert all(signal["source"] != "scanner_pitch" for signal in signals)


def test_hypothesis_registry_preserves_stable_activation_lineage(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    payload = {
        "account_signal_map": [
            {
                "id": f"SIG-0{index}",
                "signal_lane": "activation_hypothesis",
                "source": "scanner_activation_pair",
                "activation_pair_index": index,
                "theme": f"Theme {index}",
                "why": f"Why {index}",
                "what_to_do": f"Outcome {index}",
                "gate": "allowed_as_hypothesis",
            }
            for index in range(1, 4)
        ] + [
            {
                "id": "SIG-04",
                "signal_lane": "account_fact",
                "account_movement": "Theme evidence verifies an operating fact",
                "parent_evidence_ids": ["CED-01"],
                "gate": "allowed_as_fact",
                "independent_source_count": 1,
            },
            {
                "id": "SIG-05",
                "signal_lane": "external_pressure",
                "account_movement": "Theme pressure changes the external market",
                "parent_foresight_ids": ["FORCE-01"],
                "gate": "allowed_as_hypothesis",
            },
        ]
    }
    payload["required_sales_spine"] = bp._required_sales_spine(payload)
    registry = bp._build_hypothesis_registry(payload)

    assert [item["hypothesis_id"] for item in registry] == ["HYP-01", "HYP-02", "HYP-03"]
    assert [item["activation_signal_id"] for item in registry] == ["SIG-01", "SIG-02", "SIG-03"]
    assert all(item["parent_evidence_ids"] == ["CED-01"] for item in registry)
    assert all(item["parent_foresight_ids"] == ["FORCE-01"] for item in registry)


def test_service_validation_requires_exact_name_or_id_not_category_overlap(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    known = bp._known_service_names({"services": [{"service_id": "SVC-AI", "name": "AI Readiness Assessment", "category": "AI"}]})

    assert bp._service_match_exists({"service_id": "SVC-AI"}, known)
    assert bp._service_match_exists({"service_or_offer": "AI Readiness Assessment"}, known)
    assert not bp._service_match_exists({"service_or_offer": "AI", "why_relevant": "AI category overlap"}, known)
    assert not bp._service_match_exists({"service_or_offer": "Generic consulting", "safe_customer_wording": "AI Readiness Assessment may help"}, known)


def test_gate_field_action_replaces_the_unsafe_sentence_itself(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "recommended_sales_plays": [
            {"linked_signal_ids": ["SIG-01"], "hypothesis_id": "HYP-01", "safe_customer_wording": "Unsafe factual claim."}
        ]
    }
    applied, rejected = bp._apply_gate_field_actions(report, [{
        "object_id": "HYP-01",
        "path": "recommended_sales_plays[0].safe_customer_wording",
        "action": "replace",
        "replacement": "A question-led replacement.",
        "reason": "Original wording exceeded its evidence gate.",
    }])

    assert report["recommended_sales_plays"][0]["safe_customer_wording"] == "A question-led replacement."
    assert len(applied) == 1
    assert rejected == []


def test_unverified_general_summary_caps_publication_reliability(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "recommended_sales_plays": [{"gate": "allowed_as_hypothesis", "readiness_band": "Validate now", "play_classification": "strong_account_play"}],
        "evidence_confidence": {},
    }
    payload = {
        "verified_evidence": [{"id": f"CED-{index}"} for index in range(5)],
        "financial_sources": [{"id": "FIN-01"}],
        "account_signal_map": [{"id": "SIG-01"}],
        "evidence_sufficiency": {"status": "complete"},
        "scanner_outputs": {"general_summary_fact_check": {"verified": False, "false_or_unsupported_claims": []}},
    }

    status = bp._compute_reliability_status(report, payload, None, low_evidence=False)

    assert status["code"] == "C"
    assert status["publication_status"] == "internal_review_required"


def test_gate_review_packet_is_compact_and_excludes_full_payload(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "account_snapshot": {"text": "Account condition", "gate": "allowed_as_hypothesis", "evidence_basis": ["CED-01"]},
        "recommended_sales_plays": [{
            "linked_signal_ids": ["SIG-01"],
            "service_id": "SVC-01",
            "service_or_offer": "Service One",
            "trigger_to_offer_bridge": "Signal-backed bridge",
            "safe_customer_wording": "Explore whether the pressure is material.",
            "gate": "allowed_as_discovery_question",
        }],
    }
    payload = {
        "account_signal_map": [{"id": "SIG-01", "theme": "Pressure theme", "gate": "allowed_as_hypothesis"}],
        "verified_evidence": [{
            "id": "CED-01",
            "gate": "allowed_as_hypothesis",
            "generation_eligible": True,
            "entity_scope": "canonical_legal_entity",
            "source_quote": "A concise source excerpt.",
        }],
        "services": [{"service_id": "SVC-01", "name": "Service One", "large_internal_blob": "x" * 200000}],
        "source_ledger_recovery_records": [],
        "required_sales_spine": [{"id": "SIG-01", "hypothesis_id": "HYP-01"}],
        "evidence_sufficiency": {"status": "partial"},
        "scanner_outputs": {"strategic_synthesis": "x" * 200000},
    }

    packet = bp._build_gate_review_packet(report, payload)
    serialized = json.dumps(packet)

    assert len(serialized) < bp.BUYING_GATE_PACKET_CHAR_LIMIT
    assert "large_internal_blob" not in serialized
    assert "strategic_synthesis" not in serialized
    assert packet["claims"]
    assert packet["service_bridges"][0]["service_id"] == "SVC-01"


def test_gate_unavailable_is_distinct_and_caps_reliability(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    review = bp._normalize_gate_review({}, unavailable_note="Verifier timed out.")
    report = {
        "recommended_sales_plays": [{
            "gate": "allowed_as_hypothesis",
            "readiness_band": "Validate now",
            "play_classification": "strong_account_play",
        }],
        "evidence_confidence": {},
    }
    payload = {
        "verified_evidence": [{"id": f"CED-{index}"} for index in range(5)],
        "financial_sources": [{"id": "FIN-01"}],
        "account_signal_map": [{"id": "SIG-01"}],
        "evidence_sufficiency": {"status": "complete"},
        "scanner_outputs": {"general_summary_fact_check": {"verified": True, "false_or_unsupported_claims": []}},
    }

    status = bp._compute_reliability_status(report, payload, review, low_evidence=False)

    assert review["status"] == "unavailable"
    assert review["approved"] is False
    assert status["code"] == "C"
    assert any("unavailable" in reason.lower() for reason in status["reasons"])


def test_renderer_uses_deterministic_sufficiency_instead_of_numeric_confidence(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    html = bp.render_buying_potential_html({
        "companyName": "TestCo",
        "reliability_status": bp._status_template("C"),
        "publication_status": "internal_review_required",
        "quality_controls": {"reasoning_verification": {"evidence_sufficiency": "partial"}},
        "overall_confidence": None,
        "account_snapshot": {},
        "buying_potential_thesis": {},
        "sales_spine": {},
        "evidence_confidence": {},
    })

    assert "Evidence stance: constrained / question-led" in html
    assert "Confidence: 0" not in html


def test_need_hypothesis_preserves_explicit_same_signal_future_pressure(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    need = bp._need_hypothesis_from_signal({
        "id": "SIG-02",
        "play_type": "foresight_driven",
        "theme": "Industrial AI readiness",
        "why": "Current account condition requires validation.",
        "need_hypothesis": {
            "future_pressure": "Industrial AI is moving from pilots into governed production workflows.",
            "market_context": "Industrial AI adoption raises data, governance, and operating-model requirements.",
        },
    })

    assert need["future_pressure"].startswith("Industrial AI is moving")
    assert need["market_context"].startswith("Industrial AI adoption")
    assert need["account_unknown"] == "Current account condition requires validation."


def test_proactive_external_trend_is_not_labeled_as_account_condition(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    need = bp._need_hypothesis_from_signal(
        {
            "id": "SIG-03",
            "play_type": "proactive_opportunity",
            "theme": "Selective automation and agentic AI",
            "why": "Agentic AI is increasingly capable of supporting selected retail workflows.",
            "external_pressure": "Agentic AI is increasingly capable of supporting selected retail workflows.",
            "what_to_do": "Identify one bounded workflow whose relevance can be tested with the accountable owner.",
            "what_progress_shows": "Priority workflows and measurable success criteria are agreed.",
        }
    )

    assert need["account_fact"] == ""
    assert need["account_unknown"].startswith("No account-specific condition is established")
    assert need["external_pressure"].startswith("Agentic AI is increasingly capable")
    assert need["causal_bridge_status"] == "not_established"
    assert need["validation_outcome"].startswith("Priority workflows")
    assert "Account condition: Agentic AI" not in need["combined_activation_hypothesis"]
    assert "Account boundary:" in need["combined_activation_hypothesis"]
    assert "Response direction to validate—not delivery scope:" in need["combined_activation_hypothesis"]


def test_current_status_statement_splits_fact_unknown_and_consequence(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    need = bp._need_hypothesis_from_signal(
        {
            "id": "SIG-01",
            "play_type": "current_status",
            "theme": "Omnichannel operating foundation",
            "why": (
                "Customers already interact with Tokmanni through online and store-availability features, "
                "but whether the supporting architecture is sufficiently interoperable is not substantiated. "
                "Leaving this unclarified risks duplicated investment or difficult-to-measure change."
            ),
            "what_to_do": "Could we validate the current ownership, dependencies, and decision criteria?",
            "what_progress_shows": "The accountable owner and evidence needed for a decision are clear.",
        }
    )

    assert need["account_fact"].startswith("Customers already interact with Tokmanni")
    assert need["account_unknown"].startswith("whether the supporting architecture")
    assert need["potential_consequence"].startswith("Leaving this unclarified")
    assert need["causal_bridge_status"] == "not_required"
    assert "External pressure:" not in need["combined_activation_hypothesis"]


def test_activation_mapping_keeps_validation_outcome_out_of_business_consequence(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    progress = "Priority workflows and measurable success criteria are agreed."
    payload = {
        "scanner_outputs": {
            "activation_pairs": [
                {
                    "id": "SIG-03",
                    "theme_type": "foresight_driven",
                    "theme": "Selective automation and agentic AI",
                    "why": "Agentic AI is increasingly capable of supporting selected retail workflows.",
                    "external_pressure": "Agentic AI is increasingly capable of supporting selected retail workflows.",
                    "what_to_do": "Test one bounded workflow with the accountable owner.",
                    "what_progress_shows": progress,
                }
            ]
        },
        "verified_evidence": [],
        "financial_sources": [],
        "foresight_forces": [],
    }

    signal_map = bp._build_account_signal_map(payload, [])
    signal = next(item for item in signal_map if item.get("source") == "scanner_activation_pair")

    assert signal["validation_outcome"] == progress
    assert signal["business_consequence"] != progress
    assert signal["need_hypothesis"]["validation_outcome"] == progress


def test_renderer_exposes_semantic_roles_and_discovery_timing_boundary(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    need = bp._need_hypothesis_from_signal(
        {
            "id": "SIG-03",
            "play_type": "proactive_opportunity",
            "theme": "Selective automation and agentic AI",
            "why": "Agentic AI is increasingly capable of supporting selected retail workflows.",
            "external_pressure": "Agentic AI is increasingly capable of supporting selected retail workflows.",
            "what_to_do": "Could we validate one bounded workflow with the accountable owner?",
            "what_progress_shows": "Priority workflows and measurable success criteria are agreed.",
        }
    )
    report = {
        "companyName": "Tokmanni Group",
        "sales_spine": {
            "executive_thesis": "A conditional discovery hypothesis.",
            "why_now": "Validate relevance before assuming strength or weakness.",
            "recommended_entry_wedge": "A bounded discovery conversation.",
        },
        "recommended_sales_plays": [
            {
                "signal_id": "SIG-03",
                "title": "Selective automation and agentic AI",
                "play_type": "proactive_opportunity",
                "why": "Agentic AI is increasingly capable of supporting selected retail workflows.",
                "what_to_do": "Could we validate one bounded workflow with the accountable owner?",
                "what_progress_shows": "Priority workflows and measurable success criteria are agreed.",
                "need_hypothesis": need,
            }
        ],
        "pressure_signals": [
            {
                "signal_id": "SIG-03",
                "title": "Selective automation and agentic AI",
                "play_type": "proactive_opportunity",
                "account_unknown": need["account_unknown"],
                "external_pressure": need["external_pressure"],
                "causal_bridge": need["causal_bridge"],
                "potential_consequence": need["potential_consequence"],
                "validation_outcome": need["validation_outcome"],
            }
        ],
    }

    html = bp.render_buying_potential_html(report)

    assert "Account condition: Agentic AI" not in html
    assert "Account boundary" in html
    assert "External pressure" in html
    assert "Potential consequence if confirmed" in html
    assert "priority and sequencing of discovery" in html
    assert "confirmed budget" in html


def test_hypothesis_registry_keeps_activation_pressure_authoritative(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    payload = {
        "account_signal_map": [
            {
                "id": "SIG-02",
                "hypothesis_id": "HYP-02",
                "signal_lane": "activation_hypothesis",
                "source": "scanner_activation_pair",
                "activation_pair_index": 2,
                "play_type": "foresight_driven",
                "theme": "Industrial AI readiness",
                "why": "Industrial AI is moving from pilots into governed production workflows.",
                "external_pressure": "Industrial AI is moving from pilots into governed production workflows.",
                "gate": "allowed_as_hypothesis",
                "need_hypothesis": {
                    "current_condition": "Current AI and data readiness is not established.",
                    "future_pressure": "Industrial AI is moving from pilots into governed production workflows.",
                    "likely_business_consequence": "Prioritization may become harder without a shared readiness view.",
                },
            },
            {
                "id": "SIG-07",
                "signal_lane": "external_pressure",
                "account_movement": "Growth and capacity movement creates generic scaling pressure.",
                "gate": "allowed_as_hypothesis",
            },
        ],
    }
    payload["required_sales_spine"] = bp._required_sales_spine(payload)
    payload["hypothesis_registry"] = bp._build_hypothesis_registry(payload)
    bp._synchronize_hypothesis_contract(payload)

    record = payload["hypothesis_registry"][0]
    assert record["external_pressure"].startswith("Industrial AI is moving")
    assert payload["account_signal_map"][0]["need_hypothesis"]["future_pressure"].startswith("Industrial AI is moving")
    assert payload["required_sales_spine"][0]["external_pressure"].startswith("Industrial AI is moving")


def test_pressure_inference_does_not_read_ai_inside_sustainability(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    ai_pressure = bp._infer_pressure("Industrial AI readiness and adaptive operations")
    passport_pressure = bp._infer_pressure("Energy productivity and sustainability product passport readiness")

    assert ai_pressure[0] == "Industrial AI and governed-readiness pressure"
    assert passport_pressure[0] == "Energy-productivity and product-traceability pressure"


def test_hygiene_cleanup_repairs_stopped_sentence_artifacts(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    assert bp._clean_generated_text("enterprise-AI in nature,.") == "enterprise-AI in nature."
    assert bp._clean_generated_text("The balance sheet remains strongly.") == "The balance sheet remains strong."
    assert bp._clean_generated_text("It may not scale at the pace.") == "It may not scale at the required pace."
    label = bp._clip_label("Energy productivity and passport readiness could turn margin pressure and sustainability claims into measurable advantage", 80)
    assert "sustai" not in label
    assert label.endswith("...")


def test_gate_action_propagates_to_repeated_claim_occurrences(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    unsafe = "Ensto's thin operating margin creates urgency."
    replacement = "Entity-scoped financial context makes economic materiality a discovery question."
    report = {
        "sales_spine": {"account_thesis": unsafe, "linked_hypothesis_ids": ["HYP-03"]},
        "recommended_sales_plays": [{
            "hypothesis_id": "HYP-03",
            "linked_signal_ids": ["SIG-03"],
            "buying_hypothesis": unsafe,
            "trigger": unsafe,
        }],
    }
    applied, rejected = bp._apply_gate_field_actions(report, [{
        "object_id": "HYP-03",
        "path": "recommended_sales_plays[0].buying_hypothesis",
        "action": "replace",
        "replacement": replacement,
        "reason": "The margin source is entity-scoped and question-only.",
    }])
    propagated = bp._propagate_gate_field_actions(report, applied)

    assert rejected == []
    assert propagated == 2
    assert report["sales_spine"]["account_thesis"] == replacement
    assert report["recommended_sales_plays"][0]["trigger"] == replacement


def test_blocked_claim_removal_catches_close_sentence_variant(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "sales_spine": {
            "account_thesis": "Ensto's sustainability messaging remains only a brand-level claim. A valid question remains about traceability."
        }
    }

    removed = bp._remove_blocked_claims_from_public_sections(
        report,
        ["Sustainability messaging is only a brand-level claim."],
    )

    assert removed == 1
    assert "brand-level claim" not in report["sales_spine"]["account_thesis"]
    assert "valid question" in report["sales_spine"]["account_thesis"]


def test_weak_modernization_keyword_overlap_is_not_rendered_as_a_lens(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    tactics = [{
        "concept": "Beyond Budgeting",
        "category": "Governance",
        "problem_pattern": "Annual budgeting constrains adaptive planning.",
        "solution_mechanism": "Use rolling planning and delegated decisions.",
        "match_text": "governance planning budgeting decisions",
    }]

    assert bp._match_modernization_tactics("A generic governance question", tactics) == []
    assert bp._match_modernization_tactics("Planning and budgeting constraints", tactics)


def test_title_grammar_and_risk_theme_hygiene(monkeypatch):
    bp = load_buying_potential(monkeypatch)

    assert bp._normalize_discovery_play_title("Energy productivity.") == "Energy productivity discovery play"
    assert bp._clean_generated_text("Validate whether if timing exists.") == "Validate whether timing exists."
    assert bp._dedupe_text_items(["Repeated risk", "Repeated risk", "Different risk"]) == ["Repeated risk", "Different risk"]


def test_final_language_qa_repairs_deterministic_defects_and_hides_empty_fields(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "companyName": "Ensto",
        "buying_potential_thesis": {
            "text": "The qualification issue is whether because the promoted evidence remains incomplete",
        },
        "recommended_sales_plays": [{
            "play_name": "Energy productivity and passport readiness and",
            "safe_customer_wording": "",
            "buying_hypothesis": "A complete hypothesis remains available.",
        }],
        "evidence_confidence": {"report_quality_notes": []},
    }

    result = bp._run_final_language_qa(report, run_model=False)

    assert result["buying_potential_thesis"]["text"] == "The qualification issue is that the promoted evidence remains incomplete."
    assert "safe_customer_wording" not in result["recommended_sales_plays"][0]
    assert result["recommended_sales_plays"][0]["play_name"] == ""
    assert result["language_quality"]["remaining_issue_count"] == 0


def test_final_language_qa_uses_sonnet_batches_for_remaining_fragments(monkeypatch):
    bp = load_buying_potential(monkeypatch)
    report = {
        "companyName": "Ensto",
        "sales_spine": {"account_thesis": "The operating question remains because."},
        "evidence_confidence": {"report_quality_notes": []},
    }
    calls = []

    def fake_stage(_prompt, chain, **kwargs):
        calls.append((tuple(chain or ()), kwargs["log_label"]))
        item = bp._language_public_items(report)[0]
        return ({
            "repairs": [{
                "qa_id": item["qa_id"],
                "path": item["path"],
                "action": "replace",
                "replacement": "The operating question remains open because current evidence is incomplete.",
                "reason": "Completed the causal sentence without adding a claim.",
            }],
            "notes": [],
        }, "claude-sonnet-5")

    monkeypatch.setattr(bp, "_call_gate_stage", fake_stage)
    result = bp._run_final_language_qa(report)

    assert calls[0][0][0] == "claude-sonnet-5"
    assert result["sales_spine"]["account_thesis"].endswith("incomplete.")
    assert result["language_quality"]["model_repair_count"] == 1
    assert result["language_quality"]["remaining_issue_count"] == 0
