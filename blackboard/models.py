"""
PXP Blackboard — core data models.

This module is the shared contract everyone builds against:
  - Student 1 (Infra): reads/writes these types in the store + scheduler
  - Student 2 (Agents): produces BoardEntry objects from LLM output
  - Student 3 (UI/Bench): serializes these to JSON for the websocket stream

Freeze this file early (Week 1, Day 3-4) — every other module depends on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# PXP tag vocabulary
# ---------------------------------------------------------------------------

class PXPTag(str, Enum):
    """The four moves an agent can make against the current board hypothesis."""

    RATIFY = "RATIFY"   # aligns with both prediction and explanation
    REVISE = "REVISE"   # adjusts own view, writes an updated thesis
    REFUTE = "REFUTE"   # rejects the position, cannot self-correct -> logs roadblock
    REJECT = "REJECT"   # full conflict with prediction and explanation


class IntelligibilityLevel(str, Enum):
    """Session-level classification computed by the scheduler from the tag stream."""

    UNRESOLVED = "UNRESOLVED"
    STRONG = "STRONG"            # consensus reached, but with REFUTE/REJECT along the way
    ULTRA_STRONG = "ULTRA_STRONG"  # consensus reached primarily via productive REVISE loops
    DEADLOCKED = "DEADLOCKED"    # exceeded iteration/patience threshold with no consensus


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class AgentRecord(BaseModel):
    """Registry entry for one agent participating in a session."""

    agent_id: str
    persona: str                       # e.g. "cautious_verifier", "aggressive_proposer"
    counterfactual_capable: bool = False
    model_name: str = "unspecified"    # e.g. "mistral-7b-instruct"
    active: bool = True


# ---------------------------------------------------------------------------
# Board entries (the PXP message contract)
# ---------------------------------------------------------------------------

class BoardEntry(BaseModel):
    """
    One PXP-tagged message written to the blackboard.

    `target_entry_id` lets a RATIFY/REVISE/REFUTE/REJECT point at the specific
    prior entry it's reacting to, instead of only "the board in general" —
    this is what lets the UI draw edges between nodes.
    """

    entry_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str
    tag: PXPTag
    prediction: str                    # the "what"
    explanation: str                   # the "why"
    target_entry_id: Optional[str] = None
    is_counterfactual_sim: bool = False  # True if this came from a rollback sandbox, not the live run
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("prediction", "explanation")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prediction/explanation cannot be empty — PXP requires both the 'what' and the 'why'")
        return v.strip()


class DeadlockEvent(BaseModel):
    """Raised by the scheduler when a REFUTE/REJECT loop is detected."""

    triggered_at_entry_id: str
    loop_length: int
    involved_agent_ids: list[str]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CounterfactualResult(BaseModel):
    """Output of a retrospective rollback simulation."""

    agent_id: str
    original_entry_id: str            # the historical entry that was rewritten
    simulated_entry: BoardEntry       # the alternate version that was tried
    delta_score: float                # positive = simulation looked more likely to reach consensus
    applied_to_live: bool             # whether the agent actually adopted this on the live board
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Session-level state snapshot (what gets sent to the UI / persisted)
# ---------------------------------------------------------------------------

class BlackboardState(BaseModel):
    task_id: str
    entries: list[BoardEntry] = Field(default_factory=list)
    agents: dict[str, AgentRecord] = Field(default_factory=dict)
    intelligibility: IntelligibilityLevel = IntelligibilityLevel.UNRESOLVED
    deadlocks: list[DeadlockEvent] = Field(default_factory=list)
    counterfactual_events: list[CounterfactualResult] = Field(default_factory=list)
