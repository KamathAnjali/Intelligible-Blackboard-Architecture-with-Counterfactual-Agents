import React, { useEffect, useState } from 'react';

interface CanvasProps {
  wsStatus?: 'connected' | 'connecting' | 'disconnected';
}

export const Canvas: React.FC<CanvasProps> = ({ wsStatus = 'disconnected' }) => {
  const [dimensions, setDimensions] = useState({
    width: window.innerWidth,
    height: window.innerHeight,
  });

  useEffect(() => {
    const handleResize = () => {
      setDimensions({
        width: window.innerWidth,
        height: window.innerHeight,
      });
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <div className="canvas-container">
      {/* HUD Header Overlay */}
      <div className="hud-overlay header-hud">
        <div className="title-badge">
          <div className="pulse-dot" />
          <span className="title-text">Intelligible Blackboard</span>
        </div>
        <div className="status-badge">
          <span className={`status-indicator ${wsStatus}`} />
          <span className="status-text">
            WS: {wsStatus.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Main SVG Node-Graph Canvas */}
      <svg
        id="board-canvas"
        className="viewport-svg"
        width={dimensions.width}
        height={dimensions.height}
        viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
      >
        <defs>
          {/* Subtle Grid Pattern for Node Graph Visualizer */}
          <pattern
            id="grid-pattern"
            width="40"
            height="40"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 40 0 L 0 0 0 40"
              fill="none"
              stroke="rgba(255, 255, 255, 0.05)"
              strokeWidth="1"
            />
            <circle
              cx="40"
              cy="40"
              r="1.5"
              fill="rgba(99, 102, 241, 0.15)"
            />
          </pattern>

          {/* Linear Gradient for Nodes placeholder */}
          <linearGradient id="node-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.8" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.8" />
          </linearGradient>
        </defs>

        {/* Background Grid */}
        <rect
          width="100%"
          height="100%"
          fill="url(#grid-pattern)"
        />

        {/* Canvas Placeholder Center Text & Visual Cue */}
        <g transform={`translate(${dimensions.width / 2}, ${dimensions.height / 2})`}>
          {/* Decorative Outer Circle */}
          <circle
            r="120"
            fill="none"
            stroke="rgba(99, 102, 241, 0.2)"
            strokeWidth="2"
            strokeDasharray="6 6"
          />

          {/* Placeholder Center Node */}
          <circle
            r="48"
            fill="url(#node-gradient)"
            filter="drop-shadow(0px 0px 20px rgba(99, 102, 241, 0.5))"
          />

          <text
            y="-70"
            textAnchor="middle"
            fill="#f8fafc"
            fontSize="18"
            fontWeight="600"
            letterSpacing="0.05em"
          >
            Graph Visualization Canvas
          </text>

          <text
            y="95"
            textAnchor="middle"
            fill="#94a3b8"
            fontSize="13"
            fontWeight="400"
          >
            Ready for PXP Blackboard Node Graph Rendering
          </text>

          <text
            y="118"
            textAnchor="middle"
            fill="#64748b"
            fontSize="11"
            fontFamily="monospace"
          >
            {dimensions.width}px × {dimensions.height}px
          </text>
        </g>
      </svg>

      {/* HUD Footer Overlay */}
      <div className="hud-overlay footer-hud">
        <span className="hud-label">Student 4 — UI & Benchmarking</span>
        <span className="hud-detail">Vite + React Canvas Scaffold</span>
      </div>
    </div>
  );
};
