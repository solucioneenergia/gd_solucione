type Props = {
  onPipeline(): void;
  onProcessing(): void;
  onLogs(): void;
  onDownloads(): void;
  onWorkbook(): void;
  onClients(): void;
};

export function ReportsPanel(props: Props) {
  const actions = [
    ["Relatório pipeline", props.onPipeline],
    ["Relatório processamento", props.onProcessing],
    ["Pasta de logs", props.onLogs],
    ["Downloads", props.onDownloads],
    ["Abrir planilha", props.onWorkbook],
    ["Pasta de clientes", props.onClients],
  ] as const;
  return (
    <section className="control-card glass">
      <header>
        <div>
          <span className="eyebrow">Atalhos locais</span>
          <h2>Relatórios e arquivos</h2>
        </div>
      </header>
      <div className="report-grid">
        {actions.map(([label, action]) => (
          <button className="report-button" onClick={action} key={label}>
            <span>{label}</span><b>↗</b>
          </button>
        ))}
      </div>
    </section>
  );
}
