import assert from "node:assert/strict";
import test from "node:test";
import { exportGrafanaLog } from "./grafana-logs.ts";

test("exports a sanitized OTLP log to the Grafana logs endpoint", async () => {
  let requestUrl = "";
  let requestInit: RequestInit | undefined;
  await exportGrafanaLog({
    GRAFANA_OTLP_ENDPOINT: "https://grafana.example/otlp/",
    GRAFANA_OTLP_HEADERS: "Authorization=Basic%20dGVzdA==",
  }, "test-worker", "job_finished", "INFO", { status: "success", removed: 3, absent: undefined },
  async (url, init) => {
    requestUrl = String(url);
    requestInit = init;
    return new Response(null, { status: 200 });
  });
  assert.equal(requestUrl, "https://grafana.example/otlp/v1/logs");
  assert.equal(new Headers(requestInit?.headers).get("Authorization"), "Basic dGVzdA==");
  const body = JSON.parse(String(requestInit?.body));
  const record = body.resourceLogs[0].scopeLogs[0].logRecords[0];
  assert.equal(body.resourceLogs[0].resource.attributes[0].value.stringValue, "test-worker");
  assert.equal(record.body.stringValue, "job_finished");
  assert.equal(record.severityNumber, 9);
  assert.deepEqual(record.attributes.map(({ key }) => key), ["event.name", "status", "removed"]);
});

test("does not send when Grafana credentials are not configured", async () => {
  let called = false;
  await exportGrafanaLog({}, "test-worker", "job_finished", "INFO", {}, async () => {
    called = true;
    return new Response(null, { status: 200 });
  });
  assert.equal(called, false);
});

