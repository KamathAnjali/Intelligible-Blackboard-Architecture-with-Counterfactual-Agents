# Task format sample

`sample.jsonl` contains five **format samples, not benchmark data**. They were written from the KramaBench record shape used by `bench/ingest/krama_parser.py`; they are not copied from task rows and contain no answers, reference reasoning, subtasks, or real benchmark IDs. The placeholder data-source names exist only to demonstrate metadata parsing and are not executable data inputs.

Load and save a hash-backed report with:

```sh
.venv/bin/python -m bench.ingest.krama_parser --input bench/data/sample.jsonl --report
```

Reports are written under `bench/data/load_reports/`. They describe parser coverage only and must never be treated as benchmark results or supplied as evidence to agents.
