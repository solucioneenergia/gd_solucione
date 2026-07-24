import type { InitialState, PipelineSummary } from "../api/qtBridge";

type Props = {
  state: InitialState;
  summary: PipelineSummary | null;
};

export function Dashboard({ state, summary }: Props) {
  const cards: Array<[string, string | number]> = [
    ["Páginas", state.max_portal_pages || "Sem limite"],
    ["Limite de protocolos", state.max_completed_to_process || "Sem limite"],
    ["Processados", String(summary?.total_processed_success ?? 0)],
    ["Erros", String(summary?.total_errors ?? 0)],
  ];
  return (
    <section>
      <header className="section-title">
        <div>
          <span className="eyebrow">Visão geral</span>
          <h2>Dashboard operacional</h2>
        </div>
        <p>Portal, documentos e arquivamento em um único fluxo.</p>
      </header>
      <div className="metric-grid">
        {cards.map(([label, value]) => (
          <article className="metric-card glass" key={label}>
            <small>{label}</small>
            <strong>{String(value)}</strong>
          </article>
        ))}
      </div>
    </section>
  );
}
