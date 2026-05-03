/**
 * Polish slice CP1: Data Explorer.
 *
 * Force-directed graph view of the Overseer's interpretive data.
 * Nodes: questions, projects, patterns, drift, themes, episodes,
 * filed gists. Edges: evidence (gist→question), derived_from
 * (pattern/drift→gist), in_project (gist→project).
 *
 * Aesthetic stake (CP1):
 *   - Dark slate background, no grid, no minimap
 *   - Soft filled circles, sized by importance, saturation by confidence
 *   - Edges thin (1px) and slightly transparent, color by relationship
 *   - Hover: lift the node + brighten its incident edges
 *   - Click: focus mode — dim everything not within 2 hops
 *
 * Layout: d3-force runs once on data load (no animation), positions
 * are baked into the react-flow nodes. Reload triggers re-layout.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  ReactFlowProvider,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeProps,
  Handle,
  Position,
} from '@xyflow/react'
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
} from 'd3-force'
import '@xyflow/react/dist/style.css'

// ── Types from the Pi /explorer/graph endpoint ───────────────

export interface GraphNode {
  id: string                 // token, e.g. "q:42"
  type:
    | 'question'
    | 'project'
    | 'pattern'
    | 'drift'
    | 'theme'
    | 'gist'
    | 'episode'
  label: string
  confidence: 'low' | 'med' | 'high'
  size_hint: number
  metadata?: Record<string, any>
}

export interface GraphEdge {
  source: string
  target: string
  kind: 'evidence' | 'derived_from' | 'in_project'
  label?: string
  contribution?: string
}

export interface GraphResp {
  ok: boolean
  nodes?: GraphNode[]
  edges?: GraphEdge[]
  stats?: {
    nodes_total: number
    edges_total: number
    by_type: Record<string, number>
  }
  error?: string
}

// ── Aesthetic mapping ────────────────────────────────────────

const TYPE_FILL: Record<GraphNode['type'], string> = {
  question: '#7c5cff',  // accent purple
  project:  '#8da4cc',  // muted blue-grey (utility, not primary)
  pattern:  '#f59e0b',  // amber
  drift:    '#10b981',  // emerald
  theme:    '#a78bfa',  // soft violet
  gist:     '#94a3b8',  // slate
  episode:  '#f472b6',  // rose
}

const TYPE_LABEL: Record<GraphNode['type'], string> = {
  question: 'Question',
  project:  'Project',
  pattern:  'Pattern',
  drift:    'Drift',
  theme:    'Theme',
  gist:     'Gist',
  episode:  'Episode',
}

const EDGE_COLOR: Record<GraphEdge['kind'], string> = {
  evidence:     '#7c5cff66',  // purple, alpha
  derived_from: '#f59e0b55',  // amber, alpha
  in_project:   '#94a3b833',  // slate, very faint (ambient)
}

const CONF_OPACITY: Record<GraphNode['confidence'], number> = {
  high: 1.0,
  med:  0.7,
  low:  0.45,
}

// Size in px — soft minimum so single-evidence nodes don't disappear,
// log-ish growth so a 30-evidence question doesn't dwarf the canvas.
function nodeRadius(n: GraphNode): number {
  const base = 16
  const bump = Math.min(28, Math.sqrt(Math.max(1, n.size_hint)) * 6)
  return base + bump
}

// ── d3-force layout ───────────────────────────────────────────

interface PositionedNode extends GraphNode {
  x: number
  y: number
}

function runLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  width: number,
  height: number,
): PositionedNode[] {
  // Clone — d3 mutates.
  const sNodes = nodes.map((n) => ({ ...n })) as PositionedNode[]
  const sLinks = edges
    // d3-force needs source/target by id (resolves to node refs internally)
    .filter(
      (e) =>
        sNodes.some((n) => n.id === e.source) &&
        sNodes.some((n) => n.id === e.target),
    )
    .map((e) => ({ source: e.source, target: e.target }))

  const sim = forceSimulation(sNodes as any)
    .force(
      'link',
      forceLink(sLinks as any)
        .id((d: any) => d.id)
        .distance(110)
        .strength(0.4),
    )
    .force('charge', forceManyBody().strength(-280))
    .force('center', forceCenter(width / 2, height / 2))
    .force(
      'collide',
      forceCollide()
        .radius((d: any) => nodeRadius(d) + 8)
        .iterations(2),
    )
    .stop()

  // 300 ticks gives a stable layout for 50-150 nodes
  for (let i = 0; i < 300; i++) sim.tick()
  return sNodes
}

// ── Custom node renderer ─────────────────────────────────────

interface CustomNodeData {
  graph: GraphNode
  active: boolean
  dimmed: boolean
}

function CircleNode({ data }: NodeProps) {
  const { graph, active, dimmed } = data as unknown as CustomNodeData
  const r = nodeRadius(graph)
  const fill = TYPE_FILL[graph.type]
  const opacity = (dimmed ? 0.18 : 1) * CONF_OPACITY[graph.confidence]
  // Slightly larger / glowing if active (focus mode root or hovered)
  const ring = active ? 3 : 0
  const totalR = r + ring
  return (
    <div
      style={{
        width: totalR * 2,
        height: totalR * 2,
        position: 'relative',
        transition: 'opacity 200ms ease',
        opacity,
      }}
      title={`${TYPE_LABEL[graph.type]} ${graph.id}\n${graph.label}\nconfidence: ${graph.confidence}`}
    >
      {/* invisible handles so react-flow can route edges */}
      <Handle
        type="source"
        position={Position.Top}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
      <Handle
        type="target"
        position={Position.Bottom}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
      <svg
        width={totalR * 2}
        height={totalR * 2}
        style={{ position: 'absolute', inset: 0 }}
      >
        {ring > 0 && (
          <circle
            cx={totalR}
            cy={totalR}
            r={r + 1.5}
            fill="none"
            stroke={fill}
            strokeWidth={ring}
            opacity={0.85}
          />
        )}
        <circle
          cx={totalR}
          cy={totalR}
          r={r}
          fill={fill}
          opacity={0.85}
        />
        {/* type letter inset */}
        <text
          x={totalR}
          y={totalR + 4}
          textAnchor="middle"
          fontSize={Math.max(11, r * 0.5)}
          fontWeight="600"
          fill="#0b1018"
          fontFamily="ui-monospace,SFMono-Regular,Menlo,monospace"
          style={{ pointerEvents: 'none' }}
        >
          {graph.id.split(':')[0]}
        </text>
      </svg>
      {/* Label below */}
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '100%',
          transform: 'translate(-50%, 4px)',
          fontSize: 10,
          color: '#cbd5e1',
          whiteSpace: 'nowrap',
          maxWidth: 180,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          pointerEvents: 'none',
        }}
      >
        {graph.label.length > 40
          ? graph.label.slice(0, 40) + '…'
          : graph.label}
      </div>
    </div>
  )
}

const nodeTypes = { circle: CircleNode }

// ── Main panel ───────────────────────────────────────────────

export function ExplorerPanel({
  graph,
  loading,
  error,
  onRefresh,
  onTokenClick,
}: {
  graph: GraphResp | null
  loading: boolean
  error: string
  onRefresh: () => void
  onTokenClick: (token: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1200, h: 800 })
  const [focusId, setFocusId] = useState<string | null>(null)

  // Track container size for layout. ResizeObserver on the wrapper.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect()
      setSize({ w: r.width, h: r.height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Compute positions from graph data + size.
  const positioned = useMemo<PositionedNode[]>(() => {
    if (!graph?.nodes) return []
    return runLayout(graph.nodes, graph.edges || [], size.w, size.h)
  }, [graph, size.w, size.h])

  // Focus-mode dim set: nodes within 2 hops of focus stay bright.
  const dimSet = useMemo<Set<string>>(() => {
    if (!focusId || !graph) return new Set()
    const adj = new Map<string, Set<string>>()
    for (const e of graph.edges || []) {
      if (!adj.has(e.source)) adj.set(e.source, new Set())
      if (!adj.has(e.target)) adj.set(e.target, new Set())
      adj.get(e.source)!.add(e.target)
      adj.get(e.target)!.add(e.source)
    }
    const keep = new Set<string>([focusId])
    const queue = [focusId]
    let depth = 0
    while (queue.length && depth < 2) {
      const next: string[] = []
      for (const id of queue) {
        for (const n of adj.get(id) || []) {
          if (!keep.has(n)) {
            keep.add(n)
            next.push(n)
          }
        }
      }
      queue.splice(0, queue.length, ...next)
      depth += 1
    }
    // Dim = NOT in keep
    const dim = new Set<string>()
    for (const n of graph.nodes || []) if (!keep.has(n.id)) dim.add(n.id)
    return dim
  }, [focusId, graph])

  // React-flow nodes/edges, derived from positioned + focus state.
  const rfNodes = useMemo<RFNode[]>(() => {
    return positioned.map((p) => ({
      id: p.id,
      type: 'circle',
      position: { x: p.x - nodeRadius(p), y: p.y - nodeRadius(p) },
      data: {
        graph: p,
        active: focusId === p.id,
        dimmed: dimSet.has(p.id),
      },
      draggable: false,
      selectable: false,
    }))
  }, [positioned, focusId, dimSet])

  const rfEdges = useMemo<RFEdge[]>(() => {
    if (!graph?.edges) return []
    return graph.edges.map((e, i) => {
      const isAdjacent =
        focusId && (e.source === focusId || e.target === focusId)
      const dimmed = focusId && !isAdjacent
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        type: 'straight',
        animated: false,
        style: {
          stroke: EDGE_COLOR[e.kind],
          strokeWidth: isAdjacent ? 2 : 1,
          opacity: dimmed ? 0.15 : 1,
          transition: 'opacity 200ms ease, stroke-width 200ms ease',
        },
      }
    })
  }, [graph, focusId])

  return (
    <div className="flex-1 flex flex-col" style={{ background: '#0b1018' }}>
      {/* Top bar */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border">
        <h3 className="text-base font-semibold text-text-primary">
          Explorer
        </h3>
        <span className="text-xs text-text-muted whitespace-nowrap">
          {graph?.stats
            ? `${graph.stats.nodes_total} nodes · ${graph.stats.edges_total} edges`
            : loading
              ? 'loading…'
              : ''}
        </span>
        <span className="text-[10px] text-text-muted italic">
          single-click to focus · double-click to drill in
        </span>
        {focusId && (
          <button
            onClick={() => setFocusId(null)}
            className="ml-2 px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer"
          >
            Clear focus ({focusId})
          </button>
        )}
        <div className="ml-auto flex items-center gap-3">
          {/* Legend */}
          <div className="flex items-center gap-2 text-[10px] text-text-muted">
            {(['question', 'project', 'pattern', 'drift', 'theme', 'gist', 'episode'] as const).map((t) => (
              <span key={t} className="flex items-center gap-1">
                <span
                  style={{
                    background: TYPE_FILL[t],
                    width: 8,
                    height: 8,
                    borderRadius: 999,
                    display: 'inline-block',
                  }}
                />
                {TYPE_LABEL[t].toLowerCase()}
              </span>
            ))}
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="flex-1 relative">
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-red-400 z-10">
            Error: {error}
          </div>
        )}
        {!loading && !error && positioned.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-text-muted">
            No graph data yet — click Refresh.
          </div>
        )}
        <ReactFlowProvider>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.2}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            panOnScroll
            zoomOnScroll
            zoomOnPinch
            onNodeClick={(_, n) => {
              // Single click: focus only. Stays on Explorer; dims
              // everything not within 2 hops of the clicked node.
              // Click again to clear, or click a different node to
              // shift focus.
              setFocusId((cur) => (cur === n.id ? null : n.id))
            }}
            onNodeDoubleClick={(_, n) => {
              // Double click: navigate to Overview + open DetailCard.
              onTokenClick(n.id)
            }}
            onPaneClick={() => setFocusId(null)}
            colorMode="dark"
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={24}
              size={1}
              color="#1e293b"
            />
            <Controls
              position="bottom-right"
              showInteractive={false}
              style={{
                background: '#0f172a',
                border: '1px solid #1e293b',
                borderRadius: 6,
              }}
            />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  )
}
