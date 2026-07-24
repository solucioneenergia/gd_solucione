import { PRODUCTION_CONFIRMATION } from "../data/mockDashboard";

declare global {
  interface Window {
    qt?: { webChannelTransport?: unknown };
    QWebChannel?: new (transport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) => void;
    backend?: Record<string, (...args: unknown[]) => unknown> & Record<string, { connect?: (callback: (...args: unknown[]) => void) => void }>;
  }
}

let backend: Window["backend"] | null = null;

export function initializeQtBridge(): void {
  if (!window.qt?.webChannelTransport || !window.QWebChannel) {
    window.backend = createMockBackend();
    backend = window.backend;
    requestInitialData();
    return;
  }

  new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
    backend = channel.objects.backend as Window["backend"];
    window.backend = backend;
    connectSignals();
    requestInitialData();
  });
}

export function checkEnvironment(): void {
  callBackend("check_environment");
}

export function openEdgeCdp(): void {
  callBackend("open_edge_cdp");
}

export function testCdpConnection(): void {
  callBackend("test_cdp_connection");
}

export function inspectPortal(): void {
  callBackend("inspect_portal");
}

export function runDryRun(): void {
  callBackend("run_dry_run");
}

export function requestProduction(confirmation: string): void {
  if (confirmation !== PRODUCTION_CONFIRMATION) {
    emitLog("Produção bloqueada: confirmação textual inválida.");
    return;
  }
  callBackend("run_production_confirmed", confirmation);
}

export function openReport(name: string): void {
  if (name === "pipeline") {
    callBackend("open_pipeline_report");
    return;
  }
  if (name === "processing") {
    callBackend("open_processing_report");
    return;
  }
  callBackend("open_reports_folder");
}

export function openLogsFolder(): void {
  callBackend("open_reports_folder");
}

export function openWorkbook(): void {
  callBackend("open_workbook");
}

export function cleanupDryRun(): void {
  callBackend("run_cleanup_dry_run");
}

function requestInitialData(): void {
  if (backend) {
    callBackend("refresh_dashboard");
    return;
  }
  callBackendWithResult("get_dashboard_data", (payload) => {
    emit("desktop-dashboard", parsePayload(payload));
  });
  callBackendWithResult("get_recent_protocols", (payload) => {
    emit("desktop-protocols", parsePayload(payload).protocols ?? []);
  });
}

function connectSignals(): void {
  connect("statusChanged", emitLog);
  connect("logMessage", emitLog);
  connect("operationFailed", emitLog);
  connect("progressChanged", (payload) => emit("desktop-progress", payload));
  connect("dashboardChanged", (payload) => emit("desktop-dashboard", payload));
  connect("protocolsChanged", (payload) => emit("desktop-protocols", payload));
  connect("operationStarted", () => emitLog("Operação iniciada."));
  connect("operationFinished", () => emitLog("Operação concluída."));
}

function connect(signalName: string, callback: (...args: unknown[]) => void): void {
  const signal = backend?.[signalName];
  if (signal && typeof signal === "object" && "connect" in signal && typeof signal.connect === "function") {
    signal.connect(callback);
  }
}

function callBackend(method: string, ...args: unknown[]): void {
  const target = backend ?? window.backend;
  const callable = target?.[method];
  if (typeof callable === "function") {
    callable(...args);
    return;
  }
  emitLog(`Ação visual preparada: ${method}`);
}

function callBackendWithResult(method: string, callback: (payload: unknown) => void): void {
  const target = backend ?? window.backend;
  const callable = target?.[method];
  if (typeof callable === "function") {
    callable(callback);
  }
}

function createMockBackend(): Window["backend"] {
  return {
    get_dashboard_data: (callback?: unknown) => {
      if (typeof callback === "function") {
        callback({
          system_status: "Pronto",
          cdp_status: "Não testado",
          mode: "Simulação",
          batch_label: "10 protocolos"
        });
      }
    },
    get_recent_protocols: (callback?: unknown) => {
      if (typeof callback === "function") {
        callback({ protocols: [] });
      }
    },
    refresh_dashboard: () => {
      emit("desktop-dashboard", {
        system_status: "Pronto",
        cdp_status: "Não testado",
        mode: "Simulação",
        batch_label: "10 protocolos"
      });
      emit("desktop-protocols", []);
    },
    check_environment: () => emitLog("Ambiente visual pronto."),
    open_edge_cdp: () => emitLog("Abra o Edge manualmente com CDP e faça login no Portal GD."),
    test_cdp_connection: () => emitLog("Teste CDP preparado para QWebChannel."),
    inspect_portal: () => emitLog("Inspeção visual será conectada em etapa posterior."),
    run_dry_run: () => emitLog("Simulação preparada para bridge real."),
    run_production_confirmed: () => emitLog("Produção protegida por confirmação."),
    open_pipeline_report: () => emitLog("Abertura do relatório pipeline preparada."),
    open_processing_report: () => emitLog("Abertura do relatório processamento preparada."),
    open_reports_folder: () => emitLog("Abertura de pasta de logs preparada."),
    open_workbook: () => emitLog("Abertura de planilha preparada."),
    run_cleanup_dry_run: () => emitLog("Cleanup dry-run preparado.")
  };
}

function parsePayload(payload: unknown): Record<string, unknown> {
  if (typeof payload === "string") {
    try {
      return JSON.parse(payload) as Record<string, unknown>;
    } catch (_error) {
      return {};
    }
  }
  if (payload && typeof payload === "object") {
    return payload as Record<string, unknown>;
  }
  return {};
}

function emitLog(message: unknown): void {
  emit("desktop-log", String(message ?? ""));
}

function emit(name: string, detail: unknown): void {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}
