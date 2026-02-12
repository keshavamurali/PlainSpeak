import { useState, useEffect, useRef } from "react";
import { runs, formatDate, statusBadge } from "../api";

function RunPage({ runId, onBack }) {
  const [runData, setRunData] = useState(null);
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const logsEndRef = useRef(null);
  const streamCloseRef = useRef(null);

  const appendLog = (msg) => {
    setLogs((prev) => [...prev, { ts: new Date().toISOString(), msg }]);
  };

  const loadRun = async () => {
    try {
      const data = await runs.get(runId);
      setRunData(data);
      return data;
    } catch (e) {
      setError(e.message);
      return null;
    }
  };

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    loadRun().then((data) => {
      setLoading(false);
      if (data?.runs?.length) {
        setLogs(
          data.runs.map((r) => ({
            ts: r.started,
            msg: `✓ ${r.agent} completed`,
          }))
        );
      }
    });
  }, [runId]);

  useEffect(() => {
    const done = runData?.status === "completed" || runData?.status === "failed";
    if (!runId || done) return;
    appendLog("Connecting to live stream…");
    let lastRunCount = runData?.runs?.length ?? 0;
    streamCloseRef.current = runs.stream(runId, (data) => {
      setRunData(data);
      if (data?.runs?.length > lastRunCount) {
        const newRuns = data.runs.slice(lastRunCount);
        lastRunCount = data.runs.length;
        newRuns.forEach((r) => appendLog(`✓ ${r.agent} completed`));
      }
      if (data?.status === "completed") {
        appendLog("Run completed.");
      }
      if (data?.status === "failed") {
        appendLog(`Failed: ${data.error || "Unknown error"}`);
      }
    });
    return () => {
      if (streamCloseRef.current) {
        streamCloseRef.current();
      }
    };
  }, [runId, runData?.status]);

  if (loading && !runData) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <p className="text-slate-500">Loading run…</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-4xl px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="text-slate-500 hover:text-slate-700 text-sm"
              onClick={onBack}
            >
              ← Back
            </button>
            <span className="text-slate-400">|</span>
            <span className="font-mono text-sm text-slate-600">{runId}</span>
            <span className={statusBadge(runData?.status)}>
              {runData?.status || "—"}
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8">
        {error && (
          <div className="card p-4 mb-6 text-red-600">{error}</div>
        )}

        {runData?.query && (
          <div className="card p-4 mb-6">
            <h3 className="text-sm font-medium text-slate-500 mb-2">Query</h3>
            <p className="text-slate-800">{runData.query}</p>
          </div>
        )}

        <div className="card overflow-hidden mb-6">
          <div className="px-4 py-3 border-b border-slate-200 bg-slate-50 text-sm font-medium text-slate-600">
            Live logs
          </div>
          <div
            className="p-4 font-mono text-sm bg-slate-900 text-emerald-400 max-h-64 overflow-y-auto"
            style={{ minHeight: "120px" }}
          >
            {logs.length === 0 ? (
              <span className="text-slate-500">Waiting for updates…</span>
            ) : (
              logs.map((l, i) => (
                <div key={i} className="py-0.5">
                  <span className="text-slate-500 text-xs mr-2">
                    {new Date(l.ts).toLocaleTimeString()}
                  </span>
                  {l.msg}
                </div>
              ))
            )}
            <div ref={logsEndRef} />
          </div>
        </div>

        {runData?.artifacts && Object.keys(runData.artifacts).length > 0 && (
          <div className="card overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-200 bg-slate-50 text-sm font-medium text-slate-600">
              Agent outputs
            </div>
            <div className="p-4 space-y-4">
              {Object.entries(runData.artifacts).map(([name, artifact]) => (
                <div key={name}>
                  <h4 className="text-sm font-semibold text-slate-700 mb-2">
                    {name}
                  </h4>
                  <pre className="p-3 bg-slate-100 rounded-lg text-xs overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
                    {typeof artifact === "object"
                      ? JSON.stringify(artifact, null, 2)
                      : String(artifact)}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default RunPage;
