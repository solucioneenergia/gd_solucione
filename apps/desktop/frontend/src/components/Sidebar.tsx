const items = [
  ["Dashboard", "dashboard.svg"],
  ["Portal", "portal.svg"],
  ["Pipeline", "pipeline.svg"],
  ["Protocolos", "protocolos.svg"],
  ["Relatórios", "relatorios.svg"],
  ["Configurações", "configuracoes.svg"]
] as const;

function iconUrl(fileName: string): string {
  return new URL(`../assets/icons/${fileName}`, import.meta.url).href;
}

export function Sidebar() {
  return (
    <aside className="sidebar">
      <nav className="sidebar-nav" aria-label="Navegação principal">
        {items.map(([label, icon], index) => (
          <a href="#" className={`nav-item ${index === 0 ? "active" : ""}`} key={label}>
            <span className="sidebar-icon" aria-hidden="true">
              <img src={iconUrl(icon)} alt="" />
            </span>
            {label}
          </a>
        ))}
      </nav>
      <div className="python-card">
        <span className="python-logo">Py</span>
        <div>
          <strong>Python 3.11</strong>
          <small title="Modo visual estático disponível quando não há build React.">Ambiente pronto</small>
        </div>
        <span className="status-dot" />
      </div>
    </aside>
  );
}
