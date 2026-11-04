import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ProcessInspector from "./components/ProcessInspector";
import "./styles.css";

const inspectRoute = window.location.pathname.match(/^\/inspect\/([^/]+)\/?$/);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {inspectRoute ? <ProcessInspector campaignId={decodeURIComponent(inspectRoute[1])} /> : <App />}
  </StrictMode>,
);
