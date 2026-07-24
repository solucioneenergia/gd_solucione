import { Clock } from "lucide-react";

const processFlowPremiumWebpUrl = new URL("../assets/images/process-flow-premium.webp", import.meta.url).href;
const processFlowPremiumPngUrl = new URL("../assets/images/process-flow-premium.png", import.meta.url).href;
const processFlowPremiumSvgUrl = new URL("../assets/images/process-flow-premium.svg", import.meta.url).href;

export function ProcessVisualization({
  message = "Aguardando início da automação",
  percent = 0,
  activeStage = ""
}: {
  message?: string;
  percent?: number;
  activeStage?: string;
}) {
  const active = activeClass(activeStage);

  return (
    <article className="panel process-panel">
      <h2>Visualização do Processo</h2>
      <div className="process-visual process-flow premium-flow" data-active-stage={active} aria-label="Portal, Download, PDF, Planilha e Arquivo">
        <picture className="process-picture">
          <source srcSet={processFlowPremiumWebpUrl} type="image/webp" />
          <source srcSet={processFlowPremiumPngUrl} type="image/png" />
          <img className="process-asset process-premium-image" src={processFlowPremiumSvgUrl} alt="Portal → Download → PDF → Planilha → Arquivo" />
        </picture>
        <span className="sr-only">Portal</span>
        <span className="sr-only">Download</span>
        <span className="sr-only">PDF</span>
        <span className="sr-only">Planilha</span>
        <span className="sr-only">Arquivo</span>
      </div>
      <div className="activity-status">
        <Clock size={18} aria-hidden="true" />
        <span className="process-status-message">{message}</span>
        <span className="progress-pill process-status-percent">{percent}%</span>
      </div>
    </article>
  );
}

function activeClass(stage: string): string {
  const normalized = stage.toLowerCase();
  if (normalized.includes("download")) return "download";
  if (normalized.includes("pdf")) return "pdf";
  if (normalized.includes("excel")) return "planilha";
  if (normalized.includes("archive")) return "arquivo";
  if (normalized.includes("portal") || normalized.includes("cdp")) return "portal";
  return "";
}
