import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Canvas } from './components/Canvas';
import type { GraphLink, GraphNode, PXPTag } from './components/Canvas';


export type WsStatus = 'connected' | 'connecting' | 'disconnected';

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
}

interface WsEnvelope {
  type: 'board_entry' | 'session_summary' | 'stream_complete' | 'connection_ack' | string;
  entry?: BoardEntryEvent;
  summary?: Record<string, unknown>;
  message?: string;
}

/**
 * Convert a board_entry event into a GraphNode.
 * Assumption: tag field matches PXPTag enum values (RATIFY | REVISE | REFUTE | REJECT | PROPOSE).
 * TODO: Reconcile with Student 1/2 schema at standup (see docs/tag_field_review.md).
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
  };
}

/**
 * Derive a GraphLink from a new node if it references a prior entry.
 */
function nodeToLink(node: GraphNode): GraphLink | null {
  if (!node.targetEntryId) return null;
  return {
    source: node.id,
    target: node.targetEntryId,
    relation: node.tag,
  };
}

// WebSocket URL — connects to primary /ws endpoint
const WS_URL = 'ws://localhost:8000/ws?delay=0.9';

export const App: React.FC = () => {
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting');
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [streamDone, setStreamDone] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  const handleMessage = useCallback((envelope: WsEnvelope) => {
    console.log(`[WS] type=${envelope.type}`, envelope);

    if (envelope.type === 'board_entry' && envelope.entry) {
      const newNode = entryToNode(envelope.entry);
      const newLink = nodeToLink(newNode);

      setNodes((prev) => {
        // Avoid duplicates
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
    }

    if (envelope.type === 'stream_complete') {
      console.log('[WS] Stream complete. All mock events received.');
      setStreamDone(true);
    }
  }, []);

  useEffect(() => {
    let socket: WebSocket;

    try {
      socket = new WebSocket(WS_URL);
      socketRef.current = socket;

      socket.onopen = () => {
        console.log('[WS] Connected to UI Server');
        setWsStatus('connected');
      };

      socket.onmessage = (event) => {
        try {
          const envelope: WsEnvelope = JSON.parse(event.data);
          handleMessage(envelope);
        } catch (e) {
          console.warn('[WS] Failed to parse message:', event.data, e);
        }
      };

      socket.onerror = () => {
        console.warn('[WS] Connection error — server may not be running');
        setWsStatus('disconnected');
      };

      socket.onclose = () => {
        console.log('[WS] Disconnected');
        setWsStatus('disconnected');
      };
    } catch {
      setWsStatus('disconnected');
    }

    return () => {
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [handleMessage]);

  return (
    <Canvas
      wsStatus={wsStatus}
      nodes={nodes}
      links={links}
      streamDone={streamDone}
    />
  );
};

export default App;
