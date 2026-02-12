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

export const runs = {
  list: () => api("/runs"),
  get: (id) => api(`/runs/${id}`),
  create: (body) => api("/runs", { method: "POST", body: JSON.stringify(body) }),
  stream: (id, onData) => {
    const es = new EventSource(`${API}/runs/${id}/stream`);
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (onData) onData(data);
      } catch (_) {}
    };
    es.onerror = () => es.close();
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
      return "badge-running";
    case "failed":
      return "badge-error";
    default:
      return "badge-pending";
  }
}
