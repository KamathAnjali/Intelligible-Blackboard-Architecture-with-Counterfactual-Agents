"""Allowlisted, bounded data tools for KramaBench task inputs.

Data files are parsed into a temporary SQLite database. The agent can request
only the structured operations below; it cannot submit SQL or Python code.
Source files are opened read-only and are never copied into a model prompt.
"""

from __future__ import annotations

import copy
import csv
import hashlib
from collections import Counter
from html.parser import HTMLParser
import io
import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
import time
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bench.evidence import empty_filter_hint


MAX_DATA_FILES = 2048
MAX_SINGLE_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 512 * 1024 * 1024
MAX_TOOL_OUTPUT_CHARS = 18_000
MAX_PAGE_SIZE = 50
MAX_TEXT_PAGE_CHARS = 8_000
MAX_GROUPS = 5_000
MAX_EMPTY_FILTER_DIAGNOSTICS = 4
MAX_EMPTY_FILTER_VALUES = 8
MAX_EMPTY_FILTER_VALUE_CHARS = 120
MAX_REQUEST_DIAGNOSTIC_CHARS = 2_000
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_DATA_TOOL_INVOCATION_CAP = 12
MAX_BLOCKED_COMPLETION_RETRIES = 1
_NUMBER = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_RATE_QUERY = re.compile(r"\b(percent(?:age)?|rate|proportion|fraction|share)\b", re.IGNORECASE)
_DIAGNOSTIC_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+[^\s,;]+)\b",
    re.IGNORECASE,
)
_SENSITIVE_DIAGNOSTIC_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|password|passwd|secret|credential|authorization)",
    re.IGNORECASE,
)


class DataAccessError(RuntimeError):
    """Invalid or unsupported data operation; never silently returns partial data."""


class DataToolOutputTooLarge(DataAccessError):
    """The result could not fit in a single tool response."""


class DataOperationTimeout(DataAccessError):
    """One data operation exceeded its processing-only deadline."""


class TaskWallClockTimeout(DataAccessError):
    """The end-to-end task wall-clock cap was exceeded."""


class DataToolFileSelectionError(DataAccessError):
    """A single-file operation did not name exactly one allowlisted file."""


class DataToolWorkflowError(DataAccessError):
    """The model did not complete an allowlisted data investigation."""

    def __init__(self, message: str, *, attempts: list[dict[str, Any]] | None = None,
                 trace: list[dict[str, Any]] | None = None,
                 usage: dict[str, Any] | None = None,
                 workflow_diagnostics: dict[str, Any] | None = None,
                 request_diagnostics: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.trace = list(trace or [])
        self.usage = dict(usage or {})
        self.workflow_diagnostics = dict(workflow_diagnostics or {})
        self.request_diagnostics = list(request_diagnostics or [])


class DataFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    operator: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte", "is_null", "not_null"]
    value: str | int | float | None = None


class DataMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: Literal["count", "sum", "mean", "min", "max"]
    column: str = "*"
    name: str | None = None


class DataToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["catalog", "profile", "preview", "filter", "aggregate", "read_text", "search_text", "json_get", "done"]
    file: str | None = None
    files: list[str] = Field(default_factory=list, max_length=MAX_DATA_FILES)
    table: str | None = None
    columns: list[str] | None = Field(default=None, max_length=2_000)
    filters: list[DataFilter] = Field(default_factory=list, max_length=32)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    measures: list[DataMeasure] = Field(default_factory=list, max_length=32)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)
    text: str | None = None
    path: list[str | int] = Field(default_factory=list, max_length=64)
    max_chars: int = Field(default=4_000, ge=1, le=MAX_TEXT_PAGE_CHARS)
    summary: str | None = Field(default=None, max_length=4_000)
    rate_outcome_column: str | None = Field(
        default=None,
        description=(
            "Exact name of a categorical outcome column for a rate task, such as a yes/no column. "
            "Omit when the task reads an existing numeric percentage."
        ),
    )

    @model_validator(mode="after")
    def validate_aggregate_and_completion_fields(self):
        if self.action == "aggregate":
            if self.columns is not None:
                raise ValueError(
                    "aggregate does not accept `columns`; use `group_by` to return category counts"
                )
            if not self.measures:
                raise ValueError(
                    "aggregate requires at least one measure; use count with column='*' for row counts"
                )
            pairs = [(measure.function, measure.column) for measure in self.measures]
            if len(pairs) != len(set(pairs)):
                raise ValueError(
                    "aggregate measures must not repeat the same function and column; remove redundant measures"
                )
            names = [measure.name or f"{measure.function}_{measure.column}" for measure in self.measures]
            if len(names) != len(set(names)):
                raise ValueError("aggregate measure output names must be unique")
        return self


def ollama_data_tool_schema() -> dict[str, Any]:
    """Return a compact JSON Schema for Ollama's constrained-format grammar.

    Pydantic's full schema contains references, ``anyOf`` branches, defaults,
    and validation bounds. The data-tool model remains the validation
    authority; this format keeps the structural contract and enum choices but
    omits metadata and bounds that are checked again after generation.
    """
    full_schema = DataToolRequest.model_json_schema()
    definitions = full_schema.get("$defs", {})
    structural_keys = {
        "type", "properties", "required", "items", "enum", "additionalProperties",
    }

    def simplify(node: Any) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise ValueError("unexpected non-object node in DataToolRequest schema")
        node = copy.deepcopy(node)
        reference = node.pop("$ref", None)
        if reference is not None:
            prefix = "#/$defs/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                raise ValueError("unsupported reference in DataToolRequest schema")
            definition_name = reference[len(prefix):]
            if definition_name not in definitions:
                raise ValueError("unknown reference in DataToolRequest schema")
            referenced = simplify(definitions[definition_name])
            referenced.update(node)
            node = referenced

        alternatives = node.pop("anyOf", None)
        if alternatives is not None:
            if not isinstance(alternatives, list) or not alternatives:
                raise ValueError("unsupported union in DataToolRequest schema")
            branches = [simplify(branch) for branch in alternatives]
            types: list[str] = []
            for branch in branches:
                branch_type = branch.get("type")
                branch_types = branch_type if isinstance(branch_type, list) else [branch_type]
                if not all(isinstance(item, str) for item in branch_types):
                    raise ValueError("unsupported union branch in DataToolRequest schema")
                for item in branch_types:
                    if item not in types:
                        types.append(item)
            union: dict[str, Any] = {"type": types}
            # Nullable array unions need their array item schema retained.
            for key in structural_keys - {"type"}:
                values = [branch[key] for branch in branches if key in branch]
                if values and all(value == values[0] for value in values):
                    union[key] = values[0]
            union.update({key: value for key, value in node.items() if key in structural_keys})
            node = union

        result: dict[str, Any] = {}
        for key, value in node.items():
            if key not in structural_keys:
                continue
            if key == "properties":
                if not isinstance(value, dict):
                    raise ValueError("unexpected properties in DataToolRequest schema")
                result[key] = {name: simplify(child) for name, child in value.items()}
            elif key == "items":
                result[key] = simplify(value)
            elif key == "required":
                result[key] = list(value)
            elif key == "enum":
                result[key] = list(value)
            else:
                result[key] = value
        return result

    return simplify(full_schema)


DATA_TOOL_SYSTEM_PROMPT = """You are the data-access step for a benchmark task. Use only the supplied task query and the allowlisted files.
You cannot write SQL, Python, or shell commands. Request one structured tool action at a time.
Return a compact JSON object containing only the fields needed for the chosen action. Never copy tool results or explanations into a request.
Use `summary` only with action `done`, and keep it short. For `search_text`, `text` must be a short search phrase, not the document or search results.
For `rate_outcome_column`, provide only the exact name of a categorical outcome column, such as a yes/no column; never include its value or an explanation. Omit it when reading an existing numeric percentage.
For `aggregate`, use no `columns` or `summary` field, provide at least one `measures` item, and use at most three `group_by` columns.
Scope qualifier: task-selected files define the population supplied for the query. Apply an extra geography filter only when an available field represents that same level; a broader named scope may already be defined by the selected files, and a zero-match filter does not disprove it.
Available actions: catalog, profile, preview, filter, aggregate, read_text, search_text, json_get, done.
Use one of these valid request shapes, replacing placeholders with the exact values shown in the catalog:
- catalog: {"action":"catalog"}
- profile: {"action":"profile","file":"<exact allowlisted path>","table":"<catalog table>"}
- preview: {"action":"preview","file":"<exact allowlisted path>","table":"<catalog table>","columns":["<column>"],"limit":5}
- filter: {"action":"filter","file":"<exact allowlisted path>","table":"<catalog table>","filters":[{"column":"<column>","operator":"eq","value":"<value>"}],"limit":5}
- aggregate: {"action":"aggregate","file":"<exact allowlisted path>","table":"<catalog table>","group_by":[],"measures":[{"function":"sum","column":"<numeric column>"}]}
- read_text: {"action":"read_text","file":"<exact allowlisted path>","offset":0,"max_chars":4000}
- search_text: {"action":"search_text","file":"<exact allowlisted path>","text":"<search phrase>","offset":0,"limit":5}
- json_get: {"action":"json_get","file":"<exact allowlisted path>","path":["<key>"],"offset":0,"limit":5}
- done: {"action":"done","summary":"<concise evidence summary>"}
For `aggregate`, output columns are defined by `group_by` and `measures`; never send a `columns` field. An aggregate needs at least one measure.
For profile, preview, filter, read_text, search_text, json_get, and single-file aggregate requests, use `file` with the exact `path` shown in `allowlisted_files`. When exactly one file is allowlisted, an omitted selector defaults to that file; with multiple files, `file` is required. A single-entry `files` list is also accepted for single-file actions. For a calculation spanning multiple files, use aggregate with a `files` list; it validates matching table schemas and processes every listed file.
`profile` returns table metadata, row counts, missing counts, and up to five sample values per column; it does not return all rows. These samples are examples, not an exhaustive category inventory; do not conclude a category is absent because it is missing from the sample.
Use the column name and sample values to identify what a field represents before filtering. A `County` or `County Description` field contains county names, not state names; do not search for a state name in a county field. If a geographic filter returns no rows, inspect the field's categories and the dataset context before deciding what the result means.
`limit` is the maximum number of rows, matches, or JSON items returned by one preview/filter/search_text/json_get response. It must be between 1 and 50. Never set it to a table's row_count, and do not page through a large table to reconstruct it.
For calculations over a complete table, use `aggregate`; it scans all matching rows and returns summary groups rather than row records. Every aggregate requires at least one measure. It supports `count`, `sum`, `mean`, `min`, and `max`, filters, and up to three `group_by` columns. `columns` does not select aggregate output; use `group_by` for categories. Do not repeat the same function/column measure. Keep grouping focused: at most 5,000 groups and the overall tool result must fit the output limit.
For percentages or rates, group by the categorical outcome and request `count` with `column="*"`. This returns a count for every category, including missing values; `count(column)` excludes missing values and is not a row denominator. The denominator is the count across the relevant population (or the sum of its category counts); compute the percentage from the numerator category and that denominator. `rate_outcome_column` is workflow metadata, not an action-specific argument: it may accompany any request to declare the exact outcome column, and does not change the requested operation. Declare it as soon as known and repeat it in the final `done` request. Do not claim a rate from a sample, a non-null count, or an empty filtered result.
Categorical indicators such as `Violation` may contain values like `yes` and `no`. To count violations, explicitly count rows where the category is `yes`; a non-null count includes both `yes` and `no` and is not a violation count.
An empty filter result includes a bounded preview of observed values for each filtered column and the attempted filter value. Do not repeat the same unsuccessful filter unless you change the hypothesis. An empty result does not prove that an outcome category is absent or that a rate is zero. For a task-relevant empty filter, inspect unfiltered category counts for the filter column, then separately inspect unfiltered counts for the rate outcome column. Use those observations to decide whether the filter used the wrong geographic level, the dataset already represents the requested geography, or the requested subset cannot be identified from available columns. Do not require category inspection for an unrelated exploratory empty filter. If an empty result is valid, explain why using the observed categories.
If `done` is rejected because required category evidence is missing, the workflow may run the exact unfiltered `aggregate` requests needed for those columns using the same bounded tool budget. Review the returned `aggregate_recovery` results in `previous_tool_results`; do not repeat those aggregates unless the request or evidence shows a specific problem.
Use preview/filter only when specific example rows are needed. Use read_text with successive offsets only for text documents, with each response bounded by `max_chars`.
Read short text files directly. If a task asks for a calculated statistic, collect its inputs from the source; the statistic's name may never appear there.
If an operation reports malformed data, missing values, or a limit, report that accurately and refine the request where possible.
Do not guess values from a preview when a calculation over the full dataset is required. Do not request or invent reference answers, reference subtasks, or expected pipeline code.
Finish with action=done only after collecting enough evidence for the task; give a concise evidence summary.
Return a single JSON object matching the requested schema."""


def _corrected_aggregate_example(response_text: Any, toolbox: "DatasetToolbox | None") -> str | None:
    """Build a safe corrected example only from an exact allowlisted file/table."""
    if toolbox is None or not isinstance(response_text, str):
        return None
    try:
        submitted = json.loads(response_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(submitted, dict) or submitted.get("action") != "aggregate" or "columns" not in submitted:
        return None

    file_path = submitted.get("file")
    if not isinstance(file_path, str) or file_path not in toolbox.files:
        return None
    table_name = submitted.get("table")
    if table_name is None:
        candidates = [table for (path, _), table in toolbox.tables.items() if path == file_path]
        if len(candidates) != 1:
            return None
        table = candidates[0]
    else:
        table = toolbox.tables.get((file_path, table_name))
        if table is None:
            return None

    measures = submitted.get("measures")
    if not isinstance(measures, list) or not measures:
        return None
    # Keep only the requested aggregation and validated table identifiers. The
    # invalid `columns` field is intentionally not coerced into another field.
    candidate: dict[str, Any] = {
        "action": "aggregate",
        "file": file_path,
        "table": table["name"],
        "measures": measures,
    }
    group_by = submitted.get("group_by", [])
    if isinstance(group_by, list) and all(
        isinstance(column, str) and column in table["columns"] for column in group_by
    ):
        candidate["group_by"] = group_by
    try:
        parsed = DataToolRequest.model_validate(candidate, strict=True)
    except ValidationError:
        return None
    for measure in parsed.measures:
        if measure.column != "*" and measure.column not in table["columns"]:
            return None
    return json.dumps(parsed.model_dump(exclude_none=True), ensure_ascii=False, separators=(",", ":"))


def _validation_feedback(exc: Exception, response_text: Any = None,
                         toolbox: "DatasetToolbox | None" = None) -> str:
    """Summarize validation rules without replaying model-supplied values."""
    if not isinstance(exc, ValidationError):
        if isinstance(exc, (TypeError, ValueError)):
            message = re.sub(r"\s+", " ", str(exc))[:160]
            return f"\nValidation feedback: response must be one JSON object matching the supplied schema ({message})."
        return "\nValidation feedback: response must be one JSON object matching the supplied schema."
    details = []
    for error in exc.errors()[:4]:
        location = ".".join(
            re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:40]
            for part in error.get("loc", ())
        ) or "request"
        message = re.sub(r"\s+", " ", str(error.get("msg", "invalid value")))[:160]
        details.append(f"{location}: {message}")
    feedback = (
        "\nValidation feedback (field and rule only; rejected values omitted):\n- "
        + "\n- ".join(details)
        if details else ""
    )
    submitted = None
    try:
        submitted = json.loads(response_text) if isinstance(response_text, str) else None
    except (json.JSONDecodeError, TypeError):
        pass
    if isinstance(submitted, dict) and submitted.get("action") == "aggregate" and "summary" in submitted:
        file_path = submitted.get("file")
        table_name = submitted.get("table")
        if toolbox is not None:
            if file_path not in toolbox.files:
                file_path = toolbox.last_successful_file or next(iter(toolbox.files), None)
            tables = [name for path, name in toolbox.tables if path == file_path]
            if table_name not in tables:
                table_name = tables[0] if tables else None
        if file_path and table_name:
            aggregate = json.dumps({"action": "aggregate", "file": file_path, "table": table_name,
                                    "measures": [{"function": "count", "column": "*"}]})
            done = json.dumps({"action": "done", "summary": "<brief summary supported by tool results>"})
            feedback += (
                "\n`summary` is a field of action `done`, not `aggregate`. Do not silently change the action. "
                "If you meant to finish, send action=done (example, not executed): " + done +
                " If you meant to count rows, send aggregate with measures (example, not executed): " + aggregate
            )
    if isinstance(submitted, dict) and submitted.get("action") == "filter":
        limit = submitted.get("limit")
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > MAX_PAGE_SIZE:
            file_path = submitted.get("file")
            table_name = submitted.get("table")
            if toolbox is not None:
                if file_path not in toolbox.files:
                    file_path = toolbox.last_successful_file or next(iter(toolbox.files), None)
                tables = [name for path, name in toolbox.tables if path == file_path]
                if table_name not in tables:
                    table_name = tables[0] if tables else None
            feedback += f"\nA filter returns at most {MAX_PAGE_SIZE} rows; set `limit` to {MAX_PAGE_SIZE} or less; counts require aggregate with `group_by`."
            if file_path and table_name:
                column = "<actual column>"
                if toolbox is not None:
                    table = toolbox.tables.get((file_path, table_name))
                    if table and table.get("columns"):
                        column = table["columns"][0]
                filter_example = json.dumps({"action": "filter", "file": file_path, "table": table_name, "limit": MAX_PAGE_SIZE})
                aggregate_example = json.dumps({"action": "aggregate", "file": file_path, "table": table_name,
                    "group_by": [column], "measures": [{"function": "count", "column": "*"}]})
                feedback += " Corrected filter example (not executed): " + filter_example + ". Category-count example (not executed): " + aggregate_example
    example = _corrected_aggregate_example(response_text, toolbox)
    if example is not None:
        feedback += (
            "\nThe aggregate request was rejected because `columns` is not an aggregate argument. "
            "Do not silently rename it; submit a corrected request using the actual allowlisted "
            "file/table and the requested measure. Corrected example (not executed): " + example
        )
    return feedback


def _validation_error_details(exc: Exception) -> list[dict[str, str]]:
    """Return bounded Pydantic field/rule diagnostics without submitted values."""
    if isinstance(exc, ValidationError):
        details = []
        for error in exc.errors()[:8]:
            location = ".".join(
                re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))[:40]
                for part in error.get("loc", ())
            ) or "request"
            message = re.sub(r"\s+", " ", str(error.get("msg", "invalid value")))[:200]
            details.append({
                "field": location,
                "type": str(error.get("type", "validation_error"))[:80],
                "message": _DIAGNOSTIC_SECRET.sub("[REDACTED]", message),
            })
        return details
    message = re.sub(r"\s+", " ", str(exc))[:200]
    return [{
        "field": "request",
        "type": type(exc).__name__[:80],
        "message": _DIAGNOSTIC_SECRET.sub("[REDACTED]", message),
    }]


def _redact_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    """Redact obvious secrets and bound request excerpts before persistence."""
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 64:
                result["_truncated"] = True
                break
            safe_key = str(key)[:120]
            result[safe_key] = (
                "[REDACTED]" if _SENSITIVE_DIAGNOSTIC_KEY.search(safe_key)
                else _redact_diagnostic_value(item, depth=depth + 1)
            )
        return result
    if isinstance(value, list):
        return [
            _redact_diagnostic_value(item, depth=depth + 1)
            for item in value[:64]
        ] + (["[TRUNCATED]"] if len(value) > 64 else [])
    if isinstance(value, str):
        safe = _DIAGNOSTIC_SECRET.sub("[REDACTED]", value)
        return safe[:500] + ("[TRUNCATED]" if len(safe) > 500 else "")
    if value is None or type(value) in {bool, int, float}:
        return value
    return f"[{type(value).__name__}]"


def _request_diagnostic_excerpt(response_text: Any) -> str:
    if isinstance(response_text, str):
        try:
            parsed = json.loads(response_text)
        except (ValueError, TypeError):
            excerpt = _DIAGNOSTIC_SECRET.sub("[REDACTED]", response_text[:MAX_REQUEST_DIAGNOSTIC_CHARS])
        else:
            excerpt = json.dumps(
                _redact_diagnostic_value(parsed), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
    else:
        excerpt = json.dumps(_redact_diagnostic_value(response_text), ensure_ascii=False, default=str)
    if len(excerpt) > MAX_REQUEST_DIAGNOSTIC_CHARS:
        excerpt = excerpt[:MAX_REQUEST_DIAGNOSTIC_CHARS] + "[TRUNCATED]"
    return excerpt


def _invalid_response_fingerprint(response_text: Any) -> str:
    if isinstance(response_text, str):
        try:
            normalized = json.dumps(json.loads(response_text), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (ValueError, TypeError):
            normalized = response_text.strip()
    else:
        normalized = json.dumps(response_text, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _decode_bytes(raw: bytes) -> tuple[str, str]:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        attempts = ("utf-16",)
    else:
        even_zeros = raw[0::2].count(0)
        odd_zeros = raw[1::2].count(0)
        if max(even_zeros, odd_zeros) > max(2, len(raw) // 8):
            attempts = ("utf-16-be", "cp1252") if even_zeros > odd_zeros else ("utf-16-le", "cp1252")
        else:
            attempts = ("utf-8-sig", "cp1252")
    for encoding in attempts:
        try:
            return raw.decode(encoding), encoding
        except UnicodeError:
            continue
    raise DataAccessError("text encoding is unsupported; expected UTF-8, UTF-16, or Windows-1252")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_cell(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    result = str(value)
    return None if result == "" else result


def _unique_headers(raw_headers: list[Any]) -> list[str]:
    headers: list[str] = []
    used: set[str] = set()
    for index, raw in enumerate(raw_headers, start=1):
        base = str(raw).strip() if raw is not None else ""
        base = base or f"column_{index}"
        name = base
        suffix = 2
        while name.casefold() in used:
            name = f"{base}__{suffix}"
            suffix += 1
        headers.append(name)
        used.add(name.casefold())
    return headers


class _HTMLTableParser(HTMLParser):
    """Extract static table rows without executing scripts or fetching resources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, bool]]]] = []
        self._table_depth = 0
        self._rows: list[list[tuple[str, bool]]] | None = None
        self._row: list[tuple[str, bool]] | None = None
        self._cell_kind: bool | None = None
        self._cell: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if tag == "table":
            if self._table_depth == 0:
                self._rows = []
            self._table_depth += 1
        elif self._table_depth and tag == "tr":
            self._row = []
        elif self._row is not None and tag in {"td", "th"}:
            self._cell_kind = tag == "th"
            self._cell = []
        elif self._cell_kind is not None and tag in {"br", "p", "div"}:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._cell_kind is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in {"td", "th"} and self._cell_kind is not None and self._row is not None:
            self._row.append((" ".join("".join(self._cell).split()), self._cell_kind))
            self._cell_kind = None
            self._cell = []
        elif tag == "tr" and self._row is not None and self._rows is not None:
            if self._row:
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None


class DatasetToolbox:
    """Parse complete allowlisted files and expose safe, structured operations."""

    def __init__(self, dataset_root: str | Path, permitted_paths: list[Path],
                 *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, task_deadline: float | None = None):
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise DataAccessError("timeout_seconds must be a positive integer")
        self.root = Path(dataset_root).resolve(strict=True)
        if not self.root.is_dir():
            raise DataAccessError("dataset root is not a directory")
        if not permitted_paths or len(permitted_paths) > MAX_DATA_FILES:
            raise DataAccessError(f"allowlist must contain between 1 and {MAX_DATA_FILES} files")
        self.deadline = float("inf")
        self.timeout_seconds = timeout_seconds
        self.task_deadline = task_deadline
        self.operation_seconds: list[float] = []
        self._temp = tempfile.TemporaryDirectory(prefix="kramabench-data-")
        self._db = sqlite3.connect(str(Path(self._temp.name) / "dataset.sqlite3"))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=OFF")
        self._db.execute("PRAGMA synchronous=OFF")
        self.files: dict[str, dict[str, Any]] = {}
        self.tables: dict[tuple[str, str], dict[str, Any]] = {}
        self.text_pages: dict[str, list[str]] = {}
        self.json_documents: dict[str, Any] = {}
        self.last_successful_file: str | None = None
        self.total_bytes = 0
        try:
            for index, candidate in enumerate(permitted_paths):
                self._check_deadline()
                path = Path(candidate).resolve(strict=True)
                try:
                    relative = path.relative_to(self.root).as_posix()
                except ValueError as exc:
                    raise DataAccessError("an allowlisted path resolves outside the dataset root") from exc
                if not path.is_file():
                    raise DataAccessError(f"allowlisted input is not a regular file: {relative}")
                size = path.stat().st_size
                if size > MAX_SINGLE_FILE_BYTES:
                    raise DataAccessError(f"input exceeds {MAX_SINGLE_FILE_BYTES} byte per-file limit: {relative}")
                self.total_bytes += size
                if self.total_bytes > MAX_TOTAL_FILE_BYTES:
                    raise DataAccessError(f"allowlist exceeds {MAX_TOTAL_FILE_BYTES} total input bytes")
                descriptor = {
                    "path": relative,
                    "sha256": _file_sha256(path),
                    "size_bytes": size,
                    "format": path.suffix.lower().lstrip("."),
                    "encoding": None,
                    "tables": [],
                    "page_count": None,
                    "notes": [],
                }
                self.files[relative] = descriptor
                with self._operation_deadline():
                    self._load_file(path, descriptor, index)
            self._db.commit()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        db = getattr(self, "_db", None)
        if db is not None:
            db.close()
            self._db = None
        temporary = getattr(self, "_temp", None)
        if temporary is not None:
            temporary.cleanup()
            self._temp = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _check_deadline(self) -> None:
        if self.task_deadline is not None and time.monotonic() > self.task_deadline:
            raise TaskWallClockTimeout("per-task wall-clock cap exceeded")
        if time.monotonic() > self.deadline:
            raise DataOperationTimeout("data processing timed out; no partial result was returned")

    @contextmanager
    def _operation_deadline(self):
        previous = self.deadline
        started = time.monotonic()
        self.deadline = started + self.timeout_seconds
        try:
            yield
            self._check_deadline()
        finally:
            self.operation_seconds.append(round(time.monotonic() - started, 6))
            self.deadline = previous

    def _load_file(self, path: Path, descriptor: dict[str, Any], index: int) -> None:
        suffix = path.suffix.casefold()
        relative = descriptor["path"]
        if suffix in {".csv", ".tsv"}:
            self._load_delimited(path, descriptor, f"table_{index}", "\t" if suffix == ".tsv" else ",")
        elif suffix == ".json":
            self._load_json(path, descriptor, f"table_{index}")
        elif suffix == ".jsonl":
            self._load_jsonl(path, descriptor, f"table_{index}")
        elif suffix in {".html", ".htm"}:
            self._load_html(path, descriptor)
        elif suffix in {".txt", ".text", ".md", ".xml", ".sql", ".log", ".tle", ".dat"}:
            text, encoding = _decode_bytes(path.read_bytes())
            if suffix == ".dat" and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
                raise DataAccessError(f"binary DAT file is not supported: {relative}")
            descriptor["encoding"] = encoding
            self.text_pages[relative] = [text]
        elif suffix == ".xlsx":
            self._load_xlsx(path, descriptor, index)
        elif suffix == ".pdf":
            self._load_pdf(path, descriptor)
        else:
            raise DataAccessError(f"unsupported input format for {relative}: {suffix or '(no extension)'}")

    def _create_table(self, file_path: str, table_name: str, headers: list[str],
                      rows, table_index: int) -> dict[str, Any]:
        self._check_deadline()
        sql_name = f"t{table_index}"
        column_names = [f"c{index}" for index in range(len(headers))]
        definitions = [f'"{name}" TEXT' for name in column_names]
        self._db.execute(f'CREATE TABLE "{sql_name}" (_row INTEGER PRIMARY KEY, {", ".join(definitions)})')
        placeholders = ",".join("?" for _ in column_names)
        insert_sql = f'INSERT INTO "{sql_name}" ({", ".join(column_names)}) VALUES ({placeholders})'
        count = 0
        numeric_encodings: dict[tuple[str, str], int] = {}
        ambiguous_encodings: dict[str, int] = {}
        try:
            for raw_row in rows:
                if count % 512 == 0:
                    self._check_deadline()
                values = list(raw_row)
                if len(values) > len(headers):
                    raise DataAccessError(
                        f"malformed table {table_name!r} in {file_path}: row {count + 2} has extra columns"
                    )
                values.extend([None] * (len(headers) - len(values)))
                if self.files[file_path]["format"] in {"csv", "tsv"}:
                    for column, value in zip(headers, values):
                        if not isinstance(value, str) or not _looks_numeric_encoded(value):
                            continue
                        try:
                            _, transformations = _parse_numeric_value(value)
                        except ValueError:
                            ambiguous_encodings[column] = ambiguous_encodings.get(column, 0) + 1
                        except InvalidOperation:
                            continue
                        else:
                            for transformation in transformations:
                                key = (column, transformation)
                                numeric_encodings[key] = numeric_encodings.get(key, 0) + 1
                self._db.execute(insert_sql, [_as_cell(value) for value in values])
                count += 1
        except sqlite3.Error as exc:
            raise DataAccessError(f"could not index table {table_name!r} in {file_path}: {exc}") from exc
        table = {
            "file": file_path,
            "name": table_name,
            "sql_name": sql_name,
            "columns": headers,
            "column_names": column_names,
            "row_count": count,
        }
        self.tables[(file_path, table_name)] = table
        self.files[file_path]["tables"].append({
            "name": table_name,
            "row_count": count,
            "columns": list(headers),
        })
        for (column, transformation), value_count in sorted(numeric_encodings.items()):
            self.files[file_path]["notes"].append(
                f"Normalized {value_count} delimited numeric value(s) in column {column!r}: {transformation}."
            )
        for column, value_count in sorted(ambiguous_encodings.items()):
            self.files[file_path]["notes"].append(
                f"Found {value_count} ambiguous numeric-looking value(s) in column {column!r}; "
                "numeric aggregation will require an unambiguous encoding."
            )
        return table

    def _load_delimited(self, path: Path, descriptor: dict[str, Any], table_name: str, delimiter: str) -> None:
        text, encoding = _decode_bytes(path.read_bytes())
        descriptor["encoding"] = encoding
        if delimiter == ",":
            # Some source exports use a CSV extension for tab-separated data.
            sample = []
            for line in io.StringIO(text, newline=""):
                if line.strip():
                    sample.append(line)
                    if len(sample) == 3:
                        break
            if len(sample) == 3 and "\t" in sample[0] and "," not in sample[0]:
                widths = {len(row) for row in csv.reader(sample, delimiter="\t")}
                if len(widths) == 1 and next(iter(widths)) > 1:
                    delimiter = "\t"
                    descriptor["notes"].append("Detected tab-delimited content in a CSV file.")
        try:
            def records():
                reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
                for row in reader:
                    yield reader.line_num, row

            def is_blank(row: list[str]) -> bool:
                return not row or all(not cell.strip() for cell in row)

            width_counts: Counter[int] = Counter()
            for _, row in records():
                if not is_blank(row):
                    width_counts[len(row)] += 1
            if not width_counts:
                raise DataAccessError(f"empty delimited file: {descriptor['path']}")
            dominant_width = max(width_counts, key=lambda width: (width_counts[width], width))

            def is_metadata_record(row: list[str]) -> bool:
                return (
                    len(row) == 1
                    and re.fullmatch(r"\s*[A-Za-z][A-Za-z0-9 _.-]*\s*:\s*\S.*", row[0]) is not None
                )

            def has_header_labels(row: list[str]) -> bool:
                labels = [cell.strip() for cell in row]
                return (
                    bool(labels)
                    and all(labels)
                    and len({label.casefold() for label in labels}) == len(labels)
                    and any(re.search(r"[A-Za-z]", label) for label in labels)
                    and not (len(labels) == 1 and is_metadata_record(row))
                )

            def looks_like_header(row: list[str]) -> bool:
                return len(row) == dominant_width and has_header_labels(row)

            header_record = None
            first_header_candidate: tuple[int, list[str]] | None = None
            skipped_records: list[tuple[int, list[str]]] = []
            first_dominant: tuple[int, list[str]] | None = None
            for line_number, row in records():
                if is_blank(row):
                    continue
                if len(row) == dominant_width and first_dominant is None:
                    first_dominant = (line_number, row)
                if has_header_labels(row) and first_header_candidate is None:
                    first_header_candidate = (line_number, row)
                if looks_like_header(row):
                    header_record = (line_number, row)
                    break
                skipped_records.append((line_number, row))
            if header_record is None:
                # Preserve support for headers with blank/duplicate labels by
                # falling back to the first row at the dominant width.
                header_record = first_header_candidate or first_dominant
                if header_record is None:
                    raise DataAccessError(f"could not infer a delimited header in {descriptor['path']}")

            if skipped_records:
                header_line = header_record[0]
                if all(is_metadata_record(row) for _, row in skipped_records):
                    metadata_keys = ", ".join(
                        row[0].split(":", 1)[0].strip() for _, row in skipped_records
                    )
                    skipped_lines = ", ".join(str(line) for line, _ in skipped_records)
                    descriptor["notes"].append(
                        f"Skipped {len(skipped_records)} leading key:value metadata row(s) "
                        f"({metadata_keys}) at physical line(s) {skipped_lines} before the table "
                        f"header at physical line {header_line}."
                    )
                else:
                    skipped_lines = ", ".join(str(line) for line, _ in skipped_records[:32])
                    suffix = "; additional line numbers omitted" if len(skipped_records) > 32 else ""
                    descriptor["notes"].append(
                        f"Inferred the table header at physical line {header_line} from populated unique labels "
                        f"(header width {len(header_record[1])}; dominant data width {dominant_width}); skipped "
                        f"{len(skipped_records)} leading pre-header row(s) at physical line(s) "
                        f"{skipped_lines}{suffix}."
                    )

            trimmed_headers = [cell.strip() for cell in header_record[1]]
            trimmed_header_count = sum(
                original != trimmed
                for original, trimmed in zip(header_record[1], trimmed_headers)
            )
            headers = _unique_headers(trimmed_headers)
            if trimmed_header_count:
                descriptor["notes"].append(
                    f"Trimmed whitespace from {trimmed_header_count} delimited header name(s) "
                    f"at physical line {header_record[0]}."
                )

            normalized_header = [cell.strip().casefold() for cell in header_record[1]]
            repeated_header_lines: list[int] = []
            trailing_record_lines: list[int] = []

            def is_repeated_header(row: list[str]) -> bool:
                if len(row) != len(normalized_header):
                    return False
                matches = sum(
                    cell.strip().casefold() == header
                    for cell, header in zip(row, normalized_header)
                )
                # Some source files repeat section headers while replacing
                # the first column with a section title. Require at least
                # three quarters of fields (and at least two) to match.
                return matches >= max(2, (3 * len(normalized_header) + 3) // 4)

            def rows():
                table_ended = False
                data_started = False
                for line_number, row in records():
                    if line_number <= header_record[0]:
                        continue
                    if is_blank(row):
                        if data_started and not table_ended:
                            table_ended = True
                        continue
                    if len(row) > len(headers):
                        raise DataAccessError(
                            f"malformed delimited file {descriptor['path']}: line {line_number} has extra columns"
                        )
                    if is_repeated_header(row):
                        repeated_header_lines.append(line_number)
                        table_ended = False
                        data_started = True
                        continue
                    if table_ended:
                        trailing_record_lines.append(line_number)
                        continue
                    data_started = True
                    yield [cell if cell.strip() != "" else None for cell in row]

            table_index = len(self.tables)
            self._create_table(descriptor["path"], table_name, headers, rows(), table_index)
            if repeated_header_lines:
                shown = ", ".join(str(line) for line in repeated_header_lines[:32])
                suffix = "; additional line numbers omitted" if len(repeated_header_lines) > 32 else ""
                descriptor["notes"].append(
                    f"Skipped {len(repeated_header_lines)} repeated header-like row(s) matching most inferred "
                    f"column names at physical line(s) {shown}{suffix}."
                )
            if trailing_record_lines:
                shown = ", ".join(str(line) for line in trailing_record_lines[:32])
                suffix = "; additional line numbers omitted" if len(trailing_record_lines) > 32 else ""
                descriptor["notes"].append(
                    f"Skipped {len(trailing_record_lines)} non-table record(s) between detected table sections "
                    f"or after the final section at physical line(s) {shown}{suffix}."
                )
        except csv.Error as exc:
            raise DataAccessError(f"malformed delimited file {descriptor['path']}: {exc}") from exc

    def _load_json(self, path: Path, descriptor: dict[str, Any], table_prefix: str) -> None:
        text, encoding = _decode_bytes(path.read_bytes())
        descriptor["encoding"] = encoding
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataAccessError(f"malformed JSON in {descriptor['path']} at line {exc.lineno}, column {exc.colno}") from exc
        self.json_documents[descriptor["path"]] = document
        self._extract_json_tables(descriptor["path"], document, table_prefix)

    def _load_html(self, path: Path, descriptor: dict[str, Any]) -> None:
        text, encoding = _decode_bytes(path.read_bytes())
        descriptor["encoding"] = encoding
        self.text_pages[descriptor["path"]] = [text]
        parser = _HTMLTableParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:
            raise DataAccessError(f"malformed HTML in {descriptor['path']}: {type(exc).__name__}") from exc
        for table_index, raw_table in enumerate(parser.tables):
            first = raw_table[0]
            width = max(len(row) for row in raw_table)
            if any(is_header for _, is_header in first):
                raw_headers = [value for value, _ in first]
                raw_headers.extend(f"column_{position + 1}" for position in range(len(raw_headers), width))
                headers = _unique_headers(raw_headers)
                rows = raw_table[1:]
            else:
                headers = [f"column_{position + 1}" for position in range(width)]
                rows = raw_table
            values = ([value for value, _ in row] for row in rows)
            self._create_table(
                descriptor["path"], f"html_table_{table_index}", headers, values, len(self.tables)
            )
        if parser.tables:
            descriptor["notes"].append(
                "Static HTML tables are parsed as tabular inputs; scripts and remote resources are not executed or loaded. Ragged rows are padded with missing cells; row/column spans are not expanded."
            )
        else:
            descriptor["notes"].append("HTML text is searchable/readable; no static table was found.")

    def _load_jsonl(self, path: Path, descriptor: dict[str, Any], table_name: str) -> None:
        text, encoding = _decode_bytes(path.read_bytes())
        descriptor["encoding"] = encoding
        values = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            self._check_deadline()
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataAccessError(f"malformed JSONL in {descriptor['path']} at line {line_number}") from exc
            values.append(item)
        self.json_documents[descriptor["path"]] = values
        rows = values if values and all(isinstance(item, dict) for item in values) else [{"value": item} for item in values]
        headers = _unique_headers(list(OrderedDict.fromkeys(key for row in rows for key in row))) if rows else ["value"]
        data = ([row.get(header) for header in headers] for row in rows)
        self._create_table(descriptor["path"], table_name, headers, data, len(self.tables))

    def _extract_json_tables(self, file_path: str, document: Any, prefix: str) -> None:
        found = 0

        def visit(value: Any, path: str, depth: int):
            nonlocal found
            if depth > 8:
                return
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                keys = list(OrderedDict.fromkeys(key for row in value for key in row))
                headers = _unique_headers(keys)
                rows = ([row.get(key) for key in keys] for row in value)
                table_name = path or "root"
                self._create_table(file_path, table_name, headers, rows, len(self.tables))
                found += 1
                return
            if isinstance(value, dict):
                for key, nested in value.items():
                    if isinstance(nested, (dict, list)):
                        visit(nested, f"{path}/{key}" if path else str(key), depth + 1)

        visit(document, prefix, 0)

    def _load_xlsx(self, path: Path, descriptor: dict[str, Any], index: int) -> None:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise DataAccessError("XLSX support is unavailable: install the declared openpyxl dependency") from exc
        workbook = None
        formula_book = None
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
            formula_book = load_workbook(path, read_only=True, data_only=False)
            found_formula = False
            for sheet_index, sheet in enumerate(workbook.worksheets):
                self._check_deadline()
                formula_sheet = formula_book[sheet.title]
                iterator = sheet.iter_rows(values_only=True)
                first = next(iterator, None)
                if first is None:
                    continue
                headers = _unique_headers(list(first))

                def rows(source=iterator, formula_source=formula_sheet.iter_rows(), width=len(headers)):
                    nonlocal found_formula
                    for row_index, row in enumerate(source, start=2):
                        if row_index % 512 == 0:
                            self._check_deadline()
                        formulas = next(formula_source, ())
                        values = list(row)
                        for column_index, value in enumerate(values):
                            if value is None and column_index < len(formulas):
                                formula = formulas[column_index].value
                                if isinstance(formula, str) and formula.startswith("="):
                                    values[column_index] = formula
                                    found_formula = True
                        if any(value is not None for value in values):
                            yield values[:width]

                self._create_table(
                    descriptor["path"], sheet.title, headers, rows(), len(self.tables)
                )
            if found_formula:
                descriptor["notes"].append(
                    "XLSX formulas with cached values expose the cached value; formulas without cached values are exposed as formula text and are not recalculated."
                )
        except DataAccessError:
            raise
        except Exception as exc:
            raise DataAccessError(f"malformed or unreadable XLSX file {descriptor['path']}: {type(exc).__name__}") from exc
        finally:
            if workbook is not None:
                workbook.close()
            if formula_book is not None:
                formula_book.close()

    def _load_pdf(self, path: Path, descriptor: dict[str, Any]) -> None:
        try:
            import pdfplumber
        except ImportError as exc:
            raise DataAccessError("PDF support is unavailable: install the declared pdfplumber dependency") from exc
        try:
            with pdfplumber.open(path) as pdf:
                pages = []
                for page in pdf.pages:
                    self._check_deadline()
                    pages.append(page.extract_text() or "")
        except DataAccessError:
            raise
        except Exception as exc:
            raise DataAccessError(f"malformed or unreadable PDF file {descriptor['path']}: {type(exc).__name__}") from exc
        if not any(page.strip() for page in pages):
            raise DataAccessError(f"PDF has no extractable text (OCR is not implemented): {descriptor['path']}")
        descriptor["page_count"] = len(pages)
        descriptor["notes"].append("PDF page text is extracted; scanned images require OCR, which is not implemented.")
        self.text_pages[descriptor["path"]] = pages

    def catalog(self) -> dict[str, Any]:
        return {"files": [self.files[path] for path in sorted(self.files)]}

    def precomputed_profiles(self, *, sample_rows: int = 3, low_cardinality_limit: int = 20,
                             max_chars_per_file: int = 6000) -> list[dict[str, Any]]:
        """Build bounded, trusted per-file profiles without consuming model tool calls."""
        if type(sample_rows) is not int or not 1 <= sample_rows <= 10:
            raise ValueError("sample_rows must be between 1 and 10")
        if type(low_cardinality_limit) is not int or not 2 <= low_cardinality_limit <= 100:
            raise ValueError("low_cardinality_limit must be between 2 and 100")
        if type(max_chars_per_file) is not int or max_chars_per_file < 500:
            raise ValueError("max_chars_per_file must be at least 500")

        profiles = []
        for path, descriptor in sorted(self.files.items()):
            profile: dict[str, Any] = {
                "file": path,
                "sha256": descriptor["sha256"],
                "format": descriptor["format"],
                "parser_notes": list(descriptor.get("notes", [])),
                "tables": [],
            }
            for table_info in descriptor.get("tables", []):
                table = self._table(path, table_info["name"])
                self._db.set_progress_handler(lambda: 1 if time.monotonic() > self.deadline else 0, 1_000)
                try:
                    with self._operation_deadline():
                        columns = list(table["columns"])
                        rows = self._preview(table, None, 0, sample_rows)["rows"]
                        low_cardinality = {}
                        sample_only_columns = []
                        sql_table = table["sql_name"]
                        for column in columns:
                            self._check_deadline()
                            sql_column = self._column(table, column)
                            distinct_count = self._db.execute(
                                f'SELECT COUNT(*) FROM (SELECT "{sql_column}" FROM "{sql_table}" '
                                f'GROUP BY "{sql_column}" LIMIT ?)',
                                (low_cardinality_limit + 1,),
                            ).fetchone()[0]
                            if distinct_count <= low_cardinality_limit:
                                counts = self._db.execute(
                                    f'SELECT "{sql_column}", COUNT(*) FROM "{sql_table}" '
                                    f'GROUP BY "{sql_column}" ORDER BY COUNT(*) DESC, "{sql_column}"',
                                ).fetchall()
                                low_cardinality[column] = {
                                    "coverage": "exhaustive",
                                    "counts": [{"value": row[0], "count": row[1]} for row in counts],
                                }
                            else:
                                sample_only_columns.append(column)
                        profile["tables"].append({
                            "table": table["name"],
                            "row_count": table["row_count"],
                            "schema": [{"column": col, "dtype": "text (normalized parser storage)"}
                                       for col in columns],
                            "sample_rows": {"coverage": "sample", "rows": rows},
                            "low_cardinality_counts": low_cardinality,
                            "other_columns": {"coverage": "sample only", "columns": sample_only_columns},
                        })
                except sqlite3.OperationalError as exc:
                    if "interrupted" in str(exc).casefold():
                        raise DataOperationTimeout(
                            "precomputed profile operation timed out; no partial result was returned"
                        ) from exc
                    raise
                finally:
                    self._db.set_progress_handler(None, 0)
            if not descriptor.get("tables") and path in self.text_pages:
                profile["text_sample"] = {
                    "coverage": "sample",
                    "text": "\n".join(self.text_pages[path])[: min(1200, max_chars_per_file // 2)],
                }
            encoded = json.dumps(profile, ensure_ascii=False, default=str)
            if len(encoded) > max_chars_per_file:
                for table_profile in profile["tables"]:
                    table_profile["sample_rows"]["rows"] = table_profile["sample_rows"]["rows"][:1]
                if "text_sample" in profile:
                    profile["text_sample"]["text"] = profile["text_sample"]["text"][:200]
                encoded = json.dumps(profile, ensure_ascii=False, default=str)
            if len(encoded) > max_chars_per_file:
                raise DataAccessError(f"precomputed profile for {path!r} exceeds its bounded output size")
            profiles.append(profile)
        return profiles

    def _file(self, name: str | None) -> dict[str, Any]:
        if not isinstance(name, str) or name not in self.files:
            available = [item["path"] for item in self.catalog()["files"]]
            raise DataToolFileSelectionError(
                f"invalid file path {name!r}; it must exactly match an allowlisted path. "
                f"Available paths: {available}"
            )
        return self.files[name]

    def _single_file(self, operation: DataToolRequest) -> dict[str, Any]:
        """Resolve one explicit file identifier without broadening the allowlist."""
        if operation.file is not None and operation.files:
            raise DataToolFileSelectionError("single-file actions accept `file` or `files`, not both")
        if operation.file is not None:
            return self._file(operation.file)
        if not operation.files:
            example_path = self.last_successful_file
            if example_path is None and len(self.files) == 1:
                example_path = next(iter(self.files))
            if example_path is None:
                example_path = next(iter(self.files))
            example = json.dumps({"action": operation.action, "file": example_path}, ensure_ascii=False)
            if self.last_successful_file is not None:
                message = (
                    f"`file` is missing; include the last successfully used file. Corrected example: {example}"
                )
            else:
                message = (
                    f"`file` is missing; set it to an exact path from `allowlisted_files`. "
                    f"Corrected example: {example}"
                )
            raise DataToolFileSelectionError(message)
        if len(operation.files) != 1:
            raise DataToolFileSelectionError(
                "this action requires exactly one allowlisted path; use `aggregate` for multiple files"
            )
        return self._file(operation.files[0])

    def _table(self, file_path: str, table_name: str | None) -> dict[str, Any]:
        candidates = [table for (path, _), table in self.tables.items() if path == file_path]
        if not candidates:
            raise DataAccessError(f"file has no tabular content; use read_text/search_text/json_get: {file_path}")
        if table_name is None and len(candidates) == 1:
            return candidates[0]
        for table in candidates:
            if table["name"] == table_name:
                return table
        raise DataAccessError(f"table must name one of the available tables in {file_path}")

    def _column(self, table: dict[str, Any], name: str) -> str:
        try:
            return table["column_names"][table["columns"].index(name)]
        except (ValueError, KeyError) as exc:
            raise DataAccessError(f"unknown column {name!r}; available columns: {table['columns']}") from exc

    def _rows(self, table: dict[str, Any]):
        columns = table["column_names"]
        query = f'SELECT {", ".join(f"\"{name}\"" for name in columns)} FROM "{table["sql_name"]}" ORDER BY _row'
        for row in self._db.execute(query):
            yield {name: row[index] for index, name in enumerate(table["columns"])}

    def _match(self, row: dict[str, Any], filters: list[DataFilter]) -> tuple[bool, list[str]]:
        invalid_numbers = []
        for condition in filters:
            if condition.column not in row:
                raise DataAccessError(f"unknown filter column {condition.column!r}")
            actual = row[condition.column]
            expected = condition.value
            operator = condition.operator
            if operator == "is_null":
                matched = actual is None
            elif operator == "not_null":
                matched = actual is not None
            elif operator in {"eq", "ne", "contains"}:
                left, right = "" if actual is None else str(actual), "" if expected is None else str(expected)
                matched = left == right if operator == "eq" else left != right if operator == "ne" else right.casefold() in left.casefold()
            else:
                if actual is None:
                    matched = False
                else:
                    try:
                        left_num, right_num = _decimal(actual), _decimal(expected)
                    except (InvalidOperation, ValueError):
                        invalid_numbers.append(condition.column)
                        matched = False
                    else:
                        matched = {
                            "gt": left_num > right_num,
                            "gte": left_num >= right_num,
                            "lt": left_num < right_num,
                            "lte": left_num <= right_num,
                        }[operator]
            if not matched:
                return False, invalid_numbers
        return True, invalid_numbers

    def execute(self, request: DataToolRequest | dict[str, Any]) -> dict[str, Any]:
        with self._operation_deadline():
            result = self._execute(request)
            successful_files = result.get("files")
            if not isinstance(successful_files, list):
                successful_files = [result["file"]] if isinstance(result.get("file"), str) else []
            if len(successful_files) == 1 and successful_files[0] in self.files:
                self.last_successful_file = successful_files[0]
            return result

    def _execute(self, request: DataToolRequest | dict[str, Any]) -> dict[str, Any]:
        self._check_deadline()
        operation = request if isinstance(request, DataToolRequest) else DataToolRequest.model_validate(request)
        action = operation.action
        if action == "catalog":
            result = self.catalog()
        elif action == "done":
            result = {"summary": operation.summary or ""}
        elif action == "aggregate" and operation.files:
            if operation.file is not None:
                raise DataAccessError("aggregate accepts either file or files, not both")
            requested_files = list(dict.fromkeys(operation.files))
            if len(requested_files) != len(operation.files):
                raise DataAccessError("aggregate files contains duplicate paths")
            tables = [self._table(self._file(name)["path"], operation.table) for name in requested_files]
            result = self._aggregate_many(tables, operation.filters, operation.group_by, operation.measures)
        else:
            file_info = self._single_file(operation)
            path = file_info["path"]
            if action == "read_text":
                result = self._read_text(path, operation.offset, operation.max_chars)
            elif action == "search_text":
                result = self._search_text(path, operation.text, operation.offset, operation.limit)
            elif action == "json_get":
                result = self._json_get(path, operation.path, operation.offset, operation.limit)
            else:
                table = self._table(path, operation.table)
                if action == "profile":
                    result = self._profile(table)
                elif action == "preview":
                    result = self._preview(table, operation.columns, operation.offset, operation.limit)
                elif action == "filter":
                    result = self._filter(table, operation.filters, operation.columns, operation.offset, operation.limit)
                elif action == "aggregate":
                    result = self._aggregate(table, operation.filters, operation.group_by, operation.measures)
                else:
                    raise DataAccessError(f"unsupported data action: {action}")
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) > MAX_TOOL_OUTPUT_CHARS:
            raise DataToolOutputTooLarge(
                f"tool result exceeds {MAX_TOOL_OUTPUT_CHARS} characters; request fewer rows, columns, or a narrower JSON path"
            )
        return result

    def _profile(self, table: dict[str, Any]) -> dict[str, Any]:
        self._db.set_progress_handler(lambda: 1 if time.monotonic() > self.deadline else 0, 1_000)
        try:
            output = []
            for column in table["columns"]:
                self._check_deadline()
                sql_column = self._column(table, column)
                query = f'SELECT COUNT(*), COUNT("{sql_column}") FROM "{table["sql_name"]}"'
                total, populated = self._db.execute(query).fetchone()
                sample = self._db.execute(
                    f'SELECT "{sql_column}" FROM "{table["sql_name"]}" WHERE "{sql_column}" IS NOT NULL LIMIT 5'
                ).fetchall()
                output.append({
                    "column": column,
                    "nonnull_count": populated,
                    "missing_count": total - populated,
                    "sample_values": [row[0] for row in sample],
                })
            return {
                "file": table["file"], "table": table["name"], "row_count": table["row_count"],
                "sample_values_note": "Each column has up to five non-null examples; these are not exhaustive or necessarily distinct.",
                "columns": output,
            }
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).casefold():
                raise DataAccessError("profile operation timed out; no partial result was returned") from exc
            raise
        finally:
            self._db.set_progress_handler(None, 0)

    def _preview(self, table: dict[str, Any], columns: list[str] | None, offset: int, limit: int) -> dict[str, Any]:
        selected = table["columns"] if columns is None else columns
        if not selected:
            raise DataAccessError("preview columns cannot be empty")
        sql_columns = [self._column(table, name) for name in selected]
        query = f'SELECT {", ".join(f"\"{name}\"" for name in sql_columns)} FROM "{table["sql_name"]}" ORDER BY _row LIMIT ? OFFSET ?'
        records = [dict(zip(selected, tuple(row))) for row in self._db.execute(query, (limit, offset))]
        total = table["row_count"]
        return {
            "file": table["file"], "table": table["name"], "columns": selected,
            "offset": offset, "total_rows": total, "rows": records,
            "has_more": offset + len(records) < total,
            "next_offset": offset + len(records) if offset + len(records) < total else None,
        }

    def _filter(self, table: dict[str, Any], filters: list[DataFilter], columns: list[str] | None,
                offset: int, limit: int) -> dict[str, Any]:
        selected = table["columns"] if columns is None else columns
        if not selected:
            raise DataAccessError("filter columns cannot be empty")
        for column in selected:
            self._column(table, column)
        for condition in filters:
            self._column(table, condition.column)
        total_matches = 0
        invalid_counts: dict[str, int] = {}
        results = []
        for row in self._rows(table):
            if total_matches % 512 == 0:
                self._check_deadline()
            matched, invalid = self._match(row, filters)
            for name in invalid:
                invalid_counts[name] = invalid_counts.get(name, 0) + 1
            if not matched:
                continue
            if total_matches >= offset and len(results) < limit:
                results.append({name: row[name] for name in selected})
            total_matches += 1
        result = {
            "file": table["file"], "table": table["name"], "offset": offset,
            "total_matches": total_matches, "rows": results,
            "has_more": offset + len(results) < total_matches,
            "next_offset": offset + len(results) if offset + len(results) < total_matches else None,
            "non_numeric_values_skipped_by_filter": invalid_counts,
        }
        if total_matches == 0:
            diagnostics, omitted = self._empty_filter_diagnostics([table], filters)
            result["empty_filter_diagnostics"] = diagnostics
            result["empty_result_diagnostic"] = self._empty_filter_message(diagnostics, omitted)
        return result

    def _empty_filter_diagnostics(self, tables: list[dict[str, Any]], filters: list[DataFilter]):
        """Return a small, deterministic category preview for each empty predicate."""
        diagnostics = []
        omitted = 0
        for table in tables:
            for condition in filters:
                if len(diagnostics) >= MAX_EMPTY_FILTER_DIAGNOSTICS:
                    omitted += 1
                    continue
                sql_column = self._column(table, condition.column)
                self._check_deadline()
                self._db.set_progress_handler(
                    lambda: 1 if time.monotonic() > self.deadline else 0, 1_000
                )
                try:
                    rows = self._db.execute(
                        f'SELECT DISTINCT "{sql_column}" FROM "{table["sql_name"]}" '
                        f'ORDER BY "{sql_column}" LIMIT ?',
                        (MAX_EMPTY_FILTER_VALUES + 1,),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    if "interrupted" in str(exc).casefold():
                        raise DataAccessError(
                            "empty-filter category preview timed out; no partial result was returned"
                        ) from exc
                    raise
                finally:
                    self._db.set_progress_handler(None, 0)

                def bounded_value(value):
                    if isinstance(value, str) and len(value) > MAX_EMPTY_FILTER_VALUE_CHARS:
                        return value[:MAX_EMPTY_FILTER_VALUE_CHARS] + "…"
                    return value

                attempted = bounded_value(condition.value)
                values = [bounded_value(row[0]) for row in rows[:MAX_EMPTY_FILTER_VALUES]]
                diagnostic = {
                    "file": table["file"],
                    "table": table["name"],
                    "column": condition.column,
                    "operator": condition.operator,
                    "attempted_value": attempted,
                    "observed_values": values,
                    "values_truncated": len(rows) > MAX_EMPTY_FILTER_VALUES,
                    "preview_note": "Bounded distinct-value preview; it may omit additional categories.",
                }
                diagnostic["hint"] = empty_filter_hint(diagnostic)
                diagnostics.append(diagnostic)
        return diagnostics, omitted

    @staticmethod
    def _empty_filter_message(diagnostics: list[dict[str, Any]], omitted: int = 0) -> str:
        descriptions = []
        for item in diagnostics:
            attempted = json.dumps(item["attempted_value"], ensure_ascii=False)
            observed = json.dumps(item["observed_values"], ensure_ascii=False, default=str)
            suffix = "; additional categories may be omitted" if item["values_truncated"] else ""
            descriptions.append(
                f'{item["column"]} {item["operator"]} {attempted}: observed values {observed}{suffix}'
            )
        if omitted:
            descriptions.append(f"{omitted} additional filter-column previews were omitted")
        details = "; ".join(descriptions) if descriptions else "no filter conditions were supplied"
        return (
            f"No rows matched the requested filter(s): {details}. This is not evidence of a zero count or rate, "
            "and it does not establish that an outcome category is absent. Determine whether the "
            "filter uses a different geographic level, the dataset already represents the requested "
            "geography, or the requested subset cannot be identified from the available columns."
        )

    def _aggregate(self, table: dict[str, Any], filters: list[DataFilter], group_by: list[str],
                   measures: list[DataMeasure]) -> dict[str, Any]:
        return self._aggregate_many([table], filters, group_by, measures)

    def _aggregate_many(self, tables: list[dict[str, Any]], filters: list[DataFilter], group_by: list[str],
                        measures: list[DataMeasure]) -> dict[str, Any]:
        if not tables:
            raise DataAccessError("aggregate requires at least one tabular file")
        expected_columns = set(tables[0]["columns"])
        if any(set(table["columns"]) != expected_columns for table in tables[1:]):
            raise DataAccessError("aggregate across files requires every table to have the same column names")
        if not measures:
            raise DataAccessError("aggregate must request at least one measure")
        if len(group_by) > 3:
            raise DataAccessError("aggregate supports at most three grouping columns")
        for table in tables:
            for column in group_by:
                self._column(table, column)
        specs = []
        for measure in measures:
            if measure.function != "count" or measure.column != "*":
                for table in tables:
                    self._column(table, measure.column)
            specs.append((measure, measure.name or f"{measure.function}_{measure.column}"))
        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        matched_count = 0
        for table in tables:
            for row in self._rows(table):
                if matched_count % 512 == 0:
                    self._check_deadline()
                matches, invalid_filter_columns = self._match(row, filters)
                if invalid_filter_columns:
                    column = invalid_filter_columns[0]
                    raise DataAccessError(f"numeric filter could not parse values in column {column!r}; use a text predicate or inspect the data")
                if not matches:
                    continue
                matched_count += 1
                key = tuple(row[column] for column in group_by)
                if key not in groups:
                    if len(groups) >= MAX_GROUPS:
                        raise DataAccessError(f"aggregate exceeds {MAX_GROUPS} groups; add filters or fewer grouping columns")
                    groups[key] = {
                        "values": {
                            name: {"count": 0, "sum": Decimal(0), "min": None, "max": None}
                            for _, name in specs
                        }
                    }
                state = groups[key]
                for measure, name in specs:
                    accumulator = state["values"][name]
                    if measure.function == "count" and measure.column == "*":
                        accumulator["count"] += 1
                        continue
                    value = row[measure.column]
                    if value is None:
                        continue
                    if measure.function in {"sum", "mean", "min", "max"}:
                        try:
                            parsed = _decimal(value)
                        except ValueError as exc:
                            raise DataAccessError(
                                f"numeric aggregate found ambiguous value {value!r} in column "
                                f"{measure.column!r}: {exc}"
                            ) from None
                        except InvalidOperation:
                            raise DataAccessError(
                                f"numeric aggregate cannot parse value {value!r} in column {measure.column!r}"
                            ) from None
                        accumulator["count"] += 1
                        accumulator["sum"] += parsed
                        accumulator["min"] = parsed if accumulator["min"] is None else min(accumulator["min"], parsed)
                        accumulator["max"] = parsed if accumulator["max"] is None else max(accumulator["max"], parsed)
                    else:
                        accumulator["count"] += 1
        output = []
        for key, state in groups.items():
            row = {name: value for name, value in zip(group_by, key)}
            for measure, name in specs:
                accumulator = state["values"][name]
                if measure.function == "count":
                    value = accumulator["count"]
                elif not accumulator["count"]:
                    value = None
                elif measure.function == "sum":
                    value = str(accumulator["sum"])
                elif measure.function == "mean":
                    value = str(accumulator["sum"] / accumulator["count"])
                elif measure.function == "min":
                    value = str(accumulator["min"])
                else:
                    value = str(accumulator["max"])
                row[name] = value
            output.append(row)
        if not group_by and not output:
            output = [{name: 0 if measure.function == "count" else None for measure, name in specs}]
        result = {
            "files": [table["file"] for table in tables],
            "tables": [table["name"] for table in tables],
            "matched_rows": matched_count,
            "groups": output, "group_count": len(output),
        }
        if matched_count == 0 and filters:
            diagnostics, omitted = self._empty_filter_diagnostics(tables, filters)
            result["empty_filter_diagnostics"] = diagnostics
            result["empty_result_diagnostic"] = self._empty_filter_message(diagnostics, omitted)
        return result

    def _joined_text(self, file_path: str) -> str:
        pages = self.text_pages.get(file_path)
        if pages is None:
            raise DataAccessError(f"file is not a readable text or PDF document: {file_path}")
        return "\n".join(
            f"[Page {index + 1}]\n{page}" if len(pages) > 1 else page
            for index, page in enumerate(pages)
        )

    def _read_text(self, file_path: str, offset: int, max_chars: int) -> dict[str, Any]:
        text = self._joined_text(file_path)
        end = min(len(text), offset + max_chars)
        return {
            "file": file_path, "offset": offset, "total_chars": len(text),
            "text": text[offset:end], "has_more": end < len(text),
            "next_offset": end if end < len(text) else None,
        }

    def _search_text(self, file_path: str, phrase: str | None, offset: int, limit: int) -> dict[str, Any]:
        if not isinstance(phrase, str) or not phrase:
            raise DataAccessError("search_text requires a nonempty literal phrase")
        pages = self.text_pages.get(file_path)
        if pages is None:
            raise DataAccessError(f"file is not a readable text or PDF document: {file_path}")
        needle = phrase.casefold()
        hits = []
        total = 0
        for page_number, page in enumerate(pages, start=1):
            for line_number, line in enumerate(page.splitlines(), start=1):
                self._check_deadline()
                if needle in line.casefold():
                    if total >= offset and len(hits) < limit:
                        hits.append({"page": page_number, "line": line_number, "text": line})
                    total += 1
        return {
            "file": file_path, "phrase": phrase, "offset": offset,
            "total_hits": total, "matches": hits,
            "has_more": offset + len(hits) < total,
            "next_offset": offset + len(hits) if offset + len(hits) < total else None,
        }

    def _json_get(self, file_path: str, path: list[str | int], offset: int, limit: int) -> dict[str, Any]:
        if file_path not in self.json_documents:
            raise DataAccessError(f"file is not JSON or JSONL: {file_path}")
        value = self.json_documents[file_path]
        for component in path:
            try:
                value = value[component]
            except (KeyError, IndexError, TypeError) as exc:
                raise DataAccessError(f"JSON path does not exist at component {component!r}") from exc
        if isinstance(value, list):
            items = value[offset:offset + limit]
            result = {
                "file": file_path, "path": path, "offset": offset,
                "total_items": len(value), "value": items,
                "has_more": offset + len(items) < len(value),
                "next_offset": offset + len(items) if offset + len(items) < len(value) else None,
            }
        else:
            result = {"file": file_path, "path": path, "value": value}
        return result


def _looks_numeric_encoded(value: str) -> bool:
    text = value.strip()
    return any(mark in text for mark in "$,%()") or bool(re.search(r"[0-9][kKmMbB]$", text))


def _parse_numeric_value(value: Any) -> tuple[Decimal, list[str]]:
    """Parse a plain number or an unambiguous common delimited numeric encoding."""
    original = str(value).strip()
    if not original:
        raise InvalidOperation("empty numeric value")
    text = original
    transformations: list[str] = []
    negative = False

    has_open_paren, has_close_paren = text.startswith("("), text.endswith(")")
    if has_open_paren or has_close_paren:
        if not (has_open_paren and has_close_paren):
            raise ValueError("ambiguous unmatched parentheses")
        text = text[1:-1].strip()
        if text.startswith(("+", "-")):
            raise ValueError("ambiguous sign inside parenthesized number")
        negative = True
        transformations.append("interpreted parenthesized value as negative")

    if text.startswith(("+", "-")):
        if negative:
            raise ValueError("ambiguous combination of parentheses and sign")
        if text[0] == "-":
            negative = True
        text = text[1:]
    if "$" in text:
        if text.count("$") != 1 or not text.startswith("$"):
            raise ValueError("ambiguous currency symbol placement")
        text = text[1:]
        transformations.append("removed leading dollar currency symbol")
    if "+" in text or "-" in text:
        raise ValueError("ambiguous sign placement")

    if "%" in text:
        if text.count("%") != 1 or not text.endswith("%"):
            raise ValueError("ambiguous percent symbol placement")
        text = text[:-1].strip()
        transformations.append("interpreted percent-marked values as percentage points")

    multiplier = Decimal(1)
    suffix = text[-1:].casefold()
    scales = {"k": Decimal(1_000), "m": Decimal(1_000_000), "b": Decimal(1_000_000_000)}
    if suffix in scales:
        multiplier = scales[suffix]
        text = text[:-1].strip()
        transformations.append(f"expanded {suffix.upper()} magnitude suffix to base units")

    if "," in text:
        grouped = re.fullmatch(r"(?:\d{1,3})(?:,\d{3})+(?:\.\d+)?(?:[eE][+-]?\d+)?", text)
        if not grouped:
            if re.fullmatch(r"[0-9.,]+", text):
                raise ValueError("ambiguous comma/decimal separator usage")
            raise InvalidOperation("not a numeric value")
        text = text.replace(",", "")
        transformations.append("removed thousands-separator commas")
    if not _NUMBER.fullmatch(text):
        if re.fullmatch(r"[0-9.,]+", text):
            raise ValueError("ambiguous decimal separator usage")
        raise InvalidOperation("not a numeric value")
    try:
        number = Decimal(text) * multiplier
    except InvalidOperation:
        raise
    if negative:
        number = -number
    return number, transformations


def _decimal(value: Any) -> Decimal:
    return _parse_numeric_value(value)[0]


def _query_mentions_filter(query: str, filters: list[DataFilter]) -> bool:
    """Whether an empty predicate directly uses a value or field named in the task."""
    query_tokens = re.findall(r"[\w]+", query.casefold())
    if not query_tokens:
        return False
    for condition in filters:
        candidates = [condition.value] if condition.value is not None else [condition.column]
        for candidate in candidates:
            candidate_tokens = re.findall(r"[\w]+", str(candidate).casefold())
            if not candidate_tokens or len(candidate_tokens) > len(query_tokens):
                continue
            width = len(candidate_tokens)
            if any(query_tokens[index:index + width] == candidate_tokens
                   for index in range(len(query_tokens) - width + 1)):
                return True
    return False


def _verified_category_rate_evidence(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive neutral category shares only from complete, unfiltered count(*) groups.

    This computes a percentage for every observed category but does not decide
    which category answers the task. The task agents must establish that
    semantic mapping from the query and available metadata.
    """
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        action = observation.get("action")
        request = observation.get("request")
        result = observation.get("result")
        reasons = observation.get("recovery_reasons", [])
        if not isinstance(request, dict) or not isinstance(result, dict):
            continue
        outcome_column = request.get("rate_outcome_column")
        if not isinstance(outcome_column, str) or not outcome_column:
            continue
        if request.get("filters") not in (None, []):
            continue
        if request.get("group_by") != [outcome_column]:
            continue
        measures = request.get("measures")
        if not isinstance(measures, list):
            continue
        count_star = [
            measure for measure in measures
            if isinstance(measure, dict)
            and measure.get("function") == "count"
            and measure.get("column") == "*"
        ]
        if len(count_star) != 1:
            continue
        # Recovery records are generated by the workflow itself; normal
        # aggregates retain their request alongside the result below.
        if action == "aggregate_recovery" and "rate_outcome_column" not in reasons:
            continue
        if action not in {"aggregate", "aggregate_recovery"}:
            continue

        groups = result.get("groups")
        denominator = result.get("matched_rows")
        files = result.get("files")
        tables = result.get("tables")
        if (
            not isinstance(groups, list)
            or type(denominator) is not int
            or denominator <= 0
            or type(result.get("group_count")) is not int
            or result["group_count"] != len(groups)
            or not isinstance(files, list)
            or not files
            or not all(isinstance(path, str) and path for path in files)
            or not isinstance(tables, list)
            or not tables
            or not all(isinstance(name, str) and name for name in tables)
        ):
            continue

        count_measure = count_star[0]
        count_key = count_measure.get("name") or f"count_{count_measure['column']}"
        categories: list[dict[str, Any]] = []
        category_keys: set[str] = set()
        total_count = 0
        valid = True
        for group in groups:
            if not isinstance(group, dict) or outcome_column not in group:
                valid = False
                break
            count = group.get(count_key)
            if type(count) is not int or count < 0:
                valid = False
                break
            category_key = json.dumps(group[outcome_column], ensure_ascii=False, sort_keys=True, default=str)
            if category_key in category_keys:
                valid = False
                break
            category_keys.add(category_key)
            total_count += count
            percentage = Decimal(count) * Decimal(100) / Decimal(denominator)
            categories.append({
                "value": group[outcome_column],
                "count": count,
                "percentage_of_rows": str(percentage),
            })
        if not valid or total_count != denominator or not categories:
            continue

        summary = {
            "files": files,
            "tables": tables,
            "column": outcome_column,
            "denominator_rows": denominator,
            "categories": categories,
            "basis": "validated unfiltered aggregate grouped by this column with count(*)",
            "interpretation": "Category percentages are neutral; choose a numerator only when task semantics establish its meaning.",
        }
        identity = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
        if identity not in seen:
            seen.add(identity)
            summaries.append(summary)
    return summaries


def gather_dataset_evidence(client: Any, query: str, toolbox: DatasetToolbox,
                            *, max_calls: int = DEFAULT_DATA_TOOL_INVOCATION_CAP, max_evidence_chars: int = 32_000,
                            max_response_retries: int = 2,
                            precomputed_profiles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Gather bounded data evidence; max_calls caps tool dispatches, not finalization."""
    if type(max_calls) is not int or not 2 <= max_calls <= 24:
        raise ValueError("max_calls must be between 2 and 24")
    if type(max_response_retries) is not int or not 0 <= max_response_retries <= 3:
        raise ValueError("max_response_retries must be between 0 and 3")
    history: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    request_diagnostics: list[dict[str, Any]] = []
    summary = ""
    useful_actions = 0
    rate_query = bool(_RATE_QUERY.search(query))
    pending_filter_categories: set[tuple[str, str, str]] = set()
    category_counts_inspected: set[tuple[str, str, str]] = set()
    observed_tables: set[tuple[str, str]] = set()
    outcome_aggregate_targets: dict[str, set[tuple[str, str]]] = {}
    pending_rate_outcome_column: str | None = None
    pending_outcome_categories: set[tuple[str, str, str]] = set()
    empty_predicates: dict[str, list[dict[str, Any]]] = {}
    repeated_empty_predicates: dict[str, int] = {}
    empty_text_searches: set[tuple[str | None, str]] = set()
    oversized_aggregates: set[str] = set()
    inspected_files: set[str] = set()
    guard_activations: list[dict[str, Any]] = []
    aggregate_columns_feedback_count = 0
    multi_file_coverage_rejections = 0
    repeated_invalid_request_stops = 0
    blocked_done_rejections = 0
    duplicate_filter_rejections = 0
    defaulted_file_selectors = 0
    workflow_iterations = 0
    generation_requests = 0
    tool_invocations = 0
    automatic_recovery_invocations = 0
    blocked_done_attempts = 0
    blocked_done_retries_for_same_evidence = 0
    last_blocked_done_signature: str | None = None
    invalid_file_selections: set[str] = set()
    invalid_response_counts: dict[str, int] = {}
    # Keep the tool cap fixed. These extra workflow slots allow one rejected
    # completion and a final completion after its missing evidence is gathered.
    max_workflow_iterations = max_calls + MAX_BLOCKED_COMPLETION_RETRIES + 1

    table_columns = {
        (descriptor["path"], table["name"]): set(table["columns"])
        for descriptor in toolbox.catalog()["files"]
        for table in descriptor.get("tables", [])
    }

    def usage_summary() -> dict[str, Any]:
        completed = [item for item in attempts if isinstance(item, dict)]
        return {
            "calls": generation_requests,
            "prompt_tokens": sum(int(item.get("prompt_eval_count", 0) or 0) for item in completed),
            "output_tokens": sum(int(item.get("eval_count", 0) or 0) for item in completed),
            "wall_seconds": round(sum(float(item.get("wall_seconds", 0) or 0) for item in completed), 3),
        }

    def activate_guard(name: str, reason: str, details: dict[str, Any] | None = None) -> None:
        activation = {
            "scope": "data_workflow",
            "guard": name,
            "activation": len(guard_activations) + 1,
            "reason": reason,
        }
        if details:
            activation.update(details)
        guard_activations.append(activation)

    def _sorted_keys(keys: set[tuple[str, str, str]]) -> list[dict[str, str]]:
        return [
            {"file": file_path, "table": table_name, "column": column}
            for file_path, table_name, column in sorted(keys)
        ]

    def workflow_diagnostics(completed: bool = False) -> dict[str, Any]:
        unresolved_filter_categories = pending_filter_categories - category_counts_inspected
        unresolved_outcome_categories = pending_outcome_categories - category_counts_inspected
        return {
            "max_tool_invocations": max_calls,
            "max_workflow_iterations": max_workflow_iterations,
            "max_generation_requests": max_workflow_iterations * (max_response_retries + 1),
            "tool_invocations": tool_invocations,
            "workflow_iterations": workflow_iterations,
            "generation_requests": generation_requests,
            "automatic_recovery_invocations": automatic_recovery_invocations,
            "blocked_done_attempts": blocked_done_attempts,
            "category_inspection_pending": bool(
                unresolved_filter_categories
                or unresolved_outcome_categories
                or (rate_query and pending_rate_outcome_column is None)
            ),
            "unresolved_filter_categories": _sorted_keys(unresolved_filter_categories),
            "rate_validation_required": rate_query,
            "rate_outcome_column": pending_rate_outcome_column,
            "unresolved_outcome_categories": _sorted_keys(unresolved_outcome_categories),
            "duplicate_empty_filter_requests": sum(repeated_empty_predicates.values()),
            "invalid_structured_response_attempts": sum(
                item.get("status") == "validation_error" for item in request_diagnostics
            ),
            "uninspected_task_files": sorted(set(toolbox.files) - inspected_files),
            "workflow_counters": {
                "guard_activations": list(guard_activations),
                "guard_retries": len(guard_activations),
                "aggregate_columns_validation_feedback": aggregate_columns_feedback_count,
                "multi_file_coverage_rejections": multi_file_coverage_rejections,
                "repeated_invalid_request_stops": repeated_invalid_request_stops,
                "blocked_done_rejections": blocked_done_rejections,
                "duplicate_filter_rejections": duplicate_filter_rejections,
                "defaulted_file_selectors": defaulted_file_selectors,
            },
            "completed": completed,
        }

    def workflow_error(message: str) -> DataToolWorkflowError:
        return DataToolWorkflowError(
            message,
            attempts=attempts,
            trace=audit,
            usage=usage_summary(),
            workflow_diagnostics=workflow_diagnostics(),
            request_diagnostics=request_diagnostics,
        )

    def _request_targets(request: DataToolRequest, result: dict[str, Any]) -> list[tuple[str, str]]:
        if isinstance(result.get("file"), str) and isinstance(result.get("table"), str):
            return [(result["file"], result["table"])]
        files = result.get("files")
        tables = result.get("tables")
        if isinstance(files, list) and isinstance(tables, list) and len(files) == len(tables):
            return list(zip(files, tables))
        return []

    def _request_files(request: DataToolRequest, result: dict[str, Any]) -> set[str]:
        returned = result.get("files")
        if isinstance(returned, list):
            candidates = returned
        elif isinstance(result.get("file"), str):
            candidates = [result["file"]]
        elif request.action == "aggregate" and request.files:
            candidates = request.files
        elif request.file is not None:
            candidates = [request.file]
        else:
            candidates = []
        return {path for path in candidates if isinstance(path, str) and path in toolbox.files}

    def _empty_predicate_key(request: DataToolRequest) -> str | None:
        if not request.filters:
            return None
        files = request.files or ([request.file] if request.file is not None else [])
        return json.dumps({
            "files": sorted(files),
            "table": request.table,
            "filters": [condition.model_dump(exclude_none=True) for condition in request.filters],
        }, sort_keys=True, ensure_ascii=False)

    def _register_relevant_empty_filter(request: DataToolRequest, targets: list[tuple[str, str]]) -> None:
        if not _query_mentions_filter(query, request.filters):
            return
        for file_path, table_name in targets:
            for condition in request.filters:
                pending_filter_categories.add((file_path, table_name, condition.column))

    def _validation_message() -> tuple[str, list[str]]:
        nonlocal pending_rate_outcome_column
        missing: list[str] = []
        uninspected = sorted(set(toolbox.files) - inspected_files)
        if uninspected:
            missing.append(
                "task-selected files not yet profiled or queried: " + ", ".join(uninspected)
            )
        unresolved_filter = pending_filter_categories - category_counts_inspected
        if unresolved_filter:
            rendered = ", ".join(
                f"{file_path}/{table_name}:{column}"
                for file_path, table_name, column in sorted(unresolved_filter)
            )
            missing.append(f"unfiltered category counts for task-relevant empty-filter columns: {rendered}")

        if rate_query:
            outcome_column = request.rate_outcome_column or pending_rate_outcome_column
            if not outcome_column:
                missing.append(
                    "a rate outcome column; declare `rate_outcome_column` on a request or in `done` and inspect its "
                    "unfiltered categories with aggregate group_by plus count(*)"
                )
            else:
                pending_rate_outcome_column = outcome_column
                targets = {
                    target for target in observed_tables
                    if outcome_column in table_columns.get(target, set())
                }
                if not targets:
                    missing.append(
                        f"outcome column {outcome_column!r} was not found in an inspected table; "
                        "choose an exact catalog column"
                    )
                    pending_outcome_categories.clear()
                else:
                    referenced = outcome_aggregate_targets.get(outcome_column, set())
                    if referenced:
                        targets &= referenced
                    pending_outcome_categories.clear()
                    pending_outcome_categories.update(
                        (file_path, table_name, outcome_column)
                        for file_path, table_name in targets
                        if (file_path, table_name, outcome_column) not in category_counts_inspected
                    )
                    if pending_outcome_categories:
                        rendered = ", ".join(
                            f"{file_path}/{table_name}:{column}"
                            for file_path, table_name, column in sorted(pending_outcome_categories)
                        )
                        missing.append(
                            f"unfiltered outcome-category counts for {rendered}; group by the outcome "
                            "column and use count(*) so every category and the denominator are included"
                        )
        return ("cannot finish: " + "; ".join(missing), missing) if missing else ("", [])

    def _recover_missing_category_counts() -> None:
        """Gather only the exact bounded category evidence the completion guard needs."""
        nonlocal tool_invocations, automatic_recovery_invocations, useful_actions
        required = (
            (pending_filter_categories | pending_outcome_categories)
            - category_counts_inspected
        )
        for file_path, table_name, column in sorted(required):
            target = (file_path, table_name)
            key = (file_path, table_name, column)
            if column not in table_columns.get(target, set()):
                continue

            reasons = []
            if key in pending_filter_categories:
                reasons.append("task_relevant_empty_filter_column")
            if key in pending_outcome_categories:
                reasons.append("rate_outcome_column")
            request = DataToolRequest.model_validate({
                "action": "aggregate",
                "file": file_path,
                "table": table_name,
                "filters": [],
                "group_by": [column],
                "measures": [{"function": "count", "column": "*", "name": "category_count"}],
                **({"rate_outcome_column": column} if "rate_outcome_column" in reasons else {}),
            }, strict=True)
            params = request.model_dump(exclude_none=True)
            if tool_invocations >= max_calls:
                message = (
                    f"cannot complete automatic category recovery for {file_path}/{table_name}:{column}; "
                    f"the {max_calls}-tool-invocation budget is exhausted. The required operation is an "
                    "unfiltered aggregate grouped only by that exact column with count(*)."
                )
                history.append({
                    "action": "aggregate_recovery",
                    "request": params,
                    "result": {"error": message, "recovery_reasons": reasons},
                })
                audit.append({
                    "action": "aggregate_recovery",
                    "request": params,
                    "status": "error",
                    "error": message,
                    "recovery_reasons": reasons,
                })
                raise workflow_error(message)

            # Recovery is a real data-tool dispatch and consumes the same fixed cap.
            tool_invocations += 1
            automatic_recovery_invocations += 1
            try:
                result = toolbox.execute(request)
                groups = result.get("groups")
                matched_rows = result.get("matched_rows")
                if (
                    result.get("files") != [file_path]
                    or result.get("tables") != [table_name]
                    or type(matched_rows) is not int
                    or matched_rows < 0
                    or not isinstance(groups, list)
                    or any(
                        not isinstance(group, dict)
                        or column not in group
                        or type(group.get("category_count")) is not int
                        or group["category_count"] < 0
                        for group in groups
                    )
                    or sum(group["category_count"] for group in groups) != matched_rows
                ):
                    raise DataAccessError(
                        "category recovery returned an incomplete or inconsistent aggregate result"
                    )
            except (DataOperationTimeout, TaskWallClockTimeout) as exc:
                error = str(exc)
                history.append({"action": request.action, "request": params, "result": {"error": error}})
                audit.append({"action": request.action, "request": params, "status": "timeout", "error": error})
                raise workflow_error(error) from exc
            except DataAccessError as exc:
                error = str(exc)
                history.append({
                    "action": "aggregate_recovery",
                    "request": params,
                    "result": {"error": error, "recovery_reasons": reasons},
                })
                audit.append({
                    "action": "aggregate_recovery",
                    "request": params,
                    "status": "error",
                    "error": error,
                    "recovery_reasons": reasons,
                })
                raise workflow_error(
                    f"automatic category recovery failed for {file_path}/{table_name}:{column}: {error}"
                ) from exc

            # Keep the exact bounded evidence in prompt history and the audit trace.
            history.append({
                "action": "aggregate_recovery",
                "request": params,
                "result": result,
                "recovery_reasons": reasons,
            })
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            audit.append({
                "action": "aggregate_recovery",
                "request": params,
                "status": "success",
                "recovery_reasons": reasons,
                "result_sha256": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
                "result_chars": len(result_json),
                "category_count_evidence": result,
            })
            category_counts_inspected.add(key)
            pending_filter_categories.discard(key)
            pending_outcome_categories.discard(key)
            if "rate_outcome_column" in reasons:
                outcome_aggregate_targets.setdefault(column, set()).add(target)
            useful_actions += 1

    def _degraded_result(reason: str, original_failure: str) -> dict[str, Any]:
        """Return only after deterministic recovery leaves usable tool evidence."""
        _recover_missing_category_counts()
        successful = [item for item in history if isinstance(item.get("result"), dict)
                      and "error" not in item["result"]]
        has_verified_categories = any(
            "category_count_evidence" in item for item in audit if item.get("status") == "success"
        )
        if not successful or not has_verified_categories:
            raise workflow_error(original_failure)
        unresolved = (pending_filter_categories | pending_outcome_categories) - category_counts_inspected
        if unresolved:
            raise workflow_error(original_failure)
        evidence = {
            "available_files": toolbox.catalog(),
            "observations": history,
            "agent_summary": "",
            "agent_summary_status": "unverified_omitted_after_data_loop_failure",
            "verified_category_rates": _verified_category_rate_evidence(history),
        }
        evidence_json = json.dumps(evidence, ensure_ascii=False, default=str)
        if len(evidence_json) > max_evidence_chars:
            raise workflow_error(f"collected evidence exceeds {max_evidence_chars} characters")
        diagnostics = workflow_diagnostics(completed=True)
        diagnostics["data_loop_degraded"] = True
        diagnostics["data_loop_degradation_reason"] = reason
        return {
            "evidence": evidence, "trace": audit, "attempts": attempts,
            "request_diagnostics": request_diagnostics, "usage": usage_summary(),
            "workflow_diagnostics": diagnostics, "data_loop_degraded": True,
            "data_loop_degradation_reason": reason,
        }

    bounded_profiles = copy.deepcopy(precomputed_profiles or [])
    # Keep schema and exhaustive counts intact; reduce sample payload first.
    profile_budget = 8_000
    while len(json.dumps(bounded_profiles, ensure_ascii=False, default=str)) > profile_budget:
        changed = False
        for profile in bounded_profiles:
            for table in profile.get("tables", []):
                sample = table.get("sample_rows")
                if isinstance(sample, dict) and sample.get("rows"):
                    sample["rows"].pop()
                    changed = True
                    break
            if changed:
                break
        if not changed:
            raise DataAccessError("precomputed profile exceeds its bounded data-agent prompt budget")
    for call_number in range(1, max_workflow_iterations + 1):
        workflow_iterations = call_number
        prompt = json.dumps({
            "task_query": query,
            "allowlisted_files": toolbox.catalog(),
            "precomputed_file_profiles": bounded_profiles,
            "previous_tool_results": history,
        }, ensure_ascii=False)
        if len(prompt) > max_evidence_chars:
            raise workflow_error(
                f"data evidence exceeded {max_evidence_chars} characters; no results were dropped"
            )
        request = None
        retry_system = DATA_TOOL_SYSTEM_PROMPT
        uninspected_files = sorted(set(toolbox.files) - inspected_files)
        if uninspected_files:
            retry_system += (
                "\nBefore `done`, successfully profile or query every task-selected file not yet inspected: "
                + ", ".join(uninspected_files)
                + ". Use each exact catalog path; do not infer its contents from another file."
            )
        if empty_text_searches:
            retry_system += (
                "\nIf a literal search returned zero hits, use read_text to collect the source values. "
                "Once you have the values, use done with the evidence required for the calculation."
            )
        unresolved_filter_categories = pending_filter_categories - category_counts_inspected
        if unresolved_filter_categories:
            retry_system += (
                "\nTask-relevant empty-filter columns still needing full category counts: "
                + ", ".join(
                    f"{file_path}/{table_name}:{column}"
                    for file_path, table_name, column in sorted(unresolved_filter_categories)
                )
                + ". Use aggregate with no filters, group_by set to the listed column, and a count(*) measure."
            )
        if pending_rate_outcome_column and pending_outcome_categories:
            retry_system += (
                "\nThe declared rate outcome still needs unfiltered category counts: "
                + ", ".join(
                    f"{file_path}/{table_name}:{column}"
                    for file_path, table_name, column in sorted(pending_outcome_categories)
                )
                + ". Group by the outcome and use count(*), then compute the numerator and denominator from those counts."
            )
        for retry_number in range(max_response_retries + 1):
            generation_requests += 1
            try:
                raw = client.generate(
                    prompt,
                    system=retry_system,
                    schema=ollama_data_tool_schema(),
                )
            except Exception as exc:
                request_diagnostics.append({
                    "workflow_iteration": call_number,
                    "generation_attempt": retry_number + 1,
                    "generation_request": generation_requests,
                    "status": "generation_error",
                    "error_type": type(exc).__name__[:80],
                    "validation_errors": _validation_error_details(exc),
                })
                raise workflow_error(
                    f"data request generation failed: {type(exc).__name__}: {exc}"
                ) from exc
            attempts.append(raw)
            try:
                response_text = raw.get("response") if isinstance(raw, dict) else None
                request = DataToolRequest.model_validate_json(response_text, strict=True)
                break
            except Exception as exc:
                fingerprint = _invalid_response_fingerprint(response_text)
                repeated_count = invalid_response_counts.get(fingerprint, 0) + 1
                invalid_response_counts[fingerprint] = repeated_count
                request_diagnostics.append({
                    "workflow_iteration": call_number,
                    "generation_attempt": retry_number + 1,
                    "generation_request": generation_requests,
                    "status": "validation_error",
                    "request_sha256": fingerprint,
                    "repeated_identical_count": repeated_count,
                    "rejected_request": _request_diagnostic_excerpt(response_text),
                    "validation_errors": _validation_error_details(exc),
                })
                try:
                    rejected = json.loads(response_text) if isinstance(response_text, str) else {}
                except (json.JSONDecodeError, TypeError):
                    rejected = {}
                if isinstance(rejected, dict):
                    rejected_file = rejected.get("file")
                    rejected_table = rejected.get("table")
                    if rejected_file in toolbox.files and rejected_table:
                        target = (rejected_file, rejected_table)
                        outcome = rejected.get("rate_outcome_column")
                        if rate_query and isinstance(outcome, str) and outcome in table_columns.get(target, set()):
                            pending_rate_outcome_column = outcome
                            pending_outcome_categories.add((rejected_file, rejected_table, outcome))
                        rejected_filters = rejected.get("filters", [])
                        for condition in rejected_filters if isinstance(rejected_filters, list) else []:
                            column = condition.get("column") if isinstance(condition, dict) else None
                            if isinstance(column, str) and column in table_columns.get(target, set()):
                                pending_filter_categories.add((rejected_file, rejected_table, column))
                if isinstance(exc, ValidationError) and "aggregate does not accept `columns`" in str(exc):
                    aggregate_columns_feedback_count += 1
                    activate_guard(
                        "aggregate_columns_validation_feedback",
                        "unsupported aggregate columns field",
                        {"generation_request": generation_requests},
                    )
                if repeated_count > 1:
                    repeated_invalid_request_stops += 1
                    activate_guard(
                        "repeated_identical_invalid_request",
                        "the same structured request failed strict validation more than once",
                        {"generation_request": generation_requests, "request_sha256": fingerprint},
                    )
                    return _degraded_result(
                        "repeated_identical_invalid_request",
                        f"agent repeated an identical invalid structured data request on call {call_number}; "
                        "the request and validation feedback are recorded, so the workflow stopped before "
                        "spending further generation attempts",
                    )
                if retry_number == max_response_retries:
                    raise workflow_error(
                        f"agent returned an invalid structured data request on call {call_number} "
                        f"after {retry_number + 1} generation attempts"
                    ) from exc
                retry_system = (
                    DATA_TOOL_SYSTEM_PROMPT
                    + "\nYour prior response did not satisfy the structured request contract. "
                    "Return a new JSON object with valid field types and an allowed action; "
                    "do not add prose or code."
                    + _validation_feedback(exc, response_text, toolbox)
                )
        assert request is not None
        selector_defaulted = False
        if (
            request.action not in {"catalog", "done"}
            and request.file is None
            and not request.files
            and len(toolbox.files) == 1
        ):
            request = request.model_copy(update={"file": next(iter(toolbox.files))})
            selector_defaulted = True
            defaulted_file_selectors += 1
        params = request.model_dump(exclude_none=True)
        if request.rate_outcome_column is not None:
            declared_column = request.rate_outcome_column
            candidate_files = request.files or ([request.file] if request.file is not None else [])
            candidate_tables = [
                (file_path, table_name)
                for file_path, table_name in table_columns
                if (not candidate_files or file_path in candidate_files)
                and (request.table is None or table_name == request.table)
            ]
            if not any(
                declared_column in table_columns.get(target, set())
                for target in candidate_tables
            ):
                available = sorted({
                    column
                    for target in candidate_tables
                    for column in table_columns.get(target, set())
                })
                message = (
                    f"rate_outcome_column {declared_column!r} does not match a column in the selected table; "
                    f"choose an exact available column from {available[:40]}"
                )
                history.append({"action": "workflow_validation", "request": params, "result": {"error": message}})
                audit.append({
                    "action": request.action, "request": params, "status": "error",
                    "error": message, "selector_defaulted": selector_defaulted,
                })
                continue
            pending_rate_outcome_column = declared_column
            pending_outcome_categories.update(
                (file_path, table_name, declared_column)
                for file_path, table_name in candidate_tables
                if declared_column in table_columns.get((file_path, table_name), set())
                and (file_path, table_name, declared_column) not in category_counts_inspected
            )
        if request.action == "done":
            message, missing_evidence = _validation_message()
            if missing_evidence:
                history.append({
                    "action": "workflow_validation",
                    "result": {"error": message, "missing_evidence": missing_evidence},
                })
                audit.append({
                    "action": "done", "request": params, "status": "error",
                    "error": message, "missing_evidence": missing_evidence,
                })
                blocked_done_attempts += 1
                blocked_done_rejections += 1
                missing_files = sorted(set(toolbox.files) - inspected_files)
                if missing_files:
                    multi_file_coverage_rejections += 1
                    activate_guard(
                        "task_file_coverage",
                        "completion requested before every task-selected file was inspected",
                        {"uninspected_files": missing_files},
                    )
                else:
                    activate_guard(
                        "required_evidence_completion",
                        "completion requested before required evidence was gathered",
                        {"missing_evidence": missing_evidence},
                    )
                signature = json.dumps(missing_evidence, ensure_ascii=False)
                if signature == last_blocked_done_signature:
                    blocked_done_retries_for_same_evidence += 1
                else:
                    last_blocked_done_signature = signature
                    blocked_done_retries_for_same_evidence = 1
                if blocked_done_retries_for_same_evidence > MAX_BLOCKED_COMPLETION_RETRIES:
                    raise workflow_error(
                        "data agent repeatedly requested completion without required evidence: " + message
                    )
                _recover_missing_category_counts()
                continue
            if useful_actions < 1:
                raise workflow_error("agent finished without inspecting or querying any permitted data")
            summary = (request.summary or "").strip()
            break

        search_key = None
        aggregate_key = None
        duplicate_feedback = ""
        if request.action == "search_text" and request.text:
            file_path = request.file or (request.files[0] if len(request.files) == 1 else None)
            search_key = (file_path, request.text.casefold())
            if search_key in empty_text_searches:
                duplicate_feedback = (
                    "This literal search already found zero hits in this file. Use read_text to inspect the "
                    "source values or search for a different phrase."
                )
        elif request.action == "aggregate":
            # Presentation fields do not narrow the number of groups or matching input rows.
            aggregate_key = json.dumps({
                "files": request.files or [request.file], "table": request.table,
                "filters": params["filters"], "group_by": request.group_by,
                "measures": [(m.function, m.column) for m in request.measures],
            }, sort_keys=True, ensure_ascii=False)
            if aggregate_key in oversized_aggregates:
                duplicate_feedback = (
                    "This aggregation already exceeded the output limit. Use a selective filter, "
                    "a lower-cardinality grouping, or only the measures needed for the answer. "
                    "Changing limit or offset does not paginate aggregate groups."
                )
        if duplicate_feedback:
            history.append({
                "action": "workflow_validation", "request": params, "result": {"error": duplicate_feedback},
            })
            audit.append({
                "action": request.action, "request": params, "status": "skipped_duplicate",
                "error": duplicate_feedback, "selector_defaulted": selector_defaulted,
            })
            activate_guard("duplicate_no_progress_request", duplicate_feedback, {"action": request.action})
            if aggregate_key is not None and rate_query:
                _recover_missing_category_counts()
            continue
        if tool_invocations >= max_calls:
            message = (
                f"data agent reached the {max_calls}-tool-invocation limit after {workflow_iterations} "
                f"workflow iterations ({generation_requests} generation requests, {tool_invocations} "
                "tool invocations) before finishing"
            )
            history.append({"action": "workflow_validation", "request": params, "result": {"error": message}})
            audit.append({
                "action": request.action, "request": params, "status": "error",
                "error": message, "selector_defaulted": selector_defaulted,
            })
            return _degraded_result("tool_budget_exhausted", message)
        tool_invocations += 1
        predicate_key = _empty_predicate_key(request) if request.action in {"filter", "aggregate"} else None
        if predicate_key is not None and predicate_key in empty_predicates:
            message = (
                "this exact predicate already returned no rows; do not repeat it unchanged. Use the "
                "observed values in its diagnostic, or change the filter hypothesis and inspect categories"
            )
            diagnostics = empty_predicates[predicate_key]
            history.append({
                "action": "workflow_validation", "request": params,
                "result": {"error": message, "empty_filter_diagnostics": diagnostics},
            })
            audit.append({
                "action": request.action, "request": params, "status": "skipped_duplicate",
                "error": message, "empty_filter_diagnostics": diagnostics,
                "selector_defaulted": selector_defaulted,
            })
            repeated_empty_predicates[predicate_key] = repeated_empty_predicates.get(predicate_key, 0) + 1
            duplicate_filter_rejections += 1
            activate_guard(
                "duplicate_empty_filter",
                "an identical predicate had already returned no rows",
                {"duplicate_count": repeated_empty_predicates[predicate_key]},
            )
            if repeated_empty_predicates[predicate_key] > MAX_BLOCKED_COMPLETION_RETRIES:
                raise workflow_error(
                    "data agent repeatedly issued the same unsuccessful filter without changing its hypothesis"
                )
            continue

        try:
            result = toolbox.execute(request)
            if search_key is not None and result.get("total_hits") == 0:
                empty_text_searches.add(search_key)
                result = {
                    **result,
                    "guidance": "No literal hits. Use read_text to inspect the source or search a different phrase.",
                }
            if request.action != "catalog":
                useful_actions += 1
                inspected_files.update(_request_files(request, result))
            targets = _request_targets(request, result)
            observed_tables.update(targets)

            if request.action in {"filter", "aggregate"} and request.filters:
                zero_matches = (
                    result.get("total_matches") == 0
                    if request.action == "filter"
                    else result.get("matched_rows") == 0
                )
                if zero_matches:
                    _register_relevant_empty_filter(request, targets)
                    if predicate_key is not None:
                        empty_predicates[predicate_key] = result.get("empty_filter_diagnostics", [])

            if request.action == "aggregate":
                if rate_query:
                    for measure in request.measures:
                        if measure.function == "count" and measure.column != "*":
                            outcome_aggregate_targets.setdefault(measure.column, set()).update(targets)
                    for column in request.group_by:
                        outcome_aggregate_targets.setdefault(column, set()).update(targets)

                has_row_count = any(
                    measure.function == "count" and measure.column == "*"
                    for measure in request.measures
                )
                if (
                    not request.filters
                    and has_row_count
                    and len(request.group_by) == 1
                    and type(result.get("matched_rows")) is int
                    and result.get("matched_rows", -1) >= 0
                ):
                    # A single-column aggregate gives that column's category
                    # counts directly. A multi-column grouping is a joint table,
                    # not the standalone distribution required to validate a rate.
                    column = request.group_by[0]
                    for file_path, table_name in targets:
                        key = (file_path, table_name, column)
                        category_counts_inspected.add(key)
                        pending_filter_categories.discard(key)
                        pending_outcome_categories.discard(key)
            result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
            history.append({"action": request.action, "request": params, "result": result})
            audit_entry = {
                "action": request.action,
                "request": params,
                "result": result,
                "result_sha256": hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
                "result_chars": len(result_json),
                "status": "success",
                "selector_defaulted": selector_defaulted,
                **({"empty_filter_diagnostics": result["empty_filter_diagnostics"]}
                   if "empty_filter_diagnostics" in result else {}),
            }
            if request.action == "aggregate" and not request.filters and len(request.group_by) == 1:
                count_stars = [
                    measure for measure in request.measures
                    if measure.function == "count" and measure.column == "*"
                ]
                category_column = request.group_by[0]
                groups = result.get("groups")
                matched_rows = result.get("matched_rows")
                if len(count_stars) == 1 and isinstance(groups, list) and type(matched_rows) is int:
                    count_key = count_stars[0].name or "count_*"
                    complete_counts = (
                        type(result.get("group_count")) is int
                        and result["group_count"] == len(groups)
                        and all(
                            isinstance(group, dict)
                            and category_column in group
                            and type(group.get(count_key)) is int
                            and group[count_key] >= 0
                            for group in groups
                        )
                        and sum(group[count_key] for group in groups) == matched_rows
                    )
                    if complete_counts:
                        # The compact task trace carries the same validated category
                        # result exposed to the final answer and completion guard.
                        audit_entry["category_count_evidence"] = result
            audit.append(audit_entry)
        except (DataOperationTimeout, TaskWallClockTimeout) as exc:
            error = {"error": str(exc)}
            history.append({"action": request.action, "request": params, "result": error})
            audit.append({
                "action": request.action, "request": params, "status": "timeout",
                "error": str(exc), "selector_defaulted": selector_defaulted,
            })
            raise workflow_error(str(exc)) from exc
        except DataAccessError as exc:
            error = {"error": str(exc)}
            if aggregate_key is not None and isinstance(exc, DataToolOutputTooLarge):
                oversized_aggregates.add(aggregate_key)
                error["guidance"] = (
                    "Narrow filters, group by fewer categories, or use fewer measures. "
                    "Changing limit or offset does not paginate aggregate groups."
                )
            history.append({"action": request.action, "request": params, "result": error})
            audit.append({
                "action": request.action, "request": params, "status": "error",
                "error": str(exc), "selector_defaulted": selector_defaulted,
            })
            if isinstance(exc, DataToolFileSelectionError):
                missing_selector = request.file is None and not request.files
                selector_key = json.dumps({
                    "action": request.action,
                    "file": request.file,
                    "files": request.files,
                }, sort_keys=True, ensure_ascii=False)
                if selector_key in invalid_file_selections:
                    guard_name = (
                        "repeated_missing_file_selector" if missing_selector
                        else "repeated_invalid_file_selection"
                    )
                    reason = "the same request omitted its required `file` selector" if missing_selector else (
                        "the requested file path is outside the task allowlist"
                    )
                    activate_guard(
                        guard_name,
                        reason,
                    )
                    if missing_selector:
                        message = f"data agent repeated a request with `file` missing. {exc}"
                    else:
                        message = f"data agent repeated an invalid file path. {exc}"
                    raise workflow_error(message) from exc
                invalid_file_selections.add(selector_key)
    else:
        raise workflow_error(
            f"data agent used {max_workflow_iterations} bounded workflow iterations "
            f"({generation_requests} generation requests, {tool_invocations} tool invocations) "
            "without finishing"
        )

    evidence = {
        "available_files": toolbox.catalog(),
        "observations": history,
        "agent_summary": summary,
        "agent_summary_status": "model_generated_unverified",
        "verified_category_rates": _verified_category_rate_evidence(history),
    }
    evidence_json = json.dumps(evidence, ensure_ascii=False, default=str)
    if len(evidence_json) > max_evidence_chars:
        raise workflow_error(
            f"collected evidence exceeds {max_evidence_chars} characters; no results were silently truncated"
        )
    return {
        "evidence": evidence,
        "trace": audit,
        "attempts": attempts,
        "request_diagnostics": request_diagnostics,
        "usage": usage_summary(),
        "workflow_diagnostics": workflow_diagnostics(completed=True),
    }
