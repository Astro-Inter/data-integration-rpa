import type { LegacyEmail, LegacyUser } from "./domain.ts";

export type QueryResult<Row> = { rows: Row[] };
export interface SqlClient {
  query<Row extends Record<string, unknown> = Record<string, unknown>>(
    sql: string,
    parameters?: unknown[],
  ): Promise<QueryResult<Row>>;
  transactionActive?(): boolean;
}

type UserRow = Omit<LegacyUser, "emails">;
type EmailRow = LegacyEmail & { id_funcionario: number };

function databaseInteger(value: number | string | null | undefined): number | null {
  if (value == null) return null;
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error("id_banco_fora_do_limite_seguro");
  return parsed;
}

const USER_COLUMNS = `
  SELECT f.id_funcionario, f.cpf, f.nome, f.cargo, f.id_empresa,
         e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
         f.id_departamento, d.nome AS departamento_nome,
         d.id_empresa AS departamento_id_empresa
  FROM funcionario AS f
  LEFT JOIN empresa AS e ON e.id_empresa = f.id_empresa
  LEFT JOIN departamento AS d ON d.id_departamento = f.id_departamento
`;

export async function legacyUpperId(database: SqlClient): Promise<number | null> {
  const { rows } = await database.query<{ upper_id: number | string | null }>(
    "SELECT MAX(id_funcionario) AS upper_id FROM funcionario",
  );
  return databaseInteger(rows[0]?.upper_id);
}

export async function legacyBatch(
  database: SqlClient,
  { lastId, upperId, batchSize }: { lastId: number | null; upperId: number; batchSize: number },
): Promise<LegacyUser[]> {
  if (!Number.isSafeInteger(batchSize) || batchSize <= 0) throw new Error("batch_size_invalido");
  const pageSql = lastId === null
    ? `${USER_COLUMNS} WHERE f.id_funcionario <= $1 ORDER BY f.id_funcionario LIMIT $2`
    : `${USER_COLUMNS} WHERE f.id_funcionario > $1 AND f.id_funcionario <= $2 ORDER BY f.id_funcionario LIMIT $3`;
  const pageParameters = lastId === null ? [upperId, batchSize] : [lastId, upperId, batchSize];
  const page = await database.query<UserRow>(pageSql, pageParameters);
  if (page.rows.length === 0) return [];

  const ids = page.rows.map((row) => row.id_funcionario);
  const emailsResult = await database.query<EmailRow>(
    `SELECT id_funcionario, id_email, email FROM email
     WHERE id_funcionario = ANY($1::bigint[]) ORDER BY id_funcionario, id_email`,
    [ids],
  );
  const emails = new Map<number, LegacyEmail[]>(ids.map((id) => [id, []]));
  for (const row of emailsResult.rows) emails.get(row.id_funcionario)?.push({ id_email: row.id_email, email: row.email });
  return page.rows.map((row) => ({ ...row, emails: emails.get(row.id_funcionario) ?? [] }));
}

export async function* iterateLegacyBatches(
  database: SqlClient,
  batchSize: number,
): AsyncGenerator<LegacyUser[], void, void> {
  if (!Number.isSafeInteger(batchSize) || batchSize <= 0) throw new Error("batch_size_invalido");
  const upperId = await legacyUpperId(database);
  if (upperId === null) return;
  let lastId: number | null = null;
  while (true) {
    const batch = await legacyBatch(database, { lastId, upperId, batchSize });
    if (!batch.length) return;
    lastId = batch[batch.length - 1].id_funcionario;
    yield batch;
    if (batch.length < batchSize || lastId === upperId) return;
  }
}

export const INTEGRATION_KEY = "usuarios_legado";

export class SyncStateRepository {
  private readonly database: SqlClient;
  constructor(database: SqlClient) { this.database = database; }

  async lastSyncedAt(): Promise<string | null> {
    const { rows } = await this.database.query<{ last_synced_at: string | Date | null }>(
      "SELECT last_synced_at FROM rpa_sync_control WHERE integration_key = $1",
      [INTEGRATION_KEY],
    );
    const value = rows[0]?.last_synced_at ?? null;
    return value instanceof Date ? value.toISOString() : value;
  }

  async confirmedHashes(legacyIds: number[]): Promise<Map<number, string>> {
    if (!legacyIds.length) return new Map();
    const { rows } = await this.database.query<{ legacy_id: number | string; data_hash: string }>(
      `SELECT legacy_id, data_hash FROM rpa_sync_users
       WHERE integration_key = $1 AND legacy_id = ANY($2::bigint[])`,
      [INTEGRATION_KEY, legacyIds],
    );
    return new Map(rows.map(({ legacy_id, data_hash }) => {
      const id = databaseInteger(legacy_id);
      if (id === null) throw new Error("legacy_id_invalido");
      return [id, data_hash];
    }));
  }

  async lockSync(): Promise<void> {
    await this.database.query(
      `INSERT INTO rpa_sync_control (integration_key, last_synced_at)
       VALUES ($1, NULL) ON CONFLICT (integration_key) DO NOTHING`,
      [INTEGRATION_KEY],
    );
    await this.database.query(
      "SELECT integration_key FROM rpa_sync_control WHERE integration_key = $1 FOR UPDATE",
      [INTEGRATION_KEY],
    );
  }

  async confirmUser(legacyId: number, dataHash: string, syncedAt: string): Promise<void> {
    await this.database.query(
      `INSERT INTO rpa_sync_users (integration_key, legacy_id, data_hash, synced_at)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (integration_key, legacy_id) DO UPDATE
       SET data_hash = EXCLUDED.data_hash, synced_at = EXCLUDED.synced_at`,
      [INTEGRATION_KEY, legacyId, dataHash, syncedAt],
    );
  }

  async confirmRun(syncedAt: string): Promise<void> {
    await this.database.query(
      "UPDATE rpa_sync_control SET last_synced_at = $2 WHERE integration_key = $1",
      [INTEGRATION_KEY, syncedAt],
    );
  }
}
