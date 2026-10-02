"""
Board Tap — Student 4 (UI & Benchmarking)
ui/server/board_tap.py

Background asyncio task that feeds board events into the EventBus.

MODE CONTROL
────────────
Set the environment variable  BOARD_MODE  before starting the server:

  BOARD_MODE=LOG_REPLAY  (default — replays the checked-in recorded JSON snapshot)
  BOARD_MODE=LIVE_TAP    (waits for a board from a conversation in this process)

The active mode is:
  - Logged at startup (INFO level)
  - Included in every connection_ack message sent to the frontend
  - Shown in the UI header HUD ("LIVE" vs "REPLAY" badge)

LIVE_TAP
────────
Subscribes to ENTRY_POSTED events from the Blackboard owned by an in-process
agent conversation. It does not create a private demo board or fall back to
replay. Start a conversation with POST /api/conversation/start.

LOG_REPLAY
──────────
Reads a recorded BlackboardState JSON snapshot from  bench/data/recorded_session.json
and replays its entries at REPLAY_INTERVAL_S per entry.  The UI labels these
sessions with a "REPLAY" badge (see ui/frontend/src/App.tsx).

If no snapshot file exists, falls back to the synthetic mock stream (mock_stream.py)
and logs a clear warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from blackboard.core import Blackboard
from blackboard.models import BoardEventType
from ui.server.event_bus import bus

logger = logging.getLogger("board-tap")

# ── Configuration ──────────────────────────────────────────────────────────────

# Active mode: LOG_REPLAY is the default; LIVE_TAP is connected when an in-process
# conversation starts.
BOARD_MODE: str = os.getenv("BOARD_MODE", "LOG_REPLAY").upper()

# Seconds between entries in LOG_REPLAY mode
REPLAY_INTERVAL_S: float = float(os.getenv("REPLAY_INTERVAL_S", "1.0"))

# Path to the recorded session snapshot for LOG_REPLAY
RECORDED_SESSION_PATH: Path = Path(
    os.getenv("RECORDED_SESSION_PATH", "bench/data/recorded_session.json")
)


from bench.metrics.token_counter import count_tokens_for_entry, global_token_tracker

_live_loop: asyncio.AbstractEventLoop | None = None
_live_board: Blackboard | None = None
_live_listener: Any = None


# ── Helper: convert BoardEntry → wire event dict ──────────────────────────────

def _entry_to_event(entry_dict: dict) -> dict:
    """Wrap a BoardEntry dict in the standard board_entry envelope with token counts."""
    pred = entry_dict.get("prediction", "")
    expl = entry_dict.get("explanation", "")
    agent = entry_dict.get("agent_id", "")
    eid = entry_dict.get("entry_id", "")
    tag = entry_dict.get("tag", "")

    # Calculate token count for this specific entry
    tokens = count_tokens_for_entry(pred, expl, agent_id=agent)

    # Track in global tracker
    global_token_tracker.record_turn(
        entry_id=eid,
        agent_id=agent,
        tag=tag,
        prediction=pred,
        explanation=expl,
        is_counterfactual_sim=bool(entry_dict.get("is_counterfactual_sim", False)),
    )

    return {
        "type": "board_entry",
        "entry": {
            "entry_id": eid,
            "agent_id": agent,
            "tag": tag,
            "prediction": pred,
            "explanation": expl,
            "target_entry_id": entry_dict.get("target_entry_id"),
            "is_counterfactual_sim": entry_dict.get("is_counterfactual_sim", False),
            "timestamp": entry_dict.get("timestamp", ""),
            "token_count": tokens,
            "token_count_kind": "estimated_board_entry_text_only",
        },
        "token_tally": global_token_tracker.export_tally_report(),
    }


# ── LIVE_TAP: bridge the actual Blackboard event emitter ─────────────────────

def attach_live_board(board: Blackboard, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Forward ENTRY_POSTED events from this actual board to subscribed WebSockets."""
    global _live_board, _live_listener
    event_loop = loop or _live_loop
    if event_loop is None or not event_loop.is_running():
        raise RuntimeError("LIVE_TAP has no running server event loop")
    if _live_board is board:
        return
    detach_live_board()
    global_token_tracker.reset()

    def on_board_event(board_event) -> None:
        if board_event.event_type is not BoardEventType.ENTRY_POSTED:
            return
        entry = board_event.payload.get("entry")
        if not entry:
            return
        event = _entry_to_event(entry)
        try:
            asyncio.run_coroutine_threadsafe(bus.publish(event), event_loop)
        except RuntimeError:
            logger.exception("[LIVE_TAP] Could not publish board event")

    board.subscribe(on_board_event)
    _live_board = board
    _live_listener = on_board_event
    logger.info("[LIVE_TAP] Attached to Blackboard task_id=%s", board.task_id)


def detach_live_board() -> None:
    """Remove the current Blackboard event subscription, if any."""
    global _live_board, _live_listener
    if _live_board is not None and _live_listener is not None:
        _live_board.unsubscribe(_live_listener)
    _live_board = None
    _live_listener = None


# ── LOG_REPLAY: replay a recorded JSON snapshot ───────────────────────────────

async def _run_log_replay() -> None:
    """
    Replay a recorded BlackboardState JSON snapshot entry-by-entry.
    Falls back to the synthetic mock stream if no snapshot file is found.
    """
    if RECORDED_SESSION_PATH.exists():
        logger.info(
            "[LOG_REPLAY] Replaying recorded session: %s  (%.1fs/entry)",
            RECORDED_SESSION_PATH, REPLAY_INTERVAL_S,
        )
        raw = json.loads(RECORDED_SESSION_PATH.read_text(encoding="utf-8"))

        # Support both a raw BlackboardState dict and a list of BoardEntry dicts
        entries: list[dict] = []
        if isinstance(raw, dict) and "entries" in raw:
            entries = raw["entries"]
        elif isinstance(raw, list):
            entries = raw
        else:
            logger.warning("[LOG_REPLAY] Unrecognised snapshot format — falling back to mock stream")

        if entries:
            for entry_dict in entries:
                await bus.publish(_entry_to_event(entry_dict))
                logger.info(
                    "[LOG_REPLAY] Replayed: %s %s",
                    entry_dict.get("tag", "?"), entry_dict.get("entry_id", "?"),
                )
                await asyncio.sleep(REPLAY_INTERVAL_S)

            await bus.publish({
                "type": "session_summary",
                "source": "LOG_REPLAY",
                "summary": raw.get("intelligibility", "UNRESOLVED") if isinstance(raw, dict) else "UNRESOLVED",
            })
            await bus.publish({"type": "stream_complete", "source": "LOG_REPLAY"})
            logger.info("[LOG_REPLAY] Replay complete.")
            return

    # Final fallback: synthetic mock stream
    logger.warning(
        "[LOG_REPLAY] No recorded session at %s — using synthetic mock stream",
        RECORDED_SESSION_PATH,
    )
    from ui.server.mock_stream import CANNED_EVENT_STREAM  # noqa: PLC0415
    for event in CANNED_EVENT_STREAM:
        await bus.publish(event)
        await asyncio.sleep(REPLAY_INTERVAL_S)
    await bus.publish({"type": "stream_complete", "source": "MOCK_FALLBACK"})


# ── Public entry-point called from FastAPI lifespan ──────────────────────────

async def start_board_tap() -> None:
    """
    Launch the board tap background task.
    Call once from FastAPI's lifespan startup hook.
    """
    global _live_loop
    _live_loop = asyncio.get_running_loop()
    detach_live_board()
    bus.reset_history()
    logger.info("Board tap starting in mode: %s", BOARD_MODE)
    if BOARD_MODE == "LIVE_TAP":
        logger.info("[LIVE_TAP] Waiting for an in-process agent conversation to attach its Blackboard")
    else:
        asyncio.create_task(_run_log_replay(), name="board-log-replay")


async def stop_board_tap() -> None:
    """Detach any active board when the FastAPI server shuts down."""
    global _live_loop
    detach_live_board()
    _live_loop = None
