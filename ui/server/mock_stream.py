"""
Mock Event Stream Fixture & Generator — Student 4 (UI & Benchmarking)

Provides a canned sequence of board entry events depicting PXP tag transitions
(PROPOSE -> RATIFY / REVISE / REFUTE / REJECT) across multiple agents.
Includes an async generator to stream events with a controllable delay parameter.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock-stream")

# Canned sequence of PXP Blackboard interaction events
CANNED_EVENT_STREAM: List[Dict[str, Any]] = [
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-101",
      "agent_id": "Agent_Alpha (Proposer)",
      "tag": "PROPOSE",
      "prediction": "x = 12",
      "explanation": "Posing initial hypothesis solving equation x^2 = 144.",
      "target_entry_id": None,
      "is_counterfactual_sim": False,
      "timestamp": "2026-09-22T10:00:00Z"
    }
  },
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-102",
      "agent_id": "Agent_Beta (Verifier)",
      "tag": "RATIFY",
      "prediction": "Verified x = 12",
      "explanation": "Calculated 12 * 12 = 144. Aligns with proposed solution.",
      "target_entry_id": "entry-101",
      "is_counterfactual_sim": False,
      "timestamp": "2026-09-22T10:00:02Z"
    }
  },
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-103",
      "agent_id": "Agent_Gamma (Critic)",
      "tag": "REFUTE",
      "prediction": "Solution incomplete: missing x = -12",
      "explanation": "(-12)^2 = 144. The domain does not restrict to positive integers.",
      "target_entry_id": "entry-101",
      "is_counterfactual_sim": False,
      "timestamp": "2026-09-22T10:00:05Z"
    }
  },
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-104",
      "agent_id": "Agent_Alpha (Proposer)",
      "tag": "REVISE",
      "prediction": "Revised Solution Set: x ∈ {-12, 12}",
      "explanation": "Accepting refutation from Agent_Gamma. Updating thesis to include negative root.",
      "target_entry_id": "entry-103",
      "is_counterfactual_sim": False,
      "timestamp": "2026-09-22T10:00:08Z"
    }
  },
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-105",
      "agent_id": "Agent_Beta (Verifier)",
      "tag": "RATIFY",
      "prediction": "Final Consensus: x ∈ {-12, 12}",
      "explanation": "Ratifying updated thesis. Solution set is complete and verified.",
      "target_entry_id": "entry-104",
      "is_counterfactual_sim": False,
      "timestamp": "2026-09-22T10:00:10Z"
    }
  },
  {
    "type": "board_entry",
    "entry": {
      "entry_id": "entry-106",
      "agent_id": "Agent_Delta (CF Sandbox)",
      "tag": "REJECT",
      "prediction": "Simulated branch rejected",
      "explanation": "Counterfactual simulation testing x = 0 resulted in contradiction.",
      "target_entry_id": "entry-103",
      "is_counterfactual_sim": True,
      "timestamp": "2026-09-22T10:00:12Z"
    }
  },
  {
    "type": "session_summary",
    "summary": {
      "task_id": "kb_demo_101",
      "intelligibility": "ULTRA_STRONG",
      "total_entries": 6,
      "consensus_reached": True
    }
  }
]


async def mock_event_stream_generator(delay_seconds: float = 1.0) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Yield canned PXP board events sequentially with a configurable delay.

    Args:
        delay_seconds: Time to sleep between events in seconds.
    """
    logger.info(f"Starting mock event stream replay (interval: {delay_seconds}s)")
    for event in CANNED_EVENT_STREAM:
        yield event
        await asyncio.sleep(delay_seconds)
    logger.info("Finished replaying mock event stream.")
