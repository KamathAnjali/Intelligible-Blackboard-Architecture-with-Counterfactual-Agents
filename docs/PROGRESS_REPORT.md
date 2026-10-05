# Progress report

Updated 5 October 2026 for `integration-main`.

## Integrated sources

All four source branch tips are included through Git merge history. Existing source files, documentation, and data are retained.

| Branch | Included tip | Contribution |
| --- | --- | --- |
| `1-Anjali` | `fb79a7b` | Data contracts, board/storage, events, classification, and sandbox foundations. |
| `2-Lopez` | `9554aae` | Event-driven scheduling, turn enforcement, and scheduler lifecycle. |
| `3-Dhruva` | `6d7b9d8` | Day 9 agent workflows and the conversation archive. |
| `4-Tanisha` | `bc777ba` | UI/backend, ingestion, metrics, and benchmark infrastructure. |

## Implemented behavior

| Area | Current implementation |
| --- | --- |
| Board and storage | Pydantic contracts, synchronized mutations, JSON snapshots, replay export, and event subscriptions. Consensus requires matching RATIFY predictions from every active agent; a four-entry negative streak flags deadlock. |
| Scheduler | Uses the board registry, reserves round-robin turns, rejects submissions from the wrong agent, and stops on terminal board status. Board events can wake the orchestration loop through `wait_for_next_agent()`; the current conversation runner still uses `next_agent()`. |
| Local inference | Shared `qwen3:4b-instruct-2507-q4_K_M` model, context 4096, temperature 0, seed 42, and a 768-token generation cap. |
| Structured agents | Four personas share strict PEX/PXP schemas. A separate model call checks explanation support; malformed or rejected outputs trigger bounded retries before entry creation. |
| Conversations | Two-agent sessions and the three-agent seeded disagreement case submit entries through the scheduler. Reports contain prompts, raw responses, reviews, failures, usage, outcomes, and board checkpoints. |
| UI/backend | FastAPI health and conversation-start endpoints, WebSocket board streaming, React/D3 graph layouts, node inspection, history scrubbing, and estimated entry-text token totals. LIVE_TAP attaches to the backend's in-process conversation board; replay and mock sources remain available. |
| Ingestion and metrics | JSON/JSONL parsing supports the upstream KramaBench record shape. Synthetic trial modes produce CSVs; plotting requires official evaluator provenance. |
| Sandbox foundations | History slices and isolated board forks are implemented. Counterfactual audit records can carry a null score when no evaluator ran. Automated altered-history reasoning, scoring, and live adoption are pending. |

## Published conversation records

Five transcripts and five usable snapshots are tracked under `results/transcripts/` and `results/snapshots/`. Three runs completed with a recorded scheduler-consensus outcome; two contain partial checkpoints and retain their original `running` outcome. Consensus labels do not independently establish answer correctness.

Snapshots for `conversation-20260929T144759406083Z` and `conversation-20260929T144943919595Z` were recovered from the board state embedded in their transcripts. The original zero-byte snapshot files are retained in `results/snapshots/originals/`. All other published records are unchanged copies of their local originals.

The Day 9 records show an initial auditor correction labeled REFUTE, followed by prompt tuning and a repeated five-turn sequence ending at `C: insufficient information`. The two completed disagreement runs retain their original generation and explanation-review evidence.

New conversation checkpoints, Ollama reports, benchmark outputs, and raw dataset clones remain ignored; publication is a separate archive operation.

## Integration verification and remaining work

The merges completed without Git conflicts. Integration checks cover source ancestry, archive integrity, and absence of unresolved conflict markers. No additional runtime changes or runtime tests were performed for this merge.

The UI's token totals are estimates over prediction/explanation text, while transcript usage includes model generation and review calls. The checked-in replay fixture is synthetic. No official KramaBench performance results have been produced: the current trial runner uses synthetic data and keeps empirical modes disabled.

Remaining work includes runtime integration verification, counterfactual replay/scoring/adoption, and a real benchmark agent/evaluator adapter. The [README](../README.md) contains setup and run commands; the [milestones](PROJECT_MILESTONES.md) retain the planned work.
