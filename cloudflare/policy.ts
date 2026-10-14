export interface Job { cron: string; timeout_seconds: number }
export function cronJob(jobs: Record<string, Job>, cron: string): string {
  const match = Object.entries(jobs).find(([, job]) => job.cron === cron);
  if (!match) throw new Error("Unknown cron trigger");
  return match[0];
}
export function manualDryRun(url: URL): boolean {
  const value = url.searchParams.get("dry_run");
  if (value !== null && value !== "true" && value !== "false") throw new Error("Invalid dry_run");
  return value !== "false";
}
export function containerEnv(env: Record<string, unknown>, names: string[]): Record<string, string> {
  return Object.fromEntries(names.filter(name => typeof env[name] === "string").map(name => [name, env[name] as string]));
}
