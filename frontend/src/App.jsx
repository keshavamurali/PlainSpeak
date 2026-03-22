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
          <h1 className="text-xl font-semibold text-slate-800">PlainSpeak</h1>
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
