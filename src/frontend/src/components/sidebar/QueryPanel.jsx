import React, { useState } from 'react';
import { Send, MessageSquare, Bot, User, Loader2, ChevronDown, ChevronUp, RotateCcw, Network } from 'lucide-react';
import clsx from 'clsx';

const suggestions = [
    "Show me authentication flow",
    "What depends on AuthService?",
    "Find all database connections",
    "Show inheritance hierarchy",
    "List unused functions"
];

function ChatHistory({ messages, isTyping, expandedReason, toggleReason, messagesEndRef }) {
    if (!messages || messages.filter(m => !m.isStatus).length === 0) {
        return (
            <div className="flex-1 flex flex-col items-center justify-center text-text-dim text-sm p-4 text-center space-y-2">
                <Network size={28} className="text-accent/30" />
                <span>Ask a question about your codebase</span>
            </div>
        );
    }

    return (
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {messages.filter(m => !m.isStatus).map((msg) => (
                <div key={msg.id} className={clsx("flex gap-2.5", msg.role === 'user' && 'flex-row-reverse')}>
                    <div className={clsx(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                        msg.role === 'user' ? 'bg-accent text-white' : 'bg-white/5 text-accent'
                    )}>
                        {msg.role === 'user' ? <User size={11} /> : <Bot size={11} />}
                    </div>
                    <div className={clsx("min-w-0 max-w-[90%]", msg.role === 'user' ? 'text-right' : '')}>
                        <div className={clsx(
                            "inline-block rounded-lg px-3 py-2 text-sm leading-relaxed",
                            msg.role === 'user'
                                ? 'bg-accent/20 text-text-color rounded-tr-sm'
                                : 'glass-panel-light text-text-color rounded-tl-sm'
                        )}>
                            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                        </div>
                        {msg.reason && (
                            <div className={clsx("mt-1", msg.role === 'user' ? 'text-right' : 'text-left')}>
                                <button
                                    onClick={() => toggleReason(msg.id)}
                                    className="inline-flex items-center gap-0.5 text-[10px] uppercase tracking-wider text-text-dim hover:text-accent transition-colors"
                                >
                                    {expandedReason[msg.id] ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                    Why this answer
                                </button>
                                {expandedReason[msg.id] && (
                                    <div className="mt-1.5 rounded-lg glass-panel-light p-2 text-xs leading-relaxed text-text-dim text-left">
                                        {msg.reason}
                                        {msg.rewritten_query && (
                                            <div className="mt-1 pt-1 border-t border-surface-muted/50">
                                                <span className="text-accent/70">Rewritten:</span> {msg.rewritten_query}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            ))}
            {isTyping && (
                <div className="flex gap-2.5">
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/5 text-accent">
                        <Bot size={11} />
                    </div>
                    <div className="inline-flex items-center gap-1.5 rounded-lg glass-panel-light px-3 py-2">
                        <Loader2 size={12} className="animate-spin text-accent/70" />
                        <span className="text-xs text-text-dim">Thinking...</span>
                    </div>
                </div>
            )}
            <div ref={messagesEndRef} />
        </div>
    );
}

export default function QueryPanel({ onSend, isParsing, isParsed, isTyping, messages, expandedReason, toggleReason, messagesEndRef, repoUrl, handleNewSession, stats }) {
    const [input, setInput] = useState('');

    const handleSend = () => {
        if (!input.trim() || !isParsed || isTyping) return;
        onSend(input);
        setInput('');
    };

    const handleSuggestion = (s) => {
        setInput(s);
        setTimeout(() => onSend(s), 50);
    };

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* Tab Header */}
            <div className="px-4 py-3 border-b border-surface-muted flex items-center justify-between shrink-0">
                <span className="text-sm font-semibold text-text-dim uppercase tracking-wider flex items-center gap-1.5">
                    <MessageSquare size={14} /> Ask
                </span>
                <div className="flex items-center gap-1">
                    <span className="text-[10px] text-text-dim">{stats.nodes || 0} nodes</span>
                </div>
            </div>

            {/* Input area */}
            <div className="p-3 shrink-0">
                <div className="relative">
                    <textarea
                        className="w-full h-20 glass-panel-light rounded-lg p-3 text-sm text-text-color placeholder-text-dim resize-none outline-none focus:border-accent/40 focus:ring-1 focus:ring-accent/20 transition-all"
                        placeholder="Ask about your repository..."
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey) {
                                e.preventDefault();
                                handleSend();
                            }
                        }}
                    />
                    <button
                        onClick={handleSend}
                        disabled={!input.trim() || !isParsed || isTyping}
                        className="absolute bottom-3 right-3 w-7 h-7 rounded-md bg-accent/20 text-accent flex items-center justify-center hover:bg-accent/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <Send size={13} />
                    </button>
                </div>
            </div>

            {/* Suggestions when empty */}
            {messages.length === 0 && (
                <div className="px-3 pb-2 shrink-0">
                    <div className="text-[10px] font-semibold text-text-dim uppercase tracking-wider mb-2 px-1">Try asking</div>
                    <div className="space-y-1.5">
                        {suggestions.map(s => (
                            <button
                                key={s}
                                className="suggestion-chip text-left w-full text-xs"
                                onClick={() => handleSuggestion(s)}
                            >
                                {s}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Chat history */}
            <ChatHistory
                messages={messages}
                isTyping={isTyping}
                expandedReason={expandedReason}
                toggleReason={toggleReason}
                messagesEndRef={messagesEndRef}
            />
        </div>
    );
}
