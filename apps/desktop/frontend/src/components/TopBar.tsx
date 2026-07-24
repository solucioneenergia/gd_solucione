import { Settings } from "lucide-react";
import { cleanupDryRun } from "../bridge/qtBridge";

export function TopBar() {
  return (
    <header className="topbar">
      <h1>Automação GD Neoenergia — Desktop Visual</h1>
      <div className="topbar-actions">
        <div className="system-badge">
          <span />
          Sistema <strong>Pronto</strong>
        </div>
        <button className="icon-button" type="button" aria-label="Limpeza de temporários em simulação" title="Limpeza de temporários — simulação" onClick={cleanupDryRun}>
          <Settings size={18} />
        </button>
      </div>
    </header>
  );
}
