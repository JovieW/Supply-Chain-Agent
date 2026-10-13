from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from langsmith_config import LangSmithSettings


load_dotenv()


def _provider_settings() -> tuple[str, str, str]:
    provider = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()
    if provider == "dashscope":
        return (
            os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            os.getenv("DASHSCOPE_API_KEY", ""),
            os.getenv("DASHSCOPE_MODEL", "qwen3.7-flash"),
        )
    if provider == "openrouter":
        return (
            os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            os.getenv("OPENROUTER_API_KEY", ""),
            os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        )
    raise RuntimeError(
        f"Unsupported LLM_PROVIDER={provider!r}; expected 'dashscope' or 'openrouter'"
    )


def validate_llm_config() -> None:
    provider = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()
    _, api_key, model = _provider_settings()
    if not api_key:
        variable = "DASHSCOPE_API_KEY" if provider == "dashscope" else "OPENROUTER_API_KEY"
        raise RuntimeError(f"{variable} is required when LLM_PROVIDER={provider}")
    if not model:
        raise RuntimeError(f"No model configured for LLM_PROVIDER={provider}")


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """Return one validated OpenAI-compatible chat client."""
    validate_llm_config()
    base_url, api_key, model = _provider_settings()
    return ChatOpenAI(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=0,
        max_retries=2,
        timeout=60,
    )


def verify_tracing() -> bool:
    settings = LangSmithSettings.from_env()
    if settings.enabled and settings.api_key:
        settings.apply_canonical_environment()
        print("✓ LangSmith tracing enabled")
        print(f"  Project: {settings.project}")
        print(f"  Endpoint: {settings.endpoint}")
        return True
    if settings.enabled:
        print("⚠ LangSmith tracing requested but LANGSMITH_API_KEY is missing")
        return False
    print("⚠ LangSmith tracing disabled")
    return False
