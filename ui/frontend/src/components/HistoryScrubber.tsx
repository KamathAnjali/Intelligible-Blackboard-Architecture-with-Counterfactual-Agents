import React, { useEffect } from 'react';
import type { GraphNode } from './Canvas';

interface HistoryScrubberProps {
  nodes: GraphNode[];
  scrubIndex: number | null; // null means LIVE mode
  onScrub: (index: number | null) => void;
  label?: string;
  keyboardScope?: 'live' | 'branch';
}

export const HistoryScrubber: React.FC<HistoryScrubberProps> = ({
  nodes,
  scrubIndex,
  onScrub,
  label,
  keyboardScope,
}) => {
  const total = nodes.length;
  const idPrefix = keyboardScope ?? 'history';
  const isLive = scrubIndex === null || scrubIndex === total - 1;
  const activeStep = scrubIndex !== null ? scrubIndex : Math.max(0, total - 1);
  const activeNode = nodes[activeStep];

  // Keep the legacy global shortcuts for unified view. Split panes own their
  // focused keyboard scope and never move the other pane's history cursor.
  useEffect(() => {
    if (keyboardScope) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't intercept if typing in an input
      if (['INPUT', 'TEXTAREA'].includes((e.target as HTMLElement).tagName)) return;

      if (e.key === 'ArrowLeft' && total > 0) {
        e.preventDefault();
        const nextIdx = scrubIndex === null ? Math.max(0, total - 2) : Math.max(0, scrubIndex - 1);
        onScrub(nextIdx);
      } else if (e.key === 'ArrowRight' && total > 0) {
        e.preventDefault();
        if (scrubIndex !== null) {
          if (scrubIndex >= total - 1) {
            onScrub(null); // Jump to live
          } else {
            onScrub(scrubIndex + 1);
          }
        }
      } else if (e.key === 'Home' && total > 0) {
        e.preventDefault();
        onScrub(0);
      } else if (e.key === 'End' && total > 0) {
        e.preventDefault();
        onScrub(null); // Jump to live
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [total, scrubIndex, onScrub, keyboardScope]);

  if (total === 0) return null;

  return (
    <div
      className={`history-scrubber-hud ${!isLive ? 'scrubbing-active' : ''}`}
      tabIndex={keyboardScope ? 0 : undefined}
      aria-label={label}
      onKeyDown={keyboardScope ? (event) => {
        if (event.key === 'ArrowLeft') onScrub(scrubIndex === null ? Math.max(0, total - 2) : Math.max(0, scrubIndex - 1));
        if (event.key === 'ArrowRight') onScrub(scrubIndex === null || scrubIndex >= total - 2 ? null : scrubIndex + 1);
        if (event.key === 'Home') onScrub(0);
        if (event.key === 'End') onScrub(null);
      } : undefined}
    >
      {/* Mode status & live jump button */}
      <div className="scrubber-header">
        <div className="scrubber-title">
          {!isLive ? (
            <span className="scrub-badge-active">
              ⏪ <strong>{label ?? 'History Scrub Inspector'}</strong> · Step {activeStep + 1} of {total}
            </span>
          ) : (
            <span className="scrub-badge-live">
              <span className="pulse-dot-green" /> <strong>{label ?? 'Live Board View'}</strong> (Tracking latest)
            </span>
          )}
          {activeNode && (
            <span className="scrub-entry-hint">
              [{activeNode.tag}] {activeNode.agentId.split(' ')[0]}
            </span>
          )}
        </div>

        {!isLive && (
          <button
            id={`btn-jump-live-${idPrefix}`}
            className="btn-jump-live"
            onClick={() => onScrub(null)}
            title="Return to real-time live board view"
          >
            <span className="live-dot-red" /> Jump to Live
          </button>
        )}
      </div>

      {/* Scrubber playback controls & slider */}
      <div className="scrubber-controls">
        <button
          id={`btn-scrub-first-${idPrefix}`}
          className="scrub-btn"
          disabled={activeStep === 0}
          onClick={() => onScrub(0)}
          title="Jump to first entry (Home)"
        >
          ⏮
        </button>
        <button
          id={`btn-scrub-prev-${idPrefix}`}
          className="scrub-btn"
          disabled={activeStep === 0}
          onClick={() => onScrub(Math.max(0, activeStep - 1))}
          title="Step Backward (Left Arrow)"
        >
          ◀
        </button>

        <div className="scrubber-slider-track">
          <input
            id={`scrub-range-slider-${idPrefix}`}
            type="range"
            min={0}
            max={total - 1}
            value={activeStep}
            onChange={(e) => {
              const val = parseInt(e.target.value, 10);
              if (val === total - 1) {
                onScrub(null);
              } else {
                onScrub(val);
              }
            }}
            className="scrub-slider"
          />
          <div className="scrubber-ticks">
            {nodes.map((n, idx) => (
              <span
                key={n.id}
                className={`scrub-tick ${idx === activeStep ? 'active' : ''}`}
                style={{ left: `${(idx / Math.max(1, total - 1)) * 100}%` }}
                title={`Step ${idx + 1}: ${n.tag} by ${n.agentId}`}
              />
            ))}
          </div>
        </div>

        <button
          id={`btn-scrub-next-${idPrefix}`}
          className="scrub-btn"
          disabled={activeStep >= total - 1 && isLive}
          onClick={() => {
            if (activeStep >= total - 1) {
              onScrub(null);
            } else {
              onScrub(activeStep + 1);
            }
          }}
          title="Step Forward (Right Arrow)"
        >
          ▶
        </button>
        <button
          id={`btn-scrub-last-${idPrefix}`}
          className="scrub-btn"
          disabled={isLive}
          onClick={() => onScrub(null)}
          title="Jump to latest / live (End)"
        >
          ⏭
        </button>
      </div>
    </div>
  );
};
