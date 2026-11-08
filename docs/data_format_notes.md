# KramaBench data format notes

KramaBench is an end-to-end data-science agent benchmark. Its workload files contain JSON arrays of task records. The upstream repository describes records with fields such as `id`, `query`, `answer`, `answer_type`, `data_sources`, and `subtasks`; see the [official KramaBench repository](https://github.com/mitdbg/KramaBench).

## Observed task fields and mapping

| KramaBench field | Internal field | Mapping |
| --- | --- | --- |
| `id` | `task_id` | Preserved as a string. |
| `query` | `task_text` | Required prompt text. |
| `answer` | `expected_answer` | Preserved as a string for evaluation. |
| `subtasks` | `reference_steps` | Descriptive subtask text and expected results are retained; the parser does not invent PXP tags from subtask order. |
| `answer_type`, `data_sources`, `runtime`, `deepresearch_subset` | `metadata` | Preserved when present. |
| Missing domain/difficulty fields | `domain`, `difficulty` | Derived conservatively from the task ID, or defaulted to `general` and `medium`. |

The parser also accepts the project's earlier normalized fields (`task_id`, `problem_statement`, `expected_answer`, `domain`, `difficulty`, `ground_truth_reasoning_steps`, and `metadata`). It reads JSON arrays and JSONL records and can load at most `N` valid tasks.

## Parser entry points

- `parse_krama_record(dict)` parses one object.
- `parse_krama_json(str)` parses one JSON object string.
- `load_krama_dataset(path, n=N)` loads JSON or JSONL data and skips malformed records with a warning.

Example:

```bash
python -m bench.ingest.krama_parser --file path/to/workload.json --n 5 --print-tasks
```

`bench/data/recorded_session.json` is a UI replay fixture, not a KramaBench workload. The parser's no-file self-check uses synthetic examples. An upstream task file should be inspected and run through the parser before it is used in benchmark results.

On 2026-10-02, the parser was rerun against the canonical raw workload URL `https://raw.githubusercontent.com/mitdbg/KramaBench/main/workload/legal.json` (downloaded to `/private/tmp/mitdbg_krama_legal.json`): 30 tasks loaded, 0 skipped. The Git-ignored checkout under `bench/data/krama_raw/` was not used as canonical provenance; its configured `origin` is `Unsupervisedcom/KramaBench`, so its earlier one-record parse does not establish that it came from the requested upstream.
