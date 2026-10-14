import assert from "node:assert/strict";
import { test } from "node:test";
import { webcrypto } from "node:crypto";
import { FirebaseAuthClient, parseServiceAccount, type ServiceAccount } from "./firebase-auth.ts";
import { prepareUser, type LegacyUser } from "./domain.ts";

const source: LegacyUser = {
  id_funcionario: 1, cpf: "52998224725", nome: "Ana", cargo: "Analista",
  id_empresa: 1, empresa_nome: "Empresa A", empresa_cnpj: "11222333000181",
  id_departamento: 10, departamento_nome: "Operação", departamento_id_empresa: 1,
  emails: [{ id_email: 1, email: "ana@example.com" }],
};
const user = prepareUser(source);

async function testCredential(): Promise<ServiceAccount> {
  const pair = await webcrypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const der = new Uint8Array(await webcrypto.subtle.exportKey("pkcs8", pair.privateKey));
  let binary = "";
  for (const byte of der) binary += String.fromCharCode(byte);
  const privateKey = `-----BEGIN PRIVATE KEY-----\n${btoa(binary)}\n-----END PRIVATE KEY-----`;
  return { project_id: "astro-test", client_email: "service@example.iam.gserviceaccount.com", private_key: privateKey };
}

function mockFetch(handler: (url: URL, init: RequestInit) => Response | Promise<Response>): typeof fetch {
  return (async (input: RequestInfo | URL, init: RequestInit = {}) => handler(new URL(String(input)), init)) as typeof fetch;
}

const oauth = () => Response.json({ access_token: "test-token", expires_in: 3600 });

test("does not create accounts during dry-run and caches OAuth token", async () => {
  const urls: string[] = [];
  const credential = await testCredential();
  const client = new FirebaseAuthClient("astro-test", credential, true, mockFetch((url) => {
    urls.push(url.toString());
    if (url.toString().startsWith("https://oauth2.googleapis.com/token")) return oauth();
    return Response.json({ users: [] });
  }));
  assert.deepEqual(await client.ensureUser(user), { uid: null, outcome: "would_create", disabled: null });
  assert.deepEqual(await client.ensureUser(user), { uid: null, outcome: "would_create", disabled: null });
  assert.equal(urls.filter((url) => url.startsWith("https://oauth2.googleapis.com/token")).length, 1);
  assert.equal(urls.filter((url) => url.endsWith("/accounts")).length, 0);
});

test("decodes service account only in memory and rejects project mismatch", async () => {
  const credential = await testCredential();
  const encoded = btoa(JSON.stringify(credential));
  const parsed = parseServiceAccount(encoded, "astro-test");
  assert.equal(parsed.client_email, credential.client_email);
  assert.throws(() => parseServiceAccount(encoded, "another-project"), /outro projeto/);
  assert.throws(() => parseServiceAccount("invalid-base64", "astro-test"));
});

test("creates passwordless users through the authenticated Admin REST API", async () => {
  const credential = await testCredential();
  let lookupCalls = 0;
  let createdUid = "";
  const client = new FirebaseAuthClient("astro-test", credential, false, mockFetch(async (url, init) => {
    if (url.toString() === "https://oauth2.googleapis.com/token") return oauth();
    assert.equal(new Headers(init.headers).get("Authorization"), "Bearer test-token");
    if (url.pathname.endsWith("accounts:lookup")) {
      lookupCalls++;
      return Response.json(lookupCalls === 1 ? { users: [] } : { users: [{ localId: createdUid, email: "ana@example.com" }] });
    }
    assert.ok(url.pathname.endsWith("/accounts"));
    const body = JSON.parse(String(init.body));
    assert.equal(body.email, "ana@example.com");
    assert.equal(body.displayName, "Ana");
    assert.equal(body.emailVerified, false);
    assert.ok(!("password" in body));
    assert.ok(!("localId" in body));
    createdUid = "firebase-created-uid";
    return Response.json({ localId: createdUid });
  }));
  const result = await client.ensureUser(user);
  assert.equal(result.uid, createdUid);
  assert.equal(result.outcome, "created");
});

test("recovers an account after an ambiguous create response", async () => {
  const credential = await testCredential();
  let lookups = 0;
  const client = new FirebaseAuthClient("astro-test", credential, false, mockFetch((url) => {
    if (url.toString() === "https://oauth2.googleapis.com/token") return oauth();
    if (url.pathname.endsWith("accounts:lookup")) {
      lookups++;
      return Response.json(lookups === 1 ? { users: [] } : { users: [{ localId: "recovered-uid", email: "ana@example.com" }] });
    }
    return new Response("", { status: 503 });
  }));
  assert.deepEqual(await client.ensureUser(user), {
    uid: "recovered-uid", outcome: "recovered", disabled: false,
  });
});

test("uses the validated linked UID and refuses email mismatch", async () => {
  const credential = await testCredential();
  const client = new FirebaseAuthClient("astro-test", credential, true, mockFetch((url, init) => {
    if (url.toString() === "https://oauth2.googleapis.com/token") return oauth();
    const body = JSON.parse(String(init.body));
    assert.deepEqual(body.localId, ["linked-uid"]);
    return Response.json({ users: [{ localId: "linked-uid", email: "ana@example.com", disabled: true }] });
  }));
  assert.deepEqual(await client.ensureUser(user, "linked-uid"), {
    uid: "linked-uid", outcome: "existing", disabled: true,
  });
  const mismatch = new FirebaseAuthClient("astro-test", credential, true, mockFetch((url) => {
    if (url.toString() === "https://oauth2.googleapis.com/token") return oauth();
    return Response.json({ users: [{ localId: "linked-uid", email: "other@example.com" }] });
  }));
  await assert.rejects(mismatch.ensureUser(user, "linked-uid"), /diverge/);
});
