import React, { useState, useEffect, useRef, useCallback } from 'react';
import DashboardLayout from './components/layout/DashboardLayout';
import Explorer from './components/sidebar/Explorer';
import QueryPanel from './components/sidebar/QueryPanel';
import ReactFlowGraph from './components/graph/ReactFlowGraph';
import NodeDetails from './components/panels/NodeDetails';
import SetupPanel from './components/panels/SetupPanel';
import { RotateCcw } from 'lucide-react';
import { normalizeRepoUrl, repoShortName } from './utils';

const API_URL = '/api';
const SESSION_STORAGE_KEY = 'ask_my_repo_session_id';

const getOrCreateSessionId = () => {
    let id = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(SESSION_STORAGE_KEY, id);
    }
    return id;
};

export default function App() {
    const [repoUrl, setRepoUrl] = useState('');
    const [repoId, setRepoId] = useState('');
    const [isParsing, setIsParsing] = useState(false);
    const [isParsed, setIsParsed] = useState(false);
    const [stats, setStats] = useState({ files: 0, classes: 0, functions: 0, imports: 0, calls: 0, nodes: 0, edges: 0 });
    const [treePaths, setTreePaths] = useState([]);
    const [graphData, setGraphData] = useState({ nodes: [], edges: [] });
    const [isTyping, setIsTyping] = useState(false);
    const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
    const [jobProgress, setJobProgress] = useState({ progress: 0, message: '', stage: 'starting' });
    const [selectedNode, setSelectedNode] = useState(null);
    const [selectedFilePath, setSelectedFilePath] = useState(null);
    const [messages, setMessages] = useState([]);
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [expandedReason, setExpandedReason] = useState({});
    const pollRef = useRef(null);
    const messagesEndRef = useRef(null);
    const graphRef = useRef(null);

    useEffect(() => {
        return () => {
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, []);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, isTyping]);

    const appendMessage = (msg) => setMessages((prev) => [...prev, msg]);

    const pollJobStatus = useCallback((jobId) => new Promise((resolve, reject) => {
        const poll = async () => {
            try {
                const res = await fetch(`${API_URL}/parse/status/${jobId}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Lost connection to the server');

                setJobProgress({
                    progress: data.progress ?? 0,
                    message: data.message ?? 'Working on it…',
                    stage: data.stage ?? 'starting',
                });

                if (data.status === 'done') {
                    clearInterval(pollRef.current);
                    pollRef.current = null;
                    resolve(data.result);
                } else if (data.status === 'error') {
                    clearInterval(pollRef.current);
                    pollRef.current = null;
                    reject(new Error(data.error || data.message));
                }
            } catch (e) {
                clearInterval(pollRef.current);
                pollRef.current = null;
                reject(e);
            }
        };
        poll();
        pollRef.current = setInterval(poll, 700);
    }), []);

    const handleParse = async () => {
        const normalized = normalizeRepoUrl(repoUrl);
        if (!normalized) return;

        setRepoUrl(normalized);
        setIsParsing(true);
        setShowSuggestions(false);
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
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Could not start setup');

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
            setShowSuggestions(true);

            appendMessage({
                id: Date.now() + 2,
                role: 'assistant',
                content: `Done! I've learned ${result.files_count} files across ${totalNodes} connected parts. Pick a suggestion below or ask anything.`,
            });
        } catch (e) {
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

        fetch(`${API_URL}/tree/${repoId}`)
            .then(r => r.json())
            .then(data => { if (data.paths) setTreePaths(data.paths); })
            .catch(console.error);

        fetch(`${API_URL}/graph_data/${repoId}`)
            .then(r => r.json())
            .then(data => {
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
            .catch(console.error);
    }, [repoId]);

    const handleSend = async (query) => {
        if (!query || !isParsed || isTyping) return;

        const newUserMsg = { id: Date.now(), role: 'user', content: query };
        setMessages((prev) => [...prev, newUserMsg]);
        setIsTyping(true);
        setShowSuggestions(false);

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_url: repoUrl,
                    query,
                    session_id: sessionId,
                }),
            });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Could not get an answer right now');
            }

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
            appendMessage({
                id: Date.now() + 1,
                role: 'assistant',
                content: e.message,
            });
        } finally {
            setIsTyping(false);
        }
    };

    const toggleReason = (id) => {
        setExpandedReason((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    const handleNewSession = () => {
        if (pollRef.current) clearInterval(pollRef.current);
        const newId = crypto.randomUUID();
        localStorage.setItem(SESSION_STORAGE_KEY, newId);
        setSessionId(newId);
        setRepoUrl('');
        setIsParsing(false);
        setIsParsed(false);
        setStats({ files: 0, classes: 0, functions: 0, imports: 0, calls: 0, nodes: 0, edges: 0 });
        setJobProgress({ progress: 0, message: '', stage: 'starting' });
        setShowSuggestions(false);
        setMessages([]);
        setTreePaths([]);
        setGraphData({ nodes: [], edges: [] });
        setSelectedNode(null);
        setSelectedFilePath(null);
        setExpandedReason({});
        setRepoId('');
    };

    const handleNodeClick = useCallback((node) => {
        setSelectedNode(node);
        setSelectedFilePath(node.data?.path || null);
    }, []);

    const handleFileSelect = useCallback((filePath) => {
        setSelectedFilePath(filePath);
        if (graphRef.current && graphRef.current.fitViewForNode) {
            graphRef.current.fitViewForNode(filePath);
        }
        const matchingNode = graphData.nodes?.find(n =>
            n.data?.path === filePath || n.id === filePath || n.data?.label === filePath
        );
        if (matchingNode) {
            setSelectedNode(matchingNode);
        }
    }, [graphData]);

    return (
        <DashboardLayout
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
                            isParsing={isParsing}
                            isParsed={isParsed}
                            isTyping={isTyping}
                            messages={messages}
                            expandedReason={expandedReason}
                            toggleReason={toggleReason}
                            messagesEndRef={messagesEndRef}
                            repoUrl={repoUrl}
                            handleNewSession={handleNewSession}
                            stats={stats}
                        />
                    </div>
                ) : null
            }
        >
            <div className="flex-1 relative overflow-hidden bg-background">
                {/* Overlay Setup Panel when not parsed */}
                {!isParsed && (
                    <div className="absolute inset-0 z-20 flex items-center justify-center bg-background">
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
                {isParsed && graphData.nodes?.length > 0 && (
                    <ReactFlowGraph
                        ref={graphRef}
                        graphData={graphData}
                        onNodeClick={handleNodeClick}
                        selectedNodeId={selectedNode?.id || null}
                        selectedFilePath={selectedFilePath}
                    />
                )}

                {/* Empty state when parsed but no graph data */}
                {isParsed && (!graphData.nodes || graphData.nodes.length === 0) && (
                    <div className="flex items-center justify-center h-full text-text-dim text-sm">
                        Loading graph data...
                    </div>
                )}
            </div>

            {/* Node Details Panel at bottom of center area */}
            {isParsed && selectedNode && (
                <NodeDetails node={selectedNode} graphData={graphData} />
            )}
        </DashboardLayout>
    );
}
