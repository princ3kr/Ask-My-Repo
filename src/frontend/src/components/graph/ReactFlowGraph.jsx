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

function buildTreeLayout(graphNodes, graphEdges) {
    if (!graphNodes || graphNodes.length === 0) return [];

    const nodeMap = {};
    graphNodes.forEach(n => { nodeMap[n.id] = n; });

    const incomingCount = {};
    const outgoing = {};
    graphNodes.forEach(n => {
        incomingCount[n.id] = 0;
        outgoing[n.id] = [];
    });
    graphEdges.forEach(e => {
        if (incomingCount[e.target] !== undefined) incomingCount[e.target]++;
        if (outgoing[e.source]) outgoing[e.source].push(e.target);
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

    const maxLevel = Math.max(...Object.values(level), 0);
    const nodesByLevel = {};
    Object.entries(level).forEach(([id, lvl]) => {
        if (!nodesByLevel[lvl]) nodesByLevel[lvl] = [];
        nodesByLevel[lvl].push(id);
    });

    const spacingX = 300;
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

const ReactFlowGraph = forwardRef(function ReactFlowGraph({ graphData, onNodeClick, selectedNodeId, selectedFilePath }, ref) {
    const laidOutNodes = useMemo(() => buildTreeLayout(graphData?.nodes, graphData?.edges), [graphData]);
    const [nodes, setNodes, onNodesChange] = useNodesState(laidOutNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const controllerRef = React.useRef(null);

    useEffect(() => {
        if (graphData && graphData.nodes && graphData.edges) {
            setNodes(laidOutNodes);

            const styledEdges = graphData.edges.map(e => {
                const isImport = e.label === 'IMPORTS';
                const isCall = e.label === 'CALLS';
                const isInherits = e.label === 'INHERITS_FROM';
                const isInstantiates = e.label === 'INSTANTIATES';
                let color = '#8892a8';
                let dash = 'none';
                let width = 1.5;
                if (isImport) { color = '#6366f1'; dash = '6,3'; width = 1.2; }
                if (isCall) { color = '#8b5cf6'; dash = 'none'; width = 1.5; }
                if (isInherits) { color = '#f59e0b'; dash = 'none'; width = 2; }
                if (isInstantiates) { color = '#10b981'; dash = '4,2'; width = 1.5; }

                return {
                    ...e,
                    animated: isCall,
                    style: { stroke: color, strokeWidth: width, strokeDasharray: dash },
                    markerEnd: {
                        type: MarkerType.ArrowClosed,
                        color: color,
                        width: 15,
                        height: 15,
                    },
                    label: e.label,
                    labelStyle: { fill: '#8892a8', fontSize: 9, fontWeight: 600 },
                    labelBgStyle: { fill: 'var(--bg-color)', fillOpacity: 0.85, rx: 3 },
                    labelBgPadding: [6, 3],
                    labelBgBorderRadius: 3,
                };
            });
            setEdges(styledEdges);
        }
    }, [graphData, laidOutNodes, setNodes, setEdges]);

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
        style: { stroke: '#18203a', strokeWidth: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#18203a' },
    }), []);

    return (
        <div className="w-full h-full bg-background relative">
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                nodeTypes={nodeTypes}
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
                <Controls
                    className="!bg-panel !border-surface-muted !rounded-lg !shadow-lg"
                />
                <MiniMap
                    nodeColor={(n) => {
                        const t = n.data?.nodeType;
                        if (t === 'File') return '#10b981';
                        if (t === 'Class') return '#3b82f6';
                        if (t === 'Function') return '#8b5cf6';
                        if (t === 'Model') return '#f59e0b';
                        return 'var(--surface-muted-color)';
                    }}
                    maskColor="rgba(0,0,0,0.7)"
                    className="!bg-panel !border-surface-muted !rounded-lg"
                />
            </ReactFlow>
        </div>
    );
});

export default ReactFlowGraph;
