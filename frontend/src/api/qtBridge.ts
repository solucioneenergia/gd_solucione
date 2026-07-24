export type QtSignal<T = unknown> = {
  connect(callback: (value: T) => void): void;
};

export type OperationalStatus =
  | "idle"
  | "cdp_connected"
  | "reading_portal"
  | "downloading"
  | "processing_pdf"
  | "updating_excel"
  | "archiving"
  | "completed"
  | "error";

export type InitialState = {
  mode: "simulation" | "production";
  cdp_mode: boolean;
  cdp_endpoint: string;
  max_portal_pages: number;
  max_completed_to_process: number;
  enable_portal_pagination: boolean;
  skip_already_completed: boolean;
  apply_excel: boolean;
  apply_archive: boolean;
  backup_excel: boolean;
  busy: boolean;
};

export type TemporarySettings = {
  MAX_PORTAL_PAGES: number;
  MAX_COMPLETED_TO_PROCESS: number;
  DRY_RUN: boolean;
  ENABLE_PORTAL_PAGINATION: boolean;
  SKIP_ALREADY_COMPLETED: boolean;
  APPLY_EXCEL: boolean;
  APPLY_ARCHIVE: boolean;
  BACKUP_EXCEL: boolean;
};

export type ProductionConfirmation = {
  DRY_RUN: false;
  PLANILHA_PATH: string;
  CLIENTES_ROOT: string;
  APPLY_EXCEL: boolean;
  APPLY_ARCHIVE: boolean;
  BACKUP_EXCEL: boolean;
  MAX_PORTAL_PAGES: number;
  MAX_COMPLETED_TO_PROCESS: number;
};

export type PipelineSummary = Record<string, string | number | boolean>;

export type ProtocolResult = {
  protocol: string;
  client: string;
  pdf_status: string;
  excel_status: string;
  archive_status: string;
  error: string;
};

type RawBackend = {
  statusChanged: QtSignal<OperationalStatus>;
  progressChanged: QtSignal<number>;
  operationStarted: QtSignal<string>;
  operationFinished: QtSignal<string>;
  operationFailed: QtSignal<string>;
  logMessage: QtSignal<string>;
  summaryChanged: QtSignal<PipelineSummary>;
  protocolUpdated: QtSignal<ProtocolResult>;
  get_initial_state(callback: (state: InitialState) => void): void;
  get_production_confirmation(
    callback: (state: ProductionConfirmation) => void,
  ): void;
  apply_temporary_settings(payload: string, callback: (ok: boolean) => void): void;
  check_environment(): void;
  open_edge_cdp(): void;
  test_cdp_connection(): void;
  inspect_portal(): void;
  run_pipeline_dry_run(): void;
  run_pipeline_production(confirmation: string): void;
  stop_current_operation(): void;
  open_pipeline_report(): void;
  open_processing_report(): void;
  open_downloads_folder(): void;
  open_logs_folder(): void;
  open_workbook(): void;
  open_clients_root(): void;
};

export type BackendApi = {
  statusChanged: QtSignal<OperationalStatus>;
  progressChanged: QtSignal<number>;
  operationStarted: QtSignal<string>;
  operationFinished: QtSignal<string>;
  operationFailed: QtSignal<string>;
  logMessage: QtSignal<string>;
  summaryChanged: QtSignal<PipelineSummary>;
  protocolUpdated: QtSignal<ProtocolResult>;
  getInitialState(): Promise<InitialState>;
  getProductionConfirmation(): Promise<ProductionConfirmation>;
  applyTemporarySettings(settings: TemporarySettings): Promise<boolean>;
  checkEnvironment(): void;
  openEdgeCdp(): void;
  testCdpConnection(): void;
  inspectPortal(): void;
  runPipelineDryRun(): void;
  runPipelineProduction(confirmation: string): void;
  stopCurrentOperation(): void;
  openPipelineReport(): void;
  openProcessingReport(): void;
  openDownloadsFolder(): void;
  openLogsFolder(): void;
  openWorkbook(): void;
  openClientsRoot(): void;
};

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (
      transport: unknown,
      callback: (channel: { objects: { backend: RawBackend } }) => void,
    ) => unknown;
    backend?: BackendApi;
  }
}

function promiseResult<T>(
  invoke: (callback: (value: T) => void) => void,
): Promise<T> {
  return new Promise((resolve) => invoke(resolve));
}

function wrapBackend(raw: RawBackend): BackendApi {
  return {
    statusChanged: raw.statusChanged,
    progressChanged: raw.progressChanged,
    operationStarted: raw.operationStarted,
    operationFinished: raw.operationFinished,
    operationFailed: raw.operationFailed,
    logMessage: raw.logMessage,
    summaryChanged: raw.summaryChanged,
    protocolUpdated: raw.protocolUpdated,
    getInitialState: () =>
      promiseResult<InitialState>((callback) => raw.get_initial_state(callback)),
    getProductionConfirmation: () =>
      promiseResult<ProductionConfirmation>((callback) =>
        raw.get_production_confirmation(callback),
      ),
    applyTemporarySettings: (settings) =>
      promiseResult<boolean>((callback) =>
        raw.apply_temporary_settings(JSON.stringify(settings), callback),
      ),
    checkEnvironment: () => raw.check_environment(),
    openEdgeCdp: () => raw.open_edge_cdp(),
    testCdpConnection: () => raw.test_cdp_connection(),
    inspectPortal: () => raw.inspect_portal(),
    runPipelineDryRun: () => raw.run_pipeline_dry_run(),
    runPipelineProduction: (confirmation) =>
      raw.run_pipeline_production(confirmation),
    stopCurrentOperation: () => raw.stop_current_operation(),
    openPipelineReport: () => raw.open_pipeline_report(),
    openProcessingReport: () => raw.open_processing_report(),
    openDownloadsFolder: () => raw.open_downloads_folder(),
    openLogsFolder: () => raw.open_logs_folder(),
    openWorkbook: () => raw.open_workbook(),
    openClientsRoot: () => raw.open_clients_root(),
  };
}

export function connectQtBridge(): Promise<BackendApi | null> {
  if (!window.qt?.webChannelTransport || !window.QWebChannel) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    new window.QWebChannel!(window.qt!.webChannelTransport, (channel) => {
      const backend = wrapBackend(channel.objects.backend);
      window.backend = backend;
      resolve(backend);
    });
  });
}
