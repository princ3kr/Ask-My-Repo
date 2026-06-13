import React from 'react';
import { GitBranch, Network, Search, MessageSquare, Loader2, Check, Circle, Bot, User, ChevronDown, ChevronUp } from 'lucide-react';

const FEATURES = [
    { icon: Network, title: 'Code Map', desc: 'Understand relations' },
    { icon: Search, title: 'Deep Search', desc: 'Find any logic' },
    { icon: MessageSquare, title: 'Q&A', desc: 'Ask in plain English' },
];

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

const StageStep = ({ stage, currentStage, isError }) => {
    const currentIdx = STAGE_ORDER.indexOf(currentStage);
    const stepIdx = STAGE_ORDER.indexOf(stage.id);
    const isDone = !isError && (currentStage === 'done' || (currentIdx > stepIdx && currentIdx !== -1));
    const isActive = !isError && stage.id === currentStage;

    return (
        <div className={`flex flex-col items-center gap-1 text-center transition-all duration-300 ${isActive ? 'scale-105' : ''} ${isDone ? 'opacity-70' : ''} ${!isDone && !isActive ? 'opacity-30' : ''}`}>
            <div className={`w-5 h-5 rounded-full flex items-center justify-center border transition-all ${isDone ? 'bg-accent/15 border-accent/30 text-accent' : ''} ${isActive ? 'border-accent/40 text-accent shadow-[0_0_10px_rgba(139,92,246,0.2)]' : ''} ${!isDone && !isActive ? 'border-white/10 text-white/40' : ''}`}>
                {isDone ? <Check size={10} strokeWidth={3} /> : isActive ? <Loader2 size={10} className="animate-spin" /> : <Circle size={8} />}
            </div>
            <span className="text-[10px] leading-tight text-white/45">{stage.label}</span>
        </div>
    );
};

export function ProgressPanel({ progress, message, stage, isError }) {
    return (
        <div className={`relative overflow-hidden bg-white/3 backdrop-blur-xl border rounded-2xl p-5 sm:p-6 shadow-[0_16px_40px_-12px_rgba(0,0,0,0.45)] transition-all ${isError ? 'border-red-500/25 bg-red-500/5' : 'border-white/5'}`}>
            <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                    <p className="text-xs font-medium uppercase tracking-wider text-accent/70">
                        {isError ? 'Something went wrong' : 'Setting up your repository'}
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-white/80">{message}</p>
                </div>
                {!isError && (
                    <span className="shrink-0 rounded-full bg-white/5 px-2.5 py-1 text-xs font-medium text-white/60">
                        {progress}%
                    </span>
                )}
            </div>
            {!isError && (
                <>
                    <div className="h-1.5 rounded-full bg-white/5 overflow-hidden mb-4">
                        <div className="h-full rounded-full bg-gradient-to-r from-accent to-purple-500 transition-all duration-700 shadow-[0_0_12px_rgba(139,92,246,0.35)]" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="grid grid-cols-7 gap-1">
                        {INDEX_STAGES.map((s) => (
                            <StageStep key={s.id} stage={s} currentStage={stage} isError={isError} />
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}

export default function SetupPanel({ repoUrl, setRepoUrl, handleParse, isParsing, jobProgress, messages, handleSend, isTyping, toggleReason, expandedReason, messagesEndRef }) {
    const hasMessages = messages && messages.filter(m => !m.isStatus).length > 0;
    const showProgress = isParsing || jobProgress.stage === 'error';

    return (
        <div className="w-full max-w-xl mx-auto px-4 space-y-5">
            {/* Brand */}
            <div className="text-center mb-4">
                <div className="flex items-center justify-center gap-2 mb-2">
                    <div className="w-8 h-8 rounded-lg bg-accent/20 border border-accent/30 flex items-center justify-center">
                        <Network size={18} className="text-accent" />
                    </div>
                    <h1 className="text-xl font-bold text-white tracking-tight">Ask My Repo</h1>
                </div>
                <p className="text-xs text-text-dim">Turn any GitHub repository into an interactive knowledge graph</p>
            </div>

            {/* Setup Card */}
            {!hasMessages && !showProgress && (
                <div className="relative overflow-hidden bg-white/3 backdrop-blur-xl border border-white/5 rounded-2xl p-5 sm:p-7 shadow-[0_24px_48px_-12px_rgba(0,0,0,0.5)] transition-all hover:border-accent/15">
                    <p className="mb-1 text-xs uppercase tracking-[0.2em] text-accent/70">Get started</p>
                    <h2 className="mb-2 text-lg font-medium text-white">Connect a repository</h2>
                    <p className="mb-5 text-sm leading-relaxed text-white/50">
                        Paste a GitHub URL — we'll analyze the code and build a rich dependency graph.
                    </p>

                    <div className="flex flex-col sm:flex-row gap-3">
                        <div className="relative flex-1">
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
                            disabled={!repoUrl.trim() || isParsing}
                            className="btn-primary w-full sm:w-auto shrink-0 px-6 py-3 text-sm disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {isParsing ? 'Connecting...' : 'Connect'}
                        </button>
                    </div>

                    <div className="mt-5 grid grid-cols-3 gap-2">
                        {FEATURES.map(({ icon: Icon, title, desc }) => (
                            <div key={title} className="flex flex-col items-start gap-1.5 p-2.5 rounded-xl border border-white/5 bg-black/20">
                                <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-br from-accent/10 to-purple-500/10 text-accent">
                                    <Icon size={14} />
                                </div>
                                <p className="text-xs font-medium text-white/80">{title}</p>
                                <p className="text-[10px] leading-snug text-white/40">{desc}</p>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Progress */}
            {showProgress && (
                <ProgressPanel
                    progress={jobProgress.progress}
                    message={jobProgress.message}
                    stage={jobProgress.stage}
                    isError={jobProgress.stage === 'error'}
                />
            )}

            {/* Chat messages */}
            {hasMessages && (
                <div className="space-y-3 max-h-96 overflow-y-auto">
                    {messages.filter(m => !m.isStatus).map((msg) => (
                        <div key={msg.id} className={`flex gap-2.5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                            <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-1 ${
                                msg.role === 'user'
                                    ? 'bg-white text-background ring-white/20'
                                    : 'bg-white/5 text-accent ring-white/10'
                            }`}>
                                {msg.role === 'user' ? <User size={13} /> : <Bot size={13} />}
                            </div>
                            <div className={`min-w-0 max-w-[85%] ${msg.role === 'user' ? 'text-right' : ''}`}>
                                <div className={`inline-block rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                                    msg.role === 'user'
                                        ? 'chat-bubble-user rounded-tr-md'
                                        : 'chat-bubble-assistant rounded-tl-md'
                                }`}>
                                    <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                                </div>
                                {msg.reason && (
                                    <div className="mt-1">
                                        <button
                                            onClick={() => toggleReason(msg.id)}
                                            className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-dim hover:text-accent transition-colors"
                                        >
                                            {expandedReason[msg.id] ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                            Why this answer
                                        </button>
                                        {expandedReason[msg.id] && (
                                            <div className="mt-1 rounded-lg border border-white/5 bg-black/40 p-2 text-[11px] leading-relaxed text-text-dim">
                                                {msg.reason}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                    {isTyping && (
                        <div className="flex gap-2.5">
                            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/5 text-accent ring-1 ring-white/10">
                                <Bot size={13} />
                            </div>
                            <div className="chat-bubble-assistant inline-flex items-center gap-2 rounded-2xl rounded-tl-md px-3.5 py-2.5">
                                <Loader2 size={14} className="animate-spin text-accent/70" />
                                <span className="text-sm text-white/50">Looking through your codebase…</span>
                            </div>
                        </div>
                    )}
                    <div ref={messagesEndRef} />
                </div>
            )}

            {/* Chat input after progress completes */}
            {!showProgress && hasMessages && (
                <div className="relative">
                    <input
                        type="text"
                        placeholder="Ask a question about this repository..."
                        className="w-full rounded-xl border border-white/10 bg-black/40 py-3 pl-4 pr-12 text-sm text-white placeholder-white/25 outline-none transition focus:border-accent/40 focus:ring-1 focus:ring-accent/30"
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && e.target.value.trim()) {
                                handleSend(e.target.value);
                                e.target.value = '';
                            }
                        }}
                    />
                    <button
                        onClick={() => {
                            const input = document.querySelector('[placeholder="Ask a question about this repository..."]');
                            if (input && input.value.trim()) {
                                handleSend(input.value);
                                input.value = '';
                            }
                        }}
                        className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 rounded-lg bg-accent/20 text-accent flex items-center justify-center hover:bg-accent/30 transition-colors"
                    >
                        <MessageSquare size={14} />
                    </button>
                </div>
            )}
        </div>
    );
}
