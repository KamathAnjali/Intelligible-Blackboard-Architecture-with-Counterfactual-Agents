import { useEffect, useMemo, useState } from 'react';
import { TaskTraceGraph, type TraceEntry } from './TaskTraceGraph';
import './BenchmarkDashboard.css';

type BenchmarkId = 'kramabench' | 'mscore' | 'medagentbench';
interface CatalogAttempt {
  run_id?: string;
  condition?: string;
  status?: string;
  outcome?: string;
  score?: number | null;
  prediction?: string;
  model?: string;
  evaluator_mode?: string;
  adapted?: boolean;
  failure_reason?: string;
  counterfactual_fraction?: number;
  counterfactual_simulations?: number;
  transcript_available?: boolean;
}
interface CatalogTask {
  task_id: string;
  split?: string;
  evaluator_mode?: string;
  adapted?: boolean;
  result_count: number;
  runnable_in_ui: boolean;
  attempts?: CatalogAttempt[];
}
interface BenchmarkInfo {
  id: BenchmarkId;
  name: string;
  task_source: string;
  task_count: number;
  result_count: number;
  tasks: CatalogTask[];
  run_support: string;
}
interface SavedDetail {
  run: CatalogAttempt;
  transcript_available: boolean;
  task?: string;
  outcome?: string;
  entries: TraceEntry[];
  counterfactual_events: Array<Record<string, unknown>>;
  counterfactual_attempts: unknown[];
  counterfactual_skips: unknown[];
}

const labels: Record<BenchmarkId, string> = {
  kramabench: 'KramaBench', mscore: 'MSCoRe', medagentbench: 'MedAgentBench',
};
const show = (value: unknown, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value);

function withSimulationEntries(detail: SavedDetail): TraceEntry[] {
  const entries = [...(detail.entries ?? [])];
  const seen = new Set(entries.map((entry) => String(entry.entry_id ?? '')));
  for (const event of detail.counterfactual_events ?? []) {
    const simulated = event.simulated_entry;
    if (simulated && typeof simulated === 'object') {
      const entry = simulated as TraceEntry;
      const id = String(entry.entry_id ?? '');
      if (!id || seen.has(id)) continue;
      seen.add(id);
      entries.push({ ...entry, is_counterfactual_sim: true });
    }
  }
  return entries;
}

export function BenchmarkDashboard({ onOpenKramaRunner }: { onOpenKramaRunner: (taskId: string) => void }) {
  const [benchmarks, setBenchmarks] = useState<BenchmarkInfo[]>([]);
  const [selected, setSelected] = useState<BenchmarkId>('mscore');
  const [taskSearch, setTaskSearch] = useState('');
  const [selectedTask, setSelectedTask] = useState<CatalogTask | null>(null);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [savedDetail, setSavedDetail] = useState<SavedDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/benchmark-dashboard/catalog')
      .then(async (r) => { if (!r.ok) throw new Error(`Benchmark catalog failed (${r.status})`); return r.json(); })
      .then((payload: { benchmarks: BenchmarkInfo[] }) => { setBenchmarks(payload.benchmarks); setError(''); })
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, []);

  const active = benchmarks.find((item) => item.id === selected);
  const tasks = useMemo(() => (active?.tasks ?? []).filter((task) => task.task_id.toLowerCase().includes(taskSearch.toLowerCase())), [active, taskSearch]);
  const chooseBenchmark = (id: BenchmarkId) => {
    setSelected(id); setSelectedTask(null); setSelectedRunId(''); setSavedDetail(null); setTaskSearch(''); setError('');
  };
  const chooseTask = (task: CatalogTask) => {
    setSelectedTask(task);
    setSelectedRunId(task.attempts?.[0]?.run_id ?? '');
    setSavedDetail(null);
    setError('');
  };

  useEffect(() => {
    if (selected !== 'kramabench') return;
    if (!selectedTask || !selectedTask.attempts?.length) {
      setSavedDetail(null);
      return;
    }
    const runId = selectedRunId && selectedTask.attempts.some((attempt) => attempt.run_id === selectedRunId)
      ? selectedRunId : selectedTask.attempts[0].run_id ?? '';
    if (runId !== selectedRunId) { setSelectedRunId(runId); return; }
    if (!runId) { setSavedDetail(null); return; }
    let cancelled = false;
    setDetailLoading(true); setSavedDetail(null);
    fetch(`/api/benchmark-dashboard/krama/${encodeURIComponent(selectedTask.task_id)}?run_id=${encodeURIComponent(runId)}`)
      .then(async (response) => { if (!response.ok) throw new Error((await response.json()).detail || `Saved run request failed (${response.status})`); return response.json() as Promise<SavedDetail>; })
      .then((payload) => { if (!cancelled) setSavedDetail(payload); })
      .catch((reason: unknown) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selected, selectedTask, selectedRunId]);

  useEffect(() => {
    if (selected !== 'mscore' || !selectedTask || !selectedRunId) return;
    let cancelled = false;
    setDetailLoading(true); setSavedDetail(null);
    fetch(`/api/benchmark-results/mscore/tasks/${encodeURIComponent(selectedTask.task_id)}?condition=${encodeURIComponent(selectedRunId)}`)
      .then(async (response) => { if (!response.ok) throw new Error((await response.json()).detail || `Saved run request failed (${response.status})`); return response.json(); })
      .then((payload) => {
        if (cancelled) return;
        const transcript = payload.transcript ?? {};
        const board = transcript.board ?? {};
        const result = payload.result ?? {};
        setSavedDetail({
          run: { ...result, run_id: selectedRunId, condition: selectedRunId },
          transcript_available: true,
          task: transcript.task,
          outcome: transcript.outcome,
          entries: Array.isArray(board.entries) ? board.entries : [],
          counterfactual_events: Array.isArray(board.counterfactual_events) ? board.counterfactual_events : [],
          counterfactual_attempts: Array.isArray(transcript.counterfactual_attempts) ? transcript.counterfactual_attempts : [],
          counterfactual_skips: Array.isArray(transcript.counterfactual_skips) ? transcript.counterfactual_skips : [],
        });
      })
      .catch((reason: unknown) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selected, selectedTask, selectedRunId]);

  const taskHasResults = (selectedTask?.result_count ?? 0) > 0;

  return <main className="benchmark-dashboard">
    <header className="benchmark-dashboard-header">
      <div className="mscore-eyebrow">PROJECT EVALUATION · SAVED TASK REPLAYS</div>
      <h1>Benchmark dashboard</h1>
      <p>Select a benchmark, task, and saved run. The graph replays that run’s recorded board entries in order; it does not call the model.</p>
      <div className="benchmark-picker" role="tablist" aria-label="Select benchmark">
        {(['kramabench', 'mscore', 'medagentbench'] as BenchmarkId[]).map((id) => {
          const item = benchmarks.find((benchmark) => benchmark.id === id);
          return <button type="button" role="tab" aria-selected={selected === id} key={id} onClick={() => chooseBenchmark(id)}>
            <b>{labels[id]}</b><span>{loading ? '…' : `${item?.task_count ?? 0} tasks · ${item?.result_count ?? 0} saved runs`}</span>
          </button>;
        })}
      </div>
    </header>

    {error && <div className="benchmark-error" role="alert">{error}</div>}
    {loading && <div className="benchmark-empty">Loading benchmark catalogs…</div>}

    {!loading && active && <section className="benchmark-catalog-layout">
      <div className="benchmark-catalog-panel">
        <div className="benchmark-catalog-heading"><div><h2>{active.name} tasks</h2><p>{active.task_source}</p></div><strong>{active.task_count}</strong></div>
        <input aria-label={`Search ${active.name} tasks`} value={taskSearch} onChange={(event) => setTaskSearch(event.target.value)} placeholder="Search task IDs…" />
        <div className="benchmark-task-list">{tasks.map((task) => <button key={task.task_id} type="button" className={selectedTask?.task_id === task.task_id ? 'selected' : ''} onClick={() => chooseTask(task)}>
          <span><b>{task.task_id}</b><small>{[task.split, task.evaluator_mode, task.adapted ? 'adapted' : ''].filter(Boolean).join(' · ') || active.name}</small></span>
          <span className="task-result-count">{task.result_count} saved</span>
        </button>)}</div>
        {!tasks.length && <div className="benchmark-empty">No task IDs match that search.</div>}
      </div>

      <aside className="benchmark-task-info">
        {!selectedTask ? <div className="benchmark-empty">Select a task to see its saved run and agent trace.</div> : <>
          <div className="mscore-eyebrow">{active.name.toUpperCase()} · SELECTED TASK</div><h2>{selectedTask.task_id}</h2>
          {!taskHasResults ? <>
            <p>There is no saved project run for this task, so there is no agent trace to replay.</p>
            <p className="benchmark-run-support">{active.run_support}</p>
            {selected === 'medagentbench' && <div className="benchmark-adaptation-note">MedAgentBench task IDs are available, but no project run records or scorer are connected yet. The dashboard will show a graph here once genuine runs are saved.</div>}
            {selected === 'kramabench' && selectedTask.runnable_in_ui && <button className="open-runner-button" type="button" onClick={() => onOpenKramaRunner(selectedTask.task_id)}>Start a genuine KramaBench run</button>}
          </> : <>
            <p>{selectedTask.result_count} saved attempt(s). Choose one to inspect its recorded execution.</p>
            <div className="catalog-attempts" role="group" aria-label="Saved attempts">
              {(selectedTask.attempts ?? []).map((attempt, index) => <button type="button" key={`${attempt.run_id ?? index}`} className={`saved-attempt-choice${selectedRunId === attempt.run_id ? ' selected' : ''}`} onClick={() => setSelectedRunId(attempt.run_id ?? '')}>
                <b>{attempt.condition ?? attempt.outcome ?? attempt.status ?? 'saved attempt'}</b>
                <span>{attempt.score == null ? 'Not scored' : `Score ${attempt.score}`}{attempt.prediction ? ` · answer ${attempt.prediction}` : ''}</span>
                <small>{attempt.model ?? active.name} · {attempt.evaluator_mode ?? 'evaluator unknown'}{attempt.adapted ? ' · adapted' : ''} · CF sims {attempt.counterfactual_simulations ?? 0}</small>
                {attempt.failure_reason && <small className="attempt-failure">{attempt.failure_reason}</small>}
                {attempt.transcript_available === false && <small className="attempt-failure">Transcript file unavailable.</small>}
              </button>)}
            </div>
            {detailLoading && <div className="benchmark-empty">Loading the selected saved trace…</div>}
            {savedDetail && <div className="saved-run-inspector">
              <div className="saved-run-meta">
                <span>Outcome <b>{show(savedDetail.run.outcome ?? savedDetail.outcome)}</b></span>
                <span>Score <b>{savedDetail.run.score == null ? 'not scored' : `${savedDetail.run.score}${savedDetail.run.score === 1 ? ' · correct' : ' · incorrect'}`}</b></span>
                <span>Answer <b>{show(savedDetail.run.prediction)}</b></span>
                <span>Evaluator <b>{show(savedDetail.run.evaluator_mode)}{savedDetail.run.adapted ? ' · adapted' : ''}</b></span>
                <span>Counterfactual replays <b>{savedDetail.counterfactual_attempts.length}</b></span>
              </div>
              {savedDetail.task && <details className="saved-task-prompt"><summary>Task shown to the agents</summary><p>{savedDetail.task}</p></details>}
              <TaskTraceGraph entries={withSimulationEntries(savedDetail)} title={`${active.name} · recorded agent actions`} />
              <div className="saved-run-entries">
                {savedDetail.entries.map((entry, index) => <article key={String(entry.entry_id ?? index)}>
                  <span><b>{String(entry.agent_id ?? 'agent')}</b> · {String(entry.tag ?? 'ENTRY')}{entry.is_counterfactual_sim ? ' · SIMULATION' : ''}</span>
                  <strong>{String(entry.prediction ?? '')}</strong><p>{String(entry.explanation ?? '')}</p>
                </article>)}
                {!savedDetail.entries.length && <p>No board entries were saved for this attempt.</p>}
              </div>
              {!!savedDetail.counterfactual_events.length && <details className="saved-task-prompt"><summary>Counterfactual events ({savedDetail.counterfactual_events.length})</summary><pre>{JSON.stringify(savedDetail.counterfactual_events, null, 2)}</pre></details>}
            </div>}
            {selected === 'kramabench' && <div className="benchmark-adaptation-note">This is a replay of a saved KramaBench run. To execute the model again, use the separate genuine-run control.</div>}
            {selected === 'mscore' && <div className="benchmark-adaptation-note">MSCoRe results use the project’s adapted evaluator and selected Construction Complex cohort; this is not the upstream official harness.</div>}
          </>}
        </>}
      </aside>
    </section>}
  </main>;
}
