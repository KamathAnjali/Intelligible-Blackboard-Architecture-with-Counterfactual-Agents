# Benchmark Raw Data Directory (`bench/data/`)

> [!IMPORTANT]
> **Read-Only Directory**: Never write output data or code artifacts into `bench/data/`. This directory is strictly reserved for read-only benchmark source material and local clones.

---

## Benchmark Sources & Setup Instructions

### 1. KramaBench (Used from Week 1 onward)
- **Repository**: [https://github.com/mitdbg/KramaBench](https://github.com/mitdbg/KramaBench)
- **Clone command**:
  ```bash
  git clone https://github.com/mitdbg/KramaBench.git bench/data/krama_raw
  cd bench/data/krama_raw && pip install -e .
  ```
- **Structure**: Task records are JSON in `workload/`. The upstream record shape includes `id`, `query`, `answer`, `answer_type`, `data_sources`, and `subtasks`. See [`docs/data_format_notes.md`](../../docs/data_format_notes.md) for the adapter mapping. The upstream repository also contains a large data directory; only clone it when the task requires those source files.

---

### 2. MSCoRe (Post-Week-3 Full Study Only)
- **Repository**: [https://github.com/D3E0-source/MSCoRE](https://github.com/D3E0-source/MSCoRE)
- **Clone command**:
  ```bash
  git clone https://github.com/D3E0-source/MSCoRE.git bench/data/mscore_raw
  ```
- **Structure**: Multi-step reasoning datasets ship directly inside the repository.

---

### 3. MedAgentBench (Post-Week-3 Full Study Only)
- **Repository**: [https://github.com/stanfordmlgroup/MedAgentBench](https://github.com/stanfordmlgroup/MedAgentBench)
- **Clone command**:
  ```bash
  git clone https://github.com/stanfordmlgroup/MedAgentBench.git bench/data/medagentbench_raw
  ```
- **Special Requirement**: Requires a local FHIR server to execute live simulated patient record interactions. Budget setup time before running full study trials.

---

## Directory Layout
```
bench/data/
├── README.md               # This documentation
├── recorded_session.json   # Recorded session snapshot for LOG_REPLAY mode
├── krama_raw/              # gitignored local clone
├── mscore_raw/             # gitignored local clone (post-W3)
└── medagentbench_raw/      # gitignored local clone (post-W3)
```
