import assert from "node:assert/strict";
import { test } from "node:test";
import { FirestoreState } from "./firestore-state.ts";
import { diagnostic } from "./diagnostics.ts";

test("Firestore HTTP failures identify stage and status without leaking provider details", async () => {
  for (const status of [401, 403, 429, 500]) {
    const state = new FirestoreState("project-test", "(default)", { oauthToken: async () => "secret-token" },
      (async () => new Response("secret provider body", { status })) as typeof fetch);
    await assert.rejects(state.lastSyncedAt(), error => {
      const fields = diagnostic(error);
      assert.equal(fields.error_code, `FIRESTORE_HTTP_${status}`);
      assert.equal(fields.stage, "firestore_read");
      assert.equal(fields.dependency, "firestore");
      assert.ok(!JSON.stringify(fields).includes("secret"));
      return true;
    });
  }
});

test("Firestore distinguishes OAuth, timeout, network and JSON failures", async () => {
  const cases = [
    { auth: async () => { throw new Error("secret credential"); }, fetcher: async () => Response.json({}), code: "FIRESTORE_AUTH_FAILED" },
    { auth: async () => "token", fetcher: async () => { throw new DOMException("secret", "TimeoutError"); }, code: "FIRESTORE_TIMEOUT" },
    { auth: async () => "token", fetcher: async () => { throw new Error("secret network host"); }, code: "FIRESTORE_NETWORK_FAILED" },
    { auth: async () => "token", fetcher: async () => new Response("not json"), code: "FIRESTORE_INVALID_RESPONSE" },
  ];
  for (const item of cases) {
    const state = new FirestoreState("project-test", "(default)", { oauthToken: item.auth }, item.fetcher as typeof fetch);
    await assert.rejects(state.lastSyncedAt(), error => {
      const fields = diagnostic(error);
      assert.equal(fields.error_code, item.code);
      assert.equal(fields.stage, "firestore_read");
      assert.ok(!JSON.stringify(fields).includes("secret"));
      return true;
    });
  }
});

function fixture() {
  const docs = new Map<string, Record<string, unknown>>();
  const calls: string[] = [];
  let failCommit = false;
  const fetcher = async (input: string | URL | Request, options?: RequestInit) => {
    const url = new URL(String(input));
    const path = decodeURIComponent(url.pathname.split("/documents")[1] ?? "");
    const method = options?.method ?? "GET";
    calls.push(`${method} ${path}`);
    if (method === "GET") return docs.has(path) ? Response.json({ fields: docs.get(path) }) : new Response("", { status: 404 });
    if (method === "DELETE") { docs.delete(path); return Response.json({}); }
    const body = JSON.parse(String(options?.body));
    if (path === "/rpa_locks") {
      if (docs.has("/rpa_locks/usuarios_legado")) return new Response("secret", { status: 409 });
      docs.set("/rpa_locks/usuarios_legado", body.fields);
      return Response.json({ updateTime: "2026-01-01T00:00:00Z" });
    }
    if (failCommit) return new Response("provider secret", { status: 500 });
    for (const write of body.writes) docs.set(write.update.name.split("/documents")[1], write.update.fields);
    return Response.json({});
  };
  const state = () => new FirestoreState("project-test", "(default)", { oauthToken: async () => "token-test" }, fetcher as typeof fetch);
  return { docs, calls, state, fail: () => { failCommit = true; } };
}

test("Firestore stages state until flush, publishes indices and marker, and blocks concurrent runs", async () => {
  const f = fixture(); const state = f.state();
  assert.equal(await state.lastSyncedAt(), null);
  await state.lockSync();
  await assert.rejects(f.state().lockSync(), /Firestore/);
  await state.stageLink(1, 10, "uid-one");
  await assert.rejects(state.stageLink(2, 10, "uid-two"), /vinculada/);
  await state.confirmUser(1, "a".repeat(64), "2026-01-01T00:00:00Z");
  await state.confirmRun("2026-01-01T00:00:00Z");
  assert.ok(!f.calls.some(call => call.includes(":commit")));
  await state.flush();
  await state.release();
  const next = f.state();
  assert.deepEqual(await next.confirmedHashes([1]), new Map([[1, "a".repeat(64)]]));
  assert.deepEqual(await next.getLink(1), { target_id: 10, firebase_uid: "uid-one" });
  assert.equal(await next.lastSyncedAt(), "2026-01-01T00:00:00Z");
  await assert.rejects(next.checkLink(2, 10, "uid-other"), /vinculada/);
  assert.ok(!f.docs.has("/rpa_locks/usuarios_legado"));
});

test("Firestore failure does not publish a successful run or expose provider errors", async () => {
  const f = fixture(); const state = f.state();
  await state.lockSync(); await state.stageLink(1, 10, "uid-one");
  await state.confirmUser(1, "b".repeat(64), "2026-01-01T00:00:00Z");
  await state.confirmRun("2026-01-01T00:00:00Z");
  f.fail(); await assert.rejects(state.flush(), error => !String(error).includes("provider secret"));
  await state.release();
  assert.equal(await f.state().lastSyncedAt(), null);
  assert.deepEqual(await f.state().confirmedHashes([1]), new Map());
});

test("dry-run only reads; corrupt hashes fail instead of skipping users", async () => {
  const f = fixture(); const state = f.state();
  assert.deepEqual(await state.confirmedHashes([1]), new Map());
  assert.ok(f.calls.every(call => call.startsWith("GET")));
  f.docs.set("/rpa_integracoes/usuarios_legado/usuarios/1", { data_hash: { stringValue: "bad" } });
  await assert.rejects(state.confirmedHashes([1]), /Hash inválido/);
});
