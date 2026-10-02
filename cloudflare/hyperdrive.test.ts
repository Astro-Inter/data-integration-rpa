import assert from "node:assert/strict";
import { test } from "node:test";
import { withHyperdriveSessions, type HyperdriveBinding } from "./hyperdrive.ts";

class FakeClient {
  statements: string[] = [];
  connected = false;
  closed = false;
  async connect() { this.connected = true; }
  async query(sql: string) { this.statements.push(sql); return { rows: [] }; }
  async end() { this.closed = true; }
}

const source: HyperdriveBinding = { connectionString: "postgres://legacy.invalid/db" };
const target: HyperdriveBinding = { connectionString: "postgres://target.invalid/db" };

test("commits one destination transaction only after a complete successful run", async () => {
  const clients: FakeClient[] = [];
  const result = await withHyperdriveSessions(source, target, false, async (legacy, destination) => {
    assert.equal(legacy.transactionActive(), true);
    assert.equal(destination.transactionActive(), true);
    await destination.query("INSERT INTO rpa_sync_users VALUES ($1)", ["opaque"]);
    return "complete";
  }, () => { const client = new FakeClient(); clients.push(client); return client as never; });
  assert.equal(result, "complete");
  assert.deepEqual(clients[0].statements, ["BEGIN READ ONLY", "SET LOCAL statement_timeout = '30s'", "ROLLBACK"]);
  assert.deepEqual(clients[1].statements, [
    "BEGIN", "SET LOCAL statement_timeout = '30s'", "INSERT INTO rpa_sync_users VALUES ($1)", "COMMIT",
  ]);
  assert.ok(clients.every((client) => client.connected && client.closed));
});

test("rolls back the entire destination transaction when any step fails", async () => {
  const clients: FakeClient[] = [];
  await assert.rejects(withHyperdriveSessions(source, target, false, async () => {
    throw new Error("safe-test-error");
  }, () => { const client = new FakeClient(); clients.push(client); return client as never; }), /safe-test-error/);
  assert.ok(clients[1].statements.includes("ROLLBACK"));
  assert.ok(!clients[1].statements.includes("COMMIT"));
  assert.ok(clients.every((client) => client.closed));
});

test("runs dry-run destination in read-only mode and always rolls it back", async () => {
  const clients: FakeClient[] = [];
  await withHyperdriveSessions(source, target, true, async (_legacy, destination) => {
    assert.equal(destination.transactionActive(), true);
    return null;
  }, () => { const client = new FakeClient(); clients.push(client); return client as never; });
  assert.equal(clients[1].statements[0], "BEGIN READ ONLY");
  assert.ok(clients[1].statements.includes("ROLLBACK"));
  assert.ok(!clients[1].statements.includes("COMMIT"));
});
