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

  // Batched incoming queue to prevent race conditions during high-frequency streaming (W2 Day 5)
  const incomingBufferRef = useRef<WsEnvelope[]>([]);
  const seenNodeIdsRef = useRef<Set<string>>(new Set());
  const seenLinkKeysRef = useRef<Set<string>>(new Set());
  const frameIdRef = useRef<number | null>(null);

  // Flush buffer on animation frame
  const flushBuffer = useCallback(() => {
    if (incomingBufferRef.current.length === 0) return;

    const batch = [...incomingBufferRef.current];
    incomingBufferRef.current = [];

    const newNodesToAdd: GraphNode[] = [];
    const newLinksToAdd: GraphLink[] = [];
    let latestTally: TokenTallyReport | null = null;
    let isComplete = false;
    let detectedMode: BoardMode = null;

    batch.forEach((envelope) => {
      if (envelope.type === 'connection_ack' && envelope.mode) {
        detectedMode = envelope.mode;
      }
      if (envelope.token_tally) {
        latestTally = envelope.token_tally;
      }
      if (envelope.type === 'stream_complete') {
        isComplete = true;
      }
      if (envelope.type === 'board_entry' && envelope.entry) {
        const eid = envelope.entry.entry_id;
        if (!seenNodeIdsRef.current.has(eid)) {
          seenNodeIdsRef.current.add(eid);
          const newNode = entryToNode(envelope.entry);
          newNodesToAdd.push(newNode);

          const newLink = nodeToLink(newNode);
          if (newLink) {
            const linkKey = `${String(newLink.source)}->${String(newLink.target)}`;
            if (!seenLinkKeysRef.current.has(linkKey)) {
              seenLinkKeysRef.current.add(linkKey);
              newLinksToAdd.push(newLink);
            }
          }
        }
      }
    });

    if (detectedMode) {
      setBoardMode(detectedMode);
    }
    if (newNodesToAdd.length > 0) {
      setNodes((prev) => [...prev, ...newNodesToAdd]);
    }
    if (newLinksToAdd.length > 0) {
      setLinks((prev) => [...prev, ...newLinksToAdd]);
    }
    if (latestTally) {
      setTokenTally(latestTally);
    } else if (newNodesToAdd.length > 0) {
      // Incremental client-side tally calculation
      setTokenTally((prev) => {
        let addedTotal = 0;
        const agentMap = { ...prev.agents };

        newNodesToAdd.forEach((n) => {
          const predTok =
            n.tokenCount?.prediction_tokens ??
            Math.max(1, Math.round(n.prediction.split(/\s+/).length * 1.3));
          const explTok =
            n.tokenCount?.explanation_tokens ??
            Math.max(1, Math.round(n.explanation.split(/\s+/).length * 1.3));
          const totalTok = predTok + explTok;
          addedTotal += totalTok;

          const currentAgent = agentMap[n.agentId] || {
            agent_id: n.agentId,
            turn_count: 0,
            total_prediction_tokens: 0,
            total_explanation_tokens: 0,
            total_tokens: 0,
            tags_used: {},
          };

          agentMap[n.agentId] = {
            ...currentAgent,
            turn_count: currentAgent.turn_count + 1,
            total_prediction_tokens: currentAgent.total_prediction_tokens + predTok,
            total_explanation_tokens: currentAgent.total_explanation_tokens + explTok,
            total_tokens: currentAgent.total_tokens + totalTok,
            tags_used: {
              ...(currentAgent.tags_used || {}),
              [n.tag]: ((currentAgent.tags_used || {})[n.tag] || 0) + 1,
            },
          };
        });

        return {
          total_tokens: prev.total_tokens + addedTotal,
          turn_count: prev.turn_count + newNodesToAdd.length,
          agents: agentMap,
        };
      });
    }
    if (isComplete) {
      setStreamDone(true);
    }
  }, []);

  const handleMessage = useCallback(
    (envelope: WsEnvelope) => {
      // Log for narration
      if (envelope.type === 'connection_ack') {
        console.log(`[WS] ✅ Connected — mode: ${envelope.mode}`, envelope.message);
      } else if (envelope.type === 'board_entry' && envelope.entry) {
        console.log(
          `[WS] 📋 board_entry tag=${envelope.entry.tag} agent=${envelope.entry.agent_id} id=${envelope.entry.entry_id}`,
        );
      }

      incomingBufferRef.current.push(envelope);

      if (!frameIdRef.current) {
        frameIdRef.current = requestAnimationFrame(() => {
          frameIdRef.current = null;
          flushBuffer();
        });
      }
    },
    [flushBuffer],
  );

  useEffect(() => {
    let socket: WebSocket;
    let reconnectTimer: any = null;

    const connect = () => {
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
          setWsStatus('disconnected');
        };

        socket.onclose = () => {
          setWsStatus('disconnected');
          // Auto-reconnect after 3s
          reconnectTimer = setTimeout(connect, 3000);
        };
      } catch {
        setWsStatus('disconnected');
        reconnectTimer = setTimeout(connect, 3000);
      }
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (frameIdRef.current) cancelAnimationFrame(frameIdRef.current);
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
