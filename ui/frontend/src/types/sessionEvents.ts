import type { PXPTag } from '../constants/tagColors';

export type SessionStatus = 'idle' | 'connecting' | 'running' | 'completed' | 'failed' | 'disconnected';
export type SessionMode = 'LIVE' | 'REPLAY' | 'MOCK' | 'SYNTHETIC';
export type SchedulerState = 'running' | 'consensus' | 'deadlock' | 'limit' | 'not_provided';

export interface BoardEntryPayload {
  entry_id: string;
  agent_id: string;
  tag: PXPTag;
  prediction: string;
  explanation: string;
  target_entry_id?: string | null;
  is_counterfactual_sim?: boolean;
  timestamp?: string;
  token_count?: { prediction_tokens: number; explanation_tokens: number; total_tokens: number };
}

export interface SessionSummaryPayload {
  outcome?: string;
  reason?: string;
  scheduler_status?: string;
  iterations?: number;
  deadlocks?: number;
  tokens?: number;
  evaluator_score_available?: boolean;
  evaluator_score?: number | string | null;
  evaluator_mode?: string;
  adapted?: boolean;
  entry_count?: number;
  last_entry_id?: string | null;
}

export type BranchAdoptionMethod = 'prompt' | 'vote_weight' | 'discarded';
export interface CounterfactualBranchState {
  branchId: string;
  source: string;
  sourceSessionId?: string;
  cutoffEntryId?: string | null;
  alteredEntryId?: string | null;
  alteredTag?: string | null;
  explanationHash?: string | null;
  triggeredByAgent?: string | null;
  entries: BoardEntryPayload[];
  deltaScore?: number | null;
  adopted?: boolean;
  reason?: string;
  adoptionMethod?: BranchAdoptionMethod;
  adjustments: Array<{ agent_id: string; method: string; session_entry_id: string }>;
}

interface EventBase { session_id?: string; task_id?: string; source?: string; }

export type BackendEvent =
  | (EventBase & { type: 'connection_ack'; mode: string; message?: string; entry_count?: number; last_entry_id?: string | null })
  | (EventBase & { type: 'board_entry'; entry: BoardEntryPayload; entry_count?: number; last_entry_id?: string | null; token_tally?: unknown })
  | (EventBase & { type: 'token_tally'; token_tally: unknown })
  | (EventBase & { type: 'session_started'; mode?: string; model?: string; counterfactual_fraction?: number; agents?: string[]; entry_count?: number; last_entry_id?: string | null })
  | (EventBase & { type: 'session_summary'; summary: SessionSummaryPayload })
  | (EventBase & { type: 'session_error'; error: string; reason?: string })
  | (EventBase & { type: 'stream_complete'; entry_count?: number; last_entry_id?: string | null })
  | (EventBase & { type: 'heartbeat'; entry_count: number; last_entry_id: string | null })
  | (EventBase & { type: 'turn_started'; agent_id: string; turn: number; reason?: string; priority_score?: number })
  | (EventBase & { type: 'turn_finished'; agent_id: string; turn: number; outcome: string })
  | (EventBase & { type: 'scheduler_status'; state: 'running' | 'consensus' | 'deadlock' | 'limit'; agents?: Array<{ agent_id: string; persona?: string }> })
  | (EventBase & { type: 'branch_created'; branch_id: string; source_session_id?: string; cutoff_entry_id?: string | null; altered_entry_id?: string | null; altered_tag?: string | null; explanation_hash?: string | null; triggered_by_agent?: string | null })
  | (EventBase & { type: 'branch_entry_posted'; branch_id: string; entry: BoardEntryPayload })
  | (EventBase & { type: 'counterfactual_result'; branch_id: string; delta_score?: number | null; adopted: boolean; reason: string; adoption_method: BranchAdoptionMethod })
  | (EventBase & { type: 'live_adjustment_applied'; branch_id: string; agent_id: string; method: string; session_entry_id: string });

export type LocalSessionAction =
  | { type: 'socket_connecting' }
  | { type: 'socket_open' }
  | { type: 'socket_closed' }
  | { type: 'select_session'; session_id: string; task_id: string }
  | { type: 'backend_event'; event: BackendEvent };

