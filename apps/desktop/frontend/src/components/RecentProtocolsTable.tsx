import { Badge } from "./Badge";
import { recentProtocols } from "../data/mockDashboard";

export type RecentProtocol = {
  protocol: string;
  client: string;
  pdf: string;
  excel: string;
  archive: string;
  status: string;
};

export function RecentProtocolsTable({ protocols }: { protocols?: RecentProtocol[] }) {
  const rows = protocols && protocols.length > 0 ? protocols : recentProtocols;

  return (
    <section className="panel protocols-panel">
      <h2>Protocolos recentes</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Protocolo</th>
              <th>Cliente</th>
              <th>PDF</th>
              <th>Excel</th>
              <th>Arquivo</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.protocol}>
                <td>{row.protocol}</td>
                <td>{row.client}</td>
                <td>{row.pdf}</td>
                <td>{row.excel}</td>
                <td>{row.archive}</td>
                <td><Badge>{row.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
