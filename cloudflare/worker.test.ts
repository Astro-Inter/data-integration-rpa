import assert from "node:assert/strict";
import { test, mock } from "node:test";
import worker from "./worker.ts";

const env = { API_ENABLED: "true", JOBS_ENABLED: "false", JOBS_TOKEN: "x".repeat(40), SYNC_DRY_RUN: "true" } as never;

test("falha de configuração exporta etapa e run_id sem depender de mensagem bruta", async () => {
  const exported: Promise<void>[] = [];
  const bodies: string[] = [];
  const fetcher = mock.method(globalThis, "fetch", async (_url: unknown, init: RequestInit) => {
    bodies.push(String(init.body));
    return new Response(null, { status: 204 });
  });
  const consoleError = mock.method(console, "error", () => {});
  try {
    const response = await worker.fetch!(new Request("https://worker.example/jobs/sync-users?dry_run=true", {
      method: "POST", headers: { Authorization: `Bearer ${"x".repeat(40)}` },
    }), { ...env, GRAFANA_OTLP_ENDPOINT: "https://collector.example/otlp", GRAFANA_OTLP_HEADERS: "Authorization=Basic%20fixture" } as never,
    { waitUntil: promise => exported.push(promise) } as ExecutionContext);
    await Promise.all(exported);
    assert.equal(response.status, 500);
    const attributes = JSON.parse(bodies[0]!).resourceLogs[0].scopeLogs[0].logRecords[0].attributes;
    assert.ok(attributes.some((attribute: { key: string; value: { stringValue?: string } }) => attribute.key === "stage" && attribute.value.stringValue === "firebase_configuration"));
    assert.ok(attributes.some((attribute: { key: string }) => attribute.key === "run_id"));
    assert.doesNotMatch(bodies.join(), /fixture|Bearer|FIREBASE_CREDENTIALS/);
  } finally { fetcher.mock.restore(); consoleError.mock.restore(); }
});

test("health endpoint exposes only non-sensitive service state", async () => {
  const response = await worker.fetch!(new Request("https://worker.example/health"), env, {} as ExecutionContext);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { service: "data-integration-rpa", status: "ok" });
});

test("status and job endpoints require the configured bearer token", async () => {
  const status = await worker.fetch!(new Request("https://worker.example/status"), env, {} as ExecutionContext);
  assert.equal(status.status, 401);
  const job = await worker.fetch!(new Request("https://worker.example/jobs/sync-users", { method: "POST" }), env, {} as ExecutionContext);
  assert.equal(job.status, 401);
});

test("API endpoints stay disabled until an explicit deployment change", async () => {
  const disabledEnv = { ...env, API_ENABLED: "false" } as never;
  const response = await worker.fetch!(new Request("https://worker.example/status", {
    headers: { Authorization: `Bearer ${"x".repeat(40)}` },
  }), disabledEnv, {} as ExecutionContext);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "api_inactive" });
});

test("status reports the active dry-run cron without exposing bindings or credentials", async () => {
  const response = await worker.fetch!(new Request("https://worker.example/status", {
    headers: { Authorization: `Bearer ${"x".repeat(40)}` },
  }), env, {} as ExecutionContext);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    service: "data-integration-rpa", cron: "17 * * * *", enabled: false, dry_run: true,
  });
});

test("write mode cannot run while the migration is disabled", async () => {
  const response = await worker.fetch!(new Request("https://worker.example/jobs/sync-users?dry_run=false", {
    method: "POST", headers: { Authorization: `Bearer ${"x".repeat(40)}` },
  }), env, {} as ExecutionContext);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "migration_not_activated" });
});

test("write mode stays blocked while scheduled runs are dry-run", async () => {
  const enabledDryRunEnv = { ...env, JOBS_ENABLED: "true", SYNC_DRY_RUN: "true" } as never;
  const response = await worker.fetch!(new Request("https://worker.example/jobs/sync-users?dry_run=false", {
    method: "POST", headers: { Authorization: `Bearer ${"x".repeat(40)}` },
  }), enabledDryRunEnv, {} as ExecutionContext);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "migration_not_activated" });
});

test("disabled cron returns without opening database sessions", async () => {
  await worker.scheduled!({ cron: "17 * * * *", scheduledTime: Date.now() } as ScheduledController, env, {} as ExecutionContext);
  await assert.rejects(worker.scheduled!({ cron: "* * * * *", scheduledTime: Date.now() } as ScheduledController, env, {} as ExecutionContext), /Unexpected cron/);
});
