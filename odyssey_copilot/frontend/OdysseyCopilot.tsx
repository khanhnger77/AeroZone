import React, { useState, useRef, useEffect } from 'react';
import { 
  Terminal, 
  Send, 
  ChevronDown, 
  ChevronUp, 
  Radio, 
  Compass, 
  AlertTriangle, 
  Crosshair, 
  Route, 
  Sparkles,
  RefreshCw
} from 'lucide-react';

export interface CopilotAction {
  action_type: 'INSPECT_TERRAIN' | 'PLAN_ROUTE' | 'FOCUS_SAMPLE' | 'NAVIGATE_WAYPOINT' | string;
  payload: Record<string, any>;
  description: string;
}

export interface OdysseyCopilotProps {
  onAction?: (action: CopilotAction) => void;
  currentLocation?: { lon: number; lat: number };
  apiUrl?: string;
  apiBaseUrl?: string;
}

interface Message {
  role: 'user' | 'model';
  content: string;
  action?: CopilotAction;
  tools?: string[];
  timestamp: string;
}

export const OdysseyCopilot: React.FC<OdysseyCopilotProps> = ({
  onAction,
  currentLocation,
  apiUrl,
  apiBaseUrl
}) => {
  const endpoint = (apiBaseUrl || apiUrl || '/api/copilot/chat');
  const [isOpen, setIsOpen] = useState<boolean>(true);
  const [input, setInput] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'model',
      content: 'ODYSSEY CAPCOM online. Standing by for Jezero Crater EVA queries, terrain inspection, and route navigation.',
      timestamp: '00:00:00'
    }
  ]);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (isOpen) {
      scrollToBottom();
    }
  }, [messages, isOpen]);

  const quickPrompts = [
    { label: 'Sample #25 Cheyava Falls', prompt: 'Analyze Sample #25 Cheyava Falls and focus on its coordinates.' },
    { label: 'Inspect Terrain Slope', prompt: 'Inspect terrain elevation and slope hazard at my current waypoint.' },
    { label: 'Plan Route to Delta Front', prompt: 'Plan safe traverse route to the Delta Front scarp.' },
    { label: 'Séítah Samples', prompt: 'What samples were collected at Séítah?' }
  ];

  const handleSend = async (messageText?: string) => {
    const query = (messageText || input).trim();
    if (!query || loading) return;

    setError(null);
    setInput('');
    const now = new Date().toTimeString().split(' ')[0];
    
    // Add user message to history
    const userMsg: Message = { role: 'user', content: query, timestamp: now };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const historyPayload = messages.map(m => ({ role: m.role, content: m.content }));
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: query,
          history: historyPayload,
          current_location: currentLocation
        })
      });

      if (!res.ok) {
        throw new Error(`Capcom telemetry link returned status ${res.status}`);
      }

      const data = await res.json();
      const modelMsg: Message = {
        role: 'model',
        content: data.reply || 'Roger, Capcom standing by.',
        action: data.action || undefined,
        tools: data.tools_called || undefined,
        timestamp: new Date().toTimeString().split(' ')[0]
      };

      setMessages(prev => [...prev, modelMsg]);

      // Automatically emit typed map action
      if (data.action && onAction) {
        onAction(data.action);
      }
    } catch (err: any) {
      setError(err.message || 'Telemetry link failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 font-mono text-xs select-none">
      {/* Collapsed Floating Dock Pill */}
      {!isOpen ? (
        <button
          onClick={() => setIsOpen(true)}
          className="flex items-center gap-2.5 px-4 py-2.5 rounded-full bg-slate-950/90 text-orange-400 border border-orange-500/40 shadow-xl shadow-orange-950/30 backdrop-blur-md hover:border-orange-400 hover:text-orange-300 transition-all cursor-pointer"
        >
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          <Radio size={14} className="text-orange-500 animate-pulse" />
          <span className="tracking-wider uppercase font-semibold text-[11px]">EVA COPILOT // CAPCOM</span>
          <ChevronUp size={14} />
        </button>
      ) : (
        /* Expanded NASA Mission Control Window */
        <div className="w-96 md:w-[420px] h-[520px] flex flex-col rounded-xl bg-slate-950/95 border border-orange-500/30 shadow-2xl shadow-orange-950/40 backdrop-blur-lg overflow-hidden transition-all">
          
          {/* Header */}
          <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-orange-500/20 bg-slate-900/80">
            <div className="flex items-center gap-2">
              <Terminal size={14} className="text-orange-500" />
              <span className="text-orange-400 font-bold uppercase tracking-wider text-[11px]">
                ODYSSEY COPILOT // EVA OPS
              </span>
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold bg-emerald-950 text-emerald-400 border border-emerald-500/30">
                LIVE
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button 
                onClick={() => setMessages([{ role: 'model', content: 'ODYSSEY CAPCOM reset. Standing by for EVA operations.', timestamp: '00:00:00' }])}
                title="Reset History"
                className="p-1 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
              >
                <RefreshCw size={13} />
              </button>
              <button 
                onClick={() => setIsOpen(false)}
                title="Collapse Dock"
                className="p-1 text-slate-400 hover:text-orange-400 transition-colors cursor-pointer"
              >
                <ChevronDown size={15} />
              </button>
            </div>
          </div>

          {/* Current Waypoint Coordinates Bar */}
          {currentLocation && (
            <div className="px-3.5 py-1 bg-slate-900/40 border-b border-slate-800/80 flex items-center justify-between text-[10px] text-slate-400">
              <div className="flex items-center gap-1.5">
                <Crosshair size={10} className="text-orange-400" />
                <span>CURRENT EVA WAYPOINT:</span>
              </div>
              <span className="text-slate-300 font-semibold">
                {currentLocation.lon.toFixed(4)}°E, {currentLocation.lat.toFixed(4)}°N
              </span>
            </div>
          )}

          {/* Quick Prompt Chips */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-900/30 border-b border-orange-500/10 overflow-x-auto no-scrollbar">
            {quickPrompts.map((chip, idx) => (
              <button
                key={idx}
                disabled={loading}
                onClick={() => handleSend(chip.prompt)}
                className="shrink-0 px-2 py-0.5 rounded text-[10px] bg-slate-800/80 text-slate-300 border border-slate-700 hover:border-orange-500/50 hover:text-orange-300 hover:bg-slate-800 transition-all cursor-pointer disabled:opacity-50"
              >
                {chip.label}
              </button>
            ))}
          </div>

          {/* Message List */}
          <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
            {messages.map((m, idx) => {
              const isUser = m.role === 'user';
              return (
                <div key={idx} className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
                  <div className="flex items-center gap-1.5 text-[9px] text-slate-500 mb-1 px-1">
                    <span>{isUser ? 'ASTRONAUT' : 'CAPCOM'}</span>
                    <span>·</span>
                    <span>{m.timestamp}</span>
                    {m.tools && m.tools.length > 0 && (
                      <span className="text-orange-400/80">[{m.tools.join(', ')}]</span>
                    )}
                  </div>
                  <div
                    className={`max-w-[85%] rounded-lg p-2.5 leading-relaxed text-xs ${
                      isUser
                        ? 'bg-orange-600 text-white rounded-tr-none'
                        : 'bg-slate-900 text-slate-200 border border-slate-800 rounded-tl-none shadow'
                    }`}
                  >
                    {m.content}

                    {/* Action Trigger Card */}
                    {m.action && (
                      <div className="mt-2 pt-2 border-t border-slate-800 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 text-[10px] text-orange-400 truncate">
                          {m.action.action_type === 'PLAN_ROUTE' ? (
                            <Route size={12} className="shrink-0" />
                          ) : m.action.action_type === 'FOCUS_SAMPLE' ? (
                            <Sparkles size={12} className="shrink-0" />
                          ) : (
                            <Compass size={12} className="shrink-0" />
                          )}
                          <span className="truncate">{m.action.description}</span>
                        </div>
                        {onAction && (
                          <button
                            onClick={() => onAction(m.action!)}
                            className="shrink-0 px-1.5 py-0.5 rounded text-[9px] bg-orange-500/20 text-orange-300 border border-orange-500/30 hover:bg-orange-500/30 cursor-pointer"
                          >
                            SYNC
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {loading && (
              <div className="flex items-center gap-2 text-slate-400 text-xs py-1 px-2">
                <Radio size={12} className="text-orange-500 animate-spin" />
                <span className="animate-pulse">CAPCOM COMPUTING TRAVERSE METRICS...</span>
              </div>
            )}

            {error && (
              <div className="flex items-center gap-2 p-2 rounded bg-red-950/40 border border-red-500/40 text-red-300 text-xs">
                <AlertTriangle size={14} className="shrink-0 text-red-400" />
                <span>{error}</span>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Footer */}
          <form
            onSubmit={(e) => { e.preventDefault(); handleSend(); }}
            className="p-2.5 border-t border-orange-500/20 bg-slate-900/60 flex items-center gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Query Capcom or request traverse..."
              disabled={loading}
              className="flex-1 bg-slate-950 text-slate-200 border border-slate-700 rounded-md px-3 py-2 text-xs focus:outline-none focus:border-orange-500 placeholder-slate-500"
            />
            <button
              type="submit"
              disabled={!input.trim() || loading}
              className="p-2 bg-orange-600 hover:bg-orange-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-md transition-colors cursor-pointer"
            >
              <Send size={14} />
            </button>
          </form>

        </div>
      )}
    </div>
  );
};

export default OdysseyCopilot;
