import assert from "node:assert/strict";
import { test } from "node:test";
import { prepareUser, type LegacyUser } from "./domain.ts";
import { TargetIdentityError, TargetUserRepository } from "./target-user-repository.ts";
import type { SqlClient } from "./repositories.ts";

const source: LegacyUser = {
  id_funcionario: 4, cpf: "52998224725", nome: "Ana", cargo: "Analista",
  id_empresa: 1, empresa_nome: "Empresa A", empresa_cnpj: "11222333000181",
  id_departamento: 10, departamento_nome: "Operação", departamento_id_empresa: 1,
  emails: [{ id_email: 1, email: "ana@example.com" }],
};

class TargetDatabase implements SqlClient {
  calls: { sql: string; parameters: unknown[] }[] = [];
  conflictAdmin = false;
  async query<Row extends Record<string, unknown>>(sql: string, parameters: unknown[] = []) {
    this.calls.push({ sql, parameters });
    let rows: Record<string, unknown>[] = [];
    if (sql.includes("FROM admin")) rows = this.conflictAdmin ? [{ one: 1 }] : [];
    else if (sql.includes("RETURNING id_workspace")) rows = [{ id_workspace: 9 }];
    else if (sql.includes("AS id FROM cargos")) rows = [{ id: 17 }];
    else if (sql.includes("AS id FROM unidades")) rows = [{ id: 23 }];
    else if (sql.includes("RETURNING id_usuario")) rows = [{ id_usuario: 101 }];
    return { rows: rows as Row[] };
  }
}

test("persists a new user, relations, and legacy link with parameterized statements", async () => {
  const db = new TargetDatabase();
  const repo = new TargetUserRepository(db);
  const prepared = prepareUser(source);
  assert.equal(await repo.resolveIdentity(prepared), null);
  await repo.persist(prepared, "firebase-uid");
  assert.ok(db.calls.some((call) => call.sql.includes("INSERT INTO workspaces")));
  assert.ok(db.calls.some((call) => call.sql.includes("INSERT INTO cargos")));
  assert.ok(db.calls.some((call) => call.sql.includes("INSERT INTO unidades")));
  const insertedUser = db.calls.find((call) => call.sql.includes("INSERT INTO usuarios"));
  assert.deepEqual(insertedUser?.parameters, [
    "Ana", "ana@example.com", "52998224725", "firebase-uid", 17, 23, "COLABORADOR", "PRE_CADASTRADO",
  ]);
  const link = db.calls.find((call) => call.sql.includes("INSERT INTO rpa_user_links"));
  assert.deepEqual(link?.parameters, ["usuarios_legado", 4, 101, "firebase-uid"]);
  assert.ok(db.calls.every(({ sql }) => !sql.includes("Ana") && !sql.includes("ana@example.com")));
});

test("blocks administrator identity conflicts before touching destination user tables", async () => {
  const db = new TargetDatabase();
  db.conflictAdmin = true;
  const repo = new TargetUserRepository(db);
  await assert.rejects(repo.resolveIdentity(prepareUser(source)), TargetIdentityError);
  assert.ok(!db.calls.some((call) => call.sql.includes("FROM usuarios")));
});
