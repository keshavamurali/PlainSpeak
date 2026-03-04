const API = "/api";

export async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const j = JSON.parse(text);
      detail = j.detail || j.message || JSON.stringify(j);
    } catch (_) {}
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type");
  if (ct && ct.includes("application/json")) return res.json();
  return res.text();
}

const RUNS_CACHE_KEY = "plainspeak_runs_cache";

export const runs = {
  list: async () => {
    try {
      const list = await api("/runs");
      runs._lastSource = "api";
      if (Array.isArray(list)) {
        try {
          localStorage.setItem(RUNS_CACHE_KEY, JSON.stringify({ list, at: Date.now() }));
        } catch (_) {}
      }
      return list;
    } catch (e) {
      const cached = runs.getCachedList();
      if (cached) {
        runs._lastSource = "cache";
        return cached;
      }
      runs._lastSource = null;
      throw e;
    }
  },
  getCachedList: () => {
    try {
      const cached = localStorage.getItem(RUNS_CACHE_KEY);
      if (cached) {
        const { list } = JSON.parse(cached);
        return Array.isArray(list) ? list : null;
      }
    } catch (_) {}
    return null;
  },
  get: (id) => api(`/runs/${id}`),
  create: (body) => api("/runs", { method: "POST", body: JSON.stringify(body) }),
  sendInput: (id, response) =>
    api(`/runs/${id}/input`, { method: "POST", body: JSON.stringify({ response }) }),
  stream: (id, onData) => {
    const es = new EventSource(`${API}/runs/${id}/stream`);
    es.onmessage = (ev) => {
      if (!ev.data || ev.data.trim() === "") return;
      try {
        const data = JSON.parse(ev.data);
        if (onData) onData(data);
      } catch (_) {}
    };
    es.onerror = () => {
      es.close();
    };
    return () => es.close();
  },
};

export function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function statusBadge(status) {
  switch ((status || "").toLowerCase()) {
    case "completed":
      return "badge-success";
    case "running":
    case "starting":
    case "waiting_input":
      return "badge-running";
    case "failed":
      return "badge-error";
    default:
      return "badge-pending";
  }
}
