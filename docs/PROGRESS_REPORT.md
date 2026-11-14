# Progress report

Updated 19 October 2026 for `3-Dhruva`. This branch contains four personas, an explanation self-check, runnable two- and three-agent local inference paths, a four-case live sanity-check workload adapted from GSM8K and mSCoRe examples, controlled deadlock fixtures, and the Day 11 rollback interface sketch. The counterfactual mechanism and full product integration are still pending. The six-case results below document the earlier workload run on 18 October; they do not report results for the current four-case set.

## Implemented

| Area | Current behavior |
| --- | --- |
| Shared contract | Pydantic models define agents, PXP-tagged BoardEntry records, target references, UTC timestamps, deadlocks, and session state. Empty predictions and explanations are rejected. |
| Blackboard and storage | Registered agents can post entries to a lock-protected, chronological board. The board validates agent and target IDs. An in-memory store supports JSON snapshots. |
| Scheduler | Round-robin turns select an agent and stop after the board reports agreement or four consecutive `REFUTE`/`REJECT` tags. The conversation runner also has a configurable turn limit. |
| Local inference | Ollama uses `qwen3:4b-instruct-2507-q4_K_M` with context 4096, temperature 0, seed 42, and a 256-token output cap. Raw responses and timing are available. |
| Structured output | PEX requires `prediction` and `explanation`; PXP adds a tag. Strict validation rejects malformed, missing, extra, or blank fields. Up to five retries can be configured, with two by default. The same budget covers rejected explanation reviews. |
| Personas and mapping | Aggressive proposer, cautious verifier, evidence auditor, and counterexample challenger have distinct prompts and share the PEX/PXP contracts. Application code supplies agent ID, target ID, entry ID, timestamp, and simulation flag when it builds a BoardEntry. |
| Explanation self-check | A separate call reviews the prediction and explanation against the original task. Unsupported explanations or malformed reviews trigger bounded regeneration before an entry is created. Every review, failure, and generation attempt is retained; usage includes both generation and review calls. |
| Live sessions | The runner supports two agents or a seeded three-agent disagreement case. Each model agent receives the task and current board history. Validated entries pass through the scheduler; transcripts and snapshots preserve partial runs. |
| Additional sessions | `make conversation AGENTS=3` runs an unseeded three-agent task. `make sessions` runs four GSM8K/mSCoRe examples with independent per-agent agreement and expected-answer checks after inference. |
| Deadlock material | `make deadlocks` records two explicitly controlled negative-tag stalemates through the real board/scheduler. These make no model calls and are distinct from empirical model behavior. |
| Day 11 interface and freeze | `agents/counterfactual.py` defines the request, outcome, and sandbox protocol without implementing replay. Seven prompt files are frozen as v1 with recorded hashes and known limitations. |
| Demo | `make demo` checks a live three-turn `REVISE`, `RATIFY`, `RATIFY` sequence, matching predictions, one valid attempt per turn, and no recorded failures. |

The storage design uses an append-only entry list for chronological replay and a simple in-memory JSON store for short local sessions. No database service is required.

## Evidence and prompt revision

- Five initial PEX sample tasks returned the expected predictions: arithmetic, deduction, insufficient evidence, contradiction, and a constrained selection. This was a smoke check, not a benchmark.
- All four personas passed 12 live checks covering deduction, insufficient evidence, and contradiction. Every candidate and explanation review passed on its first attempt, with the expected prediction. The prompts distinguish existential from universal claims and reserve RATIFY for supported predictions and reasoning.
- The self-check rejected both an unsupported universal claim and an irrelevant explanation attached to a correct prediction. Controlled checks verified correction feedback, bounded rejection, malformed-review handling, and preservation of the board and failure records.
- On 29 September 2026, the new challenger generated a self-checked BoardEntry through WSL, and `make demo RETRIES=0` completed `REVISE`, `RATIFY`, `RATIFY` with `yes`. The demo used six model calls: one generation and one review per turn. Its local record is `results/conversations/conversation-20260929T075505206911Z/transcript.json`.
- On 30 September 2026, a seeded false claim about ticket stamps produced three-agent disagreement. The first run reached the correct answer in five turns but the auditor mislabeled its correction `REFUTE`. After clarifying the `REVISE` rule, the repeated run followed `REVISE`, `REVISE`, `RATIFY`, `RATIFY`, `RATIFY` and ended at `C: insufficient information`. All four model turns passed their explanation checks on the first attempt, with no recorded failures. The local log is under `results/conversations/`.
- On the RX 6800S, a prior Windows Vulkan check reported the model fully on GPU and about 54 output tokens per second on a fixed prompt; the earlier CPU baseline was about 16. Placement and latency vary by machine and session.

Generated transcripts and inference reports are excluded from Git. They contain raw responses, explanation reviews, attempts, total call/token usage, timing, prompt settings, entries, and the final board state.

## Day 10 and Day 11 checkpoint

The six-case live-session batch completed on 18 October with **6/6 unanimous, expected-answer consensuses** (target: at least 3/6). Every case used three real model actors, an unseeded first contribution, and four posted turns. The check independently inspected each active actor's latest RATIFY prediction, rather than relying only on the older board's consensus heuristic. Accepted explanations were also inspected against each task's stated facts.

| Case | Expected prediction | Result | Model calls | Rejected attempts |
| --- | --- | --- | --- | --- |
| `badge_deduction` | `yes` | Correct three-agent consensus | 8 | 0 |
| `inventory_total` | `17` | Correct three-agent consensus | 10 | 1 |
| `closed_bridge` | `no` | Correct three-agent consensus | 8 | 0 |
| `quantifier_gap` | `insufficient information` | Correct three-agent consensus | 8 | 0 |
| `conflicting_notes` | `inconsistent` | Correct three-agent consensus | 8 | 0 |
| `number_constraint` | `5` | Correct three-agent consensus | 8 | 0 |

Settings: Qwen3 4B Q4_K_M, Ollama 0.35.0, context 4096, temperature 0, seed 42, output cap 256, at most six turns and two retries per turn. Model placement was CPU for this run. The 50 generation/review calls used 28,858 prompt tokens and 1,761 output tokens, with 353.730 summed request-wall seconds. Prompt-token counts include repeated context and are not a count of unique input text. These are small synthetic results, not official benchmark scores.

The one rejected inventory candidate returned `Well` and an unsupported insufficient-information explanation. The reviewer rejected it, and the next candidate returned `17`. No failure was hidden or excluded from the six-case denominator. No natural protocol deadlock occurred in this batch.

The [published compact live log](../results/transcripts/session-checks-20261018T141752919242Z.json) includes all six cases, prompt hashes, usage, failures, and latest per-agent votes. Full raw transcripts, reviews, and snapshots remain local under `results/sessions/session-checks-20261018T141752919242Z/` and are Git-ignored.

Two controlled fixtures were verified on 18 October: `missing_premise` and `contradictory_premises`. Each produced five entries (`REVISE`, `REFUTE`, `REJECT`, `REFUTE`, `REJECT`), one deadlock event, and a stopped scheduler with zero model calls. Their [published fixture log](../results/transcripts/deadlock-fixtures-20261018T141802737398Z.json) contains both complete board states. The [deadlock catalogue](deadlock-scenarios.md) distinguishes these fixtures from naturally occurring model failures.

The [rollback contract](rollback-contract.md) records the agent-side inputs, outputs, exclusive replacement prefix, regenerated downstream history, nullable scoring, and append-only live adoption requirements. It was reviewed against `origin/integration-main` at `ce3993f`. Shared scheduler recovery and simulation-classification changes remain integration requirements; the contract does not claim that those are implemented or approved by other contributors.

The [prompt-library v1 notes](prompt-library-v1-notes.md) freeze all seven current prompt texts, record their fingerprints and known failure cases, and preserve the branch's 256-token cap as an explicit experiment setting. No prompt wording was changed. The user's existing philosophy default task remains in `conversation.py`; the live workload uses its own fixed cases.

Verification: all 18 existing conversation checks passed, both controlled deadlock reproductions succeeded, the interface imports successfully, and all seven prompt fingerprints match the frozen notes. No unit tests were added. Replay, scoring, and live adoption are intentionally absent from the interface sketch.

Day 10's local run/documentation deliverables and Day 11's interface/prompt-freeze deliverables are complete. Shared contract acceptance and the outstanding board/scheduler recovery interfaces remain coordination work before Week 3 integration.

## Current limits

- The board's agreement status is based on recent RATIFY tags. It does not independently prove that distinct agents agree on the same reasoning or that a prediction is correct. The runner flags different RATIFY prediction text for review. The self-check can repeat the same model's mistakes; independent correctness and semantic evaluation remain required.
- The scheduler does not enforce that `submit_entry()` receives the agent selected by `next_agent()`. The current runner follows that order, but the shared interface needs turn ownership checks before broader integration.
- The current branch has no connected UI, benchmark execution pipeline, or counterfactual replay. Counterfactual fields in the data model are placeholders for planned behavior.
- The two-agent demonstration covers a simple deductive task. It does not establish recovery from disagreement, benchmark accuracy, or the proposed counterfactual benefit.

The next integration steps are bringing the newer shared board/scheduler APIs into this branch, the five-task real KramaBench pipeline checkpoint, and isolated counterfactual replay before the planned evaluations. `integration-main` already contains the stronger turn checks and board-fork helpers, but this work did not merge or modify that branch. The [milestones](PROJECT_MILESTONES.md) describe those checkpoints.
