"""
llm_clients.py — Shared OpenAI / Anthropic helpers.

This module intentionally contains no Google-model dependency. It centralizes
the runtime model calls used by scanner research, verification, synthesis, and
maintenance workflows.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from model_policy import (
    CLAUDE_SONNET_MODEL,
    GPT_PRO_MODEL,
    GPT_REASONING_MODEL,
    GPT_VERIFIER_MODEL,
    dedupe_chain,
    resolve_model_id,
)


def clean_env_var(value: str | None) -> str:
    if not value:
        return ""
    if "=" in value:
        try:
            value = value.split("=", 1)[1]
        except Exception:
            pass
    return value.strip().strip('"').strip("'")


def openai_api_key() -> str:
    return clean_env_var(os.environ.get("GPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "")


def anthropic_api_key() -> str:
    return clean_env_var(os.environ.get("ANTHROPIC_API_KEY") or "")


def extract_openai_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text).strip()
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_text = getattr(content, "text", None)
            if content_text:
                parts.append(str(content_text))
    return "\n".join(parts).strip()


def extract_anthropic_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, dict):
            if block.get("type") not in {None, "text"}:
                continue
            text = block.get("text")
        elif getattr(block, "type", None) not in {None, "text"}:
            continue
        else:
            text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _openai_response_diagnostics(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage", {}) or {}
    output_details = _value(usage, "output_tokens_details", {}) or {}
    incomplete = _value(response, "incomplete_details", {}) or {}
    return {
        "status": str(_value(response, "status", "unknown") or "unknown"),
        "incomplete_reason": str(_value(incomplete, "reason", "") or ""),
        "input_tokens": int(_value(usage, "input_tokens", 0) or 0),
        "output_tokens": int(_value(usage, "output_tokens", 0) or 0),
        "reasoning_tokens": int(_value(output_details, "reasoning_tokens", 0) or 0),
    }


def _anthropic_response_diagnostics(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage", {}) or {}
    output_details = _value(usage, "output_tokens_details", {}) or {}
    return {
        "stop_reason": str(_value(response, "stop_reason", "unknown") or "unknown"),
        "input_tokens": int(_value(usage, "input_tokens", 0) or 0),
        "output_tokens": int(_value(usage, "output_tokens", 0) or 0),
        "thinking_tokens": int(_value(output_details, "thinking_tokens", 0) or 0),
    }


def _structured_output_format(schema: dict[str, Any], name: str) -> dict[str, Any]:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name or "scanner_output")[:64]
    return {
        "type": "json_schema",
        "name": safe_name or "scanner_output",
        "strict": True,
        "schema": schema,
    }


def call_openai_text(
    prompt: str,
    model: str = GPT_REASONING_MODEL,
    *,
    max_output_tokens: int = 8000,
    timeout: int = 90,
    web_search: bool = False,
    search_context_size: str = "medium",
    max_retries: int = 2,
    reasoning_effort: str | None = None,
    reasoning_mode: str | None = None,
    reasoning_context: str | None = "current_turn",
    text_verbosity: str | None = None,
    json_schema: dict[str, Any] | None = None,
    json_schema_name: str = "scanner_output",
    log_label: str = "OpenAI",
) -> str:
    api_key = openai_api_key()
    if not api_key:
        raise ValueError("No GPT_API_KEY configured")
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
    kwargs: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }
    if web_search:
        kwargs["tools"] = [{"type": "web_search", "search_context_size": search_context_size}]
    reasoning: dict[str, Any] = {}
    if reasoning_mode:
        # Pro is a Responses API reasoning mode on the configured Sol model.
        reasoning["mode"] = reasoning_mode
    if reasoning_effort:
        reasoning["effort"] = reasoning_effort
    if reasoning_context and model.startswith("gpt-5.6"):
        reasoning["context"] = reasoning_context
    if reasoning:
        kwargs["reasoning"] = reasoning
    text_config: dict[str, Any] = {}
    if text_verbosity:
        text_config["verbosity"] = text_verbosity
    if json_schema:
        text_config["format"] = _structured_output_format(json_schema, json_schema_name)
    if text_config:
        kwargs["text"] = text_config

    started = time.time()
    response = client.responses.create(**kwargs)
    diagnostics = _openai_response_diagnostics(response)
    elapsed_ms = int((time.time() - started) * 1000)
    print(
        f"   [{log_label}] model={model}, status={diagnostics['status']}, "
        f"input_tokens={diagnostics['input_tokens']}, output_tokens={diagnostics['output_tokens']}, "
        f"reasoning_tokens={diagnostics['reasoning_tokens']}, elapsed_ms={elapsed_ms}, "
        f"effort={reasoning_effort or '-'}, mode={reasoning_mode or '-'}, "
        f"verbosity={text_verbosity or '-'}, structured={bool(json_schema)}"
    )
    if diagnostics["status"] == "incomplete":
        reason = diagnostics["incomplete_reason"] or "unspecified"
        raise ValueError(
            f"OpenAI response incomplete: reason={reason}, output_tokens={diagnostics['output_tokens']}, "
            f"reasoning_tokens={diagnostics['reasoning_tokens']}, max_output_tokens={max_output_tokens}"
        )
    text = extract_openai_text(response)
    if not text:
        raise ValueError(f"OpenAI returned no usable text (status={diagnostics['status']})")
    return text


def call_anthropic_text(
    prompt: str,
    model: str = CLAUDE_SONNET_MODEL,
    *,
    max_tokens: int = 8000,
    timeout: int | None = None,
    web_search: bool = False,
    max_searches: int = 8,
    max_retries: int = 2,
    thinking_budget: int | None = None,
    reasoning_effort: str | None = None,
    json_schema: dict[str, Any] | None = None,
    json_schema_name: str = "scanner_output",
    log_label: str = "Anthropic",
) -> str:
    model = resolve_model_id(model)
    api_key = anthropic_api_key()
    if not api_key:
        raise ValueError("No ANTHROPIC_API_KEY configured")
    import anthropic

    client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": max(0, int(max_retries))}
    if timeout:
        client_kwargs["timeout"] = timeout
    client = anthropic.Anthropic(**client_kwargs)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if web_search:
        kwargs["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }]
    # Backward compatibility: older call sites pass a numeric thinking_budget.
    # Treat it as a request for medium effort under the current output config.
    if thinking_budget and not reasoning_effort:
        reasoning_effort = "medium"
    output_config: dict[str, Any] = {}
    if reasoning_effort:
        output_config["effort"] = reasoning_effort
    if json_schema:
        output_config["format"] = {
            "type": "json_schema",
            "schema": json_schema,
        }
    if output_config:
        kwargs["output_config"] = output_config

    started = time.time()
    response = client.messages.create(**kwargs)
    diagnostics = _anthropic_response_diagnostics(response)
    elapsed_ms = int((time.time() - started) * 1000)
    print(
        f"   [{log_label}] model={model}, stop_reason={diagnostics['stop_reason']}, "
        f"input_tokens={diagnostics['input_tokens']}, output_tokens={diagnostics['output_tokens']}, "
        f"thinking_tokens={diagnostics['thinking_tokens']}, elapsed_ms={elapsed_ms}, "
        f"effort={reasoning_effort or '-'}, structured={bool(json_schema)}, "
        f"schema={json_schema_name if json_schema else '-'}"
    )
    if diagnostics["stop_reason"] == "max_tokens":
        raise ValueError(
            f"Anthropic response incomplete: stop_reason=max_tokens, "
            f"output_tokens={diagnostics['output_tokens']}, max_tokens={max_tokens}"
        )
    text = extract_anthropic_text(response)
    if not text:
        raise ValueError(
            f"Anthropic returned no usable text (stop_reason={diagnostics['stop_reason']})"
        )
    return text


def call_json_model_chain(
    prompt: str,
    model_chain: tuple[str, ...] | list[str],
    *,
    max_tokens: int = 1200,
    timeout: int = 60,
    reasoning_effort: str | None = None,
    validate_json: bool = False,
    required_fields: tuple[str, ...] | list[str] | None = None,
    reasoning_mode: str | None = None,
    reasoning_context: str | None = "current_turn",
    text_verbosity: str | None = None,
    json_schema: dict[str, Any] | None = None,
    json_schema_name: str = "scanner_json",
    log_label: str = "OpenAI JSON",
) -> tuple[str, str]:
    errors: list[str] = []
    for model in dedupe_chain(*model_chain):
        try:
            if model.startswith("gpt-"):
                text = call_openai_text(
                    prompt,
                    model,
                    max_output_tokens=max_tokens,
                    timeout=timeout,
                    reasoning_effort=reasoning_effort,
                    reasoning_mode=reasoning_mode,
                    reasoning_context=reasoning_context,
                    text_verbosity=text_verbosity,
                    json_schema=json_schema,
                    json_schema_name=json_schema_name,
                    log_label=log_label,
                )
                if validate_json:
                    parsed = parse_json_object(text)
                    missing = [field for field in (required_fields or []) if field not in parsed]
                    if missing:
                        raise ValueError(f"JSON missing required fields: {missing}")
                return text, model
            if model.startswith("claude-"):
                text = call_anthropic_text(
                    prompt,
                    model,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    reasoning_effort=reasoning_effort,
                    json_schema=json_schema,
                    json_schema_name=json_schema_name,
                    log_label=log_label,
                )
                if validate_json:
                    parsed = parse_json_object(text)
                    missing = [field for field in (required_fields or []) if field not in parsed]
                    if missing:
                        raise ValueError(f"JSON missing required fields: {missing}")
                return text, model
            raise ValueError(f"Unsupported model provider: {model}")
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:120]}")
    raise ValueError("All model calls failed: " + " | ".join(errors))


def parse_json_object(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
