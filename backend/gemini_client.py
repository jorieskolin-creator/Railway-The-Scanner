"""Narrow Gemini helper used only by Foresight KB harvesting."""

from __future__ import annotations

import os

from llm_clients import clean_env_var


GEMINI_HARVEST_MODEL = (
    os.environ.get("GEMINI_HARVEST_MODEL") or "gemini-3.1-pro-preview"
).strip().strip('"').strip("'")


def gemini_api_key() -> str:
    return clean_env_var(os.environ.get("GEMINI_API_KEY") or "")


def call_gemini_harvest_text(
    prompt: str,
    *,
    model: str = GEMINI_HARVEST_MODEL,
    timeout: int = 90,
    max_output_tokens: int = 7000,
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
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.3,
            max_output_tokens=max_output_tokens,
        ),
    )
    return str(getattr(response, "text", "") or "").strip()
