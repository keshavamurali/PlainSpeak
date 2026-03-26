import { useState } from "react";
import ChatPage from "./pages/ChatPage";
import RunsList from "./pages/RunsList";
import HLIGGraphPage from "./pages/HLIGGraphPage";

function App() {
  const [view, setView] = useState("chat");
  const [runId, setRunId] = useState(null);

  const openRun = (id) => {
    setRunId(id);
    setView("chat");
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-4xl px-4 py-4 flex items-center justify-between">
          <h1 className="flex flex-wrap items-center gap-2 sm:gap-3">
            <span className="text-xl font-bold tracking-tight text-slate-900">
              PlainSpeak
            </span>
            <span
              className="select-none text-violet-300/90 sm:text-base text-sm font-light"
              aria-hidden
            >
              —
            </span>
            <span className="relative inline-flex items-center">
              <span
                className="pointer-events-none absolute -inset-x-2 -inset-y-1 rounded-full bg-gradient-to-r from-violet-400/25 via-fuchsia-400/30 to-amber-400/25 blur-md"
                aria-hidden
              />
              <span className="relative font-serif text-lg sm:text-xl italic font-medium tracking-[0.12em] bg-gradient-to-br from-violet-600 via-fuchsia-600 to-amber-500 bg-clip-text text-transparent drop-shadow-sm">
                Genie
              </span>
            </span>
          </h1>
          <nav className="flex gap-4">
            <button
              type="button"
              className={`text-sm font-medium ${view === "chat" ? "text-violet-600" : "text-slate-600 hover:text-slate-900"}`}
              onClick={() => setView("chat")}
            >
              Chat
            </button>
            <button
              type="button"
              className={`text-sm font-medium ${view === "graph" ? "text-violet-600" : "text-slate-600 hover:text-slate-900"}`}
              onClick={() => setView("graph")}
            >
              Graph
            </button>
            <button
              type="button"
              className={`text-sm font-medium ${view === "runs" ? "text-violet-600" : "text-slate-600 hover:text-slate-900"}`}
              onClick={() => setView("runs")}
            >
              Runs
            </button>
          </nav>
        </div>
      </header>
      {view === "chat" && <ChatPage runId={runId} onSelectRun={openRun} />}
      {view === "graph" && <HLIGGraphPage runId={runId} onSelectRun={openRun} />}
      {view === "runs" && <RunsList onSelectRun={openRun} />}
    </div>
  );
}

export default App;
