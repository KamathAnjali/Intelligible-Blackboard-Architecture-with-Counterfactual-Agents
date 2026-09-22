# Benchmark Scoping Note — Week 3 Pilot Study

**Author:** Student 4 (UI & Benchmarking)  
**Date:** Week 3 Day 2  
**Scope:** Confirmation of benchmark dataset scope, sample size constraints, and ablation configuration matrix for the Week 3 pilot evaluation.

---

## 1. Dataset Scope

### In-Scope: KramaBench
- **Source**: `https://github.com/Unsupervisedcom/KramaBench` (ingested via [`bench/ingest/krama_parser.py`](file:///Users/tanishasinghal/Downloads/Reasoning_Agents_AI_Project/Intelligible-Blackboard-Architecture-with-Counterfactual-Agents/bench/ingest/krama_parser.py)).
- **Rationale**: KramaBench contains structured, multi-step symbolic and logical reasoning tasks with explicit ground-truth step annotations. This enables precise measurement of self-correction loops (`REVISE`), refutations (`REFUTE`), and counterfactual pruning (`REJECT`).

### Out-of-Scope (Deferred to Post-Week-3 Full Study)
- **MSCoRe** (`github.com/D3E0-source/MSCoRE`): Multi-step multi-domain benchmark — planned for full evaluation phase.
- **MedAgentBench** (`github.com/stanfordmlgroup/MedAgentBench`): Requires local live FHIR server container infrastructure — budgeted for dedicated post-Week-3 study deployment.

---

## 2. Sample Size & Pilot Design

- **Batch Size per Configuration**: **25 tasks** (within the agreed $20\text{--}30$ task pilot range).
- **Total Executions**: $25 \text{ tasks} \times 4 \text{ configurations} = \mathbf{100 \text{ trial runs}}$.
- **Pre-flight Dry Run**: $4 \text{ tasks} \times 4 \text{ configurations} = \mathbf{16 \text{ dry-run checks}}$ before the main batch.

---

## 3. Ablation Configurations (Counterfactual Participation Density)

With a standard 3-agent deliberative ensemble (Proposer, Verifier, Critic) plus Counterfactual Sandbox Agent:

| Config | CF Density | Enabled CF Agents | Description |
|---|---|---|---|
| `config-0pct` | $0\%$ | 0 of 3 agents | **Pure Baseline**: Standard blackboard without counterfactual rollback sandbox (deadlocks remain unresolved or trigger iteration limits). |
| `config-33pct` | $33\%$ | 1 of 3 agents | **Low Density**: Only the Critic is empowered to initiate sandbox branches upon refutation. |
| `config-66pct` | $66\%$ | 2 of 3 agents | **Moderate Density**: Both Proposer and Critic can spawn and explore counterfactual branches. |
| `config-100pct` | $100\%$ | 3 of 3 agents | **Full Counterfactual Blackboard**: All agents participate in speculative sandbox simulations to resolve disagreements. |

---

## 4. Evaluation Metrics & Output Files

- **Results CSV Directory**: `bench/results/week3_pilot/`
- **Output Files**:
  - `run_2026-09-22_config-0pct.csv`
  - `run_2026-09-22_config-33pct.csv`
  - `run_2026-09-22_config-66pct.csv`
  - `run_2026-09-22_config-100pct.csv`
- **Generated Charts**:
  - `accuracy_vs_density.png`
  - `intelligibility_vs_density.png`
  - `tokens_vs_density.png`
  - `ablation_summary_report.html` (interactive HTML dashboard)
