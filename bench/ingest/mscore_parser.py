"""Gold-separated loader for MSCoRe JSON and JSONL task files.

This module only normalizes task records. It does not claim official benchmark
scoring; callers must select an evaluator appropriate to the record's format.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MSCoReAgentTask(BaseModel):
    """Only fields safe to provide to an agent; reference fields are forbidden."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    prompt: str
    task_type: str
    domain: str
    source_record: str
    source_row_index: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class MSCoReReference(BaseModel):
    """Evaluator-only values; never pass this object to an agent or conversation."""

    answer: str


class MSCoReTask(BaseModel):
    agent_task: MSCoReAgentTask
    evaluator_reference: MSCoReReference


class MSCoReLoadReport(BaseModel):
    source_path: str
    source_sha256: str
    records_seen: int
    records_loaded: int
    records_rejected: int
    rejection_reasons: dict[str, int] = Field(default_factory=dict)


class MSCoReLoadResult(list[MSCoReTask]):
    def __init__(self, tasks: list[MSCoReTask], report: MSCoReLoadReport):
        super().__init__(tasks)
        self.report = report


def _task_type(record: dict[str, Any], source_record: str) -> str:
    path = source_record.casefold().replace("_", "-").replace(" ", "-")
    answer = record.get("answer", record.get("output", record.get("label", "")))
    if any(word in path for word in ("judgment", "construction")) or str(answer).strip() in {
        "是", "否", "对", "错", "true", "false", "yes", "no",
    }:
        return "judgment"
    if any(word in path for word in ("ordering", "software-engineer", "sequence")):
        return "ordering"
    if "multi-choice" in path or "multiple-choice" in path:
        return "multi_choice"
    if "single-choice" in path:
        return "single_choice"
    if isinstance(record.get("options"), (dict, list)):
        return "choice"
    return "open_ended"


def _domain(source_record: str, record: dict[str, Any]) -> str:
    declared = record.get("domain")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    parts = Path(source_record).parts
    for marker in ("Data2", "MSCoRe-data"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return parts[-2] if len(parts) > 1 else "unknown"


def _prompt_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    instruction = record.get("instruction") or record.get("question")
    context = record.get("input") or record.get("context")
    if instruction is not None and str(instruction).strip():
        parts.append(str(instruction).strip())
    if context is not None and str(context).strip():
        parts.append(str(context).strip())
    options = record.get("options")
    if isinstance(options, dict):
        if all(isinstance(value, (str, int, float)) for value in options.values()):
            parts.append("Options:\n" + "\n".join(f"{key}. {value}" for key, value in options.items()))
    elif isinstance(options, list):
        if all(isinstance(value, (str, int, float)) for value in options):
            parts.append("Options:\n" + "\n".join(f"{i + 1}. {value}" for i, value in enumerate(options)))
    return "\n\n".join(parts)


def parse_record(record: Any, *, source_record: str, index: int) -> MSCoReTask:
    if not isinstance(record, dict):
        raise ValueError("record_not_object")
    prompt = _prompt_text(record)
    answer = record.get("answer", record.get("output", record.get("label")))
    if not prompt:
        raise ValueError("missing_prompt")
    options = record.get("options")
    if isinstance(options, dict) and not all(isinstance(value, (str, int, float)) for value in options.values()):
        raise ValueError("unsupported_options_shape")
    if isinstance(options, list) and not all(isinstance(value, (str, int, float)) for value in options):
        raise ValueError("unsupported_options_shape")
    if answer is None or not str(answer).strip():
        raise ValueError("missing_reference")
    source_parts = Path(source_record).parts
    source_key = source_record
    if "MSCoRe-data" in source_parts:
        source_key = "/".join(source_parts[source_parts.index("MSCoRe-data") + 1:])
    stable = hashlib.sha256(f"{source_key}:{index}".encode()).hexdigest()[:12]
    domain = _domain(source_record, record)
    safe_metadata = {
        "input_present": bool(record.get("input") or record.get("context")),
        "options_present": isinstance(record.get("options"), (dict, list)),
    }
    task_type = _task_type(record, source_record)
    return MSCoReTask(
        agent_task=MSCoReAgentTask(
            task_id=f"mscore-{stable}-{index}", prompt=prompt, task_type=task_type,
            domain=domain, source_record=source_record, source_row_index=index,
            metadata=safe_metadata,
        ),
        evaluator_reference=MSCoReReference(answer=str(answer).strip()),
    )


def load_mscore_file(path: str | Path, *, limit: int | None = None) -> MSCoReLoadResult:
    source = Path(path).expanduser().resolve()
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        raise ValueError(f"{source} is a Git LFS pointer; fetch the dataset object with git lfs pull")
    if source.suffix.casefold() == ".jsonl":
        records = []
        for line in text.splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    records.append(None)
    else:
        decoded = json.loads(text)
        records = decoded if isinstance(decoded, list) else [decoded]

    tasks: list[MSCoReTask] = []
    rejects: Counter[str] = Counter()
    for index, record in enumerate(records):
        try:
            task = parse_record(record, source_record=str(source), index=index)
        except ValueError as exc:
            rejects[str(exc)] += 1
            continue
        if limit is None or len(tasks) < limit:
            tasks.append(task)
    report = MSCoReLoadReport(
        source_path=str(source), source_sha256=digest, records_seen=len(records),
        records_loaded=len(tasks), records_rejected=sum(rejects.values()),
        rejection_reasons=dict(sorted(rejects.items())),
    )
    return MSCoReLoadResult(tasks, report)


def normalize_judgment(value: str) -> str | None:
    """Normalize explicit binary labels only; do not guess from free-form prose."""
    text = value.strip().casefold().strip(" \t\r\n.!?,，。！？:：;；\"'“”‘’()（）")
    yes = {"是", "对", "正确", "合理", "可行", "true", "yes", "y", "1"}
    no = {"否", "不", "错", "错误", "不合理", "不可行", "false", "no", "n", "0"}
    return "yes" if text in yes else "no" if text in no else None


def score_exact(task: MSCoReTask, prediction: str) -> dict[str, Any]:
    """Conservative adapted exact scoring for objective labels; no semantic guesswork."""
    task_type = task.agent_task.task_type
    reference = task.evaluator_reference.answer
    if task_type == "judgment":
        expected, actual = normalize_judgment(reference), normalize_judgment(prediction)
    elif task_type in {"single_choice", "choice"}:
        expected, actual = _extract_choice(reference), _extract_choice(prediction)
    elif task_type == "multi_choice":
        expected, actual = _extract_choices(reference), _extract_choices(prediction)
        if expected is not None and actual is not None:
            return {"metric": "adapted_exact_set_match", "score": float(actual == expected),
                    "scored": True, "reason": None}
    elif task_type == "ordering":
        expected, actual = _extract_order(reference), _extract_order(prediction)
    else:
        return {"metric": None, "score": None, "scored": False,
                "reason": "open_ended_requires_a_configured_semantic_evaluator"}
    if expected is None or actual is None or not expected or not actual:
        return {"metric": "adapted_exact_match", "score": None, "scored": False,
                "reason": "prediction_or_reference_not_a_supported_label"}
    return {"metric": "adapted_exact_match", "score": float(actual == expected),
            "scored": True, "reason": None}


def _extract_choice(value: str) -> str | None:
    text = value.strip().upper()
    if re.fullmatch(r"[A-Z]", text):
        return text
    matches = re.findall(r"(?:答案|选项|选择|ANSWER|OPTION)\s*[:：是]?\s*([A-Z])", text)
    if not matches:
        match = re.match(r"^\s*([A-Z])(?:[.)、:：\s]|$)", text)
        matches = [match.group(1)] if match else []
    return matches[-1] if matches else None


def _extract_choices(value: str) -> frozenset[str] | None:
    text = value.strip().upper()
    if re.fullmatch(r"[A-Z](?:\s*[,，、|/]\s*[A-Z])*", text):
        return frozenset(re.findall(r"[A-Z]", text))
    matches = re.findall(r"(?:答案|选项|选择|ANSWER|OPTIONS?)\s*[:：]?\s*([A-Z](?:\s*[,，、|/]\s*[A-Z])*)", text)
    if not matches:
        return None
    return frozenset(re.findall(r"[A-Z]", matches[-1]))


def _extract_order(value: str) -> str | None:
    text = value.strip().upper()
    if re.fullmatch(r"[A-Z](?:\s*[-,，、>＞]\s*[A-Z])+", text):
        return "".join(re.findall(r"[A-Z]", text))
    matches = re.findall(r"(?:答案|顺序|排序|ORDER)\s*[:：]?\s*([A-Z](?:\s*[-,，、>＞]\s*[A-Z])+)", text)
    return "".join(re.findall(r"[A-Z]", matches[-1])) if matches else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and validate MSCoRe task data without model calls.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--print-agent-tasks", action="store_true")
    args = parser.parse_args(argv)
    result = load_mscore_file(args.input, limit=args.limit)
    print(result.report.model_dump_json(indent=2))
    if args.print_agent_tasks:
        for task in result:
            print(task.agent_task.model_dump_json(ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
