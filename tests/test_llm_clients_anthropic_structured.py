import importlib
import sys
import types


def load_llm_clients(monkeypatch):
    monkeypatch.syspath_prepend("backend")
    sys.modules.pop("llm_clients", None)
    return importlib.import_module("llm_clients")


def test_anthropic_receives_structured_schema_effort_and_logs_completion(monkeypatch):
    llm = load_llm_clients(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}

    class Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"repairs": [], "notes": []}')],
                stop_reason="end_turn",
                usage=types.SimpleNamespace(
                    input_tokens=120,
                    output_tokens=30,
                    output_tokens_details=types.SimpleNamespace(thinking_tokens=10),
                ),
            )

    class Anthropic:
        def __init__(self, **_kwargs):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    schema = {
        "type": "object",
        "properties": {"repairs": {"type": "array", "items": {"type": "string"}}, "notes": {"type": "array", "items": {"type": "string"}}},
        "required": ["repairs", "notes"],
        "additionalProperties": False,
    }

    text = llm.call_anthropic_text(
        "Repair these fields.",
        model="claude-sonnet-5",
        max_tokens=4000,
        reasoning_effort="medium",
        json_schema=schema,
        json_schema_name="test_schema",
        log_label="Test Anthropic Structured",
    )

    assert text.startswith("{")
    assert captured["output_config"]["effort"] == "medium"
    assert captured["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_anthropic_max_tokens_is_rejected_as_incomplete(monkeypatch):
    llm = load_llm_clients(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class Messages:
        def create(self, **_kwargs):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"partial": true}')],
                stop_reason="max_tokens",
                usage=types.SimpleNamespace(input_tokens=100, output_tokens=4000),
            )

    class Anthropic:
        def __init__(self, **_kwargs):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))

    try:
        llm.call_anthropic_text("Test", model="claude-sonnet-5", max_tokens=4000)
    except ValueError as exc:
        assert "incomplete" in str(exc).lower()
    else:
        raise AssertionError("Expected max_tokens response to be rejected")
