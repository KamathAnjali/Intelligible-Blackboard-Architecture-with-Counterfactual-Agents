"""Model-controlled PXP fields; application-owned metadata stays in BoardEntry."""

from typing import Literal

from blackboard.models import PXPTag
from agents.pex import PEXGenerationError, PEXResponse


class PXPResponse(PEXResponse):
    # FACT is an application-owned Blackboard entry, never a model action.
    tag: Literal[PXPTag.RATIFY, PXPTag.REVISE, PXPTag.REFUTE, PXPTag.REJECT]


class InitialPXPResponse(PXPResponse):
    # The current board protocol starts with a REVISE proposal and no target.
    tag: Literal[PXPTag.REVISE]


class PXPGenerationError(PEXGenerationError):
    def __init__(self, attempts):
        super().__init__(attempts)
        self.args = (f"No valid PXP response after {len(attempts)} attempts: {attempts[-1]['error']}",)
