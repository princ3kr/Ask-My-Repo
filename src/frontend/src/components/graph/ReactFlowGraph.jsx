import React, { useCallback, useEffect, useImperativeHandle, forwardRef, useMemo } from 'react';
import {
    ReactFlow,
    MiniMap,
    Controls,
    Background,
    useNodesState,
    useEdgesState,
    useReactFlow,
    MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { nodeTypes } from './CustomNodes';

function getNodeDir(node) {
    const path = node.data?.path;
    if (!path) return null;
    const normalized = path.replace(/\\/g, '/');
    const parts = normalized.split('/');
    return parts.length > 1 ? parts[0] : '/';
}

function barycenterReorder(nodesByLevel, outgoing, incoming) {
    const levels = Object.keys(nodesByLevel).map(Number).sort((a, b) => a - b);

    for (let iter = 0; iter < 6; iter++) {
        const direction = iter % 2 === 0 ? 'down' : 'up';
        const sortedLevels = direction === 'down'
            ? levels.slice(1)
            : levels.slice(0, -1).reverse();

        sortedLevels.forEach(lvl => {
            const order = {};
            nodesByLevel[lvl].forEach((id, idx) => { order[id] = idx; });

            const barycenter = {};
            nodesByLevel[lvl].forEach(id => {
                const neighbors = direction === 'down'
                    ? (outgoing[id] || [])
                    : (incoming[id] || []);

                if (neighbors.length === 0) {
                    barycenter[id] = order[id];
                    return;
                }

                const neighborLevel = direction === 'down' ? lvl + 1 : lvl - 1;
                const neighborOrder = nodesByLevel[neighborLevel] || [];
                const neighborIdxMap = {};
                neighborOrder.forEach((nid, idx) => { neighborIdxMap[nid] = idx; });

                const validNeighbors = neighbors.filter(n => neighborIdxMap[n] !== undefined);
                if (validNeighbors.length === 0) {
                    barycenter[id] = order[id];
                    return;
                }

                const sum = validNeighbors.reduce((acc, nid) => acc + neighborIdxMap[nid], 0);
                barycenter[id] = sum / validNeighbors.length;
            });

            nodesByLevel[lvl].sort((a, b) => {
                const diff = barycenter[a] - barycenter[b];
                if (diff !== 0) return diff;
                return order[a] - order[b];
            });
        });
    }
}

function buildTreeLayout(graphNodes, graphEdges) {
    if (!graphNodes || graphNodes.length === 0) return [];

    const nodeMap = {};
    graphNodes.forEach(n => { nodeMap[n.id] = n; });

    const incomingCount = {};
    const outgoing = {};
    const incoming = {};
    graphNodes.forEach(n => {
        incomingCount[n.id] = 0;
        outgoing[n.id] = [];
        incoming[n.id] = [];
    });
    graphEdges.forEach(e => {
        if (incomingCount[e.target] !== undefined) incomingCount[e.target]++;
        if (outgoing[e.source]) outgoing[e.source].push(e.target);
        if (incoming[e.target]) incoming[e.target].push(e.source);
    });

    const isEntry = n => n.data?.is_entry === true;

    let roots = graphNodes.filter(n => isEntry(n) || incomingCount[n.id] === 0);
    if (roots.length === 0) {
        const minIncoming = Math.min(...graphNodes.map(n => incomingCount[n.id]));
        roots = graphNodes.filter(n => incomingCount[n.id] === minIncoming);
    }

    const level = {};
    const queue = [];
    roots.forEach(r => {
        level[r.id] = 0;
        queue.push(r.id);
    });
    while (queue.length > 0) {
        const id = queue.shift();
        const currentLevel = level[id];
        for (const targetId of (outgoing[id] || [])) {
            const newLevel = currentLevel + 1;
            if (level[targetId] === undefined || newLevel > level[targetId]) {
                level[targetId] = newLevel;
                queue.push(targetId);
            }
        }
    }

    const nodesByLevel = {};
    Object.entries(level).forEach(([id, lvl]) => {
        if (!nodesByLevel[lvl]) nodesByLevel[lvl] = [];
        nodesByLevel[lvl].push(id);
    });

    barycenterReorder(nodesByLevel, outgoing, incoming);

    const maxInLevel = Math.max(...Object.values(nodesByLevel).map(arr => arr.length), 1);
    const spacingX = Math.max(240, Math.min(340, 700 / maxInLevel));
    const spacingY = 220;

    return graphNodes.map(node => {
        const lvl = level[node.id] ?? 0;
        const nodesAtLevel = nodesByLevel[lvl] || [];
        const idx = nodesAtLevel.indexOf(node.id);
        const totalAtLevel = nodesAtLevel.length;
        const offsetX = idx - (totalAtLevel - 1) / 2;

        return {
            ...node,
            position: {
                x: offsetX * spacingX,
                y: lvl * spacingY,
            },
        };
    });
}

function applyGrouping(nodes) {
    const groups = new Map();
    const ungrouped = [];

    nodes.forEach(n => {
        const dir = getNodeDir(n);
        if (dir && dir !== '/') {
            if (!groups.has(dir)) groups.set(dir, []);
            groups.get(dir).push(n);
        } else {
            ungrouped.push(n);
        }
    });

    const groupNodes = [];
    const childNodes = [];

    groups.forEach((members, dir) => {
        if (members.length < 2) {
            ungrouped.push(...members);
            return;
        }

        const groupId = `group-${dir}`;
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

        members.forEach(m => {
            if (m.position.x < minX) minX = m.position.x;
            if (m.position.x > maxX) maxX = m.position.x;
            if (m.position.y < minY) minY = m.position.y;
            if (m.position.y > maxY) maxY = m.position.y;
        });

        const pad = 50;
        const w = Math.max(260, maxX - minX + pad * 2);
        const h = Math.max(160, maxY - minY + pad * 2);

        groupNodes.push({
            id: groupId,
            type: 'groupNode',
            data: { label: dir, memberCount: members.length },
            position: { x: minX - pad, y: minY - pad },
            style: { width: w, height: h },
        });

        members.forEach(m => {
            childNodes.push({
                ...m,
                parentId: groupId,
                position: {
                    x: m.position.x - (minX - pad),
                    y: m.position.y - (minY - pad),
                },
            });
        });
    });

    return [...groupNodes, ...childNodes, ...ungrouped];
}

function GraphController({ onNodeClick, selectedNodeId, onReady }) {
    const { fitView, getNodes } = useReactFlow();

    useEffect(() => {
        if (onReady) {
            onReady({
                fitViewForNode: (filePath) => {
                    const nodes = getNodes();
                    const node = nodes.find(n =>
                        n.data?.path === filePath || n.id === filePath || n.data?.label === filePath
                    );
                    if (node) {
                        fitView({ nodes: [node], padding: 0.3, duration: 400 });
                    }
                },
            });
        }
    }, [fitView, getNodes, onReady]);

    const onNodeClickHandler = useCallback((event, node) => {
        if (onNodeClick) onNodeClick(node);
    }, [onNodeClick]);

    useEffect(() => {
        if (selectedNodeId) {
            const nodes = getNodes();
            const target = nodes.find(n => n.id === selectedNodeId);
            if (target) {
                fitView({ nodes: [target], padding: 0.35, duration: 300 });
            }
        }
    }, [selectedNodeId, fitView, getNodes]);

    return null;
}

const EDGE_LABELS = {
    IMPORTS: 'imports',
    CALLS: 'calls',
    INHERITS_FROM: 'extends',
    INSTANTIATES: 'creates',
};

function getEdgeStyle(label) {
    const isImport = label === 'IMPORTS';
    const isCall = label === 'CALLS';
    const isInherits = label === 'INHERITS_FROM';
    const isInstantiates = label === 'INSTANTIATES';
    let color = '#8892a8';
    let dash = 'none';
    let width = 1.5;
    if (isImport) { color = '#6366f1'; dash = '6,3'; width = 1.5; }
    if (isCall) { color = '#8b5cf6'; dash = 'none'; width = 1.5; }
    if (isInherits) { color = '#f59e0b'; dash = 'none'; width = 2; }
    if (isInstantiates) { color = '#10b981'; dash = '4,2'; width = 1.5; }
    return { color, dash, width, isCall };
}

function GraphLegend({ visible, onToggle }) {
    if (!visible) return (
        <button
            onClick={onToggle}
            className="absolute top-16 right-3 z-10 w-7 h-7 rounded-full glass-panel-light flex items-center justify-center text-text-dim hover:text-text-color hover:border-accent/40 transition-all text-xs font-bold shadow-lg"
            title="Show legend"
        >
            ?
        </button>
    );

    return (
        <div className="absolute top-16 right-3 z-10 glass-panel-strong rounded-xl p-3 min-w-[180px] max-w-[220px] space-y-2.5 shadow-lg">
            <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-text-dim">Legend</span>
                <button onClick={onToggle} className="text-text-dim hover:text-text-color text-xs leading-none">&times;</button>
            </div>

            <div className="space-y-2">
                <div className="text-[9px] font-semibold uppercase tracking-wider text-text-dim/60">Node types</div>
                <div className="space-y-1.5">
                    <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-sm bg-emerald-500 shrink-0" />
                        <span className="text-[10px] text-text-color">File</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-sm bg-blue-500 shrink-0" />
                        <span className="text-[10px] text-text-color">Class</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-sm bg-purple-500 shrink-0" />
                        <span className="text-[10px] text-text-color">Function</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="text-yellow-400 text-[10px] leading-none shrink-0">&#9733;</span>
                        <span className="text-[10px] text-text-color">Entry point</span>
                    </div>
                </div>
            </div>

            <div className="space-y-2">
                <div className="text-[9px] font-semibold uppercase tracking-wider text-text-dim/60">Relations</div>
                <div className="space-y-1.5">
                    <div className="flex items-center gap-2">
                        <svg width="20" height="4" className="shrink-0"><line x1="0" y1="2" x2="20" y2="2" stroke="#8b5cf6" strokeWidth="1.5" /></svg>
                        <span className="text-[10px] text-text-color">calls</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <svg width="20" height="4" className="shrink-0"><line x1="0" y1="2" x2="20" y2="2" stroke="#6366f1" strokeWidth="1.5" strokeDasharray="6,3" /></svg>
                        <span className="text-[10px] text-text-color">imports</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <svg width="20" height="4" className="shrink-0"><line x1="0" y1="2" x2="20" y2="2" stroke="#f59e0b" strokeWidth="2" /></svg>
                        <span className="text-[10px] text-text-color">extends</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <svg width="20" height="4" className="shrink-0"><line x1="0" y1="2" x2="20" y2="2" stroke="#10b981" strokeWidth="1.5" strokeDasharray="4,2" /></svg>
                        <span className="text-[10px] text-text-color">creates</span>
                    </div>
                </div>
            </div>

            <div className="space-y-2">
                <div className="text-[9px] font-semibold uppercase tracking-wider text-text-dim/60">Sections</div>
                <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-sm border border-dashed border-text-dim/40 shrink-0" />
                    <span className="text-[10px] text-text-color">Directory group</span>
                </div>
            </div>
        </div>
    );
}

const ReactFlowGraph = forwardRef(function ReactFlowGraph({ graphData, onNodeClick, selectedNodeId, selectedFilePath }, ref) {
    const [legendOpen, setLegendOpen] = React.useState(true);

    const groupedNodes = useMemo(() => {
        const laidOut = buildTreeLayout(graphData?.nodes, graphData?.edges);
        return applyGrouping(laidOut);
    }, [graphData]);

    const [nodes, setNodes, onNodesChange] = useNodesState(groupedNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const controllerRef = React.useRef(null);

    const allNodeTypes = useMemo(() => ({
        ...nodeTypes,
    }), []);

    useEffect(() => {
        if (graphData && graphData.nodes && graphData.edges) {
            setNodes(groupedNodes);

            const styledEdges = graphData.edges.map(e => {
                const style = getEdgeStyle(e.label);
                return {
                    ...e,
                    type: 'smoothstep',
                    animated: style.isCall,
                    style: { stroke: style.color, strokeWidth: style.width, strokeDasharray: style.dash },
                    markerEnd: {
                        type: MarkerType.ArrowClosed,
                        color: style.color,
                        width: 18,
                        height: 18,
                    },
                    label: EDGE_LABELS[e.label] || e.label,
                    labelStyle: { fill: 'var(--text-dim)', fontSize: 9, fontWeight: 600 },
                    labelBgStyle: { fill: 'var(--bg-color)', fillOpacity: 0.85, rx: 3 },
                    labelBgPadding: [6, 3],
                    labelBgBorderRadius: 3,
                };
            });
            setEdges(styledEdges);
        }
    }, [graphData, groupedNodes, setNodes, setEdges]);

    useEffect(() => {
        setNodes(nds => nds.map(n => ({
            ...n,
            data: { ...n.data, selected: n.id === selectedNodeId },
        })));
    }, [selectedNodeId, setNodes]);

    useImperativeHandle(ref, () => ({
        fitViewForNode: (filePath) => {
            if (controllerRef.current) {
                controllerRef.current.fitViewForNode(filePath);
            }
        },
    }), []);

    const onReady = useCallback((ctrl) => {
        controllerRef.current = ctrl;
        if (ref) {
            if (typeof ref === 'function') ref(ctrl);
            else ref.current = ctrl;
        }
    }, [ref]);

    const defaultEdgeOptions = useMemo(() => ({
        type: 'smoothstep',
        style: { stroke: '#18203a', strokeWidth: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#18203a' },
    }), []);

    return (
        <div className="graph-container">
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                nodeTypes={allNodeTypes}
                fitView
                defaultEdgeOptions={defaultEdgeOptions}
                minZoom={0.1}
                maxZoom={3}
                className="bg-background"
            >
                <GraphController
                    onNodeClick={onNodeClick}
                    selectedNodeId={selectedNodeId}
                    onReady={onReady}
                />
                <Background color="var(--surface-color)" gap={28} size={1.5} />
                <GraphLegend
                    visible={legendOpen}
                    onToggle={() => setLegendOpen(p => !p)}
                />
                <Controls
                    className="!bg-transparent !border-0 !rounded-lg !shadow-lg"
                />
                <MiniMap
                    nodeColor={(n) => {
                        if (n.type === 'groupNode') return 'rgba(255,255,255,0.04)';
                        const t = n.data?.nodeType;
                        if (t === 'File') return '#10b981';
                        if (t === 'Class') return '#3b82f6';
                        if (t === 'Function') return '#8b5cf6';
                        if (t === 'Model') return '#f59e0b';
                        return 'var(--surface-muted-color)';
                    }}
                    maskColor="var(--minimap-mask)"
                    className="!bg-transparent !border-0 !rounded-lg"
                />
            </ReactFlow>
        </div>
    );
});

export default ReactFlowGraph;
