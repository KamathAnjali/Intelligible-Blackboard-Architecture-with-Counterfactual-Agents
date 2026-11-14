import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { initialSessionState, reconnectDelayMs, sessionReducer } from '../src/types/sessionReducer.ts';
import type { BackendEvent } from '../src/types/sessionEvents.ts';
import type { SessionState } from '../src/types/sessionReducer.ts';
import { SchedulerStatus } from '../src/components/SchedulerStatus.ts';
import { tokenTallyLabels } from '../src/components/tokenTallyLabels.ts';
import { branchEmptyState, branchTitle, branchVerdict } from '../src/components/counterfactualPresentation.ts';

test('reducer handles connection, start, summary, errors, completion, and source modes', () => {
  let state = sessionReducer(initialSessionState, { type: 'socket_connecting' });
  assert.equal(state.status, 'connecting');
  state = sessionReducer(state, { type: 'socket_open' });
  assert.equal(state.status, 'idle');
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'connection_ack', mode: 'LIVE_TAP' } });
  assert.equal(state.mode, 'LIVE');
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'session_started', session_id: 's1', task_id: 't1', mode: 'LIVE_TAP' } });
  assert.equal(state.status, 'running');
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'session_summary', summary: { outcome: 'agreement' } } });
  assert.equal(state.status, 'completed');
  assert.equal(state.outcome, 'agreement');
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'session_error', error: 'forced test error' } });
  assert.equal(state.status, 'failed');
  assert.equal(state.errorMessage, 'forced test error');
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'stream_complete' } });
  assert.equal(state.status, 'failed');
  const synthetic = sessionReducer(initialSessionState, { type: 'backend_event', event: { type: 'session_started', source: 'SYNTHETIC_FIXTURE' } });
  assert.equal(synthetic.mode, 'SYNTHETIC');
});

test('reconnect replay deduplicates entries and reports backend parity', () => {
  const entry = { entry_id: 'entry-1', agent_id: 'proposer', tag: 'REVISE' as const, prediction: 'x', explanation: 'y' };
  const first: BackendEvent = { type: 'board_entry', entry, entry_count: 1, last_entry_id: 'entry-1' };
  let state = sessionReducer(initialSessionState, { type: 'backend_event', event: first });
  state = sessionReducer(state, { type: 'socket_closed' });
  assert.equal(state.status, 'disconnected');
  assert.equal(state.reconnectCount, 1);
  state = sessionReducer(state, { type: 'socket_connecting' });
  state = sessionReducer(state, { type: 'backend_event', event: first });
  assert.deepEqual(state.entryIds, ['entry-1']);
  assert.equal(state.consistencyWarning, null);
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'heartbeat', entry_count: 2, last_entry_id: 'entry-2' } });
  assert.match(state.consistencyWarning ?? '', /Entry count mismatch/);
  assert.deepEqual([reconnectDelayMs(1), reconnectDelayMs(2), reconnectDelayMs(20)], [500, 1000, 30000]);
});

test('synthetic counterfactual events stay in branch history and leave live history untouched', () => {
  assert.equal(branchEmptyState([]), 'No counterfactual activity');
  const live = { entry_id: 'live-1', agent_id: 'p', tag: 'REVISE' as const, prediction: 'live', explanation: 'live evidence' };
  let state = sessionReducer(initialSessionState, { type: 'backend_event', event: {
    type: 'board_entry', source: 'LIVE', entry: live, entry_count: 1, last_entry_id: 'live-1',
  } });
  const liveIdsBefore = [...state.entryIds];
  const scripted: BackendEvent[] = [
    { type: 'branch_created', source: 'SYNTHETIC_FIXTURE', branch_id: 'b1', cutoff_entry_id: 'live-1' },
    { type: 'branch_entry_posted', source: 'SYNTHETIC_FIXTURE', branch_id: 'b1', entry: {
      entry_id: 'sim-1', agent_id: 'p', tag: 'REVISE', prediction: 'alternative', explanation: 'test only',
    } },
    { type: 'counterfactual_result', source: 'SYNTHETIC_FIXTURE', branch_id: 'b1', delta_score: 0.2,
      adopted: false, reason: 'scripted UI fixture', adoption_method: 'discarded' },
    { type: 'live_adjustment_applied', source: 'SYNTHETIC_FIXTURE', branch_id: 'b1', agent_id: 'p',
      method: 'prompt', session_entry_id: 'live-2' },
  ];
  for (const event of scripted) state = sessionReducer(state, { type: 'backend_event', event });
  assert.deepEqual(state.entryIds, liveIdsBefore);
  assert.equal(state.expectedEntryCount, 1);
  assert.equal(state.branches.b1.source, 'SYNTHETIC_FIXTURE');
  assert.equal(state.branches.b1.entries[0].is_counterfactual_sim, true);
  assert.equal(state.branches.b1.deltaScore, 0.2);
  assert.equal(state.branches.b1.adopted, false);
  assert.equal(state.branches.b1.adjustments[0].session_entry_id, 'live-2');
  assert.equal(branchTitle(state.branches.b1), 'SYNTHETIC · BRANCH/SIMULATION');
  assert.equal(branchEmptyState(Object.values(state.branches)), null);
  assert.equal(branchVerdict(state.branches.b1), 'discarded (discarded)');
  state = sessionReducer(state, { type: 'backend_event', event: scripted[1] });
  assert.equal(state.branches.b1.entries.length, 1, 'replayed branch entries are deduplicated');
});

test('scheduler events populate roster, highlight active agent, and distinguish absent events', () => {
  let state = sessionReducer(initialSessionState, { type: 'backend_event', event: { type: 'turn_started', agent_id: 'verifier', turn: 3, reason: 'review' } });
  assert.equal(state.activeAgentId, 'verifier');
  assert.equal(state.turn, 3);
  assert.deepEqual(state.agentIds, ['verifier']);
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'turn_finished', agent_id: 'verifier', turn: 3, outcome: 'posted' } });
  assert.equal(state.activeAgentId, null);
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'scheduler_status', state: 'deadlock', agents: [{ agent_id: 'proposer' }, { agent_id: 'verifier' }] } });
  assert.equal(state.schedulerState, 'deadlock');
  assert.deepEqual(state.agentIds, ['proposer', 'verifier']);
  assert.equal(initialSessionState.schedulerState, 'not_provided');
  assert.equal(state.schedulerEventsPresent, true);
});

test('scheduler status component renders explicit unavailable state and active roster from mock events', () => {
  const unavailable = renderToStaticMarkup(awaitlessElement(initialSessionState));
  assert.match(unavailable, /not provided by backend/);
  let state = sessionReducer(initialSessionState, { type: 'backend_event', event: {
    type: 'scheduler_status', state: 'running', agents: [{ agent_id: 'proposer' }, { agent_id: 'verifier' }],
  } });
  state = sessionReducer(state, { type: 'backend_event', event: { type: 'turn_started', agent_id: 'verifier', turn: 2 } });
  const rendered = renderToStaticMarkup(awaitlessElement(state));
  assert.match(rendered, /Scheduler: running/);
  assert.match(rendered, /Turn 2/);
  assert.match(rendered, /agent-chip active[^>]*data-active="true"[^>]*>verifier/);
});

test('token tally UI labels show estimates and keep provider totals separate', () => {
  const estimated = tokenTallyLabels({ providerTokens: 0, estimatedTokens: 23,
    estimatedSharePct: 100, measurementLabel: 'estimated, entry text only' });
  assert.equal(estimated.buttonText, '23 estimated');
  assert.equal(estimated.estimatedShare, '100% estimated');
  assert.equal(estimated.detailLabel, 'estimated, entry text only');

  const provider = tokenTallyLabels({ providerTokens: 40, estimatedTokens: 10,
    estimatedSharePct: 20, measurementLabel: 'provider calls plus separately reported estimates' });
  assert.equal(provider.buttonText, '40 provider tokens + 10 estimated');
  assert.equal(provider.estimatedShare, '20% estimated');
  assert.doesNotMatch(provider.buttonText, /50 provider tokens/);
});

function awaitlessElement(state: SessionState) {
  return React.createElement(SchedulerStatus, { state });
}
