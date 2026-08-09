import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";


const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (relative) => readFileSync(join(root, relative), "utf8");


test("runtime renders an empty state without bundled protocol records", () => {
  const table = read("src/components/RecentProtocolsTable.tsx");
  const staticIndex = read("static/index.html");

  assert.match(table, /Nenhum protocolo recente encontrado/);
  assert.doesNotMatch(table, /recentProtocols/);
  assert.match(staticIndex, /Nenhum protocolo recente encontrado/);
  assert.doesNotMatch(table + staticIndex, /(?<!\d)\d{8,}(?!\d)/);
});


test("unimplemented actions are disabled", () => {
  const panel = read("src/components/PortalExecutionPanel.tsx");
  const staticIndex = read("static/index.html");

  assert.match(panel, /disabled title="Indisponível nesta versão"/);
  assert.doesNotMatch(staticIndex, /data-action="open-edge-cdp"/);
  assert.doesNotMatch(staticIndex, /data-action="inspect-portal"/);
});


test("production confirmation comes from the backend contract", () => {
  const bridge = read("src/bridge/qtBridge.ts");
  const staticScript = read("static/app.js");

  assert.match(bridge, /get_production_confirmation/);
  assert.match(staticScript, /get_production_confirmation/);
  assert.doesNotMatch(bridge + staticScript, /const confirmationText/);
  assert.match(read("src/App.tsx"), /qt-channel-ready/);
  assert.match(staticScript, /qt-channel-ready/);
});
