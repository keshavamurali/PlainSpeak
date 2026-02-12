import { useState } from "react";
import Home from "./pages/Home";
import RunPage from "./pages/RunPage";

function App() {
  const [page, setPage] = useState("home");
  const [runId, setRunId] = useState(null);

  const openRun = (id) => {
    setRunId(id);
    setPage("run");
  };

  const goHome = () => {
    setPage("home");
    setRunId(null);
  };

  if (page === "run" && runId) {
    return <RunPage runId={runId} onBack={goHome} />;
  }
  return <Home onCreateRun={openRun} />;
}

export default App;
