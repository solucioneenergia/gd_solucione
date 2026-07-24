import type { OperationalStatus } from "../api/qtBridge";

const labels: Record<OperationalStatus, string> = {
  idle: "Aguardando",
  cdp_connected: "CDP conectado",
  reading_portal: "Lendo portal",
  downloading: "Baixando PDF",
  processing_pdf: "Processando PDF",
  updating_excel: "Atualizando Excel",
  archiving: "Arquivando",
  completed: "Concluído",
  error: "Erro",
};

type Props = {
  status: OperationalStatus;
  progress: number;
  connected: boolean;
  mode: string;
};

export function StatusPanel({ status, progress, connected, mode }: Props) {
  return (
    <section className="status-panel glass">
      <div>
        <span className={`status-dot ${status}`} />
        <div>
          <small>Status operacional</small>
          <strong>{labels[status]}</strong>
        </div>
      </div>
      <div className="status-facts">
        <span>Modo <b>{mode === "production" ? "Produção" : "Simulação"}</b></span>
        <span>CDP <b>{connected ? "Conectado" : "Aguardando"}</b></span>
      </div>
      <div className="progress-track" aria-label={`Progresso ${progress}%`}>
        <i style={{ width: `${progress}%` }} />
      </div>
    </section>
  );
}
