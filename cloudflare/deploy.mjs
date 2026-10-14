import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(readFileSync(join(root, "wrangler.jsonc"), "utf8"));
const bindings = new Map((config.hyperdrive ?? []).map((item) => [item.binding, item.id]));
const validId = (id) => typeof id === "string" && /^[a-f0-9]{32}$/iu.test(id) && !/^0+$/u.test(id);
const safe = !Object.hasOwn(config, "limits")
  && config.observability?.enabled === false
  && ["false", "true"].includes(config.vars?.API_ENABLED)
  && config.vars?.JOBS_ENABLED === "true"
  && config.vars?.SYNC_DRY_RUN === "true"
  && JSON.stringify(config.triggers?.crons) === JSON.stringify(["17 * * * *"])
  && (!config.containers || config.containers.length === 0)
  && (!config.durable_objects?.bindings || config.durable_objects.bindings.length === 0)
  && validId(bindings.get("LEGACY_DB"))
  && validId(bindings.get("TARGET_DB"));

if (!safe) {
  console.error("Deploy blocked: expected Worker Free defaults, hourly dry-run Cron, no production writes, and valid existing Hyperdrive bindings.");
  process.exit(1);
}

const wrangler = join(root, "node_modules", "wrangler", "bin", "wrangler.js");
const extraArgs = process.argv.slice(2);
if (extraArgs.some((argument) => argument !== "--dry-run")) {
  console.error("Deploy blocked: only --dry-run is accepted by the local guard.");
  process.exit(1);
}
const result = spawnSync(process.execPath, [wrangler, "deploy", ...extraArgs], {
  cwd: root,
  env: process.env,
  stdio: "inherit",
});
process.exit(result.status ?? 1);
