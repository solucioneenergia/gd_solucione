import { useEffect, useState } from "react";

import { initializeQtBridge } from "./bridge/qtBridge";
import { PortalExecutionPanel } from "./components/PortalExecutionPanel";
import { ProcessVisualization } from "./components/ProcessVisualization";
import { RecentProtocol, RecentProtocolsTable } from "./components/RecentProtocolsTable";
import { ReportsPanel } from "./components/ReportsPanel";
import { Sidebar } from "./components/Sidebar";
import { DashboardData, StatusCards } from "./components/StatusCards";
import { TopBar } from "./components/TopBar";

export function App() {
  const [message, setMessage] = useState("Aguardando início da automação");
  const [dashboard, setDashboard] = useState<DashboardData>();
  const [protocols, setProtocols] = useState<RecentProtocol[]>();
  const [percent, setPercent] = useState(0);
  const [activeStage, setActiveStage] = useState("");

  useEffect(() => {
    initializeQtBridge();
    const onLog = (event: Event) => {
      setMessage((event as CustomEvent<string>).detail);
    };
    const onDashboard = (event: Event) => {
      setDashboard((event as CustomEvent<DashboardData>).detail);
    };
    const onProtocols = (event: Event) => {
      setProtocols((event as CustomEvent<RecentProtocol[]>).detail);
    };
    const onProgress = (event: Event) => {
      const payload = (event as CustomEvent<{ overall_percent?: number; stage?: string; message?: string }>).detail;
      setPercent(payload.overall_percent ?? 0);
      setActiveStage(payload.stage ?? "");
      setMessage(payload.message ?? payload.stage ?? "Processando");
    };
    document.addEventListener("desktop-log", onLog);
    document.addEventListener("desktop-dashboard", onDashboard);
    document.addEventListener("desktop-protocols", onProtocols);
    document.addEventListener("desktop-progress", onProgress);
    return () => {
      document.removeEventListener("desktop-log", onLog);
      document.removeEventListener("desktop-dashboard", onDashboard);
      document.removeEventListener("desktop-protocols", onProtocols);
      document.removeEventListener("desktop-progress", onProgress);
    };
  }, []);

  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <TopBar />
        <StatusCards dashboard={dashboard} />
        <section className="middle-grid">
          <PortalExecutionPanel />
          <ProcessVisualization message={message} percent={percent} activeStage={activeStage} />
        </section>
        <RecentProtocolsTable protocols={protocols} />
        <ReportsPanel />
        <div className="log-strip">{message}</div>
      </main>
    </div>
  );
}
