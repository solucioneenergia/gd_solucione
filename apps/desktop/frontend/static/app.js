(function () {
  const confirmationText = "SIM, EXECUTAR PRODUÇÃO";
  let backend = null;
  let running = false;

  function text(selector, value) {
    const element = document.querySelector(selector);
    if (element && value !== undefined && value !== null && value !== "") {
      element.textContent = String(value);
    }
  }

  function setMessage(message) {
    text('[data-field="activity-message"]', "◷ " + String(message || "Aguardando início da automação"));
  }

  function setRunning(nextRunning) {
    running = Boolean(nextRunning);
    document.querySelectorAll("[data-action]").forEach(function (button) {
      const action = button.getAttribute("data-action");
      if (action === "request-production" || action === "run-dry-run") {
        button.disabled = running;
      }
    });
  }

  function parsePayload(payload) {
    if (!payload) {
      return {};
    }
    if (typeof payload === "string") {
      try {
        return JSON.parse(payload);
      } catch (_error) {
        return {};
      }
    }
    return payload;
  }

  function callBackend(methodName, args, onResult) {
    const callable = backend && backend[methodName];
    if (typeof callable === "function") {
      if (typeof onResult === "function") {
        callable.apply(backend, (args || []).concat(onResult));
      } else {
        callable.apply(backend, args || []);
      }
      return;
    }
    setMessage("Ação visual preparada. Bridge indisponível no modo atual.");
  }

  function applyDashboard(payload) {
    const data = parsePayload(payload);
    text('[data-field="system-status"]', data.system_status);
    text('[data-field="cdp-status"]', data.cdp_status);
    text('[data-field="mode"]', data.mode);
    text('[data-field="batch-label"]', data.batch_label);
    text('[data-field="max-pages"]', data.max_portal_pages);
    text('[data-field="max-protocols"]', data.max_completed_to_process);
  }

  function applyProtocols(payload) {
    const data = parsePayload(payload);
    const protocols = Array.isArray(data) ? data : data.protocols;
    const body = document.querySelector('[data-field="protocols-body"]');
    if (!body || !Array.isArray(protocols)) {
      return;
    }
    if (protocols.length === 0) {
      body.innerHTML = '<tr><td colspan="6">Nenhum protocolo recente encontrado.</td></tr>';
      return;
    }
    body.innerHTML = protocols.slice(0, 10).map(function (row) {
      const status = row.status || "OK";
      return [
        "<tr>",
        "<td>", escapeHtml(row.protocol || ""), "</td>",
        "<td>", escapeHtml(row.client || ""), "</td>",
        "<td>", escapeHtml(row.pdf || ""), "</td>",
        "<td>", escapeHtml(row.excel || ""), "</td>",
        "<td>", escapeHtml(row.archive || ""), "</td>",
        '<td><span class="ok-badge">', escapeHtml(status), "</span></td>",
        "</tr>",
      ].join("");
    }).join("");
    document.querySelector(".table-wrap").style.overflowY = protocols.length > 4 ? "auto" : "visible";
    document.querySelector(".table-wrap").style.maxHeight = protocols.length > 4 ? "166px" : "none";
  }

  function applyProgress(payload) {
    const data = parsePayload(payload);
    const percent = Number(data.overall_percent || 0);
    text('[data-field="overall-progress"]', Math.max(0, Math.min(100, percent)) + "%");
    setMessage(data.message || data.stage || "Processando.");
    setActiveProcess(data.stage || "");
  }

  function setActiveProcess(stage) {
    const normalized = String(stage).toLowerCase();
    document.querySelectorAll(".process-node").forEach(function (node) {
      node.classList.remove("active", "error");
    });
    const mapping = [
      ["portal", ["preflight", "cdp_connection", "portal_read", "protocol_selection"]],
      ["download", ["download", "download_or_reuse_pdf"]],
      ["pdf", ["parse_pdf", "pdf"]],
      ["planilha", ["update_excel", "excel"]],
      ["arquivo", ["archive_pdf", "archive"]],
    ];
    mapping.forEach(function (entry) {
      if (entry[1].some(function (token) { return normalized.indexOf(token) >= 0; })) {
        const node = document.querySelector(".process-node." + entry[0]);
        if (node) {
          node.classList.add("active");
        }
      }
    });
  }

  function requestProduction() {
    const typed = window.prompt(
      "A execução em produção pode alterar a planilha e arquivar PDFs.\n\n" +
        "Checklist:\n- Edge CDP aberto;\n- login manual realizado;\n- planilha fechada;\n- BACKUP_EXCEL=true;\n- primeira execução recomendada com 1 protocolo.\n\n" +
        "Digite exatamente: " + confirmationText
    );
    if (typed !== confirmationText) {
      setMessage("Produção bloqueada: confirmação textual não informada.");
      return;
    }
    callBackend("run_production_confirmed", [typed]);
  }

  function bindActions() {
    const actions = {
      "open-edge-cdp": function () {
        callBackend("open_edge_cdp");
      },
      "test-cdp": function () {
        callBackend("test_cdp_connection");
      },
      "inspect-portal": function () {
        callBackend("inspect_portal");
      },
      "run-dry-run": function () {
        callBackend("run_dry_run");
      },
      "request-production": requestProduction,
      "open-pipeline-report": function () {
        callBackend("open_pipeline_report");
      },
      "open-processing-report": function () {
        callBackend("open_processing_report");
      },
      "open-logs": function () {
        callBackend("open_reports_folder");
      },
      "open-workbook": function () {
        callBackend("open_workbook");
      },
      "cleanup-dry-run": function () {
        callBackend("run_cleanup_dry_run");
      },
    };

    document.querySelectorAll("[data-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        const action = button.getAttribute("data-action");
        if (actions[action]) {
          actions[action]();
        }
      });
    });
  }

  function connectSignal(signal, callback) {
    if (signal && typeof signal.connect === "function") {
      signal.connect(callback);
    }
  }

  function setupChannel() {
    if (typeof QWebChannel !== "function" || !window.qt || !window.qt.webChannelTransport) {
      bindActions();
      setMessage("Aguardando início da automação");
      return;
    }

    new QWebChannel(window.qt.webChannelTransport, function (channel) {
      backend = channel.objects.backend || null;
      bindActions();
      connectSignal(backend.statusChanged, setMessage);
      connectSignal(backend.logMessage, setMessage);
      connectSignal(backend.operationStarted, function () {
        setRunning(true);
        setMessage("Operação iniciada.");
      });
      connectSignal(backend.operationFinished, function () {
        setRunning(false);
        setMessage("Operação concluída.");
      });
      connectSignal(backend.operationFailed, function (message) {
        setRunning(false);
        setMessage(message);
      });
      connectSignal(backend.progressChanged, applyProgress);
      connectSignal(backend.dashboardChanged, applyDashboard);
      connectSignal(backend.protocolsChanged, applyProtocols);
      connectSignal(backend.summaryChanged, function (payload) {
        const protocols = parsePayload(payload).protocols || parsePayload(payload).protocol_results || [];
        if (protocols.length) {
          applyProtocols({ protocols: protocols });
        }
      });
      window.setTimeout(function () {
        callBackend("refresh_dashboard");
      }, 100);
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupChannel);
  } else {
    setupChannel();
  }
})();
