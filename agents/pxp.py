"""Model-controlled PXP fields; application-owned metadata stays in BoardEntry."""

from typing import Literal

from blackboard.models import PXPTag
from agents.pex import PEXGenerationError, PEXResponse


class PXPResponse(PEXResponse):
    tag: PXPTag


class InitialPXPResponse(PXPResponse):
    # Follows the opening proposal in docs/Schema_Draft.md and demo.py.
    tag: Literal[PXPTag.REVISE]


class PXPGenerationError(PEXGenerationError):
    def __init__(self, attempts):
        super().__init__(attempts)
        self.args = (f"No valid PXP response after {len(attempts)} attempts: {attempts[-1]['error']}",)
