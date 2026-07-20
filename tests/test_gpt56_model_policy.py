import importlib
import sys
import types

import pytest


def test_model_policy_defaults_to_gpt56_sol_without_haiku(monkeypatch):
    for name in (
        "SCANNER_DEFAULT_FRONTIER_MODEL",
        "SCANNER_PREMIUM_MODEL",
        "SCANNER_DETAILED_COMPLETION_MODEL",
        "SCANNER_ENABLE_GPT56_SOL_TEST",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("model_policy", None)
    policy = importlib.import_module("model_policy")

    assert policy.GPT_REASONING_MODEL == "gpt-5.6-sol"
    assert policy.GPT_PRO_MODEL == "gpt-5.6-sol"
    assert policy.GPT_PRO_REASONING_MODE == "pro"
    assert policy.DETAILED_COMPLETION_MODEL == "gpt-5.6-sol"
    assert all("haiku" not in model.lower() for model in policy.buying_generation_chain())
    assert policy.policy_for("buying_insight_generation")["qualification"]["max_output_tokens"] == 9000
    assert policy.policy_for("activation_strategic_synthesis")["max_output_tokens"] == 16000


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


def test_openai_helper_sends_sol_pro_context_verbosity_and_schema(monkeypatch):
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
        model="gpt-5.6-sol",
        reasoning_mode="pro",
        reasoning_effort="high",
        text_verbosity="high",
        json_schema=schema,
        json_schema_name="test_contract",
    )

    assert text == '{"result":"ok"}'
    assert captured["reasoning"] == {"mode": "pro", "effort": "high", "context": "current_turn"}
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
            model="gpt-5.6-sol",
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
