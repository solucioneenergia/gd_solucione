import { useEffect, useMemo, useState } from "react";
import {
  connectQtBridge,
  type BackendApi,
  type InitialState,
  type OperationalStatus,
  type PipelineSummary,
  type ProtocolResult,
} from "./api/qtBridge";

type IconName =
  | "grid"
  | "globe"
  | "pipeline"
  | "document"
  | "chart"
  | "gear"
  | "monitor"
  | "link"
  | "flask"
  | "stack"
  | "edge"
  | "search"
  | "play"
  | "warning"
  | "clock"
  | "folder"
  | "sheet";

const fallbackState: InitialState = {
  mode: "simulation",
  cdp_mode: true,
  cdp_endpoint: "http://127.0.0.1:9222",
  max_portal_pages: 1,
  max_completed_to_process: 10,
  enable_portal_pagination: true,
  skip_already_completed: true,
  apply_excel: true,
  apply_archive: true,
  backup_excel: true,
  busy: false,
};

const sampleProtocols: ProtocolResult[] = [];

const statusLabels: Record<OperationalStatus, string> = {
  idle: "Aguardando início da automação",
  cdp_connected: "CDP conectado",
  reading_portal: "Lendo Portal GD",
  downloading: "Baixando PDFs",
  processing_pdf: "Processando PDF",
  updating_excel: "Atualizando planilha",
  archiving: "Arquivando PDF",
  completed: "Processo concluido",
  error: "Falha na operacao",
};

export default function App() {
  const [backend, setBackend] = useState<BackendApi | null>(null);
  const [state, setState] = useState<InitialState>(fallbackState);
  const [status, setStatus] = useState<OperationalStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [protocols, setProtocols] = useState<ProtocolResult[]>([]);
  const [logs, setLogs] = useState<string[]>(["Base visual carregada."]);

  useEffect(() => {
    connectQtBridge().then(async (api) => {
      if (!api) {
        setLogs(["Pré-visualização: QWebChannel ainda não conectado."]);
        return;
      }

      setBackend(api);
      setState(await api.getInitialState());

      api.statusChanged.connect((nextStatus) => setStatus(nextStatus));
      api.progressChanged.connect(setProgress);
      api.operationStarted.connect((name) => {
        setBusy(true);
        setProtocols([]);
        setLogs((current) => [`Operação iniciada: ${name}.`, ...current].slice(0, 8));
      });
      api.operationFinished.connect((name) => {
        setBusy(false);
        setLogs((current) => [`Operação concluída: ${name}.`, ...current].slice(0, 8));
      });
      api.operationFailed.connect((message) => {
        setBusy(false);
        setStatus("error");
        setLogs((current) => [`Erro: ${message}`, ...current].slice(0, 8));
      });
      api.logMessage.connect((message) =>
        setLogs((current) => [message, ...current].slice(0, 8)),
      );
      api.summaryChanged.connect(setSummary);
      api.protocolUpdated.connect((protocol) =>
        setProtocols((current) => [...current, protocol]),
      );
    });
  }, []);

  const displayedProtocols = protocols.length ? protocols : sampleProtocols;
  const selectedCount = useMemo(() => {
    const value = summary?.total_selected ?? summary?.total_processed_success;
    return typeof value === "number" ? value : state.max_completed_to_process;
  }, [state.max_completed_to_process, summary]);
  const requestProduction = () => {
    const answer = window.prompt("Digite SIM para confirmar a execução em produção.");
    if (answer?.trim().toUpperCase() === "SIM") {
      backend?.runPipelineProduction(answer);
    }
  };

  return (
    <main className="desktop-shell">
      <aside className="sidebar">
        <div className="brand-mark"><Icon name="play" /></div>
        <nav aria-label="Menu principal">
          {[
            ["Dashboard", "grid"],
            ["Portal", "globe"],
            ["Pipeline", "pipeline"],
            ["Protocolos", "document"],
            ["Relatórios", "chart"],
            ["Configurações", "gear"],
          ].map(([item, icon], index) => (
              <button key={item} className={index === 0 ? "active" : ""}>
                <Icon name={icon as IconName} />
                {item}
              </button>
          ))}
        </nav>
        <div className="runtime-card">
          <Icon name="sheet" />
          <div>
            <strong>Python 3.11</strong>
            <small>{backend ? "Ambiente pronto" : "Aguardando ponte"}</small>
          </div>
          <i />
        </div>
      </aside>

      <section className="content">
        <header className="page-header">
          <div>
            <h1>Automação GD Neoenergia — Desktop Visual</h1>
          </div>
          <div className="header-actions">
            <span className={`system-pill ${backend ? "ready" : ""}`}>
              <i />
              Sistema
              <b>{backend ? "Pronto" : "Preview"}</b>
            </span>
            <button className="icon-button" aria-label="Configurações">
              <Icon name="gear" />
            </button>
          </div>
        </header>

        <section className="status-grid">
          <StatusCard icon="monitor" label="Status do sistema" value={backend ? "Pronto" : "Preview"} tone="green" />
          <StatusCard icon="link" label="CDP" value={state.cdp_mode ? "Conectado" : "Desligado"} tone="blue" />
          <StatusCard icon="flask" label="Modo" value={state.mode === "simulation" ? "Simulação" : "Produção"} tone="purple" />
          <StatusCard icon="stack" label="Lote" value={`${selectedCount} protocolos`} tone="orange" />
        </section>

        <section className="main-grid">
          <article className="panel">
            <h2>Portal e Execução</h2>
            <div className="button-grid">
              <button className="primary" disabled={!backend || busy} onClick={() => backend?.openEdgeCdp()}>
                <Icon name="edge" /> Abrir Edge CDP
              </button>
              <button disabled={!backend || busy} onClick={() => backend?.testCdpConnection()}>
                <Icon name="link" /> Testar conexão CDP
              </button>
              <button disabled={!backend || busy} onClick={() => backend?.inspectPortal()}>
                <Icon name="search" /> Inspecionar portal
              </button>
            </div>

            <h2>Pipeline</h2>
            <div className="settings-grid">
              <Badge icon="pipeline" label="Paginação do portal" value={state.enable_portal_pagination ? "Ativa" : "Inativa"} />
              <Badge label="Max. paginas" value={String(state.max_portal_pages)} />
              <Badge label="Max. protocolos" value={String(state.max_completed_to_process)} />
              <Badge icon="monitor" label="Pular concluídos" value={state.skip_already_completed ? "Sim" : "Não"} />
            </div>
            <div className="button-grid two">
              <button className="outline" disabled={!backend || busy} onClick={() => backend?.runPipelineDryRun()}>
                <Icon name="play" /> Rodar simulação
              </button>
              <button className="accent" disabled={!backend || busy} onClick={requestProduction}>
                <Icon name="play" /> Rodar produção
              </button>
            </div>
            <p className="warning"><Icon name="warning" /> Produção exige confirmação antes de atualizar planilha e arquivar PDFs.</p>
          </article>

          <article className="panel process-panel">
            <h2>Visualização do Processo</h2>
            <div className={`process-flow ${status}`}>
              {[
                ["Portal", "solar"],
                ["Download", "tower"],
                ["PDF", "pdf"],
                ["Planilha", "spreadsheet"],
                ["Arquivo", "archive"],
              ].map(([step, kind]) => (
                <div key={step} className="flow-step">
                  <span className={`process-icon ${kind}`}>
                    <i />
                  </span>
                  <strong>{step}</strong>
                </div>
              ))}
            </div>
            <div className="operation-state">
              <Icon name="clock" />
              {busy && <span>{progress}%</span>}
              {statusLabels[status]}
            </div>
          </article>
        </section>

        <section className="panel table-panel">
          <h2>Protocolos recentes</h2>
          <table>
            <thead>
              <tr>
                <th>Protocolo</th>
                <th>Cliente</th>
                <th>PDF</th>
                <th>Excel</th>
                <th>Arquivo</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {displayedProtocols.map((row) => (
                <tr key={`${row.protocol}-${row.client}`}>
                  <td className="protocol">{row.protocol}</td>
                  <td>{row.client}</td>
                  <td>{row.pdf_status || "-"}</td>
                  <td>{row.excel_status || "-"}</td>
                  <td>{row.archive_status || "-"}</td>
                  <td><span className={row.error ? "status-error" : "status-ok"}>{row.error ? "Erro" : "OK"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel reports-panel">
          <h2>Relatorios</h2>
          <button disabled={!backend} onClick={() => backend?.openPipelineReport()}><Icon name="document" /> Abrir pipeline_cdp_completo.md</button>
          <button disabled={!backend} onClick={() => backend?.openProcessingReport()}><Icon name="document" /> Abrir processamento_pdfs_planilha_clientes.md</button>
          <button disabled={!backend} onClick={() => backend?.openLogsFolder()}><Icon name="folder" /> Abrir pasta de logs</button>
          <button disabled={!backend} onClick={() => backend?.openWorkbook()}><Icon name="sheet" /> Abrir planilha</button>
        </section>

        <section className="panel log-panel">
          <h2>Eventos</h2>
          {logs.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)}
        </section>
      </section>
    </main>
  );
}
function StatusCard({ icon, label, value, tone }: { icon: IconName; label: string; value: string; tone: string }) {
  return (
    <article className={`status-card ${tone}`}>
      <span><Icon name={icon} /></span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </article>
  );
}

function Badge({ icon, label, value }: { icon?: IconName; label: string; value: string }) {
  return (
    <div className="badge">
      <span>{icon && <Icon name={icon} />}{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Icon({ name }: { name: IconName }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      {name === "grid" && <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />}
      {name === "globe" && <path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18M4 8h16M4 16h16M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />}
      {name === "pipeline" && <path d="M7 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm16 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM7 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM6 6h11M4 9v6M12 6v12H7" />}
      {name === "document" && <path d="M6 3h8l4 4v14H6zM14 3v5h4M8 13h8M8 17h8M8 9h3" />}
      {name === "chart" && <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />}
      {name === "gear" && <path d="M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm8 4a7 7 0 0 0-.2-1.7l2-1.5-2-3.4-2.4 1a8 8 0 0 0-2.9-1.7L14 2h-4l-.5 2.7a8 8 0 0 0-2.9 1.7l-2.4-1-2 3.4 2 1.5A7 7 0 0 0 4 12c0 .6.1 1.2.2 1.7l-2 1.5 2 3.4 2.4-1a8 8 0 0 0 2.9 1.7L10 22h4l.5-2.7a8 8 0 0 0 2.9-1.7l2.4 1 2-3.4-2-1.5c.1-.5.2-1.1.2-1.7Z" />}
      {name === "monitor" && <path d="M4 5h16v11H4zM9 21h6M12 16v5M8 12l2-3 3 5 2-4 1 2h2" />}
      {name === "link" && <path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1" />}
      {name === "flask" && <path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 15h8" />}
      {name === "stack" && <path d="m12 3 9 5-9 5-9-5 9-5Zm-7 9 7 4 7-4M5 16l7 4 7-4" />}
      {name === "edge" && <path d="M20 15a8 8 0 0 1-15.5 2.5C1 10 6 3 13 3c4 0 7 3 7 7 0 3-2 5-5 5H9a3 3 0 0 0 5.6 1.5" />}
      {name === "search" && <path d="M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Zm5.5-2 5 5" />}
      {name === "play" && <path d="m8 5 11 7-11 7z" />}
      {name === "warning" && <path d="m12 3 10 18H2L12 3Zm0 6v5M12 17h.01" />}
      {name === "clock" && <path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-5v6l4 2" />}
      {name === "folder" && <path d="M3 6h7l2 2h9v11H3z" />}
      {name === "sheet" && <path d="M6 3h12v18H6zM9 8h6M9 12h6M9 16h6M6 8h12M6 12h12M6 16h12" />}
    </svg>
  );
}
