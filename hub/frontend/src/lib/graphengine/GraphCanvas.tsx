import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type {
  BackgroundConfig,
  EdgeStyle,
  EngineEdge,
  EngineNode,
  LayoutConfig,
  NodeSize,
  RenderNode,
} from './types'
import { useForceLayout } from './useForceLayout'

export interface GraphCanvasProps<TNodeData = unknown, TEdgeData = unknown> {
  nodes: EngineNode<TNodeData>[]
  edges: EngineEdge<TEdgeData>[]
  /** Node currently focused — passed to renderNode as state.active. */
  activeNodeId?: string | null
  /** Node ids to render in a dimmed state. */
  dimmedNodeIds?: Set<string>
  /** Render the visual contents of each node. Engine wraps this with invisible edge anchors. */
  renderNode: RenderNode<TNodeData>
  /** Per-edge style. Engine computes `highlighted` (touches active) and `dimmed` (active set & doesn't touch). */
  edgeStyle: EdgeStyle<TEdgeData>
  /** Visual size of each node — used for the engine's center-anchor offset. */
  nodeSize: NodeSize<TNodeData>
  /** Layout strategy. */
  layout: LayoutConfig
  /** Per-node collision radius for the force sim. Defaults to max(width, height) / 2. */
  nodeRadiusForCollision?: (node: EngineNode<TNodeData>) => number
  // Camera / interaction
  minZoom?: number
  maxZoom?: number
  panOnScroll?: boolean
  panOnDrag?: boolean
  zoomOnScroll?: boolean
  zoomOnPinch?: boolean
  fitView?: boolean
  // Chrome
  background?: BackgroundConfig
  showControls?: boolean
  // Events
  onNodeClick?: (id: string, event: React.MouseEvent) => void
  onNodeDoubleClick?: (id: string, event: React.MouseEvent) => void
  onPaneClick?: () => void
}

interface InternalNodeData<TNodeData> {
  node: EngineNode<TNodeData>
  active: boolean
  dimmed: boolean
  size: { w: number; h: number }
  render: RenderNode<TNodeData>
}

function EngineNodeRenderer({ data }: NodeProps) {
  const { node, active, dimmed, size, render } =
    data as unknown as InternalNodeData<unknown>
  return (
    <div style={{ width: size.w, height: size.h, position: 'relative' }}>
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
      {render(node, { active, dimmed })}
    </div>
  )
}

const nodeTypes = { engine: EngineNodeRenderer }

function GraphCanvasInner<TNodeData, TEdgeData>(
  props: GraphCanvasProps<TNodeData, TEdgeData>,
) {
  const {
    nodes,
    edges,
    activeNodeId = null,
    dimmedNodeIds,
    renderNode,
    edgeStyle,
    nodeSize,
    layout,
    nodeRadiusForCollision,
    minZoom = 0.2,
    maxZoom = 2.5,
    panOnScroll = false,
    panOnDrag = true,
    zoomOnScroll = true,
    zoomOnPinch = true,
    fitView = true,
    background,
    showControls = true,
    onNodeClick,
    onNodeDoubleClick,
    onPaneClick,
  } = props

  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1200, h: 800 })

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

  const collisionRadius = useMemo(() => {
    if (nodeRadiusForCollision) return nodeRadiusForCollision
    return (n: EngineNode<TNodeData>) => {
      const s = nodeSize(n)
      return Math.max(s.w, s.h) / 2
    }
  }, [nodeRadiusForCollision, nodeSize])

  const positioned = useForceLayout<TNodeData>({
    nodes: layout.kind === 'force' ? nodes : null,
    edges: layout.kind === 'force' ? (edges as EngineEdge[]) : null,
    width: size.w,
    height: size.h,
    config: layout.kind === 'force' ? layout : undefined,
    nodeRadius: collisionRadius,
  })

  const rfNodes = useMemo<RFNode[]>(() => {
    return positioned.map((p) => {
      const s = nodeSize(p)
      const data: InternalNodeData<TNodeData> = {
        node: p,
        active: activeNodeId === p.id,
        dimmed: dimmedNodeIds?.has(p.id) ?? false,
        size: s,
        render: renderNode,
      }
      return {
        id: p.id,
        type: 'engine',
        position: { x: p.x - s.w / 2, y: p.y - s.h / 2 },
        data: data as unknown as Record<string, unknown>,
        draggable: false,
        selectable: false,
      }
    })
  }, [positioned, activeNodeId, dimmedNodeIds, renderNode, nodeSize])

  const rfEdges = useMemo<RFEdge[]>(() => {
    return edges.map((e, i) => {
      const touchesActive =
        !!activeNodeId && (e.source === activeNodeId || e.target === activeNodeId)
      const highlighted = touchesActive
      const dimmed = !!activeNodeId && !touchesActive
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        type: 'straight',
        animated: false,
        style: edgeStyle(e, { highlighted, dimmed }),
      }
    })
  }, [edges, activeNodeId, edgeStyle])

  const bgColor = background?.color ?? '#0b1018'
  const dotsCfg =
    background?.dots === null
      ? null
      : (background?.dots ?? { gap: 24, size: 1, color: '#1e293b' })

  return (
    <div
      ref={containerRef}
      className="h-full w-full relative"
      style={{ background: bgColor }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView={fitView}
        minZoom={minZoom}
        maxZoom={maxZoom}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnScroll={panOnScroll}
        panOnDrag={panOnDrag}
        zoomOnScroll={zoomOnScroll}
        zoomOnPinch={zoomOnPinch}
        onNodeClick={(ev, n) =>
          onNodeClick?.(n.id, ev as unknown as React.MouseEvent)
        }
        onNodeDoubleClick={(ev, n) =>
          onNodeDoubleClick?.(n.id, ev as unknown as React.MouseEvent)
        }
        onPaneClick={() => onPaneClick?.()}
        colorMode="dark"
      >
        {dotsCfg && (
          <Background
            variant={BackgroundVariant.Dots}
            gap={dotsCfg.gap ?? 24}
            size={dotsCfg.size ?? 1}
            color={dotsCfg.color ?? '#1e293b'}
          />
        )}
        {showControls && (
          <Controls
            position="bottom-right"
            showInteractive={false}
            style={{
              background: '#0f172a',
              border: '1px solid #1e293b',
              borderRadius: 6,
            }}
          />
        )}
      </ReactFlow>
    </div>
  )
}

export function GraphCanvas<TNodeData = unknown, TEdgeData = unknown>(
  props: GraphCanvasProps<TNodeData, TEdgeData>,
) {
  return (
    <ReactFlowProvider>
      <GraphCanvasInner {...props} />
    </ReactFlowProvider>
  )
}
