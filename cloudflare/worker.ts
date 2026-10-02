import { FirebaseAuthClient, parseServiceAccount } from "./firebase-auth.ts";
import { withHyperdriveSessions, type HyperdriveBinding } from "./hyperdrive.ts";
import { manualDryRun } from "./policy.ts";
import { runUserSync, type SyncSummary } from "./sync-runner.ts";
import { queueGrafanaLog, type GrafanaEnv } from "./grafana-logs.ts";

interface Env extends GrafanaEnv {
  LEGACY_DB: HyperdriveBinding;
  TARGET_DB: HyperdriveBinding;
  FIREBASE_PROJECT_ID: string;
  FIREBASE_CREDENTIALS_BASE64: string;
  API_ENABLED?: string;
  JOBS_ENABLED?: string;
  JOBS_TOKEN?: string;
  SYNC_BATCH_SIZE?: string;
  SYNC_DRY_RUN?: string;
}

const CRON = "17 * * * *";
const JOB_NAME = "sync-users";

function batchSize(env: Env): number {
  const value = Number(env.SYNC_BATCH_SIZE ?? "100");
  if (!Number.isSafeInteger(value) || value < 1 || value > 500) {
    throw new Error("SYNC_BATCH_SIZE deve ser um inteiro entre 1 e 500.");
  }
  return value;
}

async function execute(env: Env, dryRun: boolean): Promise<SyncSummary> {
  if (!env.FIREBASE_PROJECT_ID || !env.FIREBASE_CREDENTIALS_BASE64) {
    throw new Error("Configuração Firebase ausente.");
  }
  const credential = parseServiceAccount(env.FIREBASE_CREDENTIALS_BASE64, env.FIREBASE_PROJECT_ID);
  const firebase = new FirebaseAuthClient(env.FIREBASE_PROJECT_ID, credential, dryRun);
  return withHyperdriveSessions(env.LEGACY_DB, env.TARGET_DB, dryRun, (legacy, destination) =>
    runUserSync({ legacy, destination, firebase, batchSize: batchSize(env), dryRun }),
  );
}

function authorized(request: Request, env: Env): boolean {
  const token = env.JOBS_TOKEN;
  return typeof token === "string" && token.length >= 32
    && request.headers.get("Authorization") === `Bearer ${token}`;
}

function jsonError(code: string, status: number): Response {
  return Response.json({ error: code }, { status, headers: { "Cache-Control": "no-store" } });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health" && request.method === "GET") {
      return Response.json({ service: "data-integration-rpa", status: "ok" }, {
        headers: { "Cache-Control": "no-store" },
      });
    }
    if (env.API_ENABLED !== "true") return jsonError("api_inactive", 503);
    if (!authorized(request, env)) return jsonError("unauthorized", 401);

    if (url.pathname === "/status" && request.method === "GET") {
      return Response.json({
        service: "data-integration-rpa",
        cron: CRON,
        enabled: env.JOBS_ENABLED === "true",
        dry_run: env.SYNC_DRY_RUN === "true",
      }, { headers: { "Cache-Control": "no-store" } });
    }
    if (url.pathname !== `/jobs/${JOB_NAME}` || request.method !== "POST") {
      return jsonError("not_found", 404);
    }

    let dryRun: boolean;
    try { dryRun = manualDryRun(url); }
    catch { return jsonError("invalid_dry_run", 400); }
    if (!dryRun && (env.JOBS_ENABLED !== "true" || env.SYNC_DRY_RUN === "true")) {
      return jsonError("migration_not_activated", 503);
    }

    try {
      const summary = await execute(env, dryRun);
      queueGrafanaLog(ctx, env, "data-integration-rpa", "rpa_sync_finished", "INFO",
        { job: JOB_NAME, status: "success", dry_run: dryRun });
      return Response.json({ job: JOB_NAME, dry_run: dryRun, status: "success", ...summary }, {
        headers: { "Cache-Control": "no-store" },
      });
    } catch {
      // Provider/driver errors can contain emails, identifiers, and connection details.
      queueGrafanaLog(ctx, env, "data-integration-rpa", "rpa_sync_failed", "ERROR", { job: JOB_NAME });
      return jsonError("job_failed_check_worker_logs", 500);
    }
  },

  async scheduled(event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    if (event.cron !== CRON) throw new Error("Unexpected cron trigger.");
    if (env.JOBS_ENABLED !== "true") {
      console.log(JSON.stringify({ event: "cron_inactive", cron: event.cron }));
      return;
    }
    const dryRun = env.SYNC_DRY_RUN === "true";
    try {
      const summary = await execute(env, dryRun);
      queueGrafanaLog(ctx, env, "data-integration-rpa", "rpa_sync_finished", "INFO",
        { job: JOB_NAME, status: "success", dry_run: dryRun });
      console.log(JSON.stringify({ event: "job_finished", job: JOB_NAME, dry_run: dryRun, ...summary }));
    } catch {
      // Keep credentials, user records, SQL errors, and Firebase responses out of logs.
      queueGrafanaLog(ctx, env, "data-integration-rpa", "rpa_sync_failed", "ERROR", { job: JOB_NAME });
      console.error(JSON.stringify({ event: "job_failed", job: JOB_NAME }));
      throw new Error("RPA sync failed; inspect sanitized worker logs.");
    }
  },
} satisfies ExportedHandler<Env>;
