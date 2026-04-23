import React, { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';

const API = '';  // same origin in prod; CRA proxy handles /api in dev

// ── helpers ────────────────────────────────────────────────────────────────

function fmtTime(ns) {
  if (!ns) return '—';
  return new Date(ns / 1_000_000).toLocaleTimeString();
}

function StatusBadge({ status }) {
  return (
    <span className={`badge ${status === 'ERROR' ? 'badge-error' : 'badge-ok'}`}>
      {status}
    </span>
  );
}

function SourceBadge({ source }) {
  const label = source === 'claude-code' ? 'claude code' : 'chat';
  return (
    <span className={`badge ${source === 'claude-code' ? 'badge-source-cc' : 'badge-source-chat'}`}>
      {label}
    </span>
  );
}

// ── Metrics bar ────────────────────────────────────────────────────────────

function MetricsBar({ metrics }) {
  const cards = [
    { label: 'Total Calls',    value: metrics.total_calls ?? 0 },
    { label: 'Input Tokens',   value: (metrics.total_input_tokens ?? 0).toLocaleString() },
    { label: 'Output Tokens',  value: (metrics.total_output_tokens ?? 0).toLocaleString() },
    { label: 'Avg Latency',    value: `${metrics.avg_latency_ms ?? 0} ms` },
    { label: 'Error Rate',     value: `${metrics.error_rate ?? 0}%` },
  ];
  return (
    <div className="metrics-bar">
      {cards.map(c => (
        <div className="metric-card" key={c.label}>
          <div className="metric-value">{c.value}</div>
          <div className="metric-label">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ── Trace list ─────────────────────────────────────────────────────────────

function TraceList({ traces, selectedId, onSelect }) {
  return (
    <div className="trace-list">
      <div className="panel-header">
        Traces <span className="count">{traces.length}</span>
      </div>
      {traces.length === 0 && (
        <div className="empty">No traces yet. Send a message below.</div>
      )}
      {traces.map(t => (
        <div
          key={t.trace_id}
          className={`trace-row${selectedId === t.trace_id ? ' selected' : ''}`}
          onClick={() => onSelect(t.trace_id)}
        >
          <div className="trace-row-top">
            <span className="trace-name">{t.name}</span>
            <StatusBadge status={t.status} />
          </div>
          <div className="trace-row-meta">
            <span>{fmtTime(t.start_time)}</span>
            <span>{t.duration_ms} ms</span>
            {t.input_tokens != null && (
              <span>↑{t.input_tokens} ↓{t.output_tokens}</span>
            )}
            {t.model && <span className="model-tag">{t.model}</span>}
            <SourceBadge source={t.source} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Span waterfall ─────────────────────────────────────────────────────────

function SpanWaterfall({ spans }) {
  if (!spans || spans.length === 0) return null;
  const minStart = Math.min(...spans.map(s => s.start_time_ns));
  const maxEnd   = Math.max(...spans.map(s => s.end_time_ns));
  const range    = maxEnd - minStart || 1;

  return (
    <div className="waterfall">
      {spans.map(s => {
        const left  = ((s.start_time_ns - minStart) / range) * 100;
        const width = Math.max(((s.end_time_ns - s.start_time_ns) / range) * 100, 0.5);
        return (
          <div className="wf-row" key={s.span_id}>
            <div className="wf-label" title={s.name}>{s.name}</div>
            <div className="wf-bar-container">
              <div
                className={`wf-bar${s.status === 'ERROR' ? ' wf-error' : ''}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>
            <div className="wf-dur">{s.duration_ms} ms</div>
          </div>
        );
      })}
    </div>
  );
}

// ── Attribute table ────────────────────────────────────────────────────────

function AttrTable({ attrs }) {
  const entries = Object.entries(attrs || {});
  if (entries.length === 0) return null;
  return (
    <table className="attr-table">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k}>
            <td className="attr-key">{k}</td>
            <td className="attr-val">{Array.isArray(v) ? v.join(', ') : String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Span detail ────────────────────────────────────────────────────────────

function SpanDetail({ trace }) {
  const [openSpan, setOpenSpan] = useState(null);

  if (!trace) {
    return <div className="panel-placeholder">← Select a trace to inspect its spans</div>;
  }

  const { spans } = trace;
  return (
    <div className="span-detail">
      <div className="panel-header">
        Trace&nbsp;<code className="tid">{trace.trace_id.slice(0, 16)}…</code>
        &nbsp;<span className="count">{spans.length} spans</span>
      </div>

      <SpanWaterfall spans={spans} />

      <div className="span-list">
        {spans.map(s => (
          <div key={s.span_id} className="span-item">
            <div
              className="span-item-header"
              onClick={() => setOpenSpan(openSpan === s.span_id ? null : s.span_id)}
            >
              <span className="toggle">{openSpan === s.span_id ? '▼' : '▶'}</span>
              <span className="span-name">{s.name}</span>
              <StatusBadge status={s.status} />
              <span className="span-dur">{s.duration_ms} ms</span>
            </div>

            {openSpan === s.span_id && (
              <div className="span-body">
                <AttrTable attrs={s.attributes} />
                {s.events.length > 0 && (
                  <>
                    <div className="section-title">Events</div>
                    {s.events.map((ev, i) => (
                      <div key={i} className="event-block">
                        <div className="event-name">{ev.name}</div>
                        {ev.attributes['gen_ai.prompt'] && (
                          <pre className="prompt-text">{ev.attributes['gen_ai.prompt']}</pre>
                        )}
                        {ev.attributes['gen_ai.completion'] && (
                          <pre className="completion-text">{ev.attributes['gen_ai.completion']}</pre>
                        )}
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Chat panel ─────────────────────────────────────────────────────────────

function ChatPanel({ onSent }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput]       = useState('');
  const [model, setModel]       = useState('claude-opus-4-5');
  const [system, setSystem]     = useState('');
  const [loading, setLoading]   = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = async () => {
    if (!input.trim() || loading) return;
    const userMsg = { role: 'user', content: input.trim() };
    const nextMsgs = [...messages, userMsg];
    setMessages(nextMsgs);
    setInput('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: nextMsgs, model, system: system || undefined }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setMessages(prev => [...prev, { role: 'assistant', content: data.content }]);
      if (onSent) onSent();
    } catch (err) {
      setMessages(prev => [...prev, { role: 'system', content: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="chat-panel">
      <div className="panel-header">Chat with Claude</div>

      <div className="chat-settings">
        <select value={model} onChange={e => setModel(e.target.value)}>
          <option value="claude-opus-4-5">claude-opus-4-5</option>
          <option value="claude-sonnet-4-5">claude-sonnet-4-5</option>
          <option value="claude-haiku-3-5">claude-haiku-3-5</option>
        </select>
        <input
          placeholder="System prompt (optional)"
          value={system}
          onChange={e => setSystem(e.target.value)}
        />
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty">Type a message to generate OTEL traces.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <span className="msg-role">{m.role}</span>
            <span className="msg-content">{m.content}</span>
          </div>
        ))}
        {loading && (
          <div className="chat-msg chat-msg-assistant">
            <span className="msg-role">assistant</span>
            <span className="msg-content dots">●●●</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input">
        <textarea
          rows={3}
          placeholder="Send a message… (Enter to send, Shift+Enter for newline)"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <button onClick={send} disabled={loading || !input.trim()}>Send</button>
      </div>
    </div>
  );
}

// ── App ────────────────────────────────────────────────────────────────────

export default function App() {
  const [traces,       setTraces]       = useState([]);
  const [metrics,      setMetrics]      = useState({});
  const [selectedId,   setSelectedId]   = useState(null);
  const [traceDetail,  setTraceDetail]  = useState(null);
  const [sseStatus,    setSseStatus]    = useState('connecting');

  const loadTrace = useCallback(async (id) => {
    const res = await fetch(`${API}/api/traces/${id}`);
    if (res.ok) setTraceDetail(await res.json());
  }, []);

  const refreshAll = useCallback(async () => {
    const [tr, mt] = await Promise.all([
      fetch(`${API}/api/traces`).then(r => r.json()),
      fetch(`${API}/api/metrics`).then(r => r.json()),
    ]);
    setTraces(tr);
    setMetrics(mt);
  }, []);

  // live updates via SSE
  useEffect(() => {
    const es = new EventSource(`${API}/api/events`);

    es.onopen = () => setSseStatus('connected');
    es.onerror = () => setSseStatus('error');

    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'init') {
        setTraces(msg.traces);
        setMetrics(msg.metrics);
        setSseStatus('connected');
      } else if (msg.type === 'span') {
        // refresh summaries and metrics
        fetch(`${API}/api/traces`).then(r => r.json()).then(setTraces);
        fetch(`${API}/api/metrics`).then(r => r.json()).then(setMetrics);
        // refresh open trace detail
        setSelectedId(prev => {
          if (prev && msg.span.trace_id === prev) loadTrace(prev);
          return prev;
        });
      }
    };

    return () => es.close();
  }, [loadTrace]);

  const handleSelect = (id) => {
    setSelectedId(id);
    loadTrace(id);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-title">
          <span className="logo">⬡</span> Claude OTEL Monitor
        </div>
        <div className={`sse-status sse-${sseStatus}`} title={`SSE: ${sseStatus}`}>
          <span className="sse-dot" />
          {sseStatus}
        </div>
      </header>

      <MetricsBar metrics={metrics} />

      <div className="main-grid">
        <TraceList
          traces={traces}
          selectedId={selectedId}
          onSelect={handleSelect}
        />
        <SpanDetail trace={traceDetail} />
        <ChatPanel onSent={refreshAll} />
      </div>
    </div>
  );
}
