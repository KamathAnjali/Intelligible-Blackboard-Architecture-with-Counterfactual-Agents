import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';

export type PXPTag = 'PROPOSE' | 'RATIFY' | 'REVISE' | 'REFUTE' | 'REJECT';

export interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  agentId: string;
  tag: PXPTag;
  prediction: string;
  explanation: string;
  targetEntryId?: string;
  isCounterfactual?: boolean;
}

export interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  relation: string;
}

interface CanvasProps {
  wsStatus?: 'connected' | 'connecting' | 'disconnected';
  nodes?: GraphNode[];
  links?: GraphLink[];
}

// Default static mock graph nodes for Day 2 prototype
const DEFAULT_MOCK_NODES: GraphNode[] = [
  {
    id: 'e-001',
    agentId: 'Agent_Alpha (Proposer)',
    tag: 'PROPOSE',
    prediction: 'x = 12',
    explanation: 'Initial hypothesis based on equation x^2 = 144.',
  },
  {
    id: 'e-002',
    agentId: 'Agent_Beta (Verifier)',
    tag: 'RATIFY',
    prediction: 'x = 12 verified',
    explanation: 'Checked 12^2 = 144. Aligns with proposed thesis.',
    targetEntryId: 'e-001',
  },
  {
    id: 'e-003',
    agentId: 'Agent_Gamma (Critic)',
    tag: 'REFUTE',
    prediction: 'Solution incomplete: x = -12 is omitted',
    explanation: '(-12)^2 = 144 is also a valid integer solution under domain.',
    targetEntryId: 'e-001',
  },
  {
    id: 'e-004',
    agentId: 'Agent_Alpha (Proposer)',
    tag: 'REVISE',
    prediction: 'Updated Solution: x ∈ {-12, 12}',
    explanation: 'Incorporating negative root into updated thesis after refutation.',
    targetEntryId: 'e-003',
  },
  {
    id: 'e-005',
    agentId: 'Agent_Beta (Verifier)',
    tag: 'RATIFY',
    prediction: 'Consensus Reached: {-12, 12}',
    explanation: 'Ratifying revised solution set. All domain conditions satisfied.',
    targetEntryId: 'e-004',
  },
  {
    id: 'e-006',
    agentId: 'Agent_Delta (CF Sandbox)',
    tag: 'REJECT',
    prediction: 'Counterfactual Branch Rejected',
    explanation: 'Simulated alternate thesis led to contradiction in sandbox.',
    targetEntryId: 'e-003',
    isCounterfactual: true,
  },
];

const DEFAULT_MOCK_LINKS: GraphLink[] = [
  { source: 'e-002', target: 'e-001', relation: 'RATIFIES' },
  { source: 'e-003', target: 'e-001', relation: 'REFUTES' },
  { source: 'e-004', target: 'e-003', relation: 'REVISES_AFTER' },
  { source: 'e-005', target: 'e-004', relation: 'RATIFIES' },
  { source: 'e-006', target: 'e-003', relation: 'SIMULATES_ALT' },
];

export const TAG_COLORS: Record<PXPTag, string> = {
  PROPOSE: '#8b5cf6', // Purple
  RATIFY: '#10b981',  // Emerald Green
  REVISE: '#6366f1',  // Indigo Blue
  REFUTE: '#f59e0b',  // Amber Orange
  REJECT: '#ef4444',  // Rose Red
};

export const Canvas: React.FC<CanvasProps> = ({
  wsStatus = 'disconnected',
  nodes = DEFAULT_MOCK_NODES,
  links = DEFAULT_MOCK_LINKS,
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
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

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('.graph-content').remove();

    const width = dimensions.width;
    const height = dimensions.height;

    // Create main container group for zoom/pan
    const container = svg.append('g').attr('class', 'graph-content');

    // Zoom behavior
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on('zoom', (event) => {
        container.attr('transform', event.transform);
      });

    svg.call(zoom as any);

    // Deep clone nodes and links for D3 mutation safety
    const nodesData: GraphNode[] = nodes.map((d) => ({ ...d }));
    const linksData: GraphLink[] = links.map((d) => ({ ...d }));

    // Define Arrow Marker for Directed Edges
    const defs = container.append('defs');
    Object.entries(TAG_COLORS).forEach(([tag, color]) => {
      defs.append('marker')
        .attr('id', `arrow-${tag}`)
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 38)
        .attr('refY', 0)
        .attr('markerWidth', 7)
        .attr('markerHeight', 7)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', color);
    });

    // D3 Force Simulation Setup
    const simulation = d3.forceSimulation<GraphNode>(nodesData)
      .force('link', d3.forceLink<GraphNode, GraphLink>(linksData).id((d) => d.id).distance(140))
      .force('charge', d3.forceManyBody().strength(-400))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide().radius(50));

    // Render Edges
    const link = container.append('g')
      .attr('class', 'links')
      .selectAll('line')
      .data(linksData)
      .enter()
      .append('line')
      .attr('stroke-width', 2)
      .attr('stroke', (d) => {
        const targetNode = nodesData.find((n) => n.id === (typeof d.target === 'object' ? d.target.id : d.target));
        return targetNode ? TAG_COLORS[targetNode.tag] : 'rgba(255, 255, 255, 0.3)';
      })
      .attr('stroke-opacity', 0.6)
      .attr('stroke-dasharray', (d) => {
        const sourceNode = nodesData.find((n) => n.id === (typeof d.source === 'object' ? d.source.id : d.source));
        return sourceNode?.isCounterfactual ? '4 4' : 'none';
      })
      .attr('marker-end', (d) => {
        const targetNode = nodesData.find((n) => n.id === (typeof d.target === 'object' ? d.target.id : d.target));
        return targetNode ? `url(#arrow-${targetNode.tag})` : '';
      });

    // Render Nodes Group
    const node = container.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(nodesData)
      .enter()
      .append('g')
      .attr('class', 'node-group')
      .style('cursor', 'pointer')
      .on('click', (_event, d) => setSelectedNode(d));

    // Drag functionality
    const drag = d3.drag<SVGGElement, GraphNode>()
      .on('start', (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
      });

    node.call(drag as any);

    // Node Glowing Background Circle
    node.append('circle')
      .attr('r', 28)
      .attr('fill', (d) => TAG_COLORS[d.tag])
      .attr('fill-opacity', 0.2)
      .attr('stroke', (d) => TAG_COLORS[d.tag])
      .attr('stroke-width', 2)
      .attr('filter', 'drop-shadow(0px 0px 8px rgba(0,0,0,0.5))');

    // Inner Solid Node Circle
    node.append('circle')
      .attr('r', 20)
      .attr('fill', (d) => TAG_COLORS[d.tag]);

    // Tag Text Badge Inside Circle
    node.append('text')
      .text((d) => d.tag.slice(0, 3))
      .attr('text-anchor', 'middle')
      .attr('dy', 4)
      .attr('fill', '#ffffff')
      .attr('font-size', '10px')
      .attr('font-weight', '700')
      .attr('letter-spacing', '0.05em');

    // Agent Label Below Node
    node.append('text')
      .text((d) => d.agentId.split(' ')[0])
      .attr('text-anchor', 'middle')
      .attr('dy', 42)
      .attr('fill', '#e2e8f0')
      .attr('font-size', '11px')
      .attr('font-weight', '500');

    // Update positions on simulation tick
    simulation.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);

      node.attr('transform', (d) => `translate(${d.x}, ${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [nodes, links, dimensions]);

  return (
    <div className="canvas-container">
      {/* Header HUD */}
      <div className="hud-overlay header-hud">
        <div className="title-badge">
          <div className="pulse-dot" />
          <span className="title-text">PXP Blackboard Visualizer</span>
        </div>
        <div className="status-badge">
          <span className={`status-indicator ${wsStatus}`} />
          <span className="status-text">WS: {wsStatus.toUpperCase()}</span>
        </div>
      </div>

      {/* Legend HUD */}
      <div className="hud-overlay legend-hud">
        <span className="hud-label">PXP Tags:</span>
        {Object.entries(TAG_COLORS).map(([tag, color]) => (
          <div key={tag} className="legend-item">
            <span className="legend-dot" style={{ backgroundColor: color }} />
            <span className="legend-text">{tag}</span>
          </div>
        ))}
      </div>

      {/* SVG Canvas */}
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
              stroke="rgba(255, 255, 255, 0.04)"
              strokeWidth="1"
            />
            <circle cx="40" cy="40" r="1.5" fill="rgba(99, 102, 241, 0.12)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid-pattern)" />
      </svg>

      {/* Selected Node Details Drawer */}
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
            <button className="drawer-close" onClick={() => setSelectedNode(null)}>
              ×
            </button>
          </div>
          <div className="drawer-body">
            <div className="drawer-field">
              <span className="field-label">Entry ID:</span>
              <span className="field-value mono">{selectedNode.id}</span>
            </div>
            {selectedNode.targetEntryId && (
              <div className="drawer-field">
                <span className="field-label">Target Entry:</span>
                <span className="field-value mono">{selectedNode.targetEntryId}</span>
              </div>
            )}
            <div className="drawer-field">
              <span className="field-label">Prediction ("What"):</span>
              <p className="field-text">{selectedNode.prediction}</p>
            </div>
            <div className="drawer-field">
              <span className="field-label">Explanation ("Why"):</span>
              <p className="field-text">{selectedNode.explanation}</p>
            </div>
            {selectedNode.isCounterfactual && (
              <div className="cf-badge">
                ★ Counterfactual Simulation Entry
              </div>
            )}
          </div>
        </div>
      )}

      {/* Footer HUD */}
      <div className="hud-overlay footer-hud">
        <span className="hud-label">Student 4 — D3 Force Graph Prototype</span>
        <span className="hud-detail">{nodes.length} Nodes | {links.length} Edges</span>
      </div>
    </div>
  );
};
