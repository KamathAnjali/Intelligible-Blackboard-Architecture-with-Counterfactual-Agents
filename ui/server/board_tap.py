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
from threading import Lock
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


from agents.llm_client import MODEL
from bench.metrics.token_counter import UsageLedger, count_tokens_for_entry, global_token_tracker

_live_loop: asyncio.AbstractEventLoop | None = None
_live_board: Blackboard | None = None
_live_listener: Any = None
global_usage_ledger = UsageLedger()
_estimated_entry_ids: set[str] = set()
_estimated_entries_lock = Lock()
_usage_session_id: str | None = None
_usage_task_id: str | None = None
COUNTERFACTUAL_EVENT_TYPES = {
    "branch_created", "branch_entry_posted", "counterfactual_result", "live_adjustment_applied",
}


def process_counterfactual_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Validate/normalize one explicit branch lifecycle event for the UI wire."""
    kind = event.get("type")
    if isinstance(kind, str) and kind.upper() in {item.upper() for item in COUNTERFACTUAL_EVENT_TYPES}:
        kind = kind.lower()
    if kind not in COUNTERFACTUAL_EVENT_TYPES:
        return None
    payload = event.get("payload")
    normalized = {**(payload if isinstance(payload, dict) else {}), **event, "type": kind}
    normalized.pop("payload", None)
    if not isinstance(normalized.get("branch_id"), str) or not normalized["branch_id"]:
        return None
    if kind == "branch_entry_posted" and not isinstance(normalized.get("entry"), dict):
        return None
    if kind == "counterfactual_result" and (
        not isinstance(normalized.get("adopted"), bool)
        or normalized.get("adoption_method") not in {"prompt", "vote_weight", "discarded"}
        or not isinstance(normalized.get("reason"), str)
    ):
        return None
    if kind == "live_adjustment_applied" and not all(
        isinstance(normalized.get(key), str) for key in ("agent_id", "method", "session_entry_id")
    ):
        return None
    normalized.setdefault("source", "LIVE")
    return normalized


def process_board_counterfactual_event(board_event: Any, session_id: str | None = None,
                                       task_id: str | None = None) -> dict[str, Any] | None:
    """Serialize an emitter event only when it uses the explicit branch contract."""
    event_type = getattr(board_event, "event_type", "")
    raw_kind = getattr(event_type, "value", event_type)
    if not isinstance(raw_kind, str):
        raw_kind = getattr(event_type, "name", "")
    raw_kind = raw_kind.lower()
    envelope = {
        "type": raw_kind,
        "payload": getattr(board_event, "payload", {}),
        "session_id": session_id,
        "task_id": task_id or getattr(board_event, "task_id", None),
        "source": "LIVE",
    }
    return process_counterfactual_event(envelope)


def reset_token_usage(session_id: str | None = None, task_id: str | None = None) -> None:
    """Start clean UI accounting for one session/task."""
    global _usage_session_id, _usage_task_id
    global_usage_ledger.reset()
    global_token_tracker.reset()
    with _estimated_entries_lock:
        _estimated_entry_ids.clear()
    _usage_session_id, _usage_task_id = session_id, task_id


def ingest_usage_record(record: dict[str, Any]) -> dict[str, Any]:
    """Consume an optional model callback record and return its separate-source tally."""
    return global_usage_ledger.ingest(record)


def process_board_entry_event(event: dict[str, Any]) -> dict[str, Any]:
    """Add entry-text fallback accounting and return a typed live board event."""
    converted = _entry_to_event(event.get("entry", {}))
    for key in ("source", "session_id", "task_id", "entry_count", "last_entry_id"):
        if key in event:
            converted[key] = event[key]
    return converted


def usage_tally_report() -> dict[str, Any]:
    """Prefer call-level UsageRecords; otherwise expose only entry-text estimates."""
    report = global_usage_ledger.export_report()
    if report["total_calls"]:
        return report
    legacy = global_token_tracker.export_tally_report()
    agents = {}
    for agent_id, summary in legacy["agents"].items():
        tokens = summary["total_tokens"]
        agents[agent_id] = {
            **summary, "provider_tokens": 0, "estimated_tokens": tokens,
            "call_kinds": {"other": {"calls": summary["turn_count"], "estimated_tokens": tokens}},
        }
    return {
        "schema_version": 1, "total_calls": legacy["turn_count"],
        "turn_count": legacy["turn_count"],
        "provider_total_tokens": 0, "estimated_total_tokens": legacy["total_tokens"],
        "total_tokens": legacy["total_tokens"],
        "estimated_share_pct": 100.0 if legacy["total_tokens"] else 0.0,
        "source_label": "estimated" if legacy["turn_count"] else "unavailable",
        "measurement_label": "estimated, entry text only" if legacy["turn_count"] else "no usage records",
        "measurement_scope": "prediction and explanation text only; prompts and internal calls are excluded",
        "prompt_tokens": 0, "completion_tokens": legacy["total_tokens"],
        "agents": agents,
        "sessions": {_usage_session_id or "current": {
            "calls": legacy["turn_count"], "provider_tokens": 0,
            "estimated_tokens": legacy["total_tokens"], "total_tokens": legacy["total_tokens"],
        }} if legacy["turn_count"] else {},
        "by_call_kind": {"other": {"calls": legacy["turn_count"], "provider_tokens": 0,
                                    "estimated_tokens": legacy["total_tokens"],
                                    "total_tokens": legacy["total_tokens"]}},
        "by_execution": {
            "live": {"calls": legacy["turn_count"], "provider_tokens": 0,
                     "estimated_tokens": legacy["mainline_tokens"], "total_tokens": legacy["mainline_tokens"]},
            "simulation": {"calls": sum(1 for s in legacy["agents"].values() if s["simulation_tokens"]),
                           "provider_tokens": 0, "estimated_tokens": legacy["simulation_tokens"],
                           "total_tokens": legacy["simulation_tokens"]},
        },
    }


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

    # This remains explicitly scoped fallback accounting, not model-call usage.
    with _estimated_entries_lock:
        is_new_estimate = eid not in _estimated_entry_ids
        _estimated_entry_ids.add(eid)
    if is_new_estimate:
        global_token_tracker.record_turn(
            entry_id=eid, agent_id=agent, tag=tag, prediction=pred, explanation=expl,
            model_name=MODEL,
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
        "token_tally": usage_tally_report(),
    }


# ── LIVE_TAP: bridge the actual Blackboard event emitter ─────────────────────

def attach_live_board(board: Blackboard, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Forward live entries, scheduler events and counterfactual events to WebSockets."""
    global _live_board, _live_listener
    event_loop = loop or _live_loop
    if event_loop is None or not event_loop.is_running():
        raise RuntimeError("LIVE_TAP has no running server event loop")
    if _live_board is board:
        return
    detach_live_board()
    reset_token_usage(_usage_session_id, task_id=board.task_id)

    def on_board_event(board_event) -> None:
        if board_event.event_type in {
            BoardEventType.SCHEDULER_STATUS, BoardEventType.TURN_STARTED, BoardEventType.TURN_FINISHED,
        }:
            event = board_event.model_dump(mode="json", exclude_none=True)
            event["source"] = "LIVE_TAP"
            try:
                asyncio.run_coroutine_threadsafe(bus.publish(event), event_loop)
            except RuntimeError:
                logger.exception("[LIVE_TAP] Could not publish scheduler event")
            return
        if board_event.event_type is not BoardEventType.ENTRY_POSTED:
            branch_event = process_board_counterfactual_event(
                board_event, session_id=_usage_session_id, task_id=board.task_id,
            )
            if branch_event:
                try:
                    asyncio.run_coroutine_threadsafe(bus.publish(branch_event), event_loop)
                except RuntimeError:
                    logger.exception("[LIVE_TAP] Could not publish counterfactual branch event")
            return
        entry = board_event.payload.get("entry")
        if not entry:
            return
        event = _entry_to_event(entry)
        try:
            asyncio.run_coroutine_threadsafe(bus.publish(event), event_loop)
            asyncio.run_coroutine_threadsafe(bus.publish({
                "type": "token_tally", "source": "LIVE_TAP", "session_id": _usage_session_id,
                "task_id": board.task_id, "token_tally": event["token_tally"],
            }), event_loop)
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
                "summary": {
                    "outcome": raw.get("intelligibility", "UNRESOLVED") if isinstance(raw, dict) else "UNRESOLVED",
                    **bus.snapshot(),
                },
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
