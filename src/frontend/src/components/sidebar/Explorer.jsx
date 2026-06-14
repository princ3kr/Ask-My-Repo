import React, { useState, useMemo } from 'react';
import { Folder, FolderOpen, File, FileCode, FileJson, ChevronRight, ChevronDown, Search, Box, Activity } from 'lucide-react';
import clsx from 'clsx';

function getFileIcon(filename) {
    if (filename.endsWith('.py')) return <FileCode size={13} className="text-blue-400" />;
    if (filename.endsWith('.json') || filename.endsWith('.yaml') || filename.endsWith('.yml')) return <FileJson size={13} className="text-yellow-400" />;
    if (filename.endsWith('.js') || filename.endsWith('.ts') || filename.endsWith('.jsx') || filename.endsWith('.tsx')) return <FileCode size={13} className="text-emerald-400" />;
    if (filename.endsWith('.rs')) return <FileCode size={13} className="text-orange-400" />;
    return <File size={13} className="text-gray-400" />;
}

function TreeFolder({ name, children, defaultOpen = false, level = 0 }) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    return (
        <div>
            <div
                className="tree-item group"
                onClick={() => setIsOpen(!isOpen)}
                style={{ paddingLeft: `${8 + level * 12}px` }}
            >
                {isOpen ? <ChevronDown size={12} className="text-text-dim shrink-0" /> : <ChevronRight size={12} className="text-text-dim shrink-0" />}
                {isOpen ? <FolderOpen size={13} className="text-text-dim shrink-0" /> : <Folder size={13} className="text-text-dim shrink-0" />}
                <span className="truncate text-xs">{name}</span>
            </div>
            {isOpen && (
                <div>
                    {children}
                </div>
            )}
        </div>
    );
}

function TreeFile({ name, isActive, onClick, level = 0 }) {
    return (
        <div
            className={clsx(
                "tree-item group text-xs",
                isActive && "bg-accent/10 text-accent border-l-2 border-accent"
            )}
            style={{ paddingLeft: `${8 + level * 12}px` }}
            onClick={onClick}
        >
            <span className="w-[12px] shrink-0"></span>
            {getFileIcon(name)}
            <span className={clsx("truncate", isActive && "text-accent font-medium")}>{name}</span>
        </div>
    );
}

function buildTree(paths) {
    const tree = {};
    paths.forEach(path => {
        const parts = path.split(/[\\/]/);
        let current = tree;
        for (let i = 0; i < parts.length; i++) {
            const part = parts[i];
            if (i === parts.length - 1) {
                current[part] = null;
            } else {
                if (!current[part] || typeof current[part] !== 'object') current[part] = {};
                current = current[part];
            }
        }
    });
    return tree;
}

function renderTree(node, level, selectedFilePath, onFileSelect) {
    return Object.entries(node).map(([key, value]) => {
        if (value === null) {
            const isActive = selectedFilePath && (selectedFilePath.endsWith(key) || selectedFilePath === key);
            return (
                <TreeFile key={key} name={key} isActive={isActive} onClick={() => onFileSelect?.(key)} level={level} />
            );
        }
        return (
            <TreeFolder key={key} name={key} defaultOpen={level < 2} level={level}>
                {renderTree(value, level + 1, selectedFilePath, onFileSelect)}
            </TreeFolder>
        );
    });
}

export default function Explorer({ treePaths = [], stats = {}, selectedFilePath, onFileSelect }) {
    const [searchQuery, setSearchQuery] = useState('');

    const tree = useMemo(() => buildTree(treePaths), [treePaths]);

    const filteredPaths = useMemo(() => {
        if (!searchQuery.trim()) return treePaths;
        const q = searchQuery.toLowerCase();
        return treePaths.filter(path => path.toLowerCase().includes(q));
    }, [treePaths, searchQuery]);

    const filteredTree = useMemo(() => buildTree(filteredPaths), [filteredPaths]);

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* Header with stats */}
            <div className="px-3 py-2 border-b border-surface-muted flex items-center justify-between">
                <span className="text-xs font-semibold text-text-dim uppercase tracking-wider flex items-center gap-1.5">
                    <Activity size={12} /> Explorer
                </span>
                <div className="flex items-center gap-3 text-[10px] text-text-dim">
                    <span>{stats.files || 0} files</span>
                    <span>{stats.nodes || 0} nodes</span>
                </div>
            </div>

            {/* Quick Stats */}
            <div className="px-3 py-2 border-b border-surface-muted/50 flex gap-2 text-[10px]">
                <span className="px-1.5 py-0.5 rounded glass-panel-light text-blue-400">C: {stats.classes || 0}</span>
                <span className="px-1.5 py-0.5 rounded glass-panel-light text-purple-400">F: {stats.functions || 0}</span>
                <span className="px-1.5 py-0.5 rounded glass-panel-light text-emerald-400">E: {stats.edges || 0}</span>
            </div>

            {/* Search */}
            <div className="px-3 py-2 border-b border-surface-muted/50">
                <div className="relative">
                    <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-dim" />
                    <input
                        type="text"
                        placeholder="Filter files..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full glass-panel-light rounded-md py-1.5 pl-7 pr-2 text-xs text-text-color placeholder-text-dim outline-none focus:border-accent/40 transition-colors"
                    />
                </div>
            </div>

            {/* Tree */}
            <div className="flex-1 overflow-y-auto py-1">
                {Object.keys(filteredTree).length > 0 ? (
                    searchQuery ? (
                        renderTree(filteredTree, 0, selectedFilePath, onFileSelect)
                    ) : (
                        Object.entries(filteredTree).map(([key, value]) => {
                            if (value === null) {
                                return <TreeFile key={key} name={key} isActive={selectedFilePath === key} onClick={() => onFileSelect?.(key)} level={0} />;
                            }
                            return (
                                <div key={key}>
                                    {renderTree({ [key]: value }, 0, selectedFilePath, onFileSelect)}
                                </div>
                            );
                        })
                    )
                ) : (
                    <div className="text-center text-xs text-text-dim mt-8">
                        {searchQuery ? 'No matching files' : 'No files found.'}
                    </div>
                )}
            </div>
        </div>
    );
}
