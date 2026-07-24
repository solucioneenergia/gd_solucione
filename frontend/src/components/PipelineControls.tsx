import type { InitialState, TemporarySettings } from "../api/qtBridge";

type Props = {
  state: InitialState;
  busy: boolean;
  onChange(settings: TemporarySettings): void;
  onDryRun(): void;
  onProduction(): void;
  onStop(): void;
};

export function PipelineControls({
  state,
  busy,
  onChange,
  onDryRun,
  onProduction,
  onStop,
}: Props) {
  const settings: TemporarySettings = {
    MAX_PORTAL_PAGES: state.max_portal_pages,
    MAX_COMPLETED_TO_PROCESS: state.max_completed_to_process,
    DRY_RUN: state.mode !== "production",
    ENABLE_PORTAL_PAGINATION: state.enable_portal_pagination,
    SKIP_ALREADY_COMPLETED: state.skip_already_completed,
    APPLY_EXCEL: state.apply_excel,
    APPLY_ARCHIVE: state.apply_archive,
    BACKUP_EXCEL: state.backup_excel,
  };
  const update = (key: keyof TemporarySettings, value: boolean | number) =>
    onChange({ ...settings, [key]: value });

  return (
    <section className="control-card glass">
      <header>
        <div>
          <span className="eyebrow">Execução</span>
          <h2>Pipeline</h2>
        </div>
        {busy && <span className="running-pill">Em execução</span>}
      </header>
      <div className="number-fields">
        <label>
          Máximo de páginas
          <input
            type="number"
            min="0"
            value={state.max_portal_pages}
            onChange={(event) =>
              update("MAX_PORTAL_PAGES", Number(event.target.value))
            }
          />
        </label>
        <label>
          Máximo de protocolos
          <input
            type="number"
            min="0"
            value={state.max_completed_to_process}
            onChange={(event) =>
              update("MAX_COMPLETED_TO_PROCESS", Number(event.target.value))
            }
          />
        </label>
      </div>
      <div className="toggle-grid">
        {(
          [
            ["ENABLE_PORTAL_PAGINATION", "Paginação"],
            ["SKIP_ALREADY_COMPLETED", "Ignorar concluídos"],
            ["APPLY_EXCEL", "Aplicar Excel"],
            ["APPLY_ARCHIVE", "Arquivar PDFs"],
            ["BACKUP_EXCEL", "Backup Excel"],
          ] as const
        ).map(([key, label]) => (
          <label className="toggle" key={key}>
            <input
              type="checkbox"
              checked={Boolean(settings[key])}
              onChange={(event) => update(key, event.target.checked)}
            />
            <span />
            {label}
          </label>
        ))}
      </div>
      <div className="button-row">
        <button className="primary" disabled={busy} onClick={onDryRun}>
          Rodar simulação
        </button>
        <button className="danger" disabled={busy} onClick={onProduction}>
          Rodar produção
        </button>
        <button className="ghost" disabled={!busy} onClick={onStop}>
          Solicitar parada
        </button>
      </div>
    </section>
  );
}
