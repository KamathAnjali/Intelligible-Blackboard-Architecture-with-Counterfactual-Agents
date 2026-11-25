# Benchmark dashboard demo (3:00)

This walkthrough uses saved benchmark attempts and their transcripts. Playback does not make model calls. The score is the evaluator's correctness result; the graph shows the recorded collaboration path.

## Selected runs

| Benchmark | Task and saved run | Result to show |
| --- | --- | --- |
| KramaBench | `environment-easy-1`, `cf-00` | Score 1.0; answer 4.796; 10 board posts |
| MSCoRe | `mscore-8a2c228c6108-1176`, `cf00` | Blackboard answer `不合理` scores 1.0; direct baseline `合理` scores 0.0; 3 board posts |
| MATH-500 | `test/algebra/2427.json`, `cf-66` | Answer 10 scores correct; 7 board posts |
| MedAgentBench | `task4_3`, `remaining`, `cf-00` | Answer 2.7; correct; 12 board posts |

MATH-500 covers 30 selected questions, not the full 500. KramaBench uses its recorded compatibility evaluator; MSCoRe and MedAgentBench use project-adapted evaluation. Keep these scope labels visible when presenting results.

**Why MATH-500 is included:** The project documentation names KramaBench, MSCoRe, and MedAgentBench as the main benchmark suites. In the saved cohorts selected for this demo, deadlocks and counterfactual replay examples are relatively sparse, so they provide fewer clear examples for comparing solo and blackboard outcomes. We added a 30-question MATH-500 cohort to make that comparison easier to inspect: solo scored 0/30, while the blackboard conditions scored 11/30 at 0%, 33%, and 50%, and 13/30 at 66%. This is a descriptive result for this selected cohort using the project's exact-answer check; it is not a full MATH-500 score or a statistical-significance claim.

## Timed narration

### 0:00-0:14 - Introduction

**On screen:** Benchmark dashboard with all four benchmark selectors visible.

**Say:** “We ran project cohorts on KramaBench, MSCoRe, and MedAgentBench. Their saved runs have relatively few deadlocks and counterfactual replays, so we added selected MATH-500 problems for a clearer solo-versus-blackboard comparison. Here we can inspect scores, answers, and recorded agent activity; replay does not make new model calls.”

### 0:14-0:52 - KramaBench

**On screen:** Select KramaBench, search `environment-easy-1`, open the `cf-00` attempt, and show its result and graph.

**Say:** “This KramaBench data task scores 1.0 with answer 4.796. The graph starts with evidence posts, then a proposal, followed by the agents ratifying that answer. Each row is an agent and each node is a posted board entry. This is the saved compatibility-evaluator result, not a claim about the full official benchmark.”

### 0:52-1:27 - MSCoRe

**On screen:** Select MSCoRe, search `mscore-8a2c228c6108-1176`, select `cf00`, and briefly point to the direct baseline row before showing the blackboard trace.

**Say:** “This task asks whether cutting construction supervision is reasonable when quality is the priority. The direct baseline answered ‘reasonable’ and scored 0.0. The blackboard run answered ‘not reasonable’ and scored 1.0. The trace shows the proposer’s answer, a verifier ratifying it, and the proposer’s final confirmation.”

### 1:27-2:02 - MATH-500

**On screen:** Select MATH-500, search `test/algebra/2427.json`, select `cf-66`, and show the score cards and seven-entry trace.

**Say:** “This is one of 30 selected MATH-500 problems in the new Qwen3 4B GPU cohort, not a full 500-question run. In this run, the blackboard reaches answer 10 and the project’s exact-answer check marks it correct. Across this sample, solo scored zero out of 30 and the 66 percent blackboard condition scored 13 out of 30; the other blackboard conditions scored 11 out of 30 each. That is a descriptive result for this cohort, not a statistical-significance claim. The seven posts show the proposal and subsequent agreement.”

### 2:02-2:41 - MedAgentBench

**On screen:** Select MedAgentBench, search `task4_3`, choose the `remaining · cf-00` run, then show the task evidence and trace.

**Say:** “Here the question asks for a recent magnesium measurement. The first proposal says no measurement was found. A challenger points back to the chart evidence, the second proposer revises the answer to 2.7, and the other agents ratify it. The saved answer scores correct. This is a useful example of the board making a correction visible.”

### 2:41-3:00 - Close

**On screen:** Return to the dashboard overview or leave the MedAgent trace visible.

**Say:** “Across the dashboard, the score tells us whether the evaluator accepted an answer, while the graph shows how the agents reached it. The displayed cohorts are project results with their evaluator and sample-size limits labeled.”

## Screenshot capture plan

Capture one still at the end of each benchmark segment: the selected condition, score/outcome, and graph should all be visible. Suggested names: `01-kramabench-environment-easy-1.png`, `02-mscore-8a2c228c6108-1176.png`, `03-math500-algebra-2427.png`, and `04-medagent-task4_3.png`.
