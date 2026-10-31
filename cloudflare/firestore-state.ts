import type { FirebaseAuthClient } from "./firebase-auth.ts";
import type { SyncStateRepository } from "./repositories.ts";

export type Link = { target_id: number; firebase_uid: string };
export type SyncState = Pick<SyncStateRepository, "lastSyncedAt" | "confirmedHashes" | "lockSync" | "confirmUser" | "confirmRun"> & {
  getLink(id: number): Promise<Link | null>;
  checkLink(id: number, target: number, uid: string): Promise<void>;
  stageLink(id: number, target: number, uid: string): Promise<void>;
};
type Value = { stringValue?: string; integerValue?: string };
type Document = { fields?: Record<string, Value>; updateTime?: string };
const fields = (data: Record<string, string | number>) => Object.fromEntries(Object.entries(data).map(([k, v]) =>
  [k, typeof v === "number" ? { integerValue: String(v) } : { stringValue: v }]));

export class FirestoreState implements SyncState {
  private readonly root: string;
  private readonly integration: string;
  private version: string | null = null;
  private links = new Map<number, Link>();
  private pending = new Map<number, Record<string, string>>();
  private completed: string | null = null;
  private auth: Pick<FirebaseAuthClient, "oauthToken">;
  private fetcher: typeof fetch;
  constructor(project: string, database: string, auth: Pick<FirebaseAuthClient, "oauthToken">, fetcher: typeof fetch = fetch) {
    this.auth = auth; this.fetcher = fetcher;
    if (!project || !database || database.includes("/")) throw new Error("Configuração Firestore inválida.");
    this.root = `projects/${project}/databases/${database}/documents`;
    this.integration = `${this.root}/rpa_integracoes/usuarios_legado`;
  }
  private async request(path: string, method = "GET", body?: unknown): Promise<Document> {
    try {
      const response = await this.fetcher(`https://firestore.googleapis.com/v1/${path}`, {
        method, headers: { Authorization: `Bearer ${await this.auth.oauthToken()}`, "Content-Type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(10000),
      });
      if (response.status === 404 && method === "GET") return {};
      if (!response.ok) throw new Error();
      return await response.json() as Document;
    } catch { throw new Error("Falha no controle Firestore; verifique IAM, conexão e rpa_locks."); }
  }
  async lastSyncedAt(): Promise<string | null> {
    const doc = await this.request(this.integration);
    const value = doc.fields?.last_synced_at;
    if (!value) return null;
    // Python stores a native timestamp; REST also accepts timestamps written by SDK.
    const date = value.stringValue ?? (value as { timestampValue?: string }).timestampValue;
    if (!date || !Number.isFinite(Date.parse(date))) throw new Error("Data inválida no controle Firestore.");
    return date;
  }
  async confirmedHashes(ids: number[]): Promise<Map<number, string>> {
    const result = new Map<number, string>();
    for (const id of ids) {
      const doc = await this.request(`${this.integration}/usuarios/${id}`);
      if (!doc.fields) continue;
      const hash = doc.fields.data_hash?.stringValue;
      if (!hash || !/^[a-f0-9]{64}$/.test(hash)) throw new Error("Hash inválido no controle Firestore.");
      result.set(id, hash);
    }
    return result;
  }
  async lockSync(): Promise<void> {
    const doc = await this.request(`${this.root}/rpa_locks?documentId=usuarios_legado`, "POST", {
      fields: fields({ owner: crypto.randomUUID(), started_at: new Date().toISOString() }),
    });
    if (!doc.updateTime) throw new Error("Lock Firestore sem versão; confira rpa_locks.");
    this.version = doc.updateTime;
  }
  async release(): Promise<void> {
    if (!this.version) return;
    await this.request(`${this.root}/rpa_locks/usuarios_legado?currentDocument.updateTime=${encodeURIComponent(this.version)}`, "DELETE");
    this.version = null;
  }
  async getLink(id: number): Promise<Link | null> {
    if (this.links.has(id)) return this.links.get(id)!;
    const doc = await this.request(`${this.integration}/usuarios/${id}`);
    if (!doc.fields) return null;
    const target = Number(doc.fields.target_id?.integerValue);
    const uid = doc.fields.firebase_uid?.stringValue;
    if (!Number.isSafeInteger(target) || !uid) throw new Error("Vínculo inválido no Firestore.");
    return { target_id: target, firebase_uid: uid };
  }
  private async uidKey(uid: string): Promise<string> {
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(uid));
    return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("");
  }
  async checkLink(id: number, target: number, uid: string): Promise<void> {
    for (const [other, link] of this.links) {
      if (other !== id && (link.target_id === target || link.firebase_uid === uid)) throw new Error("Identidade já vinculada a outro funcionário.");
    }
    for (const path of [`destinos/${target}`, `uids/${await this.uidKey(uid)}`]) {
      const doc = await this.request(`${this.integration}/${path}`);
      if (doc.fields && Number(doc.fields.legacy_id?.integerValue) !== id) throw new Error("Identidade já vinculada a outro funcionário.");
    }
  }
  async stageLink(id: number, target: number, uid: string): Promise<void> {
    if (!this.version) throw new Error("Vínculo exige lock Firestore.");
    await this.checkLink(id, target, uid);
    this.links.set(id, { target_id: target, firebase_uid: uid });
  }
  async confirmUser(id: number, hash: string, at: string): Promise<void> { this.pending.set(id, { data_hash: hash, synced_at: at }); }
  async confirmRun(at: string): Promise<void> { this.completed = at; }
  async flush(): Promise<void> {
    if (!this.version) throw new Error("Confirmação exige lock Firestore.");
    for (const [id, data] of this.pending) {
      const link = this.links.get(id);
      if (!link) throw new Error("Confirmação sem vínculo Firestore.");
      const documents = [
        { name: `${this.integration}/usuarios/${id}`, fields: fields({ ...data, ...link }) },
        { name: `${this.integration}/destinos/${link.target_id}`, fields: fields({ legacy_id: id }) },
        { name: `${this.integration}/uids/${await this.uidKey(link.firebase_uid)}`, fields: fields({ legacy_id: id }) },
      ];
      await this.request(`${this.root}:commit`, "POST", { writes: documents.map(update => ({ update })) });
    }
    if (this.completed) await this.request(`${this.root}:commit`, "POST", { writes: [{
      update: { name: this.integration, fields: { last_synced_at: { timestampValue: this.completed } } },
      updateMask: { fieldPaths: ["last_synced_at"] },
    }] });
  }
}
