import { useState, useEffect, useRef, useCallback } from 'react';
import DashboardLayout from './components/layout/DashboardLayout';
import Explorer from './components/sidebar/Explorer';
import QueryPanel from './components/sidebar/QueryPanel';
import ReactFlowGraph from './components/graph/ReactFlowGraph';
import NodeDetails from './components/panels/NodeDetails';
import SetupPanel from './components/panels/SetupPanel';
import { RotateCcw } from 'lucide-react';
import { normalizeRepoUrl, repoShortName, safeStorage, newSessionId } from './utils';

const API_URL = '/api';
const SESSION_STORAGE_KEY = 'ask_my_repo_session_id';

const getOrCreateSessionId = () => {
    let id = safeStorage.get(SESSION_STORAGE_KEY);
    if (!id) {
        id = newSessionId();
        safeStorage.set(SESSION_STORAGE_KEY, id);
    }
    return id;
};

// Readable error from any fetch response. The backend's 500 handler returns
// JSON now, but a proxy or gateway in front of it may return HTML, so a plain
// res.json() can still reject and lose the real cause.
async function readError(res, fallback) {
    try {
        const body = await res.json();
        return body.detail || body.message || fallback;
    } catch {
        return fallback;
    }
}

export default function App() {
    const [theme, setTheme] = useState(() => safeStorage.get('theme') || 'dark');

    useEffect(() => {
        document.body.classList.toggle('light-theme', theme === 'light');
    }, [theme]);
    const [repoUrl, setRepoUrl] = useState('');
    const [repoId, setRepoId] = useState('');
    const [isParsing, setIsParsing] = useState(false);
    const [isParsed, setIsParsed] = useState(false);
    const [stats, setStats] = useState({ files: 0, classes: 0, functions: 0, imports: 0, calls: 0, nodes: 0, edges: 0 });
    const [treePaths, setTreePaths] = useState([]);
    const [graphData, setGraphData] = useState({ nodes: [], edges: [] });
    const [graphError, setGraphError] = useState(null);
    const [isTyping, setIsTyping] = useState(false);
    const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
    const [jobProgress, setJobProgress] = useState({ progress: 0, message: '', stage: 'starting' });
    const [selectedNode, setSelectedNode] = useState(null);
    const [selectedFilePath, setSelectedFilePath] = useState(null);
    const [messages, setMessages] = useState([]);
    const [expandedReason, setExpandedReason] = useState({});
    const [terminalOpen, setTerminalOpen] = useState(false);
    const [terminalHeight, setTerminalHeight] = useState(220);
    const pollRef = useRef(null);
    const pollRejectRef = useRef(null);
    const messagesEndRef = useRef(null);
    const graphRef = useRef(null);
    const chatAbortRef = useRef(null);
    const terminalDragRef = useRef(null);
    const terminalStartY = useRef(0);
    const terminalStartSize = useRef(0);

    // Clear the timer *and* settle the promise, so a pending handleParse
    // cannot hang forever with isParsing stuck true.
    const stopPolling = useCallback((reason) => {
        if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
        }
        if (pollRejectRef.current) {
            const reject = pollRejectRef.current;
            pollRejectRef.current = null;
            reject(new Error(reason || 'Cancelled'));
        }
    }, []);

    useEffect(() => stopPolling('Unmounted'), [stopPolling]);

    useEffect(() => () => chatAbortRef.current?.abort(), []);

    // Only follow the stream when already near the bottom, and coalesce onto an
    // animation frame. This used to restart a smooth-scroll animation on every
    // single token (~50/sec).
    useEffect(() => {
        const raf = requestAnimationFrame(() => {
            const end = messagesEndRef.current;
            const box = end?.parentElement;
            if (!box) return;
            if (box.scrollHeight - box.scrollTop - box.clientHeight < 120) {
                box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
            }
        });
        return () => cancelAnimationFrame(raf);
    }, [messages, isTyping]);

    const appendMessage = (msg) => setMessages((prev) => [...prev, msg]);

    const pollJobStatus = useCallback((jobId) => new Promise((resolve, reject) => {
        pollRejectRef.current = reject;
        const finish = (fn, value) => {
            if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
            }
            pollRejectRef.current = null;
            fn(value);
        };
        const poll = async () => {
            try {
                const res = await fetch(`${API_URL}/parse/status/${jobId}`);
                if (!res.ok) {
                    throw new Error(await readError(res, 'Lost connection to the server'));
                }
                const data = await res.json();

                setJobProgress({
                    progress: data.progress ?? 0,
                    message: data.message ?? 'Working on it…',
                    stage: data.stage ?? 'starting',
                });

                if (data.status === 'done') {
                    finish(resolve, data.result);
                } else if (data.status === 'error') {
                    finish(reject, new Error(data.error || data.message));
                }
            } catch (e) {
                finish(reject, e);
            }
        };
        poll();
        pollRef.current = setInterval(poll, 700);
    }), []);

    const handleParse = async () => {
        // Pressing Enter in SetupPanel fires this even while a job is already
        // running. Without this guard a second job starts, its interval
        // overwrites the first (orphaning it), and one of them polls forever.
        if (isParsing) return;
        const normalized = normalizeRepoUrl(repoUrl);
        if (!normalized) return;

        setRepoUrl(normalized);
        setIsParsing(true);
        setJobProgress({ progress: 2, message: 'Starting up…', stage: 'starting' });

        appendMessage({
            id: Date.now(),
            role: 'user',
            content: `Connect ${repoShortName(normalized)}`,
            isStatus: true,
        });

        try {
            const res = await fetch(`${API_URL}/parse`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: normalized }),
            });
            if (!res.ok) {
                throw new Error(await readError(res, 'Could not start setup'));
            }
            const data = await res.json();

            const result = await pollJobStatus(data.job_id);

            setRepoId(result.repo_id || '');
            const totalNodes = result.nodes_count || 0;
            const totalEdges = result.edges_count || 0;
            setStats({
                files: result.files_count || 0,
                nodes: totalNodes,
                edges: totalEdges,
                classes: 0, functions: 0, imports: 0, calls: 0,
            });
            setIsParsed(true);

            appendMessage({
                id: Date.now() + 2,
                role: 'assistant',
                content: `Done! I've learned ${result.files_count} files across ${totalNodes} connected parts. Pick a suggestion below or ask anything.`,
            });
        } catch (e) {
            if (e.message === 'Cancelled') return;
            setJobProgress((prev) => ({
                ...prev,
                stage: 'error',
                message: e.message,
            }));
            appendMessage({
                id: Date.now() + 2,
                role: 'assistant',
                content: e.message,
            });
        } finally {
            setIsParsing(false);
        }
    };

    useEffect(() => {
        if (!repoId) return;

        // An explicit AbortController. Without it, clicking New while this is
        // in flight let the response land in a freshly reset session and
        // resurrect the previous repo's graph.
        const ac = new AbortController();
        const getJson = async (url) => {
            const res = await fetch(url, { signal: ac.signal });
            if (!res.ok) {
                throw new Error(await readError(res, `Request failed (${res.status})`));
            }
            return res.json();
        };

        setGraphError(null);

        // Promise.all so the two slow Neo4j round trips overlap instead of
        // running back to back.
        Promise.all([
            getJson(`${API_URL}/tree/${repoId}`),
            getJson(`${API_URL}/graph_data/${repoId}`),
        ])
            .then(([tree, data]) => {
                if (ac.signal.aborted) return;
                if (tree.paths) setTreePaths(tree.paths);
                if (data.nodes) {
                    setGraphData(data);
                    setSelectedNode(data.nodes[0] || null);
                    const nodeTypes = {};
                    data.nodes.forEach(n => {
                        const t = n.data?.nodeType || 'unknown';
                        nodeTypes[t] = (nodeTypes[t] || 0) + 1;
                    });
                    setStats(prev => ({
                        ...prev,
                        classes: nodeTypes['Class'] || 0,
                        functions: nodeTypes['Function'] || 0,
                        nodes: data.nodes.length || prev.nodes,
                        edges: data.edges?.length || prev.edges,
                    }));
                }
            })
            .catch((e) => {
                if (e.name === 'AbortError') return;
                console.error('Failed to load graph data:', e);
                setGraphError(e.message);
            });

        return () => ac.abort();
    }, [repoId]);

    const handleSend = async (query) => {
        if (!query || !isParsed || isTyping) return;

        const newUserMsg = { id: Date.now(), role: 'user', content: query };
        setMessages((prev) => [...prev, newUserMsg]);
        setIsTyping(true);

        // Aborted on unmount and on New, so a late response cannot write into
        // a session that no longer exists.
        chatAbortRef.current?.abort();
        const ac = new AbortController();
        chatAbortRef.current = ac;

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_url: repoUrl,
                    query,
                    session_id: sessionId,
                }),
                signal: ac.signal,
            });

            if (!res.ok) {
                throw new Error(await readError(res, 'Could not get an answer right now'));
            }
            const data = await res.json();

            setMessages((prev) => [
                ...prev,
                {
                    id: Date.now() + 1,
                    role: 'assistant',
                    content: data.answer || 'I couldn\'t find a clear answer for that.',
                    reason: data.reason,
                    decision: data.decision,
                    rewritten_query: data.rewritten_query,
                },
            ]);
        } catch (e) {
            if (e.name === 'AbortError') return;
            console.error('Chat request failed:', e);
            appendMessage({
                id: Date.now() + 1,
                role: 'assistant',
                content: e.message,
            });
        } finally {
            if (chatAbortRef.current === ac) chatAbortRef.current = null;
            setIsTyping(false);
        }
    };

    const toggleReason = (id) => {
        setExpandedReason((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    const handleNewSession = () => {
        // Settles the in-flight poll and aborts any open request, so neither
        // can write into the freshly reset state.
        stopPolling('Cancelled');
        chatAbortRef.current?.abort();
        chatAbortRef.current = null;
        const newId = newSessionId();
        safeStorage.set(SESSION_STORAGE_KEY, newId);
        setSessionId(newId);
        setRepoUrl('');
        setIsParsing(false);
        setIsParsed(false);
        setStats({ files: 0, classes: 0, functions: 0, imports: 0, calls: 0, nodes: 0, edges: 0 });
        setJobProgress({ progress: 0, message: '', stage: 'starting' });
        setMessages([]);
        setTreePaths([]);
        setGraphData({ nodes: [], edges: [] });
        setGraphError(null);
        setSelectedNode(null);
        setSelectedFilePath(null);
        setExpandedReason({});
        setRepoId('');
    };

    const toggleTheme = useCallback(() => {
        setTheme(prev => {
            const next = prev === 'dark' ? 'light' : 'dark';
            safeStorage.set('theme', next);
            return next;
        });
    }, []);

    const handleNodeClick = useCallback((node) => {
        setSelectedNode(node);
        setSelectedFilePath(node.data?.path || null);
        setTerminalOpen(true);
    }, []);

    const handleFileSelect = useCallback((filePath) => {
        setSelectedFilePath(filePath);
        if (graphRef.current && graphRef.current.fitViewForNode) {
            graphRef.current.fitViewForNode(filePath);
        }
        // Prefer an exact path match. Falling back to `label` compares against
        // the basename, which selects the wrong node whenever a repo has two
        // files with the same name (src/a/index.js vs src/b/index.js).
        const matchingNode = graphData.nodes?.find(
            (n) => n.data?.nodeType === 'File' && n.data?.path === filePath
        );
        if (matchingNode) {
            setSelectedNode(matchingNode);
        }
    }, [graphData]);

    const handleTerminalResizeStart = useCallback((e) => {
        e.preventDefault();
        terminalDragRef.current = true;
        terminalStartY.current = e.clientY;
        terminalStartSize.current = terminalHeight;
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
    }, [terminalHeight]);

    useEffect(() => {
        const resetStyles = () => {
            terminalDragRef.current = null;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
        const handleMove = (e) => {
            if (!terminalDragRef.current) return;
            const delta = terminalStartY.current - e.clientY;
            const next = Math.max(100, Math.min(600, terminalStartSize.current + delta));
            setTerminalHeight(next);
        };
        window.addEventListener('mousemove', handleMove);
        window.addEventListener('mouseup', resetStyles);
        // Releasing outside the window never fires mouseup, and unmounting
        // mid-drag used to leave the whole page with a resize cursor and text
        // selection permanently disabled.
        window.addEventListener('blur', resetStyles);
        return () => {
            window.removeEventListener('mousemove', handleMove);
            window.removeEventListener('mouseup', resetStyles);
            window.removeEventListener('blur', resetStyles);
            resetStyles();
        };
    }, []);

    return (
        <>
            <DashboardLayout
            theme={theme}
            onToggleTheme={toggleTheme}
            repoName={isParsed ? repoShortName(repoUrl) : ''}
            topBarExtra={
                isParsed ? (
                    <button
                        onClick={handleNewSession}
                        className="text-text-dim hover:text-white p-1.5 rounded hover:bg-surface-muted transition-colors flex items-center gap-1 text-xs"
                        title="Connect another repo"
                    >
                        <RotateCcw size={13} /> New
                    </button>
                ) : null
            }
            leftSidebar={
                isParsed ? (
                    <Explorer
                        treePaths={treePaths}
                        stats={stats}
                        selectedFilePath={selectedFilePath}
                        onFileSelect={handleFileSelect}
                    />
                ) : null
            }
            rightSidebar={
                isParsed ? (
                    <div className="flex flex-col h-full overflow-hidden">
                        <QueryPanel
                            onSend={handleSend}
                            isTyping={isTyping}
                            messages={messages}
                            expandedReason={expandedReason}
                            toggleReason={toggleReason}
                            messagesEndRef={messagesEndRef}
                            stats={stats}
                        />
                    </div>
                ) : null
            }
        >
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* Graph area (top) */}
                <div className="relative flex-1 overflow-hidden">
                    {/* Overlay Setup Panel when not parsed */}
                    {!isParsed && (
                        <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/80 backdrop-blur-sm">
                            <SetupPanel
                                repoUrl={repoUrl}
                                setRepoUrl={setRepoUrl}
                                handleParse={handleParse}
                                isParsing={isParsing}
                                jobProgress={jobProgress}
                                messages={messages}
                                handleSend={handleSend}
                                isTyping={isTyping}
                                toggleReason={toggleReason}
                                expandedReason={expandedReason}
                                messagesEndRef={messagesEndRef}
                            />
                        </div>
                    )}

                    {/* ReactFlow Graph */}
                    {isParsed && !graphError && graphData.nodes?.length > 0 && (
                        <ReactFlowGraph
                            ref={graphRef}
                            graphData={graphData}
                            onNodeClick={handleNodeClick}
                            selectedNodeId={selectedNode?.id || null}
                        />
                    )}

                    {/* Error state. Previously any failure left the UI on
                        "Loading graph data..." forever, because the fetches
                        had no res.ok check and a rejected .json() landed in a
                        bare console.error. */}
                    {isParsed && graphError && (
                        <div className="flex flex-col items-center justify-center h-full gap-2 text-text-dim text-sm px-6 text-center">
                            <span>Could not load the graph for this repository.</span>
                            <span className="text-xs opacity-70">{graphError}</span>
                        </div>
                    )}

                    {/* Loading state while the graph is still in flight */}
                    {isParsed && !graphError && (!graphData.nodes || graphData.nodes.length === 0) && (
                        <div className="flex items-center justify-center h-full text-text-dim text-sm">
                            Loading graph data...
                        </div>
                    )}

                    {/* Terminal toggle button */}
                    {isParsed && selectedNode && (
                        <button
                            onClick={() => setTerminalOpen(p => !p)}
                            className="absolute bottom-2 right-2 z-10 px-2.5 py-1 rounded glass-panel-light text-[10px] text-text-dim hover:text-white hover:border-accent/40 transition-colors flex items-center gap-1.5"
                        >
                            <div className={`w-2 h-2 rounded-full ${terminalOpen ? 'bg-green-500' : 'bg-text-dim'}`} />
                            {terminalOpen ? 'Close Terminal' : 'Open Terminal'}
                        </button>
                    )}
                </div>

                {/* Terminal resize handle */}
                {isParsed && terminalOpen && (
                    <div
                        className="h-1 cursor-row-resize hover:bg-accent/40 bg-transparent transition-colors shrink-0 relative z-10"
                        onMouseDown={handleTerminalResizeStart}
                    />
                )}

                {/* Node Details / Terminal Panel (bottom) */}
                {isParsed && terminalOpen && selectedNode && (
                    <div
                        className="shrink-0 border-t-0 glass-panel z-10"
                        style={{ height: terminalHeight }}
                    >
                        <NodeDetails node={selectedNode} graphData={graphData} />
                    </div>
                )}
            </div>
        </DashboardLayout>
        </>
    );
}
