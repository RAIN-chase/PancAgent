from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol
from urllib import request

from langchain_core.runnables import Runnable

from protocol_optimization_agent.runtime.archive import current_archive, record_model_call


class LLMClient(Protocol):
    def complete_json(self, task: str, payload: dict[str, Any], schema_name: str) -> dict[str, Any] | None:
        """Return strict JSON for a task, or None when no LLM result is available."""


class OfflineLLMClient:
    """Default client for reproducible offline runs.

    It deliberately returns None so rule-based validators and fallbacks remain
    the source of truth unless a real or mock LLM client is injected.
    """

    def complete_json(self, task: str, payload: dict[str, Any], schema_name: str) -> dict[str, Any] | None:
        if current_archive():
            record_model_call(self, [{"task": task, "payload": payload, "schema_name": schema_name}], None, 0)
        return None


class StaticMockLLMClient:
    """Small test helper that returns preconfigured outputs by task name."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, task: str, payload: dict[str, Any], schema_name: str) -> dict[str, Any] | None:
        self.calls.append({"task": task, "payload": payload, "schema_name": schema_name})
        response = self.responses.get(task)
        record_model_call(self, self.calls[-1], response, 0)
        return response


class OpenAIJSONLLMClient:
    """Minimal OpenAI-compatible JSON client for schema-constrained agent steps.

    It is opt-in. OfflineLLMClient remains the default unless callers inject this
    client from config.
    """

    def __init__(
        self,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 60,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete_json(self, task: str, payload: dict[str, Any], schema_name: str) -> dict[str, Any] | None:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key environment variable: {self.api_key_env}")
        body = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict JSON function for protocol analysis. "
                        f"Return only one JSON object matching schema {schema_name}. "
                        "Every biological or normalization claim must be grounded in the provided evidence_quote."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"task": task, "schema_name": schema_name, "payload": payload}, ensure_ascii=False
                    ),
                },
            ],
        }
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
        except BaseException as exc:
            record_model_call(self, body["messages"], None, (time.perf_counter() - started) * 1000, str(exc))
            raise
        record_model_call(self, body["messages"], data, (time.perf_counter() - started) * 1000)
        return result


def build_llm_client(config: dict[str, Any] | None) -> LLMClient:
    config = config or {}
    provider = str(config.get("provider") or "offline").lower()
    if provider == "offline":
        return OfflineLLMClient()
    if provider in {"openai", "openai_compatible", "deepseek"}:
        return OpenAIJSONLLMClient(
            model=str(config["model"]),
            api_key_env=str(
                config.get("api_key_env") or ("DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY")
            ),
            base_url=str(
                config.get("base_url")
                or ("https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1")
            ),
            timeout_seconds=int(config.get("timeout_seconds") or 60),
        )
    raise ValueError(f"Unsupported llm provider: {provider}")


def build_json_llm_client_from_env() -> LLMClient:
    """Use server-owned chat provider settings for opt-in PO JSON calls."""
    provider = os.getenv("PROTOCOL_CHAT_LLM_PROVIDER") or (
        "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai" if os.getenv("OPENAI_API_KEY") else "offline")
    return build_llm_client({"provider": provider,
        "model": os.getenv("PROTOCOL_CHAT_MODEL") or ("deepseek-chat" if provider == "deepseek" else "gpt-4o-mini"),
        "api_key_env": os.getenv("PROTOCOL_CHAT_API_KEY_ENV") or ("DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"),
        "base_url": os.getenv("PROTOCOL_CHAT_BASE_URL") or ("https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1"),
        "timeout_seconds": int(os.getenv("PROTOCOL_CHAT_TIMEOUT_SECONDS") or 60)})


def _build_chat_model_from_env() -> Runnable | None:
    provider = os.getenv("PROTOCOL_CHAT_LLM_PROVIDER")
    if not provider and os.getenv("DEEPSEEK_API_KEY"):
        provider = "deepseek"
    if not provider and os.getenv("OPENAI_API_KEY"):
        provider = "openai"
    provider = (provider or "").lower()
    if not provider:
        return None

    if provider not in {"openai", "openai_compatible", "deepseek"}:
        raise ValueError(f"Unsupported PROTOCOL_CHAT_LLM_PROVIDER: {provider}")

    from langchain_openai import ChatOpenAI

    api_key_env = os.getenv("PROTOCOL_CHAT_API_KEY_ENV") or (
        "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    )
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing chat LLM API key environment variable: {api_key_env}")

    model = os.getenv("PROTOCOL_CHAT_MODEL") or ("deepseek-chat" if provider == "deepseek" else "gpt-4o-mini")
    base_url = os.getenv("PROTOCOL_CHAT_BASE_URL") or ("https://api.deepseek.com" if provider == "deepseek" else None)
    temperature = float(os.getenv("PROTOCOL_CHAT_TEMPERATURE") or 0)
    timeout = float(os.getenv("PROTOCOL_CHAT_TIMEOUT_SECONDS") or 60)

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "timeout": timeout,
        "streaming": True,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)
