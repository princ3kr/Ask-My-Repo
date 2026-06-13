import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Sun, Moon, Book, User, GitBranch, Menu } from 'lucide-react';
import logo from '../../assets/Ask-My-repo.png';

const MIN_LEFT = 180;
const MAX_LEFT = 500;
const MIN_RIGHT = 200;
const MAX_RIGHT = 500;
const DEFAULT_LEFT = 260;
const DEFAULT_RIGHT = 340;

export default function DashboardLayout({
  children,
  leftSidebar,
  rightSidebar,
  repoName,
  topBarExtra,
  theme = 'dark',
  onToggleTheme,
  onLeftWidthChange,
  onRightWidthChange,
}) {
  const [leftWidth, setLeftWidth] = useState(DEFAULT_LEFT);
  const [rightWidth, setRightWidth] = useState(DEFAULT_RIGHT);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);

  const dragging = useRef(null);
  const startX = useRef(0);
  const startSize = useRef(0);

  const handleMouseDown = useCallback((side, e) => {
    e.preventDefault();
    dragging.current = side;
    startX.current = e.clientX;
    startSize.current = side === 'left' ? leftWidth : rightWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [leftWidth, rightWidth]);

  useEffect(() => {
    const handleMove = (e) => {
      if (!dragging.current) return;
      const delta = e.clientX - startX.current;
      if (dragging.current === 'left') {
        const next = Math.max(MIN_LEFT, Math.min(MAX_LEFT, startSize.current + delta));
        setLeftWidth(next);
        onLeftWidthChange?.(next);
      } else {
        const next = Math.max(MIN_RIGHT, Math.min(MAX_RIGHT, startSize.current - delta));
        setRightWidth(next);
        onRightWidthChange?.(next);
      }
    };
    const handleUp = () => {
      dragging.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [onLeftWidthChange, onRightWidthChange]);

  return (
    <div className={`flex flex-col h-screen bg-background font-sans overflow-hidden ${theme === 'light' ? 'light-theme text-gray-800' : 'text-gray-300'}`}>
      {/* Top Header */}
      <header className="h-12 flex items-center justify-between px-3 border-b border-surface-muted bg-panel shrink-0 z-20">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setLeftOpen(p => !p)}
            className="text-text-dim hover:text-white p-1 rounded hover:bg-surface-muted transition-colors"
            title="Toggle sidebar"
          >
            <Menu size={16} />
          </button>
          <div className="flex items-center gap-2">
            <img src={logo} alt="Ask My Repo" className="h-5 w-auto" />
            <span className="font-semibold text-white tracking-wide text-sm">Ask My Repo</span>
          </div>
          {repoName && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-surface border border-surface-muted rounded text-xs">
              <GitBranch size={12} className="text-text-dim" />
              <span className="text-gray-300">{repoName}</span>
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 ml-1 shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
              <span className="text-green-400 text-[10px] font-medium">Indexed</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1">
          {topBarExtra}
          <button
            onClick={onToggleTheme}
            className="text-text-dim hover:text-white p-1.5 rounded hover:bg-surface-muted transition-colors"
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button className="text-text-dim hover:text-white p-1.5 rounded hover:bg-surface-muted transition-colors"><Book size={15} /></button>
          <div className="w-7 h-7 rounded-full bg-accent/20 text-accent flex items-center justify-center font-semibold text-xs border border-accent/30 ml-1">
            U
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Left Sidebar */}
        <div
          className="flex flex-col border-r border-surface-muted bg-panel overflow-hidden shrink-0 transition-all duration-200"
          style={{ width: leftOpen ? leftWidth : 0, minWidth: leftOpen ? MIN_LEFT : 0, opacity: leftOpen ? 1 : 0 }}
        >
          <div className="flex-1 overflow-hidden flex flex-col">
            {leftSidebar}
          </div>
        </div>

        {/* Left resize handle */}
        {leftOpen && (
          <div
            className="w-1 cursor-col-resize hover:bg-accent/40 bg-transparent transition-colors shrink-0 relative z-10"
            onMouseDown={(e) => handleMouseDown('left', e)}
          />
        )}

        {/* Center Canvas */}
        <main className="flex-1 flex flex-col relative bg-background min-w-0">
          {children}
        </main>

        {/* Right resize handle */}
        {rightOpen && (
          <div
            className="w-1 cursor-col-resize hover:bg-accent/40 bg-transparent transition-colors shrink-0 relative z-10"
            onMouseDown={(e) => handleMouseDown('right', e)}
          />
        )}

        {/* Right Sidebar */}
        <div
          className="flex flex-col border-l border-surface-muted bg-panel overflow-hidden shrink-0 transition-all duration-200"
          style={{ width: rightOpen ? rightWidth : 0, minWidth: rightOpen ? MIN_RIGHT : 0, opacity: rightOpen ? 1 : 0 }}
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-surface-muted">
            <span className="text-xs font-semibold text-text-dim uppercase tracking-wider">Details</span>
            <button
              onClick={() => setRightOpen(p => !p)}
              className="text-text-dim hover:text-white p-1 rounded hover:bg-surface-muted transition-colors"
              title="Toggle panel"
            >
              <Menu size={14} />
            </button>
          </div>
          <div className="flex-1 overflow-hidden flex flex-col">
            {rightSidebar}
          </div>
        </div>

        {/* Toggle right if closed */}
        {!rightOpen && (
          <button
            onClick={() => setRightOpen(true)}
            className="absolute right-2 top-2 z-10 text-text-dim hover:text-white p-1.5 rounded bg-panel/80 border border-surface-muted hover:bg-surface-muted transition-colors text-xs"
            title="Open details"
          >
            Details
          </button>
        )}
      </div>

      {/* Bottom Status Bar */}
      <div className="h-6 flex items-center justify-between px-3 border-t border-surface-muted bg-panel text-[10px] text-text-dim shrink-0">
        <div className="flex items-center gap-3">
          {repoName && <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-green-500" /> {repoName}</span>}
        </div>
        <div className="flex items-center gap-3">
          <span>React Flow • Neo4j • LangGraph</span>
        </div>
      </div>
    </div>
  );
}
