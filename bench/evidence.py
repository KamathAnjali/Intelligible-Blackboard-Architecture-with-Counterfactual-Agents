"""Small, deterministic helpers for moving verified data-tool evidence to PXP.

These helpers consume only successful, unfiltered category aggregates from the
allowlisted data-tool trace. They do not read benchmark answers or execute
generated code.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_RATE_QUERY = re.compile(r"\b(percent(?:age)?|rate|proportion|fraction|share)\b", re.IGNORECASE)
_PERCENT_QUERY = re.compile(r"\bpercent(?:age)?\b|%", re.IGNORECASE)
_DECIMAL_PLACES = re.compile(
    r"\b(?:to|round(?:ed)?\s+to)\s+(\d{1,2})\s+decimal\s+places?\b", re.IGNORECASE
)
_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![\w.])")
_REFUSAL = re.compile(
    r"(?:^|[:\-]\s*)(?:insufficient information|insufficient data|cannot determine|unable to determine)\b",
    re.IGNORECASE,
)
_MISSING_DATA = re.compile(
    r"\b(?:missing|unavailable|not provided|not present|not included|absent|"
    r"does not (?:include|contain|report|exist)|doesn't (?:include|contain|report|exist)|"
    r"cannot find|could not find|lacks)\b|"
    r"\bno\s+(?:[a-z][\w-]*\s+){0,4}(?:values?|measurements?|observations?|records?|readings?|data points?)\b",
    re.IGNORECASE,
)
_AFFIRMATIVE = {"yes", "y", "true", "1", "present", "positive", "affirmative"}
_NEGATIVE = {"no", "n", "false", "0", "absent", "negative"}

# General language families that can link an outcome field to the event asked
# about. Column names still come exclusively from the validated trace.
_OUTCOME_CONCEPTS = (
    frozenset({
        "violation", "violations", "violate", "violated", "exceed", "exceeds",
        "exceeded", "exceeding", "breach", "breaches", "breached", "fail",
        "fails", "failed", "failure", "failures", "noncompliance",
        "noncompliant", "closure", "closures", "closed",
    }),
)


def empty_filter_hint(diagnostic: dict[str, Any] | None = None) -> str:
    """Return bounded recovery guidance, optionally naming the filter evidence."""
    if not isinstance(diagnostic, dict):
        return (
            "An empty filter is not a zero count or rate. Compare the attempted value with the observed "
            "values and the field's geographic or categorical level; use the task-selected source scope "
            "when it already defines the population."
        )
    column = diagnostic.get("column")
    attempted = diagnostic.get("attempted_value")
    observed = diagnostic.get("observed_values")
    details = []
    if isinstance(column, str) and column:
        details.append(f"column {column!r}")
    if attempted is not None:
        details.append(f"attempted value {str(attempted)[:120]!r}")
    if isinstance(observed, list):
        details.append("observed values " + json.dumps(observed[:8], ensure_ascii=False, default=str))
    rendered = "; ".join(details)
    prefix = f"Empty-filter diagnostic ({rendered}). " if rendered else "Empty-filter diagnostic. "
    return prefix + (
        "This does not establish a zero count or rate, or that an outcome category is absent. Check whether "
        "the field represents the same level as the query, whether the task-selected files already define "
        "the population, or whether the requested subset is unavailable. Do not repeat an identical filter."
    )


def _category_result(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    if entry.get("status") != "success":
        return None
    request = entry.get("request")
    result = entry.get("category_count_evidence")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return None
    group_by = request.get("group_by")
    if not isinstance(group_by, list) or len(group_by) != 1 or not isinstance(group_by[0], str):
        return None
    column = group_by[0]
    if request.get("filters") not in (None, []):
        return None
    measures = request.get("measures")
    if not isinstance(measures, list):
        return None
    count_stars = [
        item for item in measures
        if isinstance(item, dict) and item.get("function") == "count" and item.get("column") == "*"
    ]
    if len(count_stars) != 1:
        return None
    count_key = count_stars[0].get("name") or "count_*"
    groups = result.get("groups")
    denominator = result.get("matched_rows")
    files, tables = result.get("files"), result.get("tables")
    if (
        not isinstance(groups, list)
        or type(denominator) is not int or denominator <= 0
        or type(result.get("group_count")) is not int
        or result["group_count"] != len(groups)
        or not isinstance(files, list) or not files
        or not all(isinstance(item, str) and item for item in files)
        or not isinstance(tables, list) or not tables
        or not all(isinstance(item, str) and item for item in tables)
    ):
        return None
    total = 0
    distinct: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or column not in group:
            return None
        count = group.get(count_key)
        if type(count) is not int or count < 0:
            return None
        identity = json.dumps(group[column], ensure_ascii=False, sort_keys=True, default=str)
        if identity in distinct:
            return None
        distinct.add(identity)
        total += count
    if not groups or total != denominator:
        return None
    return request, result, column


def build_evidence_message(trace_entry: dict[str, Any]) -> str:
    """Render one successful, complete category-count trace entry as a FACT."""
    validated = _category_result(trace_entry)
    if validated is None:
        raise ValueError("trace entry is not a successful complete unfiltered category-count aggregate")
    request, result, column = validated
    count_stars = [
        item for item in request["measures"]
        if isinstance(item, dict) and item.get("function") == "count" and item.get("column") == "*"
    ]
    count_key = count_stars[0].get("name") or "count_*"
    categories = [
        {"value": group[column], "count": group[count_key]}
        for group in result["groups"]
    ]
    evidence = {
        "files": result["files"],
        "tables": result["tables"],
        "column": column,
        "categories": categories,
        "total_rows": result["matched_rows"],
        "basis": "successful unfiltered group_by/count(*) result",
    }
    return "Verified data-tool FACT: " + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _outcome_is_task_relevant(question: str, column: str) -> bool:
    question_tokens = _tokens(question)
    column_tokens = _tokens(column)
    if not question_tokens or not column_tokens:
        return False
    if question_tokens & column_tokens:
        return True
    return any(bool(question_tokens & family) and bool(column_tokens & family)
               for family in _OUTCOME_CONCEPTS)


def _requested_precision(question: str) -> int | None:
    match = _DECIMAL_PLACES.search(question)
    return int(match.group(1)) if match else None


def _display_decimal(value: Decimal, precision: int | None) -> str:
    if precision is None:
        return format(value.normalize(), "f")
    quantum = Decimal(1).scaleb(-precision)
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), f".{precision}f")


def compute_rate(
    trace: list[dict[str, Any]],
    question: str,
    *,
    population_files: list[str] | None = None,
) -> dict[str, Any] | None:
    """Compute a rate only from complete unfiltered category evidence and task semantics.

    The returned ``answer`` is in percentage units for percentage questions, or
    fractional units for proportion/fraction/share/rate questions. No reference
    answer or model-written summary is consulted.
    """
    if not isinstance(question, str) or not _RATE_QUERY.search(question):
        return None
    if not isinstance(trace, list):
        return None
    allowed = set(population_files) if population_files is not None else None
    candidates = []
    for entry in trace:
        validated = _category_result(entry) if isinstance(entry, dict) else None
        if validated is None:
            continue
        request, result, column = validated
        if request.get("rate_outcome_column") != column:
            continue
        files = result["files"]
        if allowed is not None and set(files) != allowed:
            continue
        if not _outcome_is_task_relevant(question, column):
            continue
        count_stars = [
            item for item in request["measures"]
            if isinstance(item, dict) and item.get("function") == "count" and item.get("column") == "*"
        ]
        count_key = count_stars[0].get("name") or "count_*"
        categories = [
            {"value": group[column], "count": group[count_key]}
            for group in result["groups"]
        ]
        normalized = {str(item["value"]).strip().casefold(): item for item in categories}
        affirmative_values = _AFFIRMATIVE.intersection(normalized)
        no_values = _NEGATIVE.intersection(normalized)
        if len(affirmative_values) != 1 or not no_values:
            continue
        yes = normalized[next(iter(affirmative_values))]
        if "yes" not in question.casefold() and not _outcome_is_task_relevant(question, column):
            continue
        denominator = result["matched_rows"]
        if yes["count"] > denominator:
            continue
        proportion = Decimal(yes["count"]) / Decimal(denominator)
        is_percent = bool(_PERCENT_QUERY.search(question))
        raw_value = proportion * Decimal(100) if is_percent else proportion
        answer = _display_decimal(raw_value, _requested_precision(question))
        candidates.append({
            "answer": answer,
            "unit": "percent" if is_percent else "fraction",
            "numerator": yes["count"],
            "denominator": denominator,
            "outcome_column": column,
            "outcome_value": yes["value"],
            "files": files,
            "tables": result["tables"],
            "precision": _requested_precision(question),
            "basis": "complete, unfiltered category counts from the task-selected data files",
        })
    if not candidates:
        return None
    identities = {
        (item["answer"], item["unit"], item["numerator"], item["denominator"],
         item["outcome_column"], tuple(item["files"]), tuple(item["tables"]))
        for item in candidates
    }
    return candidates[0] if len(identities) == 1 else None


def _numeric_answer(answer: Any) -> Decimal | None:
    if not isinstance(answer, str):
        answer = str(answer)
    matches = _NUMBER.findall(answer)
    if len(matches) != 1:
        return None
    try:
        return Decimal(matches[0].replace(",", "").rstrip("%"))
    except InvalidOperation:
        return None


def termination_candidate(prediction: str, explanation: str = "") -> str:
    """Include the agent's specific missing-data rationale when guarding a refusal."""
    if isinstance(prediction, str) and _REFUSAL.search(prediction) and isinstance(explanation, str):
        return prediction if not explanation.strip() else prediction + "\n" + explanation
    return prediction


def _refusal_specificity_reason(trace: list[dict[str, Any]], answer: str) -> str | None:
    refusal = _REFUSAL.search(answer or "")
    if refusal is None:
        return None
    rationale = (answer or "")[refusal.end():].strip(" .,:;-\n")
    missing = _MISSING_DATA.search(rationale)
    if missing is None:
        return "refusal_missing_specific_data"
    subject = rationale[max(0, missing.start() - 80):min(len(rationale), missing.end() + 80)]
    generic = {
        "a", "an", "and", "are", "as", "available", "because", "by", "data",
        "details", "does", "evidence", "facts", "for", "from", "have", "in",
        "information", "is", "it", "not", "of", "on", "or", "present", "required",
        "the", "this", "to", "unavailable", "was", "were", "with", "without",
    }
    named_data = {token for token in _tokens(subject) if token not in generic}
    if not named_data:
        return "refusal_missing_specific_data"

    has_successful_text = any(
        isinstance(entry, dict)
        and entry.get("status") == "success"
        and entry.get("action") in {"read_text", "search_text"}
        for entry in trace
    )
    names_only_missing_column = (
        has_successful_text
        and re.search(r"\bcolumn\b", rationale, re.IGNORECASE) is not None
        and re.search(r"\b(?:text|document|content|source text)\b", rationale, re.IGNORECASE) is None
    )
    if names_only_missing_column:
        return "refusal_ignored_successful_text"
    return None


def _trace_context(trace: list[dict[str, Any]]) -> tuple[str, list[str] | None]:
    question = ""
    population_files = None
    for entry in trace if isinstance(trace, list) else []:
        if not isinstance(entry, dict):
            continue
        if not question and isinstance(entry.get("task_query"), str):
            question = entry["task_query"]
        files = entry.get("population_files")
        if population_files is None and isinstance(files, list) and all(isinstance(path, str) for path in files):
            population_files = files
    return question, population_files


def termination_reason(trace: list[dict[str, Any]], final_answer: str, question: str | None = None,
                       *, population_files: list[str] | None = None) -> str | None:
    """Explain why an otherwise-consensus answer conflicts with verified rate evidence."""
    trace_question, trace_population = _trace_context(trace)
    question = question if question is not None else trace_question
    population_files = population_files if population_files is not None else trace_population
    verified = compute_rate(trace, question, population_files=population_files)
    if verified is not None:
        answer = _numeric_answer(final_answer)
        if answer is None:
            return "refusal_despite_evidence"
        try:
            expected = Decimal(verified["answer"])
        except InvalidOperation:
            return "refusal_despite_evidence"
        if answer != expected:
            return "verified_rate_mismatch"
    return _refusal_specificity_reason(trace, final_answer)


def can_terminate(trace: list[dict[str, Any]], final_answer: str, question: str | None = None,
                  *, population_files: list[str] | None = None) -> bool:
    """Veto consensus only when complete verified rate evidence resolves the answer."""
    if question is None:
        question, trace_population = _trace_context(trace)
        if population_files is None:
            population_files = trace_population
    return termination_reason(
        trace, final_answer, question, population_files=population_files
    ) is None

