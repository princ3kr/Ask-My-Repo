import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
    GitBranch, Send, Bot, User, Loader2,
    ChevronDown, ChevronUp, Check, Circle,
    Network, Search, MessageSquare, RotateCcw,
} from 'lucide-react';
import InteractiveBackground from './components/InteractiveBackground';
import BrandLogo, { PRODUCT_NAME } from './components/BrandLogo';

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

const INDEX_STAGES = [
    { id: 'fetching', label: 'Download' },
    { id: 'graph_building', label: 'Understand' },
    { id: 'graph_saving', label: 'Connect' },
    { id: 'vector_building', label: 'Organize' },
    { id: 'vector_saving', label: 'Searchable' },
    { id: 'assistant_ready', label: 'Prepare' },
    { id: 'done', label: 'Ready' },
];

const STAGE_ORDER = INDEX_STAGES.map((s) => s.id);

const FEATURES = [
    { icon: Network, title: 'Code map', desc: 'Understand how files relate' },
    { icon: Search, title: 'Deep search', desc: 'Find logic anywhere in the repo' },
    { icon: MessageSquare, title: 'Natural Q&A', desc: 'Ask questions in plain English' },
];

const SUGGESTED_QUESTIONS = [
    'What are the main entry points?',
    'How do modules depend on each other?',
    'Where is the core business logic?',
    'What patterns does this codebase use?',
];

const normalizeRepoUrl = (url) => {
    const trimmed = url.trim();
    if (!trimmed) return '';
    if (/^https?:\/\//i.test(trimmed) || trimmed.startsWith('git@')) return trimmed.replace(/\/$/, '');
    return `https://${trimmed.replace(/\/$/, '')}`;
};

const repoShortName = (url) => {
    if (!url) return '';
    try {
        const parts = new URL(url).pathname.split('/').filter(Boolean);
        return parts.length >= 2 ? parts.slice(-2).join('/') : parts[parts.length - 1] || url;
    } catch {
        return url.split('/').slice(-2).join('/') || url;
    }
};

function useCardGlow() {
    const onMove = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        e.currentTarget.style.setProperty('--card-x', `${e.clientX - rect.left}px`);
        e.currentTarget.style.setProperty('--card-y', `${e.clientY - rect.top}px`);
    };
    return { onMouseMove: onMove, onMouseLeave: (e) => {
        e.currentTarget.style.setProperty('--card-x', '50%');
        e.currentTarget.style.setProperty('--card-y', '50%');
    }};
}

const AnimatedCounter = ({ value, duration = 1500 }) => {
    const [count, setCount] = useState(0);

    useEffect(() => {
        let startTime;
        const endValue = parseInt(value.toString().replace(/,/g, ''), 10);
        if (isNaN(endValue)) {
            setCount(value);
            return;
        }

        const animate = (time) => {
            if (!startTime) startTime = time;
            const progress = Math.min((time - startTime) / duration, 1);
            const ease = 1 - Math.pow(1 - progress, 4);
            setCount(Math.floor(ease * endValue));
            if (progress < 1) requestAnimationFrame(animate);
            else setCount(endValue);
        };

        requestAnimationFrame(animate);
    }, [value, duration]);

    return <span>{typeof count === 'number' ? count.toLocaleString() : count}</span>;
};

const StatusBadge = ({ isParsing, isParsed }) => {
    if (isParsing) {
        return (
            <span className="status-badge">
                <span className="status-dot status-dot-busy" />
                Setting up
            </span>
        );
    }
    if (isParsed) {
        return (
            <span className="status-badge">
                <span className="status-dot status-dot-live" />
                Ready
            </span>
        );
    }
    return (
        <span className="status-badge">
            <span className="status-dot" />
            Waiting
        </span>
    );
};

const StageStep = ({ stage, currentStage, isError }) => {
    const currentIdx = STAGE_ORDER.indexOf(currentStage);
    const stepIdx = STAGE_ORDER.indexOf(stage.id);
    const isDone = !isError && (currentStage === 'done' || (currentIdx > stepIdx && currentIdx !== -1));
    const isActive = !isError && stage.id === currentStage;

    return (
        <div className={`stage-step ${isActive ? 'stage-step-active' : ''} ${isDone ? 'stage-step-done' : ''}`}>
            <div className="stage-step-icon">
                {isDone ? <Check size={10} strokeWidth={3} /> : isActive ? <Loader2 size={10} className="animate-spin" /> : <Circle size={8} />}
            </div>
            <span className="stage-step-label">{stage.label}</span>
        </div>
    );
};

const ProgressPanel = ({ progress, message, stage, isError }) => (
    <div className={`progress-panel fade-in-up ${isError ? 'progress-panel-error' : ''}`}>
        <div className="mb-4 flex items-start justify-between gap-3">
            <div>
                <p className="text-xs font-medium uppercase tracking-wider text-accent/70">
                    {isError ? 'Something went wrong' : 'Setting up your repository'}
                </p>
                <p className="mt-1 text-sm leading-relaxed text-white/80 sm:text-base">{message}</p>
            </div>
            {!isError && (
                <span className="shrink-0 rounded-full bg-white/5 px-2.5 py-1 text-xs font-medium text-white/60">
                    {progress}%
                </span>
            )}
        </div>

        {!isError && (
            <>
                <div className="progress-track mb-4">
                    <div className="progress-fill" style={{ width: `${progress}%` }} />
                </div>
                <div className="stage-grid">
                    {INDEX_STAGES.map((s) => (
                        <StageStep key={s.id} stage={s} currentStage={stage} isError={isError} />
                    ))}
                </div>
            </>
        )}
    </div>
);

const App = () => {
    const [repoUrl, setRepoUrl] = useState('');
    const [isParsing, setIsParsing] = useState(false);
    const [isParsed, setIsParsed] = useState(false);
    const [stats, setStats] = useState({ files: 0, nodes: 0, edges: 0 });
    const [jobProgress, setJobProgress] = useState({ progress: 0, message: '', stage: 'starting' });
    const [messages, setMessages] = useState([
        {
            id: 1,
            role: 'assistant',
            content: `Welcome to ${PRODUCT_NAME} — connect any GitHub repo and ask questions about how it works, what depends on what, and where things live.`,
        },
    ]);
    const [input, setInput] = useState('');
    const [isTyping, setIsTyping] = useState(false);
    const [expandedReason, setExpandedReason] = useState({});
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [sessionId, setSessionId] = useState(() => getOrCreateSessionId());
    const [repoId, setRepoId] = useState('');
    const [graphOpen, setGraphOpen] = useState(false);
    const [graphHtml, setGraphHtml] = useState(null);
    const messagesEndRef = useRef(null);
    const pollRef = useRef(null);
    const cardGlow = useCardGlow();

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isTyping, jobProgress]);

    useEffect(() => () => {
        if (pollRef.current) clearInterval(pollRef.current);
    }, []);

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

            setIsParsed(true);
            setRepoId(result.repo_id || '');
            setStats({
                files: result.files_count || 0,
                nodes: result.nodes_count || 0,
                edges: result.edges_count || 0,
            });
            setShowSuggestions(true);

            appendMessage({
                id: Date.now() + 2,
                role: 'assistant',
                content: `Done! I've learned ${result.files_count} files across ${result.nodes_count} connected parts. Pick a suggestion below or ask anything.`,
            });
            // Fetch graph HTML once and cache it
            try {
                if (result.repo_id && !graphHtml) {
                    const r = await fetch(`${API_URL}/graph/${result.repo_id}`);
                    if (r.ok) {
                        const html = await r.text();
                        setGraphHtml(html);
                    }
                }
            } catch (e) {
                // ignore graph fetch failures (optional)
                console.warn('Graph fetch failed', e);
            }
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

    const toggleGraph = () => setGraphOpen((s) => !s);

    const handleDeleteRepo = async () => {
        if (!repoId) return;
        const ok = window.confirm('This will delete all indexed data for this repo. You will need to re-index to use it again. Proceed?');
        if (!ok) return;

        try {
            const res = await fetch(`${API_URL}/cleanup/manual/${repoId}`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Delete failed');

            // Clear cached UI state
            setGraphHtml(null);
            setGraphOpen(false);
            setIsParsed(false);
            setRepoUrl('');
            setMessages([{
                id: Date.now(),
                role: 'assistant',
                content: 'Repository cleared. Connect a repo to get started.',
            }]);
            setSessionId(crypto.randomUUID());
            localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
        } catch (e) {
            window.alert(e.message || 'Failed to delete repo');
        }
    };

    const handleNewSession = () => {
        if (pollRef.current) clearInterval(pollRef.current);
        const newSessionId = crypto.randomUUID();
        localStorage.setItem(SESSION_STORAGE_KEY, newSessionId);
        setSessionId(newSessionId);
        setRepoUrl('');
        setIsParsing(false);
        setIsParsed(false);
        setStats({ files: 0, nodes: 0, edges: 0 });
        setJobProgress({ progress: 0, message: '', stage: 'starting' });
        setShowSuggestions(false);
        setMessages([{
            id: Date.now(),
            role: 'assistant',
            content: 'Fresh start — connect a new repository whenever you\'re ready.',
        }]);
        setInput('');
        setExpandedReason({});
    };

    const handleSend = async (textOverride) => {
        const query = (textOverride ?? input).trim();
        if (!query || !isParsed || isTyping) return;

        const newUserMsg = { id: Date.now(), role: 'user', content: query };
        setMessages((prev) => [...prev, newUserMsg]);
        setInput('');
        setIsTyping(true);
        setShowSuggestions(false);

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_url: normalizeRepoUrl(repoUrl),
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

    const showSetup = !isParsed && !isParsing;
    const showProgress = isParsing || (jobProgress.stage === 'error' && !isParsed);

    return (
        <div className="relative h-[100dvh] overflow-hidden text-slate-200">
            <InteractiveBackground />
            <div className="grain" />

            <div className="relative z-10 flex h-full flex-col">
                <header className="flex shrink-0 flex-col gap-3 border-b border-white/5 bg-[#050508]/50 px-4 py-3 backdrop-blur-xl sm:px-6 md:px-8">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex min-w-0 flex-col items-center gap-2 sm:items-start sm:gap-1">
                            <BrandLogo variant={showSetup ? 'compact' : 'header'} />
                            <div className="flex w-full min-w-0 items-center justify-between gap-2 sm:justify-start">
                                <p className="truncate text-center text-[11px] text-white/40 sm:text-left">
                                    {isParsed ? repoShortName(repoUrl) : 'Chat with any codebase'}
                                </p>
                                <span className="shrink-0 rounded-md bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-accent/90 sm:hidden">
                                    Beta
                                </span>
                            </div>
                        </div>

                        <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-end sm:gap-3">
                            <span className="hidden rounded-md bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-accent/90 sm:inline">
                                Beta
                            </span>
                            <StatusBadge isParsing={isParsing} isParsed={isParsed} />

                        {isParsed && (
                            <>
                                <div className="stats-pill">
                                    <span><AnimatedCounter value={stats.files} /> files</span>
                                    <span className="hidden text-white/20 sm:inline">·</span>
                                    <span className="text-accent/80"><AnimatedCounter value={stats.nodes} /> parts</span>
                                    <span className="hidden text-white/20 sm:inline">·</span>
                                    <span><AnimatedCounter value={stats.edges} /> links</span>
                                </div>
                                <button
                                    onClick={handleNewSession}
                                    className="btn-ghost flex items-center gap-1.5"
                                    title="Connect a different repo"
                                >
                                    <RotateCcw size={12} />
                                    <span className="hidden sm:inline">New repo</span>
                                </button>
                            </>
                        )}

                        {isParsing && (
                            <div className="flex items-center gap-2 text-xs text-accent/80 lg:hidden">
                                <Loader2 size={14} className="animate-spin" />
                                <span>{jobProgress.progress}%</span>
                            </div>
                        )}
                        </div>
                    </div>
                </header>

                <main className="flex-1 overflow-y-auto overscroll-contain px-3 pb-48 pt-3 sm:px-4 md:px-6 md:pb-44 md:pt-4" style={{ marginRight: graphOpen ? '35%' : undefined }}>
                    <div className="mx-auto max-w-3xl space-y-5 sm:space-y-6">
                        {showSetup && (
                            <>
                                <div className="flex justify-center px-2 pt-2 sm:pt-4">
                                    <BrandLogo variant="hero" />
                                </div>
                                <div
                                    className="chat-setup-card fade-in-up mx-auto w-full max-w-xl rounded-2xl p-5 sm:p-8"
                                    {...cardGlow}
                                >
                                    <div className="relative z-10">
                                        <p className="mb-1 text-center text-xs uppercase tracking-[0.2em] text-accent/70 sm:text-left">Get started in seconds</p>
                                    <h2 className="mb-2 text-xl font-medium text-white sm:text-2xl">
                                        Turn any repo into a conversation
                                    </h2>
                                    <p className="mb-6 text-sm leading-relaxed text-white/50">
                                        Paste a GitHub link — we&apos;ll learn the codebase and answer your questions instantly.
                                    </p>

                                    <div className="feature-grid mb-6">
                                        {FEATURES.map(({ icon: Icon, title, desc }) => (
                                            <div key={title} className="feature-pill">
                                                <div className="feature-icon">
                                                    <Icon size={15} />
                                                </div>
                                                <div className="min-w-0">
                                                    <p className="text-xs font-medium text-white/90">{title}</p>
                                                    <p className="text-[11px] leading-snug text-white/40">{desc}</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>

                                    <div className="flex flex-col gap-3 sm:flex-row">
                                        <div className="relative min-w-0 flex-1">
                                            <GitBranch size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
                                            <input
                                                type="text"
                                                placeholder="github.com/owner/repo"
                                                className="w-full rounded-xl border border-white/10 bg-black/40 py-3 pl-10 pr-4 text-sm text-white placeholder-white/25 outline-none transition focus:border-accent/40 focus:ring-1 focus:ring-accent/30"
                                                value={repoUrl}
                                                onChange={(e) => setRepoUrl(e.target.value)}
                                                onKeyDown={(e) => e.key === 'Enter' && handleParse()}
                                            />
                                        </div>
                                        <button
                                            onClick={handleParse}
                                            disabled={!repoUrl.trim()}
                                            className="btn-primary w-full shrink-0 px-6 py-3 text-sm disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
                                        >
                                            Get started
                                        </button>
                                    </div>
                                </div>
                            </div>
                            </>
                        )}

                        {showProgress && (
                            <ProgressPanel
                                progress={jobProgress.progress}
                                message={jobProgress.message}
                                stage={jobProgress.stage}
                                isError={jobProgress.stage === 'error'}
                            />
                        )}

                        {messages.map((msg) => (
                            <div
                                key={msg.id}
                                className={`flex gap-2.5 fade-in-up sm:gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
                            >
                                <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-1 sm:h-8 sm:w-8 ${
                                    msg.role === 'user'
                                        ? 'bg-white text-background ring-white/20'
                                        : 'bg-white/5 text-accent ring-white/10'
                                }`}>
                                    {msg.role === 'user' ? <User size={13} /> : <Bot size={13} />}
                                </div>

                                <div className={`min-w-0 max-w-[88%] sm:max-w-[85%] ${msg.role === 'user' ? 'text-right' : ''}`}>
                                    <div className={`inline-block rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed sm:px-4 sm:py-3 sm:text-[15px] ${
                                        msg.role === 'user'
                                            ? 'chat-bubble-user rounded-tr-md'
                                            : 'chat-bubble-assistant rounded-tl-md'
                                    }`}>
                                        <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                                    </div>

                                    {msg.reason && (
                                        <div className="mt-2 text-left">
                                            <button
                                                onClick={() => toggleReason(msg.id)}
                                                className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-white/35 transition hover:text-accent/70"
                                            >
                                                {expandedReason[msg.id] ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                                Why this answer
                                            </button>
                                            {expandedReason[msg.id] && (
                                                <div className="mt-2 rounded-xl border border-white/5 bg-black/40 p-3 text-xs leading-relaxed text-white/50">
                                                    {msg.reason}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}

                        {showSuggestions && isParsed && !isTyping && (
                            <div className="fade-in-up space-y-2.5 pt-1">
                                <p className="text-[11px] uppercase tracking-wider text-white/30">Try asking</p>
                                <div className="flex flex-wrap gap-2">
                                    {SUGGESTED_QUESTIONS.map((q) => (
                                        <button
                                            key={q}
                                            type="button"
                                            onClick={() => handleSend(q)}
                                            className="suggestion-chip"
                                        >
                                            {q}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {isTyping && (
                            <div className="flex gap-2.5 fade-in-up sm:gap-3">
                                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/5 text-accent ring-1 ring-white/10 sm:h-8 sm:w-8">
                                    <Bot size={13} />
                                </div>
                                <div className="chat-bubble-assistant inline-flex items-center gap-2 rounded-2xl rounded-tl-md px-3.5 py-2.5 sm:px-4 sm:py-3">
                                    <Loader2 size={16} className="animate-spin text-accent/70" />
                                    <span className="text-sm text-white/50">Looking through your codebase…</span>
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>
                </main>

                {/* Sliding graph panel */}
                <div
                    className="graph-panel fixed top-16 right-0 h-[calc(100%-4rem)] sm:w-[35%] w-full max-w-[900px] bg-[#071022] shadow-2xl transform transition-transform duration-300 z-20"
                    style={{ transform: graphOpen ? 'translateX(0)' : 'translateX(100%)' }}
                >
                    <div className="flex items-center justify-between border-b border-white/5 p-3">
                        <div className="flex items-center gap-2">
                            <Network size={16} />
                            <strong className="truncate">{repoShortName(repoUrl) || 'Repository graph'}</strong>
                        </div>
                        <div className="flex items-center gap-2">
                            <button onClick={handleDeleteRepo} className="btn-ghost text-sm">Delete</button>
                            <button onClick={toggleGraph} className="btn-ghost">Close</button>
                        </div>
                    </div>
                    <div className="h-[calc(100%-56px)]">
                        {graphHtml ? (
                            <iframe title="repo-graph" srcDoc={graphHtml} style={{ width: '100%', height: '100%', border: 0 }} />
                        ) : (
                            <div className="flex h-full items-center justify-center text-white/50">Graph not available</div>
                        )}
                    </div>
                </div>

                {/* Toggle button always visible on right edge */}
                <button
                    aria-label="Toggle graph panel"
                    onClick={toggleGraph}
                    className="fixed right-2 top-1/2 z-30 flex h-10 w-10 items-center justify-center rounded-full bg-white/5 shadow-lg"
                >
                    <Network size={16} />
                </button>

                <footer className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-[#050508] via-[#050508]/95 to-transparent px-3 pb-4 pt-16 sm:px-4 sm:pb-5 sm:pt-20 md:px-6">
                    <div className="pointer-events-auto mx-auto max-w-3xl">
                        <div className="chat-input-bar flex items-end gap-2 rounded-2xl p-2 pl-3 sm:pl-4">
                            <textarea
                                className="max-h-32 min-h-[42px] flex-1 resize-none bg-transparent py-2.5 text-sm text-white placeholder-white/30 outline-none sm:max-h-36 sm:min-h-[44px] sm:py-3 sm:text-[15px]"
                                rows={1}
                                placeholder={
                                    isParsed
                                        ? 'Ask anything about this codebase…'
                                        : isParsing
                                            ? 'Hang tight — still setting up…'
                                            : 'Connect a repo above to start'
                                }
                                value={input}
                                disabled={!isParsed || isTyping || isParsing}
                                onChange={(e) => {
                                    setInput(e.target.value);
                                    e.target.style.height = 'auto';
                                    e.target.style.height = `${e.target.scrollHeight}px`;
                                }}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        handleSend();
                                    }
                                }}
                            />
                            <button
                                onClick={() => handleSend()}
                                disabled={!input.trim() || !isParsed || isTyping || isParsing}
                                className="btn-primary flex h-10 w-10 shrink-0 items-center justify-center rounded-xl disabled:cursor-not-allowed disabled:opacity-40 sm:h-11 sm:w-11"
                                aria-label="Send message"
                            >
                                <Send size={17} />
                            </button>
                        </div>
                        <div className="mt-2.5 flex flex-col items-center justify-between gap-1 text-[10px] text-white/25 sm:flex-row sm:gap-0">
                            <span>Enter to send · Shift+Enter for new line</span>
                            <span className="hidden sm:inline">Powered by graph + semantic search</span>
                        </div>
                    </div>
                </footer>
            </div>
        </div>
    );
};

export default App;
