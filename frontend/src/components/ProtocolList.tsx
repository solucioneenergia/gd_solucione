import type { ProtocolResult } from "../api/qtBridge";

export function ProtocolList({ protocols }: { protocols: ProtocolResult[] }) {
  return (
    <section className="table-card glass">
      <header>
        <div>
          <span className="eyebrow">Resultados recentes</span>
          <h2>Protocolos</h2>
        </div>
        <span>{protocols.length} item(ns)</span>
      </header>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Protocolo</th>
              <th>Cliente</th>
              <th>PDF</th>
              <th>Excel</th>
              <th>Arquivo</th>
              <th>Erro</th>
            </tr>
          </thead>
          <tbody>
            {protocols.length === 0 ? (
              <tr><td colSpan={6} className="empty">Nenhum protocolo processado nesta sessão.</td></tr>
            ) : (
              protocols.map((item, index) => (
                <tr key={`${item.protocol}-${index}`}>
                  <td className="protocol">{item.protocol}</td>
                  <td>{item.client}</td>
                  <td>{item.pdf_status}</td>
                  <td>{item.excel_status}</td>
                  <td>{item.archive_status}</td>
                  <td className={item.error ? "error-text" : ""}>{item.error || "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
