import React, { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { Canvas } from './components/Canvas';
import { SchedulerStatus } from './components/SchedulerStatus';
import type { GraphLink, GraphNode, PXPTag } from './components/Canvas';
import type { TokenTallyReport } from './components/TokenTally';
import type { BackendEvent, BoardEntryPayload, SessionMode } from './types/sessionEvents';
import { initialSessionState, reconnectDelayMs, sessionReducer } from './types/sessionReducer';

interface ScopeTask {
  task_id: string;
  workload: string;
  split: 'tuning' | 'held_out_pilot';
  evaluator_mode: string;
  adapted: boolean;
  answer_type: string;
}

function toCanvasMode(mode: SessionMode | null): 'LIVE_TAP' | 'LOG_REPLAY' | 'MOCK_STREAM' | null {
  if (mode === 'LIVE') return 'LIVE_TAP';
  if (mode === 'REPLAY') return 'LOG_REPLAY';
  if (mode === 'MOCK' || mode === 'SYNTHETIC') return 'MOCK_STREAM';
  return null;
}

function entryToNode(entry: BoardEntryPayload): GraphNode {
  return {
    id: entry.entry_id, agentId: entry.agent_id, tag: entry.tag as PXPTag,
    prediction: entry.prediction, explanation: entry.explanation,
    targetEntryId: entry.target_entry_id ?? undefined,
    isCounterfactual: entry.is_counterfactual_sim ?? false,
    timestamp: entry.timestamp, tokenCount: entry.token_count,
  };
}

function nodeToLink(node: GraphNode): GraphLink | null {
  if (!node.targetEntryId) return null;
  return { source: node.id, target: node.targetEntryId, relation: node.tag };
}

const wsUrl = import.meta.env.VITE_WS_URL || (() => {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${window.location.host}/ws`;
})();
const apiRoot = import.meta.env.VITE_API_URL || '';

export const App: React.FC = () => {
  const [session, dispatch] = useReducer(sessionReducer, initialSessionState);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [tokenTally, setTokenTally] = useState<TokenTallyReport>({ total_tokens: 0, turn_count: 0, agents: {} });
  const [scrubIndex, setScrubIndex] = useState<number | null>(null);
  const [liveScrubIndex, setLiveScrubIndex] = useState<number | null>(null);
  const [branchScrubIndex, setBranchScrubIndex] = useState<number | null>(null);
  const [tasks, setTasks] = useState<ScopeTask[]>([]);
  const [selectedTask, setSelectedTask] = useState('');
  const [fraction, setFraction] = useState('0');
  const [taskLoadError, setTaskLoadError] = useState<string | null>(null);
  const [runPending, setRunPending] = useState(false);
  const [apiSessionId, setApiSessionId] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const seenNodeIdsRef = useRef<Set<string>>(new Set());
  const seenLinkKeysRef = useRef<Set<string>>(new Set());
  const reconnectsRef = useRef(0);

  const acceptEvent = useCallback((event: BackendEvent) => {
    dispatch({ type: 'backend_event', event });
    if (event.type === 'board_entry') {
      const id = event.entry.entry_id;
      if (seenNodeIdsRef.current.has(id)) return;
      seenNodeIdsRef.current.add(id);
      const node = entryToNode(event.entry);
      setNodes((previous) => [...previous, node]);
      const link = nodeToLink(node);
      if (link) {
        const key = `${String(link.source)}->${String(link.target)}`;
        if (!seenLinkKeysRef.current.has(key)) {
          seenLinkKeysRef.current.add(key);
          setLinks((previous) => [...previous, link]);
        }
      }
      if (event.token_tally && typeof event.token_tally === 'object') {
        setTokenTally(event.token_tally as TokenTallyReport);
      }
    } else if (event.type === 'token_tally' && event.token_tally && typeof event.token_tally === 'object') {
      setTokenTally(event.token_tally as TokenTallyReport);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let socket: WebSocket | null = null;

    const connect = () => {
      if (disposed) return;
      dispatch({ type: 'socket_connecting' });
      try {
        socket = new WebSocket(wsUrl);
        socketRef.current = socket;
        socket.onopen = () => {
          if (disposed) return;
          reconnectsRef.current = 0;
          dispatch({ type: 'socket_open' });
        };
        socket.onmessage = (message) => {
          try { acceptEvent(JSON.parse(message.data) as BackendEvent); }
          catch (error) { console.error('[WS] Invalid backend event; event was not applied.', error); }
        };
        socket.onerror = () => { if (!disposed) dispatch({ type: 'socket_closed' }); };
        socket.onclose = () => {
          if (disposed) return;
          dispatch({ type: 'socket_closed' });
          reconnectsRef.current += 1;
          reconnectTimer = setTimeout(connect, reconnectDelayMs(reconnectsRef.current));
        };
      } catch (error) {
        console.error('[WS] Could not open the configured WebSocket.', error);
        dispatch({ type: 'socket_closed' });
        reconnectsRef.current += 1;
        reconnectTimer = setTimeout(connect, reconnectDelayMs(reconnectsRef.current));
      }
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [acceptEvent]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiRoot}/api/tasks`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Task list request failed (${response.status}).`);
        return response.json() as Promise<{ tasks: ScopeTask[] }>;
      })
      .then((payload) => {
        if (cancelled) return;
        setTasks(payload.tasks);
        if (payload.tasks.length) setSelectedTask(payload.tasks[0].task_id);
      })
      .catch((error: unknown) => { if (!cancelled) setTaskLoadError(String(error)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!apiSessionId || session.status !== 'running') return;
    let cancelled = false;
    const poll = async () => {
      try {
        const response = await fetch(`${apiRoot}/api/tasks/${encodeURIComponent(apiSessionId)}/status`);
        if (!response.ok) throw new Error(`Session status request failed (${response.status}).`);
        const payload = await response.json();
        if (cancelled) return;
        if (payload.status === 'failed' && payload.error) {
          acceptEvent({ type: 'session_error', session_id: apiSessionId, task_id: payload.task_id, error: payload.error });
        } else if ((payload.status === 'completed' || payload.status === 'failed') && payload.summary) {
          acceptEvent({ type: 'session_summary', session_id: apiSessionId, task_id: payload.task_id, summary: payload.summary });
        }
      } catch (error) { if (!cancelled) setTaskLoadError(String(error)); }
    };
    const timer = setInterval(poll, 2000);
    void poll();
    return () => { cancelled = true; clearInterval(timer); };
  }, [apiSessionId, session.status, acceptEvent]);

  const startTask = async () => {
    const task = tasks.find((item) => item.task_id === selectedTask);
    if (!task || runPending) return;
    if (!window.confirm(`Run ${task.task_id} through the real local model? This starts a genuine KramaBench execution.`)) return;
    setRunPending(true);
    setTaskLoadError(null);
    try {
      const response = await fetch(`${apiRoot}/api/tasks/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: task.task_id, counterfactual_fraction: Number(fraction), mode: 'LIVE' }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Run request failed (${response.status}).`);
      seenNodeIdsRef.current.clear();
      seenLinkKeysRef.current.clear();
      setNodes([]); setLinks([]); setTokenTally({ total_tokens: 0, turn_count: 0, agents: {} });
      setScrubIndex(null); setApiSessionId(payload.session_id);
      dispatch({ type: 'select_session', session_id: payload.session_id, task_id: task.task_id });
    } catch (error) { setTaskLoadError(String(error)); }
    finally { setRunPending(false); }
  };

  const wsStatus = session.status === 'connecting' ? 'connecting' : session.status === 'disconnected' ? 'disconnected' : 'connected';
  const branchNodes = Object.values(session.branches).flatMap((branch) => branch.entries.map((entry) => ({
    ...entryToNode(entry), isCounterfactual: true,
  })));
  const branchLinks = branchNodes.map(nodeToLink).filter((link): link is GraphLink => link !== null);

  return (
    <div className="app-root">
      <section className="session-control-panel" aria-label="KramaBench session controls">
        <div className="session-control-row">
          <label>Frozen task
            <select value={selectedTask} onChange={(event) => setSelectedTask(event.target.value)} disabled={runPending || session.status === 'running'}>
              {tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id} · {task.split} · {task.evaluator_mode}</option>)}
            </select>
          </label>
          <label>Counterfactual fraction
            <select value={fraction} onChange={(event) => setFraction(event.target.value)} disabled={runPending || session.status === 'running'}>
              <option value="0">0%</option><option value="0.33">33%</option><option value="0.66">66%</option><option value="1">100%</option>
            </select>
          </label>
          <button type="button" onClick={() => void startTask()} disabled={!selectedTask || runPending || session.status === 'running'}>
            {runPending ? 'Starting…' : session.status === 'running' ? 'Run in progress' : 'Run real task'}
          </button>
          <span className={`session-mode-pill mode-${(session.mode ?? 'unknown').toLowerCase()}`}>{session.mode ?? 'MODE UNKNOWN'}</span>
          <span className={`session-status status-${session.status}`}>{session.status.toUpperCase()}</span>
        </div>
        {session.taskId && <div className="session-meta">Task: {session.taskId} · Session: {session.sessionId} · Reconnects: {session.reconnectCount}</div>}
        {session.activeAgentId && <div className="active-turn">Turn {session.turn ?? '—'} active: {session.activeAgentId}</div>}
        <SchedulerStatus state={session} />
        {session.consistencyWarning && <div className="parity-warning" role="status">State mismatch: {session.consistencyWarning}</div>}
        {session.outcome && <div className="outcome-banner">Outcome: {session.outcome}{session.summary?.reason ? ` — ${session.summary.reason}` : ''}</div>}
        {session.status === 'completed' && session.summary?.evaluator_score_available && <div className="score-banner">Evaluator score: {session.summary.evaluator_score} · {session.summary.evaluator_mode}{session.summary.adapted ? ' · adapted' : ' · standard'}</div>}
        {session.errorMessage && <div className="session-error-banner" role="alert">Session error: {session.errorMessage}</div>}
        {taskLoadError && <div className="session-error-banner" role="alert">UI/API error: {taskLoadError}</div>}
      </section>
      <Canvas
        wsStatus={wsStatus}
        boardMode={toCanvasMode(session.mode)}
        syntheticStream={session.mode === 'SYNTHETIC'}
        nodes={[...nodes, ...branchNodes]} links={[...links, ...branchLinks]}
        streamDone={session.status === 'completed' || session.status === 'failed'}
        tokenTally={tokenTally} scrubIndex={scrubIndex} onScrub={setScrubIndex}
        branches={Object.values(session.branches)}
        liveScrubIndex={liveScrubIndex} onLiveScrub={setLiveScrubIndex}
        branchScrubIndex={branchScrubIndex} onBranchScrub={setBranchScrubIndex}
      />
    </div>
  );
};

export default App;
