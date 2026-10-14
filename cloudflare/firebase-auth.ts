import { normalizeEmail, type PreparedUser } from "./domain.ts";

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const AUTH_SCOPE = "https://www.googleapis.com/auth/identitytoolkit";
const AUTH_API = "https://identitytoolkit.googleapis.com/v1/projects";

export type FirebaseAccount = { uid: string; email: string; disabled: boolean };
export type FirebaseOutcome = "existing" | "created" | "recovered" | "would_create";
export type FirebaseUserResult = { uid: string | null; outcome: FirebaseOutcome; disabled: boolean | null };
export type ServiceAccount = { project_id: string; client_email: string; private_key: string };

export class FirebaseServiceError extends Error {
  constructor(message: string) { super(message); this.name = "FirebaseServiceError"; }
}

function base64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function encodedJson(value: unknown): string {
  return base64Url(new TextEncoder().encode(JSON.stringify(value)));
}

function decodePrivateKey(pem: string): Uint8Array {
  const body = pem.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g, "");
  try {
    const binary = atob(body);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  } catch {
    throw new FirebaseServiceError("Credencial Firebase inválida.");
  }
}

function validateCredential(value: ServiceAccount, expectedProjectId: string): void {
  if (value.project_id !== expectedProjectId || typeof value.client_email !== "string"
      || !value.client_email.includes("@") || typeof value.private_key !== "string"
      || !value.private_key.includes("BEGIN PRIVATE KEY")) {
    throw new FirebaseServiceError("Credencial Firebase inválida ou de outro projeto.");
  }
}

export function parseServiceAccount(encodedJson: string, expectedProjectId: string): ServiceAccount {
  try {
    const raw = atob(encodedJson);
    const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)) as ServiceAccount;
    validateCredential(value, expectedProjectId);
    return value;
  } catch {
    throw new FirebaseServiceError("Credencial Firebase inválida ou de outro projeto.");
  }
}

export class FirebaseAuthClient {
  private accessToken: string | null = null;
  private tokenExpiresAt = 0;
  private readonly fetcher: typeof fetch;
  private readonly projectId: string;
  private readonly credential: ServiceAccount;
  private readonly dryRun: boolean;

  constructor(
    projectId: string,
    credential: ServiceAccount,
    dryRun: boolean,
    fetcher: typeof fetch = fetch,
  ) {
    validateCredential(credential, projectId);
    this.projectId = projectId;
    this.credential = credential;
    this.dryRun = dryRun;
    this.fetcher = fetcher;
  }

  private async oauthToken(): Promise<string> {
    if (this.accessToken && Date.now() < this.tokenExpiresAt - 60_000) return this.accessToken;
    const now = Math.floor(Date.now() / 1000);
    const unsigned = `${encodedJson({ alg: "RS256", typ: "JWT" })}.${encodedJson({
      iss: this.credential.client_email,
      scope: AUTH_SCOPE,
      aud: TOKEN_URL,
      iat: now,
      exp: now + 3600,
    })}`;
    let key: CryptoKey;
    try {
      key = await crypto.subtle.importKey(
        "pkcs8", decodePrivateKey(this.credential.private_key),
        { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"],
      );
    } catch {
      throw new FirebaseServiceError("Chave da conta Firebase inválida.");
    }
    const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned));
    const assertion = `${unsigned}.${base64Url(new Uint8Array(signature))}`;
    let response: Response;
    try {
      response = await this.fetcher(TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
          assertion,
        }),
      });
    } catch {
      throw new FirebaseServiceError("Falha ao autenticar no Firebase.");
    }
    if (!response.ok) throw new FirebaseServiceError("Falha ao autenticar no Firebase.");
    let data: { access_token?: string; expires_in?: number };
    try { data = await response.json() as typeof data; }
    catch { throw new FirebaseServiceError("Resposta OAuth inválida."); }
    if (typeof data.access_token !== "string" || !Number.isFinite(data.expires_in)) {
      throw new FirebaseServiceError("Resposta OAuth inválida.");
    }
    this.accessToken = data.access_token;
    this.tokenExpiresAt = Date.now() + Number(data.expires_in) * 1000;
    return data.access_token;
  }

  private async request(path: string, body: unknown): Promise<Response> {
    const token = await this.oauthToken();
    try {
      return await this.fetcher(`${AUTH_API}/${encodeURIComponent(this.projectId)}/${path}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      throw new FirebaseServiceError("Falha de comunicação com Firebase Authentication.");
    }
  }

  private async lookup(query: { localId: string } | { email: string }): Promise<FirebaseAccount | null> {
    const body = "localId" in query ? { localId: [query.localId] } : { email: [query.email] };
    const response = await this.request("accounts:lookup", body);
    if (!response.ok) throw new FirebaseServiceError("Falha ao consultar Firebase Authentication.");
    let data: { users?: { localId?: string; email?: string; disabled?: boolean }[] };
    try { data = await response.json() as typeof data; }
    catch { throw new FirebaseServiceError("Resposta de Firebase Authentication inválida."); }
    const user = data.users?.[0];
    if (!user) return null;
    if (typeof user.localId !== "string" || typeof user.email !== "string") {
      throw new FirebaseServiceError("Resposta de Firebase Authentication inválida.");
    }
    return { uid: user.localId, email: user.email, disabled: user.disabled === true };
  }

  private validateMatch(account: FirebaseAccount, user: PreparedUser): FirebaseUserResult {
    let emailMatches = false;
    try { emailMatches = normalizeEmail(account.email) === user.usuario.email; } catch { /* sanitized below */ }
    if (!emailMatches) throw new FirebaseServiceError("O e-mail da conta Firebase diverge do cadastro.");
    return { uid: account.uid, outcome: "existing", disabled: account.disabled };
  }

  async ensureUser(user: PreparedUser, knownUid?: string): Promise<FirebaseUserResult> {
    if (knownUid !== undefined) {
      if (typeof knownUid !== "string" || knownUid.length < 1 || knownUid.length > 128) {
        throw new FirebaseServiceError("UID vinculado é inválido.");
      }
      const account = await this.lookup({ localId: knownUid });
      if (!account || account.uid !== knownUid) throw new FirebaseServiceError("A conta Firebase vinculada não existe.");
      return this.validateMatch(account, user);
    }

    const existing = await this.lookup({ email: user.usuario.email });
    if (existing) return this.validateMatch(existing, user);
    if (this.dryRun) return { uid: null, outcome: "would_create", disabled: null };

    // Match Firebase Admin SDK's create-user endpoint. It accepts OAuth service
    // account credentials and creates a passwordless account without a hash config.
    try {
      const response = await this.request("accounts", {
        email: user.usuario.email,
        displayName: user.usuario.nome,
        emailVerified: false,
      });
      if (response.ok) {
        let data: { localId?: string };
        try { data = await response.json() as typeof data; }
        catch { throw new FirebaseServiceError("Resposta de criação Firebase inválida."); }
        if (typeof data.localId === "string" && data.localId.length > 0) {
          return { uid: data.localId, outcome: "created", disabled: false };
        }
        throw new FirebaseServiceError("Resposta de criação Firebase inválida.");
      }
      if (response.status < 500 && response.status !== 429) {
        const text = await response.text().catch(() => "");
        if (!/EMAIL_EXISTS|DUPLICATE_EMAIL/u.test(text)) {
          throw new FirebaseServiceError("Falha ao criar conta Firebase.");
        }
      }
    } catch (error) {
      if (error instanceof FirebaseServiceError && !/comunicação|autenticar|resposta/i.test(error.message)) throw error;
      // Network ambiguity or transient provider failure may have created the account.
    }

    const recovered = await this.lookup({ email: user.usuario.email });
    if (!recovered) throw new FirebaseServiceError("Não foi possível confirmar a conta Firebase após a criação.");
    const matched = this.validateMatch(recovered, user);
    return { ...matched, outcome: "recovered" };
  }
}
