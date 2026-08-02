"""Bailian (百炼 / DashScope compatible-mode) LLM provider.

Wire-level chat completions for the Bailian OpenAI-compatible endpoint.
Prompt building and domain orchestration live in the LLMService shell.
"""
import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx

from app.config import Settings
from app.services.providers._base import KIND_LLM, _HttpProviderBase, register_provider

logger = logging.getLogger(__name__)


@register_provider(KIND_LLM, "bailian")
class BailianLLMProvider(_HttpProviderBase):
    """OpenAI-compatible chat completions against DashScope compatible-mode."""

    provider_name = "bailian"
    REQUEST_TIMEOUT_SECONDS = 120.0
    MAX_CONNECTIONS = 100
    MAX_KEEPALIVE_CONNECTIONS = 50

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.base_url = settings.BAILIAN_BASE_URL
        self.api_key = settings.BAILIAN_API_KEY
        self.default_model = settings.BAILIAN_MODEL

    async def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        stream: bool = False,
    ) -> str:
        """Complete a chat conversation and return the assistant text.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Accepted for signature parity; the streaming path is
                chat_complete_stream.

        Returns:
            Generated response text
        """
        client = await self._get_client()

        if not self.api_key:
            raise ValueError("No API key configured")

        # Resolve the model from config (settings.BAILIAN_MODEL) when the
        # caller doesn't pin one. This is the single source of truth; the old
        # literal default in the signature silently overrode the config.
        model = model or self.default_model

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

        except httpx.HTTPError as e:
            # Retry with delay
            await asyncio.sleep(1)
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as retry_error:
                raise

    async def chat_complete_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Stream a chat completion.

        Args:
            messages: List of message dicts
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            enable_thinking: Qwen hybrid-thinking toggle (DashScope
                ``enable_thinking``). None = omit the param (legacy
                behavior); True = stream the model's reasoning before the
                answer; False = answer directly (faster first token).
                Non-thinking model tiers reject the param with HTTP 400 —
                handled below by dropping it and retrying once, so a stale
                toggle degrades to a normal answer instead of an error
                bubble.

        Yields:
            (kind, text) tuples where kind is "content" (answer body) or
            "thinking" (reasoning stream — only when thinking is enabled
            and the model supports it).
        """
        client = await self._get_client()

        # Resolve the model from config (settings.BAILIAN_MODEL) when the
        # caller doesn't pin one. This is the single source of truth; the old
        # literal default in the signature silently overrode the config.
        model = model or self.default_model

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async for item in self._stream_completions(client, url, headers, payload):
                yield item
        except httpx.HTTPStatusError as e:
            if (
                enable_thinking is not None
                and e.response is not None
                and e.response.status_code == 400
            ):
                # Non-thinking model tier: the provider rejects the
                # enable_thinking param. Drop it and retry ONCE so the user
                # gets a normal answer rather than an error bubble. Nothing
                # was yielded before raise_for_status fired, so the retry is
                # transparent to the caller.
                logger.warning(
                    "enable_thinking=%s rejected by provider (HTTP 400: %.200s); "
                    "retrying without it",
                    enable_thinking, e.response.text,
                )
                payload.pop("enable_thinking", None)
                try:
                    async for item in self._stream_completions(client, url, headers, payload):
                        yield item
                except Exception as retry_error:
                    yield ("content", f"\n[Error: {str(retry_error)}]")
            else:
                yield ("content", f"\n[Error: {str(e)}]")
        except Exception as e:
            yield ("content", f"\n[Error: {str(e)}]")

    async def _stream_completions(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Open one SSE stream and yield (kind, text) delta tuples.

        kind is "thinking" for the ``reasoning_content`` field (Qwen hybrid
        thinking) and "content" for the answer body. Raises on HTTP errors
        so the caller can decide whether a param-rejection retry applies.
        """
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # Providers send a trailing usage-only frame with an
                        # EMPTY choices list; guard before indexing [0] or
                        # we IndexError (which the outer handler would
                        # inject into the answer as "[Error: ...]").
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        # Qwen hybrid thinking emits the reasoning stream in
                        # `reasoning_content` BEFORE the answer body starts.
                        # Forward it under the "thinking" kind so callers can
                        # route it to a separate UI block and keep the main
                        # first-token metric anchored to the answer body.
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield ("thinking", reasoning)
                        # OpenAI-compatible providers also emit deltas like
                        # {"content": null} (role-only / final frames). The
                        # key is present but the value is None — yielding
                        # that produced `{"chunk": null}` and later crashed
                        # the "".join() in the caller. Only forward real,
                        # non-empty text.
                        content = delta.get("content")
                        if content:
                            yield ("content", content)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
