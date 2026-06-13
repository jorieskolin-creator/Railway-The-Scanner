"""
llm_clients.py — Shared OpenAI / Anthropic helpers.

This module intentionally contains no Google-model dependency. It centralizes
the runtime model calls used by scanner research, verification, synthesis, and
maintenance workflows.
"""

from __future__ import annotations

import json
import os
from typing import Any


CLAUDE_OPUS_MODEL = "claude-opus-4-8"
CLAUDE_SONNET_MODEL = "claude-sonnet-4-6"
CLAUDE_HAIKU_MODEL = (os.environ.get("CLAUDE_HAIKU_MODEL") or "claude-haiku-4-5").strip().strip('"').strip("'")
GPT_REASONING_MODEL = "gpt-5.5"
GPT_VERIFIER_MODEL = "gpt-5.4"


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
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def call_openai_text(
    prompt: str,
    model: str = GPT_REASONING_MODEL,
    *,
    max_output_tokens: int = 8000,
    timeout: int = 90,
    web_search: bool = False,
    search_context_size: str = "medium",
) -> str:
    api_key = openai_api_key()
    if not api_key:
        raise ValueError("No GPT_API_KEY configured")
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout)
    kwargs: dict[str, Any] = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
    }
    if web_search:
        kwargs["tools"] = [{"type": "web_search", "search_context_size": search_context_size}]
    response = client.responses.create(**kwargs)
    return extract_openai_text(response)


def call_anthropic_text(
    prompt: str,
    model: str = CLAUDE_SONNET_MODEL,
    *,
    max_tokens: int = 8000,
    timeout: int | None = None,
    web_search: bool = False,
    max_searches: int = 8,
    thinking_budget: int | None = None,
) -> str:
    api_key = anthropic_api_key()
    if not api_key:
        raise ValueError("No ANTHROPIC_API_KEY configured")
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout) if timeout else anthropic.Anthropic(api_key=api_key)
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
    if thinking_budget:
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["extra_body"] = {"output_config": {"effort": "medium"}}
    response = client.messages.create(**kwargs)
    return extract_anthropic_text(response)


def call_json_model_chain(
    prompt: str,
    model_chain: tuple[str, ...] | list[str],
    *,
    max_tokens: int = 1200,
    timeout: int = 60,
) -> tuple[str, str]:
    errors: list[str] = []
    for model in model_chain:
        try:
            if model.startswith("gpt-"):
                return call_openai_text(prompt, model, max_output_tokens=max_tokens, timeout=timeout), model
            if model.startswith("claude-"):
                return call_anthropic_text(prompt, model, max_tokens=max_tokens, timeout=timeout), model
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
