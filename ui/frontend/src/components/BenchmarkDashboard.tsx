import { useEffect, useMemo, useState } from 'react';
import { TaskTraceGraph, type TraceEntry } from './TaskTraceGraph';
import { getApiJson } from '../api';
import { cleanModelTextForDisplay } from '../displayText';
import './BenchmarkDashboard.css';

type BenchmarkId = 'kramabench' | 'mscore' | 'medagentbench' | 'math500';
interface CatalogAttempt {
  run_id?: string;
  run?: string;
  condition?: string;
  status?: string;
  outcome?: string;
  score?: number | null;
  score_label?: string;
  metric_summary?: string;
  scored?: boolean;
  correct?: boolean;
  evaluation_status?: string;
  prediction?: string;
  total_tokens?: number | null;
  elapsed_seconds?: number | null;
  iterations?: number | null;
  deadlock_count?: number | null;
  model?: string;
  evaluator_mode?: string;
  adapted?: boolean;
  failure_reason?: string;
  result_source?: string;
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
interface MscoreSavedPayload {
  result?: CatalogAttempt;
  transcript?: {
    task?: string;
    outcome?: string;
    board?: {
      entries?: TraceEntry[];
      counterfactual_events?: Array<Record<string, unknown>>;
    };
    counterfactual_attempts?: unknown[];
    counterfactual_skips?: unknown[];
  };
}

const labels: Record<BenchmarkId, string> = {
  kramabench: 'KramaBench', mscore: 'MSCoRe', medagentbench: 'MedAgentBench', math500: 'MATH-500',
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
  const [catalogRefresh, setCatalogRefresh] = useState(0);
  const [detailRefresh, setDetailRefresh] = useState(0);

  useEffect(() => {
    setLoading(true);
    getApiJson<{ benchmarks: BenchmarkInfo[] }>('/api/benchmark-dashboard/catalog')
      .then((payload) => { setBenchmarks(payload.benchmarks); setError(''); })
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, [catalogRefresh]);

  const active = benchmarks.find((item) => item.id === selected);
  const tasks = useMemo(() => (active?.tasks ?? []).filter((task) => task.task_id.toLowerCase().includes(taskSearch.toLowerCase())), [active, taskSearch]);
  const mscoreSummaries = useMemo(() => (['direct_model_no_blackboard', 'cf00', 'cf100'] as const).map((condition) => {
    const attempts = tasks.flatMap((task) => task.attempts ?? []).filter((attempt) => attempt.condition === condition);
    const scored = attempts.filter((attempt) => attempt.scored ?? attempt.score != null);
    const correct = scored.filter((attempt) => attempt.correct ?? attempt.score === 1).length;
    return {
      condition, attempts: attempts.length, scored: scored.length, correct,
      failed: attempts.filter((attempt) => attempt.evaluation_status === 'failed_before_scoring' || attempt.status === 'failed').length,
      unscored: attempts.filter((attempt) => attempt.evaluation_status === 'not_scored').length,
      simulations: attempts.reduce((total, attempt) => total + (attempt.counterfactual_simulations ?? 0), 0),
    };
  }), [tasks]);
  const kramabenchSummaries = useMemo(() => (['solo', 'cf-00', 'cf-33', 'cf-50', 'cf-66'] as const).map((condition) => {
    const attempts = tasks.flatMap((task) => task.attempts ?? []).filter((attempt) => attempt.condition === condition);
    const exactScored = attempts.filter((attempt) => attempt.score_label === 'Success');
    return {
      condition, attempts: attempts.length, scored: exactScored.length,
      correct: exactScored.filter((attempt) => attempt.correct).length,
      errorMetrics: attempts.filter((attempt) => attempt.score_label === 'MAE').length,
      unscored: attempts.filter((attempt) => attempt.status === 'unscored').length,
      interrupted: attempts.filter((attempt) => attempt.status === 'interrupted').length,
    };
  }), [tasks]);
  const math500Summaries = useMemo(() => (['solo', 'cf-00', 'cf-33', 'cf-50', 'cf-66'] as const).map((condition) => {
    const attempts = tasks.flatMap((task) => task.attempts ?? []).filter((attempt) => attempt.condition === condition);
    const scored = attempts.filter((attempt) => attempt.scored ?? attempt.score != null);
    return {
      condition, attempts: attempts.length, scored: scored.length,
      correct: scored.filter((attempt) => attempt.correct).length,
      review: attempts.filter((attempt) => attempt.outcome?.includes('needs_review')).length,
      replays: attempts.reduce((total, attempt) => total + (attempt.counterfactual_simulations ?? 0), 0),
    };
  }), [tasks]);

  useEffect(() => {
    if (!selectedTask && active?.tasks.length) {
      const firstTask = active.tasks[0];
      setSelectedTask(firstTask);
      setSelectedRunId(firstTask.attempts?.[0]?.run_id ?? '');
    }
  }, [active, selectedTask]);
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
    getApiJson<SavedDetail>(`/api/benchmark-dashboard/krama/${encodeURIComponent(selectedTask.task_id)}?run_id=${encodeURIComponent(runId)}`)
      .then((payload) => { if (!cancelled) setSavedDetail(payload); })
      .catch((reason: unknown) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selected, selectedTask, selectedRunId, detailRefresh]);

  useEffect(() => {
    if (!['mscore', 'math500', 'medagentbench'].includes(selected) || !selectedTask || !selectedRunId) return;
    let cancelled = false;
    setDetailLoading(true); setSavedDetail(null);
    const url = selected === 'mscore'
      ? `/api/benchmark-results/mscore/tasks/${encodeURIComponent(selectedTask.task_id)}?condition=${encodeURIComponent(selectedRunId)}`
      : `/api/benchmark-results/${selected}/tasks/${encodeURIComponent(selectedTask.task_id)}?run_id=${encodeURIComponent(selectedRunId)}`;
    const request = selected === 'mscore'
      ? getApiJson<MscoreSavedPayload>(url).then((payload) => {
        const transcript = payload.transcript ?? {};
        const board = transcript.board ?? {};
        const result = payload.result ?? {};
        return {
          run: { ...result, run_id: selectedRunId, condition: selectedRunId }, transcript_available: true,
          task: transcript.task, outcome: transcript.outcome,
          entries: Array.isArray(board.entries) ? board.entries : [],
          counterfactual_events: Array.isArray(board.counterfactual_events) ? board.counterfactual_events : [],
          counterfactual_attempts: Array.isArray(transcript.counterfactual_attempts) ? transcript.counterfactual_attempts : [],
          counterfactual_skips: Array.isArray(transcript.counterfactual_skips) ? transcript.counterfactual_skips : [],
        } satisfies SavedDetail;
      })
      : getApiJson<SavedDetail>(url);
    request.then((payload) => { if (!cancelled) setSavedDetail(payload); })
      .catch((reason: unknown) => { if (!cancelled) setError(String(reason)); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selected, selectedTask, selectedRunId, detailRefresh]);

  const taskHasResults = (selectedTask?.result_count ?? 0) > 0;

  return <main className="benchmark-dashboard">
    <header className="benchmark-dashboard-header">
      <div className="mscore-eyebrow">PROJECT EVALUATION · SAVED TASK REPLAYS</div>
      <h1>Benchmark dashboard</h1>
      <p>Select a benchmark, task, and saved run. The graph replays that run’s recorded board entries in order; it does not call the model.</p>
      <div className="benchmark-picker" role="tablist" aria-label="Select benchmark">
        {(['kramabench', 'mscore', 'medagentbench', 'math500'] as BenchmarkId[]).map((id) => {
          const item = benchmarks.find((benchmark) => benchmark.id === id);
          return <button type="button" role="tab" aria-selected={selected === id} key={id} onClick={() => chooseBenchmark(id)}>
            <b>{labels[id]}</b><span>{loading ? '…' : `${item?.task_count ?? 0} tasks · ${item?.result_count ?? 0} saved runs`}</span>
          </button>;
        })}
      </div>
      {selected === 'mscore' && !loading && <section className="dashboard-score-overview" aria-label="MSCoRe result summary">
        {mscoreSummaries.map((summary) => <article className={`dashboard-score-card ${summary.condition}`} key={summary.condition}>
          <div>{summary.condition === 'direct_model_no_blackboard' ? 'Direct model · no blackboard' : summary.condition === 'cf00' ? 'Blackboard · counterfactual disabled' : 'Blackboard · counterfactual enabled'}</div>
          <strong>{summary.correct}<small> / {summary.scored} scored correct</small></strong>
          <span>{summary.scored ? `${(summary.correct * 100 / summary.scored).toFixed(1)}% accuracy among scored` : 'No scored runs'}</span>
          <small>{summary.attempts} runs · {summary.failed} failed before scoring · {summary.unscored} unscored · {summary.simulations} simulations</small>
        </article>)}
      </section>}
      {selected === 'kramabench' && !loading && <section className="dashboard-score-overview kramabench-score-overview" aria-label="KramaBench condition comparison">
        {kramabenchSummaries.map((summary) => <article className={`dashboard-score-card ${summary.condition}`} key={summary.condition}>
          <div>{summary.condition === 'solo' ? 'Solo · no blackboard' : `Blackboard · counterfactual ${({ 'cf-00': '0%', 'cf-33': '33%', 'cf-50': '50%', 'cf-66': '66%' } as Record<string, string>)[summary.condition]}`}</div>
          <strong>{summary.correct}<small> / {summary.scored} exact-match correct</small></strong>
          <span>{summary.scored ? `${(summary.correct * 100 / summary.scored).toFixed(1)}% among exact-match scores` : 'No exact-match scores'}</span>
          <small>{summary.attempts} started · {summary.errorMetrics} regression-metric scores · {summary.unscored} unscored · {summary.interrupted} interrupted</small>
        </article>)}
      </section>}
      {selected === 'math500' && !loading && <section className="dashboard-score-overview kramabench-score-overview" aria-label="MATH-500 condition comparison">
        {math500Summaries.map((summary) => <article className={`dashboard-score-card ${summary.condition}`} key={summary.condition}>
          <div>{summary.condition === 'solo' ? 'Solo · no blackboard' : `Blackboard · counterfactual ${({ 'cf-00': '0%', 'cf-33': '33%', 'cf-50': '50%', 'cf-66': '66%' } as Record<string, string>)[summary.condition]}`}</div>
          <strong>{summary.correct}<small> / {summary.scored} correct</small></strong>
          <span>{summary.scored ? `${(summary.correct * 100 / summary.scored).toFixed(1)}% accuracy` : 'No scored results'}</span>
          <small>{summary.attempts} runs · {summary.review} review flagged · {summary.replays} replays</small>
        </article>)}
      </section>}
      {selected === 'mscore' && !loading && <p className="dashboard-evaluator-note">MSCoRe scores use this project’s adapted exact-match evaluator, not the upstream official harness. The table includes the direct no-blackboard baseline and both blackboard conditions; failed and unscored attempts remain visible.</p>}
      {selected === 'kramabench' && !loading && <p className="dashboard-evaluator-note">Imported combined batch: 209 started attempts across 42 tasks and five conditions. Scores are from the compatibility-no-key evaluator recorded in each run. Exact-match results and wildfire MAE regression scores are labeled separately; 171 attempts that never started are excluded. The selected result may use a retry when its original attempt timed out.</p>}
      {selected === 'math500' && !loading && <p className="dashboard-evaluator-note">Imported cohort: 30 selected problems × five conditions (150 runs), using Qwen3 4B on GPU with self-check/review disabled. Accuracy uses the project’s exact-answer check; this is not a full 500-problem evaluation. Blackboard conditions include saved traces for replay.</p>}
    </header>

    {error && <div className="benchmark-error" role="alert">{error}<button type="button" onClick={() => { setError(''); setCatalogRefresh((value) => value + 1); setDetailRefresh((value) => value + 1); }}>Retry loading results</button></div>}
    {loading && <div className="benchmark-empty">Loading benchmark catalogs…</div>}

    {!loading && active && <section className="benchmark-catalog-layout">
      <div className="benchmark-catalog-panel">
        <div className="benchmark-catalog-heading"><div><h2>{active.name} tasks</h2><p>{active.task_source}</p></div><strong>{active.task_count}</strong></div>
        <input aria-label={`Search ${active.name} tasks`} value={taskSearch} onChange={(event) => setTaskSearch(event.target.value)} placeholder="Search task IDs…" />
        {['mscore', 'math500', 'medagentbench'].includes(selected) ? <div className="benchmark-result-table-wrap"><table className="benchmark-result-table">
          <thead><tr><th>Task</th><th>Config</th><th>Outcome</th><th>Score</th><th>Prediction</th><th>Tokens</th><th>Time</th><th>CF</th></tr></thead>
          <tbody>{tasks.flatMap((task) => (task.attempts ?? []).map((attempt, index) => {
            const isSelected = selectedTask?.task_id === task.task_id && selectedRunId === attempt.run_id;
            const failed = attempt.evaluation_status === 'failed_before_scoring' || attempt.status === 'failed';
            return <tr key={`${task.task_id}-${attempt.condition ?? index}`} className={isSelected ? 'selected' : ''} onClick={() => { setSelectedTask(task); setSelectedRunId(attempt.run_id ?? ''); setError(''); }}>
              <td>{task.task_id}</td><td><span className={`dashboard-condition ${attempt.condition}`}>{attempt.run ? `${attempt.run} · ${attempt.condition ?? 'run'}` : attempt.condition ?? 'run'}</span></td>
              <td>{attempt.outcome ?? (failed ? 'failed' : attempt.status ?? '—')}{attempt.failure_reason && <small className="dashboard-row-failure" title={attempt.failure_reason}>{attempt.failure_reason}</small>}</td>
              <td>{attempt.score == null ? attempt.evaluation_status ?? 'not scored' : `${attempt.score}${attempt.correct ?? attempt.score === 1 ? ' ✓' : ' ✗'}`}</td>
              <td>{attempt.prediction ?? '—'}</td><td>{attempt.total_tokens?.toLocaleString() ?? '—'}</td>
              <td>{attempt.elapsed_seconds == null ? '—' : `${attempt.elapsed_seconds.toFixed(1)}s`}</td>
              <td>{attempt.counterfactual_simulations ?? 0}</td>
            </tr>;
          }))}</tbody>
        </table>{!tasks.some((task) => task.attempts?.length) && <div className="benchmark-empty">No saved MSCoRe runs match that search.</div>}</div> : <div className="benchmark-task-list">{tasks.map((task) => <button key={task.task_id} type="button" className={selectedTask?.task_id === task.task_id ? 'selected' : ''} onClick={() => chooseTask(task)}>
          <span><b>{task.task_id}</b><small>{[task.split, task.evaluator_mode, task.adapted ? 'adapted' : ''].filter(Boolean).join(' · ') || active.name}</small></span>
          <span className="task-result-count">{task.result_count} saved</span>
        </button>)}</div>}
        {!tasks.length && <div className="benchmark-empty">No task IDs match that search.</div>}
      </div>

      <aside className="benchmark-task-info">
        {!selectedTask ? <div className="benchmark-empty">Select a task to see its saved run and agent trace.</div> : <>
          <div className="mscore-eyebrow">{active.name.toUpperCase()} · SELECTED TASK</div><h2>{selectedTask.task_id}</h2>
          {!taskHasResults ? <>
            <p>There is no saved project run for this task, so there is no agent trace to replay.</p>
            <p className="benchmark-run-support">{active.run_support}</p>
              {selected === 'medagentbench' && <div className="benchmark-adaptation-note">No saved MedAgentBench attempt is available for this task.</div>}
            {selected === 'kramabench' && selectedTask.runnable_in_ui && <button className="open-runner-button" type="button" onClick={() => onOpenKramaRunner(selectedTask.task_id)}>Start a genuine KramaBench run</button>}
          </> : <>
            <p>{selectedTask.result_count} saved attempt(s). Choose one to inspect its recorded execution.</p>
            <div className="catalog-attempts" role="group" aria-label="Saved attempts">
              {(selectedTask.attempts ?? []).map((attempt, index) => <button type="button" key={`${attempt.run_id ?? index}`} className={`saved-attempt-choice${selectedRunId === attempt.run_id ? ' selected' : ''}`} onClick={() => setSelectedRunId(attempt.run_id ?? '')}>
                <b>{attempt.run ? `${attempt.run} · ${attempt.condition ?? 'run'}` : attempt.condition ?? attempt.outcome ?? attempt.status ?? 'saved attempt'}</b>
                <span>{attempt.score == null ? `Not scored · ${attempt.status ?? 'unknown'}` : `${attempt.score_label ?? 'Score'} ${attempt.score}`}{attempt.correct == null ? '' : attempt.correct ? ' ✓' : ' ✗'}{attempt.prediction ? ` · answer ${attempt.prediction}` : ''}</span>
                {attempt.metric_summary && <small>{attempt.metric_summary}</small>}
                <small>{attempt.model ?? active.name} · {attempt.evaluator_mode ?? 'evaluator unknown'}{attempt.adapted ? ' · adapted' : ''} · CF sims {attempt.counterfactual_simulations ?? 0}{attempt.result_source ? ` · ${attempt.result_source}` : ''}</small>
                {attempt.failure_reason && <small className="attempt-failure">{attempt.failure_reason}</small>}
                {attempt.transcript_available === false && <small className="attempt-failure">Transcript file unavailable.</small>}
              </button>)}
            </div>
            {detailLoading && <div className="benchmark-empty">Loading the selected saved trace…</div>}
            {savedDetail && <div className="saved-run-inspector">
              <div className="saved-run-meta">
                <span>Condition <b>{show(savedDetail.run.condition)}</b></span>
                <span>Selected result <b>{show(savedDetail.run.result_source, 'original')}</b></span>
                <span>Outcome <b>{show(savedDetail.run.outcome ?? savedDetail.outcome)}</b></span>
                <span>{show(savedDetail.run.score_label, 'Score')} <b>{savedDetail.run.score == null ? `not scored (${show(savedDetail.run.status)})` : `${savedDetail.run.score}${savedDetail.run.correct == null ? '' : savedDetail.run.correct ? ' · correct' : ' · incorrect'}`}</b></span>
                <span>Answer <b>{show(savedDetail.run.prediction)}</b></span>
                <span>Evaluator <b>{show(savedDetail.run.evaluator_mode)}{savedDetail.run.adapted ? ' · adapted' : ''}</b></span>
                <span>Counterfactual replays <b>{savedDetail.counterfactual_attempts.length}</b></span>
              </div>
              {savedDetail.run.metric_summary && <div className="saved-run-metric-summary">Evaluator metrics: {savedDetail.run.metric_summary}</div>}
              {savedDetail.run.failure_reason && <div className="benchmark-adaptation-note attempt-failure-detail">Failure detail: {savedDetail.run.failure_reason}</div>}
              {savedDetail.task && <details className="saved-task-prompt"><summary>Task shown to the agents</summary><p>{savedDetail.task}</p></details>}
              {savedDetail.transcript_available === false && <div className="benchmark-adaptation-note">No board transcript was included for this saved attempt. The recorded result is still shown above.</div>}
              <TaskTraceGraph entries={withSimulationEntries(savedDetail)} title={`${active.name} · recorded agent actions`} />
              <h3 className="saved-actions-heading">All posted actions · {savedDetail.entries.length}</h3>
              <div className="saved-run-entries">
                {savedDetail.entries.map((entry, index) => {
                  const initialProposal = index === 0 && entry.tag === 'REVISE' && entry.is_counterfactual_sim !== true;
                  return <article key={String(entry.entry_id ?? index)}>
                    <span><b>{String(entry.agent_id ?? 'agent')}</b>{initialProposal && <em className="saved-action-phase">Initial proposal</em>} · PXP: {String(entry.tag ?? 'ENTRY')}{entry.is_counterfactual_sim ? ' · SIMULATION' : ''}</span>
                    <strong>{cleanModelTextForDisplay(entry.prediction)}</strong><p>{cleanModelTextForDisplay(entry.explanation)}</p>
                  </article>;
                })}
                {!savedDetail.entries.length && <p>No board entries were saved for this attempt.</p>}
              </div>
              {!!savedDetail.counterfactual_events.length && <details className="saved-task-prompt"><summary>Counterfactual events ({savedDetail.counterfactual_events.length})</summary><pre>{JSON.stringify(savedDetail.counterfactual_events, null, 2)}</pre></details>}
            </div>}
            {selected === 'kramabench' && <div className="benchmark-adaptation-note">This is a replay of a saved KramaBench run. To execute the model again, use the separate genuine-run control.</div>}
            {selected === 'mscore' && <div className="benchmark-adaptation-note">MSCoRe results use the project’s adapted evaluator and selected Construction Complex cohort; this is not the upstream official harness.</div>}
            {selected === 'math500' && <div className="benchmark-adaptation-note">This task is from the 30-problem Qwen3 4B GPU cohort. Scores use the project’s exact-answer check; the displayed trace is the saved transcript, not a new model run.</div>}
            {selected === 'medagentbench' && <div className="benchmark-adaptation-note">MedAgentBench contains saved adapted project runs. Its results export is not the upstream official benchmark harness; solo rows have no blackboard trace.</div>}
          </>}
        </>}
      </aside>
    </section>}
  </main>;
}
