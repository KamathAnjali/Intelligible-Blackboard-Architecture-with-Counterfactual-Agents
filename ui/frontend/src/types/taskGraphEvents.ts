import type { BackendEvent } from './sessionEvents';

/** Only events belonging to the explicitly selected live task may populate its graph. */
export function isActiveTaskSessionEvent(event: BackendEvent, activeSessionId: string | null, activeTaskId: string | null): boolean {
  if (event.type === 'connection_ack') return true;
  if (!activeTaskId || event.task_id !== activeTaskId) return false;
  if (event.type === 'session_started') return event.source === 'LIVE' && Boolean(event.session_id);
  const matchingSession = Boolean(activeSessionId) && event.session_id === activeSessionId;
  const isStatusPoll = event.type === 'session_summary' || event.type === 'session_error';
  return matchingSession && (event.source === 'LIVE' || isStatusPoll);
}
