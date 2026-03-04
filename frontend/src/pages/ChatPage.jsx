import { useState, useEffect, useRef } from "react";
import { runs } from "../api";

const POLL_INTERVAL_MS = 2500;

function ChatPage({ runId: initialRunId, onSelectRun }) {
  const [runId, setRunId] = useState(initialRunId);
  useEffect(() => {
    setRunId(initialRunId);
    if (initialRunId) setPolling(true);
  }, [initialRunId]);
  const [input, setInput] = useState("");
  const [runData, setRunData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [polling, setPolling] = useState(false);
  const [pendingQuery, setPendingQuery] = useState(null);
  const messagesEndRef = useRef(null);
  const streamCloseRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [runData?.messages, pendingQuery]);

  const loadRun = async (id) => {
    if (!id) return null;
    try {
      const data = await runs.get(id);
      setRunData(data);
      return data;
    } catch (e) {
      console.error("Load run failed:", e);
      return null;
    }
  };

  const startRun = async (query) => {
    if (!query.trim() || loading) return;
    const q = query.trim();
    setLoading(true);
    setPendingQuery(q);
    try {
      const res = await runs.create({ query: q });
      setRunId(res.id);
      setInput("");
      setPendingQuery(null);
      await loadRun(res.id);
      setPolling(true);
    } catch (e) {
      console.error("Create run failed:", e);
      setPendingQuery(null);
    } finally {
      setLoading(false);
    }
  };

  const sendInput = async () => {
    if (!input.trim() || !runId || sending) return;
    const text = input.trim();
    setInput("");
    setSending(true);
    try {
      await runs.sendInput(runId, text);
      const data = await loadRun(runId);
      if (data?.status === "completed" || data?.status === "failed") {
        setPolling(false);
        if (streamCloseRef.current) {
          streamCloseRef.current();
        }
      } else {
        // Planner may run again (~12s). Poll aggressively to pick up new questions.
        [3000, 8000, 15000].forEach((ms) => {
          setTimeout(() => loadRun(runId), ms);
        });
      }
    } catch (e) {
      console.error("Send input failed:", e);
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    if (runId) {
      loadRun(runId).then((d) => {
        if (d && (d.status === "running" || d.pending_clarification)) setPolling(true);
      });
    }
  }, [runId]);

  useEffect(() => {
    if (!runId || !polling || runData?.status === "completed" || runData?.status === "failed")
      return;
    streamCloseRef.current = runs.stream(runId, (data) => {
      setRunData(data);
      if (data?.status === "completed" || data?.status === "failed") {
        setPolling(false);
        if (streamCloseRef.current) streamCloseRef.current();
      }
    });
    return () => {
      if (streamCloseRef.current) streamCloseRef.current();
    };
  }, [runId, polling, runData?.status]);

  useEffect(() => {
    if (!runId || !polling || runData?.status === "completed" || runData?.status === "failed")
      return;
    const id = setInterval(() => {
      loadRun(runId).then((d) => {
        if (d?.status === "completed" || d?.status === "failed") setPolling(false);
      });
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [runId, polling, runData?.status]);

  const pending = runData?.pending_clarification;
  const messages = runData?.messages || [];
  const isCompleted = runData?.status === "completed" || runData?.status === "failed";

  return (
    <main className="mx-auto max-w-3xl flex flex-col h-[calc(100vh-72px)] bg-white rounded-lg shadow-sm border border-slate-200 overflow-hidden">
      <div className="flex-shrink-0 px-4 py-3 border-b border-slate-200 bg-slate-50">
        <h2 className="text-sm font-medium text-slate-700">
          {runId ? "Conversation" : "Chat"}
        </h2>
        {runId && (
          <p className="text-xs text-slate-500 mt-0.5 truncate" title={runId}>
            Run: {runId}
          </p>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
        {!runId ? (
          <div className="text-center py-12">
            <p className="text-slate-500 mb-4">
              Send a query to start. The agent may ask for clarification.
            </p>
            <p className="text-sm text-slate-400">
              Or select a run from the Runs tab to continue.
            </p>
          </div>
        ) : messages.length === 0 && !pendingQuery ? (
          <div className="text-center py-8 text-slate-500">Loading…</div>
        ) : (
          <>
            {pendingQuery && (
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl px-4 py-3 bg-violet-600 text-white">
                  <p className="text-sm whitespace-pre-wrap">{pendingQuery}</p>
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div
                key={`${i}-${m.role}-${String(m.content).slice(0, 20)}`}
                className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                    m.role === "user"
                      ? "bg-violet-600 text-white"
                      : "bg-slate-50 border border-slate-200"
                  }`}
                >
                  <p className="text-sm whitespace-pre-wrap break-words">{m.content}</p>
                  {m.clarification && m.options?.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {m.options.map((opt, j) => (
                        <button
                          key={j}
                          type="button"
                          className="text-xs px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700"
                          onClick={() => {
                            setInput(opt);
                          }}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="flex-shrink-0 border-t border-slate-200 bg-white p-4">
        {pending ? (
          <div className="flex flex-col gap-3">
            {pending.message && (
              <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2">
                <p className="text-xs font-medium text-amber-800 mb-1">Please answer:</p>
                <p className="text-sm text-amber-900 whitespace-pre-wrap">{pending.message}</p>
              </div>
            )}
            <div className="flex gap-2 items-end">
            <textarea
              rows={3}
              className="input flex-1 resize-y min-h-[60px]"
              placeholder="Type your response (multiple lines). Press Send when ready."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={sending}
            />
            <button
              type="button"
              className="btn-primary"
              onClick={sendInput}
              disabled={sending || !input.trim()}
            >
              {sending ? "Sending…" : "Send"}
            </button>
            </div>
          </div>
        ) : !runId ? (
          <div className="flex gap-2 items-end">
            <textarea
              rows={3}
              className="input flex-1 resize-y min-h-[60px]"
              placeholder="Describe your task (multiple lines). Press Start when ready."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="button"
              className="btn-primary"
              onClick={() => startRun(input)}
              disabled={loading || !input.trim()}
            >
              {loading ? "Starting…" : "Start"}
            </button>
          </div>
        ) : isCompleted ? (
          <div className="flex gap-2 items-end">
            <textarea
              rows={3}
              className="input flex-1 resize-y min-h-[60px]"
              placeholder="New query (multiple lines). Press New run when ready."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button
              type="button"
              className="btn-primary"
              onClick={() => startRun(input)}
              disabled={loading || !input.trim()}
            >
              New run
            </button>
          </div>
        ) : (
          <p className="text-sm text-slate-500 py-2">Processing…</p>
        )}
      </div>
    </main>
  );
}

export default ChatPage;
