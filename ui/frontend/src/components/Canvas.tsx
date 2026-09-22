import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { TAG_COLORS } from '../constants/tagColors';
import type { PXPTag } from '../constants/tagColors';

// ──────────────────────────────────────────────────────────────────────────────
// Re-export for consumers that used to import these from Canvas directly
// ──────────────────────────────────────────────────────────────────────────────
export type { PXPTag };
export { TAG_COLORS };

// ──────────────────────────────────────────────────────────────────────────────
// Graph types
// ──────────────────────────────────────────────────────────────────────────────

export interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  agentId: string;
  tag: PXPTag;
  prediction: string;
  explanation: string;
  targetEntryId?: string;
  isCounterfactual?: boolean;
  timestamp?: string;
}

export interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  relation: string;
}

export type BoardMode = 'LIVE_TAP' | 'LOG_REPLAY' | 'MOCK_STREAM' | null;

interface CanvasProps {
  wsStatus?: 'connected' | 'connecting' | 'disconnected';
  boardMode?: BoardMode;
  nodes?: GraphNode[];
  links?: GraphLink[];
  streamDone?: boolean;
}

// ──────────────────────────────────────────────────────────────────────────────
// Badge label helpers
// ──────────────────────────────────────────────────────────────────────────────

function modeBadgeLabel(mode: BoardMode): string {
  if (mode === 'LIVE_TAP') return '● LIVE';
  if (mode === 'LOG_REPLAY') return '⏪ REPLAY';
  if (mode === 'MOCK_STREAM') return '⚙ MOCK';
  return '';
}

function modeBadgeClass(mode: BoardMode): string {
  if (mode === 'LIVE_TAP') return 'mode-badge live';
  if (mode === 'LOG_REPLAY') return 'mode-badge replay';
  return 'mode-badge mock';
}

// ──────────────────────────────────────────────────────────────────────────────
// Canvas Component
// ──────────────────────────────────────────────────────────────────────────────

export const Canvas: React.FC<CanvasProps> = ({
  wsStatus = 'disconnected',
  boardMode = null,
  nodes = [],
  links = [],
  streamDone = false,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simulationRef = useRef<d3.Simulation<GraphNode, GraphLink> | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [dimensions, setDimensions] = useState({
    width: window.innerWidth,
    height: window.innerHeight,
  });

  useEffect(() => {
    const handleResize = () =>
      setDimensions({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // ── D3 incremental update ──────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);

    // One-time setup
    if (!simulationRef.current) {
      svg.append('g').attr('class', 'graph-content');

      const zoom = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.2, 4])
        .on('zoom', (event) =>
          svg.select('.graph-content').attr('transform', event.transform),
        );
      svg.call(zoom as any);

      const content = svg.select<SVGGElement>('.graph-content');
      const defs = content.append('defs');

      // Arrow markers — one per tag, colored to match
      (Object.entries(TAG_COLORS) as [PXPTag, string][]).forEach(([tag, color]) => {
        defs
          .append('marker')
          .attr('id', `arrow-${tag}`)
          .attr('viewBox', '0 -5 10 10')
          .attr('refX', 42)
          .attr('refY', 0)
          .attr('markerWidth', 7)
          .attr('markerHeight', 7)
          .attr('orient', 'auto')
          .append('path')
          .attr('d', 'M0,-5L10,0L0,5')
          .attr('fill', color);
      });

      content.append('g').attr('class', 'links-group');
      content.append('g').attr('class', 'nodes-group');

      simulationRef.current = d3
        .forceSimulation<GraphNode>()
        .force(
          'link',
          d3.forceLink<GraphNode, GraphLink>().id((d) => d.id).distance(150),
        )
        .force('charge', d3.forceManyBody().strength(-450))
        .force('center', d3.forceCenter(dimensions.width / 2, dimensions.height / 2))
        .force('collide', d3.forceCollide().radius(56));
    }

    const sim = simulationRef.current!;
    const content = svg.select<SVGGElement>('.graph-content');

    // Preserve existing positions
    const nodesData: GraphNode[] = nodes.map((d) => {
      const existing = sim.nodes().find((n) => n.id === d.id);
      return existing
        ? { ...d, x: existing.x, y: existing.y, vx: existing.vx, vy: existing.vy }
        : { ...d };
    });
    const linksData: GraphLink[] = links.map((d) => ({ ...d }));

    // ── Nodes ────────────────────────────────────────────────────────────────
    const nodeGroups = content
      .select<SVGGElement>('.nodes-group')
      .selectAll<SVGGElement, GraphNode>('g.node-group')
      .data(nodesData, (d) => d.id);

    nodeGroups.exit().remove();

    const nodeEnter = nodeGroups
      .enter()
      .append('g')
      .attr('class', 'node-group')
      .style('cursor', 'pointer')
      .style('opacity', 0)
      .on('click', (_e, d) => setSelectedNode(d));

    const drag = d3
      .drag<SVGGElement, GraphNode>()
      .on('start', (event, d) => {
        if (!event.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      });
    nodeEnter.call(drag as any);

    // Glow ring
    nodeEnter.append('circle')
      .attr('r', 30)
      .attr('fill', (d) => TAG_COLORS[d.tag])
      .attr('fill-opacity', 0.18)
      .attr('stroke', (d) => TAG_COLORS[d.tag])
      .attr('stroke-width', 2)
      .attr('filter', 'drop-shadow(0px 0px 12px rgba(0,0,0,0.6))');

    // Filled core
    nodeEnter.append('circle')
      .attr('r', 22)
      .attr('fill', (d) => TAG_COLORS[d.tag]);

    // Tag abbreviation
    nodeEnter.append('text')
      .attr('text-anchor', 'middle').attr('dy', 4)
      .attr('fill', '#fff').attr('font-size', '10px')
      .attr('font-weight', '700').attr('letter-spacing', '0.05em')
      .text((d) => d.tag.slice(0, 3));

    // Agent label
    nodeEnter.append('text')
      .attr('text-anchor', 'middle').attr('dy', 44)
      .attr('fill', '#e2e8f0').attr('font-size', '11px').attr('font-weight', '500')
      .text((d) => d.agentId.split(' ')[0]);

    // Fade in new nodes
    nodeEnter.transition().duration(500).style('opacity', 1);

    // ── Links ─────────────────────────────────────────────────────────────────
    const linkLines = content
      .select<SVGGElement>('.links-group')
      .selectAll<SVGLineElement, GraphLink>('line.graph-link')
      .data(linksData, (d) => `${String(d.source)}->${String(d.target)}`);

    linkLines.exit().remove();

    const linkEnter = linkLines
      .enter()
      .append('line')
      .attr('class', 'graph-link')
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0)
      .attr('stroke-dasharray', (d) => {
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        return nodesData.find((n) => n.id === srcId)?.isCounterfactual ? '6 4' : 'none';
      })
      .attr('stroke', (d) => {
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        const src = nodesData.find((n) => n.id === srcId);
        return src ? TAG_COLORS[src.tag] : 'rgba(255,255,255,0.3)';
      })
      .attr('marker-end', (d) => {
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        const src = nodesData.find((n) => n.id === srcId);
        return src ? `url(#arrow-${src.tag})` : '';
      });

    linkEnter.transition().duration(400).attr('stroke-opacity', 0.65);

    // ── Feed simulation ───────────────────────────────────────────────────────
    sim.nodes(nodesData);
    (sim.force('link') as d3.ForceLink<GraphNode, GraphLink>).links(linksData);
    sim.alpha(0.4).restart();

    sim.on('tick', () => {
      content.select('.links-group')
        .selectAll<SVGLineElement, GraphLink>('line.graph-link')
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      content.select('.nodes-group')
        .selectAll<SVGGElement, GraphNode>('g.node-group')
        .attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });
  }, [nodes, links, dimensions]);

  // ── Render ─────────────────────────────────────────────────────────────────
  const hasNodes = nodes.length > 0;

  return (
    <div className="canvas-container">

      {/* Header HUD */}
      <div className="hud-overlay header-hud">
        <div className="title-badge">
          <div className="pulse-dot" />
          <span className="title-text">PXP Blackboard Visualizer</span>
        </div>

        {/* LIVE / REPLAY / MOCK mode badge */}
        {boardMode && (
          <span className={modeBadgeClass(boardMode)}>
            {modeBadgeLabel(boardMode)}
          </span>
        )}

        {/* WebSocket status */}
        <div className="status-badge">
          <span className={`status-indicator ${wsStatus}`} />
          <span className="status-text">WS: {wsStatus.toUpperCase()}</span>
        </div>

        {streamDone && (
          <div className="stream-done-badge">✓ Stream Complete</div>
        )}
      </div>

      {/* Tag legend */}
      <div className="hud-overlay legend-hud">
        <span className="hud-label">Tags:</span>
        {(Object.entries(TAG_COLORS) as [PXPTag, string][]).map(([tag, color]) => (
          <div key={tag} className="legend-item">
            <span className="legend-dot" style={{ backgroundColor: color }} />
            <span className="legend-text">{tag}</span>
          </div>
        ))}
      </div>

      {/* SVG canvas */}
      <svg
        ref={svgRef}
        id="board-canvas"
        className="viewport-svg"
        width={dimensions.width}
        height={dimensions.height}
      >
        <defs>
          <pattern id="grid-pattern" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
            <circle cx="40" cy="40" r="1.5" fill="rgba(99,102,241,0.12)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid-pattern)" />
      </svg>

      {/* Empty-state waiting overlay */}
      {!hasNodes && (
        <div className="empty-state-overlay">
          <div className="empty-state-ring" />
          <p className="empty-state-label">
            {wsStatus === 'connected'
              ? 'Waiting for board events…'
              : wsStatus === 'connecting'
              ? 'Connecting to server…'
              : '⚠ Server offline — start with:\nuvicorn ui.server.main:app --reload'}
          </p>
        </div>
      )}

      {/* Footer counter */}
      <div className="hud-overlay footer-hud">
        <span className="hud-label">S4 — Live Graph</span>
        <span className="hud-detail">{nodes.length} nodes · {links.length} edges</span>
      </div>

      {/* Node detail drawer */}
      {selectedNode && (
        <div className="node-drawer">
          <div className="drawer-header">
            <span
              className="drawer-tag-badge"
              style={{ backgroundColor: TAG_COLORS[selectedNode.tag] }}
            >
              {selectedNode.tag}
            </span>
            <span className="drawer-title">{selectedNode.agentId}</span>
            <button className="drawer-close" onClick={() => setSelectedNode(null)}>×</button>
          </div>
          <div className="drawer-body">
            <div className="drawer-field">
              <span className="field-label">Entry ID</span>
              <span className="field-value mono">{selectedNode.id}</span>
            </div>
            {selectedNode.targetEntryId && (
              <div className="drawer-field">
                <span className="field-label">Refers To</span>
                <span className="field-value mono">{selectedNode.targetEntryId}</span>
              </div>
            )}
            {selectedNode.timestamp && (
              <div className="drawer-field">
                <span className="field-label">Timestamp</span>
                <span className="field-value mono">
                  {new Date(selectedNode.timestamp).toLocaleTimeString()}
                </span>
              </div>
            )}
            <div className="drawer-field">
              <span className="field-label">Prediction (What)</span>
              <p className="field-text">{selectedNode.prediction}</p>
            </div>
            <div className="drawer-field">
              <span className="field-label">Explanation (Why)</span>
              <p className="field-text">{selectedNode.explanation}</p>
            </div>
            {selectedNode.isCounterfactual && (
              <div className="cf-badge">★ Counterfactual Simulation Entry</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
