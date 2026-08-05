import { Button } from "./Button";
import { requestProduction, runDryRun, testCdpConnection } from "../bridge/qtBridge";

function iconUrl(fileName: string): string {
  return new URL(`../assets/icons/${fileName}`, import.meta.url).href;
}

export function PortalExecutionPanel() {
  return (
    <article className="panel execution-panel">
      <h2>Portal e Execução</h2>
      <div className="button-grid">
        <Button className="primary" disabled title="Indisponível nesta versão">
          <img className="btn-svg" src={iconUrl("cdp-link.svg")} alt="" /> Abrir Edge CDP
        </Button>
        <Button onClick={testCdpConnection}>
          <img className="btn-svg" src={iconUrl("cdp-link.svg")} alt="" /> Testar conexão CDP
        </Button>
        <Button disabled title="Indisponível nesta versão">
          <img className="btn-svg" src={iconUrl("portal.svg")} alt="" /> Inspecionar portal
        </Button>
      </div>
      <div className="divider" />
      <h2>Pipeline</h2>
      <div className="config-grid">
        <div className="config-chip"><span>Paginação do portal:</span><strong>Ativa</strong></div>
        <div className="config-chip"><span>Máx. páginas:</span><strong>2</strong></div>
        <div className="config-chip"><span>Máx. protocolos:</span><strong>10</strong></div>
        <div className="config-chip"><span>Pular concluídos:</span><strong>Sim</strong></div>
      </div>
      <div className="run-grid">
        <Button className="outline" onClick={runDryRun}>
          <img className="btn-svg" src={iconUrl("simulation-mode.svg")} alt="" /> Rodar simulação
        </Button>
        <Button className="production" onClick={requestProduction}>
          <img className="btn-svg" src={iconUrl("pipeline.svg")} alt="" /> Rodar produção
        </Button>
      </div>
      <div className="warning"><span>⚠</span>Produção exige confirmação antes de atualizar planilha e arquivar PDFs.</div>
    </article>
  );
}
