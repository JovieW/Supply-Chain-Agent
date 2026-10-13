"""Optional LangSmith configuration shared by tracing and evaluations.

The application remains fully usable with local JSONL traces when LangSmith is
disabled.  Secrets are read from environment variables and are never added to
trace metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

try:
    from dotenv import load_dotenv
except ImportError:  # keep status/dry-run usable in minimal test environments
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


TRUE_VALUES = {"1", "true", "yes", "on"}


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class LangSmithSettings:
    enabled: bool
    api_key: str
    endpoint: str
    project: str
    workspace_id: str
    dataset: str
    experiment_prefix: str
    environment: str
    app_version: str
    prompt_version: str
    llm_provider: str
    llm_model: str

    @classmethod
    def from_env(cls) -> "LangSmithSettings":
        # LANGCHAIN_* names are retained as a compatibility bridge for older
        # local .env files. New configuration uses the LANGSMITH_* names.
        tracing_value = os.getenv(
            "LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", "false")
        )
        provider = os.getenv("LLM_PROVIDER", "dashscope").strip().lower()
        model_variable = "DASHSCOPE_MODEL" if provider == "dashscope" else "OPENROUTER_MODEL"
        default_model = "qwen3.7-flash" if provider == "dashscope" else "openai/gpt-4o-mini"
        return cls(
            enabled=_enabled(tracing_value),
            api_key=(
                os.getenv("LANGSMITH_API_KEY")
                or os.getenv("LANGCHAIN_API_KEY")
                or ""
            ).strip(),
            endpoint=os.getenv(
                "LANGSMITH_ENDPOINT",
                os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"),
            ).strip(),
            project=os.getenv(
                "LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "supply-chain-control-tower")
            ).strip(),
            workspace_id=os.getenv("LANGSMITH_WORKSPACE_ID", "").strip(),
            dataset=os.getenv(
                "LANGSMITH_EVAL_DATASET", "supply-chain-control-tower-regression"
            ).strip(),
            experiment_prefix=os.getenv(
                "LANGSMITH_EXPERIMENT_PREFIX", "supply-chain-agent"
            ).strip(),
            environment=os.getenv("APP_ENV", "local").strip(),
            app_version=os.getenv("AGENT_VERSION", "interview-v1").strip(),
            prompt_version=os.getenv("AGENT_PROMPT_VERSION", "v1").strip(),
            llm_provider=provider,
            llm_model=os.getenv(model_variable, default_model).strip(),
        )

    def validate(self, *, require_enabled: bool = False) -> None:
        if require_enabled and not self.enabled:
            raise RuntimeError("LANGSMITH_TRACING=true is required")
        if (require_enabled or self.enabled) and not self.api_key:
            raise RuntimeError("LANGSMITH_API_KEY is required when LangSmith is enabled")
        if not self.project:
            raise RuntimeError("LANGSMITH_PROJECT cannot be empty")
        if not self.dataset:
            raise RuntimeError("LANGSMITH_EVAL_DATASET cannot be empty")

    def apply_canonical_environment(self) -> None:
        """Expose current LangSmith variable names to LangChain integrations."""
        if not self.enabled:
            return
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", self.api_key)
        os.environ.setdefault("LANGSMITH_ENDPOINT", self.endpoint)
        os.environ.setdefault("LANGSMITH_PROJECT", self.project)
        if self.workspace_id:
            os.environ.setdefault("LANGSMITH_WORKSPACE_ID", self.workspace_id)

    def trace_metadata(self, *, workflow_run_id: str, product_id: int | None) -> dict:
        """Return filterable, non-secret metadata inherited by child runs."""
        return {
            "workflow_run_id": str(workflow_run_id),
            "product_id": product_id,
            "environment": self.environment,
            "agent_version": self.app_version,
            "prompt_version": self.prompt_version,
            "governed_workflow": True,
            "ls_provider": self.llm_provider,
            "ls_model_name": self.llm_model,
        }


def langsmith_status() -> dict:
    """Safe status payload for diagnostics; never expose the API key."""
    settings = LangSmithSettings.from_env()
    return {
        "enabled": settings.enabled,
        "configured": bool(settings.api_key),
        "project": settings.project,
        "endpoint": settings.endpoint,
        "dataset": settings.dataset,
        "environment": settings.environment,
        "agent_version": settings.app_version,
        "prompt_version": settings.prompt_version,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }
