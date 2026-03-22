import express from "express";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(__dirname, "dist");
const API_BASE = process.env.API_BASE || "http://127.0.0.1:8000";
const PORT = process.env.PORT || 5173;

const app = express();
app.use(express.json());

/** Proxy request to FastAPI backend */
async function proxy(req, res, targetPath, opts = {}) {
  const qs = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  const url = `${API_BASE}${targetPath}${qs}`;
  try {
    const init = {
      method: req.method,
      headers: { "Content-Type": "application/json", ...opts.headers },
      ...(req.method !== "GET" &&
      req.method !== "HEAD" &&
      req.body &&
      Object.keys(req.body).length > 0
        ? { body: JSON.stringify(req.body) }
        : {}),
    };
    const f = await fetch(url, init);
    const ct = f.headers.get("content-type") || "";
    res.status(f.status);
    if (ct.includes("application/json")) {
      const data = await f.json();
      return res.json(data);
    }
    const text = await f.text();
    res.setHeader("Content-Type", ct || "text/plain");
    return res.send(text);
  } catch (e) {
    console.error("Proxy error:", e.message);
    return res.status(502).json({ detail: "Backend unreachable: " + e.message });
  }
}

/** SSE: stream run updates. Subscribe to FastAPI events, on each event fetch run and send to client. */
app.get("/api/runs/:id/stream", async (req, res) => {
  const runId = req.params.id;
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();

  const sendRun = async () => {
    try {
      const f = await fetch(`${API_BASE}/api/runs/${runId}`);
      if (!f.ok) return;
      const data = await f.json();
      res.write(`data: ${JSON.stringify(data)}\n\n`);
      res.flush?.();
    } catch (_) {}
  };

  let buffer = "";
  const ctrl = new AbortController();
  req.on("close", () => ctrl.abort());

  try {
    const eventRes = await fetch(`${API_BASE}/api/events`, {
      headers: { Accept: "text/event-stream" },
      signal: ctrl.signal,
    });
    if (!eventRes.body) return res.end();

    await sendRun();
    for await (const chunk of eventRes.body) {
      buffer += chunk.toString();
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        if (part.includes("data:")) await sendRun();
      }
      res.flush?.();
    }
  } catch (e) {
    if (e.name !== "AbortError" && e.code !== "ABORT_ERR") {
      console.error("SSE stream error:", e.message);
    }
  } finally {
    res.end();
  }
});

/** Strip /api prefix and proxy all other /api/* to FastAPI */
app.use("/api", (req, res) => {
  const targetPath = req.path.replace(/^\/api/, "") || "/";
  return proxy(req, res, `/api${targetPath}`);
});

app.use(express.static(distDir));

app.get("*", (req, res) => {
  if (path.extname(req.path)) return res.status(404).send("Not found");
  res.sendFile(path.join(distDir, "index.html"), (err) => {
    if (err) res.status(404).send("Not found");
  });
});

app.listen(PORT, () => {
  console.log(
    `PlainSpeak frontend: http://localhost:${PORT} (API → ${API_BASE})`
  );
});
