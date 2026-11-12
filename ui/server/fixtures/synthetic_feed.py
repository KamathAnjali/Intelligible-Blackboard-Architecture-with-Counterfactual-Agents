"""Gold-free synthetic board events for UI visualization testing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from bench.ingest.krama_parser import AgentFacingTask

logger = logging.getLogger("synthetic-visualisation-feed")
SYNTHETIC_SOURCE = "SYNTHETIC_FIXTURE"


async def _feed(tasks: Sequence[AgentFacingTask], board: Any | None,
                interval_s: float, emit_to_bus: bool) -> list[Any]:
    from blackboard.core import Blackboard
    from blackboard.models import AgentRecord, BoardEntry, PXPTag

    if interval_s < 0:
        raise ValueError("interval_s must be non-negative")
    bus = None
    if emit_to_bus:
        from ui.server.event_bus import bus as event_bus
        bus = event_bus
        await bus.publish({"type": "session_started", "source": SYNTHETIC_SOURCE})

    states = []
    for index, task in enumerate(tasks, start=1):
        active_board = board or Blackboard(task_id=f"synthetic-{task.task_id}")
        roster = (
            ("Synthetic_Proposer", "aggressive_proposer"),
            ("Synthetic_Verifier", "cautious_verifier"),
            ("Synthetic_Critic", "critic"),
        )
        for agent_id, persona in roster:
            active_board.register_agent(AgentRecord(
                agent_id=agent_id, persona=persona, model_name="SYNTHETIC_FIXTURE"
            ))

        async def post(tag: PXPTag, agent_id: str, prediction: str,
                       explanation: str, target: str | None = None) -> str:
            entry = BoardEntry(
                agent_id=agent_id, tag=tag, prediction=prediction,
                explanation=explanation, target_entry_id=target,
            )
            active_board.post_entry(entry)
            if bus is not None:
                from ui.server.board_tap import _entry_to_event
                event = _entry_to_event(entry.model_dump(mode="json"))
                event["source"] = SYNTHETIC_SOURCE
                await bus.publish(event)
            if interval_s:
                await asyncio.sleep(interval_s)
            return entry.entry_id

        root_id = await post(
            PXPTag.REVISE, "Synthetic_Proposer", "A placeholder candidate is under review.",
            f"SYNTHETIC visualization placeholder for the {task.domain} domain.",
        )
        verifier_id = await post(
            PXPTag.RATIFY, "Synthetic_Verifier", "The placeholder was checked.",
            "SYNTHETIC second-perspective review; no benchmark evidence was used.", root_id,
        )
        await post(
            PXPTag.RATIFY, "Synthetic_Critic", "The visualization sequence is complete.",
            "SYNTHETIC closing event for UI layout testing.", verifier_id,
        )
        states.append(active_board.get_state())
        logger.info("SYNTHETIC_FIXTURE task %d/%d emitted", index, len(tasks))

    if bus is not None:
        await bus.publish({"type": "stream_complete", "source": SYNTHETIC_SOURCE})
    return states


def synthetic_visualisation_feed(
    tasks: Sequence[AgentFacingTask],
    board: Any | None = None,
    interval_s: float = 0.0,
    emit_to_bus: bool = True,
) -> list[Any]:
    """Emit gold-free, explicitly synthetic board events for visualization only."""
    return asyncio.run(_feed(tasks, board, interval_s, emit_to_bus))
