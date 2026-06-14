import React from 'react';
import { GitBranch, Network, Search, MessageSquare, Loader2, Check, Circle, Bot, User, ChevronDown, ChevronUp } from 'lucide-react';
import BrandLogo from './BrandLogo';
import InteractiveBackground from './InteractiveBackground';

const FEATURES = [
    { icon: Network, title: 'Code map', desc: 'Understand how files relate' },
    { icon: Search, title: 'Deep search', desc: 'Find logic anywhere in the repo' },
    { icon: MessageSquare, title: 'Natural Q&A', desc: 'Ask questions in plain English' },
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

export function ProgressPanel({ progress, message, stage, isError }) {
    return (
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
}

export default function LandingPage({ repoUrl, setRepoUrl, handleParse, isParsing, jobProgress, messages, handleSend, isTyping, toggleReason, expandedReason, messagesEndRef }) {
    const cardGlow = useCardGlow();
    const showSetup = !isParsing && !isTyping && (!messages || messages.filter(m => !m.isStatus).length === 0);
    const showProgress = isParsing || (jobProgress.stage === 'error');
    const hasChatMessages = messages && messages.filter(m => !m.isStatus).length > 0;

    return (
        <div className="relative h-[100dvh] overflow-hidden text-slate-200 bg-background">
            <InteractiveBackground />

            <div className="relative z-10 flex h-full flex-col">
                <header className="flex shrink-0 flex-col gap-3 border-b border-white/5 bg-[#050508]/40 px-4 py-3 backdrop-blur-2xl sm:px-6 md:px-8">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex min-w-0 flex-col items-center gap-2 sm:items-start sm:gap-1">
                            <BrandLogo variant="header" />
                            <div className="flex w-full min-w-0 items-center justify-between gap-2 sm:justify-start">
                                <p className="truncate text-center text-[11px] text-white/40 sm:text-left">
                                    Chat with any codebase
                                </p>
                                <span className="shrink-0 rounded-md bg-accent/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-accent/90 sm:hidden">
                                    Beta
                                </span>
                            </div>
                        </div>
                    </div>
                </header>

                <main className="flex-1 overflow-y-auto overscroll-contain px-3 pb-48 pt-3 sm:px-4 md:px-6 md:pb-44 md:pt-10">
                    <div className="mx-auto max-w-3xl space-y-5 sm:space-y-6">
                        {showSetup && (
                            <>
                                <div className="flex justify-center px-2 pt-2 sm:pt-4">
                                    <BrandLogo variant="hero" />
                                </div>
                                <div className="chat-setup-card fade-in-up mx-auto w-full max-w-xl rounded-2xl p-5 sm:p-8" {...cardGlow}>
                                    <div className="relative z-10">
                                        <p className="mb-1 text-center text-xs uppercase tracking-[0.2em] text-accent/70 sm:text-left">Get started in seconds</p>
                                        <h2 className="mb-2 text-xl font-medium text-white sm:text-2xl">
                                            Turn any repo into a conversation
                                        </h2>
                                        <p className="mb-6 text-sm leading-relaxed text-white/50">
                                            Paste a GitHub link — we'll learn the codebase and answer your questions instantly.
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

                        {hasChatMessages && (
                            <div className="space-y-4">
                                {messages.filter(m => !m.isStatus).map((msg) => (
                                    <div key={msg.id} className={`flex gap-2.5 fade-in-up sm:gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
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
                                                        <div className="mt-2 rounded-xl border border-white/5 bg-black/40 backdrop-blur-md p-3 text-xs leading-relaxed text-white/50">
                                                            {msg.reason}
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                ))}

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
                        )}
                    </div>
                </main>
            </div>
        </div>
    );
}
