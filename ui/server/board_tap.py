"""
Board Tap — Student 4 (UI & Benchmarking)
ui/server/board_tap.py

Background asyncio task that feeds board events into the EventBus.

MODE CONTROL
────────────
Set the environment variable  BOARD_MODE  before starting the server:

  BOARD_MODE=LIVE_TAP    (default when blackboard/ module is importable)
  BOARD_MODE=LOG_REPLAY  (fallback — replays a recorded JSON snapshot)

The active mode is:
  - Logged at startup (INFO level)
  - Included in every connection_ack message sent to the frontend
  - Shown in the UI header HUD ("LIVE" vs "REPLAY" badge)

LIVE_TAP
────────
Polls Blackboard.get_state() at LIVE_POLL_INTERVAL_S seconds.  This is a
read-only tap that does NOT require any API changes from Student 1/2 — it just
reads the already-public get_state() and publishes new entries as they appear.

TODO (Week 2 — Student 2 Integration):
  Replace polling with a proper push mechanism once Student 2 exposes an event
  publisher on the scheduler.  The EventBus.publish() call site stays the same.

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

from ui.server.event_bus import bus

logger = logging.getLogger("board-tap")

# ── Configuration ──────────────────────────────────────────────────────────────

# Active mode: LIVE_TAP or LOG_REPLAY
BOARD_MODE: str = os.getenv("BOARD_MODE", "LIVE_TAP").upper()

# Seconds between board polls in LIVE_TAP mode
LIVE_POLL_INTERVAL_S: float = float(os.getenv("LIVE_POLL_INTERVAL_S", "0.5"))

# Seconds between entries in LOG_REPLAY mode
REPLAY_INTERVAL_S: float = float(os.getenv("REPLAY_INTERVAL_S", "1.0"))

# Path to the recorded session snapshot for LOG_REPLAY
RECORDED_SESSION_PATH: Path = Path(
    os.getenv("RECORDED_SESSION_PATH", "bench/data/recorded_session.json")
)


# ── Helper: convert BoardEntry → wire event dict ──────────────────────────────

def _entry_to_event(entry_dict: dict) -> dict:
    """Wrap a BoardEntry dict in the standard board_entry envelope."""
    return {
        "type": "board_entry",
        "entry": {
            "entry_id": entry_dict.get("entry_id", ""),
            "agent_id": entry_dict.get("agent_id", ""),
            "tag": entry_dict.get("tag", ""),
            "prediction": entry_dict.get("prediction", ""),
            "explanation": entry_dict.get("explanation", ""),
            "target_entry_id": entry_dict.get("target_entry_id"),
            "is_counterfactual_sim": entry_dict.get("is_counterfactual_sim", False),
            "timestamp": entry_dict.get("timestamp", ""),
        },
    }


# ── LIVE_TAP: poll Blackboard.get_state() ────────────────────────────────────

async def _run_live_tap() -> None:
    """
    Read-only tap into Student 1/2's Blackboard.
    Polls get_state() and publishes newly-added entries to the EventBus.

    TODO (Student 1/2 Integration):
      Import their actual Blackboard instance here once they expose a shared
      singleton or a factory.  Current import path is a best-guess based on
      the existing repo layout; adjust if needed.

      Expected interface:
        board.get_state() -> BlackboardState
        state.entries    -> list[BoardEntry]  (ordered, append-only)
    """
    try:
        # TODO: Replace with shared Blackboard singleton from Student 1/2
        # e.g.: from blackboard.session import active_board as board
        from blackboard.core import Blackboard  # noqa: PLC0415
        from blackboard.models import AgentRecord  # noqa: PLC0415

        # Bootstrap a minimal board for the demo session
        board = Blackboard(task_id="ui_demo_session")
        board.register_agent(AgentRecord(
            agent_id="Agent_Alpha (Proposer)",
            persona="aggressive_proposer",
            model_name="mistral-7b-instruct",
        ))
        board.register_agent(AgentRecord(
            agent_id="Agent_Beta (Verifier)",
            persona="cautious_verifier",
            model_name="mistral-7b-instruct",
        ))
        board.register_agent(AgentRecord(
            agent_id="Agent_Gamma (Critic)",
            persona="critic",
            model_name="mistral-7b-instruct",
        ))

        logger.info("[LIVE_TAP] Polling Blackboard every %.1fs", LIVE_POLL_INTERVAL_S)

        seen_ids: set[str] = set()
        while True:
            state = board.get_state()
            for entry in state.entries:
                if entry.entry_id not in seen_ids:
                    seen_ids.add(entry.entry_id)
                    event = _entry_to_event(entry.model_dump(mode="json"))
                    await bus.publish(event)
                    logger.info("[LIVE_TAP] Published: %s %s", entry.tag, entry.entry_id)

            await asyncio.sleep(LIVE_POLL_INTERVAL_S)

    except Exception as exc:
        logger.error("[LIVE_TAP] Failed: %s — falling back to LOG_REPLAY", exc)
        await _run_log_replay()


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
    logger.info("Board tap starting in mode: %s", BOARD_MODE)
    if BOARD_MODE == "LIVE_TAP":
        asyncio.create_task(_run_live_tap(), name="board-live-tap")
    else:
        asyncio.create_task(_run_log_replay(), name="board-log-replay")
