"""
glm5_client.py — GLM-5 API client for web-search-grounded AI calls.

Uses the OpenAI-compatible endpoint at Z.ai (formerly ZhipuAI).
GLM-5 supports web search via the zhipuai-style tools parameter:
  tools=[{"type": "web_search", "web_search": {"search_result": True}}]

This module provides drop-in replacements for Gemini calls in:
  - Stakeholder Intel (person enrichment)
  - Forensic Scan (x_ray_search)

Environment variable: ZAI_API_KEY
"""

import concurrent.futures
import json
import os
import time

from openai import OpenAI


def _get_client():
    """Create an OpenAI client pointed at the Z.ai API."""
    api_key = os.environ.get("ZAI_API_KEY", "")
    if not api_key:
        raise ValueError("ZAI_API_KEY not set")
    return OpenAI(
        api_key=api_key,
        base_url="https://open.bigmodel.cn/api/paas/v4/",
    )


def glm5_web_search(
    prompt: str,
    label: str = "GLM-5",
    temperature: float = 0.0,
    max_tokens: int = 4000,
    timeout: int = 90,
) -> str:
    """
    Call GLM-5 with web search enabled and return the text response.

    Uses the zhipuai web_search tool type for grounded search results.
    Runs in a thread pool to enforce wall-clock timeout.

    Returns the response text, or empty string on failure.
    """
    client = _get_client()

    def _call():
        response = client.chat.completions.create(
            model="glm-5",
            messages=[
                {"role": "system", "content": "You are a professional research analyst. Search the web to find accurate, up-to-date information. Return only factual findings based on search results."},
                {"role": "user", "content": prompt},
            ],
            tools=[{
                "type": "web_search",
                "web_search": {
                    "search_result": True,
                }
            }],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        result = future.result(timeout=timeout)
        return result
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        print(f"   ⏰ [{label}] GLM-5 timed out after {timeout}s")
        return ""
    except Exception as e:
        print(f"   ❌ [{label}] GLM-5 error: {e}")
        return ""
    finally:
        pool.shutdown(wait=False)


def glm5_chat(
    prompt: str,
    label: str = "GLM-5",
    system_prompt: str = "You are a professional analyst. Respond only with the requested format.",
    temperature: float = 0.0,
    max_tokens: int = 8000,
    timeout: int = 120,
) -> str:
    """
    Call GLM-5 WITHOUT web search (pure text generation).
    Used for JSON structuring / repair tasks.
    """
    client = _get_client()

    def _call():
        response = client.chat.completions.create(
            model="glm-5",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        result = future.result(timeout=timeout)
        return result
    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        print(f"   ⏰ [{label}] GLM-5 timed out after {timeout}s")
        return ""
    except Exception as e:
        print(f"   ❌ [{label}] GLM-5 error: {e}")
        return ""
    finally:
        pool.shutdown(wait=False)
