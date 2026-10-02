import assert from "node:assert/strict";
import { test } from "node:test";
import {
  firebasePayload, normalizeDocument, normalizeEmail, organizationPayload, prepareUser,
  targetInsertPayload, targetUpdatePayload, UserDataError, userFingerprint, workspacePayload, type LegacyUser,
} from "./domain.ts";

const sample: LegacyUser = {
  id_funcionario: 1, cpf: "52998224725", nome: "Ana", cargo: "Analista",
  id_empresa: 1, empresa_nome: "Empresa A", empresa_cnpj: "11222333000181",
  id_departamento: 10, departamento_nome: "Operação", departamento_id_empresa: 1,
  emails: [{ id_email: 1, email: "ana@example.com" }, { id_email: 2, email: null }],
};

test("normalizes and validates CPF/CNPJ with ASCII digits and checksums", () => {
  assert.equal(normalizeDocument("012.345.678-90", "cpf"), "01234567890");
  assert.equal(normalizeDocument("04.252.011/0001-10", "cnpj"), "04252011000110");
  for (const [value, kind] of [["11111111111", "cpf"], ["52998224724", "cpf"], ["00000000000000", "cnpj"]] as const) {
    assert.throws(() => normalizeDocument(value, kind));
  }
  assert.throws(() => normalizeDocument("５２９９８２２４７２５", "cpf"));
});

test("prepares the same normalized user payload and rejects ambiguous emails", () => {
  const prepared = prepareUser({
    ...sample,
    nome: "  Ana\tD'Ávila  ", cargo: "  TI   Sênior ", empresa_nome: "  ACME   SA  ",
    departamento_nome: "  Operação   Sul ", cpf: "529.982.247-25", empresa_cnpj: "11.222.333/0001-81",
    emails: [{ id_email: 1, email: "  ANA@EXAMPLE.COM " }, { id_email: 2, email: "ana@example.com" }],
  });
  assert.deepEqual(prepared.usuario, { nome: "Ana D'Ávila", email: "ana@example.com", cpf: "52998224725" });
  assert.deepEqual(prepared.workspace, { legacy_empresa_id: 1, nome: "ACME SA", cnpj: "11222333000181" });
  assert.deepEqual(prepared.unidade, { legacy_departamento_id: 10, nome: "Operação Sul", ativo: true });
  assert.throws(() => prepareUser({
    ...sample, emails: [{ id_email: 1, email: "ana@example.com" }, { id_email: 2, email: "other@example.com" }],
  }), /email\.ambiguo/);
  assert.throws(() => prepareUser({ ...sample, departamento_id_empresa: 2 }), /departamento\.empresa/);
  let invalid: unknown;
  try { prepareUser({ ...sample, empresa_cnpj: "inválido", cpf: "inválido" }); }
  catch (error) { invalid = error; }
  assert.ok(invalid instanceof UserDataError);
  assert.deepEqual(invalid.fields, ["usuario.cpf", "workspace.cnpj"]);
});

test("normalizes email and rejects display names and whitespace inside address", () => {
  assert.equal(normalizeEmail("  ANA@EXAMPLE.COM "), "ana@example.com");
  for (const value of ["Ana <ana@example.com>", "ana @example.com", "ana\n@example.com", "a..b@example.com"]) {
    assert.throws(() => normalizeEmail(value));
  }
});

test("creates Firebase and destination payloads without overwriting app-owned fields", () => {
  const prepared = prepareUser(sample);
  assert.deepEqual(firebasePayload(prepared), {
    email: "ana@example.com", display_name: "Ana", email_verified: false,
  });
  assert.deepEqual(workspacePayload(prepared), { nome: "Empresa A", cnpj: "11222333000181" });
  assert.deepEqual(organizationPayload(9, " Operação "), { workspace_id: 9, nome: "Operação", ativo: true });
  const inserted = targetInsertPayload(prepared, { firebase_uid: "firebase-real-uid", cargo_id: 17, unidade_id: 23 });
  assert.deepEqual(inserted, {
    nome: "Ana", email: "ana@example.com", cpf: "52998224725", firebase_uid: "firebase-real-uid",
    cargo_id: 17, unidade_id: 23, tipo: "COLABORADOR", status: "PRE_CADASTRADO",
  });
  const updated = targetUpdatePayload(prepared, { firebase_uid: "uid", cargo_id: 17, unidade_id: 23 });
  assert.ok(!("tipo" in updated) && !("status" in updated));
  assert.throws(() => targetInsertPayload(prepared, { firebase_uid: "", cargo_id: 1, unidade_id: 2 }));
});

test("fingerprint matches Python version 3 and ignores email ordering", async () => {
  assert.equal(await userFingerprint(sample), "c84543af9f88fba35082955399bbd5062b7811423acdf88108e6f51de85fba5b");
  assert.equal(await userFingerprint(sample), await userFingerprint({ ...sample, emails: [...sample.emails].reverse() }));
  assert.notEqual(await userFingerprint(sample), await userFingerprint({ ...sample, nome: "Ana Silva" }));
});
