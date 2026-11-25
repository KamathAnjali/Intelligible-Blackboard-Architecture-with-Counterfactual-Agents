import { useEffect, useMemo, useState } from 'react';
import './MsCoReResults.css';
import { TaskTraceGraph, type TraceEntry } from './TaskTraceGraph';
import { getApiJson } from '../api';

type Condition = 'cf00' | 'cf100';
type ResultRow = Record<string, unknown> & { task_id: string; condition: Condition };
interface ConditionSummary {
  attempts: number;
  scored: number;
  correct: number;
  failed_before_scoring: number;
  unscored: number;
  counterfactual_simulations: number;
}
interface ResultsIndex {
  manifest: Record<string, unknown>;
  task_count: number;
  rows: ResultRow[];
  by_condition: Record<Condition, ConditionSummary>;
}
interface Detail {
  result: ResultRow;
  transcript: {
    task?: string;
    outcome?: string;
    board?: { entries?: Array<Record<string, unknown>>; counterfactual_events?: Array<Record<string, unknown>>; deadlocks?: unknown[]; intelligibility?: string };
    counterfactual_attempts?: Array<Record<string, unknown>>;
    counterfactual_skips?: Array<Record<string, unknown>>;
    counterfactual_capable_agents?: string[];
    scheduler_status?: string;
  };
}

const show = (value: unknown, fallback = '—') => value === null || value === undefined || value === '' ? fallback : String(value);
const pct = (n: number, d: number) => d ? `${(100 * n / d).toFixed(1)}%` : '—';

export function MsCoReResults({ compact = false }: { compact?: boolean }) {
  const [data, setData] = useState<ResultsIndex | null>(null);
  const [condition, setCondition] = useState<'all' | Condition>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<{ taskId: string; condition: Condition } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);

  const traceEntries = useMemo(() => {
    const entries: TraceEntry[] = [...(detail?.transcript.board?.entries ?? [])];
    const seen = new Set(entries.map((entry) => String(entry.entry_id ?? '')));
    for (const event of detail?.transcript.board?.counterfactual_events ?? []) {
      const simulated = event.simulated_entry;
      if (!simulated || typeof simulated !== 'object') continue;
      const entry = simulated as TraceEntry;
      const id = String(entry.entry_id ?? '');
      if (!id || seen.has(id)) continue;
      seen.add(id);
      entries.push({ ...entry, is_counterfactual_sim: true });
    }
    return entries;
  }, [detail]);

  useEffect(() => {
    setLoading(true);
    getApiJson<ResultsIndex>('/api/benchmark-results/mscore')
      .then((payload) => { setData(payload); setError(''); })
      .catch((reason: unknown) => setError(String(reason)))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    setDetail(null);
    getApiJson<Detail>(`/api/benchmark-results/mscore/tasks/${encodeURIComponent(selected.taskId)}?condition=${selected.condition}`)
      .then(setDetail)
      .catch((reason: unknown) => setError(String(reason)));
  }, [selected]);

  const visibleRows = useMemo(() => (data?.rows ?? []).filter((row) =>
    (condition === 'all' || row.condition === condition) &&
    `${row.task_id} ${row.outcome ?? ''} ${row.evaluation_status ?? ''} ${row.prediction ?? ''}`
      .toLowerCase().includes(query.toLowerCase()),
  ), [data, condition, query]);

  if (loading) return <main className={`mscore-results${compact ? ' compact' : ''}`}><div className="mscore-loading">Loading saved MSCoRe results…</div></main>;
  if (error && !data) return <main className={`mscore-results${compact ? ' compact' : ''}`}><div className="mscore-error" role="alert">{error}<button type="button" onClick={() => { setError(''); setRefresh((value) => value + 1); }}>Retry loading results</button></div></main>;
  if (!data) return null;

  return (
    <main className={`mscore-results${compact ? ' compact' : ''}`}>
      <header className="mscore-heading">
        <div>
          <div className="mscore-eyebrow">BENCHMARK RESULTS · ADAPTED MSCoRe EVALUATION</div>
          <h1>MSCoRe paired run</h1>
          <p>{data.task_count} fixed Construction Complex tasks · same task IDs across both configurations</p>
        </div>
        <div className="mscore-run-meta">
          <span>Model <b>{show(data.manifest.model)}</b></span>
          <span>Context <b>{show((data.manifest.model_options as Record<string, unknown> | undefined)?.num_ctx)}</b></span>
          <span>Output cap <b>{show((data.manifest.model_options as Record<string, unknown> | undefined)?.num_predict)}</b></span>
          <span>Code <b>{show(data.manifest.git_commit).slice(0, 10)}</b></span>
        </div>
      </header>

      <div className="mscore-adaptation-note">
        Scores use the project’s adapted exact-match evaluator, not the upstream official MSCoRe harness. Unscored and failed runs remain visible.
      </div>

      <section className="mscore-config-cards" aria-label="Condition comparison">
        {(['cf00', 'cf100'] as Condition[]).map((key) => {
          const summary = data.by_condition[key];
          return <article className={`mscore-config-card ${key}`} key={key}>
            <div className="mscore-config-title">{key === 'cf00' ? 'Counterfactual disabled' : 'Counterfactual enabled'}</div>
            <strong>{summary.correct} <small>/ {summary.scored} scored correct</small></strong>
            <div className="mscore-rate">Accuracy among scored: {pct(summary.correct, summary.scored)}</div>
            <div className="mscore-mini-stats">
              <span>{summary.attempts} runs</span><span>{summary.failed_before_scoring} failed before scoring</span>
              <span>{summary.unscored} unscored</span><span>{summary.counterfactual_simulations} simulations</span>
            </div>
          </article>;
        })}
      </section>

      <section className="mscore-results-workspace">
        <div className="mscore-table-panel">
          <div className="mscore-table-tools">
            <div className="mscore-condition-tabs" role="tablist" aria-label="Filter counterfactual condition">
              {(['all', 'cf00', 'cf100'] as const).map((key) => <button key={key} role="tab" aria-selected={condition === key} onClick={() => setCondition(key)}>
                {key === 'all' ? 'All runs' : key}
              </button>)}
            </div>
            <input aria-label="Search results" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find task, answer, outcome…" />
          </div>
          <div className="mscore-table-scroll">
            <table>
              <thead><tr><th>Task</th><th>Config</th><th>Outcome</th><th>Score</th><th>Prediction</th><th>Tokens</th><th>Time</th><th>CF sims</th></tr></thead>
              <tbody>{visibleRows.map((row, index) => {
                const isSelected = selected?.taskId === row.task_id && selected.condition === row.condition;
                return <tr key={`${row.task_id}-${row.condition}-${row.attempt}-${index}`} className={isSelected ? 'selected' : ''} onClick={() => setSelected({ taskId: row.task_id, condition: row.condition })}>
                  <td className="task-id">{row.task_id}</td><td><span className={`condition-badge ${row.condition}`}>{row.condition}</span></td>
                  <td>{show(row.outcome)}</td><td>{row.scored ? `${row.score} ${row.correct ? '✓' : '✗'}` : show(row.evaluation_status)}</td>
                  <td className="prediction-cell">{show(row.prediction)}</td><td>{show(row.total_tokens)}</td>
                  <td>{row.elapsed_seconds == null ? '—' : `${Number(row.elapsed_seconds).toFixed(1)}s`}</td>
                  <td>{show(row.counterfactual_simulations, '0')}</td>
                </tr>;
              })}</tbody>
            </table>
            {!visibleRows.length && <div className="mscore-empty">No tasks match that filter.</div>}
          </div>
          <div className="mscore-table-footer">Showing {visibleRows.length} of {data.rows.length} attempts</div>
        </div>

        <aside className="mscore-detail-panel" aria-label="Selected task transcript">
          {!selected && <div className="mscore-empty">Select a task row to inspect its answer, board entries, and transcript.</div>}
          {selected && !detail && <div className="mscore-empty">Loading task transcript…</div>}
          {detail && <>
            <div className="mscore-detail-title"><span className={`condition-badge ${detail.result.condition}`}>{detail.result.condition}</span><h2>{detail.result.task_id}</h2></div>
            <div className="mscore-detail-grid">
              <span>Prediction</span><b>{show(detail.result.prediction)}</b>
              <span>Evaluation</span><b>{detail.result.scored ? `${detail.result.score} · ${detail.result.correct ? 'correct' : 'incorrect'}` : show(detail.result.evaluation_status)}</b>
              <span>Outcome</span><b>{show(detail.result.outcome)}</b>
              <span>Iterations / deadlocks</span><b>{show(detail.result.iterations)} / {show(detail.result.deadlock_count)}</b>
              <span>Live tokens</span><b>{show(detail.result.live_prompt_tokens)} in · {show(detail.result.live_completion_tokens)} out</b>
              <span>Simulation tokens</span><b>{show(detail.result.simulation_prompt_tokens)} in · {show(detail.result.simulation_completion_tokens)} out</b>
              <span>CF triggers / sims</span><b>{show(detail.result.counterfactual_triggers, '0')} / {show(detail.result.counterfactual_simulations, '0')}</b>
            </div>
            {detail.result.failure_reason && <div className="mscore-failure">{show(detail.result.failure_reason)}</div>}
            <details className="mscore-task-prompt"><summary>Task prompt used by the agents</summary><p>{show(detail.transcript.task)}</p></details>
            <h3>Blackboard entries</h3>
            <TaskTraceGraph entries={traceEntries} title={`${detail.result.task_id} · recorded action sequence`} />
            <div className="mscore-entry-list">{traceEntries.map((entry, index) => <article className="mscore-entry" key={String(entry.entry_id ?? index)}>
              <div><b>{show(entry.agent_id)}</b><span className={`entry-tag tag-${String(entry.tag ?? '').toLowerCase()}`}>{show(entry.tag)}</span>{entry.is_counterfactual_sim ? <span className="sim-label">SIMULATION</span> : null}</div>
              <strong>{show(entry.prediction)}</strong><p>{show(entry.explanation)}</p>
            </article>)}</div>
            <h3>Counterfactual activity</h3>
            <p className="mscore-cf-note">{detail.transcript.counterfactual_attempts?.length
              ? `${detail.transcript.counterfactual_attempts.length} recovery/replay attempt(s) recorded.`
              : `No recovery/replay attempt recorded. Config: ${detail.result.condition}; triggers: ${show(detail.result.counterfactual_triggers, '0')}; simulations: ${show(detail.result.counterfactual_simulations, '0')}. A 100% capability setting does not mean a replay happened.`}</p>
            {!!detail.transcript.board?.counterfactual_events?.length && <div className="mscore-entry-list">{detail.transcript.board.counterfactual_events.map((event, index) => {
              const simulated = event.simulated_entry as Record<string, unknown> | undefined;
              return <article className="mscore-entry" key={String(event.original_entry_id ?? index)}>
                <div><b>Replay event</b><span className="sim-label">SIMULATION</span><span>{event.applied_to_live ? 'Applied to live run' : 'Not applied to live run'}</span></div>
                <strong>{show(simulated?.prediction)}</strong><p>{show(simulated?.explanation)}</p>
                <p>Score change: {show(event.delta_score)} · Original entry: {show(event.original_entry_id)}</p>
              </article>;
            })}</div>}
            {!!detail.transcript.counterfactual_skips?.length && <details><summary>Skipped recovery checks ({detail.transcript.counterfactual_skips.length})</summary><pre>{JSON.stringify(detail.transcript.counterfactual_skips, null, 2)}</pre></details>}
          </>}
          {error && data && <div className="mscore-error" role="alert">{error}</div>}
        </aside>
      </section>
    </main>
  );
}
