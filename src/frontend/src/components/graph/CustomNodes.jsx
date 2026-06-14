import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { FileCode, Box, Play, Database, Star, GitFork, Import, Folder } from 'lucide-react';
import clsx from 'clsx';

const NODE_BADGE_VARS = {
    green: { color: 'var(--node-file-color)', bg: 'var(--node-file-bg)', border: 'var(--node-file-border)' },
    blue: { color: 'var(--node-class-color)', bg: 'var(--node-class-bg)', border: 'var(--node-class-border)' },
    purple: { color: 'var(--node-function-color)', bg: 'var(--node-function-bg)', border: 'var(--node-function-border)' },
    amber: { color: 'var(--node-model-color)', bg: 'var(--node-model-bg)', border: 'var(--node-model-border)' },
};

function NodeBadge({ label, color }) {
    const vars = NODE_BADGE_VARS[color] || NODE_BADGE_VARS.green;
    return (
        <span
            className="px-1.5 py-[1px] rounded text-[9px] font-semibold uppercase tracking-wider border"
            style={{
                color: vars.color,
                backgroundColor: vars.bg,
                borderColor: vars.border,
            }}
        >
            {label}
        </span>
    );
}

export function GroupNode({ data }) {
    return (
        <div className="w-full h-full rounded-xl border border-dashed border-text-dim/20 glass-panel-subtle overflow-hidden">
            <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-glass-border bg-surface/30">
                <Folder size={11} className="text-text-dim shrink-0" />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-text-dim/80">{data.label}</span>
                <span className="ml-auto text-[8px] text-text-dim/40">{data.memberCount} nodes</span>
            </div>
        </div>
    );
}

const NODE_VARS = {
    File: { border: 'var(--node-file-border)', icon: 'var(--node-file-color)', header: 'var(--node-file-bg)', text: 'var(--node-file-color)', Icon: FileCode, selectedBorder: 'var(--node-file-color)' },
    Class: { border: 'var(--node-class-border)', icon: 'var(--node-class-color)', header: 'var(--node-class-bg)', text: 'var(--node-class-color)', Icon: Box, selectedBorder: 'var(--node-class-color)' },
    Function: { border: 'var(--node-function-border)', icon: 'var(--node-function-color)', header: 'var(--node-function-bg)', text: 'var(--node-function-color)', Icon: Play, selectedBorder: 'var(--node-function-color)' },
    Model: { border: 'var(--node-model-border)', icon: 'var(--node-model-color)', header: 'var(--node-model-bg)', text: 'var(--node-model-color)', Icon: Database, selectedBorder: 'var(--node-model-color)' },
};

export function CustomNode({ data }) {
    const { label, nodeType, path, classes, functions, imports, is_entry, entry_kind, selected } = data;

    const vars = NODE_VARS[nodeType] || NODE_VARS.File;
    const Icon = vars.Icon;
    const isDefault = !nodeType || !NODE_VARS[nodeType];

    const complexity = (classes?.length || 0) + (functions?.length || 0);
    const importCount = imports?.length || 0;

    const borderStyle = selected
        ? `0 0 20px color-mix(in srgb, ${vars.icon} 30%, transparent), 0 0 0 1px ${vars.selectedBorder}`
        : undefined;

    return (
        <div
            className={clsx(
                "rounded-xl border-2 shadow-lg transition-all duration-200 min-w-[160px] max-w-[240px] overflow-hidden",
                isDefault ? 'border-surface-muted' : '',
                selected ? 'bg-surface ring-1' : 'glass-panel-light'
            )}
            style={{
                borderColor: isDefault ? undefined : selected ? vars.selectedBorder : vars.border,
                boxShadow: selected ? borderStyle : undefined,
            }}
        >
            <Handle type="target" position={Position.Top} className="!w-2.5 !h-2.5 !border-2 !border-background !bg-text-dim hover:!scale-125 transition-transform" />

            <div className="flex items-center gap-2 px-3 py-2 border-b border-glass-border" style={{ backgroundColor: vars.header }}>
                <Icon size={14} className="shrink-0" style={{ color: vars.icon }} />
                <span className="font-semibold text-xs truncate" style={{ color: vars.text }}>
                    {label}
                </span>
                {is_entry && (
                    <span className="relative shrink-0" title="Entry point">
                        <Star size={12} className="text-yellow-400" fill="currentColor" />
                        <span className="absolute inset-0 rounded-full bg-yellow-400/20 animate-ping" style={{ animationDuration: '3s' }} />
                    </span>
                )}
            </div>

            <div className="px-3 py-2 space-y-1.5">
                {nodeType === 'File' && path && (
                    <p className="text-[9px] text-text-dim font-mono truncate leading-tight">{path.split('/').pop()}</p>
                )}

                <div className="flex flex-wrap gap-1">
                    <NodeBadge label={nodeType || 'Node'} color={
                        nodeType === 'File' ? 'green' : nodeType === 'Class' ? 'blue' : nodeType === 'Function' ? 'purple' : 'amber'
                    } />
                    {entry_kind && <NodeBadge label={entry_kind} color="amber" />}
                </div>

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

                {classes && classes.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                        {classes.slice(0, 2).map((c, i) => (
                            <span key={i} className="text-[8px] px-1 py-[1px] rounded border" style={{ color: 'var(--node-class-color)', backgroundColor: 'var(--node-class-bg)', borderColor: 'var(--node-class-border)' }}>C:{c}</span>
                        ))}
                        {classes.length > 2 && <span className="text-[8px] text-text-dim">+{classes.length - 2}</span>}
                    </div>
                )}
                {functions && functions.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                        {functions.slice(0, 2).map((f, i) => (
                            <span key={i} className="text-[8px] px-1 py-[1px] rounded border" style={{ color: 'var(--node-function-color)', backgroundColor: 'var(--node-function-bg)', borderColor: 'var(--node-function-border)' }}>F:{f}</span>
                        ))}
                        {functions.length > 2 && <span className="text-[8px] text-text-dim">+{functions.length - 2}</span>}
                    </div>
                )}
            </div>

            <Handle type="source" position={Position.Bottom} className="!w-2.5 !h-2.5 !border-2 !border-background !bg-text-dim hover:!scale-125 transition-transform" />
        </div>
    );
}

export const nodeTypes = {
    customNode: CustomNode,
    groupNode: GroupNode,
};
