"""
Production-grade LLM client.

Features:
  - Retry with exponential backoff + jitter (handles 429, 500, 502, 503, 504)
  - Streaming support (SSE async generator)
  - Provider fallback (primary → fallback)
  - Token counting (tiktoken-free approximation)
  - Reusable client (connection pooling)
  - Structured logging
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncGenerator, Optional

import httpx

from ..core.config import get_settings
from ..core.flags import get_flags

logger = logging.getLogger(__name__)

# ── Reusable client (connection pool) ────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def close_client():
    """Close the shared HTTP client. Call on app shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ── Provider config ──────────────────────────────────────────────────

def _get_provider_config(provider: Optional[str] = None) -> tuple[str, str, str]:
    """Returns (base_url, api_key, default_model) for a provider."""
    settings = get_settings()
    p = (provider or get_flags().llm_provider).lower()

    if p == "gemini":
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            settings.gemini_api_key,
            settings.default_llm_model,
        )
    elif p == "aiml":
        return settings.aiml_base_url, settings.aiml_api_key, settings.default_llm_model
    else:  # openai (fallback)
        return settings.openai_base_url, settings.openai_api_key, settings.default_llm_model


# ── Retry logic ──────────────────────────────────────────────────────

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 16.0


async def _retry_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    """Execute request with exponential backoff + jitter."""
    last_exc = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.request(method, url, **kwargs)

            if resp.status_code not in RETRYABLE_STATUS:
                if resp.status_code >= 400:
                    # Log the actual error body from the API before raising
                    try:
                        error_body = resp.text[:500]
                        logger.error("LLM API error %d: %s", resp.status_code, error_body)
                    except Exception:
                        pass
                resp.raise_for_status()
                return resp

            # Retryable error
            retry_after = resp.headers.get("retry-after")
            delay = float(retry_after) if retry_after else min(
                MAX_DELAY, BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
            )
            logger.warning(
                "LLM %d (attempt %d/%d) — retrying in %.1fs",
                resp.status_code, attempt + 1, MAX_RETRIES + 1, delay,
            )
            last_exc = httpx.HTTPStatusError(
                f"{resp.status_code}", request=resp.request, response=resp
            )
            await asyncio.sleep(delay)

        except httpx.TimeoutException as e:
            delay = min(MAX_DELAY, BASE_DELAY * (2 ** attempt) + random.uniform(0, 1))
            logger.warning(
                "LLM timeout (attempt %d/%d) — retrying in %.1fs",
                attempt + 1, MAX_RETRIES + 1, delay,
            )
            last_exc = e
            await asyncio.sleep(delay)

        except httpx.HTTPStatusError:
            raise  # Non-retryable HTTP errors
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = min(MAX_DELAY, BASE_DELAY * (2 ** attempt))
                await asyncio.sleep(delay)
            continue

    raise last_exc or RuntimeError("LLM request failed after retries")


# ── Main chat function ───────────────────────────────────────────────

async def chat(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[list[dict]] = None,
    tool_choice: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict:
    """
    Chat completion with retry + optional provider fallback.
    Returns the full API response as dict.
    """
    settings = get_settings()
    active_provider = (provider or get_flags().llm_provider).lower()
    base_url, api_key, default_model = _get_provider_config(provider)

    if not api_key:
        raise ValueError(
            f"No API key for LLM provider '{active_provider}'. "
            "Set GEMINI_API_KEY, AIML_API_KEY, or OPENAI_API_KEY."
        )

    payload: dict[str, Any] = {
        "model": model or default_model,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.default_llm_temperature,
        "max_tokens": max_tokens or settings.default_llm_max_tokens,
    }
    if tools:
        payload["tools"] = tools
        # Gemini supports parallel tool calls natively; only OpenAI needs this param
        if active_provider != "gemini":
            payload["parallel_tool_calls"] = True
    if tool_choice:
        payload["tool_choice"] = tool_choice

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    client = _get_client()

    try:
        resp = await _retry_request(client, "POST", url, json=payload, headers=headers)
        data = resp.json()
        elapsed = time.monotonic() - start

        # Log usage
        usage = data.get("usage", {})
        logger.info(
            "LLM %s: %dms | in=%d out=%d tokens | model=%s",
            "tool_call" if data.get("choices", [{}])[0].get("message", {}).get("tool_calls") else "chat",
            int(elapsed * 1000),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            payload["model"],
        )
        return data

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.error("LLM failed after %.1fs: %s", elapsed, e)

        # Try fallback provider if primary failed
        flags = get_flags()
        fallback = _get_fallback_provider(flags.llm_provider)
        if fallback and not provider:  # Only fallback once
            logger.info("Falling back to %s", fallback)
            return await chat(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                provider=fallback,
            )
        raise


def _get_fallback_provider(primary: str) -> Optional[str]:
    """Get fallback provider. Returns None if no fallback available."""
    settings = get_settings()
    candidates = []
    if primary != "gemini" and settings.gemini_api_key:
        candidates.append("gemini")
    if primary != "aiml" and settings.aiml_api_key:
        candidates.append("aiml")
    if primary != "openai" and settings.openai_api_key:
        candidates.append("openai")
    return candidates[0] if candidates else None


# ── Streaming ────────────────────────────────────────────────────────

STREAM_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
STREAM_FALLBACK_MODEL = "gemini-2.5-flash"


async def chat_stream(
    messages: list[dict],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[list[dict]] = None,
) -> AsyncGenerator[dict, None]:
    """
    Streaming chat completion with retry and model fallback.

    Strategy:
      1. Try with the requested model (default: gemini-3-flash-preview).
      2. On transient failure (429/5xx), retry ONCE after a short delay.
      3. If retry also fails, fallback to gemini-2.5-flash (no thinking mode,
         no thought_signature issues) for THIS REQUEST ONLY.
      4. The default model is never changed — fallback is per-request.

    Yields: {"content": str, "done": bool, "tool_calls": list|None}
    """
    settings = get_settings()
    base_url, api_key, default_model = _get_provider_config()

    if not api_key:
        yield {"content": "LLM not configured.", "done": True, "tool_calls": None}
        return

    resolved_model = model or default_model

    # ── Attempt 1: primary model ────────────────────────────────────
    chunks_or_error = await _stream_with_collect(
        base_url, api_key, resolved_model, messages, temperature, max_tokens, tools, settings,
    )
    if not isinstance(chunks_or_error, Exception):
        for chunk in chunks_or_error:
            yield chunk
        return

    primary_error = chunks_or_error
    logger.warning("LLM stream attempt 1 failed (%s): %s", resolved_model, primary_error)

    # ── Attempt 2: retry same model after delay ─────────────────────
    delay = 1.0 + random.uniform(0, 0.5)
    logger.info("LLM stream: retrying %s in %.1fs", resolved_model, delay)
    await asyncio.sleep(delay)

    chunks_or_error = await _stream_with_collect(
        base_url, api_key, resolved_model, messages, temperature, max_tokens, tools, settings,
    )
    if not isinstance(chunks_or_error, Exception):
        for chunk in chunks_or_error:
            yield chunk
        return

    retry_error = chunks_or_error
    logger.warning("LLM stream attempt 2 failed (%s): %s", resolved_model, retry_error)

    # ── Attempt 3: fallback to stable model (single attempt) ────────
    if resolved_model != STREAM_FALLBACK_MODEL:
        logger.info("LLM stream: falling back to %s (no thinking)", STREAM_FALLBACK_MODEL)
        chunks_or_error = await _stream_with_collect(
            base_url, api_key, STREAM_FALLBACK_MODEL, messages,
            temperature, max_tokens, tools, settings,
            extra_params={"reasoning_effort": "none"},
        )
        if not isinstance(chunks_or_error, Exception):
            for chunk in chunks_or_error:
                yield chunk
            return

        logger.error("LLM stream fallback also failed: %s", chunks_or_error)

    yield {
        "content": "The AI service is temporarily unavailable. Please try again in a moment.",
        "done": True,
        "tool_calls": None,
    }


async def _stream_with_collect(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: Optional[float],
    max_tokens: Optional[int],
    tools: Optional[list[dict]],
    settings: Any,
    extra_params: Optional[dict] = None,
) -> list[dict] | Exception:
    """
    Execute a single streaming request and collect all chunks.

    Returns list[dict] on success (the SSE chunks to yield to caller),
    or an Exception on failure. This two-phase pattern lets the caller
    decide whether to retry or fallback before yielding anything to the
    upstream consumer (important: once you yield a token, you can't un-yield it).
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.default_llm_temperature,
        "max_tokens": max_tokens or settings.default_llm_max_tokens,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    if extra_params:
        payload.update(extra_params)

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    client = _get_client()
    accumulated_tool_calls: dict[int, dict] = {}
    chunks: list[dict] = []
    total_content = ""

    logger.info("LLM stream start: model=%s messages=%d tools=%d",
                model, len(messages), len(tools) if tools else 0)

    try:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code in STREAM_RETRYABLE_STATUS:
                error_body = await resp.aread()
                error_text = error_body.decode("utf-8", errors="replace")[:500]
                logger.warning("LLM stream %d (model=%s): %s",
                               resp.status_code, model, error_text)
                return httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp,
                )

            if resp.status_code >= 400:
                error_body = await resp.aread()
                error_text = error_body.decode("utf-8", errors="replace")[:500]
                logger.error("LLM stream error %d (model=%s): %s",
                             resp.status_code, model, error_text)
                return httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp,
                )

            logger.info("LLM stream connected: model=%s", model)

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    logger.info("LLM stream done: model=%s content=%d chars",
                                model, len(total_content))
                    chunks.append({"content": "", "done": True, "tool_calls": None})
                    return chunks

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})

                # ── Accumulate tool calls ──────────────────────────
                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("id"):
                            accumulated_tool_calls[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            accumulated_tool_calls[idx]["function"]["name"] = func["name"]
                            logger.info("LLM stream: tool_call → %s", func["name"])
                        if func.get("arguments"):
                            accumulated_tool_calls[idx]["function"]["arguments"] += func["arguments"]

                        # Gemini 3 thinking models: thought_signature inside each tool_call
                        tc_extra = tc.get("extra_content")
                        if tc_extra:
                            accumulated_tool_calls[idx]["extra_content"] = tc_extra

                # ── Accumulate content ─────────────────────────────
                content = delta.get("content", "")
                if content:
                    total_content += content
                    chunks.append({"content": content, "done": False, "tool_calls": None})

                # ── Handle finish ──────────────────────────────────
                finish = chunk.get("choices", [{}])[0].get("finish_reason")

                if finish == "tool_calls" or (finish == "stop" and accumulated_tool_calls):
                    tool_names = [t["function"]["name"] for t in accumulated_tool_calls.values()]
                    logger.info("LLM stream finish: tool_calls=%s model=%s", tool_names, model)
                    chunks.append({
                        "content": "",
                        "done": True,
                        "tool_calls": list(accumulated_tool_calls.values()),
                    })
                    return chunks
                elif finish == "stop":
                    logger.info("LLM stream finish: stop | model=%s content=%d chars",
                                model, len(total_content))
                    chunks.append({"content": "", "done": True, "tool_calls": None})
                    return chunks

        # Stream ended without [DONE] or finish_reason — unusual but handle gracefully
        if chunks:
            chunks.append({"content": "", "done": True, "tool_calls": None})
            return chunks
        return RuntimeError("Stream ended without any data")

    except httpx.HTTPStatusError:
        raise  # Already handled above, won't reach here
    except (httpx.TimeoutException, httpx.ConnectError, OSError) as e:
        logger.warning("LLM stream network error (model=%s): %s", model, e)
        return e
    except Exception as e:
        logger.error("LLM stream unexpected error (model=%s): %s", model, e)
        return e


# ── Convenience functions ────────────────────────────────────────────

async def chat_simple(
    prompt: str,
    system: str = "",
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Send a prompt, get a string back. No tools."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await chat(
        messages=messages, model=model,
        temperature=temperature, max_tokens=max_tokens,
    )
    return response["choices"][0]["message"]["content"] or ""


async def chat_with_vision(
    prompt: str,
    image_urls: list[str],
    system: str = "",
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """Chat with image inputs (vision). Returns string response."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    response = await chat(messages=messages, model=model, max_tokens=max_tokens)
    return response["choices"][0]["message"]["content"] or ""


# ── Token estimation ─────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Estimate token count without tiktoken dependency.
    Rule of thumb: ~4 chars per token for English.
    """
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        total += 4  # message overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    total += estimate_tokens(part["text"])
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    total += 85  # base image token cost
        if msg.get("name"):
            total += estimate_tokens(msg["name"])
    total += 2  # priming
    return total
