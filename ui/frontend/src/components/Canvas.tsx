import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as d3 from 'd3';
import { TAG_COLORS } from '../constants/tagColors';
import type { PXPTag } from '../constants/tagColors';
import { HistoryScrubber } from './HistoryScrubber';
import { TokenTally } from './TokenTally';
import type { TokenTallyReport } from './TokenTally';

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
  tokenCount?: {
    prediction_tokens: number;
    explanation_tokens: number;
    total_tokens: number;
  };
  isBottleneck?: boolean;
  depth?: number;
}

export interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  relation: string;
  isBottleneck?: boolean;
}

export type BoardMode = 'LIVE_TAP' | 'LOG_REPLAY' | 'MOCK_STREAM' | null;
export type LayoutMode = 'radial' | 'force';
export type ViewMode = 'unified' | 'split';

interface CanvasProps {
  wsStatus?: 'connected' | 'connecting' | 'disconnected';
  boardMode?: BoardMode;
  nodes?: GraphNode[];
  links?: GraphLink[];
  streamDone?: boolean;
  tokenTally?: TokenTallyReport;
  scrubIndex?: number | null;
  onScrub?: (index: number | null) => void;
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
// Bottleneck Detection
// ──────────────────────────────────────────────────────────────────────────────

interface BottleneckCluster {
  targetId: string;
  nodeIds: string[];
  reason: string;
}

function detectBottlenecks(nodes: GraphNode[]): {
  bottleneckNodeIds: Set<string>;
  clusters: BottleneckCluster[];
} {
  const bottleneckNodeIds = new Set<string>();
  const clusters: BottleneckCluster[] = [];

  const negativeByTarget: Record<string, string[]> = {};
  nodes.forEach((n) => {
    if ((n.tag === 'REFUTE' || n.tag === 'REJECT') && n.targetEntryId) {
      if (!negativeByTarget[n.targetEntryId]) {
        negativeByTarget[n.targetEntryId] = [];
      }
      negativeByTarget[n.targetEntryId].push(n.id);
    }
  });

  Object.entries(negativeByTarget).forEach(([targetId, conflictingIds]) => {
    if (conflictingIds.length >= 2) {
      bottleneckNodeIds.add(targetId);
      conflictingIds.forEach((id) => bottleneckNodeIds.add(id));
      clusters.push({
        targetId,
        nodeIds: [targetId, ...conflictingIds],
        reason: `Cascading disagreement (${conflictingIds.length} refutations/rejections on entry ${targetId})`,
      });
    }
  });

  let consecutiveNeg: string[] = [];
  nodes.forEach((n) => {
    if (n.tag === 'REFUTE' || n.tag === 'REJECT') {
      consecutiveNeg.push(n.id);
      if (consecutiveNeg.length >= 2) {
        consecutiveNeg.forEach((id) => bottleneckNodeIds.add(id));
      }
    } else {
      consecutiveNeg = [];
    }
  });

  return { bottleneckNodeIds, clusters };
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
  tokenTally = { total_tokens: 0, turn_count: 0, agents: {} },
  scrubIndex = null,
  onScrub = () => {},
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simulationRef = useRef<d3.Simulation<GraphNode, GraphLink> | null>(null);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('radial');
  const [viewMode, setViewMode] = useState<ViewMode>('unified');
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

  useEffect(() => {
    if (scrubIndex !== null && nodes[scrubIndex]) {
      setSelectedNode(nodes[scrubIndex]);
    }
  }, [scrubIndex, nodes]);

  const { bottleneckNodeIds, clusters } = useMemo(
    () => detectBottlenecks(nodes),
    [nodes],
  );

  // Counterfactual nodes for split view
  const cfNodes = useMemo(() => nodes.filter((n) => n.isCounterfactual), [nodes]);
  const mainNodes = useMemo(() => nodes.filter((n) => !n.isCounterfactual), [nodes]);

  // ── D3 incremental update ──────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);

    if (!simulationRef.current) {
      svg.append('g').attr('class', 'graph-content');

      const zoom = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.15, 4])
        .on('zoom', (event) =>
          svg.select('.graph-content').attr('transform', event.transform),
        );
      svg.call(zoom as any);

      const content = svg.select<SVGGElement>('.graph-content');
      const defs = content.append('defs');

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

      defs
        .append('marker')
        .attr('id', 'arrow-bottleneck')
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 42)
        .attr('refY', 0)
        .attr('markerWidth', 9)
        .attr('markerHeight', 9)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', '#ef4444');

      content.append('g').attr('class', 'links-group');
      content.append('g').attr('class', 'nodes-group');

      simulationRef.current = d3
        .forceSimulation<GraphNode>()
        .force(
          'link',
          d3.forceLink<GraphNode, GraphLink>().id((d) => d.id).distance(140),
        )
        .force('charge', d3.forceManyBody().strength(-400))
        .force('center', d3.forceCenter(dimensions.width / 2, dimensions.height / 2))
        .force('collide', d3.forceCollide().radius(60));
    }

    const sim = simulationRef.current!;
    const content = svg.select<SVGGElement>('.graph-content');

    const visibleNodes =
      scrubIndex !== null && scrubIndex < nodes.length - 1
        ? nodes.slice(0, scrubIndex + 1)
        : nodes;
    const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));
    const visibleLinks = links.filter((l) => {
      const s = typeof l.source === 'object' ? (l.source as any).id : l.source;
      const t = typeof l.target === 'object' ? (l.target as any).id : l.target;
      return visibleNodeIds.has(s) && visibleNodeIds.has(t);
    });

    // Compute center based on ViewMode
    const isSplit = viewMode === 'split';
    const leftCx = dimensions.width * 0.28;
    const rightCx = dimensions.width * 0.72;
    const cy = dimensions.height / 2;

    const depthMap = new Map<string, number>();
    const rootNodes = visibleNodes.filter((n) => !n.targetEntryId);
    const rootId = rootNodes.length > 0 ? rootNodes[0].id : visibleNodes[0]?.id;

    if (rootId) {
      depthMap.set(rootId, 0);
      const queue = [rootId];
      while (queue.length > 0) {
        const curr = queue.shift()!;
        const currDepth = depthMap.get(curr)!;
        visibleNodes
          .filter((n) => n.targetEntryId === curr)
          .forEach((child) => {
            if (!depthMap.has(child.id)) {
              depthMap.set(child.id, currDepth + 1);
              queue.push(child.id);
            }
          });
      }
    }

    const nodesByDepth = new Map<number, GraphNode[]>();
    visibleNodes.forEach((n) => {
      const d = depthMap.get(n.id) ?? (n.targetEntryId ? 1 : 0);
      if (!nodesByDepth.has(d)) nodesByDepth.set(d, []);
      nodesByDepth.get(d)!.push(n);
    });

    const nodesData: GraphNode[] = visibleNodes.map((d) => {
      const existing = sim.nodes().find((n) => n.id === d.id);
      const depth = depthMap.get(d.id) ?? 0;
      const siblings = nodesByDepth.get(depth) || [d];
      const sibIdx = siblings.indexOf(d);
      const totalSib = siblings.length;

      const nodeCx = isSplit ? (d.isCounterfactual ? rightCx : leftCx) : dimensions.width / 2;

      let radialTargetX = nodeCx;
      let radialTargetY = cy;
      if (depth > 0) {
        const radius = depth * 140;
        const angle = (sibIdx / totalSib) * 2 * Math.PI - Math.PI / 2;
        radialTargetX = nodeCx + radius * Math.cos(angle);
        radialTargetY = cy + radius * Math.sin(angle);
      }

      const isAlert = bottleneckNodeIds.has(d.id);

      return existing
        ? {
            ...d,
            x: existing.x,
            y: existing.y,
            vx: existing.vx,
            vy: existing.vy,
            isBottleneck: isAlert,
            depth,
            targetX: radialTargetX,
            targetY: radialTargetY,
          }
        : {
            ...d,
            x: radialTargetX + (Math.random() - 0.5) * 40,
            y: radialTargetY + (Math.random() - 0.5) * 40,
            isBottleneck: isAlert,
            depth,
            targetX: radialTargetX,
            targetY: radialTargetY,
          };
    });

    const linksData: GraphLink[] = visibleLinks.map((d) => {
      const srcId = typeof d.source === 'object' ? (d.source as any).id : d.source;
      const tgtId = typeof d.target === 'object' ? (d.target as any).id : d.target;
      const isBottleneckEdge =
        bottleneckNodeIds.has(srcId) && bottleneckNodeIds.has(tgtId);
      return { ...d, isBottleneck: isBottleneckEdge };
    });

    // Update Simulation Forces
    if (layoutMode === 'radial') {
      sim.force('center', d3.forceCenter(dimensions.width / 2, cy).strength(isSplit ? 0.05 : 0.3));
      sim.force('charge', d3.forceManyBody().strength(-200));
      sim.force(
        'x',
        d3.forceX<GraphNode>((d) => (d as any).targetX || dimensions.width / 2).strength(0.6),
      );
      sim.force(
        'y',
        d3.forceY<GraphNode>((d) => (d as any).targetY || cy).strength(0.6),
      );
    } else {
      (sim.force('x') as any)?.strength(0);
      (sim.force('y') as any)?.strength(0);
      sim.force('center', d3.forceCenter(dimensions.width / 2, cy).strength(1));
      sim.force('charge', d3.forceManyBody().strength(-450));
    }

    // ── Nodes Enter/Update ───────────────────────────────────────────────────
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
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });
    nodeEnter.call(drag as any);

    // Glow ring / Simulation dashed ring
    nodeEnter
      .append('circle')
      .attr('class', 'glow-ring')
      .attr('r', 30)
      .attr('fill', (d) => TAG_COLORS[d.tag])
      .attr('fill-opacity', 0.18)
      .attr('stroke', (d) => TAG_COLORS[d.tag])
      .attr('stroke-width', 2)
      .attr('filter', 'drop-shadow(0px 0px 12px rgba(0,0,0,0.6))');

    // Filled core
    nodeEnter
      .append('circle')
      .attr('class', 'core-circle')
      .attr('r', 22)
      .attr('fill', (d) => TAG_COLORS[d.tag]);

    // Tag abbreviation
    nodeEnter
      .append('text')
      .attr('class', 'tag-text')
      .attr('text-anchor', 'middle')
      .attr('dy', 4)
      .attr('fill', '#fff')
      .attr('font-size', '10px')
      .attr('font-weight', '700')
      .attr('letter-spacing', '0.05em')
      .text((d) => d.tag.slice(0, 3));

    // Agent label
    nodeEnter
      .append('text')
      .attr('class', 'agent-label')
      .attr('text-anchor', 'middle')
      .attr('dy', 44)
      .attr('fill', '#e2e8f0')
      .attr('font-size', '11px')
      .attr('font-weight', '500')
      .text((d) => d.agentId.split(' ')[0]);

    // Simulated / Sandbox badge above node
    nodeEnter
      .filter((d) => !!d.isCounterfactual)
      .append('text')
      .attr('class', 'sim-node-badge')
      .attr('text-anchor', 'middle')
      .attr('dy', -36)
      .attr('fill', '#38bdf8')
      .attr('font-size', '9px')
      .attr('font-weight', '700')
      .text('🧪 SIMULATED');

    nodeEnter.transition().duration(500).style('opacity', 1);

    // Apply active styling
    const activeNodeId =
      scrubIndex !== null && nodes[scrubIndex] ? nodes[scrubIndex].id : null;

    content
      .selectAll<SVGGElement, GraphNode>('g.node-group')
      .classed('bottleneck-alert', (d) => !!d.isBottleneck)
      .classed('counterfactual-node', (d) => !!d.isCounterfactual)
      .select('circle.glow-ring')
      .attr('stroke', (d) =>
        d.id === activeNodeId
          ? '#fbbf24'
          : d.isBottleneck
          ? '#ef4444'
          : d.isCounterfactual
          ? '#38bdf8'
          : TAG_COLORS[d.tag],
      )
      .attr('stroke-width', (d) =>
        d.id === activeNodeId ? 4 : d.isBottleneck ? 3.5 : d.isCounterfactual ? 3 : 2,
      )
      .attr('stroke-dasharray', (d) => (d.isCounterfactual ? '4 2' : 'none'))
      .attr('fill-opacity', (d) =>
        d.id === activeNodeId ? 0.4 : d.isBottleneck ? 0.35 : 0.22,
      );

    // ── Links Enter/Update ───────────────────────────────────────────────────
    const linkLines = content
      .select<SVGGElement>('.links-group')
      .selectAll<SVGLineElement, GraphLink>('line.graph-link')
      .data(linksData, (d) => `${String(d.source)}->${String(d.target)}`);

    linkLines.exit().remove();

    const linkEnter = linkLines
      .enter()
      .append('line')
      .attr('class', 'graph-link')
      .attr('stroke-width', (d) => (d.isBottleneck ? 3 : 2))
      .attr('stroke-opacity', 0)
      .attr('stroke-dasharray', (d) => {
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        return nodesData.find((n) => n.id === srcId)?.isCounterfactual ? '6 4' : 'none';
      })
      .attr('stroke', (d) => {
        if (d.isBottleneck) return '#ef4444';
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        const src = nodesData.find((n) => n.id === srcId);
        return src ? (src.isCounterfactual ? '#38bdf8' : TAG_COLORS[src.tag]) : 'rgba(255,255,255,0.3)';
      })
      .attr('marker-end', (d) => {
        if (d.isBottleneck) return 'url(#arrow-bottleneck)';
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        const src = nodesData.find((n) => n.id === srcId);
        return src ? `url(#arrow-${src.tag})` : '';
      });

    linkEnter.transition().duration(400).attr('stroke-opacity', 0.7);

    content
      .selectAll<SVGLineElement, GraphLink>('line.graph-link')
      .classed('bottleneck-link', (d) => !!d.isBottleneck)
      .classed('cf-link', (d) => {
        const srcId = typeof d.source === 'object' ? d.source.id : String(d.source);
        return !!nodesData.find((n) => n.id === srcId)?.isCounterfactual;
      });

    sim.nodes(nodesData);
    (sim.force('link') as d3.ForceLink<GraphNode, GraphLink>).links(linksData);
    sim.alpha(0.35).restart();

    sim.on('tick', () => {
      content
        .select('.links-group')
        .selectAll<SVGLineElement, GraphLink>('line.graph-link')
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      content
        .select('.nodes-group')
        .selectAll<SVGGElement, GraphNode>('g.node-group')
        .attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });
  }, [nodes, links, dimensions, scrubIndex, layoutMode, viewMode, bottleneckNodeIds]);

  // ── Render ─────────────────────────────────────────────────────────────────
  const hasNodes = nodes.length > 0;
  const hasBottlenecks = clusters.length > 0;

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

        {/* View Mode Toggle: Unified vs Split Alternative Timeline (W3 Day 1) */}
        <div className="view-mode-selector">
          <button
            id="btn-view-unified"
            className={`view-btn ${viewMode === 'unified' ? 'active' : ''}`}
            onClick={() => setViewMode('unified')}
            title="Unified DAG Canvas"
          >
            ◻ Unified
          </button>
          <button
            id="btn-view-split"
            className={`view-btn ${viewMode === 'split' ? 'active' : ''}`}
            onClick={() => setViewMode('split')}
            title="Split-Panel Alternative Timeline View"
          >
            ◫ Split Timeline
          </button>
        </div>

        {/* Layout Mode Selector */}
        <div className="layout-selector">
          <button
            id="btn-layout-radial"
            className={`layout-btn ${layoutMode === 'radial' ? 'active' : ''}`}
            onClick={() => setLayoutMode('radial')}
            title="Concentric Radial Tree layout"
          >
            ◎ Radial
          </button>
          <button
            id="btn-layout-force"
            className={`layout-btn ${layoutMode === 'force' ? 'active' : ''}`}
            onClick={() => setLayoutMode('force')}
            title="Physics force-directed layout"
          >
            ☵ Force
          </button>
        </div>

        {/* Token Tally Pill */}
        <TokenTally tally={tokenTally} />

        {streamDone && <div className="stream-done-badge">✓ Stream Complete</div>}
      </div>

      {/* Split Panel Headers when Split View active (W3 Day 1) */}
      {viewMode === 'split' && (
        <div className="split-view-headers">
          <div className="split-panel-title left-title">
            <span className="panel-dot live-dot" />
            <strong>Live Mainline Board</strong>
            <span className="panel-count">({mainNodes.length} entries)</span>
          </div>
          <div className="split-divider-line" />
          <div className="split-panel-title right-title">
            <span className="panel-dot cf-dot" />
            <strong>Rollback & Sandbox Simulation</strong>
            <span className="panel-count">({cfNodes.length} simulated entries)</span>
          </div>
        </div>
      )}

      {/* Bottleneck Alert Banner */}
      {hasBottlenecks && (
        <div className="bottleneck-banner">
          <div className="alert-flashing-icon">🚨</div>
          <div className="alert-content">
            <strong>Bottleneck Alert:</strong> <span>{clusters[0].reason}</span>
          </div>
          <button
            className="alert-jump-btn"
            onClick={() => {
              const targetNode = nodes.find((n) => n.id === clusters[0].targetId);
              if (targetNode) setSelectedNode(targetNode);
            }}
          >
            Inspect Target
          </button>
        </div>
      )}

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
          <pattern
            id="grid-pattern"
            width="40"
            height="40"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 40 0 L 0 0 0 40"
              fill="none"
              stroke="rgba(255,255,255,0.04)"
              strokeWidth="1"
            />
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

      {/* History Scrubber Controls */}
      <HistoryScrubber
        nodes={nodes}
        scrubIndex={scrubIndex}
        onScrub={onScrub}
      />

      {/* Footer counter */}
      <div className="hud-overlay footer-hud">
        <span className="hud-label">S4 — {viewMode === 'split' ? 'Split Timeline' : 'Unified'} View</span>
        <span className="hud-detail">
          {nodes.length} nodes ({cfNodes.length} simulated) · {links.length} edges
        </span>
      </div>

      {/* Node detail drawer / inspector */}
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
            <button
              id="btn-close-drawer"
              className="drawer-close"
              onClick={() => setSelectedNode(null)}
            >
              ×
            </button>
          </div>
          <div className="drawer-body">
            {selectedNode.isCounterfactual && (
              <div className="drawer-cf-alert">
                🧪 <strong>Isolated Sandbox Simulation Entry</strong>
                <p className="cf-subtext">Rollback alternative branch tested by Student 1 sandbox.</p>
              </div>
            )}
            {selectedNode.isBottleneck && (
              <div className="drawer-bottleneck-alert">
                ⚠️ Bottleneck conflict node (cascading refutations / deadlock)
              </div>
            )}
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
            {selectedNode.tokenCount && (
              <div className="drawer-field">
                <span className="field-label">Turn Token Cost</span>
                <div className="drawer-token-chips">
                  <span className="token-chip-mini">
                    Pred: <strong>{selectedNode.tokenCount.prediction_tokens}</strong>
                  </span>
                  <span className="token-chip-mini">
                    Expl: <strong>{selectedNode.tokenCount.explanation_tokens}</strong>
                  </span>
                  <span className="token-chip-mini total">
                    Total: <strong>{selectedNode.tokenCount.total_tokens}</strong>
                  </span>
                </div>
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
          </div>
        </div>
      )}
    </div>
  );
};
