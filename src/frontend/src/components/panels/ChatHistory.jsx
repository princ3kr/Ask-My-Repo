import React from 'react';
import { Bot, User, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import clsx from 'clsx';

export default function ChatHistory({ messages, isTyping, expandedReason, toggleReason, messagesEndRef, showSuggestions, handleSend }) {
    if (!messages || messages.length === 0) {
        return (
            <div className="flex-1 flex items-center justify-center text-text-dim text-sm p-4 text-center">
                Ask a question to get started
            </div>
        );
    }

    return (
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {messages.filter(m => !m.isStatus).map((msg) => (
                <div
                    key={msg.id}
                    className={clsx("flex gap-2", msg.role === 'user' ? 'flex-row-reverse' : '')}
                >
                    <div className={clsx(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                        msg.role === 'user'
                            ? 'bg-white text-background'
                            : 'bg-white/5 text-accent'
                    )}>
                        {msg.role === 'user' ? <User size={11} /> : <Bot size={11} />}
                    </div>

                    <div className={clsx("min-w-0 max-w-[88%]", msg.role === 'user' ? 'text-right' : '')}>
                        <div className={clsx(
                            "inline-block rounded-xl px-3 py-2 text-sm leading-relaxed",
                            msg.role === 'user'
                                ? 'bg-accent/20 text-gray-100 rounded-tr-sm'
                                : 'bg-surface border border-surface-muted text-gray-300 rounded-tl-sm'
                        )}>
                            <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                        </div>

                        {msg.reason && (
                            <div className={clsx("mt-1", msg.role === 'user' ? 'text-right' : 'text-left')}>
                                <button
                                    onClick={() => toggleReason(msg.id)}
                                    className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-dim hover:text-accent transition-colors"
                                >
                                    {expandedReason[msg.id] ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                    Why this answer
                                </button>
                                {expandedReason[msg.id] && (
                                    <div className="mt-1 rounded-lg border border-surface-muted bg-black/40 p-2 text-[11px] leading-relaxed text-text-dim text-left">
                                        {msg.reason}
                                        {msg.rewritten_query && (
                                            <div className="mt-1 pt-1 border-t border-surface-muted/50">
                                                <span className="text-accent/70">Rewritten query:</span> {msg.rewritten_query}
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
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/5 text-accent">
                        <Bot size={11} />
                    </div>
                    <div className="inline-flex items-center gap-2 rounded-xl bg-surface border border-surface-muted px-3 py-2">
                        <Loader2 size={14} className="animate-spin text-accent/70" />
                        <span className="text-xs text-text-dim">Looking through your codebase…</span>
                    </div>
                </div>
            )}

            <div ref={messagesEndRef} />
        </div>
    );
}
