export interface GrafanaEnv {
  GRAFANA_OTLP_ENDPOINT?: string;
  GRAFANA_OTLP_HEADERS?: string;
}

type AttributeValue = string | number | boolean | undefined;
type Severity = "INFO" | "WARN" | "ERROR";

const severityNumber: Record<Severity, number> = { INFO: 9, WARN: 13, ERROR: 17 };

function otlpHeaders(raw: string): Headers {
  const headers = new Headers({ "Content-Type": "application/json" });
  for (const pair of raw.split(",")) {
    const separator = pair.indexOf("=");
    if (separator < 1) continue;
    const name = pair.slice(0, separator).trim();
    const value = decodeURIComponent(pair.slice(separator + 1).trim());
    if (name) headers.set(name, value);
  }
  return headers;
}

export async function exportGrafanaLog(
  env: GrafanaEnv,
  service: string,
  event: string,
  severity: Severity = "INFO",
  attributes: Record<string, AttributeValue> = {},
  fetcher: typeof fetch = fetch,
): Promise<void> {
  if (!env.GRAFANA_OTLP_ENDPOINT || !env.GRAFANA_OTLP_HEADERS) return;
  try {
    const base = env.GRAFANA_OTLP_ENDPOINT.replace(/\/+$/u, "");
    const endpoint = base.endsWith("/v1/logs") ? base : `${base}/v1/logs`;
    const timeUnixNano = String(BigInt(Date.now()) * 1_000_000n);
    const logAttributes = [
      ["event.name", event],
      ...Object.entries(attributes).filter((entry): entry is [string, Exclude<AttributeValue, undefined>] => entry[1] !== undefined),
    ].map(([key, value]) => ({
      key,
      value: typeof value === "string" ? { stringValue: value }
        : typeof value === "boolean" ? { boolValue: value }
          : { intValue: String(value) },
    }));

    const response = await fetcher(endpoint, {
      method: "POST",
      headers: otlpHeaders(env.GRAFANA_OTLP_HEADERS),
      signal: AbortSignal.timeout(5000),
      body: JSON.stringify({
        resourceLogs: [{
          resource: { attributes: [{ key: "service.name", value: { stringValue: service } }] },
          scopeLogs: [{
            scope: { name: "astro-cloudflare-logs", version: "1" },
            logRecords: [{
              timeUnixNano,
              severityNumber: severityNumber[severity],
              severityText: severity,
              body: { stringValue: event },
              attributes: logAttributes,
            }],
          }],
        }],
      }),
    });
    if (!response.ok) {
      console.warn(`[grafana-otlp] export failed for ${service}: HTTP ${response.status}`);
    }
  } catch (error) {
    const reason = error instanceof Error ? error.name : "unknown error";
    console.warn(`[grafana-otlp] export failed for ${service}: ${reason}`);
    // Telemetry is best-effort; Grafana outages must not affect the job or API.
  }
}

export function queueGrafanaLog(
  ctx: Pick<ExecutionContext, "waitUntil"> | undefined,
  env: GrafanaEnv,
  service: string,
  event: string,
  severity: Severity = "INFO",
  attributes: Record<string, AttributeValue> = {},
): void {
  ctx?.waitUntil(exportGrafanaLog(env, service, event, severity, attributes));
}
