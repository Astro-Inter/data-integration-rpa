import {
  organizationPayload,
  targetInsertPayload,
  targetUpdatePayload,
  workspacePayload,
  type PreparedUser,
} from "./domain.ts";
import { INTEGRATION_KEY, type SqlClient } from "./repositories.ts";

export class TargetIdentityError extends Error {
  constructor(message: string) { super(message); this.name = "TargetIdentityError"; }
}

type ExistingUser = {
  id_usuario: number | string;
  cpf: string;
  email: string;
  firebase_uid: string | null;
  cargo_id: number | string;
  unidade_id: number | string;
};
type Link = { target_id: number | string; firebase_uid: string };

function integer(value: number | string): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(parsed)) throw new TargetIdentityError("Identificador fora do limite seguro.");
  return parsed;
}

export class TargetUserRepository {
  private readonly database: SqlClient;
  constructor(database: SqlClient) { this.database = database; }

  private async identity(user: PreparedUser, uid: string | null): Promise<ExistingUser | null> {
    const { rows: linkRows } = await this.database.query<Link>(
      `SELECT target_id, firebase_uid FROM rpa_user_links
       WHERE integration_key = $1 AND legacy_id = $2`,
      [INTEGRATION_KEY, user.legacy_id],
    );
    const link = linkRows[0] ?? null;
    const email = user.usuario.email;
    for (const table of ["admin", "ONLY conta"]) {
      const { rows } = await this.database.query(
        `SELECT 1 FROM ${table} WHERE lower(trim(email)) = $1 OR firebase_uid = $2 LIMIT 1`,
        [email, uid],
      );
      if (rows.length) throw new TargetIdentityError("Identidade já utilizada por administrador ou conta independente.");
    }

    const { rows } = await this.database.query<ExistingUser>(
      `SELECT id_usuario, cpf, email, firebase_uid, cargo_id, unidade_id
       FROM usuarios WHERE cpf = $1 OR lower(trim(email)) = $2
          OR firebase_uid = $3 OR id_usuario = $4 FOR UPDATE`,
      [user.usuario.cpf, email, uid, link ? integer(link.target_id) : null],
    );
    if (rows.length > 1) throw new TargetIdentityError("CPF, e-mail ou UID apontam para usuários diferentes no destino.");
    const row = rows[0] ?? null;
    if (link && (!row || integer(row.id_usuario) !== integer(link.target_id)
        || row.firebase_uid !== link.firebase_uid)) {
      throw new TargetIdentityError("O vínculo legado/destino diverge do cadastro atual.");
    }
    if (!row) return null;
    if (row.cpf !== user.usuario.cpf) throw new TargetIdentityError("CPF diverge da identidade localizada.");
    if (!row.firebase_uid || (uid !== null && row.firebase_uid !== uid)) {
      throw new TargetIdentityError("UID diverge da identidade localizada no destino.");
    }

    const { rows: otherLinks } = await this.database.query<{ legacy_id: number | string }>(
      `SELECT legacy_id FROM rpa_user_links WHERE target_id = $1
       AND (legacy_id <> $2 OR integration_key <> $3)`,
      [integer(row.id_usuario), user.legacy_id, INTEGRATION_KEY],
    );
    if (otherLinks.length) throw new TargetIdentityError("Usuário destino já vinculado a outro registro de origem.");

    const { rows: relationRows } = await this.database.query<{ cargo_cnpj: string; unidade_cnpj: string }>(
      `SELECT wc.cnpj AS cargo_cnpj, wu.cnpj AS unidade_cnpj
       FROM cargos c JOIN workspaces wc ON wc.id_workspace = c.workspace_id
       JOIN unidades u ON u.id_unidade = $1
       JOIN workspaces wu ON wu.id_workspace = u.workspace_id
       WHERE c.id_cargo = $2`,
      [integer(row.unidade_id), integer(row.cargo_id)],
    );
    const relations = relationRows[0];
    if (!relations || relations.cargo_cnpj !== user.workspace.cnpj || relations.unidade_cnpj !== user.workspace.cnpj) {
      throw new TargetIdentityError("Mudança de workspace ou vínculo organizacional inconsistente exige revisão.");
    }
    return row;
  }

  async resolveIdentity(user: PreparedUser): Promise<string | null> {
    const row = await this.identity(user, null);
    return row?.firebase_uid ?? null;
  }

  async persist(user: PreparedUser, uid: string): Promise<void> {
    const row = await this.identity(user, uid);
    const workspace = workspacePayload(user);
    const { rows: workspaceRows } = await this.database.query<{ id_workspace: number | string }>(
      `INSERT INTO workspaces (nome, cnpj) VALUES ($1, $2)
       ON CONFLICT (cnpj) DO UPDATE SET nome = EXCLUDED.nome
       RETURNING id_workspace`,
      [workspace.nome, workspace.cnpj],
    );
    const workspaceId = integer(workspaceRows[0]?.id_workspace);
    const jobId = await this.organization("cargos", "id_cargo", organizationPayload(workspaceId, user.cargo.nome));
    const unitId = await this.organization("unidades", "id_unidade", organizationPayload(workspaceId, user.unidade.nome));
    const payload = targetInsertPayload(user, { firebase_uid: uid, cargo_id: jobId, unidade_id: unitId });
    let targetId: number;
    if (!row) {
      const { rows } = await this.database.query<{ id_usuario: number | string }>(
        `INSERT INTO usuarios (nome, email, cpf, firebase_uid, cargo_id, unidade_id, tipo, status)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id_usuario`,
        [payload.nome, payload.email, payload.cpf, payload.firebase_uid,
          payload.cargo_id, payload.unidade_id, payload.tipo, payload.status],
      );
      targetId = integer(rows[0]?.id_usuario);
    } else {
      targetId = integer(row.id_usuario);
      const update = targetUpdatePayload(user, { firebase_uid: uid, cargo_id: jobId, unidade_id: unitId });
      await this.database.query(
        `UPDATE usuarios SET nome = $1, email = $2, cpf = $3,
           firebase_uid = $4, cargo_id = $5, unidade_id = $6 WHERE id_usuario = $7`,
        [update.nome, update.email, update.cpf, update.firebase_uid,
          update.cargo_id, update.unidade_id, targetId],
      );
    }

    const { rows: links } = await this.database.query(
      `SELECT target_id FROM rpa_user_links WHERE integration_key = $1 AND legacy_id = $2`,
      [INTEGRATION_KEY, user.legacy_id],
    );
    if (!links.length) {
      await this.database.query(
        `INSERT INTO rpa_user_links (integration_key, legacy_id, target_id, firebase_uid)
         VALUES ($1, $2, $3, $4)`,
        [INTEGRATION_KEY, user.legacy_id, targetId, uid],
      );
    }
  }

  private async organization(
    table: "cargos" | "unidades",
    idColumn: "id_cargo" | "id_unidade",
    payload: { workspace_id: number; nome: string; ativo: true },
  ): Promise<number> {
    await this.database.query(
      `INSERT INTO ${table} (workspace_id, nome, ativo) VALUES ($1, $2, $3)
       ON CONFLICT (workspace_id, nome) DO NOTHING`,
      [payload.workspace_id, payload.nome, payload.ativo],
    );
    const { rows } = await this.database.query<{ id: number | string }>(
      `SELECT ${idColumn} AS id FROM ${table} WHERE workspace_id = $1 AND nome = $2`,
      [payload.workspace_id, payload.nome],
    );
    return integer(rows[0]?.id);
  }
}
