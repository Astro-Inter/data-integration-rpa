import assert from "node:assert/strict";
import { test } from "node:test";
import { INTEGRATION_KEY, legacyBatch, legacyUpperId, SyncStateRepository, type SqlClient } from "./repositories.ts";

class FakeDatabase implements SqlClient {
  calls: { sql: string; parameters: unknown[] }[] = [];
  responses: Record<string, Record<string, unknown>[]> = {};
  async query<Row extends Record<string, unknown>>(sql: string, parameters: unknown[] = []) {
    this.calls.push({ sql, parameters });
    const key = sql.includes("FROM pg_attribute") ? "layout"
      : sql.includes("MAX(id_funcionario)") ? "max"
      : sql.includes("FROM funcionario AS f") ? "users"
      : sql.includes("FROM email") ? "emails"
      : sql.includes("last_synced_at") ? "control"
      : sql.includes("data_hash") ? "hashes"
      : "other";
    return { rows: (this.responses[key] ?? (key === "layout" ? [{ column_name: "cpf", encrypted: false }] : [])) as Row[] };
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
  assert.deepEqual(db.calls[2].parameters, [2, 8, 50]);
  assert.deepEqual(db.calls[3].parameters, [[3]]);
  assert.match(db.calls[3].sql, /ORDER BY id_funcionario, id_email/);
});

test("encrypted CPF uses bound key on both pages and inline email needs no email table", async () => {
  for (const lastId of [null, 2]) {
    const db = new FakeDatabase();
    db.responses.layout = [{ column_name: "cpf", encrypted: true }, { column_name: "email", encrypted: false }];
    db.responses.users = [{ id_funcionario: 3, cpf: "01234567890", funcionario_email: "ana@example.com" }];
    const result = await legacyBatch(db, { lastId, upperId: 8, batchSize: 1 }, "chave-teste");
    const page = db.calls[1];
    assert.match(page.sql, /pgp_sym_decrypt\(f.cpf, \$[34]::text\) AS cpf/);
    assert.ok(!page.sql.includes("chave-teste"));
    assert.deepEqual(page.parameters, lastId === null ? [8, 1, "chave-teste"] : [2, 8, 1, "chave-teste"]);
    assert.equal(result[0].cpf, "01234567890");
    assert.deepEqual(result[0].emails, [{ id_email: 3, email: "ana@example.com" }]);
    assert.equal(db.calls.length, 2);
  }
});

test("missing key fails before encrypted rows are read", async () => {
  const db = new FakeDatabase();
  db.responses.layout = [{ column_name: "cpf", encrypted: true }];
  await assert.rejects(legacyBatch(db, { lastId: null, upperId: 8, batchSize: 1 }), /LEGACY_CPF_KEY/);
  assert.equal(db.calls.length, 1);
});

test("decryption errors never expose database parameters", async () => {
  const db = new FakeDatabase();
  db.responses.layout = [{ column_name: "cpf", encrypted: true }];
  const query = db.query.bind(db);
  db.query = async (sql, parameters) => {
    if (sql.includes("pgp_sym_decrypt")) throw new Error("SQL chave-teste 01234567890");
    return query(sql, parameters);
  };
  await assert.rejects(legacyBatch(db, { lastId: null, upperId: 8, batchSize: 1 }, "chave-teste"), (error: Error) => {
    assert.ok(!error.message.includes("chave-teste"));
    assert.ok(!error.message.includes("01234567890"));
    return true;
  });
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
