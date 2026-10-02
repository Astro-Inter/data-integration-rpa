import { Client } from "pg";
import type { QueryResult, SqlClient } from "./repositories.ts";

export type HyperdriveBinding = { connectionString: string };
type PgClient = Pick<Client, "connect" | "query" | "end">;
type ClientFactory = (binding: HyperdriveBinding) => PgClient;

export class PostgresSession implements SqlClient {
  private readonly client: PgClient;
  private active = false;

  constructor(client: PgClient) { this.client = client; }

  async connect(): Promise<void> { await this.client.connect(); }

  async begin(readOnly: boolean): Promise<void> {
    if (this.active) throw new Error("transacao_ja_aberta");
    await this.client.query(readOnly ? "BEGIN READ ONLY" : "BEGIN");
    this.active = true;
    await this.client.query("SET LOCAL statement_timeout = '30s'");
  }

  async query<Row extends Record<string, unknown> = Record<string, unknown>>(
    sql: string,
    parameters: unknown[] = [],
  ): Promise<QueryResult<Row>> {
    const result = await this.client.query(sql, parameters);
    return { rows: result.rows as Row[] };
  }

  transactionActive(): boolean { return this.active; }

  async commit(): Promise<void> {
    if (!this.active) throw new Error("transacao_ausente");
    await this.client.query("COMMIT");
    this.active = false;
  }

  async rollback(): Promise<void> {
    if (!this.active) return;
    try { await this.client.query("ROLLBACK"); }
    finally { this.active = false; }
  }

  async close(): Promise<void> { await this.client.end(); }
}

const defaultClientFactory: ClientFactory = (binding) => new Client({
  connectionString: binding.connectionString,
  connectionTimeoutMillis: 10_000,
});

/** Opens independent Hyperdrive sessions and commits the target only after a complete run. */
export async function withHyperdriveSessions<T>(
  legacyBinding: HyperdriveBinding,
  targetBinding: HyperdriveBinding,
  dryRun: boolean,
  operation: (legacy: PostgresSession, target: PostgresSession) => Promise<T>,
  clientFactory: ClientFactory = defaultClientFactory,
): Promise<T> {
  const legacy = new PostgresSession(clientFactory(legacyBinding));
  const target = new PostgresSession(clientFactory(targetBinding));
  let targetConnected = false;
  try {
    await legacy.connect();
    await legacy.begin(true);
    await target.connect();
    targetConnected = true;
    await target.begin(dryRun);
    const result = await operation(legacy, target);
    if (!dryRun) await target.commit();
    else await target.rollback();
    return result;
  } catch (error) {
    try { await target.rollback(); } catch { /* preserve the original safe failure */ }
    throw error;
  } finally {
    try { await legacy.rollback(); } finally { await legacy.close(); }
    if (targetConnected) await target.close();
  }
}
