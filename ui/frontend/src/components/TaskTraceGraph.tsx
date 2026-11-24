import { useEffect, useMemo, useState } from 'react';
import './TaskTraceGraph.css';

export type TraceEntry = Record<string, unknown>;

export function TaskTraceGraph({ entries, title = 'Task board trace' }: { entries: TraceEntry[]; title?: string }) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const entriesKey = useMemo(() => entries.map((entry) => String(entry.entry_id ?? '')).join('|'), [entries]);

  useEffect(() => { setCurrentIndex(0); setPlaying(false); }, [entriesKey]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCurrentIndex((index) => {
        if (index >= entries.length - 1) { setPlaying(false); return index; }
        return index + 1;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [playing, entries.length]);

  if (!entries.length) return <div className="trace-empty">No board entries were saved for this attempt.</div>;
  const agents = [...new Set(entries.map((entry) => String(entry.agent_id ?? 'agent')))];
  const width = Math.max(640, entries.length * 142 + 64);
  const height = Math.max(105, agents.length * 52 + 24);
  const points = new Map(entries.map((entry, index) => [String(entry.entry_id ?? index), {
    x: 42 + index * ((width - 84) / Math.max(entries.length - 1, 1)),
    y: 25 + agents.indexOf(String(entry.agent_id ?? 'agent')) * 52,
  }]));
  const color = (tag: unknown, simulated: boolean) => simulated ? '#bd9cff' : ({ RATIFY: '#54d6a0', REVISE: '#f4c36e', REFUTE: '#f4a44d', REJECT: '#ff7180', FACT: '#67b9ff' }[String(tag)] ?? '#a8b6c7');
  const activeEntry = entries[currentIndex];
  const currentX = points.get(String(activeEntry.entry_id ?? currentIndex))?.x ?? 42;

  return <section className="trace-graph-wrap" aria-label={title}>
    <div className="trace-graph-title">{title}<span>Saved-run playback · no model call</span></div>
    <div className="trace-playback-controls" aria-label="Saved trace playback controls">
      <button type="button" title="Restart" aria-label="Restart playback" onClick={() => { setCurrentIndex(0); setPlaying(false); }}>⏮</button>
      <button type="button" title="Previous action" aria-label="Previous action" onClick={() => { setPlaying(false); setCurrentIndex((index) => Math.max(0, index - 1)); }}>‹</button>
      <button type="button" className="trace-play-button" aria-label={playing ? 'Pause playback' : 'Play saved trace'} onClick={() => {
        if (currentIndex >= entries.length - 1 && !playing) setCurrentIndex(0);
        setPlaying((value) => !value);
      }}>{playing ? 'Pause' : 'Play saved actions'}</button>
      <button type="button" title="Next action" aria-label="Next action" onClick={() => { setPlaying(false); setCurrentIndex((index) => Math.min(entries.length - 1, index + 1)); }}>›</button>
      <button type="button" title="Show complete trace" aria-label="Show complete trace" onClick={() => { setPlaying(false); setCurrentIndex(entries.length - 1); }}>⏭</button>
      <span>Action {currentIndex + 1} of {entries.length}</span>
    </div>
    <div className="trace-graph-scroll"><svg width={width} height={height} role="img" aria-label={`${title}, action ${currentIndex + 1} of ${entries.length}`}>
      {agents.map((agent, index) => <g key={agent}><line x1="0" x2={width} y1={25 + index * 52} y2={25 + index * 52} stroke="#243442" /><text x="6" y={19 + index * 52} fill="#91a6b8" fontSize="9">{agent}</text></g>)}
      <line x1={currentX} x2={currentX} y1="4" y2={height - 5} stroke="#d6c5ff" strokeDasharray="3 4" opacity=".65" />
      {entries.map((entry, index) => {
        const id = String(entry.entry_id ?? index); const point = points.get(id)!; const target = points.get(String(entry.target_entry_id ?? ''));
        const simulated = entry.is_counterfactual_sim === true;
        const active = index === currentIndex;
        const visible = index <= currentIndex;
        return <g key={id} opacity={visible ? 1 : .17}>
          {target && index <= currentIndex && <line x1={point.x} y1={point.y} x2={target.x} y2={target.y} stroke={color(entry.tag, simulated)} strokeOpacity=".8" strokeWidth="1.6" strokeDasharray={simulated ? '4 3' : undefined} />}
          {active && <circle cx={point.x} cy={point.y} r="18" fill="none" stroke="#e5d8ff" strokeWidth="1.5" opacity=".7" />}
          <circle cx={point.x} cy={point.y} r="12" fill={simulated ? '#251b37' : '#111b26'} stroke={color(entry.tag, simulated)} strokeWidth="3" strokeDasharray={simulated ? '3 2' : undefined} />
          <text x={point.x} y={point.y + 3} textAnchor="middle" fill={color(entry.tag, simulated)} fontSize="7" fontWeight="700">{String(entry.tag ?? '?').slice(0, 3)}</text>
          <text x={point.x} y={point.y + 25} textAnchor="middle" fill="#9fb0bf" fontSize="8">{String(entry.prediction ?? '').slice(0, 13)}</text>
          {simulated && <text x={point.x} y={point.y - 17} textAnchor="middle" fill="#c9adff" fontSize="7">SIM</text>}
        </g>;
      })}
    </svg></div>
    <div className="trace-current-action"><b>{String(activeEntry.agent_id ?? 'agent')}</b><span className={`trace-tag trace-tag-${String(activeEntry.tag ?? '').toLowerCase()}`}>{String(activeEntry.tag ?? 'ENTRY')}</span>{activeEntry.is_counterfactual_sim === true && <span className="trace-sim-label">SIMULATION</span>}
      <strong>{String(activeEntry.prediction ?? '')}</strong><p>{String(activeEntry.explanation ?? '')}</p>
    </div>
  </section>;
}
