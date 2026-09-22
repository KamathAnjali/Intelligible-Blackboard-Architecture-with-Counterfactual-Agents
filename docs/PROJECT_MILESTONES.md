# Intelligible Blackboard Project: Weekly Milestones
| Checkpoint | Tangible result |
| --- | --- |
| End of Week 1 | Two local LLM agents can contribute to a shared blackboard, and their interaction can be inspected in a graph. |
| End of Week 2 | A complete three-agent baseline runs through the scheduler, board, live dashboard, and logging pipeline. |
| End of Week 3 | Counterfactual agents can replay an alternative history, use the result in the live session, and be compared with ordinary agents in a pilot experiment. |
| After Week 3 | The system is evaluated across the required benchmarks, and the final report explains the results and limitations. |

## Week 1 milestone: Working blackboard and first agent interaction

**By the end of Week 1, the team should be able to demonstrate two real local LLM agents exchanging structured predictions and explanations through the blackboard.** The interaction should be visible in the UI, either live or through replay of the actual session log.

### What should be ready

| Workstream | Combined deliverable |
| --- | --- |
| Student 1: Data Model & Storage | Agreed board and message schemas, input validation, an in-memory store, JSON snapshot save/load, and documented board APIs. |
| Student 2: Protocol & Scheduler | Agent registration, thread-safe board updates, PXP transition handling, a basic event-driven scheduler with round-robin turns, and initial consensus/deadlock detection. |
| Student 3: Agents & Counterfactual | Working local model connection, two personas, structured prediction/explanation output, validation and retry handling, and a recorded two-agent session. |
| Student 4: UI & Benchmarking | Backend/WebSocket and frontend scaffolds, a graph responding to events, a connection to real board history, a KramaBench sample parser, and a token-counter stub. |

### Team demonstration

1. Start a session with a simple task and register the two agents.
2. Let the scheduler drive agent turns and show their PXP-tagged entries appearing on the board.
3. Show one completed agreement sequence and the graph of that actual interaction.
4. Use a controlled disagreement sequence to demonstrate the deadlock flag.
5. Save and reload a snapshot, showing that the recorded board contents are preserved.

### Completion checks

- [ ] The agreed schema and tag definitions are documented and used by the board, agents, and UI.
- [ ] Validation rejects malformed entries, unknown agents, and nonexistent target entries; the planned unit tests pass.
- [ ] Snapshot tests cover an empty board, a populated board, and a corrupted file.
- [ ] At least one real two-agent interaction is saved with predictions, explanations, tags, and a terminal outcome.
- [ ] The graph displays a real session or its recorded replay. Replayed sessions are labelled as replay.
- [ ] The KramaBench parser loads a documented sample into the internal task format; the number loaded is recorded.
- [ ] The integrated code is merged and tagged for Week 1, with setup instructions and a short failure/known-issues log.

**Completion evidence:** runnable demo, test results, session log, JSON snapshot, graph view, and the Week 1 release tag.

Live UI streaming, accurate token totals, and counterfactual execution are later milestones. A token-counter stub and graph replay satisfy the Week 1 schedule.

## Week 2 milestone: Integrated baseline with live observability

**By the end of Week 2, the team should have a complete baseline system in which three agents work on a task, the scheduler manages their turns, and the dashboard shows the evolving interaction and token use in real time.** The interface for the upcoming counterfactual module should also be agreed.

### What should be ready

| Workstream | Combined deliverable |
| --- | --- |
| Student 1: Data Model & Storage | Board event publication, documented intelligibility classification, snapshot/replay support, and the final rollback data contract. |
| Student 2: Protocol & Scheduler | A documented priority policy, concurrency checks, a debug API for the inspector, and integration with the rollback interface. |
| Student 3: Agents & Counterfactual | A frozen library of 3–4 personas, explanation-support self-checks, three-agent runs, a catalogue of disagreement/deadlock cases, and the sandbox interface stub. |
| Student 4: UI & Benchmarking | Live event streaming, tag colours, active-agent/scheduler visibility, a history inspector, bottleneck alerts, real token counts, and task ingestion connected to session execution. |

### Team demonstration

Run the same five selected KramaBench tasks through the integrated system:

**Task ingestion → scheduler → agents → board → recorded outcome**, with the dashboard updating throughout.

Show a completed session, a known disagreement/deadlock case, the token tally, and manual navigation through session history. These five runs establish integration; solving all five correctly is not a Week 2 completion requirement.

### Completion checks

- [ ] All five task runs have saved histories and explicit outcomes: agreement, deadlock, iteration/time limit, or an execution failure with its reason.
- [ ] The UI receives actual board events live and its displayed state agrees with the backend.
- [ ] History navigation changes the inspected view without changing the live board.
- [ ] Tests with four or more simultaneous writes show no lost entries or corrupted state.
- [ ] Token totals use recorded model-call usage and include retries/self-check calls, rather than placeholder values.
- [ ] The scheduled easy synthetic cases achieve the workbook's target of agreement on at least half; record correctness separately where reference answers exist.
- [ ] Consensus and intelligibility rules are documented and checked against representative traces. Repeated agreement messages from one agent cannot impersonate agreement by all agents.
- [ ] All four students have reviewed the rollback contract, including the historical cutoff, board snapshot, relevant agent context, simulation output, and proposed live update.
- [ ] The full-pipeline fixes, prompt library, deadlock scenarios, and Week 2 demonstration are integrated and documented.

**Completion evidence:** five session logs, a working live dashboard, concurrency and integration test results, token records, deadlock examples, and the agreed rollback contract.

The sandbox remains an interface stub at this checkpoint, as assigned in the Excel. Any unavailable benchmark data or execution capability must be recorded explicitly; merely loading a question does not establish that its benchmark task was executed correctly.

## Week 3 milestone: Counterfactual recovery and pilot comparison

**By the end of Week 3, the team should be able to demonstrate an agent responding to a deadlock by trying an alternative earlier contribution in an isolated replay, assessing the result, and using it to guide a new contribution to the live session.** The dashboard should display both histories, and the pilot should compare all four counterfactual configurations.

### What should be ready

| Workstream | Combined deliverable |
| --- | --- |
| Student 1: Data Model & Storage | Isolated simulation state, preservation of live history, isolation tests, per-trial outcome logs, and saved ablation results. |
| Student 2: Protocol & Scheduler | Deadlock-to-counterfactual triggering, configurable counterfactual participation, a deadlock injection harness, and scheduling/load checks across configurations. |
| Student 3: Agents & Counterfactual | Historical replay with an altered tag/explanation, explicit delta scoring, adoption through prompt/context or voting-weight adjustment, and a controlled enabled-versus-disabled test. |
| Student 4: UI & Benchmarking | Live-versus-simulation panels, per-agent/session token views, an automated trial runner, CSV output, comparison charts, and a rehearsed walkthrough. |

### Team demonstration

1. Run a controlled case that produces a deadlock with ordinary agents.
2. Run that case with counterfactual capability enabled.
3. Show the earlier contribution selected for revision and the isolated alternative history.
4. Show regenerated downstream agent responses and the score used to assess the alternative.
5. Show the resulting adjustment and a new contribution in the live session.
6. Demonstrate recovery on at least one controlled case, then display the pilot comparison charts.

### Completion checks

- [ ] The sandbox changes one selected historical contribution and reruns subsequent agent turns within a defined limit. Changing a saved tag alone does not count as replay.
- [ ] Simulation cannot modify the original live history; branch entries are clearly identifiable in logs and the UI.
- [ ] The score and adoption rule are documented. An unhelpful alternative can be discarded, and an unrecovered session ends at a defined limit.
- [ ] The live session can continue after the deadlock trigger; a recoverable deadlock is distinguished from permanent termination.
- [ ] The same injected case has recorded runs with and without counterfactual capability, including a controlled recovery example.
- [ ] The runner supports 0%, approximately 33%, approximately 66%, and 100% counterfactual participation. With three AI agents, these mean 0, 1, 2, and 3 enabled agents.
- [ ] A dry run of 3–5 tasks per configuration is completed, followed by the scheduled pilot of 20–30 tasks per configuration, using the same selected tasks across conditions.
- [ ] Every pilot run produces a result record, including failures. Recorded metrics include outcome, iterations, deadlocks, intelligibility, and total tokens including simulation calls.
- [ ] Accuracy charts use an independent answer check and valid task inputs/execution. Any adapted tasks are labelled and reported separately from standard benchmark results.
- [ ] The graph, alternative timeline, alerts, history inspector, and token display work together in the final demo.
- [ ] The integrated release, pilot CSVs/charts, methods drafts, and walkthrough are ready for review.

**Completion evidence:** runnable counterfactual demo, isolation/recovery tests, paired case logs, pilot results and charts, integrated release, and methods drafts.

The controlled recovery example establishes that the mechanism works. Whether it improves performance across benchmark tasks is an experimental result; the pilot should report improvements, regressions, or no clear change honestly.

## After Week 3: Full evaluation and final research report

Weekly tests and the Week 3 pilot are part of implementation. This phase completes the broader evaluation requested in the project brief.

### 1. Finalise benchmark execution and scoring

- Complete and verify adapters for KramaBench, MSCoRe, and MedAgentBench, including their required data, tools/environments, and evaluators.
- Verify a small sample from each benchmark before launching the full study.
- Record benchmark versions, task IDs, any adaptations, and the exact success criteria. Keep reference answers outside agent prompts and counterfactual selection.

### 2. Freeze the comparison

- Fix the model/version, personas, number of agents, scheduler policy, task set, and stopping rules across the four conditions.
- Use the same held-out tasks across conditions and separate these from examples used to tune prompts or replay rules.
- Document which agents receive counterfactual capability; balance or rotate assignments so the result is not solely an effect of one persona.
- Define live and simulation budgets explicitly. Record both token use and elapsed time.

### 3. Run the study specified in the brief

- Run **500 distinct test instances per configuration across the selected benchmarks**, covering all three named benchmarks. State the allocation across datasets before running.
- With four configurations, this is **2,000 trial executions before repetitions**. Repeated runs of a task do not count as additional distinct tasks.
- Add repeated runs where feasible to measure variation. Preserve all outcomes, including timeouts, malformed outputs, and unsuccessful recoveries.

### 4. Analyse the requested outcomes

| Measure | What the report should establish |
| --- | --- |
| Correct convergence | How often the agents reach a unified, correct answer within the defined limits. |
| Intelligibility | How sessions distribute across the documented Strong, Ultra-Strong, and other classifications. |
| Total computation | How token consumption and elapsed time change when simulation, retries, and other model calls are included. |
| Deadlock recovery | How often deadlocks arise, how often recovery is attempted, and whether recovery produces a correct result. |

Report variation or uncertainty alongside aggregate results and examine representative successes and failures. Increased agreement alone does not demonstrate improved reasoning.

### 5. Complete the final deliverables

- [ ] Reproducible experiment configuration and run instructions.
- [ ] Saved results and charts for all configurations and benchmarks.
- [ ] A final research report covering the architecture, counterfactual mechanism, experimental method, results, limitations, and conclusions.
- [ ] A final demonstration showing the baseline, counterfactual replay, and evaluation findings.

## Small clarifications to the Excel schedule

These clarify completion without changing the weekly allocation:

1. Treat Week 1 snapshot work as basic save/load and Week 2 work as its integration with replay and inspection.
2. Treat Week 1 graph replay as acceptable; require actual live streaming by Week 2.
3. Give consensus, intelligibility, simulation scoring, and recovery explicit definitions before using them in reported metrics.
4. Implement one supported live adaptation method first; the brief allows either prompt/context adjustment or voting-weight adjustment.
5. Keep the Excel's 20–30-task experiment in Week 3 as the pilot. Complete the full benchmark study and paper afterwards.
