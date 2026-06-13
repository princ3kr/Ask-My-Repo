import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { FileCode, Box, Play, Database, Star, GitFork, Import } from 'lucide-react';
import clsx from 'clsx';

function NodeBadge({ label, color }) {
    return (
        <span className={clsx(
            "px-1.5 py-[1px] rounded text-[9px] font-semibold uppercase tracking-wider border",
            color === 'blue' && 'text-blue-400 bg-blue-500/10 border-blue-500/20',
            color === 'purple' && 'text-purple-400 bg-purple-500/10 border-purple-500/20',
            color === 'green' && 'text-green-400 bg-green-500/10 border-green-500/20',
            color === 'amber' && 'text-amber-400 bg-amber-500/10 border-amber-500/20',
        )}>
            {label}
        </span>
    );
}

export function CustomNode({ data }) {
    const { label, nodeType, path, classes, functions, imports, is_entry, entry_kind, selected } = data;

    let borderColor = 'border-surface-muted';
    let bgColor = 'bg-surface';
    let iconColor = 'text-gray-400';
    let Icon = FileCode;
    let headerBg = 'bg-surface-muted/50';

    if (nodeType === 'File') {
        borderColor = selected ? 'border-emerald-400' : 'border-emerald-700/40';
        bgColor = selected ? 'bg-emerald-500/8' : 'bg-emerald-950/20';
        iconColor = 'text-emerald-400';
        headerBg = 'bg-emerald-900/20';
        Icon = FileCode;
    } else if (nodeType === 'Class') {
        borderColor = selected ? 'border-blue-400' : 'border-blue-700/40';
        bgColor = selected ? 'bg-blue-500/8' : 'bg-blue-950/20';
        iconColor = 'text-blue-400';
        headerBg = 'bg-blue-900/20';
        Icon = Box;
    } else if (nodeType === 'Function') {
        borderColor = selected ? 'border-purple-400' : 'border-purple-700/40';
        bgColor = selected ? 'bg-purple-500/8' : 'bg-purple-950/20';
        iconColor = 'text-purple-400';
        headerBg = 'bg-purple-900/20';
        Icon = Play;
    } else if (nodeType === 'Model') {
        borderColor = selected ? 'border-amber-400' : 'border-amber-700/40';
        bgColor = selected ? 'bg-amber-500/8' : 'bg-amber-950/20';
        iconColor = 'text-amber-400';
        headerBg = 'bg-amber-900/20';
        Icon = Database;
    }

    const complexity = (classes?.length || 0) + (functions?.length || 0);
    const importCount = imports?.length || 0;

    return (
        <div
            className={clsx(
                "rounded-xl border-2 shadow-lg transition-all duration-200 min-w-[160px] max-w-[240px] overflow-hidden",
                bgColor,
                borderColor,
                selected && 'shadow-[0_0_20px_rgba(139,92,246,0.25)] ring-1 ring-accent/30'
            )}
        >
            <Handle type="target" position={Position.Top} className="!w-2 !h-2 !border-2 !border-background !bg-text-dim" />

            {/* Header */}
            <div className={clsx("flex items-center gap-2 px-3 py-2 border-b border-white/5", headerBg)}>
                <Icon size={14} className={clsx("shrink-0", iconColor)} />
                <span className={clsx(
                    "font-semibold text-xs truncate",
                    nodeType === 'File' && 'text-emerald-200',
                    nodeType === 'Class' && 'text-blue-200',
                    nodeType === 'Function' && 'text-purple-200',
                    nodeType === 'Model' && 'text-amber-200',
                    !nodeType && 'text-gray-200'
                )}>
                    {label}
                </span>
                {is_entry && (
                    <Star size={10} className="text-yellow-400 shrink-0" fill="currentColor" />
                )}
            </div>

            {/* Body */}
            <div className="px-3 py-2 space-y-1.5">
                {nodeType === 'File' && path && (
                    <p className="text-[9px] text-text-dim font-mono truncate leading-tight">{path.split('/').pop()}</p>
                )}

                {/* Type badges row */}
                <div className="flex flex-wrap gap-1">
                    <NodeBadge label={nodeType || 'Node'} color={
                        nodeType === 'File' ? 'green' : nodeType === 'Class' ? 'blue' : nodeType === 'Function' ? 'purple' : 'amber'
                    } />
                    {entry_kind && <NodeBadge label={entry_kind} color="amber" />}
                </div>

                {/* Stats row */}
                <div className="flex items-center gap-2 text-[9px] text-text-dim">
                    {complexity > 0 && (
                        <span className="flex items-center gap-0.5">
                            <GitFork size={9} />
                            {complexity} defs
                        </span>
                    )}
                    {importCount > 0 && (
                        <span className="flex items-center gap-0.5">
                            <Import size={9} />
                            {importCount} imports
                        </span>
                    )}
                </div>

                {/* Detail row - functions/classes list */}
                {classes && classes.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                        {classes.slice(0, 2).map((c, i) => (
                            <span key={i} className="text-[8px] px-1 py-[1px] rounded bg-blue-500/10 text-blue-300 border border-blue-500/20">C:{c}</span>
                        ))}
                        {classes.length > 2 && <span className="text-[8px] text-text-dim">+{classes.length - 2}</span>}
                    </div>
                )}
                {functions && functions.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                        {functions.slice(0, 2).map((f, i) => (
                            <span key={i} className="text-[8px] px-1 py-[1px] rounded bg-purple-500/10 text-purple-300 border border-purple-500/20">F:{f}</span>
                        ))}
                        {functions.length > 2 && <span className="text-[8px] text-text-dim">+{functions.length - 2}</span>}
                    </div>
                )}
            </div>

            <Handle type="source" position={Position.Bottom} className="!w-2 !h-2 !border-2 !border-background !bg-text-dim" />
        </div>
    );
}

export const nodeTypes = {
    customNode: CustomNode,
};
