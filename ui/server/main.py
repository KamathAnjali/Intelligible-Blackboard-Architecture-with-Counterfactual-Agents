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

from agents.conversation import DEFAULT_AGENTS, run_conversation
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

    prompt: str | None = Field(default=None, max_length=12000)
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


def _genuine_run_approved() -> bool:
    return os.environ.get(
        "UI_APPROVE_GENUINE_RUN", os.environ.get("KRAMA_UI_APPROVE_GENUINE_RUN", "")
    ).strip().casefold() in {"1", "true", "yes"}


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


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BENCHMARK_RESULTS_ROOT = _REPO_ROOT / "results" / "benchmarks"
_MSCORE_RESULTS_ROOT = _BENCHMARK_RESULTS_ROOT / "mscore" / "paired-blackboard-100-v1"
_KRAMABENCH_RESULTS_ROOT = _BENCHMARK_RESULTS_ROOT / "kramabench" / "latest-20261125"


def _mscore_results_root() -> Path:
    configured = os.environ.get("MSCORE_RESULTS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if _MSCORE_RESULTS_ROOT.is_dir():
        return _MSCORE_RESULTS_ROOT.resolve()
    # Backward compatibility for workspaces that have not moved the imported run.
    return (_REPO_ROOT / "results" / "mscore-paired-100-v1").resolve()


def _mscore_direct_results_root() -> Path:
    configured = os.environ.get("MSCORE_DIRECT_RESULTS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    canonical = _BENCHMARK_RESULTS_ROOT / "mscore" / "direct-baseline-100-v1"
    if canonical.is_dir():
        return canonical.resolve()
    return (_REPO_ROOT / "results" / "mscore-direct-baseline-100-v1").resolve()


def _benchmark_result_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows


def _safe_artifact(root: Path, relative: str | None) -> Path | None:
    """Resolve a saved result reference strictly within its imported artifact tree."""
    if not relative:
        return None
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _safe_result_directory(root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_dir():
        return None
    return candidate


def _total_usage_tokens(usage: dict) -> int | None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("output_tokens")
    if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
        return int(prompt + completion)
    return None


def _safe_transcript_detail(transcript: dict | None, result: dict, *, transcript_available: bool) -> dict:
    """Expose replay fields only; never return expected/reference fields from source records."""
    transcript = transcript or {}
    board = transcript.get("board") if isinstance(transcript.get("board"), dict) else {}
    entries = board.get("entries") if isinstance(board.get("entries"), list) else transcript.get("entries", [])
    events = board.get("counterfactual_events") if isinstance(board.get("counterfactual_events"), list) else []
    run = {
        key: result.get(key) for key in (
            "run_id", "condition", "run", "result_source", "status", "outcome", "score", "correct", "scored",
            "score_label", "metric_summary", "evaluation_status", "prediction", "total_tokens", "elapsed_seconds", "iterations",
            "deadlock_count", "model", "evaluator_mode", "adapted", "failure_reason",
            "counterfactual_fraction", "counterfactual_simulations",
        ) if key in result
    }
    return {
        "run": run,
        "transcript_available": transcript_available,
        "task": transcript.get("task"),
        "outcome": transcript.get("outcome") or result.get("outcome"),
        "entries": entries if isinstance(entries, list) else [],
        "counterfactual_events": events,
        "counterfactual_attempts": transcript.get("counterfactual_attempts", []),
        "counterfactual_skips": transcript.get("counterfactual_skips", []),
    }


def _math500_rows() -> tuple[Path, list[dict]]:
    root = _BENCHMARK_RESULTS_ROOT / "math500" / "latest-20261125"
    result_file = root / "summary.jsonl"
    if not result_file.is_file():
        result_file = root / "results.jsonl"
    rows = _benchmark_result_rows(result_file)
    metadata = _load_json(root / "summary.json") or {}
    for row in rows:
        row.setdefault("model", metadata.get("model"))
    return root, rows


def _math500_trace_reference(row: dict) -> str | None:
    """Map source-host absolute MATH-500 trace paths to imported local files."""
    condition = str(row.get("condition") or "")
    if condition not in {"cf-00", "cf-33", "cf-50", "cf-66"}:
        return None
    parts = str(row.get("task_id") or "").split("/")
    if len(parts) != 3 or parts[0] != "test" or not parts[2].endswith(".json"):
        return None
    domain, item_id = parts[1], parts[2][:-5]
    if not domain or not item_id or not all(ch.isalnum() or ch in "_-" for ch in domain + item_id):
        return None
    return f"{condition}/math500-{domain}-{item_id}/transcript.json"


def _medagent_rows() -> tuple[Path, list[dict]]:
    root = _BENCHMARK_RESULTS_ROOT / "medagentbench" / "latest-20261125"
    return root, _benchmark_result_rows(root / "results.jsonl")


def _kramabench_rows() -> tuple[Path, list[dict]]:
    """Read the imported combined KramaBench batch and attach safe replay paths."""
    root = _KRAMABENCH_RESULTS_ROOT
    rows: list[dict] = []
    csv_path = root / "results.csv"
    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            source_rows = list(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error):
        return root, rows

    for source in source_rows:
        attempt_dir = _safe_result_directory(root, source.get("result_directory"))
        if attempt_dir is None:
            continue
        manifest_paths = sorted(attempt_dir.glob("*/run_manifest.json"))
        if not manifest_paths:
            continue
        manifest_path = manifest_paths[0]
        run_root = manifest_path.parent
        manifest = _load_json(manifest_path) or {}
        if manifest.get("task_id") != source.get("task_id"):
            continue
        record_paths = sorted((run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_task_records").glob("*.json"))
        record = _load_json(record_paths[0]) if record_paths else {}
        record = record or {}
        usage = record.get("provider_token_usage") if isinstance(record.get("provider_token_usage"), dict) else {}
        counters = record.get("workflow_counters") if isinstance(record.get("workflow_counters"), dict) else {}
        transcript_paths = sorted((run_root / "system_scratch" / "KramaBenchPXPSystem" / "krama_conversations").rglob("transcript.json"))
        transcript_path = transcript_paths[0] if transcript_paths else None
        try:
            success = float(source["success"]) if source.get("success") not in (None, "") else None
        except (TypeError, ValueError):
            success = None
        try:
            mae = float(source["mean_absolute_error"]) if source.get("mean_absolute_error") not in (None, "") else None
        except (TypeError, ValueError):
            mae = None
        try:
            mse = float(source["mean_squared_error"]) if source.get("mean_squared_error") not in (None, "") else None
        except (TypeError, ValueError):
            mse = None
        try:
            rae = float(source["rae_score"]) if source.get("rae_score") not in (None, "") else None
        except (TypeError, ValueError):
            rae = None
        status = str(source.get("status") or "unknown")
        condition = str(source.get("condition") or "unknown")
        score = success if success is not None else mae
        score_label = "Success" if success is not None else "MAE" if mae is not None else None
        metric_summary = None
        if mae is not None:
            metric_summary = f"MAE {mae:g}" + (f" · MSE {mse:g}" if mse is not None else "") + (f" · RAE {rae:g}" if rae is not None else "")
        errors = record.get("errors") if isinstance(record.get("errors"), list) else []
        failure_reason = " | ".join(
            str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in errors
        ) or None
        run_id = f"{condition}:{manifest.get('run_id') or run_root.name}"
        rows.append({
            "run_id": run_id, "task_id": source.get("task_id"), "condition": condition, "workload": source.get("workload"),
            "result_source": source.get("result_source"), "status": status,
            "outcome": source.get("board_outcome") or status,
            "score": score, "score_label": score_label,
            "correct": success == 1.0 if success is not None else None,
            "scored": status == "scored", "evaluation_status": status,
            "prediction": record.get("agent_answer") or record.get("answer"),
            "metric_summary": metric_summary,
            "total_tokens": _total_usage_tokens(usage),
            "elapsed_seconds": record.get("runtime_seconds") or usage.get("wall_seconds"),
            "iterations": usage.get("calls"), "deadlock_count": source.get("deadlocks"),
            "model": manifest.get("model"), "evaluator_mode": manifest.get("evaluator_mode"),
            "adapted": manifest.get("evaluator_mode") != "official",
            "failure_reason": failure_reason,
            "counterfactual_fraction": manifest.get("counterfactual_fraction", record.get("counterfactual_fraction")),
            "counterfactual_simulations": counters.get("counterfactual_simulations", source.get("replays", 0)),
            "transcript_available": transcript_path is not None,
            "result_directory": source.get("result_directory"),
            "transcript_path": transcript_path.relative_to(root).as_posix() if transcript_path else None,
            "record_path": record_paths[0].relative_to(root).as_posix() if record_paths else None,
        })
    return root, rows


def _parse_mscore_result_row(row: dict[str, str]) -> dict:
    number_fields = {
        "counterfactual_fraction", "attempt", "score", "iterations", "deadlock_count",
        "reviewer_rejections", "counterfactual_triggers", "counterfactual_simulations",
        "counterfactual_changed_outcome", "num_ctx", "num_predict", "temperature", "model_seed",
        "live_prompt_tokens", "live_completion_tokens", "simulation_prompt_tokens",
        "simulation_completion_tokens", "total_prompt_tokens", "total_completion_tokens",
        "total_tokens", "live_elapsed_seconds", "simulation_elapsed_seconds", "elapsed_seconds",
    }
    boolean_fields = {"retrospective_recovery", "completed_without_crash", "scored", "correct", "adapted"}
    parsed: dict = {}
    for key, value in row.items():
        if value == "":
            parsed[key] = None
        elif key in number_fields:
            try:
                parsed[key] = float(value) if "." in value else int(value)
            except ValueError:
                parsed[key] = None
        elif key in boolean_fields:
            parsed[key] = value.casefold() == "true"
        elif key == "counterfactual_capable_agents":
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed[key] = []
        else:
            parsed[key] = value
    return parsed


@app.get("/api/benchmark-results/mscore")
async def mscore_results_index():
    """Read the imported paired MSCoRe run without touching evaluator data."""
    root = _mscore_results_root()
    manifest = _load_json(root / "results_manifest.json")
    csv_path = root / "results.csv"
    if not root.is_dir() or manifest is None or not csv_path.is_file():
        raise HTTPException(status_code=404, detail=f"MSCoRe results not found under {root}")
    try:
        with csv_path.open(encoding="utf-8", newline="") as stream:
            rows = [_parse_mscore_result_row(row) for row in csv.DictReader(stream)]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read MSCoRe results: {exc}") from exc
    task_ids = sorted({row["task_id"] for row in rows if row.get("task_id")})
    summaries = {}
    for condition in ("cf00", "cf100"):
        subset = [row for row in rows if row.get("condition") == condition]
        summaries[condition] = {
            "attempts": len(subset),
            "scored": sum(row.get("scored") is True for row in subset),
            "correct": sum(row.get("correct") is True for row in subset),
            "failed_before_scoring": sum(row.get("evaluation_status") == "failed_before_scoring" for row in subset),
            "unscored": sum(row.get("evaluation_status") == "not_scored" for row in subset),
            "counterfactual_simulations": sum(row.get("counterfactual_simulations") or 0 for row in subset),
        }
    direct_root = _mscore_direct_results_root()
    direct_rows = []
    direct_csv = direct_root / "results.csv"
    if direct_csv.is_file():
        with direct_csv.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if not row.get("task_id"):
                    continue
                correct = row.get("correct", "").casefold() == "true"
                direct_rows.append({
                    "task_id": row.get("task_id"), "condition": row.get("condition"),
                    "outcome": row.get("status"), "prediction": row.get("prediction"),
                    "score": float(row["score"]) if row.get("score") not in (None, "") else None,
                    "correct": correct, "scored": row.get("scored", "").casefold() == "true",
                    "evaluator_mode": "project-adapted-exact", "adapted": True,
                    "model": row.get("model"),
                    "total_tokens": sum(int(row.get(key) or 0) for key in ("prompt_tokens", "completion_tokens")),
                    "elapsed_seconds": float(row["wall_seconds"]) if row.get("wall_seconds") else None,
                    "counterfactual_simulations": 0,
                })
    return {"manifest": manifest, "task_count": len(task_ids), "rows": rows, "by_condition": summaries,
            "direct_baseline": {"manifest": _load_json(direct_root / "results_manifest.json"),
                                "task_count": len({row["task_id"] for row in direct_rows}),
                                "rows": direct_rows}}


@app.get("/api/benchmark-dashboard/catalog")
async def benchmark_dashboard_catalog():
    """Return task IDs and result availability without exposing benchmark references."""
    mscore_root = _mscore_results_root()
    mscore_ids: set[str] = set()
    mscore_runs: dict[str, int] = {}
    mscore_attempts: dict[str, list[dict]] = {}
    mscore_manifest = _load_json(mscore_root / "results_manifest.json") or {}
    mscore_csv = mscore_root / "results.csv"
    if mscore_csv.is_file():
        with mscore_csv.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                task_id = row.get("task_id")
                if task_id:
                    mscore_ids.add(task_id)
                    mscore_runs[task_id] = mscore_runs.get(task_id, 0) + 1
                    condition = row.get("condition", "")
                    attempt_dir = (mscore_root / str(row.get("attempt_dir") or "")).resolve()
                    transcript_rel = Path(str(row.get("transcript") or ""))
                    transcript_path = (attempt_dir / transcript_rel).resolve()
                    transcript_available = (
                        mscore_root in attempt_dir.parents
                        and attempt_dir in transcript_path.parents
                        and transcript_path.name == "transcript.json"
                        and transcript_path.is_file()
                    )
                    mscore_attempts.setdefault(task_id, []).append({
                        "run_id": condition,
                        "condition": condition,
                        "status": row.get("attempt_status"),
                        "outcome": row.get("outcome"),
                        "score": (float(row["score"]) if row.get("scored", "").casefold() == "true"
                                  and row.get("score") not in (None, "") else None),
                        "scored": row.get("scored", "").casefold() == "true",
                        "correct": row.get("correct", "").casefold() == "true",
                        "evaluation_status": row.get("evaluation_status"),
                        "prediction": row.get("prediction"),
                        "total_tokens": int(row["total_tokens"]) if row.get("total_tokens") not in (None, "") else None,
                        "elapsed_seconds": float(row["elapsed_seconds"]) if row.get("elapsed_seconds") not in (None, "") else None,
                        "iterations": int(row["iterations"]) if row.get("iterations") not in (None, "") else None,
                        "deadlock_count": int(row["deadlock_count"]) if row.get("deadlock_count") not in (None, "") else None,
                        "model": mscore_manifest.get("model"),
                        "evaluator_mode": row.get("evaluator_mode"),
                        "adapted": row.get("adapted", "").casefold() == "true",
                        "failure_reason": row.get("failure_reason") or row.get("error"),
                        "counterfactual_fraction": float(row["counterfactual_fraction"])
                            if row.get("counterfactual_fraction") not in (None, "") else None,
                        "counterfactual_simulations": int(row["counterfactual_simulations"] or 0),
                        "transcript_available": transcript_available,
                    })
    direct_root = _mscore_direct_results_root()
    direct_csv = direct_root / "results.csv"
    if direct_csv.is_file():
        direct_manifest = _load_json(direct_root / "results_manifest.json") or {}
        with direct_csv.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                task_id = row.get("task_id")
                if not task_id:
                    continue
                mscore_ids.add(task_id)
                mscore_runs[task_id] = mscore_runs.get(task_id, 0) + 1
                try:
                    score = float(row["score"]) if row.get("score") not in (None, "") else None
                except ValueError:
                    score = None
                try:
                    token_count = sum(int(row.get(key) or 0) for key in ("prompt_tokens", "completion_tokens"))
                except ValueError:
                    token_count = None
                try:
                    elapsed = float(row["wall_seconds"]) if row.get("wall_seconds") else None
                except ValueError:
                    elapsed = None
                mscore_attempts.setdefault(task_id, []).append({
                    "run_id": row.get("condition", "direct_model_no_blackboard"),
                    "condition": row.get("condition", "direct_model_no_blackboard"),
                    "status": row.get("status"), "outcome": row.get("status"),
                    "score": score,
                    "scored": row.get("scored", "").casefold() == "true",
                    "correct": row.get("correct", "").casefold() == "true",
                    "evaluation_status": "scored" if row.get("scored", "").casefold() == "true" else "not_scored",
                    "prediction": row.get("prediction"), "total_tokens": token_count,
                    "elapsed_seconds": elapsed, "model": row.get("model") or direct_manifest.get("model"),
                    "evaluator_mode": "project-adapted-exact", "adapted": True,
                    "failure_reason": row.get("error") or None,
                    "counterfactual_fraction": 0.0, "counterfactual_simulations": 0,
                    "transcript_available": False,
                })

    krama_root, krama_rows = _kramabench_rows()
    krama_tasks_by_id: dict[str, list[dict]] = {}
    for attempt in krama_rows:
        task_id = str(attempt.get("task_id") or "")
        if task_id:
            krama_tasks_by_id.setdefault(task_id, []).append(attempt)
    condition_order = {name: index for index, name in enumerate(("solo", "cf-00", "cf-33", "cf-50", "cf-66"))}
    krama_tasks = [{
        "task_id": task_id,
        "split": "combined results",
        "evaluator_mode": next((row.get("evaluator_mode") for row in attempts if row.get("evaluator_mode")), None),
        "adapted": any(row.get("adapted") for row in attempts),
        "result_count": len(attempts),
        "attempts": [{key: value for key, value in row.items() if key not in {"result_directory", "transcript_path", "record_path"}}
                     for row in sorted(attempts, key=lambda item: condition_order.get(str(item.get("condition")), 99))],
        "runnable_in_ui": False,
    } for task_id, attempts in sorted(krama_tasks_by_id.items())]

    math_root, math_rows = _math500_rows()
    math_tasks_by_id: dict[str, list[dict]] = {}
    for index, source_row in enumerate(math_rows):
        task_id = str(source_row.get("task_id") or "")
        if not task_id:
            continue
        trace_rel = _math500_trace_reference(source_row)
        if trace_rel is None and source_row.get("trace") and not Path(str(source_row["trace"])).is_absolute():
            trace_rel = str(source_row["trace"])
        transcript_available = _safe_artifact(math_root, trace_rel) is not None
        correct = source_row.get("correct") if isinstance(source_row.get("correct"), bool) else None
        usage = source_row.get("usage") if isinstance(source_row.get("usage"), dict) else {}
        condition = str(source_row.get("condition") or "unknown")
        math_tasks_by_id.setdefault(task_id, []).append({
            "run_id": f"{condition}:{index}", "condition": condition,
            "status": source_row.get("outcome"), "outcome": source_row.get("outcome"),
            "score": (1.0 if correct else 0.0) if correct is not None else None,
            "score_label": "Exact match",
            "scored": correct is not None, "correct": correct,
            "evaluation_status": "scored" if correct is not None else "not_scored",
            "prediction": source_row.get("prediction"),
            "total_tokens": _total_usage_tokens(usage),
            "elapsed_seconds": source_row.get("wall_seconds"),
            "model": source_row.get("model"), "evaluator_mode": "project exact-answer check (adapted)", "adapted": True,
            "counterfactual_fraction": source_row.get("requested_fraction"),
            "counterfactual_simulations": source_row.get("replays", 0),
            "transcript_available": transcript_available,
        })
    math_tasks = [{"task_id": task_id, "result_count": len(attempts), "attempts": attempts,
                   "runnable_in_ui": False}
                  for task_id, attempts in sorted(math_tasks_by_id.items())]

    med_root, med_rows = _medagent_rows()
    med_tasks_by_id: dict[str, list[dict]] = {}
    for index, source_row in enumerate(med_rows):
        task_id = str(source_row.get("task_id") or "")
        if not task_id:
            continue
        trace_rel = source_row.get("trace")
        trace_path = _safe_artifact(med_root, trace_rel)
        transcript_available = bool(trace_path and trace_path.name == "transcript.json")
        correct = source_row.get("correct") if isinstance(source_row.get("correct"), bool) else None
        usage = source_row.get("usage") if isinstance(source_row.get("usage"), dict) else {}
        condition = str(source_row.get("condition") or "unknown")
        run_label = str(source_row.get("run") or "unknown")
        try:
            requested_fraction = float(source_row["requested_fraction"])
        except (KeyError, TypeError, ValueError):
            requested_fraction = None
        med_tasks_by_id.setdefault(task_id, []).append({
            "run_id": f"{run_label}:{condition}:{index}", "condition": condition, "run": run_label,
            "status": source_row.get("outcome"), "outcome": source_row.get("outcome"),
            "score": (1.0 if correct else 0.0) if correct is not None else None,
            "scored": correct is not None, "correct": correct,
            "evaluation_status": "scored" if correct is not None else "not_scored",
            "prediction": source_row.get("prediction"),
            "total_tokens": _total_usage_tokens(usage),
            "elapsed_seconds": source_row.get("wall_seconds") or usage.get("wall_seconds"),
            "iterations": usage.get("calls"), "model": None,
            "evaluator_mode": "project normalized exact-answer check (adapted)", "adapted": True,
            "counterfactual_fraction": requested_fraction,
            # This artifact's coverage report records zero counterfactual attempts.
            "counterfactual_simulations": 0,
            "transcript_available": transcript_available,
        })
    med_tasks = [{"task_id": task_id, "result_count": len(attempts), "attempts": attempts,
                  "runnable_in_ui": False}
                 for task_id, attempts in sorted(med_tasks_by_id.items())]

    return {
        "benchmarks": [
            {"id": "kramabench", "name": "KramaBench", "task_source": "combined saved batch · latest 25 Nov 2026 · five conditions",
             "task_count": len(krama_tasks), "result_count": len(krama_rows),
             "tasks": krama_tasks, "run_support": "saved combined results; use the separate runner for new live runs"},
            {"id": "mscore", "name": "MSCoRe", "task_source": "imported paired cohort · adapted project evaluator",
             "task_count": len(mscore_ids), "result_count": sum(mscore_runs.values()),
             "tasks": [{"task_id": task_id, "result_count": mscore_runs.get(task_id, 0),
                        "attempts": mscore_attempts.get(task_id, []),
                        "runnable_in_ui": False} for task_id in sorted(mscore_ids)],
             "run_support": "saved results are viewable; UI execution is not wired"},
            {"id": "math500", "name": "MATH-500", "task_source": "30 selected tasks × five conditions · Qwen3 4B GPU · self-check off",
             "task_count": len(math_tasks), "result_count": sum(len(items) for items in math_tasks_by_id.values()),
             "tasks": math_tasks,
             "run_support": "saved replays only; this complete cohort covers 30 selected questions, not the full 500-question set"},
            {"id": "medagentbench", "name": "MedAgentBench", "task_source": "562 saved project records from pilot and remaining batches · adapted project answer check",
             "task_count": len(med_tasks), "result_count": sum(len(items) for items in med_tasks_by_id.values()),
             "tasks": med_tasks,
             "run_support": "saved replays only; 562 records are available, with some planned remaining-batch records absent"},
        ]
    }


@app.get("/api/benchmark-results/math500/tasks/{task_id:path}")
async def math500_result_detail(task_id: str, run_id: str):
    root, rows = _math500_rows()
    index_text = run_id.rsplit(":", 1)[-1]
    try:
        index = int(index_text)
        source_row = rows[index]
    except (ValueError, IndexError):
        raise HTTPException(status_code=404, detail="No MATH-500 result exists for this run") from None
    if source_row.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="No MATH-500 result exists for this task and run")
    trace_rel = _math500_trace_reference(source_row)
    if trace_rel is None and source_row.get("trace") and not Path(str(source_row["trace"])).is_absolute():
        trace_rel = str(source_row["trace"])
    trace_path = _safe_artifact(root, trace_rel)
    transcript = _load_json(trace_path) if trace_path else None
    usage = source_row.get("usage") if isinstance(source_row.get("usage"), dict) else {}
    correct = source_row.get("correct") if isinstance(source_row.get("correct"), bool) else None
    safe_result = {
        "run_id": run_id, "condition": source_row.get("condition"), "outcome": source_row.get("outcome"),
        "score_label": "Exact match",
        "score": (1.0 if correct else 0.0) if correct is not None else None,
        "correct": correct, "scored": correct is not None,
        "evaluation_status": "scored" if correct is not None else "not_scored",
        "prediction": source_row.get("prediction"), "total_tokens": _total_usage_tokens(usage),
        "model": source_row.get("model"),
        "elapsed_seconds": source_row.get("wall_seconds"), "adapted": True,
        "evaluator_mode": "project exact-answer check (adapted)",
        "counterfactual_fraction": source_row.get("requested_fraction"),
        "counterfactual_simulations": source_row.get("replays", 0),
    }
    return _safe_transcript_detail(transcript, safe_result, transcript_available=transcript is not None)


@app.get("/api/benchmark-results/medagentbench/tasks/{task_id}")
async def medagentbench_result_detail(task_id: str, run_id: str):
    root, rows = _medagent_rows()
    selected: dict | None = None
    selected_index: int | None = None
    try:
        index = int(run_id.rsplit(":", 1)[-1])
        candidate = rows[index]
        if candidate.get("task_id") == task_id:
            selected, selected_index = candidate, index
    except (ValueError, IndexError):
        selected = None
    if selected is None:
        raise HTTPException(status_code=404, detail="No MedAgentBench result exists for this task and run")
    transcript_path = _safe_artifact(root, selected.get("trace"))
    transcript = _load_json(transcript_path) if transcript_path and transcript_path.name == "transcript.json" else None
    if transcript is None and selected.get("replay_snapshot"):
        snapshot = _load_json(_safe_artifact(root, selected.get("replay_snapshot")))
        if snapshot:
            transcript = {"entries": snapshot.get("entries", []), "counterfactual_attempts": [],
                          "counterfactual_skips": [], "outcome": selected.get("outcome")}
    usage = selected.get("usage") if isinstance(selected.get("usage"), dict) else {}
    correct = selected.get("correct") if isinstance(selected.get("correct"), bool) else None
    condition = str(selected.get("condition") or "unknown")
    safe_result = {
        "run_id": run_id, "condition": condition, "run": selected.get("run"),
        "outcome": selected.get("outcome"), "score": (1.0 if correct else 0.0) if correct is not None else None,
        "correct": correct, "scored": correct is not None,
        "evaluation_status": "scored" if correct is not None else "not_scored",
        "prediction": selected.get("prediction"),
        "total_tokens": _total_usage_tokens(usage),
        "elapsed_seconds": selected.get("wall_seconds") or usage.get("wall_seconds"),
        "iterations": usage.get("calls"), "adapted": True,
        "evaluator_mode": "project normalized exact-answer check (adapted)",
        "counterfactual_fraction": selected.get("requested_fraction"), "counterfactual_simulations": 0,
    }
    return _safe_transcript_detail(transcript, safe_result, transcript_available=transcript is not None)


@app.get("/api/benchmark-dashboard/krama/{task_id}")
async def krama_saved_task_detail(task_id: str, run_id: str | None = None):
    """Load one selected attempt from the canonical imported KramaBench batch."""
    root, attempts = _kramabench_rows()
    selected = next((item for item in attempts
                     if item.get("task_id") == task_id and (run_id is None or item.get("run_id") == run_id)), None)
    if selected is None:
        raise HTTPException(status_code=404, detail="No imported KramaBench attempt exists for this task and run")
    record_path = _safe_artifact(root, selected.get("record_path"))
    record = _load_json(record_path) if record_path else None
    transcript_path = _safe_artifact(root, selected.get("transcript_path"))
    transcript = _load_json(transcript_path) if transcript_path else None
    if transcript is None and record:
        transcript = {"board": {"entries": record.get("board_evidence_entries", [])}}
    safe_result = {key: selected[key] for key in (
        "run_id", "condition", "result_source", "status", "outcome", "score", "score_label", "correct", "scored",
        "evaluation_status", "prediction", "metric_summary", "total_tokens", "elapsed_seconds",
        "iterations", "deadlock_count", "model", "evaluator_mode", "adapted", "failure_reason",
        "counterfactual_fraction", "counterfactual_simulations",
    ) if key in selected}
    return _safe_transcript_detail(transcript, safe_result, transcript_available=transcript_path is not None)


@app.get("/api/benchmark-results/mscore/tasks/{task_id}")
async def mscore_result_detail(task_id: str, condition: str):
    if condition == "direct_model_no_blackboard":
        root = _mscore_direct_results_root()
        csv_path = root / "results.csv"
        if not csv_path.is_file():
            raise HTTPException(status_code=404, detail="MSCoRe direct-baseline results are not available")
        with csv_path.open(encoding="utf-8", newline="") as stream:
            row = next((item for item in csv.DictReader(stream)
                        if item.get("task_id") == task_id and item.get("condition") == condition), None)
        if row is None:
            raise HTTPException(status_code=404, detail="No direct-baseline result exists for this task")
        try:
            score = float(row["score"]) if row.get("score") not in (None, "") else None
            total_tokens = sum(int(row.get(key) or 0) for key in ("prompt_tokens", "completion_tokens"))
            elapsed_seconds = float(row["wall_seconds"]) if row.get("wall_seconds") else None
        except ValueError:
            score, total_tokens, elapsed_seconds = None, None, None
        return _safe_transcript_detail(None, {
            "run_id": condition, "condition": condition, "outcome": row.get("status"),
            "score": score, "correct": row.get("correct", "").casefold() == "true",
            "scored": row.get("scored", "").casefold() == "true",
            "evaluation_status": "scored" if row.get("scored", "").casefold() == "true" else "not_scored",
            "prediction": row.get("prediction"), "total_tokens": total_tokens,
            "elapsed_seconds": elapsed_seconds, "model": row.get("model"),
            "evaluator_mode": "project-adapted-exact", "adapted": True,
            "counterfactual_fraction": 0.0, "counterfactual_simulations": 0,
        }, transcript_available=False)
    root = _mscore_results_root()
    csv_path = root / "results.csv"
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail="MSCoRe results are not available")
    with csv_path.open(encoding="utf-8", newline="") as stream:
        row = next((item for item in csv.DictReader(stream)
                    if item.get("task_id") == task_id and item.get("condition") == condition), None)
    if row is None:
        raise HTTPException(status_code=404, detail="No result exists for this task and condition")
    parsed = _parse_mscore_result_row(row)
    attempt_root = (root / str(parsed["attempt_dir"])).resolve()
    if root not in attempt_root.parents:
        raise HTTPException(status_code=400, detail="Invalid result artifact path")
    transcript_rel = Path(str(parsed.get("transcript") or ""))
    transcript_path = (attempt_root / transcript_rel).resolve()
    if attempt_root not in transcript_path.parents or transcript_path.name != "transcript.json":
        raise HTTPException(status_code=400, detail="Invalid transcript path")
    transcript = _load_json(transcript_path)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript is missing or unreadable")
    # Return only post-run agent/session evidence. Gold/reference fields are omitted.
    return {
        "result": parsed,
        "transcript": {
            "created_at": transcript.get("created_at"),
            "finished_at": transcript.get("finished_at"),
            "task": transcript.get("task"),
            "turns": transcript.get("turns", []),
            "outcome": transcript.get("outcome"),
            "workflow_counters": transcript.get("workflow_counters", {}),
            "counterfactual_attempts": transcript.get("counterfactual_attempts", []),
            "counterfactual_skips": transcript.get("counterfactual_skips", []),
            "counterfactual_capable_agents": transcript.get("counterfactual_capable_agents", []),
            "scheduler_status": transcript.get("scheduler_status"),
            "board": {
                "entries": (transcript.get("board") or {}).get("entries", []),
                "counterfactual_events": (transcript.get("board") or {}).get("counterfactual_events", []),
                "deadlocks": (transcript.get("board") or {}).get("deadlocks", []),
                "intelligibility": (transcript.get("board") or {}).get("intelligibility"),
            },
        },
    }


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
    if not _genuine_run_approved():
        raise HTTPException(status_code=403, detail="Real model execution is disabled. Set UI_APPROVE_GENUINE_RUN=1 after approving a genuine run.")
    if BOARD_MODE != "LIVE_TAP":
        raise HTTPException(status_code=409, detail="Start the server with BOARD_MODE=LIVE_TAP")
    if request.scenario not in {"default", "disagreement"}:
        raise HTTPException(status_code=422, detail="scenario must be default or disagreement")
    if _conversation_task is not None and not _conversation_task.done():
        raise HTTPException(status_code=409, detail="A conversation is already running")
    if _active_task_session_id is not None and _task_sessions.get(_active_task_session_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="A benchmark task session is already running")

    session_id = uuid4().hex
    task_id = f"playground-{session_id[:8]}"
    board_tap.reset_token_usage(session_id)
    bus.reset_history()
    await bus.publish({"type": "session_started", "source": "LIVE_TAP", "mode": "LIVE",
                       "session_id": session_id, "task_id": task_id,
                       "model": OllamaClient().model,
                       "agents": [agent_id for agent_id, _ in DEFAULT_AGENTS],
                       "entry_count": 0, "last_entry_id": None})

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
                "task_id": task_id,
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
                "session_id": session_id,
                "task_id": task_id,
                "summary": {
                    "outcome": report.get("outcome", "unknown"),
                    "scheduler_status": report.get("scheduler_status"),
                    "failures": report.get("failures", []),
                    "evaluator_score_available": False,
                    **bus.snapshot(),
                },
                "transcript_path": str(transcript_path),
            })
        except Exception as exc:
            logger.exception("Live agent conversation failed")
            await bus.publish({
                "type": "session_error",
                "source": "LIVE_TAP",
                "session_id": session_id,
                "task_id": task_id,
                "error": str(exc),
            })
        finally:
            detach_live_board()
            await bus.publish({"type": "stream_complete", "source": "LIVE_TAP",
                               "session_id": session_id, "task_id": task_id})

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
    if not _genuine_run_approved():
        raise HTTPException(status_code=403, detail="Real model execution is disabled. Set UI_APPROVE_GENUINE_RUN=1 after approving a genuine run.")
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
