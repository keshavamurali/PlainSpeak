import { useState, useEffect } from "react";
import { runs, formatDate, statusBadge } from "../api";

function RunsList({ onSelectRun }) {
  const [runsList, setRunsList] = useState(() => runs.getCachedList() || []);
  const [loading, setLoading] = useState(true);
  const [fromCache, setFromCache] = useState(false);

  const loadRuns = async () => {
    setLoading(true);
    setFromCache(false);
    try {
      const list = await runs.list();
      setRunsList(Array.isArray(list) ? list : []);
      setFromCache(runs._lastSource === "cache");
    } catch (e) {
      console.error(e);
      const cached = runs.getCachedList();
      if (cached) {
        setRunsList(cached);
        setFromCache(true);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRuns();
  }, []);

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <div className="card overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-lg font-semibold text-slate-800">Runs</h2>
          <div className="flex items-center gap-2">
            {fromCache && (
              <span className="text-xs text-amber-600 bg-amber-50 px-2 py-1 rounded">
                Showing cached runs — backend offline
              </span>
            )}
            <button type="button" className="btn-secondary text-sm" onClick={loadRuns}>
              Refresh
            </button>
          </div>
        </div>
        {loading ? (
          <div className="p-8 text-center text-slate-500">Loading…</div>
        ) : runsList.length === 0 ? (
          <div className="p-8 text-center text-slate-500">
            No runs yet. Go to Chat to start one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                    ID
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                    Query
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                    Created
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {runsList.map((r) => (
                  <tr key={r.id} className="hover:bg-slate-50">
                    <td className="px-6 py-3 text-sm font-mono text-slate-600">
                      {r.id}
                    </td>
                    <td className="px-6 py-3 text-sm text-slate-800 max-w-xs truncate">
                      {r.query || "—"}
                    </td>
                    <td className="px-6 py-3">
                      <span className={statusBadge(r.status)}>{r.status || "—"}</span>
                    </td>
                    <td className="px-6 py-3 text-sm text-slate-600">
                      {formatDate(r.created_at)}
                    </td>
                    <td className="px-6 py-3">
                      <button
                        type="button"
                        className="text-violet-600 hover:underline text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                        onClick={() => onSelectRun(r.id)}
                        title={fromCache ? "Start the backend to load run details" : undefined}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}

export default RunsList;
