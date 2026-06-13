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
            <div className="flex-1 flex flex-col items-center justify-center text-text-dim text-xs p-4 text-center space-y-2">
                <Network size={24} className="text-accent/30" />
                <span>Ask a question about your codebase</span>
            </div>
        );
    }

    return (
        <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {messages.filter(m => !m.isStatus).map((msg) => (
                <div key={msg.id} className={clsx("flex gap-2", msg.role === 'user' && 'flex-row-reverse')}>
                    <div className={clsx(
                        "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                        msg.role === 'user' ? 'bg-white text-background' : 'bg-white/5 text-accent'
                    )}>
                        {msg.role === 'user' ? <User size={9} /> : <Bot size={9} />}
                    </div>
                    <div className={clsx("min-w-0 max-w-[90%]", msg.role === 'user' ? 'text-right' : '')}>
                        <div className={clsx(
                            "inline-block rounded-lg px-2.5 py-1.5 text-[11px] leading-relaxed",
                            msg.role === 'user'
                                ? 'bg-accent/20 text-gray-100 rounded-tr-sm'
                                : 'bg-surface border border-surface-muted text-gray-300 rounded-tl-sm'
                        )}>
                            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                        </div>
                        {msg.reason && (
                            <div className={clsx("mt-0.5", msg.role === 'user' ? 'text-right' : 'text-left')}>
                                <button
                                    onClick={() => toggleReason(msg.id)}
                                    className="inline-flex items-center gap-0.5 text-[9px] uppercase tracking-wider text-text-dim hover:text-accent transition-colors"
                                >
                                    {expandedReason[msg.id] ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                                    Why this answer
                                </button>
                                {expandedReason[msg.id] && (
                                    <div className="mt-1 rounded-lg border border-surface-muted bg-black/40 p-1.5 text-[10px] leading-relaxed text-text-dim text-left">
                                        {msg.reason}
                                        {msg.rewritten_query && (
                                            <div className="mt-0.5 pt-0.5 border-t border-surface-muted/50">
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
                <div className="flex gap-2">
                    <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/5 text-accent">
                        <Bot size={9} />
                    </div>
                    <div className="inline-flex items-center gap-1.5 rounded-lg bg-surface border border-surface-muted px-2 py-1.5">
                        <Loader2 size={10} className="animate-spin text-accent/70" />
                        <span className="text-[10px] text-text-dim">Thinking...</span>
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
            <div className="px-3 py-2 border-b border-surface-muted flex items-center justify-between shrink-0">
                <span className="text-xs font-semibold text-text-dim uppercase tracking-wider flex items-center gap-1.5">
                    <MessageSquare size={12} /> Ask
                </span>
                <div className="flex items-center gap-1">
                    <span className="text-[9px] text-text-dim">{stats.nodes || 0} nodes</span>
                </div>
            </div>

            {/* Input area */}
            <div className="p-2 shrink-0">
                <div className="relative">
                    <textarea
                        className="w-full h-16 bg-surface border border-surface-muted rounded-lg p-2 text-[11px] text-white placeholder-text-dim resize-none outline-none focus:border-accent/40 focus:ring-1 focus:ring-accent/20 transition-all"
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
                        className="absolute bottom-2 right-2 w-6 h-6 rounded-md bg-accent/20 text-accent flex items-center justify-center hover:bg-accent/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <Send size={11} />
                    </button>
                </div>
            </div>

            {/* Suggestions when empty */}
            {messages.length === 0 && (
                <div className="px-2 pb-1 shrink-0">
                    <div className="text-[9px] font-semibold text-text-dim uppercase tracking-wider mb-1.5 px-1">Try asking</div>
                    <div className="space-y-1">
                        {suggestions.map(s => (
                            <button
                                key={s}
                                className="suggestion-chip text-left w-full text-[10px]"
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
