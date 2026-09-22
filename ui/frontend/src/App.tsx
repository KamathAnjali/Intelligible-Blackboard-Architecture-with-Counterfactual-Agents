import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Canvas } from './components/Canvas';
import type { GraphLink, GraphNode, PXPTag } from './components/Canvas';
import type { TokenTallyReport } from './components/TokenTally';

// ── Types ────────────────────────────────────────────────────────────────────

export type WsStatus = 'connected' | 'connecting' | 'disconnected';
export type BoardMode = 'LIVE_TAP' | 'LOG_REPLAY' | 'MOCK_STREAM' | null;

/** Raw board_entry payload from the WebSocket stream. */
interface BoardEntryEvent {
  entry_id: string;
  agent_id: string;
  tag: PXPTag;
  prediction: string;
  explanation: string;
  target_entry_id?: string | null;
  is_counterfactual_sim?: boolean;
  timestamp?: string;
  token_count?: {
    prediction_tokens: number;
    explanation_tokens: number;
    total_tokens: number;
  };
}

interface WsEnvelope {
  type: 'board_entry' | 'session_summary' | 'stream_complete' | 'connection_ack' | 'token_tally' | string;
  mode?: BoardMode;
  entry?: BoardEntryEvent;
  summary?: Record<string, unknown>;
  token_tally?: TokenTallyReport;
  message?: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Convert a board_entry event into a GraphNode.
 */
function entryToNode(entry: BoardEntryEvent): GraphNode {
  return {
    id: entry.entry_id,
    agentId: entry.agent_id,
    tag: entry.tag,
    prediction: entry.prediction,
    explanation: entry.explanation,
    targetEntryId: entry.target_entry_id ?? undefined,
    isCounterfactual: entry.is_counterfactual_sim ?? false,
    timestamp: entry.timestamp,
    tokenCount: entry.token_count,
  };
}

function nodeToLink(node: GraphNode): GraphLink | null {
  if (!node.targetEntryId) return null;
  return { source: node.id, target: node.targetEntryId, relation: node.tag };
}

// WebSocket URL — primary live board stream
const WS_URL = 'ws://localhost:8000/ws';

// ── App Component ─────────────────────────────────────────────────────────────

export const App: React.FC = () => {
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting');
  const [boardMode, setBoardMode] = useState<BoardMode>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [streamDone, setStreamDone] = useState(false);
  const [tokenTally, setTokenTally] = useState<TokenTallyReport>({
    total_tokens: 0,
    turn_count: 0,
    agents: {},
  });
  const [scrubIndex, setScrubIndex] = useState<number | null>(null);

  const socketRef = useRef<WebSocket | null>(null);

  const handleMessage = useCallback((envelope: WsEnvelope) => {
    // Detailed console logging for demo narration
    if (envelope.type === 'connection_ack') {
      console.log(`[WS] ✅ Connected — mode: ${envelope.mode}`, envelope.message);
    } else if (envelope.type === 'board_entry' && envelope.entry) {
      console.log(
        `[WS] 📋 board_entry  tag=${envelope.entry.tag}  agent=${envelope.entry.agent_id}  id=${envelope.entry.entry_id}`,
        envelope.entry,
      );
    } else if (envelope.type === 'session_summary') {
      console.log('[WS] 📊 session_summary', envelope.summary ?? envelope);
    } else if (envelope.type === 'stream_complete') {
      console.log('[WS] ✓ stream_complete — all events received');
    }

    if (envelope.type === 'connection_ack' && envelope.mode) {
      setBoardMode(envelope.mode);
    }

    if (envelope.token_tally) {
      setTokenTally(envelope.token_tally);
    }

    if (envelope.type === 'board_entry' && envelope.entry) {
      const newNode = entryToNode(envelope.entry);
      const newLink = nodeToLink(newNode);

      setNodes((prev) => {
        if (prev.find((n) => n.id === newNode.id)) return prev;
        return [...prev, newNode];
      });

      if (newLink) {
        setLinks((prev) => {
          const key = `${String(newLink.source)}->${String(newLink.target)}`;
          if (prev.find((l) => `${String(l.source)}->${String(l.target)}` === key)) return prev;
          return [...prev, newLink];
        });
      }

      // If token_tally wasn't attached directly to envelope, compute running tally on client
      if (!envelope.token_tally) {
        setTokenTally((prev) => {
          const predTok = newNode.tokenCount?.prediction_tokens ?? Math.max(1, Math.round(newNode.prediction.split(/\s+/).length * 1.3));
          const explTok = newNode.tokenCount?.explanation_tokens ?? Math.max(1, Math.round(newNode.explanation.split(/\s+/).length * 1.3));
          const totalTok = predTok + explTok;

          const agentId = newNode.agentId;
          const currentAgent = prev.agents[agentId] || {
            agent_id: agentId,
            turn_count: 0,
            total_prediction_tokens: 0,
            total_explanation_tokens: 0,
            total_tokens: 0,
            tags_used: {},
          };

          const updatedAgent = {
            ...currentAgent,
            turn_count: currentAgent.turn_count + 1,
            total_prediction_tokens: currentAgent.total_prediction_tokens + predTok,
            total_explanation_tokens: currentAgent.total_explanation_tokens + explTok,
            total_tokens: currentAgent.total_tokens + totalTok,
            tags_used: {
              ...(currentAgent.tags_used || {}),
              [newNode.tag]: ((currentAgent.tags_used || {})[newNode.tag] || 0) + 1,
            },
          };

          return {
            total_tokens: prev.total_tokens + totalTok,
            turn_count: prev.turn_count + 1,
            agents: {
              ...prev.agents,
              [agentId]: updatedAgent,
            },
          };
        });
      }
    }

    if (envelope.type === 'stream_complete') {
      setStreamDone(true);
    }
  }, []);

  useEffect(() => {
    let socket: WebSocket;

    try {
      socket = new WebSocket(WS_URL);
      socketRef.current = socket;

      socket.onopen = () => {
        console.log('[WS] Connection open');
        setWsStatus('connected');
      };

      socket.onmessage = (event) => {
        try {
          handleMessage(JSON.parse(event.data) as WsEnvelope);
        } catch (e) {
          console.warn('[WS] Failed to parse message:', event.data, e);
        }
      };

      socket.onerror = () => {
        console.warn('[WS] Error — is the server running? (uvicorn ui.server.main:app --reload)');
        setWsStatus('disconnected');
      };

      socket.onclose = () => {
        console.log('[WS] Closed');
        setWsStatus('disconnected');
      };
    } catch {
      setWsStatus('disconnected');
    }

    return () => {
      socketRef.current?.close();
    };
  }, [handleMessage]);

  return (
    <Canvas
      wsStatus={wsStatus}
      boardMode={boardMode}
      nodes={nodes}
      links={links}
      streamDone={streamDone}
      tokenTally={tokenTally}
      scrubIndex={scrubIndex}
      onScrub={setScrubIndex}
    />
  );
};

export default App;
