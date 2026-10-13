from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExplanationOutput(StrictAgentOutput):
    reasoning: str = Field(min_length=2, description="简洁、基于证据的中文解释")


class LogisticsDecisionOutput(ExplanationOutput):
    expedite: bool


class ReflectionOutput(StrictAgentOutput):
    verdict: Literal["PASS", "REVISE"]
    critique: str = Field(min_length=2, description="对当前决策质量的中文批判性审查")
    issues: List[str] = Field(default_factory=list, max_length=8)
    strengths: List[str] = Field(default_factory=list, max_length=8)
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class CoordinatorDecisionOutput(ExplanationOutput):
    decision_type: Literal["REORDER", "HOLD"]
    supplier_id: Optional[int] = Field(default=None, ge=1)
    quantity: int = Field(ge=0)
    expedite: bool

    @model_validator(mode="after")
    def validate_decision_shape(self):
        if self.decision_type == "HOLD":
            if self.supplier_id is not None or self.quantity != 0 or self.expedite:
                raise ValueError("HOLD must have no supplier, zero quantity, and no expedite")
        elif self.supplier_id is None or self.quantity <= 0:
            raise ValueError("REORDER requires a supplier and positive quantity")
        return self


async def invoke_structured(llm, schema, prompt: str, config: dict):
    """Invoke an LLM asynchronously and validate its complete response."""
    structured_llm = llm.with_structured_output(schema)
    result = await structured_llm.ainvoke(prompt, config=config)
    return result if isinstance(result, schema) else schema.model_validate(result)
