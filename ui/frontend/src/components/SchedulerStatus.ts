import React from 'react';
import type { SessionState } from '../types/sessionReducer';

export function SchedulerStatus({ state }: { state: SessionState }): React.ReactElement {
  const status = state.schedulerState !== 'not_provided'
    ? state.schedulerState
    : state.schedulerEventsPresent ? 'turn events received; state not reported' : 'not provided by backend';
  return React.createElement('section', { className: 'scheduler-status-panel', 'aria-label': 'Scheduler status' },
    React.createElement('strong', null, `Scheduler: ${status}`),
    state.turn === null ? null : React.createElement('span', { className: 'scheduler-turn' }, `Turn ${state.turn}`),
    state.agentIds.length
      ? React.createElement('div', { className: 'agent-roster' }, 'Agents: ', ...state.agentIds.map((agent) =>
          React.createElement('span', {
            key: agent,
            className: state.activeAgentId === agent ? 'agent-chip active' : 'agent-chip',
            'data-active': state.activeAgentId === agent ? 'true' : 'false',
          }, agent)))
      : null,
  );
}
