import type { BackendEvent, CounterfactualBranchState, LocalSessionAction, SchedulerState, SessionMode, SessionStatus } from './sessionEvents';

export interface SessionState {
  status: SessionStatus;
  sessionId: string | null;
  taskId: string | null;
  mode: SessionMode | null;
  outcome: string | null;
  errorMessage: string | null;
  reconnectCount: number;
  entryIds: string[];
  lastEntryId: string | null;
  expectedEntryCount: number | null;
  consistencyWarning: string | null;
  schedulerState: SchedulerState;
  schedulerEventsPresent: boolean;
  agentIds: string[];
  activeAgentId: string | null;
  turn: number | null;
  summary: Extract<BackendEvent, { type: 'session_summary' }>['summary'] | null;
  branches: Record<string, CounterfactualBranchState>;
}

export const initialSessionState: SessionState = {
  status: 'idle', sessionId: null, taskId: null, mode: null, outcome: null,
  errorMessage: null, reconnectCount: 0, entryIds: [], lastEntryId: null,
  expectedEntryCount: null, consistencyWarning: null,
  schedulerState: 'not_provided', schedulerEventsPresent: false,
  agentIds: [], activeAgentId: null, turn: null, summary: null, branches: {},
};

function sessionMode(value?: string, source?: string): SessionMode | null {
  if (source === 'SYNTHETIC_FIXTURE' || value?.includes('SYNTHETIC')) return 'SYNTHETIC';
  if (value === 'LIVE_TAP' || value === 'LIVE') return 'LIVE';
  if (value === 'LOG_REPLAY' || value === 'REPLAY') return 'REPLAY';
  if (value?.includes('MOCK')) return 'MOCK';
  return null;
}

function reconcile(state: SessionState, count?: number, lastId?: string | null): SessionState {
  const expectedEntryCount = typeof count === 'number' ? count : state.expectedEntryCount;
  const expectedLastId = lastId === undefined ? state.lastEntryId : lastId;
  let consistencyWarning: string | null = null;
  if (expectedEntryCount !== null && expectedEntryCount !== state.entryIds.length) {
    consistencyWarning = `Entry count mismatch: backend ${expectedEntryCount}, UI ${state.entryIds.length}.`;
  } else if (expectedEntryCount !== null && expectedEntryCount > 0 && expectedLastId !== state.lastEntryId) {
    consistencyWarning = `Last entry mismatch: backend ${expectedLastId ?? 'none'}, UI ${state.lastEntryId ?? 'none'}.`;
  }
  return { ...state, expectedEntryCount, consistencyWarning };
}

export function sessionReducer(state: SessionState, action: LocalSessionAction): SessionState {
  if (action.type === 'socket_connecting') {
    return { ...state, status: state.status === 'completed' || state.status === 'failed' ? state.status : 'connecting' };
  }
  if (action.type === 'socket_open') {
    return { ...state, status: state.status === 'connecting' ? 'idle' : state.status };
  }
  if (action.type === 'socket_closed') {
    const terminal = state.status === 'completed' || state.status === 'failed';
    return { ...state, status: terminal ? state.status : 'disconnected', reconnectCount: state.reconnectCount + 1 };
  }
  if (action.type === 'select_session') {
    return { ...initialSessionState, status: 'running', sessionId: action.session_id, taskId: action.task_id };
  }

  const event = action.event;
  let next = { ...state };
  if (event.session_id && !next.sessionId) next.sessionId = event.session_id;
  if (event.task_id && !next.taskId) next.taskId = event.task_id;
  if (event.source === 'SYNTHETIC_FIXTURE') next.mode = 'SYNTHETIC';

  switch (event.type) {
    case 'connection_ack':
      next.mode = sessionMode(event.mode, event.source) ?? next.mode;
      return reconcile(next, event.entry_count, event.last_entry_id);
    case 'session_started':
      next.status = 'running';
      next.mode = sessionMode(event.mode, event.source) ?? next.mode ?? 'LIVE';
      next.sessionId = event.session_id ?? next.sessionId;
      next.taskId = event.task_id ?? next.taskId;
      if (event.agents) next.agentIds = [...new Set(event.agents)];
      return reconcile(next, event.entry_count, event.last_entry_id);
    case 'board_entry': {
      const id = event.entry.entry_id;
      if (!next.entryIds.includes(id)) {
        next.entryIds = [...next.entryIds, id];
        next.lastEntryId = id;
        if (!next.agentIds.includes(event.entry.agent_id)) next.agentIds = [...next.agentIds, event.entry.agent_id];
      }
      return reconcile(next, event.entry_count, event.last_entry_id ?? next.lastEntryId);
    }
    case 'token_tally':
      return next;
    case 'session_summary':
      next.status = next.status === 'failed' ? 'failed' : 'completed';
      next.summary = event.summary;
      next.outcome = event.summary.outcome ?? event.summary.scheduler_status ?? 'completed';
      if (event.summary.scheduler_status) {
        const status = event.summary.scheduler_status.toLowerCase();
        next.schedulerState = status.includes('deadlock') ? 'deadlock' : status.includes('limit') ? 'limit' : status.includes('consensus') ? 'consensus' : next.schedulerState;
      }
      return reconcile(next, event.summary.entry_count, event.summary.last_entry_id);
    case 'session_error':
      next.status = 'failed';
      next.errorMessage = event.error || event.reason || 'The backend reported an unspecified session error.';
      return next;
    case 'stream_complete':
      if (next.status !== 'failed') next.status = 'completed';
      return reconcile(next, event.entry_count, event.last_entry_id);
    case 'heartbeat':
      return reconcile(next, event.entry_count, event.last_entry_id);
    case 'scheduler_status':
      next.schedulerEventsPresent = true;
      next.schedulerState = event.state;
      if (event.agents) next.agentIds = [...new Set(event.agents.map((agent) => agent.agent_id))];
      return next;
    case 'turn_started':
      next.schedulerEventsPresent = true;
      next.schedulerState = 'running';
      next.activeAgentId = event.agent_id;
      next.turn = event.turn;
      if (!next.agentIds.includes(event.agent_id)) next.agentIds = [...next.agentIds, event.agent_id];
      return next;
    case 'turn_finished':
      next.schedulerEventsPresent = true;
      if (next.activeAgentId === event.agent_id) next.activeAgentId = null;
      next.turn = event.turn;
      return next;
    case 'branch_created':
      next.branches = {
        ...next.branches,
        [event.branch_id]: {
          ...(next.branches[event.branch_id] ?? {
            branchId: event.branch_id, entries: [], adjustments: [], source: event.source ?? 'LIVE',
          }),
          branchId: event.branch_id,
          source: event.source ?? next.branches[event.branch_id]?.source ?? 'LIVE',
          sourceSessionId: event.source_session_id,
          cutoffEntryId: event.cutoff_entry_id,
          alteredEntryId: event.altered_entry_id,
          alteredTag: event.altered_tag,
          explanationHash: event.explanation_hash,
          triggeredByAgent: event.triggered_by_agent,
        },
      };
      return next;
    case 'branch_entry_posted': {
      const current = next.branches[event.branch_id] ?? {
        branchId: event.branch_id, source: event.source ?? 'LIVE', entries: [], adjustments: [],
      };
      if (!current.entries.some((entry) => entry.entry_id === event.entry.entry_id)) {
        next.branches = { ...next.branches, [event.branch_id]: {
          ...current, source: event.source ?? current.source,
          entries: [...current.entries, { ...event.entry, is_counterfactual_sim: true }],
        } };
      }
      return next;
    }
    case 'counterfactual_result': {
      const current = next.branches[event.branch_id] ?? {
        branchId: event.branch_id, source: event.source ?? 'LIVE', entries: [], adjustments: [],
      };
      next.branches = { ...next.branches, [event.branch_id]: {
        ...current, deltaScore: event.delta_score, adopted: event.adopted,
        reason: event.reason, adoptionMethod: event.adoption_method,
      } };
      return next;
    }
    case 'live_adjustment_applied': {
      const current = next.branches[event.branch_id] ?? {
        branchId: event.branch_id, source: event.source ?? 'LIVE', entries: [], adjustments: [],
      };
      if (!current.adjustments.some((item) => item.session_entry_id === event.session_entry_id)) {
        next.branches = { ...next.branches, [event.branch_id]: {
          ...current, adjustments: [...current.adjustments, {
            agent_id: event.agent_id, method: event.method, session_entry_id: event.session_entry_id,
          }],
        } };
      }
      return next;
    }
  }
}

export function reconnectDelayMs(attempt: number): number {
  return Math.min(30_000, 500 * 2 ** Math.max(0, attempt - 1));
}
