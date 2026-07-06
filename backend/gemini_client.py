"""Narrow grounded Gemini helpers used by evidence-acquisition workflows."""

from __future__ import annotations

import os

from llm_clients import clean_env_var


GEMINI_HARVEST_MODEL = (
    os.environ.get("GEMINI_HARVEST_MODEL") or "gemini-3.1-pro-preview"
).strip().strip('"').strip("'")
GEMINI_FORENSIC_MODEL = (
    os.environ.get("GEMINI_FORENSIC_MODEL") or "gemini-3.1-pro-preview"
).strip().strip('"').strip("'")


def gemini_api_key() -> str:
    return clean_env_var(os.environ.get("GEMINI_API_KEY") or "")


def call_gemini_harvest_text(
    prompt: str,
    *,
    model: str = GEMINI_HARVEST_MODEL,
    timeout: int = 90,
    max_output_tokens: int = 7000,
    temperature: float = 0.3,
    thinking_level: str | None = None,
) -> str:
    """Run one grounded Gemini discovery call with a provider-native timeout."""
    api_key = gemini_api_key()
    if not api_key:
        raise ValueError("No GEMINI_API_KEY configured")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout * 1000),
    )
    config_kwargs = {
        "tools": [types.Tool(google_search=types.GoogleSearch())],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if thinking_level and hasattr(types, "ThinkingConfig"):
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level
            )
        except TypeError:
            pass

    try:
        generation_config = types.GenerateContentConfig(**config_kwargs)
    except TypeError:
        config_kwargs.pop("thinking_config", None)
        generation_config = types.GenerateContentConfig(**config_kwargs)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=generation_config,
    )
    return str(getattr(response, "text", "") or "").strip()


def call_gemini_forensic_text(
    prompt: str,
    *,
    model: str = GEMINI_FORENSIC_MODEL,
    timeout: int = 105,
    max_output_tokens: int = 3200,
    thinking_level: str | None = "low",
) -> str:
    """Run one jury-scoped grounded acquisition call."""
    return call_gemini_harvest_text(
        prompt,
        model=model,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        temperature=0.2,
        thinking_level=thinking_level,
    )
