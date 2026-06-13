import React, { useState, useMemo } from 'react';
import { Info, Box, FileCode, Target, Network, GitBranch, ArrowRight, ArrowLeft, List, Code2 } from 'lucide-react';
import clsx from 'clsx';

const TABS = ['Properties', 'Relations', 'Code'];

export default function NodeDetails({ node, graphData }) {
    const [activeTab, setActiveTab] = useState('Properties');

    if (!node) {
        return null;
    }

    const { label, nodeType, path, classes, functions, imports, is_entry, entry_kind, name, qualified_name, line_start, line_end } = node.data || {};

    // Find incoming and outgoing edges
    const incomingEdges = useMemo(() => {
        if (!graphData?.edges) return [];
        return graphData.edges.filter(e => e.target === node.id);
    }, [graphData, node.id]);

    const outgoingEdges = useMemo(() => {
        if (!graphData?.edges) return [];
        return graphData.edges.filter(e => e.source === node.id);
    }, [graphData, node.id]);

    const findNodeById = (id) => graphData?.nodes?.find(n => n.id === id);

    return (
        <div className="flex flex-col h-full bg-panel">
            {/* Tab bar */}
            <div className="flex items-center border-b border-surface-muted bg-panel">
                {TABS.map(tab => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={clsx(
                            "px-4 py-2 text-xs font-medium transition-colors relative",
                            activeTab === tab
                                ? 'text-accent'
                                : 'text-text-dim hover:text-gray-300'
                        )}
                    >
                        {tab}
                        {activeTab === tab && (
                            <div className="absolute bottom-0 left-2 right-2 h-0.5 bg-accent rounded-full" />
                        )}
                    </button>
                ))}
            </div>

            <div className="flex-1 overflow-y-auto">
                {activeTab === 'Properties' && (
                    <div className="p-3 space-y-3">
                        {/* Header */}
                        <div className="flex items-center gap-2.5">
                            <div className={clsx(
                                "w-8 h-8 rounded-lg border flex items-center justify-center",
                                nodeType === 'Class' && 'bg-blue-500/10 border-blue-500/20 text-blue-400',
                                nodeType === 'Function' && 'bg-purple-500/10 border-purple-500/20 text-purple-400',
                                nodeType === 'File' && 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400',
                                nodeType === 'Model' && 'bg-amber-500/10 border-amber-500/20 text-amber-400',
                                !nodeType && 'bg-surface border-surface-muted text-text-dim'
                            )}>
                                {nodeType === 'Class' ? <Box size={15} /> : nodeType === 'Function' ? <Network size={15} /> : nodeType === 'File' ? <FileCode size={15} /> : <Target size={15} />}
                            </div>
                            <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                    <h3 className="text-sm font-semibold text-gray-200 truncate">{label}</h3>
                                    <span className={clsx(
                                        "text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded border",
                                        nodeType === 'Class' && 'text-blue-400 border-blue-500/20 bg-blue-500/10',
                                        nodeType === 'Function' && 'text-purple-400 border-purple-500/20 bg-purple-500/10',
                                        nodeType === 'File' && 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10',
                                        nodeType === 'Model' && 'text-amber-400 border-amber-500/20 bg-amber-500/10',
                                    )}>{nodeType}</span>
                                    {is_entry && <span className="text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded text-yellow-400 border border-yellow-500/20 bg-yellow-500/10">Entry</span>}
                                </div>
                                {path && <p className="text-[10px] text-text-dim font-mono truncate mt-0.5">{path}</p>}
                            </div>
                        </div>

                        {/* Stats Grid */}
                        <div className="grid grid-cols-4 gap-2">
                            {functions && <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{functions.length}</span><span className="text-[9px] text-text-dim">Functions</span></div>}
                            {classes && <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{classes.length}</span><span className="text-[9px] text-text-dim">Classes</span></div>}
                            {imports && <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{imports.length}</span><span className="text-[9px] text-text-dim">Imports</span></div>}
                            {line_start && <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{line_start}{line_end ? `-${line_end}` : ''}</span><span className="text-[9px] text-text-dim">Lines</span></div>}
                            <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{incomingEdges.length}</span><span className="text-[9px] text-text-dim">Incoming</span></div>
                            <div className="bg-surface border border-surface-muted rounded-lg p-2 text-center"><span className="block text-xs font-semibold text-gray-200">{outgoingEdges.length}</span><span className="text-[9px] text-text-dim">Outgoing</span></div>
                        </div>

                        {/* Properties */}
                        <div className="space-y-1">
                            {node.data && Object.entries(node.data).filter(([k]) => !['label', 'nodeType', 'functions', 'classes', 'imports', 'selected'].includes(k)).map(([key, val]) => (
                                <div key={key} className="flex items-center gap-2 text-[11px]">
                                    <span className="text-text-dim font-mono min-w-[80px]">{key.replace(/_/g, ' ')}</span>
                                    <span className="text-gray-300 truncate font-mono">
                                        {Array.isArray(val) ? val.join(', ') : String(val ?? '-')}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {activeTab === 'Relations' && (
                    <div className="p-3 space-y-3">
                        {/* Incoming edges */}
                        <div>
                            <h4 className="text-[10px] font-semibold uppercase tracking-wider text-text-dim mb-2 flex items-center gap-1.5">
                                <ArrowLeft size={12} /> Incoming ({incomingEdges.length})
                            </h4>
                            {incomingEdges.length === 0 ? (
                                <p className="text-[11px] text-text-dim italic">No incoming relations</p>
                            ) : (
                                <div className="space-y-1">
                                    {incomingEdges.map((edge, i) => {
                                        const sourceNode = findNodeById(edge.source);
                                        return (
                                            <div key={i} className="flex items-center gap-2 text-[11px] bg-surface border border-surface-muted rounded-lg px-2.5 py-1.5">
                                                <span className="text-gray-300 font-medium truncate max-w-[100px]">{sourceNode?.data?.label || edge.source}</span>
                                                <ArrowRight size={10} className="text-text-dim shrink-0" />
                                                <span className={clsx(
                                                    "text-[9px] font-semibold uppercase px-1 rounded",
                                                    edge.label === 'IMPORTS' && 'text-indigo-400 bg-indigo-500/10',
                                                    edge.label === 'CALLS' && 'text-purple-400 bg-purple-500/10',
                                                    edge.label === 'INHERITS' && 'text-amber-400 bg-amber-500/10',
                                                    !edge.label && 'text-text-dim'
                                                )}>{edge.label || 'RELATES'}</span>
                                                <ArrowRight size={10} className="text-text-dim shrink-0" />
                                                <span className="text-gray-300 font-medium truncate max-w-[100px]">{label}</span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>

                        {/* Outgoing edges */}
                        <div>
                            <h4 className="text-[10px] font-semibold uppercase tracking-wider text-text-dim mb-2 flex items-center gap-1.5">
                                <ArrowRight size={12} /> Outgoing ({outgoingEdges.length})
                            </h4>
                            {outgoingEdges.length === 0 ? (
                                <p className="text-[11px] text-text-dim italic">No outgoing relations</p>
                            ) : (
                                <div className="space-y-1">
                                    {outgoingEdges.map((edge, i) => {
                                        const targetNode = findNodeById(edge.target);
                                        return (
                                            <div key={i} className="flex items-center gap-2 text-[11px] bg-surface border border-surface-muted rounded-lg px-2.5 py-1.5">
                                                <span className="text-gray-300 font-medium truncate max-w-[100px]">{label}</span>
                                                <ArrowRight size={10} className="text-text-dim shrink-0" />
                                                <span className={clsx(
                                                    "text-[9px] font-semibold uppercase px-1 rounded",
                                                    edge.label === 'IMPORTS' && 'text-indigo-400 bg-indigo-500/10',
                                                    edge.label === 'CALLS' && 'text-purple-400 bg-purple-500/10',
                                                    edge.label === 'INHERITS' && 'text-amber-400 bg-amber-500/10',
                                                    !edge.label && 'text-text-dim'
                                                )}>{edge.label || 'RELATES'}</span>
                                                <ArrowRight size={10} className="text-text-dim shrink-0" />
                                                <span className="text-gray-300 font-medium truncate max-w-[100px]">{targetNode?.data?.label || edge.target}</span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>

                        {/* Correlation with graph positioning */}
                        <div className="flex items-center gap-2 text-[10px] text-text-dim border-t border-surface-muted pt-2">
                            <Network size={12} className="text-accent" />
                            <span>Selected node highlighted in graph</span>
                        </div>
                    </div>
                )}

                {activeTab === 'Code' && (
                    <div className="p-3 space-y-3">
                        <div className="flex items-center gap-2 text-[10px] text-text-dim mb-1">
                            <Code2 size={12} className="text-accent" />
                            <span>Definitions in {label}</span>
                        </div>

                        {classes && classes.length > 0 && (
                            <div>
                                <h4 className="text-[10px] font-semibold text-blue-400 mb-1">Classes</h4>
                                <div className="flex flex-wrap gap-1">
                                    {classes.map((c, i) => (
                                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/20 font-mono">{c}</span>
                                    ))}
                                </div>
                            </div>
                        )}

                        {functions && functions.length > 0 && (
                            <div>
                                <h4 className="text-[10px] font-semibold text-purple-400 mb-1">Functions</h4>
                                <div className="flex flex-wrap gap-1">
                                    {functions.map((f, i) => (
                                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/20 font-mono">{f}</span>
                                    ))}
                                </div>
                            </div>
                        )}

                        {imports && imports.length > 0 && (
                            <div>
                                <h4 className="text-[10px] font-semibold text-indigo-400 mb-1">Imports</h4>
                                <div className="flex flex-wrap gap-1">
                                    {imports.map((imp, i) => (
                                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/20 font-mono">{imp}</span>
                                    ))}
                                </div>
                            </div>
                        )}

                        {(!classes?.length && !functions?.length && !imports?.length) && (
                            <p className="text-[11px] text-text-dim italic">No code definitions available for this node.</p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
