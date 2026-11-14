"""Offline contract tests for verified category evidence and deterministic rates."""

from bench.evidence import (
    build_evidence_message,
    can_terminate,
    compute_rate,
    empty_filter_hint,
    termination_candidate,
    termination_reason,
)


def category_trace(*, column="Threshold violation", request_overrides=None, result_overrides=None):
    request = {
        "action": "aggregate",
        "file": "observations.csv",
        "table": "table_0",
        "filters": [],
        "group_by": [column],
        "measures": [{"function": "count", "column": "*", "name": "category_count"}],
        "rate_outcome_column": column,
    }
    request.update(request_overrides or {})
    result = {
        "files": ["observations.csv"],
        "tables": ["table_0"],
        "matched_rows": 15_388,
        "groups": [
            {column: "no", "category_count": 14_650},
            {column: "yes", "category_count": 738},
        ],
        "group_count": 2,
    }
    result.update(result_overrides or {})
    return [{
        "action": "aggregate_recovery",
        "status": "success",
        "request": request,
        "category_count_evidence": result,
    }]


def test_builds_a_blackboard_fact_from_complete_successful_counts():
    entry = category_trace()[0]
    message = build_evidence_message(entry)
    assert message.startswith("Verified data-tool FACT:")
    assert '"column":"Threshold violation"' in message
    assert '"count":738' in message
    assert '"total_rows":15388' in message


def test_rate_uses_task_relevant_affirmative_category_and_full_denominator():
    trace = category_trace()
    question = "What percentage (to 3 decimal places) exceeded the threshold?"

    rate = compute_rate(trace, question, population_files=["observations.csv"])

    assert rate == {
        "answer": "4.796",
        "unit": "percent",
        "numerator": 738,
        "denominator": 15_388,
        "outcome_column": "Threshold violation",
        "outcome_value": "yes",
        "files": ["observations.csv"],
        "tables": ["table_0"],
        "precision": 3,
        "basis": "complete, unfiltered category counts from the task-selected data files",
    }
    assert can_terminate(trace, "4.796", question)
    assert can_terminate(trace, "4.796%", question)
    assert not can_terminate(trace, "insufficient information", question)
    assert not can_terminate(trace, "4.795", question)
    assert termination_reason(trace, "insufficient information", question) == "refusal_despite_evidence"


def test_rate_is_not_derived_from_ambiguous_population_or_outcome():
    trace = category_trace()
    assert compute_rate(trace, "What share of observations were recorded?", population_files=["observations.csv"]) is None
    assert compute_rate(
        trace,
        "What percentage exceeded the threshold?",
        population_files=["another-file.csv"],
    ) is None
    assert not can_terminate(trace, "insufficient information", "What percentage of rows had an unknown result?")
    assert can_terminate(
        trace,
        "insufficient information: the requested outcome is missing from the task-selected source.",
        "What percentage of rows had an unknown result?",
    )


def test_refusal_requires_specific_missing_data_and_cannot_reject_text_for_lacking_a_column():
    trace = [{"action": "read_text", "status": "success", "result_sha256": "abc"}]
    column_only_refusal = termination_candidate(
        "insufficient information",
        "The required column 'forecast_error' does not exist in the file.",
    )
    assert not can_terminate(trace, column_only_refusal, "Compute the mean error from this document.")
    assert termination_reason(
        trace, column_only_refusal, "Compute the mean error from this document."
    ) == "refusal_ignored_successful_text"
    assert not can_terminate(trace, "insufficient information", "Compute the mean error from this document.")

    specific_text_refusal = termination_candidate(
        "insufficient information",
        "The observed measurement for the requested date is not present in the returned source text.",
    )
    assert can_terminate(trace, specific_text_refusal, "Compute the mean error from this document.")


def test_rate_rejects_filtered_incomplete_or_inconsistent_counts():
    question = "What percentage (to 3 decimal places) exceeded the threshold?"
    filtered = category_trace(request_overrides={"filters": [{"column": "region", "operator": "eq", "value": "north"}]})
    incomplete = category_trace(result_overrides={"matched_rows": 15_389})
    duplicate_group = category_trace(result_overrides={
        "groups": [
            {"Threshold violation": "no", "category_count": 14_650},
            {"Threshold violation": "no", "category_count": 738},
        ],
    })
    assert compute_rate(filtered, question) is None
    assert compute_rate(incomplete, question) is None
    assert compute_rate(duplicate_group, question) is None


def test_empty_filter_hint_uses_the_actual_column_and_value_without_claiming_zero_rate():
    hint = empty_filter_hint({
        "column": "County Description",
        "attempted_value": "Massachusetts",
        "observed_values": ["Barnstable", "Plymouth"],
    })
    assert "County Description" in hint
    assert "Massachusetts" in hint
    assert "Barnstable" in hint and "Plymouth" in hint
    assert "not establish a zero count or rate" in hint

