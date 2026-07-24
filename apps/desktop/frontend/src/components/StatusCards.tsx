export type DashboardData = {
  system_status?: string;
  cdp_status?: string;
  mode?: string;
  batch_label?: string;
};

const cardDefinitions = [
  ["green", "monitor", "status-system.svg", "Status do sistema", "system_status", "Pronto"],
  ["blue", "link", "cdp-link.svg", "CDP", "cdp_status", "Não testado"],
  ["purple", "flask", "simulation-mode.svg", "Modo", "mode", "Simulação"],
  ["orange", "layers", "batch-layers.svg", "Lote", "batch_label", "10 protocolos"]
] as const;

function iconUrl(fileName: string): string {
  return new URL(`../assets/icons/${fileName}`, import.meta.url).href;
}

export function StatusCards({ dashboard }: { dashboard?: DashboardData }) {
  return (
    <section className="status-grid">
      {cardDefinitions.map(([tone, iconClass, icon, label, field, fallback]) => (
        <article className={`status-card ${tone}`} key={label}>
          <div className={`status-icon ${iconClass}`}>
            <img src={iconUrl(icon)} alt="" />
          </div>
          <div>
            <small>{label}</small>
            <strong>{dashboard?.[field] || fallback}</strong>
          </div>
        </article>
      ))}
    </section>
  );
}
