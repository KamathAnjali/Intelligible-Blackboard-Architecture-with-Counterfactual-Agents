"""Validated prediction/explanation output, before mapping to BoardEntry."""

from pydantic import BaseModel, ConfigDict, Field


class PEXResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    prediction: str = Field(min_length=1, pattern=r"\S")
    explanation: str = Field(min_length=1, pattern=r"\S")


class ExplanationCheck(BaseModel):
    """Separate model review; never included in the PEX or BoardEntry fields."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    supported: bool
    reason: str = Field(min_length=1, max_length=300, pattern=r"\S")


class PEXGenerationError(RuntimeError):
    """No valid response within the budget; raw attempts remain inspectable."""

    def __init__(self, attempts):
        self.attempts = attempts
        super().__init__(f"No valid PEX response after {len(attempts)} attempts: {attempts[-1]['error']}")
