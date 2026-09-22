import React, { useEffect, useState } from 'react';
import { Canvas } from './components/Canvas';

export const App: React.FC = () => {
  const [wsStatus, setWsStatus] = useState<'connected' | 'connecting' | 'disconnected'>('connecting');

  useEffect(() => {
    // Attempt WebSocket connection to FastAPI server
    const wsUrl = 'ws://localhost:8000/ws';
    let socket: WebSocket | null = null;

    try {
      socket = new WebSocket(wsUrl);

      socket.onopen = () => {
        console.log('[UI Server WebSocket] Connected');
        setWsStatus('connected');
      };

      socket.onmessage = (event) => {
        console.log('[UI Server WebSocket] Message:', event.data);
      };

      socket.onerror = (error) => {
        console.warn('[UI Server WebSocket] Error:', error);
        setWsStatus('disconnected');
      };

      socket.onclose = () => {
        console.log('[UI Server WebSocket] Disconnected');
        setWsStatus('disconnected');
      };
    } catch {
      setWsStatus('disconnected');
    }

    return () => {
      if (socket) {
        socket.close();
      }
    };
  }, []);

  return <Canvas wsStatus={wsStatus} />;
};

export default App;
