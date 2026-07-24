import { openLogsFolder, openReport, openWorkbook } from "../bridge/qtBridge";

function iconUrl(fileName: string): string {
  return new URL(`../assets/icons/${fileName}`, import.meta.url).href;
}

export function ReportsPanel() {
  return (
    <section className="panel reports-panel">
      <h2>Relatórios</h2>
      <div className="reports-grid">
        <button className="report-button" type="button" onClick={() => openReport("pipeline")}>
          <img src={iconUrl("report-file.svg")} alt="" /> <span className="report-label">Abrir pipeline_cdp_completo.md</span>
        </button>
        <button className="report-button" type="button" onClick={() => openReport("processing")}>
          <img src={iconUrl("report-file.svg")} alt="" /> <span className="report-label">Abrir processamento_pdfs_planilha_clientes.md</span>
        </button>
        <button className="report-button" type="button" onClick={openLogsFolder}>
          <img src={iconUrl("logs-folder.svg")} alt="" /> <span className="report-label">Abrir pasta de logs</span>
        </button>
        <button className="report-button sheet" type="button" onClick={openWorkbook}>
          <img src={iconUrl("spreadsheet-file.svg")} alt="" /> <span className="report-label">Abrir planilha</span>
        </button>
      </div>
    </section>
  );
}
