import assert from "node:assert/strict";
import { test } from "node:test";
import { FirebaseAuthClient } from "./firebase-auth.ts";
import { runUserSync } from "./sync-runner.ts";
import type { SqlClient } from "./repositories.ts";

const row = {
  id_funcionario: 1, cpf: "52998224725", nome: "Ana", cargo: "Analista", id_empresa: 1,
  empresa_nome: "Empresa A", empresa_cnpj: "11222333000181", id_departamento: 10,
  departamento_nome: "Operação", departamento_id_empresa: 1,
};

class LegacyDatabase implements SqlClient {
  async query<Row extends Record<string, unknown>>(sql: string): Promise<{ rows: Row[] }> {
    if (sql.includes("MAX(id_funcionario)")) return { rows: [{ upper_id: 1 } as Row] };
    if (sql.includes("FROM funcionario AS f")) return { rows: [row as Row] };
    if (sql.includes("FROM email")) return { rows: [{ id_funcionario: 1, id_email: 1, email: "ana@example.com" } as Row] };
    return { rows: [] };
  }
}

class DestinationDatabase implements SqlClient {
  calls: string[] = [];
  async query<Row extends Record<string, unknown>>(sql: string): Promise<{ rows: Row[] }> {
    this.calls.push(sql);
    if (sql.includes("SELECT last_synced_at")) return { rows: [] };
    if (sql.includes("SELECT legacy_id, data_hash")) return { rows: [] };
    if (sql.includes("RETURNING id_workspace")) return { rows: [{ id_workspace: 9 } as Row] };
    if (sql.includes("AS id FROM cargos")) return { rows: [{ id: 17 } as Row] };
    if (sql.includes("AS id FROM unidades")) return { rows: [{ id: 23 } as Row] };
    if (sql.includes("RETURNING id_usuario")) return { rows: [{ id_usuario: 101 } as Row] };
    return { rows: [] };
  }
}

test("dry-run scans and validates without lock, Firebase, or destination writes", async () => {
  const destination = new DestinationDatabase();
  let firebaseCalls = 0;
  const firebase = { ensureUser: async () => { firebaseCalls++; throw new Error("must not run"); } } as unknown as FirebaseAuthClient;
  const result = await runUserSync({
    legacy: new LegacyDatabase(), destination, firebase, batchSize: 20, dryRun: true,
  });
  assert.deepEqual([result.total, result.new, result.validated, result.confirmed], [1, 1, 1, 0]);
  assert.equal(result.last_synced_at, null);
  assert.equal(firebaseCalls, 0);
  assert.ok(!destination.calls.some((sql) => /INSERT|UPDATE|DELETE/u.test(sql)));
});

test("real sync confirms each changed user only after Firebase and database persistence", async () => {
  const destination = new DestinationDatabase();
  const order: string[] = [];
  const firebase = {
    ensureUser: async () => { order.push("firebase"); return { uid: "real-uid", outcome: "created", disabled: false }; },
  } as unknown as FirebaseAuthClient;
  const db = destination as DestinationDatabase & { query: SqlClient["query"] };
  const originalQuery = db.query.bind(db);
  db.query = async (sql: string, parameters?: unknown[]) => {
    if (sql.includes("INSERT INTO usuarios")) order.push("persist");
    if (sql.includes("INSERT INTO rpa_sync_users")) order.push("confirm");
    return originalQuery(sql, parameters);
  };
  const result = await runUserSync({
    legacy: new LegacyDatabase(), destination: db, firebase, batchSize: 20, dryRun: false,
  });
  assert.deepEqual(order, ["firebase", "persist", "confirm"]);
  assert.equal(result.confirmed, 1);
  assert.ok(destination.calls.some((sql) => sql.includes("UPDATE rpa_sync_control")));
});
