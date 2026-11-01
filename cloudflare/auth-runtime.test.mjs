import assert from "node:assert/strict";
import { test } from "node:test";
import { generateKeyPairSync } from "node:crypto";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

test("native workerd fetch supports OAuth and Firestore without binding the client as receiver", async () => {
  const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const credential = {
    project_id: "runtime-test", client_email: "runtime@example.iam.gserviceaccount.com",
    private_key: privateKey.export({ type: "pkcs8", format: "pem" }),
  };
  const bundle = await build({
    stdin: {
      resolveDir: fileURLToPath(new URL(".", import.meta.url)),
      contents: `
        import { FirebaseAuthClient, parseServiceAccount } from './firebase-auth.ts';
        import { FirestoreState } from './firestore-state.ts';
        export default { async fetch(request, env) {
          const auth = new FirebaseAuthClient('runtime-test', parseServiceAccount(env.CREDENTIAL, 'runtime-test'), true);
          const state = new FirestoreState('runtime-test', '(default)', auth);
          return Response.json({ last: await state.lastSyncedAt(), hashes: [...await state.confirmedHashes([1])] });
        } };
      `,
    }, bundle: true, write: false, format: "esm", platform: "browser",
  });
  const requests = [];
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, script: bundle.outputFiles[0].text,
    compatibilityDate: "2026-10-01", compatibilityFlags: ["nodejs_compat"],
    bindings: { CREDENTIAL: Buffer.from(JSON.stringify(credential)).toString("base64") },
    outboundService: async request => {
      const url = new URL(request.url);
      requests.push(url.origin);
      if (url.origin === "https://oauth2.googleapis.com") {
        const body = await request.formData();
        assert.equal(body.get("grant_type"), "urn:ietf:params:oauth:grant-type:jwt-bearer");
        return Response.json({ access_token: "runtime-token", expires_in: 3600 });
      }
      assert.equal(url.origin, "https://firestore.googleapis.com");
      assert.equal(request.headers.get("Authorization"), "Bearer runtime-token");
      return Response.json({ error: { status: "NOT_FOUND" } }, { status: 404 });
    },
  }));
  try {
    const response = await runtime.dispatchFetch("http://runtime.test");
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { last: null, hashes: [] });
    assert.deepEqual(requests, ["https://oauth2.googleapis.com", "https://firestore.googleapis.com", "https://firestore.googleapis.com"]);
  } finally { await runtime.dispose(); }
});
