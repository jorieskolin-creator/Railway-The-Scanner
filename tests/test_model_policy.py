import importlib
from pathlib import Path
import sys
import types

import pytest


def test_model_policy_defaults_and_fallback_order(monkeypatch):
    for name in (
        "SCANNER_DEFAULT_FRONTIER_MODEL",
        "SCANNER_PREMIUM_MODEL",
        "SCANNER_PREMIUM_REASONING_MODE",
        "SCANNER_VALIDATOR_MODEL",
        "SCANNER_DETAILED_COMPLETION_MODEL",
        "SCANNER_ENABLE_GPT56_SOL_TEST",
        "SCANNER_BUYING_GENERATION_MODEL",
        "SCANNER_MODEL_POLICY_VERSION",
        "CLAUDE_PREMIUM_MODEL",
        "CLAUDE_OPUS_MODEL",
        "CLAUDE_SONNET_MODEL",
        "SCANNER_GROUNDED_RESEARCH_MODEL",
        "GEMINI_HARVEST_MODEL",
        "GEMINI_FORENSIC_MODEL",
        "GEMINI_FAST_FALLBACK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("model_policy", None)
    policy = importlib.import_module("model_policy")

    assert policy.GPT_REASONING_MODEL == "gpt-6.1-sol"
    assert policy.GPT_PRO_MODEL == "gpt-6.1-sol"
    assert policy.GPT_VERIFIER_MODEL == "gpt-6.1-terra"
    assert policy.GPT_PRO_REASONING_MODE == "pro"
    assert policy.DETAILED_COMPLETION_MODEL == "gpt-6.1-sol"
    assert policy.CLAUDE_PREMIUM_MODEL == "claude-sonnet-5-5"
    assert policy.CLAUDE_SONNET_MODEL == "claude-sonnet-5-5"
    assert policy.GEMINI_GROUNDED_MODEL == "gemini-3.8-flash"
    assert policy.GEMINI_HARVEST_MODEL == policy.GEMINI_FORENSIC_MODEL == "gemini-3.8-flash"
    assert policy.GEMINI_FAST_FALLBACK_MODEL == "gemini-3.8-flash"
    assert policy.MODEL_POLICY_VERSION == "model-policy-v5-sonnet55"
    assert policy.buying_generation_chain("economy") == ("gpt-6.1-terra", "claude-sonnet-5-5")
    assert policy.buying_generation_chain("balanced") == ("claude-sonnet-5-5", "gpt-6.1-sol")
    assert policy.buying_generation_chain("quality") == ("gpt-6.1-sol", "claude-sonnet-5-5")
    assert policy.buying_generation_chain("board_grade") == ("gpt-6.1-sol", "claude-sonnet-5-5")
    assert policy.source_claim_chain() == ("gpt-6.1-terra", "claude-sonnet-5-5")
    assert policy.policy_for("ai_diagnosis") == {"effort": "high", "max_output_tokens": 8000}
    assert policy.policy_for("grounded_acquisition")["availability_fallback"] == ("gpt-6.1-sol",)
    assert policy.policy_for("gpt_web_repair")["fallback"] == "gemini-3.8-flash"
    assert policy.policy_for("buying_insight_generation")["qualification"]["max_output_tokens"] == 9000
    assert policy.policy_for("activation_strategic_synthesis")["max_output_tokens"] == 16000


def test_model_policy_allows_explicit_opus48_rollback(monkeypatch):
    monkeypatch.delenv("CLAUDE_PREMIUM_MODEL", raising=False)
    monkeypatch.setenv("CLAUDE_OPUS_MODEL", "claude-opus-4-8")
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("model_policy", None)
    try:
        policy = importlib.import_module("model_policy")
        assert policy.CLAUDE_PREMIUM_MODEL == "claude-opus-4-8"
    finally:
        sys.modules.pop("model_policy", None)


def test_deployment_model_pins_migrate_to_requested_replacements(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    monkeypatch.delenv("CLAUDE_PREMIUM_MODEL", raising=False)
    monkeypatch.setenv("SCANNER_DEFAULT_FRONTIER_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("SCANNER_VALIDATOR_MODEL", "gpt-5.4")
    monkeypatch.setenv("SCANNER_BUYING_GENERATION_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("CLAUDE_OPUS_MODEL", "claude-opus-5")
    monkeypatch.setenv("CLAUDE_SONNET_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("GEMINI_FORENSIC_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_FAST_FALLBACK_MODEL", "gemini-3.7-flash")
    sys.modules.pop("model_policy", None)
    try:
        policy = importlib.import_module("model_policy")
        assert policy.GPT_REASONING_MODEL == "gpt-6.1-sol"
        assert policy.GPT_VERIFIER_MODEL == "gpt-6.1-terra"
        assert policy.CLAUDE_PREMIUM_MODEL == "claude-sonnet-5-5"
        assert policy.CLAUDE_SONNET_MODEL == "claude-sonnet-5-5"
        assert policy.GEMINI_FORENSIC_MODEL == "gemini-3.8-flash"
        assert policy.policy_for("grounded_acquisition")["availability_fallback"] == ("gpt-6.1-sol", "gemini-3.7-flash")
        assert policy.buying_generation_chain("quality") == ("gpt-6.1-sol", "claude-sonnet-5-5")
    finally:
        sys.modules.pop("model_policy", None)


def test_active_backend_paths_do_not_pin_opus48():
    backend = Path(__file__).resolve().parents[1] / "backend"
    active_paths = (
        "model_policy.py",
        "main.py",
        "seed_manager.py",
        "foresight_observatory.py",
        "detailed_report.py",
    )

    for relative_path in active_paths:
        source = (backend / relative_path).read_text(encoding="utf-8")
        assert "claude-opus-4-8" not in source, relative_path


def _load_llm_clients(monkeypatch, response):
    monkeypatch.setenv("GPT_API_KEY", "test-key")
    monkeypatch.syspath_prepend("backend")

    captured = {}

    class Responses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return response

    class OpenAI:
        def __init__(self, **_kwargs):
            self.responses = Responses()

    openai = types.ModuleType("openai")
    openai.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", openai)
    sys.modules.pop("llm_clients", None)
    module = importlib.import_module("llm_clients")
    return module, captured


def test_openai_helper_sends_sol_pro_verbosity_and_schema(monkeypatch):
    usage = types.SimpleNamespace(
        input_tokens=100,
        output_tokens=80,
        output_tokens_details=types.SimpleNamespace(reasoning_tokens=35),
    )
    response = types.SimpleNamespace(
        status="completed",
        output_text='{"result":"ok"}',
        output=[],
        usage=usage,
        incomplete_details=None,
    )
    llm, captured = _load_llm_clients(monkeypatch, response)
    schema = {
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
        "additionalProperties": False,
    }

    text = llm.call_openai_text(
        "Return JSON",
        model="gpt-6.1-sol",
        reasoning_mode="pro",
        reasoning_effort="high",
        text_verbosity="high",
        json_schema=schema,
        json_schema_name="test_contract",
    )

    assert text == '{"result":"ok"}'
    assert captured["model"] == "gpt-6.1-sol"
    assert captured["reasoning"] == {"mode": "pro", "effort": "high"}
    assert captured["text"]["verbosity"] == "high"
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["strict"] is True
    assert captured["text"]["format"]["schema"] == schema
    assert "temperature" not in captured


def test_openai_helper_rejects_incomplete_response_before_parse(monkeypatch):
    usage = types.SimpleNamespace(
        input_tokens=100,
        output_tokens=5000,
        output_tokens_details=types.SimpleNamespace(reasoning_tokens=4900),
    )
    response = types.SimpleNamespace(
        status="incomplete",
        output_text="",
        output=[],
        usage=usage,
        incomplete_details=types.SimpleNamespace(reason="max_output_tokens"),
    )
    llm, _captured = _load_llm_clients(monkeypatch, response)

    with pytest.raises(ValueError, match="response incomplete.*max_output_tokens"):
        llm.call_openai_text(
            "Reason deeply",
            model="gpt-6.1-sol",
            max_output_tokens=5000,
            reasoning_effort="high",
        )


def test_detailed_completion_retries_sol_at_high_effort(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("detailed_report", None)
    detailed = importlib.import_module("detailed_report")
    monkeypatch.setattr(detailed, "_build_input_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(detailed, "_build_completion_prompt", lambda *_args, **_kwargs: "complete report")
    monkeypatch.setattr(
        detailed,
        "detailed_report_quality",
        lambda text, compact=False: {"complete": bool(text), "word_count": 900 if text else 0},
    )
    efforts = []

    def fake_call(*_args, **kwargs):
        efforts.append(kwargs.get("reasoning_effort"))
        if len(efforts) == 1:
            raise ValueError("incomplete response")
        return "Complete detailed report"

    monkeypatch.setattr(detailed, "call_openai_text", fake_call)

    text, model, quality = detailed.complete_detailed_report_draft({}, "TestCo", None, "short draft")

    assert efforts == ["medium", "high"]
    assert text == "Complete detailed report"
    assert model.endswith("completion-high")
    assert quality["complete"] is True
