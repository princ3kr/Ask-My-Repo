import React, { useState, useEffect, useRef } from 'react';
import { GitBranch, Send, Network, Database, FileCode, Cpu, Link as LinkIcon } from 'lucide-react';

const API_URL = 'http://localhost:8000/api';

const AnimatedCounter = ({ value, duration = 2000 }) => {
    const [count, setCount] = useState(0);

    useEffect(() => {
        let startTime;
        const endValue = parseInt(value.toString().replace(/,/g, ''));
        if (isNaN(endValue)) {
            setCount(value);
            return;
        }

        const animate = (time) => {
            if (!startTime) startTime = time;
            const progress = Math.min((time - startTime) / duration, 1);
            const ease = 1 - Math.pow(1 - progress, 4);

            setCount(Math.floor(ease * endValue));

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                setCount(endValue);
            }
        };

        requestAnimationFrame(animate);
    }, [value, duration]);

    return <span>{typeof count === 'number' ? count.toLocaleString() : count}</span>;
};

const App = () => {
    const [repoUrl, setRepoUrl] = useState('');
    const [isParsing, setIsParsing] = useState(false);
    const [isParsed, setIsParsed] = useState(false);
    const [filesCount, setFilesCount] = useState(0);
    const [messages, setMessages] = useState([
        {
            id: 1,
            role: 'assistant',
            content: 'I am R2G Mapper. Connect a repository graph to begin.'
        }
    ]);
    const [input, setInput] = useState('');
    const [isTyping, setIsTyping] = useState(false);
    const messagesEndRef = useRef(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isTyping]);

    const handleParse = async () => {
        if (!repoUrl) return;
        setIsParsing(true);
        try {
            const res = await fetch(`${API_URL}/parse`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: repoUrl })
            });
            const data = await res.json();
            if (data.status === 'success') {
                setIsParsed(true);
                setFilesCount(data.files_count || 0);
            } else {
                alert("Error parsing repository: " + data.detail);
            }
        } catch (e) {
            alert("Failed to connect to backend: " + e.message);
        } finally {
            setIsParsing(false);
        }
    };

    const handleSend = async () => {
        if (!input.trim() || !isParsed) return;

        const newUserMsg = { id: Date.now(), role: 'user', content: input };
        setMessages(prev => [...prev, newUserMsg]);
        setInput('');
        setIsTyping(true);

        try {
            const res = await fetch(`${API_URL}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: repoUrl, query: input, history: [] })
            });
            const data = await res.json();

            const newAsstMsgId = Date.now() + 1;
            setMessages(prev => [...prev, {
                id: newAsstMsgId,
                role: 'assistant',
                content: data.answer || "No response.",
                reason: data.reason,
                decision: data.decision
            }]);
        } catch (e) {
            setMessages(prev => [...prev, {
                id: Date.now() + 1,
                role: 'assistant',
                content: "Error communicating with the backend: " + e.message
            }]);
        } finally {
            setIsTyping(false);
        }
    };

    return (
        <div className="flex h-screen font-sans antialiased relative z-10 text-slate-200">
            <div className="grain"></div>

            {/* LEFT SIDEBAR */}
            <div className="w-[280px] glass-panel flex flex-col fade-in-up relative z-20">
                <div className="p-6 border-b border-white/5">
                    <h2 className="text-[10px] uppercase tracking-[0.25em] text-white/40 mb-5 font-mono font-semibold">Repository Target</h2>

                    <div className="space-y-4">
                        <div className="relative group">
                            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-white/30 group-focus-within:text-accent transition-colors">
                                <GitBranch size={16} />
                            </div>
                            <input
                                type="text"
                                placeholder="github.com/org/repo"
                                className="w-full bg-black/40 border border-white/10 rounded-md py-2.5 pl-10 pr-3 text-sm text-white placeholder-white/20 focus:outline-none focus:border-accent/40 focus:bg-black/60 transition-all font-mono"
                                value={repoUrl}
                                onChange={(e) => setRepoUrl(e.target.value)}
                                disabled={isParsing || isParsed}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleParse();
                                }}
                            />
                        </div>
                        <button
                            onClick={handleParse}
                            disabled={!repoUrl || isParsing || isParsed}
                            className={`w-full py-2.5 px-4 rounded-md text-sm font-medium transition-all flex items-center justify-center space-x-2 ${isParsed
                                    ? 'bg-white/5 text-accent border border-accent/20 cursor-default'
                                    : isParsing
                                        ? 'bg-accent/20 text-accent cursor-wait'
                                        : 'bg-accent text-background hover:bg-accent/90 hover:shadow-[0_0_15px_rgba(0,212,255,0.4)] disabled:opacity-30 disabled:hover:shadow-none disabled:cursor-not-allowed'
                                }`}
                        >
                            {isParsing ? (
                                <>
                                    <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-accent" viewBox="0 0 24 24">
                                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"></circle>
                                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                    </svg>
                                    Graph building...
                                </>
                            ) : isParsed ? (
                                <span className="flex items-center"><LinkIcon size={14} /><span className="ml-2">Connected</span></span>
                            ) : (
                                'Parse Repo'
                            )}
                        </button>
                    </div>
                </div>

                {/* Metadata Card */}
                <div className="p-6 flex-1 overflow-y-auto">
                    {(isParsing || isParsed) && (
                        <div className={`bg-black/30 rounded-lg p-5 space-y-5 transition-all duration-700 ${isParsing ? 'animate-pulse shimmer-bg border border-accent/10' : 'border border-white/5'}`}>
                            <div className="flex items-center space-x-3 pb-4 border-b border-white/5">
                                <div className="h-9 w-9 rounded-full bg-gradient-to-br from-accent/20 to-transparent flex items-center justify-center text-accent shadow-[inset_0_0_10px_rgba(0,212,255,0.2)]">
                                    <Network size={16} />
                                </div>
                                <div className="overflow-hidden">
                                    <div className="text-[10px] text-white/40 font-mono tracking-wider uppercase mb-0.5">Active Context</div>
                                    <div className="text-sm font-semibold truncate text-white/90">{repoUrl.split('/').pop() || 'Unknown Repo'}</div>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div className="flex justify-between items-center text-xs font-mono">
                                    <div className="flex items-center text-white/40 space-x-2"><FileCode size={14} /><span>Files</span></div>
                                    <div className="text-white/80">{isParsed ? <AnimatedCounter value={filesCount} /> : '-'}</div>
                                </div>
                                <div className="flex justify-between items-center text-xs font-mono">
                                    <div className="flex items-center text-white/40 space-x-2"><Database size={14} /><span>Nodes</span></div>
                                    <div className="text-accent font-medium">{isParsed ? <AnimatedCounter value={filesCount * 12} /> : '-'}</div>
                                </div>
                                <div className="flex justify-between items-center text-xs font-mono">
                                    <div className="flex items-center text-white/40 space-x-2"><Network size={14} /><span>Edges</span></div>
                                    <div className="text-accent/70 font-medium">{isParsed ? <AnimatedCounter value={filesCount * 45} /> : '-'}</div>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Decorative Mini-graph */}
                <div className="p-6 mt-auto opacity-30 flex justify-center pb-8">
                    <svg width="120" height="80" viewBox="0 0 120 80" className="animate-pulse-slow">
                        <line x1="30" y1="40" x2="60" y2="20" stroke="rgba(0,212,255,0.4)" strokeWidth="1" />
                        <line x1="60" y1="20" x2="90" y2="40" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
                        <line x1="60" y1="20" x2="60" y2="60" stroke="rgba(0,212,255,0.2)" strokeWidth="1" />
                        <line x1="30" y1="40" x2="60" y2="60" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
                        <line x1="90" y1="40" x2="60" y2="60" stroke="rgba(255,255,255,0.1)" strokeWidth="1" />

                        <circle cx="30" cy="40" r="3" fill="#00D4FF" />
                        <circle cx="60" cy="20" r="4" fill="#00D4FF" className="animate-breathe" style={{ transformOrigin: '60px 20px' }} />
                        <circle cx="90" cy="40" r="3" fill="rgba(255,255,255,0.5)" />
                        <circle cx="60" cy="60" r="3" fill="rgba(255,255,255,0.3)" />
                    </svg>
                </div>
            </div>

            {/* MAIN AREA */}
            <div className="flex-1 flex flex-col relative overflow-hidden bg-background">
                <div className="particle-bg"></div>

                {/* Top Bar */}
                <div className="h-16 border-b border-white/5 bg-background/60 backdrop-blur-xl flex items-center justify-between px-8 z-20 fade-in-up delay-100">
                    <div className="flex items-center space-x-5">
                        <div className="font-mono font-bold text-[15px] tracking-widest text-white flex items-center">
                            <span className="text-accent mr-3 opacity-80">✦</span> R2G_MAPPER
                        </div>
                        <div className="h-5 w-px bg-white/10"></div>
                        <div className="text-xs font-mono text-white/60 bg-white/[0.03] border border-white/5 px-3 py-1.5 rounded-full shadow-inner">
                            {isParsed ? (repoUrl.split('/').pop() || 'connected') : 'awaiting_connection'}
                        </div>
                    </div>

                    <div className="flex items-center space-x-3 font-mono text-[11px] uppercase tracking-wider text-white/40 bg-black/20 px-3 py-1.5 rounded-full border border-white/5">
                        <div className={`w-2 h-2 rounded-full ${isParsed ? 'bg-accent animate-breathe' : 'bg-white/20'}`}></div>
                        <span>{isParsed ? 'Session Active' : 'Idle'}</span>
                    </div>
                </div>

                {/* Chat Messages Area */}
                <div className="flex-1 overflow-y-auto px-8 md:px-16 pt-10 pb-40 space-y-10 z-10 scroll-smooth fade-in-up delay-200">
                    <div className="max-w-4xl mx-auto space-y-10">
                        {messages.map((msg) => (
                            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                                <div className={`max-w-[85%] ${msg.role === 'user'
                                        ? 'user-msg px-6 py-4 rounded-2xl rounded-tr-sm shadow-xl'
                                        : 'glass-assistant-msg px-7 py-6 rounded-2xl rounded-tl-sm w-full'
                                    }`}>
                                    <div className={`text-[15px] leading-relaxed whitespace-pre-wrap ${msg.role === 'user' ? 'font-medium tracking-tight' : 'text-white/80 font-light tracking-wide'}`}>
                                        {msg.content}
                                    </div>

                                    {msg.reason && (
                                        <div className="mt-4 rounded-lg overflow-hidden code-block text-[13px] font-mono relative shadow-2xl">
                                            <div className="bg-white/5 px-4 py-2 text-xs text-white/40 border-b border-white/5 flex items-center justify-between">
                                                <span className="flex items-center"><Cpu size={14} className="mr-2" /> Agent Logic</span>
                                                <span className="uppercase text-[10px] tracking-wider text-accent">{msg.decision}</span>
                                            </div>
                                            <div className="p-4 text-white/60">
                                                {msg.reason}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}

                        {isTyping && (
                            <div className="flex justify-start">
                                <div className="glass-assistant-msg px-6 py-5 rounded-2xl rounded-tl-sm flex items-center space-x-2">
                                    <div className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce-elastic"></div>
                                    <div className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce-elastic" style={{ animationDelay: '0.15s' }}></div>
                                    <div className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce-elastic" style={{ animationDelay: '0.3s' }}></div>
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>
                </div>

                {/* Input Area */}
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-background via-background/95 to-transparent pt-32 pb-10 px-8 z-20 fade-in-up delay-300 pointer-events-none">
                    <div className="max-w-3xl mx-auto relative pointer-events-auto">
                        <div className="glass-panel bg-black/40 rounded-2xl p-2.5 pl-6 pr-2.5 flex items-end shadow-[0_20px_40px_rgba(0,0,0,0.4)] ring-1 ring-white/10 focus-within:ring-accent/40 focus-within:ring-2 focus-within:bg-black/60 transition-all duration-300">
                            <textarea
                                className="w-full bg-transparent text-white placeholder-white/30 resize-none py-3.5 focus:outline-none max-h-40 font-medium text-[15px] leading-relaxed"
                                rows="1"
                                placeholder={isParsed ? "Query the repository graph..." : "Connect a repository to ask questions..."}
                                value={input}
                                disabled={!isParsed || isTyping}
                                onChange={(e) => {
                                    setInput(e.target.value);
                                    e.target.style.height = 'auto';
                                    e.target.style.height = (e.target.scrollHeight) + 'px';
                                }}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && !e.shiftKey) {
                                        e.preventDefault();
                                        handleSend();
                                    }
                                }}
                            />
                            <button
                                className={`p-3.5 rounded-xl flex-shrink-0 transition-all duration-300 ml-2 ${input.trim() && isParsed && !isTyping
                                        ? 'bg-accent text-background hover:bg-accent/90 shadow-[0_0_15px_rgba(0,212,255,0.3)] hover:shadow-[0_0_20px_rgba(0,212,255,0.5)] transform hover:scale-105'
                                        : 'bg-white/5 text-white/20 cursor-not-allowed'
                                    }`}
                                onClick={handleSend}
                                disabled={!input.trim() || !isParsed || isTyping}
                            >
                                <Send size={18} />
                            </button>
                        </div>
                        <div className="flex justify-between items-center mt-4 px-3 font-mono text-[10px] text-white/30 uppercase tracking-[0.15em]">
                            <div className="flex space-x-6">
                                <span className="flex items-center"><span className="w-1 h-1 bg-white/20 rounded-full mr-2"></span>Model: GPT-4-R2G</span>
                                <span className="flex items-center"><span className="w-1 h-1 bg-white/20 rounded-full mr-2"></span>Reranker: Cross-Encoder</span>
                            </div>
                            <div className="flex items-center text-accent/60">
                                <div className="w-1.5 h-1.5 bg-accent rounded-full mr-2 shadow-[0_0_5px_rgba(0,212,255,0.5)]"></div>
                                12.4k Context
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default App;
