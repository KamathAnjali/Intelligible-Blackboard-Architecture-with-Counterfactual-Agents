# Progress report

Updated 27 September 2026 for `3-Dhruva`. This branch contains a runnable two-agent local inference path. The counterfactual mechanism and full product integration are still pending.

## Implemented

| Area | Current behavior |
| --- | --- |
| Shared contract | Pydantic models define agents, PXP-tagged BoardEntry records, target references, UTC timestamps, deadlocks, and session state. Empty predictions and explanations are rejected. |
| Blackboard and storage | Registered agents can post entries to a lock-protected, chronological board. The board validates agent and target IDs. An in-memory store supports JSON snapshots. |
| Scheduler | Round-robin turns select an agent and stop after the board reports agreement or four consecutive `REFUTE`/`REJECT` tags. The conversation runner also has a configurable turn limit. |
| Local inference | Ollama uses `qwen3:4b-instruct-2507-q4_K_M` with context 4096, temperature 0, seed 42, and a 256-token output cap. Raw responses and timing are available. |
| Structured output | PEX requires `prediction` and `explanation`; PXP adds a tag. Strict validation rejects malformed, missing, extra, or blank fields. Up to five retries can be configured, with two by default. |
| Personas and mapping | An aggressive proposer and cautious verifier have separate prompts. The model chooses only the tag and answer fields; application code supplies agent ID, target ID, entry ID, timestamp, and simulation flag when it builds a BoardEntry. |
| Live sessions | The runner registers both agents, gives each the original task and latest board history, posts validated entries through the scheduler, and saves a transcript and board snapshot. Errors preserve the partial run. |
| Demo | `make demo` checks a live three-turn `REVISE`, `RATIFY`, `RATIFY` sequence, matching predictions, one valid attempt per turn, and no recorded failures. |

The storage design uses an append-only entry list for chronological replay and a simple in-memory JSON store for short local sessions. No database service is required.

## Evidence and prompt revision

- Five initial PEX sample tasks returned the expected predictions: arithmetic, deduction, insufficient evidence, contradiction, and a constrained selection. This was a smoke check, not a benchmark.
- Both personas returned valid predictions on three factual tasks each. One earlier explanation used overly broad wording about individual properties. The revised PEX and PXP prompts now distinguish existential from universal claims; PXP also reserves RATIFY for supported predictions and reasoning.
- The first live two-agent session on a tulip deduction ended `REVISE`, `RATIFY`, `RATIFY` with `yes` from all turns. No malformed tags, extra fields, retries, or off-topic explanations were observed.
- After the prompt revision, `make demo` repeated that sequence successfully on 27 September 2026. All three replies passed on their first attempt, and the explanations applied the supplied rule. The local raw record is `results/conversations/conversation-20260927T121843796445Z/transcript.json`.
- On the RX 6800S, a prior Windows Vulkan check reported the model fully on GPU and about 54 output tokens per second on a fixed prompt; the earlier CPU baseline was about 16. Placement and latency vary by machine and session.

Generated transcripts and latency reports are excluded from Git. They contain raw responses, attempts, timing, prompt settings, entries, and the final board state.

## Current limits

- The board's agreement status is based on recent RATIFY tags. It does not independently prove that distinct agents agree on the same reasoning or that a prediction is correct. The runner flags different RATIFY prediction text for review; semantic review remains manual.
- The scheduler does not enforce that `submit_entry()` receives the agent selected by `next_agent()`. The current runner follows that order, but the shared interface needs turn ownership checks before broader integration.
- The current branch has no connected UI, benchmark execution pipeline, or counterfactual replay. Counterfactual fields in the data model are placeholders for planned behavior.
- The two-agent demonstration covers a simple deductive task. It does not establish recovery from disagreement, benchmark accuracy, or the proposed counterfactual benefit.

The next integration steps are to connect the real board history to the UI, define enforceable turn and consensus rules, add a third agent, and implement isolated counterfactual replay before running the planned evaluations. The [milestones](PROJECT_MILESTONES.md) describe those checkpoints.
