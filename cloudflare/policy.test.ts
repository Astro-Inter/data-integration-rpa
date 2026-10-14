import test from "node:test";
import assert from "node:assert/strict";
import { containerEnv, cronJob, manualDryRun } from "./policy.ts";

test("manual execution requires an explicit request to write", () => {
  assert.equal(manualDryRun(new URL("https://worker/jobs/sync")), true);
  assert.equal(manualDryRun(new URL("https://worker/jobs/sync?dry_run=true")), true);
  assert.equal(manualDryRun(new URL("https://worker/jobs/sync?dry_run=false")), false);
  assert.throws(() => manualDryRun(new URL("https://worker/jobs/sync?dry_run=no")));
});
test("only declared cron expressions can launch a job", () => {
  const jobs = { backup: { cron: "17 6 * * *", timeout_seconds: 1800 } };
  assert.equal(cronJob(jobs, "17 6 * * *"), "backup");
  assert.throws(() => cronJob(jobs, "* * * * *"));
});
test("container receives only allowed scalar configuration", () => {
  assert.deepEqual(containerEnv({ DATABASE_URL: "example", JOBS_TOKEN: "control-secret", SERVICE: {}, API_ENABLED: "true" }, ["DATABASE_URL", "SERVICE"]), { DATABASE_URL: "example" });
});
