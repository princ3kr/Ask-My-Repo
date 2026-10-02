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

// Folders and files are <button> rather than clickable <div>s: the tree is
// the Explorer's entire purpose and it was previously unreachable by keyboard
// or screen reader (no role, no tabIndex, no key handler, no aria-expanded).
function TreeFolder({ name, children, defaultOpen = false, level = 0 }) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    return (
        <div role="none">
            <button
                type="button"
                role="treeitem"
                aria-expanded={isOpen}
                className="tree-item group w-full text-left"
                onClick={() => setIsOpen(!isOpen)}
                style={{ paddingLeft: `${8 + level * 12}px` }}
            >
                {isOpen ? <ChevronDown size={12} className="text-text-dim shrink-0" /> : <ChevronRight size={12} className="text-text-dim shrink-0" />}
                {isOpen ? <FolderOpen size={13} className="text-text-dim shrink-0" /> : <Folder size={13} className="text-text-dim shrink-0" />}
                <span className="truncate text-xs">{name}</span>
            </button>
            {isOpen && (
                <div role="group">
                    {children}
                </div>
            )}
        </div>
    );
}

function TreeFile({ name, isActive, onClick, level = 0, showFullPath = false }) {
    return (
        <button
            type="button"
            role="treeitem"
            aria-selected={!!isActive}
            className={clsx(
                "tree-item group text-xs w-full text-left",
                isActive && "bg-accent/10 text-accent border-l-2 border-accent"
            )}
            style={{ paddingLeft: `${8 + level * 12}px` }}
            onClick={onClick}
        >
            <span className="w-[12px] shrink-0"></span>
            {getFileIcon(name)}
            <span className={clsx("truncate", showFullPath && "font-mono text-[10px]", isActive && "text-accent font-medium")}>{name}</span>
        </button>
    );
}

// Leaves carry their full repo-relative path. The renderer previously passed
// only the leaf segment, which App.handleFileSelect had to match against
// `n.data.label` (the basename) — so two files with the same name in different
// directories selected whichever node Neo4j happened to return first, and the
// `endsWith` highlight matched unrelated siblings.
function buildTree(paths) {
    const tree = {};
    paths.forEach((path) => {
        const parts = path.split(/[\\/]/);
        let current = tree;
        for (let i = 0; i < parts.length; i++) {
            const part = parts[i];
            if (i === parts.length - 1) {
                current[part] = { __isFile: true, __path: path };
            } else {
                if (!current[part] || current[part].__isFile) current[part] = {};
                current = current[part];
            }
        }
    });
    return tree;
}

function renderTree(node, level, selectedFilePath, onFileSelect) {
    return Object.entries(node).map(([key, value]) => {
        if (value && value.__isFile) {
            return (
                <TreeFile
                    key={value.__path}
                    name={key}
                    isActive={selectedFilePath === value.__path}
                    onClick={() => onFileSelect?.(value.__path)}
                    level={level}
                />
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

    const filteredPaths = useMemo(() => {
        if (!searchQuery.trim()) return treePaths;
        const q = searchQuery.toLowerCase();
        return treePaths.filter(path => path.toLowerCase().includes(q));
    }, [treePaths, searchQuery]);

    const filteredTree = useMemo(() => buildTree(filteredPaths), [filteredPaths]);
    const isSearching = Boolean(searchQuery.trim());

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
                        aria-label="Filter files"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full glass-panel-light rounded-md py-1.5 pl-7 pr-2 text-xs text-text-color placeholder-text-dim outline-none focus:border-accent/40 transition-colors"
                    />
                </div>
            </div>

            {/* Tree */}
            <div className="flex-1 overflow-y-auto py-1" role="tree" aria-label="Repository files">
                {Object.keys(filteredTree).length > 0 ? (
                    isSearching ? (
                        // Flat list in search mode. Rebuilding the nested tree
                        // left every folder at depth >= 2 collapsed
                        // (defaultOpen={level < 2}), so a match buried under
                        // src/backend/chunking/ was invisible while the panel
                        // still claimed results existed.
                        <div className="px-1">
                            {filteredPaths.map((path) => (
                                <TreeFile
                                    key={path}
                                    name={path}
                                    isActive={selectedFilePath === path}
                                    onClick={() => onFileSelect?.(path)}
                                    level={0}
                                    showFullPath
                                />
                            ))}
                        </div>
                    ) : (
                        Object.entries(filteredTree).map(([key, value]) => (
                            <div key={key}>
                                {renderTree({ [key]: value }, 0, selectedFilePath, onFileSelect)}
                            </div>
                        ))
                    )
                ) : (
                    <div className="text-center text-xs text-text-dim mt-8">
                        {isSearching ? 'No matching files' : 'No files found.'}
                    </div>
                )}
            </div>
        </div>
    );
}
