"""KramaBench System adapter over the existing PXP conversation workflow.

The adapter accepts only the official SUT arguments. In particular, it never
accepts a complete workload row, so evaluator-only answers and reference
subtasks cannot enter the agent prompt through this boundary.
"""

from __future__ import annotations

import ast
import fnmatch
import glob
import hashlib
import inspect
import json
import logging
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from threading import Lock

from blackboard.models import BoardEventType
from pydantic import BaseModel, Field, field_validator

from agents.conversation import run_conversation
from agents.llm_client import (
    MODEL, OPTIONS, OllamaClient, ROOT, inference_usage, parse_response,
    sanitized_base_url,
)
from bench.evidence import (
    build_evidence_message,
    can_terminate,
    compute_rate,
    termination_candidate,
    termination_reason,
)
from bench.metrics.token_counter import UsageLedger, UsageRecord, count_tokens_for_entry
from bench.kramabench_data import (
    DATA_TOOL_SYSTEM_PROMPT,
    DEFAULT_TIMEOUT_SECONDS,
    DataAccessError,
    DataToolWorkflowError,
    DatasetToolbox,
    gather_dataset_evidence,
)

DEFAULT_TASK_WALL_CLOCK_CAP_SECONDS = 900
logger = logging.getLogger(__name__)


KRAMABENCH_REVISION = "b2e0d77540263f8b6119f977fe79a8c4386b5a02"
SAFE_QUERY_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_UI_USAGE_EVENT_LOCK = Lock()

PIPELINE_SYSTEM_PROMPT = """Generate the Python data pipeline for the supplied task using only the
permitted files and the conversation's proposed answer. Return JSON matching
the supplied schema, with one nonempty `pipeline_code` string. Return plain
Python source in that string, without Markdown fences. Do not claim that the
code was executed. Do not include or infer any expected answer or reference
solution. If the available inputs do not support a pipeline, fail to produce
the required schema rather than inventing data or a reference solution.
"""


class KramaBenchAdapterError(RuntimeError):
    """Base error for adapter failures that must not become benchmark scores."""


class DatasetInputError(KramaBenchAdapterError):
    """The dataset directory or permitted task files are invalid or unreadable."""


class AgentWorkflowError(KramaBenchAdapterError):
    """The Blackboard workflow did not produce an agreed answer."""


class PipelineGenerationError(KramaBenchAdapterError):
    """The existing client did not produce validated, nonempty pipeline code."""

    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


class PipelineCodeResponse(BaseModel):
    pipeline_code: str = Field(min_length=1)

    @field_validator("pipeline_code")
    @classmethod
    def _nonempty_code(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("pipeline_code cannot be empty")
        return value


def resolve_subset_files(dataset_directory: str | os.PathLike,
                         subset_files: list[str]) -> list[Path]:
    """Resolve official relative file patterns under the processed data root."""
    root = Path(dataset_directory).resolve(strict=True)
    if not root.is_dir():
        raise DatasetInputError("dataset_directory is not a directory")
    if not isinstance(subset_files, list) or not subset_files:
        raise DatasetInputError("subset_files must name at least one permitted data file")

    resolved: dict[str, Path] = {}
    for pattern in subset_files:
        if not isinstance(pattern, str) or not pattern.strip():
            raise DatasetInputError("subset_files contains an empty or non-string path")
        pattern = pattern.strip().replace("\\", "/")
        if pattern in {".", "./", ""}:
            raise DatasetInputError("a dataset-wide subset is not an explicit file allowlist")
        relative = Path(pattern)
        if relative.is_absolute() or ".." in relative.parts:
            raise DatasetInputError(f"unsafe subset path: {pattern}")

        matches = sorted(Path(p) for p in glob.glob(str(root / pattern), recursive=True))
        if not matches:
            # KramaBench workload patterns sometimes name a source directory or
            # filename without its parent folders, and a few checked-in folder
            # names differ only by case. Match the explicit pattern against a
            # suffix of dataset-relative path components; never broaden it to
            # unrelated siblings elsewhere in the dataset.
            pattern_parts = relative.parts
            if "**" not in pattern_parts:
                for candidate in root.rglob("*"):
                    candidate_parts = candidate.relative_to(root).parts
                    if len(candidate_parts) < len(pattern_parts):
                        continue
                    suffix_parts = candidate_parts[-len(pattern_parts):]
                    if all(
                        fnmatch.fnmatchcase(actual.casefold(), expected.casefold())
                        for actual, expected in zip(suffix_parts, pattern_parts)
                    ):
                        matches.append(candidate)
        expanded: list[Path] = []
        for match in matches:
            if match.is_dir():
                expanded.extend(path for path in sorted(match.rglob("*")) if path.is_file())
            elif match.is_file():
                expanded.append(match)
        if not expanded:
            raise DatasetInputError(f"no dataset files match permitted path: {pattern}")

        for candidate in expanded:
            path = candidate.resolve(strict=True)
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise DatasetInputError(f"subset path resolves outside dataset: {pattern}") from exc
            resolved[str(path)] = path

    if not resolved:
        raise DatasetInputError("subset_files resolved to no readable files")
    return sorted(resolved.values(), key=lambda path: path.relative_to(root).as_posix())


def inspect_permitted_files(dataset_directory: str | os.PathLike,
                            subset_files: list[str]) -> list[dict[str, Any]]:
    """Fully parse allowlisted inputs and return metadata, never file contents."""
    root = Path(dataset_directory).resolve(strict=True)
    paths = resolve_subset_files(root, subset_files)
    try:
        with DatasetToolbox(root, paths) as toolbox:
            return [dict(item) for item in toolbox.catalog()["files"]]
    except DataAccessError as exc:
        raise DatasetInputError(str(exc)) from exc
    except OSError as exc:
        raise DatasetInputError("cannot read one or more allowlisted dataset files") from exc


def build_agent_task(query: str, data_evidence: dict[str, Any],
                     task_data_sources: list[str] | None = None,
                     verified_rate: dict[str, Any] | None = None) -> str:
    """Give PXP the official task scope and structured, allowlisted evidence."""
    if task_data_sources is None:
        catalog = data_evidence.get("available_files", {})
        task_data_sources = [
            item["path"] for item in catalog.get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ] if isinstance(catalog, dict) else []
    scope = {
        "data_sources": list(task_data_sources),
        "source": "KramaBench subset_files argument, derived from the task's data_sources",
    }
    deterministic_rate_context = ""
    if verified_rate is not None:
        deterministic_rate_context = (
            "\nVerified deterministic calculation for the final answer:\n"
            + json.dumps(verified_rate, ensure_ascii=False, separators=(",", ":"))
            + "\nThis value is computed from complete, unfiltered category counts in the task-selected "
            "population. Use the provided numeric `answer` exactly; do not redo the arithmetic, convert "
            "percentage units to a fraction, or claim insufficient information when this calculation applies.\n"
        )
    # The free-form summary is generated by the data model and is not evidence.
    # Keep it in the data workflow's internal return/trace for diagnostics, but
    # do not let it compete with validated aggregates in the answer prompt.
    prompt_evidence = {
        key: value for key, value in data_evidence.items()
        if key not in {"agent_summary", "agent_summary_status"}
    }
    if verified_rate is not None:
        # Once complete unfiltered aggregates resolve a rate over the official
        # task population, failed filters and their recovery errors are not
        # answer evidence. The full workflow remains auditable in its task
        # record and Blackboard FACT entries.
        observations = prompt_evidence.get("observations")
        if isinstance(observations, list):
            prompt_evidence["observations"] = [
                observation for observation in observations
                if isinstance(observation, dict)
                and observation.get("action") not in {"workflow_validation"}
                and not (
                    observation.get("action") == "filter"
                    and isinstance(observation.get("result"), dict)
                    and observation["result"].get("total_matches") == 0
                )
            ]
    return (
        "KramaBench task query:\n"
        f"{query.strip()}\n\n"
        "Official task-scope context:\n"
        + json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
        + "\nThe listed files are the benchmark-selected source data for this question, not examples to search for a literal copy of every phrase in the query. Use all source rows for a rate over that source population unless the query requests a narrower subset that can be supported by the available metadata and columns. Geographic fields may describe a finer level than the query (for example, counties within a state); do not require a broader-level name to appear as a value in a finer-level field, and do not infer that the population is absent from such a mismatch. If the task scope or a requested narrower subset genuinely cannot be established from the query, source metadata, and data, preserve that uncertainty.\n"
        "If a successful `read_text` or `search_text` observation is present, extract requested values and measurements from its returned text even if there is no tabular column with the requested concept's name. Do not refuse because a literal column name is absent. If evidence is genuinely insufficient, name the specific value, measurement, date, or record that is missing and why it is needed.\n"
        "Verified category-rate evidence contains deterministic shares calculated from complete, unfiltered one-column group_by/count(*) results. These are neutral percentages for each category; they do not designate which category answers the task. Use a category as the numerator only when the query and available metadata establish its meaning. Do not invent an outcome mapping.\n\n"
        "Use the validated structured aggregates and task-selected source scope as data evidence. Do not infer additional facts beyond them.\n\n"
        + deterministic_rate_context
        + "Evidence collected through the constrained dataset interface (no source file is truncated into this prompt):\n"
        + json.dumps(prompt_evidence, ensure_ascii=False, separators=(",", ":"))
    )


def generate_pipeline_code(client: Any, task: str, prediction: str,
                           explanation: str, max_retries: int = 1) -> dict[str, Any]:
    """Use the configured existing model client to generate schema-checked code."""
    if type(max_retries) is not int or max_retries < 0 or max_retries > 5:
        raise ValueError("max_retries must be an integer between 0 and 5")
    prompt = json.dumps({
        "task_and_permitted_inputs": task,
        "blackboard_consensus": {
            "prediction": prediction,
            "explanation": explanation,
        },
    }, ensure_ascii=False)
    attempts: list[dict[str, Any]] = []
    feedback = ""
    for _ in range(max_retries + 1):
        try:
            raw = client.generate(
                prompt,
                system=PIPELINE_SYSTEM_PROMPT + feedback,
                schema=PipelineCodeResponse.model_json_schema(),
            )
        except (RuntimeError, OSError) as exc:
            attempts.append({"raw": None, "error": str(exc)})
            raise PipelineGenerationError(f"pipeline generation transport failed: {exc}", attempts) from exc
        attempt: dict[str, Any] = {"raw": raw, "error": None}
        attempts.append(attempt)
        try:
            parsed = parse_response(raw, PipelineCodeResponse)
        except (ValueError, TypeError) as exc:
            attempt["error"] = str(exc)
            feedback = (
                f"\nThe previous output failed validation: {exc}. Return a nonempty "
                "pipeline_code string in the required JSON schema."
            )
            continue
        try:
            ast.parse(parsed.pipeline_code)
        except SyntaxError as exc:
            attempt["error"] = f"pipeline_code is not valid Python: {exc.msg}"
            feedback = (
                f"\nThe previous pipeline_code was not valid Python: {exc.msg}. "
                "Return syntactically valid Python in the required JSON schema."
            )
            continue
        return {
            "pipeline_code": parsed.pipeline_code,
            "attempts": attempts,
            "usage": inference_usage(attempts),
        }
    raise PipelineGenerationError(
        f"pipeline generation returned no valid code after {len(attempts)} attempt(s)", attempts
    )


def _prompt_version() -> str:
    prompts_dir = ROOT / "agents" / "prompts"
    names = (
        "pxp_template.txt", "explanation_check.txt",
        "aggressive_proposer.txt", "cautious_verifier.txt",
    )
    digest = hashlib.sha256(PIPELINE_SYSTEM_PROMPT.encode("utf-8"))
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update((prompts_dir / name).read_bytes())
    digest.update(DATA_TOOL_SYSTEM_PROMPT.encode("utf-8"))
    digest.update((ROOT / "bench" / "evidence.py").read_bytes())
    digest.update(inspect.getsource(build_agent_task).encode("utf-8"))
    return digest.hexdigest()


def _data_generation_metadata(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist data-call counters and response fingerprints without raw output text."""
    summaries = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            summaries.append({"response_type": type(attempt).__name__})
            continue
        response = attempt.get("response")
        summary = {
            key: attempt[key]
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration", "wall_seconds")
            if key in attempt and type(attempt[key]) in {int, float}
        }
        if isinstance(response, str):
            summary["response_sha256"] = hashlib.sha256(response.encode("utf-8", errors="replace")).hexdigest()
            summary["response_chars"] = len(response)
        summaries.append(summary)
    return summaries


def _source_manifest(dataset_root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    """Record every allowlisted source even if its parser later rejects it."""
    manifest = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest.append({
            "path": path.relative_to(dataset_root).as_posix(),
            "sha256": digest.hexdigest(),
            "size_bytes": path.stat().st_size,
            "format": path.suffix.lower().lstrip("."),
            "parse_status": "pending",
        })
    return manifest


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _provider_usage(data_usage: dict[str, Any], other_attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine raw data-tool request counts with wrapped PXP/pipeline attempts."""
    other = inference_usage(other_attempts)
    return {
        "calls": int(data_usage.get("calls", 0) or 0) + other["calls"],
        "prompt_tokens": int(data_usage.get("prompt_tokens", 0) or 0) + other["prompt_tokens"],
        "output_tokens": int(data_usage.get("output_tokens", 0) or 0) + other["output_tokens"],
        "wall_seconds": round(float(data_usage.get("wall_seconds", 0) or 0) + other["wall_seconds"], 3),
    }


def _publish_ui_board_events(board: Any) -> None:
    """Bridge actual child-process Blackboard entries to a run-scoped JSONL file."""
    event_path = os.environ.get("KRAMA_UI_BOARD_EVENT_FILE")
    if not event_path:
        return
    session_id = os.environ.get("KRAMA_UI_SESSION_ID", "")
    lock = Lock()
    event_count = 0
    branch_event_types = {
        "branch_created", "branch_entry_posted", "counterfactual_result", "live_adjustment_applied",
    }
    scheduler_event_types = {"turn_started", "turn_finished", "scheduler_status"}

    def on_event(board_event: Any) -> None:
        nonlocal event_count
        if board_event.event_type is not BoardEventType.ENTRY_POSTED:
            event_type = getattr(board_event, "event_type", "")
            raw_kind = getattr(event_type, "value", event_type)
            if not isinstance(raw_kind, str):
                raw_kind = getattr(event_type, "name", "")
            kind = raw_kind.lower()
            payload = getattr(board_event, "payload", {})
            if kind in scheduler_event_types:
                scheduler_session_id = getattr(board_event, "session_id", None)
                envelope = {
                    "type": kind, "source": "LIVE",
                    "session_id": session_id or scheduler_session_id,
                    "task_id": board.task_id,
                }
                if session_id and scheduler_session_id and session_id != scheduler_session_id:
                    envelope["scheduler_session_id"] = scheduler_session_id
                for field in ("agent_id", "turn", "reason", "priority_score", "outcome", "state", "agents"):
                    value = getattr(board_event, field, None)
                    if value is not None:
                        envelope[field] = value
                try:
                    target = Path(event_path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with lock, target.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
                        stream.flush()
                except OSError:
                    logger.exception("Could not write UI scheduler-event bridge file")
                return
            if kind not in branch_event_types or not isinstance(payload, dict) or not payload.get("branch_id"):
                return
            envelope = {
                **payload, "type": kind, "source": "LIVE", "session_id": session_id,
                "task_id": board.task_id,
            }
            if kind == "branch_entry_posted" and not isinstance(envelope.get("entry"), dict):
                return
            try:
                target = Path(event_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                with lock, target.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
                    stream.flush()
            except OSError:
                logger.exception("Could not write UI counterfactual-event bridge file")
            return
        entry = board_event.payload.get("entry")
        if not isinstance(entry, dict):
            return
        event_count += 1
        envelope = {
            "type": "board_entry", "source": "LIVE", "session_id": session_id,
            "task_id": board.task_id, "entry": entry, "entry_count": event_count,
            "last_entry_id": entry.get("entry_id"),
        }
        try:
            target = Path(event_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with lock, target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
                stream.flush()
        except OSError:
            logger.exception("Could not write UI board-event bridge file")

    board.subscribe(on_event)


def _append_ui_usage_event(record: dict[str, Any]) -> None:
    """Append a usage callback record to the UI subprocess bridge when enabled."""
    event_path = os.environ.get("KRAMA_UI_BOARD_EVENT_FILE")
    if not event_path:
        return
    envelope = {
        "type": "usage_record", "source": "LIVE", "session_id": record.get("session_id"),
        "task_id": record.get("task_id"), "record": record,
    }
    target = Path(event_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _UI_USAGE_EVENT_LOCK, target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
        stream.flush()


class _UsageCallbackClient:
    """Pass an optional, request-scoped usage callback to compatible clients."""

    def __init__(self, client: Any, callback: Callable[[dict[str, Any]], None], *,
                 session_id: str, task_id: str, agent_id: str, call_kind: str):
        self._client = client
        self._callback = callback
        self._context = {
            "session_id": session_id, "task_id": task_id, "agent_id": agent_id,
            "call_kind": call_kind, "branch_id": None,
        }

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def generate(self, *args, **kwargs):
        generate = self._client.generate
        try:
            parameters = inspect.signature(generate).parameters.values()
            supports_usage = any(
                parameter.name == "on_usage" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            supports_usage = False
        if supports_usage:
            def forward(record):
                value = asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)
                self._callback({**self._context, **value})
            kwargs["on_usage"] = forward
        return generate(*args, **kwargs)


def _merge_workflow_counters(current: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    """Merge data-workflow and conversation guard activations without losing either."""
    merged = dict(current)
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if key == "guard_activations":
            merged[key] = list(merged.get(key, [])) + list(value or [])
        elif key in {"guard_retries", "refusal_despite_evidence_retries"}:
            merged[key] = int(merged.get(key, 0) or 0) + int(value or 0)
        elif key == "max_guard_retries":
            merged[key] = max(int(merged.get(key, 0) or 0), int(value or 0))
        elif key == "data_loop_degraded":
            merged[key] = bool(merged.get(key, False) or value)
        elif key in merged and isinstance(value, (int, float)) and isinstance(merged[key], (int, float)):
            merged[key] += value
        else:
            merged[key] = value
    return merged


class KramaBenchSystem:
    """Minimal official System adapter using the repository's PXP Blackboard."""

    def __init__(self, verbose: bool = False, output_dir: str | os.PathLike | None = None,
                 *, client: Any | None = None,
                 conversation_runner: Callable[..., tuple[dict[str, Any], Path]] = run_conversation,
                 pipeline_generator: Callable[..., dict[str, Any]] = generate_pipeline_code,
                 max_turns: int = 6, max_retries: int = 1,
                 pipeline_evaluation_enabled: bool = False,
                 task_wall_clock_cap_seconds: int = DEFAULT_TASK_WALL_CLOCK_CAP_SECONDS,
                 counterfactual_fraction: float = 0.0,
                 counterfactual_seed: int = 42,
                 counterfactual_streak_threshold: int = 2,
                 max_counterfactual_simulations: int = 1):
        if type(task_wall_clock_cap_seconds) is not int or task_wall_clock_cap_seconds <= 0:
            raise ValueError("task_wall_clock_cap_seconds must be a positive integer")
        self.name = "KramaBenchPXPSystem"
        self.dataset_directory: Path | None = None
        self.verbose = bool(verbose)
        self.output_dir = Path(output_dir) if output_dir is not None else ROOT / "results" / "kramabench-system"
        self.client = client if client is not None else OllamaClient()
        self.conversation_runner = conversation_runner
        self.pipeline_generator = pipeline_generator
        self.max_turns = max_turns
        self.max_retries = max_retries
        self.pipeline_evaluation_enabled = bool(pipeline_evaluation_enabled)
        self.task_wall_clock_cap_seconds = task_wall_clock_cap_seconds
        if counterfactual_fraction not in (0, 0.33, 0.66, 1.0):
            raise ValueError("counterfactual_fraction must be one of 0, 0.33, 0.66, or 1.0")
        if type(counterfactual_seed) is not int:
            raise ValueError("counterfactual_seed must be an integer")
        self.counterfactual_fraction = float(counterfactual_fraction)
        self.counterfactual_seed = counterfactual_seed
        if type(counterfactual_streak_threshold) is not int or counterfactual_streak_threshold < 2:
            raise ValueError("counterfactual_streak_threshold must be at least 2")
        if type(max_counterfactual_simulations) is not int or max_counterfactual_simulations < 0:
            raise ValueError("max_counterfactual_simulations must be non-negative")
        self.counterfactual_streak_threshold = counterfactual_streak_threshold
        self.max_counterfactual_simulations = max_counterfactual_simulations

    def process_dataset(self, dataset_directory: str | os.PathLike) -> None:
        """Retain only a validated dataset root; task files are opened per query."""
        try:
            root = Path(dataset_directory).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DatasetInputError("dataset_directory does not exist or cannot be resolved") from exc
        if not root.is_dir():
            raise DatasetInputError("dataset_directory is not a directory")
        if not any(path.is_file() for path in root.rglob("*")):
            raise DatasetInputError("dataset_directory contains no files")
        self.dataset_directory = root

    def _write_record(self, record: dict[str, Any]) -> Path:
        path = self.output_dir / "krama_task_records" / f"{record['run_id']}.json"
        _atomic_json(path, record)
        return path

    def serve_query(self, query: str, query_id: str, subset_files: list[str]) -> dict[str, Any]:
        """Run a benchmark query through PXP and return the official SUT schema."""
        if self.dataset_directory is None:
            raise DatasetInputError("process_dataset must succeed before serve_query")
        if not isinstance(query, str) or not query.strip():
            raise DatasetInputError("query must be a nonempty string")
        if not isinstance(query_id, str) or not SAFE_QUERY_ID.fullmatch(query_id) or ".." in query_id:
            raise DatasetInputError("query_id must be a path-safe nonempty identifier")

        run_id = f"{query_id}-{uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        start = time.perf_counter()
        wall_deadline = time.monotonic() + self.task_wall_clock_cap_seconds
        if hasattr(self.client, "set_task_deadline"):
            self.client.set_task_deadline(wall_deadline)
        contexts: list[dict[str, Any]] = []
        conversation_report: dict[str, Any] | None = None
        conversation_counters_merged = False
        pipeline_attempts: list[dict[str, Any]] = []
        generation_attempts: list[dict[str, Any]] = []
        record: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": query_id,
            "query_id": query_id,
            "benchmark_revision": KRAMABENCH_REVISION,
            "provider": "ollama",
            "model": getattr(self.client, "model", MODEL),
            "base_url": sanitized_base_url(getattr(self.client, "base_url", "local")),
            "model_options": dict(getattr(self.client, "options", OPTIONS)),
            "model_seed": int(getattr(self.client, "options", OPTIONS).get("seed", OPTIONS["seed"])),
            "prompt_version": _prompt_version(),
            "pipeline_evaluation_enabled": self.pipeline_evaluation_enabled,
            "counterfactual_fraction": self.counterfactual_fraction,
            "counterfactual_seed": self.counterfactual_seed,
            "counterfactual_streak_threshold": self.counterfactual_streak_threshold,
            "max_counterfactual_simulations": self.max_counterfactual_simulations,
            "max_turns": self.max_turns,
            "max_retries": self.max_retries,
            "data_operation_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "task_wall_clock_cap_seconds": self.task_wall_clock_cap_seconds,
            "started_at": started_at.isoformat(),
            "status": "running",
            "task_data_sources": [],
            "permitted_files": [],
            "data_access_trace": [],
            "data_access_usage": {"calls": 0, "prompt_tokens": 0, "output_tokens": 0},
            "data_access_diagnostics": {},
            "data_access_request_diagnostics": [],
            "workflow_counters": {
                "guard_activations": [],
                "guard_retries": 0,
                "max_guard_retries": 2,
                "refusal_despite_evidence_retries": 0,
                "aggregate_columns_validation_feedback": 0,
                "multi_file_coverage_rejections": 0,
                "repeated_invalid_request_stops": 0,
                "blocked_done_rejections": 0,
                "duplicate_filter_rejections": 0,
                "defaulted_file_selectors": 0,
                "data_loop_degraded": False,
                "data_loop_degradation_reason": None,
                "reviewer_rejections": 0,
                "counterfactual_triggers": 0,
                "counterfactual_simulations": 0,
                "counterfactual_changed_outcome": 0,
            },
            "provider_token_usage": {"calls": 0, "prompt_tokens": 0, "output_tokens": 0},
            "errors": [],
            "data_loop_degraded": False,
            "data_loop_degradation_reason": None,
            "data_loop_unverified_summary": None,
        }

        usage_ledger = UsageLedger()
        usage_records: list[dict[str, Any]] = []

        def on_usage(value: UsageRecord | dict[str, Any]) -> None:
            usage_record = asdict(value) if isinstance(value, UsageRecord) else dict(value)
            usage_record.setdefault("session_id", os.environ.get("KRAMA_UI_SESSION_ID") or run_id)
            usage_record.setdefault("task_id", query_id)
            usage_ledger.ingest(usage_record)
            usage_records.append(usage_record)
            record["usage_records"] = list(usage_records)
            record["usage_ledger"] = usage_ledger.export_report()
            ledger_path = self.output_dir / "usage_ledgers" / f"{run_id}.json"
            usage_ledger.write_json(ledger_path)
            self._write_record(record)
            _append_ui_usage_event(usage_record)

        session_usage_id = os.environ.get("KRAMA_UI_SESSION_ID") or run_id
        data_usage_client = _UsageCallbackClient(
            self.client, on_usage, session_id=session_usage_id, task_id=query_id,
            agent_id="data_agent", call_kind="other",
        )
        pipeline_usage_client = _UsageCallbackClient(
            self.client, on_usage, session_id=session_usage_id, task_id=query_id,
            agent_id="pipeline_generator", call_kind="other",
        )

        toolbox: DatasetToolbox | None = None
        data_attempts: list[dict[str, Any]] = []
        try:
            paths = resolve_subset_files(self.dataset_directory, subset_files)
            record["task_data_sources"] = list(subset_files)
            record["permitted_files"] = _source_manifest(self.dataset_directory, paths)
            toolbox = DatasetToolbox(self.dataset_directory, paths, task_deadline=wall_deadline)
            record["permitted_files"] = [dict(item) for item in toolbox.catalog()["files"]]
            record["precomputed_file_profiles"] = toolbox.precomputed_profiles()
            prepared = gather_dataset_evidence(
                data_usage_client, query, toolbox,
                precomputed_profiles=record["precomputed_file_profiles"],
            )
            data_attempts = prepared["attempts"]
            record["data_access_trace"] = prepared["trace"]
            record["data_access_usage"] = prepared["usage"]
            record["data_access_diagnostics"] = prepared["workflow_diagnostics"]
            record["data_operation_processing_seconds"] = list(toolbox.operation_seconds)
            record["review_evidence"] = [
                {"kind": "precomputed_file_profile", **profile}
                for profile in record["precomputed_file_profiles"]
            ] + prepared["evidence"].get("observations", [])
            record["data_access_request_diagnostics"] = prepared["request_diagnostics"]
            record["data_loop_degraded"] = bool(prepared.get("data_loop_degraded", False))
            record["data_loop_degradation_reason"] = prepared.get("data_loop_degradation_reason")
            if record["data_loop_degraded"]:
                record["data_loop_unverified_summary"] = None
                for item in reversed(prepared["request_diagnostics"]):
                    excerpt = item.get("rejected_request")
                    if isinstance(excerpt, str):
                        try:
                            excerpt = json.loads(excerpt)
                        except json.JSONDecodeError:
                            continue
                    if isinstance(excerpt, dict) and isinstance(excerpt.get("summary"), str):
                        record["data_loop_unverified_summary"] = excerpt["summary"]
                        break
                prepared["workflow_diagnostics"]["workflow_counters"]["data_loop_degraded"] = True
                prepared["workflow_diagnostics"]["workflow_counters"]["data_loop_degradation_reason"] = prepared.get("data_loop_degradation_reason")
            record["workflow_counters"] = _merge_workflow_counters(
                record["workflow_counters"],
                prepared["workflow_diagnostics"].get("workflow_counters"),
            )
            population_files = [
                item["path"] for item in prepared["evidence"].get("available_files", {}).get("files", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            ]
            verified_rate = compute_rate(
                prepared["trace"], query, population_files=population_files
            )
            record["verified_rate_calculation"] = verified_rate
            termination_trace = [
                {**item, "task_query": query, "population_files": population_files}
                for item in prepared["trace"]
            ]
            evidence_messages = [
                build_evidence_message(item)
                for item in prepared["trace"]
                if item.get("status") == "success" and "category_count_evidence" in item
            ]
            profile_messages = [
                "Precomputed file profile (sample and exhaustive coverage are labelled): "
                + json.dumps(profile, ensure_ascii=False, default=str)
                for profile in record["precomputed_file_profiles"]
            ]
            agent_task = build_agent_task(
                query, prepared["evidence"], subset_files, verified_rate=verified_rate
            )
            conversation_dir = self.output_dir / "krama_conversations" / run_id
            conversation_kwargs = dict(
                task=agent_task,
                max_turns=self.max_turns,
                max_retries=self.max_retries,
                output_dir=conversation_dir,
                scenario="default",
                task_id=query_id,
                on_board_created=_publish_ui_board_events,
                initial_facts=profile_messages + evidence_messages,
                termination_trace=termination_trace,
                termination_guard=lambda trace, answer: can_terminate(trace, answer),
                termination_guard_reason=lambda trace, answer: termination_reason(trace, answer),
                review_evidence=record["review_evidence"],
                on_rejection=lambda entry, review: record.setdefault("counterfactual_rejection_events", []).append({
                    "entry": entry.model_dump(mode="json"), "review": dict(review),
                }),
                counterfactual_fraction=self.counterfactual_fraction,
                counterfactual_seed=self.counterfactual_seed,
                counterfactual_streak_threshold=self.counterfactual_streak_threshold,
                max_counterfactual_simulations=self.max_counterfactual_simulations,
            )
            try:
                parameters = inspect.signature(self.conversation_runner).parameters.values()
                supports_usage = any(
                    parameter.name == "on_usage" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
            except (TypeError, ValueError):
                supports_usage = False
            if supports_usage:
                conversation_kwargs["on_usage"] = on_usage
            conversation_report, transcript_path = self.conversation_runner(self.client, **conversation_kwargs)
            record["workflow_counters"] = _merge_workflow_counters(
                record["workflow_counters"], conversation_report.get("workflow_counters")
            )
            conversation_counters_merged = True
            record["counterfactual_capable_agents"] = conversation_report.get(
                "counterfactual_capable_agents", []
            )
            record["board_evidence_entries"] = [
                entry for entry in conversation_report.get("board", {}).get("entries", [])
                if entry.get("tag") == "FACT"
            ]
            record["conversation_transcript"] = str(transcript_path)
            entries = conversation_report.get("board", {}).get("entries", [])
            agent_entries = [entry for entry in entries if entry.get("tag") != "FACT"]
            final_entry = agent_entries[-1] if agent_entries else None
            agent_answer = final_entry.get("prediction") if final_entry else None
            conversation_outcome = conversation_report.get("outcome", "unknown")
            answer_is_verified = (
                isinstance(agent_answer, str)
                and agent_answer.strip()
                and final_entry.get("tag") != "REJECT"
                and conversation_outcome == "scheduler_consensus"
                and can_terminate(
                    termination_trace,
                    termination_candidate(agent_answer, str(final_entry.get("explanation", ""))),
                )
            )
            if answer_is_verified:
                answer = agent_answer
                answer_source = "degraded_agent_consensus" if record.get("data_loop_degraded") else "agent_consensus"
                answer_explanation = str(final_entry.get("explanation", ""))
            elif verified_rate is not None:
                # A validated complete category aggregate is an independently
                # sufficient deterministic answer. Keep the failed/rejected
                # PXP path in the task record, while returning the exact rate
                # rather than turning a proven calculation into a refusal.
                answer = verified_rate["answer"]
                answer_source = (
                    "degraded_verified_data_calculation_fallback"
                    if record.get("data_loop_degraded") else "verified_data_calculation_fallback"
                )
                answer_explanation = (
                    f"Calculated from {verified_rate['numerator']} of "
                    f"{verified_rate['denominator']} rows in the task-selected population; "
                    f"{verified_rate['outcome_column']}={verified_rate['outcome_value']}."
                )
            else:
                raise AgentWorkflowError(
                    f"PXP workflow ended with {conversation_outcome} and no verified deterministic answer"
                )

            record["agent_answer"] = agent_answer
            record["agent_conversation_outcome"] = conversation_outcome
            record["answer_source"] = answer_source
            record["agent_conversation_failures"] = conversation_report.get("failures", [])

            generated = self.pipeline_generator(
                pipeline_usage_client,
                agent_task,
                answer,
                answer_explanation,
                max_retries=self.max_retries,
            )
            pipeline_code = generated.get("pipeline_code")
            if not isinstance(pipeline_code, str) or not pipeline_code.strip():
                raise PipelineGenerationError("pipeline generator returned no pipeline code",
                                              generated.get("attempts", []))
            pipeline_attempts = generated.get("attempts", [])
            turn_attempts = [
                attempt
                for turn in conversation_report.get("turns", [])
                for attempt in turn.get("attempts", [])
            ]
            counterfactual_attempts = [
                attempt
                for simulation in conversation_report.get("counterfactual_attempts", [])
                for attempt in simulation.get("attempts", [])
            ]
            generation_attempts = data_attempts + turn_attempts + counterfactual_attempts + pipeline_attempts
            usage = _provider_usage(prepared["usage"], turn_attempts + counterfactual_attempts + pipeline_attempts)
            record.update({
                "status": "success",
                "answer": answer,
                "answer_source": answer_source,
                "pipeline_code": pipeline_code,
                "execution_outcome": conversation_outcome,
                "provider_token_usage": usage,
                "attempt_count": usage["calls"],
                "generation_attempts": generation_attempts,
                "errors": conversation_report.get("failures", []),
            })
            response = {
                "explanation": {"answer": answer},
                "pipeline_code": pipeline_code,
                "token_usage": usage["prompt_tokens"] + usage["output_tokens"],
                "token_usage_input": usage["prompt_tokens"],
                "token_usage_output": usage["output_tokens"],
            }
            return response
        except Exception as exc:
            if conversation_report is not None:
                if not conversation_counters_merged:
                    record["workflow_counters"] = _merge_workflow_counters(
                        record["workflow_counters"], conversation_report.get("workflow_counters")
                    )
                    conversation_counters_merged = True
                record["board_evidence_entries"] = [
                    entry for entry in conversation_report.get("board", {}).get("entries", [])
                    if entry.get("tag") == "FACT"
                ]
            if isinstance(exc, PipelineGenerationError):
                pipeline_attempts = exc.attempts
            if isinstance(exc, DataToolWorkflowError):
                data_attempts = _data_generation_metadata(exc.attempts)
                record["data_access_trace"] = exc.trace
                record["data_access_usage"] = exc.usage
                record["data_access_diagnostics"] = exc.workflow_diagnostics
                record["data_access_request_diagnostics"] = exc.request_diagnostics
                record["workflow_counters"] = _merge_workflow_counters(
                    record["workflow_counters"],
                    exc.workflow_diagnostics.get("workflow_counters"),
                )
            turn_attempts = [
                attempt
                for turn in (conversation_report or {}).get("turns", [])
                for attempt in turn.get("attempts", [])
            ]
            counterfactual_attempts = [
                attempt
                for simulation in (conversation_report or {}).get("counterfactual_attempts", [])
                for attempt in simulation.get("attempts", [])
            ]
            generation_attempts = data_attempts + turn_attempts + counterfactual_attempts + pipeline_attempts
            record["status"] = "failed"
            record["generation_attempts"] = generation_attempts
            usage = _provider_usage(record["data_access_usage"], turn_attempts + counterfactual_attempts + pipeline_attempts)
            record["attempt_count"] = usage["calls"]
            record["provider_token_usage"] = usage
            record["errors"] = record.get("errors", []) + [{
                "type": type(exc).__name__,
                "message": str(exc),
                "pipeline_attempts": pipeline_attempts,
                "conversation_failures": (
                    conversation_report.get("failures", []) if conversation_report else []
                ),
            }]
            raise
        finally:
            if toolbox is not None:
                record["data_operation_processing_seconds"] = list(toolbox.operation_seconds)
                toolbox.close()
            if not usage_records:
                aggregate = record.get("provider_token_usage", {})
                if int(aggregate.get("calls", 0) or 0) > 0:
                    usage_ledger.ingest_aggregate(
                        session_id=os.environ.get("KRAMA_UI_SESSION_ID") or run_id,
                        task_id=query_id,
                        source="provider",
                        prompt_tokens=int(aggregate.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(aggregate.get("output_tokens", 0) or 0),
                        calls=int(aggregate.get("calls", 0) or 0),
                    )
                elif conversation_report is not None:
                    # Preserve the fallback as explicitly scoped board-entry text estimates.
                    for entry in conversation_report.get("board", {}).get("entries", []):
                        counts = count_tokens_for_entry(
                            str(entry.get("prediction", "")), str(entry.get("explanation", "")),
                            agent_id=str(entry.get("agent_id", "unknown")),
                            model_name=str(record.get("model", MODEL)),
                        )
                        is_sim = bool(entry.get("is_counterfactual_sim", False))
                        usage_record = UsageRecord(
                            session_id=os.environ.get("KRAMA_UI_SESSION_ID") or run_id,
                            task_id=query_id,
                            agent_id=str(entry.get("agent_id", "unknown")),
                            call_kind="counterfactual_sim" if is_sim else "other",
                            branch_id=entry.get("branch_id"),
                            prompt_tokens=0,
                            completion_tokens=counts["total_tokens"],
                            total_tokens=counts["total_tokens"],
                            source="estimated",
                            model=str(record.get("model", MODEL)),
                            started_at=str(entry.get("timestamp") or started_at.isoformat()),
                            duration_ms=0,
                        )
                        usage_ledger.ingest(usage_record)
                    record["usage_records"] = [asdict(item) for item in usage_ledger.records]
            record["usage_ledger"] = usage_ledger.export_report()
            aggregate_usage = record.get("provider_token_usage", {})
            aggregate_provider_tokens = int(aggregate_usage.get("prompt_tokens", 0) or 0) + int(
                aggregate_usage.get("output_tokens", 0) or 0
            )
            callback_provider_tokens = int(record["usage_ledger"].get("provider_total_tokens", 0) or 0)
            record["usage_ledger_reconciliation"] = {
                "provider_aggregate_tokens": aggregate_provider_tokens,
                "ledger_provider_tokens": callback_provider_tokens,
                "matches": aggregate_provider_tokens == callback_provider_tokens,
                "per_call_attribution_available": bool(usage_records),
            }
            ledger_path = self.output_dir / "usage_ledgers" / f"{run_id}.json"
            usage_ledger.write_json(ledger_path)
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            record["runtime_seconds"] = round(time.perf_counter() - start, 4)
            record["task_wall_clock_elapsed_seconds"] = round(time.monotonic() - (wall_deadline - self.task_wall_clock_cap_seconds), 4)
            if hasattr(self.client, "set_task_deadline"):
                self.client.set_task_deadline(None)
            self._write_record(record)
