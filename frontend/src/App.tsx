import MemoryPage from "./components/MemoryPage";
import CampaignWorkspace from "./components/CampaignWorkspace";
import { useEffect, useState } from "react";
import { getHealth, type Health } from "./api/client";
import SourceBrowser from "./components/SourceBrowser";

type Connection =
  | { state: "checking" }
  | { state: "connected"; health: Health }
  | { state: "error" };

export default function App() {
  const [view, setView] = useState(() =>
    window.location.hash === "#memory" ? "memory" : window.location.hash === "#sources" ? "sources" : "campaigns",
  );
  useEffect(() => {
    const update = () =>
      setView(window.location.hash === "#memory" ? "memory" : window.location.hash === "#sources" ? "sources" : "campaigns");
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const [connection, setConnection] = useState<Connection>({
    state: "checking",
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    setConnection({ state: "checking" });
    getHealth(controller.signal)
      .then((health) => {
        if (!cancelled) setConnection({ state: "connected", health });
      })
      .catch(() => {
        if (!cancelled) setConnection({ state: "error" });
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [attempt]);

  return (
    <div className="app">
      <aside className="sidebar">
        <a className="wordmark" href="/" aria-label="Magi home">
          <img className="brand-logo" src="/magi-logo.png" alt="Magi" />
        </a>
        <div id="campaign-navigation" />
        <nav className="sidebar-footer" aria-label="Workspace tools">
          <a className="memory-nav" href="#memory" aria-current={view === "memory" ? "page" : undefined}>Memories</a>
          <a className="memory-nav" href="#sources" aria-current={view === "sources" ? "page" : undefined}>Sources</a>
        </nav>
      </aside>

      <main className={view === "campaigns" ? "campaign-view" : undefined}>
        <nav className="mobile-workspace-nav" aria-label="Mobile workspace">
          <a
            href="#campaigns"
            aria-current={view === "campaigns" ? "page" : undefined}
          >
            Campaigns
          </a>
          <a
            href="#sources"
            aria-current={view === "sources" ? "page" : undefined}
          >
            Sources
          </a>
          <a href="#memory" aria-current={view === "memory" ? "page" : undefined}>Memory</a>
        </nav>
        {view === "memory" && <MemoryPage />}
        <div hidden={view !== "sources"}>{view === "sources" ? (
          <>
            <SourceBrowser />
          </>
        ) : (
          <>

            {connection.state === "error" && (
              <div className="error" role="alert">
                We couldn’t connect to the workspace. Check that the backend is
                running.
                <button onClick={() => setAttempt((value) => value + 1)}>
                  Try again
                </button>
              </div>
            )}


          </>
        )}</div>
        <div className="campaign-view-content" hidden={view !== "campaigns"}><CampaignWorkspace /></div>
      </main>
    </div>
  );
}
