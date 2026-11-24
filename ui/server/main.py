"""
Intelligible Blackboard — UI Server
ui/server/main.py

FastAPI app providing REST health checks and WebSocket board event streaming.

WebSocket endpoints:
  /ws        Live board event stream (mode controlled by BOARD_MODE env var)
  /ws/mock   Always-mock explicit endpoint (legacy / testing)
  POST /api/conversation/start  Start the local agent runner in LIVE_TAP mode

Run:
  uvicorn ui.server.main:app --reload --port 8000

  # Set mode before starting:
  BOARD_MODE=LOG_REPLAY  uvicorn ui.server.main:app --reload   # default; replay recorded session
  BOARD_MODE=LIVE_TAP    uvicorn ui.server.main:app --reload   # stream an in-process conversation
"""

import asyncio
import csv
import inspect
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.conversation import run_conversation
from agents.llm_client import OllamaClient
from ui.server.board_tap import (
    BOARD_MODE,
    attach_live_board,
    detach_live_board,
    start_board_tap,
    stop_board_tap,
)
from ui.server import board_tap
from ui.server.event_bus import bus
from ui.server.mock_stream import mock_event_stream_generator
from bench.archive_run import archive_run
from bench.scope.load_scope import ScopeError, load_scope, task_scope_view

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("ui-server")
_conversation_task: asyncio.Task | None = None
_task_sessions: dict[str, dict] = {}
_task_session_workers: dict[str, asyncio.Task] = {}
_active_task_session_id: str | None = None
_SCOPE_PATH = Path(__file__).resolve().parents[2] / "bench" / "scope" / "pilot_scope_v1.json"


class ConversationStartRequest(BaseModel):
    """Settings for a local agent conversation streamed over /ws."""

    prompt: str | None = None
    turns: int = Field(default=6, ge=1, le=12)
    retries: int = Field(default=2, ge=0, le=5)
    scenario: str = "default"


class TaskRunRequest(BaseModel):
    task_id: str = Field(min_length=1)
    scope_version: str | None = None
    counterfactual_fraction: Literal[0, 0.33, 0.66, 1.0] = 0.0
    mode: Literal["LIVE"] = "LIVE"


def _configured_cors_origins() -> list[str]:
    raw = os.environ.get("UI_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# ── Lifespan: start the board tap once at server startup ─────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Intelligible Blackboard UI Server starting (mode=%s) ===", BOARD_MODE)
    await start_board_tap()
    yield
    if _conversation_task is not None and not _conversation_task.done():
        _conversation_task.cancel()
    await stop_board_tap()
    logger.info("=== UI Server shutting down ===")


app = FastAPI(
    title="Intelligible Blackboard — UI Server",
    description="FastAPI + WebSocket backend for live board visualization and benchmarking",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "Intelligible Blackboard UI Server",
        "status": "ok",
        "mode": BOARD_MODE,
        "ws_endpoints": {
            "/ws": f"Live board event stream (mode={BOARD_MODE})",
            "/ws/mock": "Explicit mock replay endpoint with configurable delay (?delay=s)",
        },
        "conversation_start_endpoint": "/api/conversation/start (POST; LIVE_TAP mode only)",
        "task_endpoints": ["GET /api/tasks", "POST /api/tasks/run", "GET /api/tasks/{session_id}/status"],
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "ui-server", "mode": BOARD_MODE}


def _load_task_scope() -> dict:
    return load_scope(_SCOPE_PATH)


def _public_task(task: dict) -> dict:
    view = task_scope_view(_load_task_scope(), task["workload"], task["task_id"])
    return {
        "task_id": view["task_id"], "workload": view["workload"], "split": view["split"],
        "evaluator_mode": view["evaluator_mode"], "adapted": view["adapted"],
        "answer_type": view["answer_type"], "adaptation_flags": view["adaptation_flags"],
    }


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _find_run_artifacts(output_root: Path, task_id: str, metric_name: str = "success") -> tuple[Path | None, dict | None, dict | None, float | None]:
    manifests = sorted(output_root.rglob("run_manifest.json"), key=lambda path: path.stat().st_mtime)
    manifest_path = manifests[-1] if manifests else None
    manifest = _load_json(manifest_path) if manifest_path else None
    records = sorted(output_root.rglob("krama_task_records/*.json"), key=lambda path: path.stat().st_mtime)
    record = next((_load_json(path) for path in reversed(records)
                   if (_load_json(path) or {}).get("task_id") == task_id), None)
    score = None
    if manifest_path:
        for measure in sorted(manifest_path.parent.rglob("*_measures_*.csv")):
            try:
                with measure.open(encoding="utf-8", newline="") as stream:
                    for row in csv.DictReader(stream):
                        if row.get("metric") == metric_name and row.get("value") not in (None, ""):
                            score = float(row["value"])
            except (OSError, ValueError):
                continue
    return manifest_path.parent if manifest_path else None, manifest, record, score


def _terminal_outcome(record: dict | None, manifest: dict | None, return_code: int | None) -> tuple[str, str]:
    if record:
        raw = str(record.get("execution_outcome") or record.get("agent_conversation_outcome") or "").casefold()
        errors = record.get("errors") or []
        reason = " | ".join(
            str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in errors
        )
        combined = f"{raw} {reason}".casefold().replace("-", "_")
        if "deadlock" in combined:
            return "deadlock", reason
        if any(word in combined for word in ("turn_limit", "turn limit", "max_turns", "iteration_limit")):
            return "limit", reason
        if any(word in combined for word in ("timeout", "timed out", "deadline", "wall_clock")):
            return "limit", reason
        if raw in {"scheduler_consensus", "agreement", "consensus"}:
            return "agreement", reason
    if manifest and manifest.get("status") == "success":
        return "scored", ""
    return "failure", str((manifest or {}).get("error") or f"runner exited with status {return_code}")


def _persist_ui_failure(output_root: Path, session: dict, reason: str) -> Path:
    run_root = output_root / f"run-ui-{session['session_id']}"
    records = run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records"
    logs = run_root / "driver_logs"
    records.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1, "run_id": f"ui-{session['session_id']}", "task_id": session["task_id"],
        "status": "failed", "execution_outcome": "execution_failure",
        "errors": [{"type": "UIRunnerFailure", "message": reason}],
        "workflow_counters": {},
    }
    (records / f"{session['task_id']}-{session['session_id']}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    (run_root / "run_manifest.json").write_text(json.dumps({
        "schema_version": 1, "run_id": record["run_id"], "task_id": session["task_id"],
        "evaluator_mode": session["evaluator_mode"], "adapted": session["adapted"],
        "status": "failed", "error": reason, "finished_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    return run_root


def _record_terminal_failure(run_root: Path, session: dict, reason: str, outcome: str) -> Path:
    """Turn a partially-written runner directory into a terminal, auditable result."""
    records = run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records"
    records.mkdir(parents=True, exist_ok=True)
    record_path = next(iter(records.glob("*.json")), records / f"{session['task_id']}-ui-failure.json")
    record = _load_json(record_path) or {
        "schema_version": 1,
        "run_id": run_root.name,
        "task_id": session["task_id"],
    }
    record["status"] = "failed"
    record["execution_outcome"] = outcome
    record["ui_terminal_failure"] = True
    errors = record.get("errors")
    if not isinstance(errors, list):
        errors = []
    errors.append({"type": "UITaskSessionFailure", "message": reason})
    record["errors"] = errors
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = run_root / "run_manifest.json"
    manifest = _load_json(manifest_path) or {
        "schema_version": 1,
        "run_id": run_root.name,
        "task_id": session["task_id"],
    }
    manifest.update({
        "status": "failed",
        "error": reason,
        "terminal_outcome": outcome,
        "ui_terminal_failure": True,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record_path


async def _execute_scope_task(session: dict, task: dict, scope: dict) -> dict:
    """Run the existing CLI runner and forward actual child-board events to the UI bus."""
    benchmark_root = Path(os.environ.get("KRAMABENCH_ROOT", str(Path.home() / "kramabench-pinned"))).expanduser()
    evaluator_python = os.environ.get("KRAMABENCH_EVALUATOR_PYTHON", str(Path.home() / "kramabench-eval-venv" / "bin" / "python"))
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    output_root = Path(os.environ.get("KRAMA_UI_OUTPUT_ROOT", str(Path.home() / "krama-results" / "ui"))).expanduser() / session["session_id"]
    output_root.mkdir(parents=True, exist_ok=False)
    event_file = output_root / "board_events.jsonl"
    event_file.touch()
    stdout_path = output_root / "runner.stdout.log"
    stderr_path = output_root / "runner.stderr.log"
    limits = scope["stopping_rules"]
    counterfactual_seed = int(session["session_id"].replace("-", "")[:8], 16)
    command = [
        sys.executable, "-m", "bench.kramabench_runner",
        "--kramabench-root", str(benchmark_root), "--workload", task["workload"],
        "--task-id", task["task_id"], "--output-directory", str(output_root),
        "--evaluator-python", evaluator_python, "--evaluator-mode", task["evaluator_mode"],
        "--model", scope["model"]["name"], "--base-url", base_url,
        "--model-seed", str(scope["model_options"]["seed"]),
        "--max-turns", str(limits["max_turns"]), "--max-retries", str(limits["max_retries_per_generation"]),
        "--task-wall-clock-cap-seconds", str(limits["task_wall_clock_cap_seconds"]),
        "--counterfactual-fraction", str(session["counterfactual_fraction"]),
        "--counterfactual-seed", str(counterfactual_seed),
        "--counterfactual-streak-threshold", str(limits["counterfactual_streak_threshold"]),
        "--max-counterfactual-simulations", str(limits["max_counterfactual_simulations_per_task"]),
        "--archive-label", f"ui-{session['session_id']}", "--archive-driver-log", str(event_file),
        "--approve-genuine-run",
    ]
    environment = os.environ.copy()
    environment.update({
        "KRAMA_UI_BOARD_EVENT_FILE": str(event_file),
        "KRAMA_UI_SESSION_ID": session["session_id"],
    })
    process = None
    event_lines_seen = 0
    timeout_seconds = int(limits["task_wall_clock_cap_seconds"]) + 300
    try:
        with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=Path(__file__).resolve().parents[2], env=environment,
                stdout=stdout_stream, stderr=stderr_stream,
            )
            started = asyncio.get_running_loop().time()
            while process.returncode is None:
                try:
                    lines = event_file.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                for line in lines[event_lines_seen:]:
                    event_lines_seen += 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "usage_record":
                        tally = board_tap.ingest_usage_record(event.get("record", {}))
                        await bus.publish({
                            "type": "token_tally", "source": "LIVE",
                            "session_id": session["session_id"], "task_id": task["task_id"],
                            "token_tally": tally,
                        })
                        continue
                    if event.get("type") == "board_entry":
                        event = board_tap.process_board_entry_event(event)
                    elif event.get("type") in board_tap.COUNTERFACTUAL_EVENT_TYPES:
                        event = board_tap.process_counterfactual_event(event)
                        if event is None:
                            continue
                    elif event.get("type") in board_tap.SCHEDULER_EVENT_TYPES:
                        event = board_tap.process_scheduler_event(
                            event, session_id=session["session_id"], task_id=task["task_id"],
                        )
                        if event is None:
                            continue
                    session["entry_count"] += int(event.get("type") == "board_entry")
                    session["last_entry_id"] = event.get("last_entry_id", session["last_entry_id"])
                    await bus.publish(event)
                    if event.get("type") == "board_entry":
                        await bus.publish({
                            "type": "token_tally", "source": "LIVE",
                            "session_id": session["session_id"], "task_id": task["task_id"],
                            "token_tally": event["token_tally"],
                        })
                if asyncio.get_running_loop().time() - started > timeout_seconds:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                    raise TimeoutError(f"KramaBench UI task exceeded {timeout_seconds}s")
                await asyncio.sleep(0.2)
            return_code = await process.wait()
        lines = event_file.read_text(encoding="utf-8").splitlines()
        for line in lines[event_lines_seen:]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "usage_record":
                tally = board_tap.ingest_usage_record(event.get("record", {}))
                await bus.publish({
                    "type": "token_tally", "source": "LIVE", "session_id": session["session_id"],
                    "task_id": task["task_id"], "token_tally": tally,
                })
                continue
            if event.get("type") == "board_entry":
                event = board_tap.process_board_entry_event(event)
            elif event.get("type") in board_tap.COUNTERFACTUAL_EVENT_TYPES:
                event = board_tap.process_counterfactual_event(event)
                if event is None:
                    continue
            elif event.get("type") in board_tap.SCHEDULER_EVENT_TYPES:
                event = board_tap.process_scheduler_event(
                    event, session_id=session["session_id"], task_id=task["task_id"],
                )
                if event is None:
                    continue
            session["entry_count"] += int(event.get("type") == "board_entry")
            session["last_entry_id"] = event.get("last_entry_id", session["last_entry_id"])
            await bus.publish(event)
            if event.get("type") == "board_entry":
                await bus.publish({
                    "type": "token_tally", "source": "LIVE", "session_id": session["session_id"],
                    "task_id": task["task_id"], "token_tally": event["token_tally"],
                })
        metric_name = (task.get("metric_names") or ["success"])[0]
        run_root, manifest, record, score = _find_run_artifacts(output_root, task["task_id"], metric_name)
        if run_root is None:
            reason = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:] or "runner failed before writing its run manifest"
            run_root = _persist_ui_failure(output_root, session, reason)
            manifest = _load_json(run_root / "run_manifest.json")
            record = _load_json(next((run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records").glob("*.json")))
        outcome, reason = _terminal_outcome(record, manifest, return_code)
        transcript_path = Path(str(record.get("conversation_transcript"))) if record and record.get("conversation_transcript") else None
        transcript = _load_json(transcript_path) if transcript_path else None
        usage_report = record.get("usage_ledger") if record else None
        if not isinstance(usage_report, dict):
            usage_report = board_tap.usage_tally_report()
        tokens = usage_report.get("total_tokens")
        public_usage_report = {key: value for key, value in usage_report.items() if key != "records"}
        counters = record.get("workflow_counters", {}) if record else {}
        summary = {
            "outcome": outcome, "reason": reason, "iterations": len(transcript.get("turns", [])) if transcript else None,
            "deadlocks": int(counters.get("deadlocks", 0) or (outcome == "deadlock")), "tokens": tokens,
            "usage_ledger": public_usage_report,
            "evaluator_score_available": score is not None, "evaluator_mode": task["evaluator_mode"],
            "adapted": bool(task["adapted"]), "entry_count": session["entry_count"],
            "last_entry_id": session["last_entry_id"],
        }
        if score is not None:
            summary["evaluator_score"] = score
        return {"status": "completed" if score is not None else "failed", "summary": summary,
                "error": reason if score is None else None, "run_root": str(run_root),
                "archive_path": str(Path(__file__).resolve().parents[2] / "archive" / f"ui-{session['session_id']}")}
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        run_root, manifest, record, score = _find_run_artifacts(
            output_root, task["task_id"], (task.get("metric_names") or ["success"])[0]
        )
        if run_root is None:
            run_root = _persist_ui_failure(output_root, session, reason)
            manifest = _load_json(run_root / "run_manifest.json")
            record_path = next((run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records").glob("*.json"), None)
            record = _load_json(record_path) if record_path else None
        _record_terminal_failure(
            run_root, session, reason,
            "time_limit" if isinstance(exc, TimeoutError) else "execution_failure",
        )
        archive_path = Path(__file__).resolve().parents[2] / "archive" / f"ui-{session['session_id']}"
        if archive_path.exists():
            archive_path = archive_path.with_name(archive_path.name + "-finalized")
        if not archive_path.exists():
            extra = [path for path in (event_file, stdout_path, stderr_path) if path.exists()]
            archive_path = archive_run(run_root, f"ui-{session['session_id']}", extra_files=extra)
        transcript_path = Path(str(record.get("conversation_transcript"))) if record and record.get("conversation_transcript") else None
        transcript = _load_json(transcript_path) if transcript_path else None
        usage = record.get("provider_token_usage") if record else None
        token_counts = [usage.get(key) for key in ("prompt_tokens", "output_tokens")] if isinstance(usage, dict) else []
        tokens = sum(value for value in token_counts if isinstance(value, (int, float))) if token_counts and all(
            isinstance(value, (int, float)) for value in token_counts
        ) else None
        counters = record.get("workflow_counters", {}) if record else {}
        summary = {
            "outcome": "limit" if isinstance(exc, TimeoutError) else "failure", "reason": reason,
            "iterations": len(transcript.get("turns", [])) if transcript else None,
            "deadlocks": int(counters.get("deadlocks", 0) or 0), "tokens": tokens,
            "usage_ledger": {
                key: value for key, value in (
                    record.get("usage_ledger", {}) if record else board_tap.usage_tally_report()
                ).items() if key != "records"
            },
            "evaluator_score_available": score is not None,
            "evaluator_mode": task["evaluator_mode"], "adapted": bool(task["adapted"]),
            "entry_count": session["entry_count"], "last_entry_id": session["last_entry_id"],
        }
        if score is not None:
            summary["evaluator_score"] = score
        return {"status": "completed" if score is not None else "failed", "error": reason,
                "summary": summary, "run_root": str(run_root), "archive_path": str(archive_path)}


async def _run_task_session(session_id: str, task: dict, scope: dict) -> None:
    global _active_task_session_id
    session = _task_sessions[session_id]
    try:
        result = await _execute_scope_task(session, task, scope)
        session.update(result)
        session["finished_at"] = datetime.now(timezone.utc).isoformat()
        session["status"] = result["status"]
        if result.get("error"):
            await bus.publish({"type": "session_error", "source": "LIVE", "session_id": session_id,
                               "task_id": task["task_id"], "error": result["error"]})
        if result.get("summary", {}).get("usage_ledger"):
            await bus.publish({"type": "token_tally", "source": "LIVE", "session_id": session_id,
                               "task_id": task["task_id"],
                               "token_tally": result["summary"]["usage_ledger"]})
        await bus.publish({"type": "session_summary", "source": "LIVE", "session_id": session_id,
                           "task_id": task["task_id"], "summary": result["summary"]})
    except Exception as exc:
        logger.exception("UI task session failed")
        reason = str(exc) or type(exc).__name__
        session.update({"status": "failed", "error": reason, "finished_at": datetime.now(timezone.utc).isoformat()})
        await bus.publish({"type": "session_error", "source": "LIVE", "session_id": session_id,
                           "task_id": task["task_id"], "error": reason})
        await bus.publish({"type": "session_summary", "source": "LIVE", "session_id": session_id,
                           "task_id": task["task_id"], "summary": {
                               "outcome": "failure", "reason": reason, "iterations": None,
                               "deadlocks": 0, "tokens": 0, "evaluator_score_available": False,
                               "evaluator_mode": task["evaluator_mode"], "adapted": bool(task["adapted"]),
                               "entry_count": session["entry_count"], "last_entry_id": session["last_entry_id"],
                           }})
    finally:
        await bus.publish({"type": "stream_complete", "source": "LIVE", "session_id": session_id,
                           "task_id": task["task_id"], "entry_count": session["entry_count"],
                           "last_entry_id": session["last_entry_id"]})
        if _active_task_session_id == session_id:
            _active_task_session_id = None


@app.post("/api/conversation/start", status_code=202)
async def start_live_conversation(request: ConversationStartRequest):
    """Run the actual local conversation runner and stream its board entries."""
    global _conversation_task
    if BOARD_MODE != "LIVE_TAP":
        raise HTTPException(status_code=409, detail="Start the server with BOARD_MODE=LIVE_TAP")
    if request.scenario not in {"default", "disagreement"}:
        raise HTTPException(status_code=422, detail="scenario must be default or disagreement")
    if _conversation_task is not None and not _conversation_task.done():
        raise HTTPException(status_code=409, detail="A conversation is already running")

    session_id = uuid4().hex
    board_tap.reset_token_usage(session_id)
    bus.reset_history()
    await bus.publish({"type": "session_started", "source": "LIVE_TAP", "mode": "LIVE",
                       "session_id": session_id})

    async def run_and_publish_result() -> None:
        try:
            loop = asyncio.get_running_loop()

            def on_usage(value) -> None:
                usage_record = dict(value) if isinstance(value, dict) else vars(value)
                usage_record.setdefault("session_id", session_id)
                tally = board_tap.ingest_usage_record(usage_record)
                asyncio.run_coroutine_threadsafe(bus.publish({
                    "type": "token_tally", "source": "LIVE_TAP", "session_id": session_id,
                    "task_id": usage_record.get("task_id"), "token_tally": tally,
                }), loop)

            conversation_kwargs = {
                "scenario": request.scenario,
                "on_board_created": attach_live_board,
            }
            try:
                parameters = inspect.signature(run_conversation).parameters
            except (TypeError, ValueError):
                parameters = {}
            supports_extra = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
            if supports_extra or "on_usage" in parameters:
                conversation_kwargs["on_usage"] = on_usage
            if supports_extra or "session_id" in parameters:
                conversation_kwargs["session_id"] = session_id
            report, transcript_path = await asyncio.to_thread(
                run_conversation,
                OllamaClient(),
                request.prompt,
                request.turns,
                request.retries,
                **conversation_kwargs,
            )
            await bus.publish({
                "type": "session_summary",
                "source": "LIVE_TAP",
                "summary": {
                    "outcome": report.get("outcome", "unknown"),
                    "scheduler_status": report.get("scheduler_status"),
                    "failures": report.get("failures", []),
                    **bus.snapshot(),
                },
                "transcript_path": str(transcript_path),
            })
        except Exception as exc:
            logger.exception("Live agent conversation failed")
            await bus.publish({
                "type": "session_error",
                "source": "LIVE_TAP",
                "error": str(exc),
            })
        finally:
            detach_live_board()
            await bus.publish({"type": "stream_complete", "source": "LIVE_TAP"})

    _conversation_task = asyncio.create_task(run_and_publish_result(), name="live-agent-conversation")
    return {"status": "started", "source": "LIVE_TAP", "session_id": session_id}


@app.get("/api/tasks")
async def list_scope_tasks():
    try:
        scope = _load_task_scope()
    except ScopeError as exc:
        raise HTTPException(status_code=503, detail=f"Frozen task scope is unavailable: {exc}") from exc
    return {"scope_id": scope["scope_id"], "scope_version": scope["content_sha256"],
            "tasks": [_public_task(task) for task in scope["tasks"]]}


@app.post("/api/tasks/run", status_code=202)
async def start_scope_task(request: TaskRunRequest):
    global _active_task_session_id
    if os.environ.get("KRAMA_UI_APPROVE_GENUINE_RUN", "").strip().casefold() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Real model execution is disabled. Set KRAMA_UI_APPROVE_GENUINE_RUN=1 after approving a genuine run.")
    if BOARD_MODE != "LIVE_TAP":
        raise HTTPException(status_code=409, detail="Start the UI server with BOARD_MODE=LIVE_TAP to run and stream a real task.")
    if _active_task_session_id is not None:
        active = _task_sessions.get(_active_task_session_id, {})
        if active.get("status") == "running":
            raise HTTPException(status_code=409, detail="A KramaBench task session is already active.")
        _active_task_session_id = None
    if _conversation_task is not None and not _conversation_task.done():
        raise HTTPException(status_code=409, detail="A UI conversation is already active.")
    try:
        scope = _load_task_scope()
    except ScopeError as exc:
        raise HTTPException(status_code=503, detail=f"Frozen task scope is unavailable: {exc}") from exc
    if request.scope_version and request.scope_version not in {scope["scope_id"], scope["content_sha256"]}:
        raise HTTPException(status_code=409, detail="Requested scope version does not match the frozen pilot scope.")
    task = next((item for item in scope["tasks"] if item["task_id"] == request.task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="task_id is not present in the frozen pilot scope.")
    session_id = uuid4().hex
    session = {
        "session_id": session_id, "task_id": task["task_id"], "workload": task["workload"],
        "status": "running", "mode": "LIVE", "evaluator_mode": task["evaluator_mode"],
        "adapted": bool(task["adapted"]), "counterfactual_fraction": float(request.counterfactual_fraction),
        "entry_count": 0, "last_entry_id": None, "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _task_sessions[session_id] = session
    _active_task_session_id = session_id
    board_tap.reset_token_usage(session_id, task["task_id"])
    bus.reset_history()
    from agents.conversation import DEFAULT_AGENTS
    await bus.publish({
        "type": "session_started", "source": "LIVE", "mode": "LIVE", "session_id": session_id,
        "task_id": task["task_id"], "counterfactual_fraction": session["counterfactual_fraction"],
        "model": scope["model"]["name"], "agents": [agent_id for agent_id, _ in DEFAULT_AGENTS],
        "entry_count": 0, "last_entry_id": None,
    })
    _task_session_workers[session_id] = asyncio.create_task(
        _run_task_session(session_id, task, scope), name=f"krama-ui-session-{session_id}"
    )
    return {"session_id": session_id, "task_id": task["task_id"], "status": "running", "mode": "LIVE"}


@app.get("/api/tasks/{session_id}/status")
async def task_session_status(session_id: str):
    session = _task_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown UI task session.")
    response = {
        "session_id": session_id, "task_id": session["task_id"], "status": session["status"],
        "mode": session["mode"], "evaluator_mode": session["evaluator_mode"],
        "adapted": session["adapted"], "counterfactual_fraction": session["counterfactual_fraction"],
        "started_at": session["started_at"], "entry_count": session["entry_count"],
        "last_entry_id": session["last_entry_id"],
    }
    if session.get("finished_at"):
        response["finished_at"] = session["finished_at"]
    if session["status"] in {"completed", "failed"}:
        response["summary"] = session.get("summary")
        if session.get("error"):
            response["error"] = session["error"]
        if session.get("summary", {}).get("evaluator_score_available"):
            response["evaluator_score"] = session["summary"].get("evaluator_score")
        response["archive_path"] = session.get("archive_path")
    return response


# ── Primary WebSocket: live event bus ─────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Primary WebSocket endpoint.

    Subscribes to the EventBus (fed by board_tap.py) and fans out events to
    this client.  Each client gets its own queue — slow clients cannot block
    others.

    The 'mode' field in connection_ack tells the frontend whether to render
    a LIVE or REPLAY badge.

    The live bridge subscribes to the Blackboard event emitter in
    ``ui.server.board_tap``; the endpoint forwards events already published on
    the shared EventBus.
    """
    await websocket.accept()
    q = bus.subscribe()
    logger.info("[/ws] Client connected  (mode=%s)", BOARD_MODE)

    try:
        # Handshake — lets the frontend know mode immediately
        await websocket.send_json({
            "type": "connection_ack",
            "mode": BOARD_MODE,   # "LIVE_TAP" | "LOG_REPLAY"
            "message": f"Connected to Intelligible Blackboard UI Server ({BOARD_MODE})",
            **bus.snapshot(),
        })

        async for event in bus.stream(q):
            await websocket.send_json(event)

    except WebSocketDisconnect:
        logger.info("[/ws] Client disconnected.")
    except Exception as e:
        logger.error("[/ws] Error: %s", e)
    finally:
        bus.unsubscribe(q)


# ── Legacy / testing mock endpoint ────────────────────────────────────────────

@app.websocket("/ws/mock")
async def websocket_mock_endpoint(websocket: WebSocket, delay: float = 1.0):
    """
    Explicit mock WebSocket endpoint — always replays canned synthetic stream.
    Used for isolated frontend testing when board_tap is not configured.
    Supports ?delay=<seconds> query parameter.
    """
    await websocket.accept()
    logger.info("[/ws/mock] Client connected (delay=%.1fs)", delay)
    try:
        await websocket.send_json({
            "type": "connection_ack",
            "mode": "MOCK_STREAM",
            "message": "Connected to synthetic mock event stream",
        })
        async for event in mock_event_stream_generator(delay_seconds=delay):
            await websocket.send_json(event)
        await websocket.send_json({"type": "stream_complete", "source": "MOCK_STREAM"})
    except WebSocketDisconnect:
        logger.info("[/ws/mock] Client disconnected.")
    except Exception as e:
        logger.error("[/ws/mock] Error: %s", e)
