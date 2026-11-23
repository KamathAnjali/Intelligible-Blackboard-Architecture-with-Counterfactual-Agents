import React, { useState } from 'react';
import { TAG_COLORS } from '../constants/tagColors';
import type { PXPTag } from '../constants/tagColors';
import { tokenTallyLabels } from './tokenTallyLabels';

export interface AgentTokenStats {
  agent_id: string;
  turn_count: number;
  total_prediction_tokens: number;
  total_explanation_tokens: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  total_tokens: number;
  simulation_tokens?: number;
  tags_used?: Record<string, number>;
  provider_tokens?: number;
  estimated_tokens?: number;
  call_kinds?: Record<string, { calls?: number; provider_tokens?: number; estimated_tokens?: number; total_tokens?: number }>;
}

export interface TokenTallyReport {
  total_tokens: number;
  provider_total_tokens?: number;
  estimated_total_tokens?: number;
  estimated_share_pct?: number;
  source_label?: string;
  measurement_label?: string;
  measurement_scope?: string;
  call_attribution_available?: boolean;
  turn_count: number;
  mainline_tokens?: number;
  simulation_tokens?: number;
  agents: Record<string, AgentTokenStats>;
  by_call_kind?: Record<string, { calls?: number; provider_tokens?: number; estimated_tokens?: number; total_tokens?: number }>;
  by_execution?: Record<string, { calls?: number; provider_tokens?: number; estimated_tokens?: number; total_tokens?: number }>;
  sessions?: Record<string, { calls?: number; provider_tokens?: number; estimated_tokens?: number; total_tokens?: number }>;
}

interface TokenTallyProps {
  tally: TokenTallyReport;
}

export const TokenTally: React.FC<TokenTallyProps> = ({ tally }) => {
  const [isOpen, setIsOpen] = useState(false);
  const agentList = Object.values(tally.agents || {});

  const simTokens =
    tally.simulation_tokens ??
    agentList.reduce((acc, a) => acc + (a.simulation_tokens || 0), 0);
  const providerTokens = tally.provider_total_tokens ?? (tally.source_label === 'provider' ? tally.total_tokens : 0);
  const estimatedTokens = tally.estimated_total_tokens ?? (tally.source_label === 'estimated' ? tally.total_tokens : 0);
  const estimatedPct = tally.estimated_share_pct ?? (tally.total_tokens ? (estimatedTokens / tally.total_tokens) * 100 : 0);
  const labels = tokenTallyLabels({
    providerTokens, estimatedTokens, estimatedSharePct: estimatedPct,
    measurementLabel: tally.measurement_label,
  });
  const sourceLabel = labels.detailLabel;

  return (
    <div className="token-tally-container">
      {/* Toggle button in HUD */}
      <button
        id="btn-token-tally"
        className={`token-tally-pill ${isOpen ? 'active' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        title={sourceLabel}
      >
        <span className="token-icon">⚡</span>
        <span className="token-pill-text">
          {labels.buttonText}
        </span>
        {tally.total_tokens > 0 && (
          <span className="token-pill-badge" data-testid="estimated-share">{labels.estimatedShare}</span>
        )}
        {simTokens > 0 && (
            <span className="sim-token-badge" title="Counterfactual sandbox entry-text estimate">
            🧪 {simTokens.toLocaleString()}t est. sim
          </span>
        )}
        <span className="token-pill-badge">{tally.turn_count} turns</span>
        <span className="token-chevron">{isOpen ? '▲' : '▼'}</span>
      </button>

      {/* Expanded breakdown drawer / modal */}
      {isOpen && (
        <div className="token-breakdown-card">
          <div className="token-card-header">
            <div className="token-header-title">
              <span className="token-icon-lg">⚡</span>
              <div>
                <h4>Model Usage</h4>
                <p className="token-header-subtitle">
                  {sourceLabel}. Provider usage and estimates are never combined without separate labels.
                  {tally.measurement_scope ? ` ${tally.measurement_scope}.` : ''}
                </p>
              </div>
            </div>
            <button
              id="btn-close-token-card"
              className="token-close-btn"
              onClick={() => setIsOpen(false)}
            >
              ×
            </button>
          </div>

          <div className="token-summary-row">
            <div className="token-stat-box">
              <span className="stat-label">Provider Total</span>
              <span className="stat-val highlight">
                {providerTokens.toLocaleString()}
              </span>
            </div>
            <div className="token-stat-box">
              <span className="stat-label">Estimated Total</span>
              <span className="stat-val">{estimatedTokens.toLocaleString()}</span>
            </div>
            <div className="token-stat-box sim-box">
              <span className="stat-label">Estimated Share</span>
              <span className="stat-val sim-highlight">{estimatedPct.toFixed(0)}%</span>
            </div>
          </div>

          <div className="token-summary-row">
            {(['live', 'simulation', 'unattributed'] as const).map((kind) => {
              const group = tally.by_execution?.[kind];
              if (!group) return null;
              return <div className="token-stat-box" key={kind}>
                <span className="stat-label">{kind === 'live' ? 'Live' : kind === 'simulation' ? 'Simulation' : 'Unattributed'}</span>
                <span className="stat-val">{(group.total_tokens ?? 0).toLocaleString()}</span>
              </div>;
            })}
          </div>

          <div className="usage-call-kind-section">
            <h5 className="section-title">By Session</h5>
            {Object.entries(tally.sessions ?? {}).length === 0 ? <p className="no-data-msg">No session totals provided.</p> :
              Object.entries(tally.sessions ?? {}).map(([sessionId, counts]) => <div key={sessionId}>
                {sessionId}: {(counts.total_tokens ?? 0).toLocaleString()} tokens
                {' '}({(counts.provider_tokens ?? 0).toLocaleString()} provider, {(counts.estimated_tokens ?? 0).toLocaleString()} estimated)
              </div>)}
          </div>

          <div className="usage-call-kind-section">
            <h5 className="section-title">By Call Kind</h5>
            {Object.entries(tally.by_call_kind ?? {}).length === 0 ? <p className="no-data-msg">Call-level breakdown not provided.</p> :
              Object.entries(tally.by_call_kind ?? {}).map(([kind, counts]) => <div key={kind}>
                {kind}: {counts.calls ?? 0} calls; provider {(counts.provider_tokens ?? 0).toLocaleString()}; estimated {(counts.estimated_tokens ?? 0).toLocaleString()}
              </div>)}
          </div>

          <div className="token-agents-section">
            <h5 className="section-title">Per-Agent Breakdown</h5>
            {agentList.length === 0 ? (
              <p className="no-data-msg">No agent turns recorded yet.</p>
            ) : (
              <div className="agent-token-list">
                {agentList.map((agent) => {
                  const isCf =
                    agent.agent_id.toLowerCase().includes('sandbox') ||
                    agent.agent_id.toLowerCase().includes('counterfactual') ||
                    (agent.simulation_tokens ?? 0) > 0;

                  return (
                    <div
                      key={agent.agent_id}
                      className={`agent-token-row ${isCf ? 'cf-agent-row' : ''}`}
                    >
                      <div className="agent-token-info">
                        <div className="agent-title-wrap">
                          <span className="agent-name">{agent.agent_id}</span>
                          {isCf && (
                            <span className="agent-cf-tag">
                              ★ Sandbox Agent
                            </span>
                          )}
                        </div>
                        <div className="agent-tag-chips">
                          {agent.tags_used &&
                            Object.entries(agent.tags_used).map(([tag, count]) => (
                              <span
                                key={tag}
                                className="mini-tag-chip"
                                style={{
                                  color: TAG_COLORS[tag as PXPTag] || '#94a3b8',
                                  borderColor: TAG_COLORS[tag as PXPTag] || '#334155',
                                }}
                              >
                                {tag}: {count}
                              </span>
                            ))}
                        </div>
                      </div>

                      <div className="agent-token-bars">
                        <div className="token-bar-track">
                          <div
                            className={`token-bar-fill ${isCf ? 'cf-bar-fill' : ''}`}
                            style={{
                              width: `${
                                tally.total_tokens > 0
                                  ? (agent.total_tokens / tally.total_tokens) * 100
                                  : 0
                              }%`,
                            }}
                          />
                        </div>
                        <div className="token-counts-split">
                          <span className="split-detail">
                            pred: <strong>{agent.total_prediction_tokens}</strong> ·
                            expl:{' '}
                            <strong>{agent.total_explanation_tokens}</strong>
                            {(agent.simulation_tokens ?? 0) > 0 && (
                              <span className="sim-split-badge">
                                {' '}· sim: {agent.simulation_tokens}
                              </span>
                            )}
                          </span>
                          <span className="agent-total-tokens">
                            Provider {(agent.provider_tokens ?? 0).toLocaleString()} · estimated {(agent.estimated_tokens ?? agent.total_tokens).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
