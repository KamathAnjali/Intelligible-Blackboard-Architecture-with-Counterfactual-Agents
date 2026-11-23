"""KramaBench parser with a strict agent-facing task and separate references.

The loader records a hash-backed LoadReport. Its CLI defaults to the documented
format sample, which is not benchmark data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("krama-parser")


# ─────────────────────────────────────────────────────────────────────────────
# Internal task contract
# ─────────────────────────────────────────────────────────────────────────────

class AgentFacingTask(BaseModel):
    """Gold-free task data allowed to cross into agent-facing code."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    task_text: str
    domain: str = "general"
    difficulty: str = "medium"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_text")
    @classmethod
    def _non_empty_task_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_text cannot be empty")
        return value


class EvaluatorReference(BaseModel):
    """Reference-only fields; never pass this object to an agent."""

    expected_answer: str | None = None
    reference_steps: List[str] = Field(default_factory=list)


class TaskFormat(BaseModel):
    """Parsed record with a gold-free agent view and isolated evaluator reference."""

    task_id: str = Field(default_factory=lambda: f"kb_task_{uuid4().hex[:8]}")
    task_text: str
    domain: str = "general"
    difficulty: str = "medium"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evaluator_reference: EvaluatorReference = Field(default_factory=EvaluatorReference)

    @field_validator("task_text")
    @classmethod
    def _non_empty_task_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_text cannot be empty — problem_statement is required.")
        return value

    @property
    def agent_facing_task(self) -> AgentFacingTask:
        return AgentFacingTask(
            task_id=self.task_id, task_text=self.task_text, domain=self.domain,
            difficulty=self.difficulty, metadata=_agent_safe_metadata(self.metadata),
        )


class LoadReport(BaseModel):
    n_total: int
    n_loaded: int
    n_rejected: int
    n_limited_out: int = 0
    rejection_reasons: Dict[str, int] = Field(default_factory=dict)
    source_path: str
    file_sha256: str = ""


class LoadResult(list[TaskFormat]):
    """List-compatible loaded tasks carrying their structured report."""

    def __init__(self, tasks: List[TaskFormat], report: LoadReport):
        super().__init__(tasks)
        self.report = report


_REFERENCE_KEYS = {
    "answer", "expectedanswer", "groundtruthanswer", "answerkey", "gold", "goldanswer", "reference", "referenceanswer",
    "referencesteps", "groundtruth", "groundtruthreasoningsteps", "subtasks",
    "pipeline", "referencepipeline", "solution", "query",
}


def _agent_safe_metadata(value: Any) -> Any:
    """Strip known evaluator/reference fields recursively from agent metadata."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = "".join(char for char in str(key).casefold() if char.isalnum())
            if normalized in _REFERENCE_KEYS:
                continue
            safe[str(key)] = _agent_safe_metadata(item)
        return safe
    if isinstance(value, list):
        return [_agent_safe_metadata(item) for item in value]
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_krama_schema(record: Any) -> Optional[str]:
    """
    Validate a raw record dict.

    Returns:
        None if valid, or an error message string if invalid.
    """
    if not isinstance(record, dict):
        return "Record is not a dict."
    problem = record.get("problem_statement") or record.get("task_text") or record.get("query")
    if not problem or not str(problem).strip():
        return "Missing/empty 'problem_statement', 'task_text', or 'query'."
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Single-record parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_krama_record(raw: Dict[str, Any]) -> TaskFormat:
    """Convert one raw KramaBench record into separated task/reference objects."""
    err = validate_krama_schema(raw)
    if err:
        raise ValueError(f"Invalid KramaBench record: {err}")

    task_id = str(raw.get("task_id") or raw.get("id") or f"kb_task_{uuid4().hex[:8]}")
    task_text = str(raw.get("problem_statement") or raw.get("task_text") or raw.get("query")).strip()
    raw_id = str(raw.get("id") or raw.get("task_id") or "")
    domain = str(raw.get("domain") or (raw_id.split("-")[0] if "-" in raw_id else "general"))
    difficulty = str(raw.get("difficulty") or ("hard" if "hard" in raw_id else ("easy" if "easy" in raw_id else "medium")))

    raw_steps = raw.get("ground_truth_reasoning_steps") or []
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]
    reference_steps = [str(step) for step in raw_steps if str(step).strip()] if isinstance(raw_steps, list) else []
    if not reference_steps and isinstance(raw.get("subtasks"), list):
        for sub in raw["subtasks"]:
            if isinstance(sub, dict):
                description = sub.get("step") or sub.get("query") or sub.get("description") or ""
                answer = sub.get("answer")
                if description:
                    line = f"{description}"
                    if answer not in (None, ""):
                        line += f" Expected result: {answer}"
                    reference_steps.append(line)
            elif isinstance(sub, str) and sub.strip():
                reference_steps.append(sub.strip())

    raw_metadata = raw.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    for source_key, target_key in (
        ("answer_type", "answer_type"), ("data_sources", "data_sources"),
        ("deepresearch_subset", "deepresearch_subset"), ("runtime", "benchmark_runtime"),
    ):
        if source_key in raw:
            metadata[target_key] = raw[source_key]

    answer_value = raw.get("expected_answer", raw.get("answer"))
    expected_answer = None if answer_value is None else str(answer_value).strip()
    return TaskFormat(
        task_id=task_id, task_text=task_text, domain=domain, difficulty=difficulty,
        metadata=metadata,
        evaluator_reference=EvaluatorReference(
            expected_answer=expected_answer, reference_steps=reference_steps,
        ),
    )


def parse_krama_json(raw_json: str) -> TaskFormat:
    """Parse one KramaBench task encoded as a JSON object string."""
    try:
        record = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid KramaBench JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("A single KramaBench JSON task must be an object.")
    return parse_krama_record(record)


def _report_path(filepath: str | Path) -> Path:
    source = Path(filepath)
    return Path(__file__).resolve().parents[2] / "bench" / "data" / "load_reports" / f"{source.stem}.json"


def load_krama_dataset(
    filepath: str | Path,
    n: Optional[int] = None,
    *,
    report_path: str | Path | None = None,
) -> LoadResult:
    """Load records and return a list-compatible result carrying its LoadReport.

    ``n`` limits how many valid tasks are returned, but the full source is
    scanned so n_total and malformed-record counts remain accurate.
    """
    if n is not None and (type(n) is not int or n < 0):
        raise ValueError("n must be a non-negative integer or None")
    path = Path(filepath).expanduser()
    rejection_reasons: Counter[str] = Counter()
    parsed: List[TaskFormat] = []
    n_total = 0
    n_limited_out = 0
    digest = ""
    rows: list[tuple[Any, str | None, str]] = []

    try:
        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8-sig")
        if path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                n_total += 1
                try:
                    rows.append((json.loads(line), None, f"line {line_number}"))
                except json.JSONDecodeError:
                    rows.append((None, "invalid_json", f"line {line_number}"))
        else:
            data = json.loads(text)
            records = data if isinstance(data, list) else [data]
            n_total = len(records)
            rows = [(item, None, f"index {index}") for index, item in enumerate(records)]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        rejection_reasons["source_unreadable"] += 1
        logger.error("Failed to read %s: %s", path, type(exc).__name__)

    for raw, parse_error, location in rows:
        if parse_error:
            rejection_reasons[parse_error] += 1
            logger.warning("Skipped malformed record (%s): %s", location, parse_error)
            continue
        try:
            task = parse_krama_record(raw)
        except Exception as exc:
            reason = "record_not_object" if not isinstance(raw, dict) else "invalid_task_record"
            rejection_reasons[reason] += 1
            logger.warning("Skipped malformed record (%s): %s", location, type(exc).__name__)
            continue
        if n is None or len(parsed) < n:
            parsed.append(task)
        else:
            n_limited_out += 1

    report = LoadReport(
        n_total=n_total,
        n_loaded=len(parsed),
        n_rejected=sum(count for reason, count in rejection_reasons.items() if reason != "source_unreadable"),
        n_limited_out=n_limited_out,
        rejection_reasons=dict(sorted(rejection_reasons.items())),
        source_path=str(path.resolve()),
        file_sha256=digest,
    )
    result = LoadResult(parsed, report)
    logger.info(
        "LoadReport: total=%d loaded=%d rejected=%d source=%s",
        report.n_total, report.n_loaded, report.n_rejected, path,
    )
    if report_path is not None:
        target = Path(report_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return result


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse KramaBench task JSON/JSONL without mixing agent and reference data.")
    parser.add_argument("--input", "--file", "-f", dest="input_path",
                        default=str(Path(__file__).resolve().parents[2] / "bench" / "data" / "sample.jsonl"),
                        help="Input .json or .jsonl file; defaults to the documented format sample.")
    parser.add_argument("--n", "-n", type=int, default=None, help="Maximum valid tasks returned; all records are counted in the report.")
    parser.add_argument("--report", action="store_true", help="Write LoadReport JSON under bench/data/load_reports/.")
    parser.add_argument("--report-path", type=Path, help="Override the report destination; implies --report.")
    parser.add_argument("--print-tasks", action="store_true", help="Print only gold-free agent-facing task objects.")
    args = parser.parse_args(argv)
    target_report = args.report_path or (_report_path(args.input_path) if args.report else None)
    result = load_krama_dataset(args.input_path, n=args.n, report_path=target_report)
    report = result.report
    print(f"LoadReport: total={report.n_total} loaded={report.n_loaded} rejected={report.n_rejected} sha256={report.file_sha256 or 'unavailable'}")
    if target_report:
        print(f"Report saved: {target_report}")
    if args.print_tasks:
        for task in result:
            print(task.agent_facing_task.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli(sys.argv[1:]))
