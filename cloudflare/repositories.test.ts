import assert from "node:assert/strict";
import { test } from "node:test";
import { INTEGRATION_KEY, legacyBatch, legacyUpperId, SyncStateRepository, type SqlClient } from "./repositories.ts";

class FakeDatabase implements SqlClient {
  calls: { sql: string; parameters: unknown[] }[] = [];
  responses: Record<string, Record<string, unknown>[]> = {};
  async query<Row extends Record<string, unknown>>(sql: string, parameters: unknown[] = []) {
    this.calls.push({ sql, parameters });
    const key = sql.includes("MAX(id_funcionario)") ? "max"
      : sql.includes("FROM funcionario AS f") ? "users"
      : sql.includes("FROM email") ? "emails"
      : sql.includes("last_synced_at") ? "control"
      : sql.includes("data_hash") ? "hashes"
      : "other";
    return { rows: (this.responses[key] ?? []) as Row[] };
  }
}

test("legacy batching bounds the scan, joins related rows, and keeps email ordering", async () => {
  const db = new FakeDatabase();
  db.responses.users = [{
    id_funcionario: 3, cpf: "52998224725", nome: "Ana", cargo: "Analista", id_empresa: 1,
    empresa_nome: "Empresa A", empresa_cnpj: "11222333000181", id_departamento: 10,
    departamento_nome: "Operação", departamento_id_empresa: 1,
  }];
  db.responses.emails = [
    { id_funcionario: 3, id_email: 1, email: "ana@example.com" },
    { id_funcionario: 3, id_email: 2, email: null },
  ];
  db.responses.max = [{ upper_id: 8 }];
  assert.equal(await legacyUpperId(db), 8);
  const batch = await legacyBatch(db, { lastId: 2, upperId: 8, batchSize: 50 });
  assert.deepEqual(batch[0].emails, [
    { id_email: 1, email: "ana@example.com" }, { id_email: 2, email: null },
  ]);
  assert.deepEqual(db.calls[1].parameters, [2, 8, 50]);
  assert.deepEqual(db.calls[2].parameters, [[3]]);
  assert.match(db.calls[2].sql, /ORDER BY id_funcionario, id_email/);
});

test("sync state uses the stable integration key and parameterized writes", async () => {
  const db = new FakeDatabase();
  db.responses.control = [{ last_synced_at: "2026-01-01T00:00:00Z" }];
  db.responses.hashes = [{ legacy_id: 4, data_hash: "a".repeat(64) }];
  const state = new SyncStateRepository(db);
  assert.equal(await state.lastSyncedAt(), "2026-01-01T00:00:00Z");
  assert.deepEqual(await state.confirmedHashes([]), new Map());
  assert.deepEqual(await state.confirmedHashes([4]), new Map([[4, "a".repeat(64)]]));
  await state.lockSync();
  await state.confirmUser(4, "b".repeat(64), "2026-01-02T00:00:00Z");
  await state.confirmRun("2026-01-02T00:00:00Z");
  assert.equal(db.calls[0].parameters[0], INTEGRATION_KEY);
  assert.equal(db.calls[1].parameters[0], INTEGRATION_KEY);
});
