import { useState, useEffect } from "react";
import { runs, formatDate, statusBadge } from "../api";

function Home({ onCreateRun }) {
  const [query, setQuery] = useState("");
  const [runsList, setRunsList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  const loadRuns = async () => {
    setLoading(true);
    try {
      const list = await runs.list();
      setRunsList(Array.isArray(list) ? list : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRuns();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!query.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await runs.create({ query: query.trim() });
      setQuery("");
      onCreateRun(res.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-4xl px-4 py-4">
          <h1 className="text-xl font-semibold text-slate-800">
            PlainSpeak
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Agentic AI development
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8">
        <section className="card p-6 mb-8">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">
            Create run
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4 max-w-2xl">
            <div>
              <label
                htmlFor="query"
                className="block text-sm font-medium text-slate-700 mb-1"
              >
                Task (query)
              </label>
              <textarea
                id="query"
                rows={3}
                className="input"
                placeholder="Describe the task..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              className="btn-primary"
              disabled={creating}
            >
              {creating ? "Creating…" : "Create run"}
            </button>
          </form>
          {error && (
            <p className="mt-3 text-sm text-red-600">{error}</p>
          )}
        </section>

        <section className="card overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-800">Runs</h2>
            <button
              type="button"
              className="btn-secondary text-sm"
              onClick={loadRuns}
            >
              Refresh
            </button>
          </div>
          {loading ? (
            <div className="p-8 text-center text-slate-500">Loading runs…</div>
          ) : runsList.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              No runs yet. Create one above.
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
                        <span className={statusBadge(r.status)}>
                          {r.status || "—"}
                        </span>
                      </td>
                      <td className="px-6 py-3 text-sm text-slate-600">
                        {formatDate(r.created_at)}
                      </td>
                      <td className="px-6 py-3">
                        <button
                          type="button"
                          className="text-violet-600 hover:underline text-sm"
                          onClick={() => onCreateRun(r.id)}
                        >
                          View
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default Home;
