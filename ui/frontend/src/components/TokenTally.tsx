import React, { useState } from 'react';
import { TAG_COLORS } from '../constants/tagColors';
import type { PXPTag } from '../constants/tagColors';

export interface AgentTokenStats {
  agent_id: string;
  turn_count: number;
  total_prediction_tokens: number;
  total_explanation_tokens: number;
  total_prompt_tokens?: number;
  total_completion_tokens?: number;
  total_tokens: number;
  tags_used?: Record<string, number>;
}

export interface TokenTallyReport {
  total_tokens: number;
  turn_count: number;
  agents: Record<string, AgentTokenStats>;
}

interface TokenTallyProps {
  tally: TokenTallyReport;
}

export const TokenTally: React.FC<TokenTallyProps> = ({ tally }) => {
  const [isOpen, setIsOpen] = useState(false);
  const agentList = Object.values(tally.agents || {});

  return (
    <div className="token-tally-container">
      {/* Toggle button in HUD */}
      <button
        id="btn-token-tally"
        className={`token-tally-pill ${isOpen ? 'active' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        title="View live token consumption tally"
      >
        <span className="token-icon">⚡</span>
        <span className="token-pill-text">
          <strong>{tally.total_tokens.toLocaleString()}</strong> tokens
        </span>
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
                <h4>Token Consumption Tally</h4>
                <p className="token-header-subtitle">Live metrics from Student 3 LLM client</p>
              </div>
            </div>
            <button className="token-close-btn" onClick={() => setIsOpen(false)}>×</button>
          </div>

          <div className="token-summary-row">
            <div className="token-stat-box">
              <span className="stat-label">Total Tokens</span>
              <span className="stat-val highlight">{tally.total_tokens.toLocaleString()}</span>
            </div>
            <div className="token-stat-box">
              <span className="stat-label">Total Turns</span>
              <span className="stat-val">{tally.turn_count}</span>
            </div>
            <div className="token-stat-box">
              <span className="stat-label">Avg / Turn</span>
              <span className="stat-val">
                {tally.turn_count > 0
                  ? Math.round(tally.total_tokens / tally.turn_count)
                  : 0}
              </span>
            </div>
          </div>

          <div className="token-agents-section">
            <h5 className="section-title">Per-Agent Breakdown</h5>
            {agentList.length === 0 ? (
              <p className="no-data-msg">No agent turns recorded yet.</p>
            ) : (
              <div className="agent-token-list">
                {agentList.map((agent) => (
                  <div key={agent.agent_id} className="agent-token-row">
                    <div className="agent-token-info">
                      <span className="agent-name">{agent.agent_id}</span>
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
                          className="token-bar-fill"
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
                          pred: <strong>{agent.total_prediction_tokens}</strong> · expl:{' '}
                          <strong>{agent.total_explanation_tokens}</strong>
                        </span>
                        <span className="agent-total-tokens">
                          <strong>{agent.total_tokens.toLocaleString()}</strong> t
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
