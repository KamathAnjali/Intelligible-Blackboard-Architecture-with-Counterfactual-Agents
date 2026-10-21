"""Synthetic adapter tests: no Ollama or official benchmark score is used."""

import json
from pathlib import Path

import pytest

from agents.llm_client import OllamaClient
from bench.kramabench_adapter import (
    AgentWorkflowError,
    DatasetInputError,
    KramaBenchSystem,
    PipelineGenerationError,
    generate_pipeline_code,
    resolve_subset_files,
)
from bench.kramabench_data import (
    DataAccessError,
    DataToolWorkflowError,
    DatasetToolbox,
    ollama_data_tool_schema,
)


class FakeClient(OllamaClient):
    def __init__(self, responses):
        self.model = "fake-local-model"
        self.base_url = "http://user:secret@127.0.0.1:11434/api?token=private"
        self.responses = iter(responses)
        self.prompts = []

    def generate(self, prompt, system="", schema=None):
        self.prompts.append({"prompt": prompt, "system": system, "schema": schema})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return {
            "response": json.dumps(response),
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 11,
            "eval_count": 5,
            "wall_seconds": 0.01,
        }

    def check_explanation(self, task, answer):
        return {
            "raw": {"prompt_eval_count": 2, "eval_count": 1},
            "parsed": {"supported": True, "reason": "synthetic fixture"},
            "error": None,
        }


def pxp(tag, prediction="42"):
    return {"tag": tag, "prediction": prediction, "explanation": "The permitted CSV supports this result."}


def data_tool(action, **fields):
    return {"action": action, **fields}


def fixture_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "records.csv").write_text("value\n42\n", encoding="utf-8")
    return dataset


def fake_responses():
    return [
        data_tool("filter", file="records.csv", filters=[
            {"column": "value", "operator": "contains", "value": "not-present"}
        ]),
        data_tool("aggregate", file="records.csv", measures=[
            {"function": "max", "column": "value", "name": "observed_value"}
        ]),
        data_tool("done", summary="The complete file contains the requested value."),
        pxp("REVISE"),
        pxp("RATIFY"),
        pxp("RATIFY"),
        {"pipeline_code": "import pandas as pd\ndf = pd.read_csv('records.csv')\nprint(df['value'].iloc[0])"},
    ]


def test_system_uses_real_pxp_runner_and_returns_official_schema_without_gold(tmp_path):
    dataset = fixture_dataset(tmp_path)
    client = FakeClient(fake_responses())
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=client, output_dir=output, task_wall_clock_cap_seconds=77)
    system.process_dataset(dataset)
    evaluator_task = {
        "id": "fixture-1",
        "query": "Read records.csv and report the value.",
        "answer": "EVALUATOR_ONLY_SECRET",
        "answer_type": "string_exact",
        "data_sources": ["records.csv"],
        "reference_pipeline": "REFERENCE_PIPELINE_SECRET",
        "subtasks": [{"answer": "REFERENCE_SUBTASK_SECRET"}],
    }

    response = system.serve_query(
        query=evaluator_task["query"],
        query_id=evaluator_task["id"],
        subset_files=evaluator_task["data_sources"],
    )

    assert response["explanation"] == {"answer": "42"}
    assert response["pipeline_code"].startswith("import pandas")
    assert response["token_usage"] > 0
    assert len(client.prompts) == 7
    assert all(call["schema"] == ollama_data_tool_schema() for call in client.prompts[:3])
    assert all("records.csv" in call["prompt"] for call in client.prompts)
    assert any('"observed_value": "42"' in call["prompt"] for call in client.prompts)
    assert all("must not be sent" not in call["prompt"] for call in client.prompts)
    for call in client.prompts:
        for reserved in ("EVALUATOR_ONLY_SECRET", "REFERENCE_PIPELINE_SECRET", "REFERENCE_SUBTASK_SECRET"):
            assert reserved not in call["prompt"]
    records = list((output / "krama_task_records").glob("*.json"))
    assert len(records) == 1
    saved = json.loads(records[0].read_text(encoding="utf-8"))
    assert saved["status"] == "success"
    assert saved["data_operation_timeout_seconds"] == 120
    assert saved["task_wall_clock_cap_seconds"] == 77
    assert saved["task_wall_clock_elapsed_seconds"] > 0
    assert len(saved["data_operation_processing_seconds"]) >= 3
    assert saved["task_id"] == saved["query_id"] == "fixture-1"
    assert saved["benchmark_revision"] == "b2e0d77540263f8b6119f977fe79a8c4386b5a02"
    assert saved["pipeline_code"] == response["pipeline_code"]
    assert "secret" not in saved["base_url"] and "token" not in saved["base_url"]
    assert saved["permitted_files"][0]["path"] == "records.csv"
    assert saved["permitted_files"][0]["format"] == "csv"
    assert saved["permitted_files"][0]["tables"][0]["row_count"] == 1
    assert len(saved["data_access_trace"]) == 2
    assert saved["data_access_trace"][0]["status"] == "success"
    assert saved["data_access_usage"]["calls"] == 3
    assert saved["data_access_diagnostics"] == {
        "max_tool_invocations": 12,
        "max_workflow_iterations": 14,
        "max_generation_requests": 42,
        "tool_invocations": 2,
        "workflow_iterations": 3,
        "generation_requests": 3,
        "automatic_recovery_invocations": 0,
        "blocked_done_attempts": 0,
        "category_inspection_pending": False,
        "unresolved_filter_categories": [],
        "rate_validation_required": False,
        "rate_outcome_column": None,
        "unresolved_outcome_categories": [],
        "duplicate_empty_filter_requests": 0,
        "invalid_structured_response_attempts": 0,
        "uninspected_task_files": [],
        "workflow_counters": {
            "guard_activations": [],
            "guard_retries": 0,
            "aggregate_columns_validation_feedback": 0,
            "multi_file_coverage_rejections": 0,
            "repeated_invalid_request_stops": 0,
            "blocked_done_rejections": 0,
            "duplicate_filter_rejections": 0,
        },
        "completed": True,
    }
    assert saved["provider_token_usage"]["calls"] == saved["attempt_count"]
    assert saved["provider_token_usage"]["calls"] > saved["data_access_usage"]["calls"]
    transcript = json.loads(open(saved["conversation_transcript"], encoding="utf-8").read())
    transcript_json = json.dumps(transcript)
    assert "secret" not in transcript_json and "token=private" not in transcript_json
    assert transcript["task_id"] == "fixture-1"


def test_file_allowlist_resolves_patterns_without_sending_raw_files(tmp_path):
    dataset = fixture_dataset(tmp_path)
    (dataset / "private.txt").write_text("must not be sent", encoding="utf-8")
    paths = resolve_subset_files(dataset, ["records.csv"])
    with DatasetToolbox(dataset, paths) as toolbox:
        files = toolbox.catalog()["files"]
        assert len(files) == 1
        assert files[0]["tables"][0]["row_count"] == 1
        assert files[0]["sha256"]
        assert "content" not in files[0]
    assert [path.name for path in resolve_subset_files(dataset, ["records.csv"])] == ["records.csv"]


def test_file_allowlist_resolves_nested_case_variant_source_suffix(tmp_path):
    dataset = tmp_path / "dataset"
    source = dataset / "archive" / "CSVs" / "State MSA Identity Theft data"
    source.mkdir(parents=True)
    (source / "California.csv").write_text("value\n7\n", encoding="utf-8")
    unrelated = dataset / "other" / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "unrelated.csv").write_text("private\nsecret\n", encoding="utf-8")

    paths = resolve_subset_files(dataset, ["State MSA Identity Theft Data/*"])

    with DatasetToolbox(dataset, paths) as toolbox:
        files = toolbox.catalog()["files"]
        assert [item["path"] for item in files] == [
            "archive/CSVs/State MSA Identity Theft data/California.csv"
        ]
        assert all(item["path"] != "other/unrelated/unrelated.csv" for item in files)


@pytest.mark.parametrize("subset", [[], ["./"], ["../outside.csv"], [""]])
def test_invalid_or_overbroad_subset_is_rejected(tmp_path, subset):
    dataset = fixture_dataset(tmp_path)
    with pytest.raises(DatasetInputError):
        resolve_subset_files(dataset, subset)


def test_missing_files_fail_and_persist_failure_instead_of_fabricating_response(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=FakeClient([]), output_dir=output)
    system.process_dataset(dataset)
    with pytest.raises(DatasetInputError, match="no dataset files match"):
        system.serve_query("query", "missing-1", ["absent.csv"])
    record = next((output / "krama_task_records").glob("*.json"))
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["errors"][0]["type"] == "DatasetInputError"
    assert "answer" not in saved and "pipeline_code" not in saved


def test_data_tool_limit_failure_persists_generation_trace_and_usage(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"
    profile = data_tool("profile", file="records.csv")
    client = FakeClient([profile for _ in range(12)] + [data_tool("preview", file="records.csv")])
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    with pytest.raises(RuntimeError, match="12-tool-invocation limit"):
        system.serve_query("query", "tool-limit-1", ["records.csv"])

    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 13
    assert len(saved["generation_attempts"]) == 13
    assert saved["data_access_usage"] == {
        "calls": 13,
        "prompt_tokens": 143,
        "output_tokens": 65,
        "wall_seconds": 0.13,
    }
    assert len(saved["data_access_trace"]) == 13
    assert saved["data_access_trace"][-1]["action"] == "preview"
    assert saved["data_access_trace"][-1]["status"] == "error"
    assert saved["data_access_diagnostics"] == {
        "max_tool_invocations": 12,
        "max_workflow_iterations": 14,
        "max_generation_requests": 42,
        "tool_invocations": 12,
        "workflow_iterations": 13,
        "generation_requests": 13,
        "automatic_recovery_invocations": 0,
        "blocked_done_attempts": 0,
        "category_inspection_pending": False,
        "unresolved_filter_categories": [],
        "rate_validation_required": False,
        "rate_outcome_column": None,
        "unresolved_outcome_categories": [],
        "duplicate_empty_filter_requests": 0,
        "invalid_structured_response_attempts": 0,
        "uninspected_task_files": [],
        "workflow_counters": {
            "guard_activations": [],
            "guard_retries": 0,
            "aggregate_columns_validation_feedback": 0,
            "multi_file_coverage_rejections": 0,
            "repeated_invalid_request_stops": 0,
            "blocked_done_rejections": 0,
            "duplicate_filter_rejections": 0,
        },
        "completed": False,
    }
    assert saved["provider_token_usage"]["calls"] == 13
    assert saved["errors"][0]["type"] == "DataToolWorkflowError"
    assert "13 workflow iterations" in saved["errors"][0]["message"]


def test_multi_file_coverage_guard_retries_and_persists_workflow_counters(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "a.csv").write_text("value\n1\n", encoding="utf-8")
    (dataset / "b.csv").write_text("value\n2\n", encoding="utf-8")
    output = tmp_path / "system-output"
    client = FakeClient([
        data_tool("profile", file="a.csv"),
        data_tool("done", summary="Both selected sources are represented."),
        data_tool("profile", file="b.csv"),
        data_tool("done", summary="Each selected source was inspected."),
        pxp("REVISE", "The selected files contain values 1 and 2."),
        pxp("RATIFY", "The selected files contain values 1 and 2."),
        pxp("RATIFY", "The selected files contain values 1 and 2."),
        {"pipeline_code": "# Uses the selected CSV inputs."},
    ])
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    system.serve_query("Compare the values in the selected records.", "multi-file-coverage-1", ["a.csv", "b.csv"])

    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "success"
    counters = saved["workflow_counters"]
    assert counters["multi_file_coverage_rejections"] == 1
    assert counters["guard_retries"] == 1
    activation = next(item for item in counters["guard_activations"] if item["guard"] == "task_file_coverage")
    assert activation["uninspected_files"] == ["b.csv"]
    assert saved["data_access_diagnostics"]["uninspected_task_files"] == []


def test_repeated_invalid_file_selection_persists_recoverable_diagnostics(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"
    invalid = data_tool("profile", files=["private.csv"])
    client = FakeClient([invalid, invalid, invalid])
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    with pytest.raises(DataToolWorkflowError, match="repeated the same invalid file selection"):
        system.serve_query("query", "invalid-file-1", ["records.csv"])

    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 2
    assert saved["data_access_usage"]["calls"] == 2
    assert len(saved["data_access_trace"]) == 2
    assert all(entry["status"] == "error" for entry in saved["data_access_trace"])
    assert saved["data_access_diagnostics"]["workflow_iterations"] == 2
    assert saved["data_access_diagnostics"]["tool_invocations"] == 2
    assert saved["errors"][0]["type"] == "DataToolWorkflowError"
    assert "exact `path` shown in `allowlisted_files`" in saved["errors"][0]["message"]


def test_repeated_invalid_structured_request_persists_redacted_attempt_diagnostics(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"
    invalid = data_tool(
        "preview", file="records.csv", unexpected="field",
        api_key="sk-1234567890abcdefghijklmnopqrstuvwxyz",
    )
    client = FakeClient([invalid, invalid, invalid])
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    with pytest.raises(DataToolWorkflowError, match="repeated an identical invalid structured data request"):
        system.serve_query("Inspect the permitted file", "invalid-request-1", ["records.csv"])

    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert saved["attempt_count"] == 2
    assert saved["data_access_usage"]["calls"] == 2
    assert saved["data_access_diagnostics"]["generation_requests"] == 2
    assert saved["data_access_diagnostics"]["invalid_structured_response_attempts"] == 2
    diagnostics = saved["data_access_request_diagnostics"]
    assert len(diagnostics) == 2
    assert diagnostics[0]["rejected_request"] != ""
    assert diagnostics[0]["validation_errors"]
    assert diagnostics[1]["repeated_identical_count"] == 2
    serialized = json.dumps(saved)
    assert "sk-1234567890abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "[REDACTED]" in serialized
    assert len(saved["generation_attempts"]) == 2
    assert all("response" not in attempt for attempt in saved["generation_attempts"])
    assert all(attempt.get("response_sha256") for attempt in saved["generation_attempts"])
    assert saved["errors"][0]["type"] == "DataToolWorkflowError"


def test_adapter_persists_automatic_category_recovery_and_accounting(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "samples.csv").write_text(
        "County Description,Violation\nBarnstable,yes\nPlymouth,no\n",
        encoding="utf-8",
    )
    output = tmp_path / "system-output"
    empty_filter = data_tool("filter", file="samples.csv", filters=[{
        "column": "County Description", "operator": "contains", "value": "Massachusetts",
    }])
    premature_done = data_tool(
        "done", rate_outcome_column="Violation", summary="The percentage is zero."
    )
    client = FakeClient([
        empty_filter,
        premature_done,
        data_tool(
            "done", rate_outcome_column="Violation",
            summary="The empty filter is not a zero rate; recovered county and outcome categories are in the evidence.",
        ),
        pxp("REVISE", "50.000"),
        pxp("RATIFY", "50.000"),
        pxp("RATIFY", "50.000"),
        {"pipeline_code": "import pandas as pd\ndf = pd.read_csv('samples.csv')\nprint(df.shape[0])"},
    ])
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    system.serve_query(
        "What percentage of Massachusetts samples had a violation?",
        "empty-rate-1",
        ["samples.csv"],
    )

    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "success"
    assert saved["data_access_usage"]["calls"] == 3
    assert saved["data_access_trace"][0]["status"] == "success"
    empty_diagnostic = saved["data_access_trace"][0]["empty_filter_diagnostics"][0]
    assert {key: empty_diagnostic[key] for key in (
        "file", "table", "column", "operator", "attempted_value", "observed_values",
        "values_truncated", "preview_note",
    )} == {
        "file": "samples.csv",
        "table": "table_0",
        "column": "County Description",
        "operator": "contains",
        "attempted_value": "Massachusetts",
        "observed_values": ["Barnstable", "Plymouth"],
        "values_truncated": False,
        "preview_note": "Bounded distinct-value preview; it may omit additional categories.",
    }
    assert "not establish a zero count or rate" in empty_diagnostic["hint"]
    assert saved["data_access_diagnostics"]["unresolved_filter_categories"] == []
    assert saved["data_access_diagnostics"]["unresolved_outcome_categories"] == []
    assert saved["data_access_diagnostics"]["automatic_recovery_invocations"] == 2
    assert saved["data_access_diagnostics"]["tool_invocations"] == 3
    recovery = [item for item in saved["data_access_trace"] if item["action"] == "aggregate_recovery"]
    assert len(recovery) == 2
    assert all(item["status"] == "success" for item in recovery)
    assert recovery[1]["category_count_evidence"]["groups"] == [
        {"Violation": "yes", "category_count": 1},
        {"Violation": "no", "category_count": 1},
    ]


def test_phase_2_13_evidence_scope_and_rate_recovery_produce_answer_path(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    source = dataset / "water-body-testing-2013.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        stream.write("County Description,Violation\n")
        for index in range(15_388):
            county = "Barnstable" if index % 2 == 0 else "Plymouth"
            violation = "yes" if index < 738 else "no"
            stream.write(f"{county},{violation}\n")

    profile = data_tool(
        "profile", file="water-body-testing-2013.csv",
        columns=["County Description", "Violation"], limit=5,
    )
    empty_state_filter = data_tool(
        "filter", file="water-body-testing-2013.csv", table="table_0",
        filters=[{
            "column": "County Description", "operator": "contains", "value": "Massachusetts",
        }], limit=50,
    )
    invalid_redundant_aggregate = data_tool(
        "aggregate", file="water-body-testing-2013.csv", table="table_0",
        columns=["Violation"], group_by=["County Description"],
        measures=[{"function": "count", "column": "*"}],
        rate_outcome_column="Violation",
    )
    county_counts = data_tool(
        "aggregate", file="water-body-testing-2013.csv", table="table_0",
        group_by=["County Description"], measures=[{"function": "count", "column": "*"}],
        rate_outcome_column="Violation",
    )
    premature_done = data_tool("done", rate_outcome_column="Violation")
    complete = data_tool(
        "done", rate_outcome_column="Violation",
        summary="The task source has verified category counts and neutral category shares.",
    )
    computed_rate = round(738 / 15_388 * 100, 3)
    formatted_rate = f"{computed_rate:.3f}"
    client = FakeClient([
        profile,
        empty_state_filter,
        invalid_redundant_aggregate,
        empty_state_filter,
        county_counts,
        premature_done,
        complete,
        pxp("REVISE", formatted_rate),
        pxp("RATIFY", formatted_rate),
        pxp("RATIFY", formatted_rate),
        {"pipeline_code": "# Uses the allowlisted source data."},
    ])
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    response = system.serve_query(
        "What percentage (to 3 decimal places) of water samples collected from Massachusetts beaches during the 2013 bathing season exceeded bacterial standards, leading to temporary closures?",
        "environment-easy-1",
        ["water-body-testing-2013.csv"],
    )

    assert response["explanation"]["answer"] == formatted_rate == "4.796"
    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "success"
    assert saved["task_data_sources"] == ["water-body-testing-2013.csv"]
    assert saved["data_access_diagnostics"]["automatic_recovery_invocations"] == 1
    assert saved["data_access_diagnostics"]["rate_outcome_column"] == "Violation"
    recovery = next(
        item for item in saved["data_access_trace"]
        if item["action"] == "aggregate_recovery"
    )
    assert recovery["category_count_evidence"]["matched_rows"] == 15_388
    assert {
        item["Violation"]: item["category_count"]
        for item in recovery["category_count_evidence"]["groups"]
    } == {"yes": 738, "no": 14_650}

    transcript = json.loads(Path(saved["conversation_transcript"]).read_text())
    task_text = transcript["task"]
    assert '"data_sources":["water-body-testing-2013.csv"]' in task_text
    assert "task's data_sources" in task_text
    assert "count(*)" in task_text
    assert '"denominator_rows":15388' in task_text
    assert '"value":"yes","count":738' in task_text
    assert "4.795944892" in task_text
    assert "Category percentages are neutral" in task_text
    assert "agent_summary" not in task_text
    assert "No rows matched" not in task_text
    assert '"answer":"4.796"' in task_text
    assert saved["verified_rate_calculation"]["numerator"] == 738
    assert saved["verified_rate_calculation"]["denominator"] == 15_388
    expected_fact_entries = sum(
        item.get("status") == "success" and "category_count_evidence" in item
        for item in saved["data_access_trace"]
    )
    assert len(saved["board_evidence_entries"]) == expected_fact_entries
    assert all(entry["tag"] == "FACT" for entry in saved["board_evidence_entries"])
    assert all('"count":' in entry["prediction"] for entry in saved["board_evidence_entries"])
    assert saved["answer"] == formatted_rate


def test_verified_rate_falls_back_to_deterministic_answer_when_agents_cannot_finish(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "measurements.csv").write_text(
        "Threshold violation\nyes\nyes\nno\nno\n", encoding="utf-8"
    )

    def failed_runner(*args, **kwargs):
        assert kwargs["initial_facts"]
        assert kwargs["termination_trace"]
        return {
            "outcome": "error",
            "board": {"entries": []},
            "turns": [{"attempts": [{"raw": {
                "prompt_eval_count": 7, "eval_count": 3, "wall_seconds": 0.01,
            }}]}],
            "failures": [{"category": "turn_error", "error": "review rejected both candidates"}],
            "workflow_counters": {"guard_activations": [], "guard_retries": 0},
        }, tmp_path / "conversation-transcript.json"

    system = KramaBenchSystem(
        client=FakeClient([
            data_tool(
                "aggregate", file="measurements.csv", table="table_0",
                group_by=["Threshold violation"],
                measures=[{"function": "count", "column": "*", "name": "category_count"}],
                rate_outcome_column="Threshold violation",
            ),
            data_tool("done", rate_outcome_column="Threshold violation", summary="Counts inspected."),
        ]),
        output_dir=tmp_path / "output",
        conversation_runner=failed_runner,
        pipeline_generator=lambda *args, **kwargs: {
            "pipeline_code": "# generated in the offline fixture", "attempts": [],
        },
    )
    system.process_dataset(dataset)

    response = system.serve_query(
        "What percentage (to 3 decimal places) of samples exceeded the threshold?",
        "verified-rate-fallback",
        ["measurements.csv"],
    )

    assert response["explanation"]["answer"] == "50.000"
    saved = json.loads(next((tmp_path / "output" / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "success"
    assert saved["answer_source"] == "verified_data_calculation_fallback"
    assert saved["agent_answer"] is None
    assert saved["agent_conversation_outcome"] == "error"
    assert saved["agent_conversation_failures"][0]["category"] == "turn_error"
    assert saved["verified_rate_calculation"] == {
        "answer": "50.000",
        "unit": "percent",
        "numerator": 2,
        "denominator": 4,
        "outcome_column": "Threshold violation",
        "outcome_value": "yes",
        "files": ["measurements.csv"],
        "tables": ["table_0"],
        "precision": 3,
        "basis": "complete, unfiltered category counts from the task-selected data files",
    }
    assert saved["provider_token_usage"]["calls"] == 3


def test_refusal_consensus_is_rejected_then_retried_with_verified_board_evidence(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "observations.csv").write_text(
        "County Description,Threshold violation\n"
        "North County,yes\nNorth County,yes\nSouth County,no\nSouth County,no\n",
        encoding="utf-8",
    )
    client = FakeClient([
        data_tool("profile", file="observations.csv", columns=["County Description", "Threshold violation"]),
        data_tool("filter", file="observations.csv", filters=[{
            "column": "County Description", "operator": "contains", "value": "Example State",
        }]),
        data_tool("done", rate_outcome_column="Threshold violation", summary="The filter returned no rows."),
        data_tool("done", rate_outcome_column="Threshold violation", summary="The recovery counts cover the selected source."),
        pxp("REVISE", "insufficient information"),
        pxp("RATIFY", "insufficient information"),
        pxp("RATIFY", "insufficient information"),
        pxp("REVISE", "50.000"),
        pxp("RATIFY", "50.000"),
        pxp("RATIFY", "50.000"),
        {"pipeline_code": "# Safe, generated source; not executed."},
    ])
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    result = system.serve_query(
        "What percentage (to 3 decimal places) of samples from Example State exceeding a threshold were recorded?",
        "guard-recovery-1",
        ["observations.csv"],
    )

    assert result["explanation"]["answer"] == "50.000"
    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "success"
    assert saved["workflow_counters"]["refusal_despite_evidence_retries"] == 1
    activation = next(
        item for item in saved["workflow_counters"]["guard_activations"]
        if item.get("reason") == "refusal_despite_evidence"
    )
    assert activation["reason"] == "refusal_despite_evidence"
    assert activation["retry_count"] == 1
    assert len(saved["board_evidence_entries"]) == 2
    transcript = json.loads(Path(saved["conversation_transcript"]).read_text())
    facts = [entry for entry in transcript["board"]["entries"] if entry["tag"] == "FACT"]
    assert len(facts) == 2
    reject = next(entry for entry in transcript["board"]["entries"] if entry["tag"] == "REJECT")
    assert "refusal_despite_evidence" in reject["explanation"]
    assert any(turn["entry"]["prediction"] == "50.000" for turn in transcript["turns"])
    first_agent_turn = json.loads(client.prompts[4]["prompt"])
    assert any("Verified data-tool FACT" in entry["prediction"] for entry in first_agent_turn["history"])
    assert '"answer":"50.000"' in first_agent_turn["task"]


def test_ambiguous_population_or_outcome_keeps_category_rates_neutral(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "measurements.csv").write_text(
        "label,count\nA,1\nB,1\n", encoding="utf-8"
    )
    client = FakeClient([
        data_tool(
            "aggregate", file="measurements.csv", table="table_0",
            group_by=["label"], measures=[{"function": "count", "column": "*"}],
            rate_outcome_column="label",
        ),
        data_tool("done", rate_outcome_column="label", summary="The category counts are complete."),
        pxp("REVISE", "insufficient information: the named region is missing from the task scope."),
        pxp("RATIFY", "insufficient information: the named region is missing from the task scope."),
        pxp("RATIFY", "insufficient information: the named region is missing from the task scope."),
        {"pipeline_code": "# No unsupported subset is inferred."},
    ])
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=client, output_dir=output)
    system.process_dataset(dataset)

    response = system.serve_query(
        "What percentage of samples in an unspecified region had the adverse condition?",
        "ambiguous-rate-1",
        ["measurements.csv"],
    )

    assert response["explanation"]["answer"] == "insufficient information: the named region is missing from the task scope."
    # The adapter's saved conversation is the auditable prompt source.
    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    transcript = json.loads(Path(saved["conversation_transcript"]).read_text())
    task_text = transcript["task"]
    assert "preserve that uncertainty" in task_text
    assert "Category percentages are neutral" in task_text
    assert '"value":"A","count":1' in task_text
    assert '"value":"B","count":1' in task_text


def test_malformed_allowlisted_file_is_recorded_before_parser_failure(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "malformed.csv").write_text("a,b\n1,2,3\n", encoding="utf-8")
    output = tmp_path / "system-output"
    system = KramaBenchSystem(client=FakeClient([]), output_dir=output)
    system.process_dataset(dataset)
    with pytest.raises(DataAccessError, match="extra columns"):
        system.serve_query("inspect file", "bad-file-1", ["malformed.csv"])
    record = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert record["permitted_files"][0]["path"] == "malformed.csv"
    assert record["permitted_files"][0]["sha256"]
    assert record["permitted_files"][0]["parse_status"] == "pending"
    assert record["status"] == "failed"


def test_agent_nonconsensus_is_recorded_as_failure(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"

    def failed_runner(*args, **kwargs):
        return {"outcome": "turn_limit", "board": {"entries": []}, "turns": [], "failures": []}, tmp_path / "transcript.json"

    system = KramaBenchSystem(
        client=FakeClient([
            data_tool("aggregate", file="records.csv", measures=[{"function": "count", "column": "*"}]),
            data_tool("done", summary="Count complete."),
        ]), output_dir=output, conversation_runner=failed_runner
    )
    system.process_dataset(dataset)
    with pytest.raises(AgentWorkflowError, match="turn_limit"):
        system.serve_query("query", "timeout-1", ["records.csv"])
    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert saved["errors"][0]["type"] == "AgentWorkflowError"


def test_pipeline_generation_error_does_not_return_empty_or_fake_code(tmp_path):
    dataset = fixture_dataset(tmp_path)
    output = tmp_path / "system-output"
    client = FakeClient([
        data_tool("aggregate", file="records.csv", measures=[{"function": "count", "column": "*"}]),
        data_tool("done", summary="Count complete."),
        pxp("REVISE"), pxp("RATIFY"), pxp("RATIFY"), {"pipeline_code": "   "},
    ])
    system = KramaBenchSystem(client=client, output_dir=output, max_retries=0)
    system.process_dataset(dataset)
    with pytest.raises(PipelineGenerationError):
        system.serve_query("query", "no-code-1", ["records.csv"])
    saved = json.loads(next((output / "krama_task_records").glob("*.json")).read_text())
    assert saved["status"] == "failed"
    assert "pipeline_code" not in saved


def test_pipeline_generation_retries_syntax_error_without_executing_code():
    client = FakeClient([
        {"pipeline_code": "def ("},
        {"pipeline_code": "print(42)"},
    ])
    result = generate_pipeline_code(client, "task + allowed data", "42", "supported", max_retries=1)
    assert result["pipeline_code"] == "print(42)"
    assert len(result["attempts"]) == 2
    assert "not valid Python" in result["attempts"][0]["error"]


def test_process_dataset_requires_an_existing_nonempty_directory(tmp_path):
    system = KramaBenchSystem(client=FakeClient([]), output_dir=tmp_path / "out")
    with pytest.raises(DatasetInputError, match="does not exist"):
        system.process_dataset(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DatasetInputError, match="contains no files"):
        system.process_dataset(empty)
