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

function useLayoutedNodes(graphNodes) {
    return useMemo(() => {
        if (!graphNodes || graphNodes.length === 0) return [];
        const cols = Math.ceil(Math.sqrt(graphNodes.length));
        const spacingX = 280;
        const spacingY = 200;
        return graphNodes.map((node, i) => {
            const col = i % cols;
            const row = Math.floor(i / cols);
            return {
                ...node,
                position: {
                    x: col * spacingX + (row % 2 === 0 ? 0 : spacingX / 2),
                    y: row * spacingY,
                },
            };
        });
    }, [graphNodes]);
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
    const laidOutNodes = useLayoutedNodes(graphData?.nodes);
    const [nodes, setNodes, onNodesChange] = useNodesState(laidOutNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const controllerRef = React.useRef(null);

    useEffect(() => {
        if (graphData && graphData.nodes && graphData.edges) {
            setNodes(laidOutNodes);

            const styledEdges = graphData.edges.map(e => {
                const isImport = e.label === 'IMPORTS';
                const isCall = e.label === 'CALLS';
                const isInherits = e.label === 'INHERITS';
                let color = '#8b949e';
                let dash = 'none';
                let width = 1.5;
                if (isImport) { color = '#6366f1'; dash = '6,3'; width = 1.2; }
                if (isCall) { color = '#8b5cf6'; dash = 'none'; width = 1.5; }
                if (isInherits) { color = '#f59e0b'; dash = 'none'; width = 2; }

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
                    labelStyle: { fill: '#8b949e', fontSize: 9, fontWeight: 600 },
                    labelBgStyle: { fill: '#0d1117', fillOpacity: 0.85, rx: 3 },
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
        style: { stroke: '#30363d', strokeWidth: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#30363d' },
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
                <Background color="#161b22" gap={28} size={1.5} />
                <Controls
                    className="!bg-panel !border-surface-muted !rounded-lg !shadow-lg"
                    style={{ button: { borderBottom: '1px solid #22272e', backgroundColor: '#1c2128', color: '#8b949e', fill: '#8b949e' } }}
                />
                <MiniMap
                    nodeColor={(n) => {
                        const t = n.data?.nodeType;
                        if (t === 'File') return '#059669';
                        if (t === 'Class') return '#2563eb';
                        if (t === 'Function') return '#8b5cf6';
                        if (t === 'Model') return '#d97706';
                        return '#30363d';
                    }}
                    maskColor="rgba(13,17,23,0.7)"
                    className="!bg-panel !border-surface-muted !rounded-lg"
                    style={{ background: '#161b22' }}
                />
            </ReactFlow>
        </div>
    );
});

export default ReactFlowGraph;
