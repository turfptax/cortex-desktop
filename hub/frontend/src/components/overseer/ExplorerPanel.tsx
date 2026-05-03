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
  // CP2: filter-supporting fields
  last_seen?: string | null  // ISO-ish timestamp
  tags?: string[]
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

// CP2: bumped evidence alpha — it's the load-bearing edge for the
// "what feeds what question" reading. Derived_from stays mid; in_
// project stays ambient (otherwise the perimeter project edges
// dominate visually).
const EDGE_COLOR: Record<GraphEdge['kind'], string> = {
  evidence:     '#7c5cffcc',  // purple, ~80% alpha (was 66 / 40%)
  derived_from: '#f59e0b99',  // amber, ~60% alpha (was 55 / 33%)
  in_project:   '#94a3b833',  // slate, very faint (unchanged)
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

// ── Filter state + predicate ─────────────────────────────────

interface ExplorerFilters {
  search: string
  confidence: Set<GraphNode['confidence']>
  recencyDays: number | null   // null = no recency filter
  questionFocus: string | null // q:N — same effect as click-focus
  hideDisconnected: boolean    // hides nodes with degree 0
}

const DEFAULT_FILTERS: ExplorerFilters = {
  search: '',
  confidence: new Set(['low', 'med', 'high']),
  recencyDays: null,
  questionFocus: null,
  hideDisconnected: true,  // most useful default — Tory had 47 floaters
}

function nodeMatchesFilters(
  n: GraphNode,
  f: ExplorerFilters,
  degree: Map<string, number>,
): boolean {
  // Disconnected gate
  if (f.hideDisconnected && (degree.get(n.id) || 0) === 0) {
    return false
  }
  // Confidence
  if (!f.confidence.has(n.confidence)) {
    return false
  }
  // Recency — only filters nodes that HAVE a last_seen. Nodes with
  // no timestamp are kept regardless (better than hiding them).
  if (f.recencyDays != null && n.last_seen) {
    const ts = Date.parse(n.last_seen)
    if (!Number.isNaN(ts)) {
      const cutoff = Date.now() - f.recencyDays * 24 * 60 * 60 * 1000
      if (ts < cutoff) return false
    }
  }
  // Search — matches across label + tags + id token
  if (f.search.trim()) {
    const needle = f.search.trim().toLowerCase()
    const haystack = [
      n.label.toLowerCase(),
      n.id.toLowerCase(),
      ...(n.tags || []).map((t) => t.toLowerCase()),
    ].join(' ')
    if (!haystack.includes(needle)) return false
  }
  return true
}

// ── Sidebar ──────────────────────────────────────────────────

function ExplorerSidebar({
  graph,
  filters,
  setFilters,
}: {
  graph: GraphResp | null
  filters: ExplorerFilters
  setFilters: (f: ExplorerFilters) => void
}) {
  const questions = (graph?.nodes || []).filter((n) => n.type === 'question')

  const toggleConfidence = (c: GraphNode['confidence']) => {
    const next = new Set(filters.confidence)
    if (next.has(c)) next.delete(c)
    else next.add(c)
    // Don't allow empty set — at least one must be on. Re-add if
    // user emptied it.
    if (next.size === 0) next.add(c)
    setFilters({ ...filters, confidence: next })
  }

  return (
    <aside
      className="w-56 shrink-0 flex flex-col gap-4 p-4 border-r border-border overflow-y-auto"
      style={{ background: '#0a0e16' }}
    >
      {/* Search */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Search
        </label>
        <input
          type="text"
          value={filters.search}
          onChange={(e) => setFilters({ ...filters, search: e.target.value })}
          placeholder="label, tag, or id…"
          className="w-full px-2 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
        />
      </div>

      {/* Confidence */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Confidence
        </label>
        <div className="flex gap-1">
          {(['high', 'med', 'low'] as const).map((c) => {
            const on = filters.confidence.has(c)
            return (
              <button
                key={c}
                onClick={() => toggleConfidence(c)}
                className={`px-2 py-1 rounded text-[10px] uppercase font-medium cursor-pointer transition-opacity ${
                  on
                    ? 'bg-accent/30 text-accent-hover'
                    : 'bg-surface-tertiary text-text-muted opacity-60'
                }`}
              >
                {c}
              </button>
            )
          })}
        </div>
      </div>

      {/* Recency */}
      <div>
        <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
          Recency
        </label>
        <div className="flex gap-1 flex-wrap">
          {[
            { label: 'all', days: null },
            { label: '90d', days: 90 },
            { label: '30d', days: 30 },
            { label: '7d', days: 7 },
          ].map((opt) => {
            const on = filters.recencyDays === opt.days
            return (
              <button
                key={opt.label}
                onClick={() => setFilters({ ...filters, recencyDays: opt.days })}
                className={`px-2 py-1 rounded text-[10px] uppercase font-medium cursor-pointer ${
                  on
                    ? 'bg-accent/30 text-accent-hover'
                    : 'bg-surface-tertiary text-text-muted'
                }`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Question focus */}
      {questions.length > 0 && (
        <div>
          <label className="text-[10px] uppercase tracking-wide text-text-muted block mb-1">
            Focus on question
          </label>
          <select
            value={filters.questionFocus || ''}
            onChange={(e) =>
              setFilters({
                ...filters,
                questionFocus: e.target.value || null,
              })
            }
            className="w-full px-2 py-1.5 bg-surface-tertiary text-text-primary text-xs rounded border border-border focus:outline-none focus:border-accent"
          >
            <option value="">— none —</option>
            {questions.map((q) => (
              <option key={q.id} value={q.id}>
                {q.id}: {q.label.length > 32 ? q.label.slice(0, 32) + '…' : q.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Hide disconnected */}
      <div>
        <label className="flex items-center gap-2 text-[11px] text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={filters.hideDisconnected}
            onChange={(e) =>
              setFilters({ ...filters, hideDisconnected: e.target.checked })
            }
          />
          Hide disconnected nodes
        </label>
      </div>

      {/* Reset */}
      <button
        onClick={() => setFilters({ ...DEFAULT_FILTERS })}
        className="text-[10px] uppercase text-text-muted hover:text-text-primary cursor-pointer self-start"
      >
        Reset filters
      </button>

      {/* Legend (moved from topbar) */}
      <div className="mt-auto pt-4 border-t border-border">
        <div className="text-[10px] uppercase tracking-wide text-text-muted mb-2">
          Legend
        </div>
        <ul className="space-y-1 text-[11px] text-text-secondary">
          {(['question', 'pattern', 'drift', 'theme', 'episode', 'gist', 'project'] as const).map((t) => (
            <li key={t} className="flex items-center gap-2">
              <span
                style={{
                  background: TYPE_FILL[t],
                  width: 10,
                  height: 10,
                  borderRadius: 999,
                  display: 'inline-block',
                }}
              />
              {TYPE_LABEL[t]}
            </li>
          ))}
        </ul>
        <div className="mt-3 text-[10px] text-text-muted leading-relaxed">
          single-click to focus<br />
          double-click to drill in
        </div>
      </div>
    </aside>
  )
}

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
  const [filters, setFilters] = useState<ExplorerFilters>({
    ...DEFAULT_FILTERS,
  })

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

  // Effective focus = filters.questionFocus takes precedence over click
  // focus, so the user can pin a question and still single-click to
  // explore other nodes ad hoc. Clicking a node OVERRIDES the pinned
  // focus only until they clear-click.
  const effectiveFocus = focusId || filters.questionFocus

  // Degree map (for hide-disconnected). Recomputed when graph changes.
  const degree = useMemo<Map<string, number>>(() => {
    const m = new Map<string, number>()
    if (!graph) return m
    for (const e of graph.edges || []) {
      m.set(e.source, (m.get(e.source) || 0) + 1)
      m.set(e.target, (m.get(e.target) || 0) + 1)
    }
    return m
  }, [graph])

  // Filter predicate result per node id. Used for both layout (we
  // skip filtered-out nodes from the simulation) and rendering (we
  // hide them entirely via display:none — preserves layout for
  // nodes that ARE visible if user toggles a filter).
  const visibleSet = useMemo<Set<string>>(() => {
    if (!graph) return new Set()
    const out = new Set<string>()
    for (const n of graph.nodes || []) {
      if (nodeMatchesFilters(n, filters, degree)) out.add(n.id)
    }
    return out
  }, [graph, filters, degree])

  // Compute positions from graph data + size, ONLY for visible nodes
  // (so hide-disconnected actually removes the visual mass instead of
  // just dimming, AND so the layout uses screen real estate well).
  const positioned = useMemo<PositionedNode[]>(() => {
    if (!graph?.nodes) return []
    const visibleNodes = graph.nodes.filter((n) => visibleSet.has(n.id))
    const visibleEdges = (graph.edges || []).filter(
      (e) => visibleSet.has(e.source) && visibleSet.has(e.target),
    )
    return runLayout(visibleNodes, visibleEdges, size.w, size.h)
  }, [graph, visibleSet, size.w, size.h])

  // Focus-mode dim set: nodes within 2 hops of focus stay bright.
  const dimSet = useMemo<Set<string>>(() => {
    if (!effectiveFocus || !graph) return new Set()
    const adj = new Map<string, Set<string>>()
    for (const e of graph.edges || []) {
      if (!adj.has(e.source)) adj.set(e.source, new Set())
      if (!adj.has(e.target)) adj.set(e.target, new Set())
      adj.get(e.source)!.add(e.target)
      adj.get(e.target)!.add(e.source)
    }
    const keep = new Set<string>([effectiveFocus])
    const queue = [effectiveFocus]
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
    // Dim = visible AND NOT in keep
    const dim = new Set<string>()
    for (const id of visibleSet) if (!keep.has(id)) dim.add(id)
    return dim
  }, [effectiveFocus, graph, visibleSet])

  // React-flow nodes/edges, derived from positioned + focus state.
  const rfNodes = useMemo<RFNode[]>(() => {
    return positioned.map((p) => ({
      id: p.id,
      type: 'circle',
      position: { x: p.x - nodeRadius(p), y: p.y - nodeRadius(p) },
      data: {
        graph: p,
        active: effectiveFocus === p.id,
        dimmed: dimSet.has(p.id),
      },
      draggable: false,
      selectable: false,
    }))
  }, [positioned, effectiveFocus, dimSet])

  const rfEdges = useMemo<RFEdge[]>(() => {
    if (!graph?.edges) return []
    // Only render edges between currently-visible nodes.
    return graph.edges
      .filter((e) => visibleSet.has(e.source) && visibleSet.has(e.target))
      .map((e, i) => {
        const isAdjacent = effectiveFocus &&
          (e.source === effectiveFocus || e.target === effectiveFocus)
        const dimmed = effectiveFocus && !isAdjacent
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
  }, [graph, effectiveFocus, visibleSet])

  // Visible/total node count for the topbar — gives a sense of how
  // much the filters are hiding without us screaming "X HIDDEN" at
  // the user.
  const visibleCount = visibleSet.size
  const totalCount = graph?.nodes?.length || 0

  return (
    <div className="flex-1 flex" style={{ background: '#0b1018' }}>
      <ExplorerSidebar
        graph={graph}
        filters={filters}
        setFilters={setFilters}
      />
      <div className="flex-1 flex flex-col">
        {/* Top bar — tightened, only essential info */}
        <div className="flex items-center gap-3 px-4 py-2 border-b border-border min-w-0">
          <h3 className="text-sm font-semibold text-text-primary whitespace-nowrap">
            Explorer
          </h3>
          <span className="text-xs text-text-muted whitespace-nowrap truncate">
            {graph?.stats
              ? visibleCount === totalCount
                ? `${totalCount} nodes · ${graph.stats.edges_total} edges`
                : `${visibleCount} of ${totalCount} nodes shown`
              : loading
                ? 'loading…'
                : ''}
          </span>
          {effectiveFocus && (
            <button
              onClick={() => {
                setFocusId(null)
                setFilters({ ...filters, questionFocus: null })
              }}
              className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-secondary cursor-pointer whitespace-nowrap"
            >
              Clear focus ({effectiveFocus})
            </button>
          )}
          <button
            onClick={onRefresh}
            disabled={loading}
            className="ml-auto px-3 py-1 rounded-md text-xs font-medium bg-surface-tertiary hover:bg-surface-tertiary/70 text-text-primary cursor-pointer disabled:opacity-50"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
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
    </div>
  )
}
