/** Port of the RPA's deterministic user validation and change fingerprint. */

export type LegacyEmail = { id_email: number; email: string | null };
export type LegacyUser = {
  id_funcionario: number;
  cpf: string;
  nome: string;
  cargo: string;
  id_empresa: number;
  empresa_nome: string | null;
  empresa_cnpj: string | null;
  id_departamento: number;
  departamento_nome: string | null;
  departamento_id_empresa: number | null;
  emails: LegacyEmail[];
};

export type PreparedUser = {
  legacy_id: number;
  workspace: { legacy_empresa_id: number; nome: string; cnpj: string };
  unidade: { legacy_departamento_id: number; nome: string; ativo: true };
  cargo: { nome: string; ativo: true };
  usuario: { nome: string; email: string; cpf: string };
};

export class UserDataError extends Error {
  readonly fields: string[];
  constructor(fields: string[]) {
    super(`Dados inválidos nos campos: ${fields.join(", ")}`);
    this.name = "UserDataError";
    this.fields = fields;
  }
}

export function normalizeDocument(value: unknown, kind: "cpf" | "cnpj"): string {
  if (typeof value !== "string") throw new Error("documento_obrigatorio");
  if (!/^[0-9. /\-\s]+$/u.test(value)) throw new Error("documento_caracteres_invalidos");
  const digits = value.replace(/[. /\-\s]/gu, "");
  const size = kind === "cpf" ? 11 : 14;
  if (digits.length !== size) throw new Error("documento_tamanho_invalido");
  if (new Set(digits).size === 1) throw new Error("documento_digitos_repetidos");
  const weights = kind === "cpf"
    ? [[10, 9, 8, 7, 6, 5, 4, 3, 2], [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]]
    : [[5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]];
  for (let index = 0; index < weights.length; index++) {
    const remainder = [...digits.slice(0, size - 2 + index)]
      .reduce((sum, char, position) => sum + Number(char) * weights[index][position], 0) % 11;
    const expected = remainder < 2 ? 0 : 11 - remainder;
    if (Number(digits[size - 2 + index]) !== expected) throw new Error("documento_verificadores_invalidos");
  }
  return digits;
}

export function cleanText(value: unknown): string {
  if (typeof value !== "string") throw new Error("texto_obrigatorio");
  const normalized = value.normalize("NFC");
  if ([...normalized].some((char) => /\p{C}/u.test(char) && !/\s/u.test(char))) {
    throw new Error("caractere_invalido");
  }
  const cleaned = normalized.trim().split(/\s+/u).filter(Boolean).join(" ");
  if (cleaned.length < 2) throw new Error("string_too_short");
  if (cleaned.length > 255) throw new Error("string_too_long");
  return cleaned;
}

export function normalizeEmail(value: unknown): string {
  if (typeof value !== "string") throw new Error("email_obrigatorio");
  const email = value.trim().toLowerCase();
  const split = email.split("@");
  if (split.length !== 2) throw new Error("email_invalido");
  const [local, rawDomain] = split;
  if (local.length < 1 || local.length > 64 || local.startsWith(".") || local.endsWith(".") || local.includes("..")
      || !/^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+$/u.test(local)) throw new Error("email_invalido");
  if (!/^[\p{L}\p{N}.-]+$/u.test(rawDomain)) throw new Error("email_invalido");
  let domain: string;
  try { domain = new URL(`http://${rawDomain}`).hostname.toLowerCase(); }
  catch { throw new Error("email_invalido"); }
  const labels = domain.split(".");
  if (domain.length > 253 || labels.length < 2 || labels.some((label) => !label || label.length > 63
      || label.startsWith("-") || label.endsWith("-") || !/^[a-z0-9-]+$/u.test(label))) {
    throw new Error("email_invalido");
  }
  const result = `${local}@${domain}`;
  if (result.length > 254) throw new Error("email_invalido");
  return result;
}

export function prepareUser(user: LegacyUser): PreparedUser {
  const errors: string[] = [];
  if (user.departamento_id_empresa == null || user.departamento_id_empresa !== user.id_empresa) {
    errors.push("departamento.empresa");
  }
  const emails = new Set<string>();
  for (const item of user.emails) {
    if (item.email == null || (typeof item.email === "string" && !item.email.trim())) continue;
    try { emails.add(normalizeEmail(item.email)); }
    catch { errors.push("email.formato"); }
  }
  if (emails.size === 0) errors.push("email.obrigatorio");
  if (emails.size > 1) errors.push("email.ambiguo");
  if (errors.length) throw new UserDataError([...new Set(errors)].sort());
  const fieldErrors: string[] = [];
  const parseField = <T>(path: string, parser: () => T): T | undefined => {
    try { return parser(); }
    catch { fieldErrors.push(path); return undefined; }
  };
  const workspaceName = parseField("workspace.nome", () => cleanText(user.empresa_nome));
  const workspaceCnpj = parseField("workspace.cnpj", () => normalizeDocument(user.empresa_cnpj, "cnpj"));
  const unitName = parseField("unidade.nome", () => cleanText(user.departamento_nome));
  const jobName = parseField("cargo.nome", () => cleanText(user.cargo));
  const userName = parseField("usuario.nome", () => cleanText(user.nome));
  const cpf = parseField("usuario.cpf", () => normalizeDocument(user.cpf, "cpf"));
  if (fieldErrors.length) throw new UserDataError([...new Set(fieldErrors)].sort());
  return {
    legacy_id: user.id_funcionario,
    workspace: { legacy_empresa_id: user.id_empresa, nome: workspaceName!, cnpj: workspaceCnpj! },
    unidade: { legacy_departamento_id: user.id_departamento, nome: unitName!, ativo: true },
    cargo: { nome: jobName!, ativo: true },
    usuario: { nome: userName!, email: [...emails][0], cpf: cpf! },
  };
}

function sortedJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${sortedJson(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function userFingerprint(user: LegacyUser): Promise<string> {
  const payload = {
    version: 3,
    user: { ...user, emails: [...user.emails].sort((a, b) => a.id_email - b.id_email) },
  };
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(sortedJson(payload)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function workspacePayload(user: PreparedUser) {
  return { nome: user.workspace.nome, cnpj: user.workspace.cnpj };
}

export function organizationPayload(workspace_id: number, nome: string) {
  if (!Number.isSafeInteger(workspace_id) || workspace_id <= 0) throw new Error("workspace_id_invalido");
  return { workspace_id, nome: cleanText(nome), ativo: true as const };
}

export function firebasePayload(user: PreparedUser) {
  return { email: user.usuario.email, display_name: user.usuario.nome, email_verified: false as const };
}

export function targetInsertPayload(
  user: PreparedUser,
  { firebase_uid, cargo_id, unidade_id }: { firebase_uid: string; cargo_id: number; unidade_id: number },
) {
  if (typeof firebase_uid !== "string" || firebase_uid.length < 1 || firebase_uid.length > 128
      || !Number.isSafeInteger(cargo_id) || cargo_id <= 0
      || !Number.isSafeInteger(unidade_id) || unidade_id <= 0) throw new Error("identificadores_destino_invalidos");
  return {
    ...user.usuario,
    firebase_uid,
    cargo_id,
    unidade_id,
    tipo: "COLABORADOR" as const,
    status: "PRE_CADASTRADO" as const,
  };
}

export function targetUpdatePayload(
  user: PreparedUser,
  identifiers: { firebase_uid: string; cargo_id: number; unidade_id: number },
) {
  const { tipo: _tipo, status: _status, ...payload } = targetInsertPayload(user, identifiers);
  return payload;
}
