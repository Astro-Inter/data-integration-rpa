import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Client } from "pg";
import { iterateLegacyBatches } from "./repositories.ts";
import { userFingerprint } from "./domain.ts";
import { PostgresSession } from "./hyperdrive.ts";

const url = process.env.TEST_POSTGRES_URL?.replace("postgresql+psycopg://", "postgresql://");

test("PostgreSQL decrypts paginated CPF in read-only mode and preserves fingerprints", { skip: !url }, async () => {
  const admin = new Client({ connectionString: url });
  const reader = new Client({ connectionString: url });
  const schema = "rpa_worker_test_" + randomUUID().replaceAll("-", "");
  const key = "chave-pgp-teste";
  await admin.connect();
  let created = false;
  try {
    await admin.query("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public");
    await admin.query(`CREATE SCHEMA "${schema}"`);
    created = true;
    await admin.query(`SET search_path TO "${schema}", public`);
    await admin.query("CREATE TABLE empresa (id_empresa INT PRIMARY KEY, nome TEXT, cnpj TEXT)");
    await admin.query("CREATE TABLE departamento (id_departamento INT PRIMARY KEY, nome TEXT, id_empresa INT)");
    await admin.query("CREATE TABLE funcionario (id_funcionario INT PRIMARY KEY, cpf BYTEA, nome TEXT, cargo TEXT, id_empresa INT, id_departamento INT, email TEXT)");
    await admin.query("INSERT INTO empresa VALUES (1, 'Empresa', '11222333000181')");
    await admin.query("INSERT INTO departamento VALUES (1, 'Operação', 1)");
    await admin.query(`INSERT INTO funcionario VALUES
      (1, pgp_sym_encrypt('01234567890', $1::text), 'Ana', 'Analista', 1, 1, 'ana@example.com'),
      (2, pgp_sym_encrypt('11144477735', $1::text), 'Bruno', 'Analista', 1, 1, NULL)`, [key]);
    await reader.connect();
    await reader.query(`SET search_path TO "${schema}", public`);
    const database = new PostgresSession(reader);
    const scan = async (cpfKey: string) => {
      await database.begin(true);
      try {
        const users = [];
        for await (const batch of iterateLegacyBatches(database, 1, cpfKey)) users.push(...batch);
        return users;
      } finally { await database.rollback(); }
    };
    const users = await scan(key);
    assert.deepEqual(users.map((user) => user.cpf), ["01234567890", "11144477735"]);
    assert.equal(users[0].emails[0].email, "ana@example.com");
    assert.equal(users[1].emails[0].email, null);
    await assert.rejects(scan(""), /LEGACY_CPF_KEY/);
    await assert.rejects(scan("chave-incorreta"), (error: Error) => {
      assert.ok(!error.message.includes("chave-incorreta"));
      assert.ok(!error.message.includes("01234567890"));
      return true;
    });
    await admin.query("UPDATE funcionario SET cpf=pgp_sym_encrypt(pgp_sym_decrypt(cpf, $1::text), $1::text)", [key]);
    const reencrypted = await scan(key);
    assert.equal(await userFingerprint(reencrypted[0]), await userFingerprint(users[0]));
    // Mesmo esquema de e-mail, agora com CPF textual; não exige chave nem pgp_sym_decrypt.
    await admin.query("ALTER TABLE funcionario ALTER COLUMN cpf TYPE TEXT USING pgp_sym_decrypt(cpf, 'chave-pgp-teste')");
    assert.deepEqual((await scan("")).map((user) => user.cpf), users.map((user) => user.cpf));
  } finally {
    await reader.end();
    if (created) await admin.query(`DROP SCHEMA "${schema}" CASCADE`);
    await admin.end();
  }
});
