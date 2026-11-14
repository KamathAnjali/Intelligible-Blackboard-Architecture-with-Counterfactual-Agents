# Retrospective rollback contract

Agent-side interface v1, 18 October 2026. Reviewed against `origin/integration-main` at `ce3993f` and its shared board contract. This document records the agent contribution and the unresolved integration requirements; it does not claim implementation of replay or approval by the other workstreams.

## Inputs

`agents/counterfactual.py` defines `RollbackRequest`, the `CounterfactualSandbox.simulate()` protocol, and `create_sandbox()` for copying the prefix before a selected contribution.

| Input | Meaning |
| --- | --- |
| `task` | Original task text. The board state does not contain this text, so the caller must supply it. |
| `source_state` | Detached `board.get_state()` snapshot containing ordered entries, agent IDs/personas/model names/active flags, status, and deadlock records. |
| `cutoff_entry_id` | Original contribution selected for replacement, not the end of the subsequent deadlock. It must exist in the live history. |
| `target_agent_id` | Registered, active author of the selected contribution. |
| `alternative_instruction` | Instruction to reconsider that contribution using the original facts. It must not contain a reference answer. |
| `max_replay_turns` | Maximum number of newly generated sandbox turns, including the alternative. Default 6. |
| `max_alternatives` | Maximum candidate timelines. Default 1. |
| `max_retries` | Regeneration budget per turn, shared with explanation checking. Default 2. |

The replay also records model tag/digest, inference options, prompt texts/hashes, agent order, failures, and all generation/review usage for reproducibility. The normal active registration order is retained, and downstream replay starts after the author of the alternative. A future priority-based scheduler must explicitly supply its ordering policy rather than silently substituting one.

The frozen dataclass protects field assignment, not mutation of nested Pydantic objects. A future implementation must deep-copy the supplied state and entries before editing a sandbox. Expected answers and official evaluator reference data stay outside model context.

## Cutoff and replay semantics

1. The source history and its IDs remain unchanged.
2. Shared `get_history_slice()` and `fork_simulation_blackboard()` currently include the cutoff entry. A replacement experiment must retain only entries strictly before that entry in its working sandbox. For the first entry, the retained prefix is empty.
3. Generate a new alternative with a fresh entry ID. Keep the original cutoff ID as provenance in `RollbackOutcome.original_entry_id`. Do not retain the old cutoff contribution alongside its replacement or reuse downstream live responses.
4. Rebuild target references against entries that exist in the alternative timeline. New sandbox entries have `is_counterfactual_sim=True`; inherited prefix entries keep their original metadata.
5. Regenerate subsequent turns using the new history, the regular persona/PXP prompts, and explanation checks. Reset deadlock-window state at the intervention. Stop on the sandbox's evaluated consensus/deadlock, a generation error, or the configured budget.

## Outputs and evaluation

`RollbackOutcome` contains the original cutoff ID, an optional candidate entry, the tuple of newly generated replay entries, a terminal outcome (`consensus`, `deadlocked`, `turn_limit`, or `error`), an optional `delta_score`/`score_name`, and an optional failure reason. The candidate is the first element of `replayed_entries` when it exists. An error must preserve any partial replay.

An internal consensus-progress score is separate from correctness. Its definition and baseline must be recorded before interpreting a numeric delta. Without an evaluator, both score fields are `None`; do not substitute zero, a probability, or a fabricated improvement. Official benchmark scoring requires the appropriate independent evaluator. Generation, self-checks, rejected candidates, and replay calls all count toward cost.

Simulation returns a proposal and never applies it. The later live controller may submit a fresh `REVISE` through the scheduler after acceptance, referencing the original live entry and citing the corrected reasoning. Live history is append-only. A `CounterfactualResult` audit record reports the original entry, alternative, score or null, and whether adoption actually occurred.

## Shared integration requirements

| Area | Current state and required integration |
| --- | --- |
| `3-Dhruva` | The integrated board provides history slices, isolated forks and a nullable result score. The agent-side replay remains an interface sketch. |
| `integration-main` board | History copying/forking exists, but consensus ignores simulation-marked entries. A sandbox evaluation path must evaluate its own alternative timeline without polluting live classification. |
| Deadlock lifecycle | The board preserves `DEADLOCKED`, and the scheduler stops selecting agents. Recovery needs an explicit handoff and an agreed resume/reset operation. Do not bypass turn enforcement or edit private scheduler/board fields. |
| Scheduler state | Read access to active agent order, selected turn, and status is sufficient for the interface. A stable public recovery/debug API is still needed for integration. |
| UI and audit | Live and simulated entries must remain distinguishable. Simulation lineage, usage, and outcome should be available to the event/metrics layer. |

Sandbox preparation is implemented. Replay, evaluator integration, scheduler recovery, and adoption remain pending.
